#!/usr/bin/env python3
"""Replay the REAL LaneAnchor against a recorded drive. Does it know which lane he was in?

WHY THIS EXISTS AND WHY IT COMES BEFORE ANY MORE WIRING. The anchor was built from a MEASUREMENT of
the right road edge -- trusted on 5-15% of motorway frames, p50 4.6-4.8 m -- and never once run as
an estimator against a road. Those are different claims. "The edge is readable" does not imply "the
lane index derived from it is right", and the hog gate now depends on the second one.

The failure that would matter is silent: an anchor that confidently says "lane 1 of 5" while he is
in the far left lane makes the hog warning wrong in a NEW way rather than fixing it. The old bug at
least had an obvious cause.

WHAT TO READ:

  availability   the share of moving frames where the anchor has any answer at all. Low is fine --
                 unknown means the gate refuses -- but near zero would mean the hog fix disabled
                 the warning rather than correcting it, and he should be told that plainly.
  distribution   which lanes it claims. On a freeway drive the mass should sit in the middle and
                 right lanes with a real tail in the left. All-one-value is the tell for a stuck
                 estimator; leftmost-heavy is the tell for the old bug in a new costume.
  vs the hog     how often it says LEFTMOST while a slow lead is present -- the exact conjunction
                 the warning fires on. Compare against hogCount from the same drive.
  at a change    the anchor must DROP when the blinker goes on, and re-establish after. If it holds
                 an index across a lane change it is dead reckoning through the one event it cannot
                 survive.

Read-only. Run on the device from /data/openpilot.

    python tools/bp_anchor_replay.py <route> [path/to/candidate/lane_anchor.py]

The second argument replays a CANDIDATE module instead of the installed one, so a change can be
scored against a recorded drive before it is shipped to a car that is driven. Without it there is
no way to measure an anchor change except by deploying it first, which is backwards.
"""
import glob
import importlib.util
import os
import re
import sys
from collections import Counter

MIN_SPEED = 15.0     # m/s -- freeway only; the anchor is not for parking lots
DT = 0.05


def segments_in_order(route):
  """Drive order, not string order: sorted(glob) puts --10 before --2."""
  segs = glob.glob(f"/data/media/0/realdata/{route}--*")

  def idx(p):
    m = re.search(r"--(\d+)$", p)
    return int(m.group(1)) if m else -1
  return sorted(segs, key=idx)


