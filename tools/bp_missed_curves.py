#!/usr/bin/env python3
"""FusionPilot: curves the car took FAST, not ones it took slow.

Every tool here so far finds slowdowns and attributes them, which answers "why did it slow" but not
"why didn't it". Two reports on 2026-08-11 are the second kind: "there was a curve where I had my
speed held at 50 where it probably should have gone slower for that one", and the exit near the end
of the drive that never slows enough.

So this scans for the opposite signature -- sustained high lateral acceleration with nothing asking
for a lower speed. For each one it prints what both curve controllers were doing at the time, which
separates the three reasons a curve gets missed:

  * neither controller ever fired          -> the corner is invisible to both
  * a controller asked, but not enough     -> a tuning question
  * a controller asked and was overruled    -> a veto, a limiter, or the driver on the pedal

LATERAL ACCELERATION IS READ FROM THE CONTROLLER, not derived from the steering angle. The
steering-derived figure counts lane changes and corrections as cornering and reads several m/s^2 on a
straight freeway, which would bury every real result. `currentLateralAccel` is v_ego^2 * curvature
from controlsState, which is the quantity the curve controllers themselves regulate.

USAGE, on the device:

    cd /data/openpilot && python tools/bp_missed_curves.py
    python tools/bp_missed_curves.py --route 00000042--aa11bb22cc --lat 2.5
"""
from __future__ import annotations

import argparse
import os
import sys

from openpilot.tools.bp_logtime import DriveClock

REALDATA = "/data/media/0/realdata"
MS_TO_MPH = 2.23694
NO_TARGET_MPH = 500.0



def seg_index(name: str) -> int:
  """Segment ORDER IS NUMERIC, not lexicographic.

  sorted() puts `--10` before `--2`, so any route with ten or more segments was read out of order.
  Found on 2026-08-11 on a 32-segment route: the timeline jumped around, timestamps ran to eight
  hours on a half-hour drive, and the reboot re-basing kept adding shifts to chase it. Per-frame data
  was still real -- a run of frames inside one segment is contiguous either way -- but every t+ label
  spanning segments was wrong, and windows pulled from them pointed at the wrong part of the drive.
  """
  tail = name.rsplit("--", 1)[-1]
  return int(tail) if tail.isdigit() else -1

def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--route", default=None)
  ap.add_argument("--lat", type=float, default=2.5,
                  help="m/s^2 that counts as a curve worth having slowed for")
  ap.add_argument("--hold-s", type=float, default=1.0, help="seconds it must persist")
  ap.add_argument("--max-segments", type=int, default=40)
  args = ap.parse_args()

  try:
    from openpilot.tools.lib.logreader import LogReader
  except ImportError as e:
    sys.exit(f"no LogReader ({e}); run from /data/openpilot")

  if not os.path.isdir(REALDATA):
    sys.exit(f"no {REALDATA} -- run this on the device")
  entries = sorted((d for d in os.listdir(REALDATA) if "--" in d), key=seg_index)
  route = args.route or entries[-1].rsplit("--", 1)[0]
  segs = [d for d in entries if d.startswith(route + "--")][:args.max_segments]
  print(f"# route {route}, {len(segs)} segments, threshold {args.lat:.1f} m/s^2\n")

  st = {"v": 0.0, "dash": 0.0, "src": "?", "lat": 0.0, "pred": 0.0,
        "mapV": 0.0, "mapAct": False, "visV": 0.0, "visAct": False, "gas": False}
  clock = DriveClock()
  run_start = None
  peak = 0.0
  worst: dict = {}
  found = 0

  print("   start     dur   peak lat   mph   dash  source        sccMap  sccVis   why")
  for seg in segs:
    path = None
    for name in ("rlog", "rlog.zst", "rlog.bz2"):
      p = os.path.join(REALDATA, seg, name)
      if os.path.exists(p):
        path = p
        break
    if path is None:
      continue
    for msg in LogReader(path):
      w = msg.which()
      ts = clock.seconds(msg.logMonoTime)
      try:
        if w == "carState":
          st["v"] = msg.carState.vEgo * MS_TO_MPH
          st["dash"] = msg.carState.cruiseState.speedCluster * MS_TO_MPH
          st["gas"] = bool(msg.carState.gasPressed)
          continue
        if w != "longitudinalPlanSP":
          continue
        lp = msg.longitudinalPlanSP
        st["src"] = str(lp.longitudinalPlanSource)
        st["lat"] = float(lp.smartCruiseControl.vision.currentLateralAccel)
        st["pred"] = float(lp.smartCruiseControl.vision.maxPredictedLateralAccel)
        st["mapV"] = lp.smartCruiseControl.map.vTarget * MS_TO_MPH
        st["mapAct"] = bool(lp.smartCruiseControl.map.active)
        st["visV"] = lp.smartCruiseControl.vision.vTarget * MS_TO_MPH
        st["visAct"] = bool(lp.smartCruiseControl.vision.active)
      except Exception:  # noqa: BLE001
        continue

      if st["lat"] >= args.lat:
        if run_start is None:
          run_start, peak, worst = ts, st["lat"], dict(st)
        elif st["lat"] > peak:
          peak, worst = st["lat"], dict(st)
        continue

      if run_start is None:
        continue
      dur = ts - run_start
      if dur >= args.hold_s:
        found += 1
        m = "  --  " if not worst["mapAct"] or worst["mapV"] > NO_TARGET_MPH else f"{worst['mapV']:5.0f}*"
        v = "  --  " if not worst["visAct"] or worst["visV"] > NO_TARGET_MPH else f"{worst['visV']:5.0f}*"
        if worst["gas"]:
          why = "DRIVER ON THE PEDAL"
        elif not worst["mapAct"] and not worst["visAct"]:
          why = f"NEITHER CONTROLLER FIRED (model predicted {worst['pred']:.2f})"
        elif worst["dash"] > worst["v"] + 3:
          why = "a controller asked; the set speed had not arrived"
        else:
          why = "a controller asked; its target was too high"
        print(f"  t+{run_start:6.0f} {dur:6.1f}s {peak:7.2f}  {worst['v']:5.0f} {worst['dash']:5.0f}"
              f"  {worst['src']:<12} {m}  {v}  {why}")
      run_start = None

  print(f"\n=== {found} curves taken above {args.lat:.1f} m/s^2 ===")
  print("  NEITHER CONTROLLER FIRED with a low predicted value is the camera not seeing the bend;")
  print("  with a high one it is the target being too generous. Those need different fixes.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
