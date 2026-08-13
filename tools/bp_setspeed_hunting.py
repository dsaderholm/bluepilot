#!/usr/bin/env python3
"""FusionPilot: find where the SET SPEED oscillated — raised and lowered over and over.

Reported 2026-08-12: "At one point on a drive today, it raised and lowered my cruise over and over
which was really strange. It was when the speed limit changed to 25."

Every other diagnostic here looks for a sustained move -- a slowdown, a curve, a hold change. An
oscillation is the opposite shape: lots of movement with no net travel, which those tools average
away to nothing. This looks for DIRECTION REVERSALS in the dash set speed, which is the signature.

For each burst it prints who was asking, what each source wanted, and the driver's hold, because the
three plausible causes look identical from the seat and different in the log:

  * the target sitting on Ford's 20 mph ACC floor, so ICBM commands down against a car that will not
    go lower and then corrects back up
  * the hold re-baselining -- an uncommanded set-speed move reads as a driver press (fallbackIdle),
    which moves the baseline, which moves the target, which moves the set speed again
  * two sources alternating frame to frame, each with a different target

USAGE, on the device:

    cd /data/openpilot && python tools/bp_setspeed_hunting.py --route 00000361--b42670c35f
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import deque

from openpilot.tools.bp_logtime import DriveClock

REALDATA = "/data/media/0/realdata"
MS_TO_MPH = 2.23694
NO_TARGET_MPH = 500.0


def seg_index(name: str) -> int:
  tail = name.rsplit("--", 1)[-1]
  return int(tail) if tail.isdigit() else -1


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--route", default=None)
  ap.add_argument("--reversals", type=int, default=4,
                  help="direction changes inside the window to count as hunting")
  ap.add_argument("--window", type=float, default=20.0, help="seconds")
  ap.add_argument("--max-segments", type=int, default=40)
  args = ap.parse_args()

  try:
    from openpilot.tools.lib.logreader import LogReader
  except ImportError as e:
    sys.exit(f"no LogReader ({e}); run from /data/openpilot")

  entries = sorted((d for d in os.listdir(REALDATA) if "--" in d), key=seg_index)
  route = args.route or entries[-1].rsplit("--", 1)[0]
  segs = [d for d in entries if d.startswith(route + "--")][:args.max_segments]
  print(f"# route {route}, {len(segs)} segments, >={args.reversals} reversals in {args.window:.0f}s\n")

  st = {"dash": 0.0, "v": 0.0, "src": "?", "hold": 0.0, "tgt": 0.0, "state": "?",
        "slaV": 0.0, "visV": 0.0, "mapV": 0.0, "lead": 0.0}
  hist: deque = deque()          # (ts, dash, snapshot)
  clock = DriveClock()
  last_dash = None
  last_dir = 0
  reported_until = -1e9
  found = 0

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
          cs = msg.carState
          st["dash"] = cs.cruiseState.speedCluster * MS_TO_MPH
          st["v"] = cs.vEgo * MS_TO_MPH
        elif w == "longitudinalPlanSP":
          lp = msg.longitudinalPlanSP
          st["src"] = str(lp.longitudinalPlanSource)
          st["visV"] = lp.smartCruiseControl.vision.vTarget * MS_TO_MPH
          st["mapV"] = lp.smartCruiseControl.map.vTarget * MS_TO_MPH
          try:
            st["slaV"] = lp.speedLimit.resolver.speedLimit * MS_TO_MPH
          except Exception:  # noqa: BLE001
            pass
        elif w == "selfdriveStateSP":
          icbm = msg.selfdriveStateSP.intelligentCruiseButtonManagement
          st["hold"] = float(icbm.vBaseline)
          st["tgt"] = float(icbm.vTarget)
          st["state"] = str(icbm.state)
        elif w == "radarState":
          ld = msg.radarState.leadOne
          st["lead"] = ld.dRel if ld.status else 0.0
        else:
          continue
      except Exception:  # noqa: BLE001
        continue

      d = round(st["dash"])
      if last_dash is None:
        last_dash = d
        continue
      if d != last_dash:
        direction = 1 if d > last_dash else -1
        if last_dir and direction != last_dir:
          hist.append((ts, d, dict(st)))
        last_dir = direction
        last_dash = d

      while hist and ts - hist[0][0] > args.window:
        hist.popleft()

      if len(hist) >= args.reversals and ts > reported_until:
        found += 1
        reported_until = ts + args.window
        print(f"===== hunting #{found}: {len(hist)} reversals in {args.window:.0f}s, "
              f"ending t+{ts:.0f}s =====")
        print("   time   dash   mph   hold  icbmTgt  icbmState    source       SLA   sccVis  sccMap  lead")
        for hts, hd, s in hist:
          v = "  --  " if s["visV"] > NO_TARGET_MPH else f"{s['visV']:5.0f} "
          m = "  --  " if s["mapV"] > NO_TARGET_MPH else f"{s['mapV']:5.0f} "
          sla = "  --  " if s["slaV"] <= 0 or s["slaV"] > NO_TARGET_MPH else f"{s['slaV']:5.0f} "
          print(f"  t+{hts:6.0f} {hd:5.0f} {s['v']:5.0f} {s['hold']:6.0f} {s['tgt']:8.0f}"
                f"  {s['state']:<11} {s['src']:<12} {sla} {v} {m} {s['lead']:5.0f}")
        print()
        hist.clear()

  print(f"=== {found} bursts of set-speed hunting ===")
  print("  hold changing during a burst means the baseline is being re-derived from the car's own")
  print("  set-speed movement. icbmTgt pinned near 20 mph means it is fighting Ford's ACC floor.")
  print("  source alternating frame to frame means two controllers are trading the target.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
