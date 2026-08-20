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

# THE UNITS DO NOT MATCH, AND THIS COST A REPORTED NUMBER. `speedDeficit` is published in m/s
# (custom.capnp @6) and `minDeficitActive` in MPH (passing_assist.py: `min_deficit_ms * MS_TO_MPH`).
# Comparing them directly demands a lead 2.237x slower than the real gate does, so every
# slow-lead count published from this tool was over a far too strict population.
#
# `minDeficitActive` is also the SETTING, not the live threshold: the gate tests
# `min_deficit_active_ms`, which is the setting times `patience_scale`. Patience only ever RAISES
# the bar, so treating the setting as the threshold over-counts where patience was active. That
# part cannot be reconstructed from the wire, so it is stated rather than silently absorbed.
DEFICIT_MPH_TO_MS = 0.44704

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

  # Fail LOUDLY on a field that does not exist. Every read below is inside a try that treats a
  # missing field as "no data", which is right for a field that is genuinely absent on some frames
  # and catastrophic for one that is misspelled -- it turns a typo into a confident zero.
  from cereal import custom
  _pa_fields = set(custom.LongitudinalPlanSP.PassingAssist.schema.fieldnames)
  for _f in ("speedDeficit", "minDeficitActive", "hasLead"):
    if _f not in _pa_fields:
      sys.exit(f"passingAssist has no field {_f!r} -- this tool would silently report zeros")

  anchor = LaneAnchor()
  speed = 0.0
  lanes = 0
  one_way = False
  hwy = ""
  blinker = False
  prev_blinker = False
  prev_right = False
  lead_slow = False

  seen = Counter()          # lane index (or None) -> frames
  by_class = Counter()      # highwayClass -> frames with an answer
  cls_total = Counter()
  leftmost_with_lead = 0
  full_gate = 0
  right_geo = False
  lead_frames = 0
  held_across_change = 0
  changes = 0
  fresh_frames = 0
  witness_left = 0
  bounded = 0
  pinned = 0
  both_spoke = 0
  contradictions = 0
  moving = 0

  for s in segs:
    f = os.path.join(s, "rlog.zst")
    if not os.path.exists(f):
      continue
    for m in LogReader(f):
      w = m.which()
      if w == "carState":
        speed = float(m.carState.vEgo)
        left_blink = bool(m.carState.leftBlinker)
        right_blink = bool(m.carState.rightBlinker)
        blinker = left_blink or right_blink
      elif w == "mapdOut":
        try:
          lanes = int(m.mapdOut.lanes)
          one_way = bool(m.mapdOut.oneWay)
          hwy = str(m.mapdOut.highwayClass)
        except (AttributeError, TypeError, ValueError):
          lanes, one_way = 0, False
      elif w == "longitudinalPlanSP":
        # RECONSTRUCTED, because `lead_is_slow` is internal to the detector and is NOT on the wire.
        # This read `passingAssist.leadIsSlow` for a day. That field has never existed, the
        # AttributeError was swallowed by a broad except, and the tool reported "no slow-lead
        # frames on this drive" across six routes and 60,000 frames -- on drives that produced 43
        # suggestions, which cannot happen without a slow lead. A diagnostic that answers zero
        # when it should crash is worse than no diagnostic. Hence require_field() below.
        pa = m.longitudinalPlanSP.passingAssist
        deficit = float(pa.speedDeficit)                              # m/s
        threshold = float(pa.minDeficitActive) * DEFICIT_MPH_TO_MS     # mph on the wire -> m/s
        lead_slow = bool(pa.hasLead) and threshold > 0 and deficit >= threshold
      elif w == "modelV2":
        if speed < MIN_SPEED:
          continue
        moving += 1
        try:
          std = float(m.modelV2.roadEdgeStds[RE_RIGHT])
          d = float(m.modelV2.roadEdges[RE_RIGHT].y[0])
        except (AttributeError, IndexError, TypeError, ValueError):
          std, d = None, None

        # THE FALLING EDGE, because that is when the car does it. `_stand_down` is reached from
        # the stalk path when the blinker goes OFF, not when it comes on -- so hooking the rising
        # edge shifted the replay's anchor a whole lane change early and every number below was
        # measured against behavior the car does not have.
        if prev_blinker and not blinker:
          changes += 1
          had = anchor.index is not None
          anchor.note_lane_change(rightward=prev_right)
          if had and anchor.index is not None:
            held_across_change += 1
        prev_blinker = blinker
        prev_right = right_blink if blinker else prev_right

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
          if getattr(anchor, "edge_index", None) is not None:
            both_spoke += 1
            if getattr(anchor, "contradiction", False):
              contradictions += 1
        if lead_slow:
          lead_frames += 1
          if anchor.in_leftmost_lane():
            leftmost_with_lead += 1
            if right_geo:
              full_gate += 1

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
  print("DO THE TWO WITNESSES AGREE?  (edge index vs the range the lines allow)")
  if both_spoke:
    print(f"  both spoke on {both_spoke} frames; they CONTRADICTED on {contradictions} "
          f"({100.0 * contradictions / both_spoke:.1f}%)")
    print("  ANSWERED: the edge reads to the outer edge of the SHOULDER, so it lands about a lane")
    print("  left of the truth, and the disagreement is not occasional. Measured CLEAN on freeway")
    print("  after the stale-state fix: 95.4% and 99.2%. The 79-80% quoted before that fix was")
    print("  diluted by frames where neither witness had actually spoken.")
    print("  The anchor refuses a contradicted edge reading, so this is a MONITOR, and near 100%")
    print("  is the healthy reading. A drop toward zero means a witness went quiet, not that the")
    print("  two started agreeing -- check availability before reading anything into it.")
  else:
    print("  never both at once on this drive; the cross-check cannot be scored here")
  print()
  print("THE HOG CONJUNCTION")
  if lead_frames:
    print(f"  slow lead present: {lead_frames} frames")
    print(f"  ...and anchor says LEFTMOST: {leftmost_with_lead} "
          f"({100.0 * leftmost_with_lead / lead_frames:.1f}%)")
    print(f"  ...and the FULL gate (+ a lane to our right): {full_gate} "
          f"({100.0 * full_gate / lead_frames:.1f}%)")
    print("  The warning fires on the FULL gate. The middle line is two of its three terms and is")
    print("  always higher -- reading that gap as the anchor over-claiming leftmost sends work at")
    print("  the wrong term, which is what the two-term version of this invited.")
  else:
    print("  no slow-lead frames on this drive; the conjunction cannot be scored here")
  print()
  print("LANE CHANGES")
  print(f"  blinker onsets: {changes}   anchor FOLLOWED the change: {held_across_change}")
  print("  Following is now the intended behavior, not a bug -- the anchor shifts the index by one")
  print("  in the direction of travel. What protects it is that update() drops any latched index")
  print("  the lane lines no longer allow, so a wrong shift dies on the next frame rather than")
  print("  being carried. A count near zero here means the shift is being refused every time,")
  print("  which is worth looking at: either no index was latched, or the map lane count moved.")


if __name__ == "__main__":
  main()
