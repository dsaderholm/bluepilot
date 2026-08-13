#!/usr/bin/env python3
"""FusionPilot: which source slowed the car, for any slowdown, not just exit-sized ones.

Three drives running, three reports of "went too slow on a highway curve", and three settings
changes made on the ASSUMPTION that SCC-Vision was the source. That was never checked. It could
equally be SCC-Map carrying mapped curve geometry on the highway, Speed Limit Assist, a lead, or the
model-stop path. Changing the vision sensitivity does nothing if vision is not the one asking.

bp_dump_exit.py cannot answer it: it triggers on 55 -> 45 mph, so a 75 -> 60 highway curve never
appears. This one triggers on any sustained drop, at any speed, and attributes it.

For each slowdown it prints who was the plan source at the start, what each SCC controller was
asking for, and the lateral acceleration being demanded -- which is the number the curve settings
actually move, so an unreasonable target is visible as a number rather than a feeling.

USAGE, on the device:

    cd /data/openpilot && python tools/bp_why_slow.py
    python tools/bp_why_slow.py --route 00000042--aa11bb22cc --drop 6
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from collections import deque

from openpilot.tools.bp_logtime import DriveClock

REALDATA = "/data/media/0/realdata"
MS_TO_MPH = 2.23694
STEER_RATIO = 17.07     # FORD_FUSION_MK5
WHEELBASE = 2.85
NO_TARGET_MPH = 500.0   # SCC publishes 255 m/s as "no target"



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

def find_segments(route: str | None) -> list[str]:
  if not os.path.isdir(REALDATA):
    sys.exit(f"no {REALDATA} -- run this on the device")
  entries = sorted((d for d in os.listdir(REALDATA) if "--" in d), key=seg_index)
  if not entries:
    sys.exit("no route segments")
  if route is None:
    route = entries[-1].rsplit("--", 1)[0]
    print(f"# newest route: {route}")
  segs = [os.path.join(REALDATA, d) for d in entries if d.startswith(route + "--")]
  if not segs:
    sys.exit(f"no segments for {route}")
  return segs


def rlog(seg: str) -> str | None:
  for name in ("rlog", "rlog.zst", "rlog.bz2"):
    p = os.path.join(seg, name)
    if os.path.exists(p):
      return p
  return None


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--route", default=None)
  ap.add_argument("--drop", type=float, default=8.0, help="mph lost to count as a slowdown")
  ap.add_argument("--window", type=float, default=12.0, help="seconds to lose it in")
  # Parsing rlogs on the device is slow and a long route has dozens of segments. Capped so a run
  # finishes in a couple of minutes; the occupancy percentages are stable well before the whole drive.
  ap.add_argument("--max-segments", type=int, default=8)
  args = ap.parse_args()

  try:
    from openpilot.tools.lib.logreader import LogReader
  except ImportError as e:
    sys.exit(f"no LogReader ({e}); run from /data/openpilot")

  st = {"v": 0.0, "dash": 0.0, "src": "?", "mapV": 0.0, "mapAct": False,
        "visV": 0.0, "visAct": False, "brake": False, "gas": False, "lead": 0.0, "angle": 0.0}
  hist: deque = deque(maxlen=4000)     # ~40 s of carState
  events = 0
  t0 = None
  # WHOLE-DRIVE occupancy, which is the number that matters most. If SCC-Vision is the plan source
  # for most of a highway drive then it is not slowing for curves, it is simply governing -- and the
  # HOLD badge greys for exactly as long, since hold_suppressed is true whenever the source is not
  # cruise or speedLimitAssist. One state, two complaints.
  src_frames: dict = {}
  plan_frames = 0

  segs = find_segments(args.route)
  if len(segs) > args.max_segments:
    print(f"# {len(segs)} segments; reading the first {args.max_segments} (--max-segments to change)")
    segs = segs[:args.max_segments]
  # Timestamps come from DriveClock, not from a raw t0. A route CAN cross a reboot -- on 00000348
  # that printed a slowdown lasting -1040 s -- but re-basing on ANY backward step, which is what this
  # used to do, mistakes ordinary out-of-order service logging for a reset and inflates the whole
  # timeline. It turned a 753 s drive into timestamps past t+3300. See tools/bp_logtime.py.
  clock = DriveClock()
  for seg in segs:
    path = rlog(seg)
    if path is None:
      continue
    for msg in LogReader(path):
      w = msg.which()
      ts = clock.seconds(msg.logMonoTime)
      t = ts
      try:
        if w == "carState":
          cs = msg.carState
          st["v"] = cs.vEgo * MS_TO_MPH
          st["dash"] = cs.cruiseState.speedCluster * MS_TO_MPH
          st["brake"] = cs.brakePressed
          st["gas"] = cs.gasPressed
          st["angle"] = float(cs.steeringAngleDeg)
          hist.append((t - t0, dict(st)))
        elif w == "radarState":
          ld = msg.radarState.leadOne
          st["lead"] = ld.dRel if ld.status else 0.0
        elif w == "longitudinalPlanSP":
          lp = msg.longitudinalPlanSP
          st["src"] = str(lp.longitudinalPlanSource)
          st["mapV"] = lp.smartCruiseControl.map.vTarget * MS_TO_MPH
          st["mapAct"] = bool(lp.smartCruiseControl.map.active)
          st["visV"] = lp.smartCruiseControl.vision.vTarget * MS_TO_MPH
          st["visAct"] = bool(lp.smartCruiseControl.vision.active)
          src_frames[st["src"]] = src_frames.get(st["src"], 0) + 1
          plan_frames += 1
        else:
          continue
      except Exception:  # noqa: BLE001
        continue

      # A slowdown is `drop` mph lost inside `window` seconds. Compared against the OLDEST sample
      # still inside the window rather than a peak, so a slow steady decay counts the same as an
      # abrupt one -- "too slow through a curve" is usually the gradual kind.
      if len(hist) < 50:
        continue
      now_t, now = hist[-1]
      old = None
      for ts, s in hist:
        if now_t - ts <= args.window:
          old = (ts, s)
          break
      if old is None:
        continue
      if old[1]["v"] - now["v"] < args.drop:
        continue

      events += 1
      print(f"\n===== slowdown #{events}: {old[1]['v']:.0f} -> {now['v']:.0f} mph "
            f"over {now_t - old[0]:.1f}s, at t+{now_t:.0f}s =====")
      print("   time    mph  dash  source        sccMap  sccVis   latAcc  lead  G B")
      shown = 0
      for ts, s in hist:
        if ts < old[0]:
          continue
        shown += 1
        if shown % 25:
          continue
        # Lateral acceleration being DELIVERED, from the steering angle. This is the quantity the
        # curve sensitivity settings move, so it says whether a target was unreasonable.
        curv = math.tan(math.radians(s["angle"] / STEER_RATIO)) / WHEELBASE
        lat = abs((s["v"] / MS_TO_MPH) ** 2 * curv)
        m = "  --  " if not s["mapAct"] or s["mapV"] > NO_TARGET_MPH else f"{s['mapV']:5.0f}*"
        v = "  --  " if not s["visAct"] or s["visV"] > NO_TARGET_MPH else f"{s['visV']:5.0f}*"
        print(f"  t+{ts:6.0f} {s['v']:6.1f} {s['dash']:5.0f}  {s['src']:<12} {m}  {v}  "
              f"{lat:6.2f}  {s['lead']:4.0f}  {'G' if s['gas'] else '.'} "
              f"{'B' if s['brake'] else '.'}")
      hist.clear()

  print("\n=== who GOVERNED the drive, whole route ===")
  if plan_frames:
    for src, n in sorted(src_frames.items(), key=lambda x: -x[1]):
      print(f"  {src:<18} {100.0 * n / plan_frames:5.1f}%  {'#' * int(40.0 * n / plan_frames)}")
    grey = sum(n for k, n in src_frames.items() if k not in ("cruise", "speedLimitAssist"))
    print(f"\n  HOLD badge reads grey for {100.0 * grey / plan_frames:.1f}% of the drive.")
    print("  hold_suppressed is true whenever the source is not cruise or speedLimitAssist, so a")
    print("  high number here explains a permanently grey badge AND permanent curve limiting with")
    print("  ONE cause instead of two.")

  print(f"\n=== {events} slowdowns of >={args.drop:.0f} mph found ===")
  print("  Read the `source` column at the START of each block -- that is who asked. If it is not")
  print("  sccVision, the curve sensitivity settings are the wrong knob and always were.")
  print("  latAcc is what the car actually pulled; the vision factors target 2.0 / sensitivity.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