def main():
  route = sys.argv[1] if len(sys.argv) > 1 else "0000038e"
  candidate = sys.argv[2] if len(sys.argv) > 2 else None
  sys.path.insert(0, "/data/openpilot")
  from openpilot.tools.lib.logreader import LogReader

  if candidate:
    spec = importlib.util.spec_from_file_location("candidate_lane_anchor", candidate)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    LaneAnchor = mod.LaneAnchor
    print(f"replaying CANDIDATE module: {candidate}")
  else:
    from openpilot.sunnypilot.selfdrive.controls.lib.lane_anchor import LaneAnchor

  # Line and edge indices. Taken from the shipped module when it has them, so this cannot drift
  # from the code it is scoring -- and falling back to the literals only when it does not, which
  # is how a candidate gets replayed on a device whose passing_assist.py predates the fourth line.
  try:
    from openpilot.sunnypilot.selfdrive.controls.lib.passing_assist import (
      LL_FAR_LEFT, LL_FAR_RIGHT, RE_RIGHT,
    )
  except ImportError:
    LL_FAR_LEFT, LL_FAR_RIGHT, RE_RIGHT = 0, 3, 1
    print("note: this openpilot predates LL_FAR_RIGHT; using literal line indices")

  segs = segments_in_order(route)
  if not segs:
    sys.exit(f"no segments for {route}")

  anchor = LaneAnchor()
  speed = 0.0
  lanes = 0
  one_way = False
  hwy = ""
  blinker = False
  prev_blinker = False
  lead_slow = False

  seen = Counter()          # lane index (or None) -> frames
  by_class = Counter()      # highwayClass -> frames with an answer
  cls_total = Counter()
  leftmost_with_lead = 0
  lead_frames = 0
  held_across_change = 0
  changes = 0
  fresh_frames = 0
  witness_left = 0
  bounded = 0
  pinned = 0
  moving = 0

  for s in segs:
    f = os.path.join(s, "rlog.zst")
    if not os.path.exists(f):
      continue
    for m in LogReader(f):
      w = m.which()
      if w == "carState":
        speed = float(m.carState.vEgo)
        blinker = bool(m.carState.leftBlinker) or bool(m.carState.rightBlinker)
      elif w == "mapdOut":
        try:
          lanes = int(m.mapdOut.lanes)
          one_way = bool(m.mapdOut.oneWay)
          hwy = str(m.mapdOut.highwayClass)
        except (AttributeError, TypeError, ValueError):
          lanes, one_way = 0, False
      elif w == "longitudinalPlanSP":
        try:
          lead_slow = bool(m.longitudinalPlanSP.passingAssist.leadIsSlow)
        except (AttributeError, TypeError, ValueError):
          lead_slow = False
      elif w == "modelV2":
        if speed < MIN_SPEED:
          continue
        moving += 1
        try:
          std = float(m.modelV2.roadEdgeStds[RE_RIGHT])
          d = float(m.modelV2.roadEdges[RE_RIGHT].y[0])
        except (AttributeError, IndexError, TypeError, ValueError):
          std, d = None, None

        # The real hook: the anchor is dropped on a driver lane change.
        if blinker and not prev_blinker:
          changes += 1
          had = anchor.index is not None
          anchor.note_lane_change()
          if had and anchor.index is not None:
            held_across_change += 1
        prev_blinker = blinker

        try:
          flp = float(m.modelV2.laneLineProbs[LL_FAR_LEFT])
          frp = float(m.modelV2.laneLineProbs[LL_FAR_RIGHT])
        except (AttributeError, IndexError, TypeError, ValueError):
          flp = frp = None
        idx = anchor.update(DT, d, std, lanes, one_way, flp, frp)
        if anchor.confident:
          fresh_frames += 1
        seen[idx] += 1
        cls_total[hwy] += 1
        if idx is not None:
          by_class[hwy] += 1
        if anchor.no_lane_left:
          witness_left += 1
        if anchor.line_bounds is not None:
          bounded += 1
          if anchor.line_bounds[0] == anchor.line_bounds[1]:
            pinned += 1
        if lead_slow:
          lead_frames += 1
          if anchor.in_leftmost_lane():
            leftmost_with_lead += 1

  if not moving:
    sys.exit("no moving frames")

  known = sum(v for k, v in seen.items() if k is not None)
  print(f"route {route}: {moving} moving frames above {MIN_SPEED * 2.237:.0f} mph")
  print()
  print(f"AVAILABILITY  anchor had an answer on {known} frames ({100.0 * known / moving:.1f}%)")
  print(f"              of which FRESH readings: {fresh_frames} "
        f"({100.0 * fresh_frames / moving:.1f}% of all frames) -- the rest are latched")
  print()
  print("LANE INDEX CLAIMED  (0 = far right)")
  for k in sorted(seen, key=lambda x: (x is None, x)):
    label = "unknown" if k is None else f"lane {k}"
    print(f"  {label:>9}  {seen[k]:>7}  {100.0 * seen[k] / moving:>5.1f}%")
  print()
  print("BY ROAD CLASS  (share of frames with an answer)")
  for c in sorted(cls_total, key=lambda x: -cls_total[x]):
    if cls_total[c] < 200:
      continue
    print(f"  {c:<14} {by_class[c]:>7} of {cls_total[c]:>7}  {100.0 * by_class[c] / cls_total[c]:>5.1f}%")
  print()
  print(f"LANE-LINE WITNESS  said NO LANE LEFT on {witness_left} frames "
        f"({100.0 * witness_left / moving:.1f}%) -- this is what makes leftmost reachable")
  print()
  print(f"FOUR-LINE BOUND   narrowed the lane on {bounded} frames "
        f"({100.0 * bounded / moving:.1f}%), PINNED it exactly on {pinned} "
        f"({100.0 * pinned / moving:.1f}%)")
  print()
  print("THE HOG CONJUNCTION")
  if lead_frames:
    print(f"  slow lead present: {lead_frames} frames")
    print(f"  ...and anchor says LEFTMOST: {leftmost_with_lead} "
          f"({100.0 * leftmost_with_lead / lead_frames:.1f}%)")
    print("  Compare with hogCount/hogSeconds from bp_passing_report for the same drive.")
  else:
    print("  no slow-lead frames on this drive; the conjunction cannot be scored here")
  print()
  print("LANE CHANGES")
  print(f"  blinker onsets: {changes}   anchor still held an index afterwards: {held_across_change}")
  if held_across_change:
    print("  *** the anchor survived a lane change. note_lane_change is not doing its job. ***")


if __name__ == "__main__":
  main()
