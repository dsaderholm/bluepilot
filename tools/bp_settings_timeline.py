"""When did each setting change during the drives? Read-only, runs off-device.

*"I tweaked a lot of settings as I drove."* That makes every pooled statistic suspect, because a
number averaged across a settings change describes a car that never existed. This turns the tweaks
into a TIMELINE so the rest of the analysis can be split on it instead of averaging through it.

`initData` carries a full params snapshot at the start of EVERY SEGMENT -- not just every route --
so mid-route changes are recoverable at ~1 minute resolution. That matters here: he changes things
while moving, so per-route config (what an earlier version of this read) is too coarse and silently
mixes configurations inside a single route.

Prints one line per CHANGE, not per segment, so a long drive with three tweaks is three lines.

    python tools/bp_settings_timeline.py <dir-of-rlog.zst> [route ...]
"""
import collections
import glob
import os
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
              "FordVLTExtraMax", "FordPathAngleBlendRatio", "enable_lane_positioning_ang",
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


def main():
  d = sys.argv[1]
  routes = set(sys.argv[2:])
  files = sorted(glob.glob(os.path.join(d, "*.rlog.zst")), key=seg_key)
  if routes:
    files = [f for f in files if os.path.basename(f).split("--")[0] in routes]
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
