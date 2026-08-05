#!/usr/bin/env python3
"""BluePilot: dump everything passing assist knows, from the device, in one paste.

RUN THIS ON THE CAR over SSH:

    cd /data/openpilot && python tools/bp_passing_report.py            # the stored drives
    cd /data/openpilot && python tools/bp_passing_report.py --live 60  # watch it decide, 60 s

WHY IT EXISTS
Every diagnostic in this feature has been squeezed into three lines on a panel, because the
standing assumption was that the panel was the only channel -- no SSH, no logs. That assumption is
half retired: "I can do SSH as much as you want... I just won't be able to SSH and do stuff like
that after each drive."

So this is NOT the per-drive workflow. The panel line he reads at a stop is still that. This is for
settling a question that the panel cannot answer in three lines and that is currently costing whole
drives -- run it once, get the answer, go back to driving.

It matters more than it sounds. The panel can say WHICH term refused; it cannot say what the four
numbers were doing over an hour, and it cannot be read at 70 mph anyway. Five drives produced
twenty-one passes, zero suggestions, and no idea which of four constants was responsible. --live
answers that in a couple of minutes of ordinary driving, and --history hands over a fortnight.

Neither mode writes anything or touches the car.
"""

import argparse
import json
import time
from collections import Counter


# The left gate's terms, in the order passing_assist.py evaluates them. Mirrored rather than
# imported: this runs on the device where importing the planner drags in the whole stack.
GEO_TERMS = ("edge unsure", "paint", "lane width", "room past it")


def _params():
  from openpilot.common.params import Params
  return Params()


def history() -> int:
  p = _params()
  hist = p.get("PassingAssistHistory") or []
  last = p.get("PassingAssistLastDrive")
  if last:
    hist = list(hist) + [dict(last, build="(in progress)")]
  if not hist:
    print("no drives recorded yet")
    return 0
  print(f"{len(hist)} drives\n")
  for i, d in enumerate(hist, 1):
    build = d.get("build", "?")
    print(f"--- drive {i}  build {build}")
    print(json.dumps({k: v for k, v in d.items() if k != "build"}, sort_keys=True))
  return 0


def live(seconds: float) -> int:
  import cereal.messaging as messaging

  sm = messaging.SubMaster(['longitudinalPlanSP', 'carState'])
  refusals: Counter = Counter()
  sums: dict[str, float] = {}
  blocked: Counter = Counter()
  frames = 0
  wanted = 0.0
  seen_left_ok = 0

  print(f"watching for {seconds:.0f}s -- drive normally, then paste everything below\n")
  end = time.monotonic() + seconds
  while time.monotonic() < end:
    sm.update(100)
    if not sm.updated['longitudinalPlanSP']:
      continue
    pa = sm['longitudinalPlanSP'].passingAssist
    frames += 1
    blocked[str(pa.blockedBy)] += 1
    if pa.leftGeometryOk:
      seen_left_ok += 1
      continue
    # Same order as the gate. Whichever it hits first is the one to act on.
    if pa.leftEdgeStd > 0.5:
      term, val = GEO_TERMS[0], pa.leftEdgeStd
    elif pa.leftLineProb < 0.5:
      term, val = GEO_TERMS[1], pa.leftLineProb
    elif not (3.0 <= pa.leftLaneWidth <= 5.0):
      term, val = GEO_TERMS[2], pa.leftLaneWidth
    else:
      term, val = GEO_TERMS[3], pa.leftEdgeBeyond
    refusals[term] += 1
    sums[term] = sums.get(term, 0.0) + val
    wanted = pa.wantedSeconds

  if not frames:
    print("nothing published -- is the car on and openpilot running?")
    return 1

  print(f"frames        {frames}")
  print(f"wantedSeconds {wanted:.0f}")
  print(f"left lane OK  {seen_left_ok} frames ({seen_left_ok / frames * 100:.0f}%)")
  print("\nwhat blocked it:")
  for name, n in blocked.most_common():
    print(f"  {name:20} {n / frames * 100:5.1f}%")
  if refusals:
    print("\nwhen the LEFT side was refused, by which term:")
    for term, n in refusals.most_common():
      print(f"  {term:14} {n / sum(refusals.values()) * 100:5.1f}%   mean {sums[term] / n:.2f}")
  return 0


def main() -> int:
  ap = argparse.ArgumentParser(description=__doc__,
                               formatter_class=argparse.RawDescriptionHelpFormatter)
  ap.add_argument("--live", type=float, metavar="SECONDS", default=None,
                  help="watch the live decision instead of the stored drives")
  args = ap.parse_args()
  return live(args.live) if args.live else history()


if __name__ == "__main__":
  raise SystemExit(main())
