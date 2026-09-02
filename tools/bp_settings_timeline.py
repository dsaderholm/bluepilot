"""When did each setting change during the drives? Read-only, runs off-device.

*"I tweaked a lot of settings as I drove."* That makes every pooled statistic suspect, because a
number averaged across a settings change describes a car that never existed. This turns the tweaks
into a TIMELINE so the rest of the analysis can be split on it instead of averaging through it.

**`initData` IS A BOOT SNAPSHOT AND CANNOT SHOW A MID-ROUTE CHANGE.** An earlier version of this
docstring claimed the opposite -- that a fresh params snapshot opens every segment, making changes
recoverable at ~1 minute resolution -- and the whole ledger was built on that claim. It is false.
The snapshot is taken once per boot and replayed unchanged into every segment of every route.

PROVEN on route 0000040e: `FordHighSpeedFactor_ang` was written at 23:12:32, segment 14 closed at
23:12:33, the route ran until 23:28 -- and all 31 segments report the pre-change 0.68. Sixteen
minutes of driving on a different setting were invisible here.

**So this tool reads TWO sources and prints both.** The params snapshot says what was stored at
boot. `--telemetry` recovers the gains from `controllerStateBP`, which is published per frame and
is what actually multiplied the command -- and at or above 70 mph the speed blend is saturated, so
the recovery is EXACT rather than an estimate:

    gainLowCurv  == FordHighSpeedDampening_ang
    gainHighCurv == anchor * FordHighSpeedFactor_ang     (anchor per platform, see angle_gains.py)

Prints one line per CHANGE, not per segment, so a long drive with three tweaks is three lines.

    python tools/bp_settings_timeline.py <dir-of-rlog.zst> [route ...]
    python tools/bp_settings_timeline.py <dir> --telemetry      # what the WIRE says, per segment
"""
import ast
import glob
import os
import statistics
import sys

import capnp
import zstandard

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
capnp.remove_import_hook()
log_capnp = capnp.load(os.path.join(REPO, "cereal", "log.capnp"),
                       imports=[os.path.join(REPO, "cereal")])

# Everything that changes how the car drives, grouped so the report reads by subsystem.
WATCH = {
  "LATERAL": ("FordLowSpeedFactor_ang", "FordHighSpeedFactor_ang", "FordHighSpeedDampening_ang",
              # NB: FordVLTExtraMax and FordPathAngleBlendRatio were watched here for weeks
              # and are NOT params -- both are hardcoded constants. Watching a key that
              # cannot exist makes the report look thorough while checking nothing.
              "enable_lane_positioning_ang",
              "custom_path_offset_ang", "lane_centering_strength_ang",
              "lane_centering_damping_ang", "LagdToggle",
              "LagdValueCache"),
  "SCC":     ("SmartCruiseControlVision", "SmartCruiseControlMap",
              "SmartCruiseControlVisionLowSpeedFactor", "SmartCruiseControlVisionHighSpeedFactor",
              "SmartCruiseControlMapFactor", "SmartCruiseControlMapHighSpeedFactor",
              "SmartCruiseControlMapDecel"),
  "SLA":     ("SpeedLimitMode", "SpeedLimitPolicy", "SpeedLimitAutoFollow",
              "SpeedLimitValueOffset", "SpeedLimitOffsetType", "SpeedLimitMaxSetSpeed"),
  "ICBM":    ("IntelligentCruiseButtonManagement", "IcbmMaxTargetDrop", "IcbmModelStopEnabled",
              "IcbmPinnedHoldsEnabled", "IcbmGapControl", "IcbmBaselineResetDelta"),
  "MODE":    ("ExperimentalMode", "DynamicExperimentalControl", "AlphaLongitudinalEnabled",
              "MapdV2", "ModelManagerSelectedBundle"),
}


def seg_key(p):
  b = os.path.basename(p)
  parts = b.split("--")
  try:
    return (parts[0], int(parts[2].split(".")[0]))
  except (IndexError, ValueError):
    return (b, 0)


