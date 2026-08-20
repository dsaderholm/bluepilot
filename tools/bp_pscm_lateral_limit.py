#!/usr/bin/env python3
"""FusionPilot: how fast CAN the PSCM hold a corner in angle mode? Measured, not assumed.

His correction, and the one number the tile-curvature work is waiting on:

    "remember that my PSCM requires slower speeds for curves, so how I take the curve won't be
     accurate. I want to take the curve as fast as the PSCM can handle with angle steering."

A corner speed is `v = sqrt(a_lat * R)`. The radius is settled -- `tile_curvature` measures his I-80
bend at 259 m against the 240 m the car actually pulled. `a_lat` is not, and the two candidates lying
around are both wrong:

    mapd's 2.2 m/s^2      somebody else's comfort constant. Asks 53 mph where he drove 64.
    his measured 3.46     what the car did with HIM steering. He has said the PSCM needs it slower.

THIS IS MEASURABLE, AND ONLY BECAUSE OF SOMETHING FOUND WHILE LOOKING FOR IT: `MAX_LATERAL_ACCEL`
(~2.4 m/s^2, ISO minus road roll) is applied in `carcontroller.py` and `lateral_curv_ext.py` -- and
appears ZERO times in `lateral_angle_ext.py`, which is the path his car takes (`ALT_STEER_ANGLE`).
So in angle mode openpilot is NOT holding lateral acceleration below 2.4, and every corner he has
driven with MADS on is already a sample of what the PSCM will do.

WHAT IT MEASURES

  achieved lateral acceleration, while OPENPILOT was steering, split by whether either of the two
  limiters was biting that frame:

    angleRateLimited           the path_angle soft rate-of-change clip actually bit
    curvatureDeviationLimited  the command was clipped toward measured curvature

  Frames with NEITHER flag are ones where the controller asked for what it wanted and got it -- so
  the top of that distribution is capability that has been DEMONSTRATED, not inferred. Frames with a
  flag are where our own code intervened, and they bound the answer from the other side.

WHY THIS TOOL'S ANSWER IS NOT THE CORNER TARGET, added 2026-08-20 after it produced two wrong ones.

  It measures ACHIEVED lateral acceleration and where our own limiters bite. Neither is the failure
  that matters. He settled it from the seat: *"I just ignore most steering saturated errors until it
  starts to stray enough from my lane."* The failure is RUNNING WIDE, and the signal for it is the
  lateral-acceleration SHORTFALL -- `(desiredCurvature - curvature) * v^2` -- which is flat to
  2.5 m/s^2 on his car and collapses above it, while `steerSaturated` fires where tracking is clean
  and NOT in the bin that actually runs wide.

  So: this tool tells you what the car HAS done. It does not tell you what it can hold. Ask which
  failure you are measuring before taking a number from here.

WHY A PERCENTILE AND NOT THE MAXIMUM. A single frame at 4 m/s^2 is a pothole, a lane change, or the
driver's own hand on the wheel. p99 over thousands of frames is a number the car reached and held.

READ IT ALONGSIDE THE DRIVER-STEERED DISTRIBUTION, which this also prints: if openpilot's ceiling
sits well below his own, the gap IS the PSCM limit he is describing. If they coincide, then nothing
has been limiting him and the corner target can go to the comfort number after all.

USAGE, on the device:

    cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 \\
        tools/bp_pscm_lateral_limit.py            # newest 3 routes
        tools/bp_pscm_lateral_limit.py --routes 6
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

REALDATA = "/data/media/0/realdata"
MS_TO_MPH = 2.23694
MIN_MPH = 25.0          # below this a corner is a turn, not the highway case the target is for
MIN_LAT_ACC = 0.5       # ignore straights; they are most frames and would drown every percentile


def seg_index(name: str) -> int:
  tail = name.rsplit("--", 1)[-1]
  return int(tail) if tail.isdigit() else -1


def routes_by_recency(n: int) -> list[str]:
  groups: dict[str, list[str]] = defaultdict(list)
  for d in os.listdir(REALDATA):
    if "--" in d and seg_index(d) >= 0:
      groups[d.rsplit("--", 1)[0]].append(d)

  def when(r: str) -> float:
    return max(os.path.getmtime(os.path.join(REALDATA, d)) for d in groups[r])

  return sorted(groups, key=when, reverse=True)[:n]


def pct(vals: list[float], q: float) -> float:
  if not vals:
    return float("nan")
  s = sorted(vals)
  return s[min(len(s) - 1, int(q * len(s)))]


def report(name: str, vals: list[float]) -> None:
  if not vals:
    print("  {:<34} NO DATA".format(name))
    return
  print("  {:<34} n={:6d}   p50 {:.2f}   p90 {:.2f}   p99 {:.2f}   max {:.2f}".format(
    name, len(vals), pct(vals, 0.50), pct(vals, 0.90), pct(vals, 0.99), max(vals)))


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--routes", type=int, default=3)
  args = ap.parse_args()

  if not os.path.isdir(REALDATA):
    sys.exit("no {} -- run this on the device".format(REALDATA))
  sys.path.insert(0, "/data/openpilot")
  from openpilot.tools.lib.logreader import LogReader

  op_clean: list[float] = []      # openpilot steering, neither limiter biting
  op_limited: list[float] = []    # openpilot steering, a limiter bit
  driver: list[float] = []        # he was steering
  no_flag_data = True

  for route in routes_by_recency(args.routes):
    segs = sorted([d for d in os.listdir(REALDATA) if d.startswith(route)], key=seg_index)
    print("# {}  ({} segments)".format(route, len(segs)))

    lat_active = False
    steering_pressed = False
    rate_lim = dev_lim = False
    v_mph = 0.0

    for seg in segs:
      p = os.path.join(REALDATA, seg, "rlog")
      if not os.path.exists(p):
        p += ".zst"
      if not os.path.exists(p):
        continue
      try:
        lr = LogReader(p)
      except Exception:
        continue

      for m in lr:
        try:
          w = m.which()
        except Exception:
          continue
        try:
          if w == "carState":
            v_mph = float(m.carState.vEgo) * MS_TO_MPH
            steering_pressed = bool(m.carState.steeringPressed)
          elif w == "carControl":
            lat_active = bool(m.carControl.latActive)
          elif w == "controllerStateBP":
            c = m.controllerStateBP
            rate_lim = bool(getattr(c, "angleRateLimited", False))
            dev_lim = bool(getattr(c, "curvatureDeviationLimited", False))
            no_flag_data = False
          elif w == "longitudinalPlanSP":
            a = abs(float(m.longitudinalPlanSP.smartCruiseControl.vision.currentLateralAccel))
            if v_mph < MIN_MPH or a < MIN_LAT_ACC:
              continue
            # `latActive` alone is NOT "openpilot was steering" -- it means openpilot was PERMITTED
            # to steer. He caught the first version reporting 3.21 m/s^2 as openpilot's capability
            # when 892 of those frames had his own hands on the wheel, and above 3.0 m/s^2 NINETY
            # PERCENT of them did. steeringPressed is the discriminator and it moves the answer by
            # half a metre per second squared.
            if not lat_active or steering_pressed:
              driver.append(a)
            elif rate_lim or dev_lim:
              op_limited.append(a)
            else:
              op_clean.append(a)
        except Exception:
          continue

  print("")
  print("=== ACHIEVED LATERAL ACCELERATION, m/s^2 (above {:.0f} mph, above {:.1f} m/s^2) ===".format(
    MIN_MPH, MIN_LAT_ACC))
  report("openpilot steering, UNLIMITED", op_clean)
  report("openpilot steering, a limiter bit", op_limited)
  report("HE was steering (or hands on)", driver)
  if no_flag_data:
    print("  !! controllerStateBP never arrived -- the limiter split above is NOT trustworthy.")

  print("")
  if op_clean:
    p99 = pct(op_clean, 0.99)
    print("  DEMONSTRATED CAPABILITY (p99, openpilot steering, nothing clipping): {:.2f} m/s^2".format(p99))
    print("  A 259 m corner at that is {:.0f} mph.".format((p99 * 259.0) ** 0.5 * MS_TO_MPH))
    if driver:
      dp99 = pct(driver, 0.99)
      print("  His own p99 is {:.2f}. ".format(dp99), end="")
      if dp99 > p99 * 1.15:
        print("HIGHER -- the gap is the PSCM limit he describes.")
      else:
        print("about the same -- nothing has been limiting him, so the target is not PSCM-bound.")
  else:
    print("  NO unlimited openpilot-steered cornering above the thresholds. Nothing to conclude.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
