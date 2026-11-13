#!/usr/bin/env python3
"""`noLaneAvailable` is the top blocker. WHICH of those refusals are actually wrong?

HIS POINT, 2026-08-20, and it reframes the whole number: *"A lot of lane changes are correct, I
just have to wait for no one to be in that lane. Once we get BLIS and rear radar, it won't make
these suggestions as much."* A refusal while a car sits in that lane is the system being RIGHT.
Lumping it in with "the camera cannot find paint" makes a working gate look broken.

So this splits the refusals in two:

  OCCUPIED   the radar was tracking a vehicle in the lane at that moment. Correct refusal, and the
             one that BLIS and a rear radar will eventually let it state properly instead of
             reporting as an absent lane.
  EMPTY      nothing was there and it still said no lane. Split AGAIN by the lane anchor, because
             "no lane to your left" is simply TRUE in the leftmost lane and that refusal is right
             as well. Only what survives both splits is a camera problem.

For the EMPTY subset it then reports which geometry term did the refusing, independently, because
they overlap -- a frame can fail paint and width at once, and a share that sums past 100% is the
honest shape rather than a bug.

Read-only. Run on the device from /data/openpilot.
"""
import glob
import os
import re
import sys
from collections import Counter

MIN_SPEED = 15.0
# The ENUM NAME, not its ordinal. `int()` on a capnp enum raises TypeError on the device -- it is
# in CLAUDE.md, it took down the UI once, and the first version of this tool did it anyway and
# reported "no noLaneAvailable frames" on drives where it was the top blocker at 89%. str() returns
# the bare name.
NO_LANE = "noLaneAvailable"

# The gate's own thresholds, imported rather than restated -- a tool carrying its own copy stops
# measuring the shipped gate, which is how a 1.8% finding got produced against invented numbers.
sys.path.insert(0, "/data/openpilot")


def segments_in_order(route):
  segs = glob.glob(f"/data/media/0/realdata/{route}--*")

  def idx(p):
    m = re.search(r"--(\d+)$", p)
    return int(m.group(1)) if m else -1
  return sorted(segs, key=idx)