def snapshot(path):
  try:
    with open(path, "rb") as f:
      raw = zstandard.ZstdDecompressor().stream_reader(f).read()
    for m in log_capnp.Event.read_multiple_bytes(raw, traversal_limit_in_words=2 ** 32):
      if m.which() != "initData":
        continue
      out = {}
      for e in m.initData.params.entries:
        for keys in WATCH.values():
          if e.key in keys:
            try:
              v = e.value.decode(errors="replace").strip()
            except Exception:
              v = repr(e.value)
            out[e.key] = v[:40]
      return out
  except Exception:
    pass
  return None


def _gain_anchors(fingerprint):
  """(low, high) gain anchors for a platform, READ WITHOUT IMPORTING THE CAR LAYER.

  **IMPORTING `opendbc.car.*` AFTER `capnp.load()` KILLS THE INTERPRETER.** Measured 2026-09-02:
  `opendbc.car.structs` loads its own capnp schema, and a second load in a process that already
  holds `log.capnp` calls `abort()` -- exit 127, no traceback, no Python-level exception, so a
  `try/except ImportError` around it catches nothing. The first version of this function did
  exactly that import and every `--telemetry` run died silently after printing its header.

  Every tool in here loads `log.capnp` to read rlogs, so NO rlog tool can import a car constant.
  The anchors are parsed out of `angle_gains.py` instead -- still one definition, still no
  duplicated 1.15 to drift, and no import. Hardcoding 1.15 would be wrong on CAN-FD.
  """
  src = os.path.join(REPO, "opendbc_repo", "opendbc", "sunnypilot", "car", "ford", "angle_gains.py")
  tree = ast.parse(open(src, encoding="utf-8").read())
  pairs, sets = {}, {}
  for node in tree.body:
    if not isinstance(node, ast.Assign) or not isinstance(node.targets[0], ast.Name):
      continue
    name = node.targets[0].id
    try:
      pairs[name] = ast.literal_eval(node.value)
    except ValueError:
      # frozenset({CAR.X, ...}) -- literal_eval refuses the attribute nodes, so take the names
      sets[name] = {a.attr for a in ast.walk(node.value) if isinstance(a, ast.Attribute)}
  fp = str(fingerprint) if fingerprint is not None else ""
  if fp in sets.get("CANFD_BOF_CARS", set()):
    return pairs["GAIN_CANFD_BOF"]
  if fp in sets.get("CANFD_SUV_CARS", set()):
    return pairs["GAIN_CANFD_SUV"]
  return pairs["GAIN_CAN"]


def recover_from_wire(path, fingerprint=None):
  """The gains as they ACTUALLY applied, recovered per segment from controllerStateBP.

  At or above 70 mph (31.29 m/s) the speed blend is saturated, so the schedule collapses to

      gainLowCurv  = FordHighSpeedDampening_ang
      gainHighCurv = anchor_high * FordHighSpeedFactor_ang

  which inverts EXACTLY. Below saturation both terms are speed-blended and the recovery is not
  possible, so those frames are excluded rather than estimated -- an estimate here would be
  indistinguishable from a real settings change, which is the whole failure this mode exists to fix.
  """
  _, anchor_high = _gain_anchors(fingerprint)

  v = 0.0
  lows, highs = [], []
  try:
    with open(path, "rb") as fh:
      raw = zstandard.ZstdDecompressor().stream_reader(fh).read()
    evs = log_capnp.Event.read_multiple_bytes(raw, traversal_limit_in_words=2 ** 32)
  except Exception:
    return None
  while True:
    try:
      m = next(evs)
    except StopIteration:
      break
    except Exception:
      break
    try:
      w = m.which()
      if w == "carState":
        v = float(m.carState.vEgo)
      elif w == "controllerStateBP":
        if v < 31.29:
          continue
        lo = float(m.controllerStateBP.gainLowCurv)
        hi = float(m.controllerStateBP.gainHighCurv)
        if lo == 0.0 and hi == 0.0:
          continue
        lows.append(lo)
        highs.append(hi)
    except Exception:
      continue
  if len(lows) < 40:
    return None
  lo = statistics.median(lows)
  hi = statistics.median(highs)
  return {"FordHighSpeedDampening_ang": round(lo, 3),
          "FordHighSpeedFactor_ang": round(hi / anchor_high, 3)}


