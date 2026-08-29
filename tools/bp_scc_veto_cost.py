"""What did the SCC-Map veto actually COST? Now measurable. Read-only, off-device.

Last night this was unanswerable: `get_v_target_from_control()` returns V_CRUISE_UNSET once a veto
clears `is_active`, so the speed the map WANTED was discarded before it reached the log. A detector
built on `active` could only ever return zero for suppressed corners -- a vacuous safety result that
reads exactly like a clean one.

`vetoedVTarget @9` was added for precisely this and shipped with the ICBM build, so routes from
000003e2 onward carry it. For every veto this asks the only question that matters:

    was the corner it suppressed REAL?

using the same geometry that found the phantoms:

    R_map   = vetoed_v_target^2 / (a_lat * factor^2)   what the map wanted, inverted to a radius
    R_drove = v^2 / a_lat at the peak that followed    what the road turned out to be

    R_drove >> R_map   the map was inventing a hairpin  -> the veto was RIGHT
    R_drove ~= R_map   the corner was real              -> the veto COST a slowdown

Hands-off only. A peak reached while he was steering is his cornering, not the controller's, and
conflating them is the split that produced the wrong 3.21 m/s^2 figure.
"""
import collections
import glob
import math
import os
import sys

import capnp
import zstandard

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
capnp.remove_import_hook()
log_capnp = capnp.load(os.path.join(REPO, "cereal", "log.capnp"),
                       imports=[os.path.join(REPO, "cereal")])

MS_TO_MPH = 2.23694
SCC_MAP_LAT_A, SCC_MAP_FACTOR = 2.2, 0.90
SR, WB = 17.0, 2.85
LOOKAHEAD = 15.0


def seg_key(p):
  b = os.path.basename(p).split("--")
  try:
    return (b[0], int(b[2].split(".")[0]))
  except (IndexError, ValueError):
    return (os.path.basename(p), 0)


def main():
  d = sys.argv[1]
  routes = set(sys.argv[2:])
  files = sorted(glob.glob(os.path.join(d, "*.rlog.zst")), key=seg_key)
  if routes:
    files = [f for f in files if os.path.basename(f).split("--")[0] in routes]

  by_route = collections.OrderedDict()
  for f in files:
    by_route.setdefault(os.path.basename(f).split("--")[0], []).append(f)

  print("=== WHAT THE SCC-MAP VETO SUPPRESSED (vetoedVTarget, now on the wire) ===")
  print()
  print("  %-9s %8s %7s %8s %9s %8s %6s  %s" % (
    "route", "t+", "veto mph", "R_map", "R_drove", "ratio", "hands", "verdict"))

  tot = collections.Counter()
  for route, paths in by_route.items():
    t0 = None
    lat = []
    runs = []
    cur = None
    has_field = False
    for p in paths:
      try:
        with open(p, "rb") as f:
          raw = zstandard.ZstdDecompressor().stream_reader(f).read()
        evs = log_capnp.Event.read_multiple_bytes(raw, traversal_limit_in_words=2 ** 32)
      except Exception:
        continue
      while True:
        try:
          m = next(evs)
        except (StopIteration, Exception):
          break
        mono = m.logMonoTime / 1e9
        if t0 is None or mono < t0:
          t0 = mono
        ts = mono - t0
        w = m.which()
        try:
          if w == "carState":
            cs = m.carState
            v = cs.vEgo
            k = abs(math.tan(math.radians(float(cs.steeringAngleDeg) / SR)) / WB)
            lat.append((ts, v * v * k, v, bool(cs.steeringPressed)))
          elif w == "longitudinalPlanSP":
            mp = m.longitudinalPlanSP.smartCruiseControl.map
            vv = float(getattr(mp, "vetoedVTarget", 0.0))
            if vv > 0:
              has_field = True
              if cur is None:
                cur = dict(t0=ts, vt=vv)
              else:
                cur["vt"] = min(cur["vt"], vv)
            elif cur is not None:
              runs.append(cur)
              cur = None
        except Exception:
          continue
    if cur:
      runs.append(cur)

    if not has_field:
      tot["no_field"] += 1
      continue

    for r in runs:
      win = [(a, v) for t, a, v, h in lat if r["t0"] <= t <= r["t0"] + LOOKAHEAD and not h]
      hands_win = [1 for t, a, v, h in lat if r["t0"] <= t <= r["t0"] + LOOKAHEAD and h]
      if not win:
        continue
      peak, vpk = max(win)
      r_map = r["vt"] ** 2 / (SCC_MAP_LAT_A * SCC_MAP_FACTOR ** 2)
      r_drove = (vpk * vpk / peak) if peak > 0.05 else 0
      ratio = (r_drove / r_map) if r_map > 0 and r_drove > 0 else 0
      if ratio >= 3.0:
        verdict = "phantom -- veto CORRECT"
        tot["correct"] += 1
      elif ratio > 0:
        verdict = "*** REAL CORNER SUPPRESSED -- this is the cost ***"
        tot["cost"] += 1
      else:
        verdict = "no cornering measured"
        tot["nomeasure"] += 1
      print("  %-9s %8.0f %8.0f %8.0f %9.0f %7.1fx %6s  %s" % (
        route, r["t0"], r["vt"] * MS_TO_MPH, r_map, r_drove, ratio,
        bool(hands_win), verdict))

  print()
  print("  veto episodes where the map was inventing a corner : %d" % tot["correct"])
  print("  veto episodes that suppressed a REAL corner        : %d   <- the cost" % tot["cost"])
  print("  no cornering measurable in the window              : %d" % tot["nomeasure"])
  if tot["no_field"]:
    print("  routes without vetoedVTarget (pre-ICBM build)     : %d" % tot["no_field"])


if __name__ == "__main__":
  main()