def main():
  routes = sys.argv[1:]
  if not routes:
    sys.exit("usage: bp_no_lane_why.py <route> [route ...]")
  from openpilot.tools.lib.logreader import LogReader
  from openpilot.sunnypilot.selfdrive.controls.lib.passing_assist import (
    MAX_LANE_WIDTH_M, MAX_ROAD_EDGE_STD, MIN_ADJACENT_LINE_PROB, MIN_EDGE_BEYOND_LINE_M,
    MIN_LANE_WIDTH_M,
  )
  from openpilot.sunnypilot.selfdrive.controls.lib.lane_anchor import LaneAnchor
  from cereal import custom
  fields = set(custom.LongitudinalPlanSP.PassingAssist.schema.fieldnames)
  for f in ("blockedBy", "leftLineProb", "leftLaneWidth", "leftEdgeStd", "leftEdgeBeyond",
            "adjacentLeft"):
    if f not in fields:
      sys.exit(f"passingAssist has no field {f!r} -- this tool would report a confident zero")

  speed = 0.0
  lanes = 0
  one_way = False
  anchor = LaneAnchor()
  leftmost = 0
  not_leftmost = 0
  lane_unknown = 0
  refused = 0
  occupied = 0
  empty = 0
  unavailable = 0
  terms = Counter()
  empty_terms = Counter()
  by_class = Counter()
  hwy = "?"

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
        elif w == "mapdOut":
          hwy = str(m.mapdOut.highwayClass)
          lanes = int(m.mapdOut.lanes)
          one_way = bool(m.mapdOut.oneWay)
        elif w == "modelV2":
          # The anchor has to be fed every frame or its latch means nothing.
          try:
            probs = m.modelV2.laneLineProbs
            anchor.update(0.05, float(m.modelV2.roadEdges[1].y[0]),
                          float(m.modelV2.roadEdgeStds[1]), lanes, one_way,
                          float(probs[0]), float(probs[3]))
          except (IndexError, AttributeError):
            pass
        elif w == "longitudinalPlanSP":
          if speed < MIN_SPEED:
            continue
          pa = m.longitudinalPlanSP.passingAssist
          # Deliberately NOT wrapped. A tool that turns a schema mistake into a silent zero is
          # worse than one that crashes -- that is how this file first reported no refusals at all.
          if str(pa.blockedBy) != NO_LANE:
            continue
          refused += 1
          left = pa.adjacentLeft
          if not bool(left.available):
            unavailable += 1
          elif bool(left.occupied):
            occupied += 1
            continue
          else:
            empty += 1
            by_class[hwy] += 1
            # ALREADY IN THE LEFT LANE? Then "no lane available" is simply true, and this refusal
            # belongs with the correct ones. This split did not exist before the four-line lane
            # counting landed -- the anchor could not reach a leftmost claim on a wide road at all.
            if anchor.in_leftmost_lane():
              leftmost += 1
            elif anchor.index is not None:
              not_leftmost += 1
            else:
              lane_unknown += 1

          # Which term refused it. Independent, so they overlap on purpose.
          hits = []
          if float(pa.leftLineProb) < MIN_ADJACENT_LINE_PROB:
            hits.append("paint (no line beyond ours)")
          lw = float(pa.leftLaneWidth)
          if not MIN_LANE_WIDTH_M <= lw <= MAX_LANE_WIDTH_M:
            hits.append(f"lane width outside {MIN_LANE_WIDTH_M}-{MAX_LANE_WIDTH_M} m")
          if float(pa.leftEdgeStd) > MAX_ROAD_EDGE_STD:
            hits.append("road edge unsure")
          if float(pa.leftEdgeBeyond) < MIN_EDGE_BEYOND_LINE_M:
            hits.append("no room past the lane")
          for h in hits:
            terms[h] += 1
            if bool(left.available) and not bool(left.occupied):
              empty_terms[h] += 1
          if not hits:
            terms["(nothing failed -- refused elsewhere)"] += 1

  if not refused:
    sys.exit("no noLaneAvailable frames above the speed floor on these routes")

  print(f"noLaneAvailable refusals, above {MIN_SPEED * 2.237:.0f} mph: {refused}")
  print()
  print("WAS THERE ACTUALLY A CAR IN THAT LANE?")
  for label, n in (("OCCUPIED -- correct refusal", occupied),
                   ("EMPTY -- worth attacking", empty),
                   ("radar had no view of the lane", unavailable)):
    print(f"  {label:<34} {n:>7}  {100.0 * n / refused:>5.1f}%")
  print()
  print("OF THE EMPTY ONES, WHERE WERE WE?")
  for label, n in (("already in the LEFTMOST lane -- also correct", leftmost),
                   ("a lane DID exist to our left -- the real target", not_leftmost),
                   ("lane position unknown", lane_unknown)):
    print(f"  {label:<46} {n:>7}  {100.0 * n / max(empty, 1):>5.1f}%")
  print()
  print("OF THE EMPTY ONES, WHICH TERM REFUSED IT  (independent, so these overlap)")
  for k, v in empty_terms.most_common():
    print(f"  {k:<38} {v:>7}  {100.0 * v / max(empty, 1):>5.1f}%")
  print()
  print("EMPTY REFUSALS BY ROAD CLASS")
  for c, v in by_class.most_common(6):
    print(f"  {c:<16} {v:>7}  {100.0 * v / max(empty, 1):>5.1f}%")
  print()
  print(f"THE ACTUAL TARGET: {not_leftmost} frames -- lane empty, and we were NOT in the leftmost")
  print(f"lane, so a lane demonstrably existed and the gate still said it did not. That is")
  print(f"{100.0 * not_leftmost / refused:.1f}% of all noLaneAvailable refusals.")
  print()
  print("Everything else is the system being right: a car was there, or there was genuinely no")
  print("lane. BLIS and a rear radar will let the occupied ones be stated properly rather than")
  print("reported as an absent lane.")


main()
