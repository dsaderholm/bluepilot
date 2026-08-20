#!/usr/bin/env python3
"""Why does the anchor not know which lane we are in?

On 2026-08-20 the noLaneAvailable split left one honest gap: of the refusals where the lane was
empty, 32.1% happened while the anchor had no lane position at all. That is not a fault in the
passing gate, it is missing coverage in the thing that was built to close it, and "32% unknown"
is not actionable until it is broken into causes.

Each unknown frame is attributed to ONE cause, in the order the anchor itself would hit them, so
the shares add to 100 and the biggest bar is the thing to fix. Every threshold is imported from
lane_anchor rather than restated -- a tool carrying its own copy of a constant stops measuring the
shipped code, which has already produced one wrong finding here.

Read-only. Run on the device from /data/openpilot.
"""
import glob
import os
import re
import sys
from collections import Counter

MIN_SPEED = 15.0
NO_LANE = "noLaneAvailable"      # str(), never int() -- see bp_no_lane_why.py
sys.path.insert(0, "/data/openpilot")


def segments_in_order(route):
  segs = glob.glob(f"/data/media/0/realdata/{route}--*")

  def idx(p):
    m = re.search(r"--(\d+)$", p)
    return int(m.group(1)) if m else -1
  return sorted(segs, key=idx)


def main():
  args = sys.argv[1:]
  # --refusals restricts to the exact frames the noLaneAvailable split left unexplained: the gate
  # refused, the lane was EMPTY, and the anchor had no position. Whole-drive coverage and coverage
  # in those frames are different questions with different denominators, and quoting one for the
  # other is how a 32% turned into a 38%.
  only_refusals = "--refusals" in args
  routes = [a for a in args if not a.startswith("--")]
  if not routes:
    sys.exit("usage: bp_lane_unknown_why.py [--refusals] <route> [route ...]")
  from openpilot.tools.lib.logreader import LogReader
  from openpilot.sunnypilot.selfdrive.controls.lib.lane_anchor import (
    LaneAnchor, NO_LEFT_LINE_PROB, lane_bounds_from_lines,
  )

  anchor = LaneAnchor()
  refusing_empty = False
  speed = 0.0
  lanes = 0
  one_way = False
  hwy = "?"
  cause = Counter()
  by_class = Counter()
  total = 0
  known = 0

  for route in routes:
    # A FRESH ANCHOR AND FRESH CARRIED STATE PER ROUTE. One object across several routes carries
    # the last latched index and the last mapdOut of one drive into the opening frames of the
    # next, which is the stale-state class that produced a bogus 12% reading once already.
    anchor = LaneAnchor()
    speed, lanes, one_way, hwy = 0.0, 0, False, "?"
    for s in segments_in_order(route):
      p = os.path.join(s, "rlog.zst")
      if not os.path.exists(p):
        continue
      for m in LogReader(p):
        w = m.which()
        if w == "carState":
          speed = float(m.carState.vEgo)
        elif w == "longitudinalPlanSP":
          pa = m.longitudinalPlanSP.passingAssist
          left = pa.adjacentLeft
          refusing_empty = (str(pa.blockedBy) == NO_LANE and bool(left.available)
                            and not bool(left.occupied))
        elif w == "mapdOut":
          hwy = str(m.mapdOut.highwayClass)
          lanes = int(m.mapdOut.lanes)
          one_way = bool(m.mapdOut.oneWay)
        elif w == "modelV2":
          if speed < MIN_SPEED:
            continue
          try:
            probs = m.modelV2.laneLineProbs
            fl, fr = float(probs[0]), float(probs[3])
            std = float(m.modelV2.roadEdgeStds[1])
            d = abs(float(m.modelV2.roadEdges[1].y[0]))
          except (IndexError, AttributeError):
            continue
          idx = anchor.update(0.05, d, std, lanes, one_way, fl, fr)
          if only_refusals and not refusing_empty:
            continue
          total += 1
          if idx is not None:
            known += 1
            continue

          by_class[hwy] += 1
          # ONE cause per frame, in the order the anchor hits them.
          if not one_way:
            cause["road is TWO-WAY (map lanes counts both directions)"] += 1
            continue
          if lanes <= 0:
            cause["map has no lane count here"] += 1
            continue
          if lanes == 1:
            # `lane_bounds_from_lines` refuses n <= 1 BEFORE it looks at the lines, so a one-lane
            # carriageway has no bound by design. Without this bucket every ramp fell through to
            # "NEITHER outer line seen -- contradiction" or "two lanes with a line each side",
            # which reads as an actionable map-vs-camera disagreement when nothing disagreed.
            cause["single-lane road -- no bound is possible"] += 1
            continue
          bound = lane_bounds_from_lines(fl, fr, lanes)
          if bound is None:
            if fl < NO_LEFT_LINE_PROB and fr < NO_LEFT_LINE_PROB:
              cause["NEITHER outer line seen -- contradiction, claims nothing"] += 1
            elif lanes < 3:
              cause["two lanes with a line each side -- inconsistent"] += 1
            else:
              cause["no bound for another reason"] += 1
            continue
          # A bound exists but did not pin, and the edge could not narrow it. Taken from the
          # anchor rather than recomputed: a local copy of the trust rule omitted
          # `lane_index_from_edge`'s out-of-range refusal, so frames the anchor never trusted were
          # being reported as "edge trusted but contradicted it".
          edge_ok = anchor.edge_index is not None
          if bound[0] != bound[1]:
            if not edge_ok:
              cause[f"bound is a RANGE {bound} and the edge was untrusted"] += 1
            else:
              cause[f"bound is a RANGE {bound}, edge trusted but contradicted it"] += 1
          else:
            cause["pinned but still unknown -- unexpected, look at this"] += 1

  if not total:
    sys.exit("no moving frames on these routes")
  unknown = total - known
  scope = "empty noLaneAvailable refusals" if only_refusals else "moving frames"
  print(f"{total} {scope}; anchor knew the lane on {known} ({100.0 * known / total:.1f}%)")
  print(f"UNKNOWN on {unknown} frames ({100.0 * unknown / total:.1f}%). Why:")
  print()
  for k, v in cause.most_common():
    print(f"  {k:<56} {v:>7}  {100.0 * v / max(unknown, 1):>5.1f}%")
  print()
  print("UNKNOWN BY ROAD CLASS")
  for c, v in by_class.most_common(8):
    print(f"  {c:<16} {v:>7}  {100.0 * v / max(unknown, 1):>5.1f}%")
  print()
  print("Two-way roads are a REFUSAL BY DESIGN, not a gap: the map's lane count there includes")
  print("oncoming lanes, so counting leftward from the shoulder would walk into them. Only the")
  print("one-way causes below it are coverage worth closing.")


main()
