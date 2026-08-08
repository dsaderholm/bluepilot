#!/usr/bin/env python3
"""BluePilot: why the car does not slow enough for a freeway exit.

Standing report, unresolved across several drives: "It still does not slow down anywhere close to
enough on freeway exits."

There are two candidate causes and they need opposite fixes, so guessing between them is worthless:

  A. SCC-Map never fires. MapTargetVelocities is written by mapd -- an EXTERNAL binary -- and
     nothing in this repo produces it. No mapd, no data for that road, or ramp geometry absent from
     the way you are still on, and map_controller has nothing to work with. Fixing the tuning would
     then change nothing at all.

  B. SCC-Map fires but cannot finish. At SmartCruiseControlMapDecel = 0.8 m/s^2 an 80 -> 40 mph exit
     needs 600 m of runway; at 1.5 it needs 320. If mapd's lookahead is shorter than the requirement,
     the cycle starts too late no matter how correct it is. Then the fix is the decel setting, and
     touching mapd would be wasted effort.

This tells them apart. It finds every large deceleration in a route and shows what the planner was
choosing at the time, plus whether SCC-Map was ever the source at all.

USAGE, on the device:

    cd /data/openpilot && python tools/bp_dump_exit.py
    python tools/bp_dump_exit.py --route 00000042--aa11bb22cc

Paste the whole thing back. The summary line at the end is the part that decides A vs B.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import deque

REALDATA = "/data/media/0/realdata"
MS_TO_MPH = 2.23694

# An "exit" for our purposes: was doing freeway speed, ended up well below it.
FAST_MPH = 55.0
SLOW_MPH = 45.0
# longitudinalPlanSource is a capnp ENUM, and str() on it gives the enumerant name directly --
# "sccMap", "sccVision", "cruise", "speedLimitAssist". The first version called int() on it and died
# with "int() argument must be ... not '_DynamicEnum'" after parsing a whole route. Verified against
# the schema rather than assumed: str() -> 'sccMap', .raw -> 2.


def find_segments(route: str | None) -> list[str]:
  if not os.path.isdir(REALDATA):
    sys.exit(f"no {REALDATA} -- is this running on the device?")
  entries = sorted(d for d in os.listdir(REALDATA) if "--" in d)
  if not entries:
    sys.exit(f"no route segments under {REALDATA}")
  if route is None:
    route = entries[-1].rsplit("--", 1)[0]
    print(f"# newest route: {route}\n")
  segs = [os.path.join(REALDATA, d) for d in entries if d.startswith(route + "--")]
  if not segs:
    sys.exit(f"no segments for route {route}")
  return segs


def log_path(seg: str) -> str | None:
  for name in ("rlog", "rlog.zst", "rlog.bz2"):
    p = os.path.join(seg, name)
    if os.path.exists(p):
      return p
  return None


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--route", default=None)
  ap.add_argument("--window", type=float, default=25.0, help="seconds of approach to show")
  args = ap.parse_args()

  try:
    from openpilot.tools.lib.logreader import LogReader
  except ImportError as e:
    sys.exit(f"could not import LogReader ({e}); run this from /data/openpilot on the device")

  # Live peek at mapd -- but ONLY meaningful while driving, and this is usually run parked.
  #
  # The first version printed "EMPTY, mapd is producing nothing" whenever the param was unset, which
  # parked is exactly what you would expect whether mapd works or not. That is a conclusion the
  # evidence does not support, and stating it would have sent the next hour after the wrong cause.
  # The log-based count in the summary is the reliable answer; this is a bonus that only speaks up
  # when it has something positive to say.
  try:
    from openpilot.common.params import Params
    onroad = bool(Params().get_bool("IsOnroad"))
    n = len(Params("/dev/shm/params").get("MapTargetVelocities") or b"")
    if n:
      print(f"# mapd is producing data right now: MapTargetVelocities = {n} bytes")
    elif onroad:
      print("# MapTargetVelocities is EMPTY while onroad -- mapd has nothing for this road")
    else:
      print("# parked, so the live mapd check proves nothing either way; see the summary instead")
  except Exception as e:  # noqa: BLE001
    print(f"# live mapd check unavailable ({e}); the summary below does not depend on it")
  print()

  st = {"v": 0.0, "src": None, "mapV": 0.0, "mapAct": False, "visV": 0.0, "visAct": False,
        "setSpeed": 0.0}
  hist: deque = deque(maxlen=6000)
  map_active_frames = 0
  map_source_frames = 0
  total = 0
  events = 0
  bad = 0
  t0 = None
  armed = False

  for seg in find_segments(args.route):
    path = log_path(seg)
    if path is None:
      continue
    for msg in LogReader(path):
      w = msg.which()
      t = msg.logMonoTime / 1e9
      if t0 is None:
        t0 = t

      # Wrapped because a route takes minutes to parse and one unexpected field should not throw
      # all of it away -- which is exactly what happened on the first run.
      try:
        if w == "carState":
          st["v"] = msg.carState.vEgo * MS_TO_MPH
          st["setSpeed"] = msg.carState.vCruiseCluster
        elif w == "longitudinalPlanSP":
          lp = msg.longitudinalPlanSP
          st["src"] = str(lp.longitudinalPlanSource)
          st["mapV"] = lp.smartCruiseControl.map.vTarget * MS_TO_MPH
          st["mapAct"] = lp.smartCruiseControl.map.active
          st["visV"] = lp.smartCruiseControl.vision.vTarget * MS_TO_MPH
          st["visAct"] = lp.smartCruiseControl.vision.active
          total += 1
          map_active_frames += 1 if st["mapAct"] else 0
          map_source_frames += 1 if st["src"] == "sccMap" else 0
        else:
          continue
      except Exception as e:  # noqa: BLE001
        if not bad:
          print(f"# skipping unreadable {w} frames: {e}")
        bad += 1
        continue

      hist.append((t - t0, dict(st)))

      if st["v"] >= FAST_MPH:
        armed = True
      elif armed and st["v"] <= SLOW_MPH:
        armed = False
        events += 1
        print(f"===== deceleration #{events}: {FAST_MPH:.0f} -> {SLOW_MPH:.0f} mph "
              f"at t+{t - t0:.1f}s  ({os.path.basename(seg)}) =====")
        print("   time     mph   setSpd  source      sccMap    sccVis")
        lo = (t - t0) - args.window
        shown = 0
        for ts, s in hist:
          if ts < lo:
            continue
          shown += 1
          if shown % 20:      # ~1 Hz is plenty for a 25 s approach
            continue
          m = f"{s['mapV']:5.0f}{'*' if s['mapAct'] else ' '}"
          v = f"{s['visV']:5.0f}{'*' if s['visAct'] else ' '}"
          print(f"  t+{ts:7.1f} {s['v']:6.1f} {s['setSpeed']:7.0f}  {str(s['src']):<10} {m}    {v}")
        print()

  print("===== summary =====")
  print(f"  longitudinalPlanSP frames : {total}")
  print(f"  SCC-Map ACTIVE            : {map_active_frames}")
  print(f"  SCC-Map was the SOURCE    : {map_source_frames}")
  print(f"  large decelerations found : {events}")
  if bad:
    print(f"  unreadable frames skipped : {bad}")
  print()
  if map_active_frames == 0:
    print("  -> CANDIDATE A. SCC-Map never became active anywhere in this route, so no amount of")
    print("     tuning will change the exits. Look at whether mapd is running and has data:")
    print("       ls -la /data/media/0/osm  ;  grep -i mapd /data/log/* | tail")
  elif map_source_frames == 0:
    print("  -> SCC-Map was active but never won the min(). Something else was always asking for")
    print("     less, so its target was never the limiting one. Check the source column above.")
  else:
    print("  -> CANDIDATE B. SCC-Map does fire and does win. Then the question is whether it starts")
    print("     early enough: at SmartCruiseControlMapDecel = 0.8 m/s^2 an 80 -> 40 exit needs 600 m")
    print("     of runway, and 1.5 needs 320. Compare where the sccMap column first drops against")
    print("     how far out that was.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