def telemetry_timeline(files):
  print("=== RECOVERED FROM THE WIRE (controllerStateBP, >= 70 mph) ===")
  print("   This is what actually multiplied the command. It SEES mid-route changes.")
  print("   A segment with under 40 saturated frames prints nothing -- not a zero.")
  print()
  prev = None
  changes = 0
  scored = 0
  for f in files:
    route, idx = seg_key(f)
    rec = recover_from_wire(f)
    if rec is None:
      continue
    scored += 1
    if rec != prev:
      tag = "BASELINE" if prev is None else "CHANGED "
      print("  %s %s seg %-3d  damp=%.3f  high=%.3f"
            % (tag, route, idx, rec["FordHighSpeedDampening_ang"], rec["FordHighSpeedFactor_ang"]))
      if prev is not None:
        changes += 1
      prev = rec
  print()
  print("  %d segments had enough saturated road to score; %d changes seen." % (scored, changes))
  if scored == 0:
    print("  Nothing above 70 mph in this pull, or the route predates the 2026-09-01 telemetry.")


def main():
  argv = [a for a in sys.argv[1:] if a != "--telemetry"]
  wire = "--telemetry" in sys.argv
  d = argv[0]
  routes = set(argv[1:])
  files = sorted(glob.glob(os.path.join(d, "*.rlog.zst")), key=seg_key)
  if routes:
    files = [f for f in files if os.path.basename(f).split("--")[0] in routes]
  if wire:
    telemetry_timeline(files)
    return
  print("  initData.params is a BOOT SNAPSHOT, replayed unchanged into every segment of a route.")
  print("  A setting changed MID-ROUTE therefore does NOT appear here -- PROVEN on 0000040e, where")
  print("  FordHighSpeedFactor_ang was written at 23:12:32, segment 14 closed at 23:12:33, and all")
  print("  31 segments still report the pre-change value. Split such a route BY SEGMENT NUMBER")
  print("  against the param mtime, or read the gain telemetry with bp_lateral_gain.py, which is")
  print("  published per frame and cannot lie about what actually multiplied the command.")
  print()

  print("=== SETTINGS TIMELINE (one line per CHANGE, not per segment) ===")
  print()
  prev = None
  changes = 0
  seen_routes = []
  for f in files:
    route, idx = seg_key(f)
    snap = snapshot(f)
    if snap is None:
      continue
    if route not in seen_routes:
      seen_routes.append(route)
    if prev is None:
      print("  BASELINE at %s seg %d:" % (route, idx))
      for group, keys in WATCH.items():
        vals = [(k, snap[k]) for k in keys if k in snap]
        if vals:
          print("    %-8s %s" % (group, "  ".join("%s=%s" % kv for kv in vals)))
      print()
      prev = snap
      continue
    diffs = []
    for k in set(list(prev) + list(snap)):
      a, b = prev.get(k, "(unset)"), snap.get(k, "(unset)")
      if a != b:
        grp = next((g for g, ks in WATCH.items() if k in ks), "?")
        diffs.append((grp, k, a, b))
    if diffs:
      changes += 1
      print("  %s seg %-3d CHANGED:" % (route, idx))
      for grp, k, a, b in sorted(diffs):
        print("      [%s] %-38s %s -> %s" % (grp, k, a, b))
    prev = snap

  print()
  print("  %d routes scanned, %d settings changes detected." % (len(seen_routes), changes))
  if changes:
    print("  ANY statistic pooled across those boundaries describes a car that never existed.")
    print("  Split on them before reading anything into a rate or a median.")


if __name__ == "__main__":
  main()
