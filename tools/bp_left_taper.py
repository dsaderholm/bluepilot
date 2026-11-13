#!/usr/bin/env python3
"""FusionPilot: does the LEFT road edge close in ahead, and could that see a work zone?

WHY THIS EXISTS. Two separate things wait on it.

  THE MISSING SENSE. He reported passing assist trying to move into a construction zone's cones.
  Every gate says that lane is clear and by every signal we read it IS: cones return almost nothing
  on radar, the paint is still under them, and the left road edge is refused as untrustworthy. The
  recorded candidate is the one nobody has measured -- "the model may already know: roadEdges is
  trained on real scenes and a coned taper is a common one. If the left edge moves inward through a
  work zone even at low confidence, that is the signal."

  THE STD DECISION. The separation test showed `MAX_ROAD_EDGE_STD` measures DISTANCE, not lane
  existence, and that dropping it takes motorway reachability 22% -> 65%. It was held back because a
  high std was refusing INCIDENTALLY in coned zones, so dropping it removes an accidental defense.
  If a taper signal exists, it replaces that accident with something deliberate.

WHAT IT MEASURES. The mirror of `_road_widening`, which already does this on the RIGHT to spot an
off-ramp. Gap between ego's own left lane line and the left road edge, near and far:

    near = line[WIDEN_NEAR_IDX] - edge[WIDEN_NEAR_IDX]      y is negative to the left, so the
    far  = line[WIDEN_FAR_IDX]  - edge[WIDEN_FAR_IDX]       edge is the more negative and both
    taper = near - far                                      gaps come back positive

Positive taper means the road is CLOSING IN ahead. That is precisely what `_road_widening` throws
away -- `max(0.0, far - near)` -- and correctly so on the right, where narrowing is a lane ending
the availability test already handles. On the left it is the thing we are hunting.

**IT MUST NOT GATE ON `roadEdgeStds`, and `_road_widening` does.** A work zone is exactly where the
edge reads as untrusted, so inheriting that guard would blind the measurement to its own subject.
`bp_left_edge_truth.py` licenses this: the edge POSITION is steady at every std band, with frame
jump flat at 0.13-0.14 m from std 0.5 to 8+. It is the std that is meaningless, not the position.

  python tools/bp_left_taper.py <route-prefix> [<route-prefix> ...]

THE THREE POPULATIONS, because a distribution on its own decides nothing:

  OPEN TODAY       the gate already offers a left pass here
  WOULD ADMIT      paint, width and beyond all pass and only the std cutoff refuses -- exactly the
                   frames dropping the cutoff would newly open, so it is the set whose safety is
                   under discussion
  REFUSED ANYWAY   something other than std refuses; dropping the cutoff changes nothing here

If WOULD ADMIT carries a heavier narrowing tail than OPEN TODAY, the std cutoff was doing real work
and the change needs the taper gate first. If the two look alike, the cutoff was refusing wide roads
and nothing else, and its work-zone defense was never real.

WHAT A NULL RESULT MEANS, stated up front so it does not get read as a finding. Four ordinary drives
may contain NO work zone at all, in which case a thin tail says the roads were clear, not that the
signal is absent. Absence in a log is evidence about the log's conditions first -- this file has
made that mistake enough times to name it. The tool therefore reports the tail SHAPE and how many
frames reach each candidate threshold, so a later located event can be scored against the same
numbers rather than re-deriving them.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

REALDATA = "/data/media/0/realdata"


def q(v, f):
  if not v:
    return float("nan")
  v = sorted(v)
  return v[min(len(v) - 1, int(f * len(v)))]


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("route", nargs="+")
  ap.add_argument("--segments", type=int, default=0)
  args = ap.parse_args()

  from openpilot.tools.lib.logreader import LogReader
  from openpilot.sunnypilot.selfdrive.controls.lib.passing_assist import (
    MAX_ROAD_EDGE_STD, MIN_ADJACENT_LINE_PROB, MIN_LANE_WIDTH_M, MAX_LANE_WIDTH_M,
    MIN_EDGE_BEYOND_LINE_M, MAX_WIDENING_M, WIDEN_NEAR_IDX, WIDEN_FAR_IDX,
    LL_LEFT, RE_LEFT,
  )

  # FAIL LOUDLY on a field that does not exist -- a broad try around a schema read turns a typo
  # into a confident zero, and a confident zero here reads as "no work zones seen".
  from cereal import custom
  pa_fields = set(custom.LongitudinalPlanSP.PassingAssist.schema.fieldnames)
  for f in ("leftEdgeStd", "leftLineProb", "leftLaneWidth", "leftEdgeBeyond", "leftGeometryOk"):
    if f not in pa_fields:
      sys.exit(f"passingAssist has no field {f!r} -- this tool would silently report zeros")

  all_segs = []
  for route in args.route:
    segs = sorted((d for d in os.listdir(REALDATA) if d.startswith(route)),
                  key=lambda d: int(d.rsplit("--", 1)[-1]) if d.rsplit("--", 1)[-1].isdigit() else -1)
    if args.segments:
      segs = segs[:args.segments]
    if not segs:
      print(f"  (no segments matching {route})")
      continue
    all_segs += segs
  if not all_segs:
    sys.exit(f"no segments matching any of {args.route}")

  # taper values per (road class, population)
  taper = defaultdict(lambda: defaultdict(list))
  counts = defaultdict(Counter)
  cur_hwy = "?"
  cur_speed = 0.0
  cur_taper = None

  for seg in all_segs:
    p = os.path.join(REALDATA, seg, "rlog")
    if not os.path.exists(p):
      p += ".zst"
    if not os.path.exists(p):
      continue
    try:
      lr = LogReader(p)
    except Exception as e:  # noqa: BLE001
      print(f"  (skipped {seg}: {e})")
      continue
    for m in lr:
      w = m.which()
      if w == "mapdOut":
        try:
          cur_hwy = str(m.mapdOut.highwayClass) or "?"
        except Exception:  # noqa: BLE001
          pass
        continue
      if w == "carState":
        cur_speed = float(m.carState.vEgo)
        continue
      if w == "modelV2":
        # DELIBERATELY NOT GATED ON roadEdgeStds. See the docstring: a work zone is exactly where
        # the std explodes, so inheriting _road_widening's guard would blind this to its subject.
        try:
          line = m.modelV2.laneLines[LL_LEFT].y
          edge = m.modelV2.roadEdges[RE_LEFT].y
          if len(line) <= WIDEN_FAR_IDX or len(edge) <= WIDEN_FAR_IDX:
            cur_taper = None
            continue
          near = float(line[WIDEN_NEAR_IDX]) - float(edge[WIDEN_NEAR_IDX])
          far = float(line[WIDEN_FAR_IDX]) - float(edge[WIDEN_FAR_IDX])
          # POSITIVE MEANS CLOSING IN. The sign is the opposite of the right-side widening test on
          # purpose; there growth is the interesting direction, here it is shrinkage.
          cur_taper = near - far
        except (IndexError, AttributeError, TypeError):
          cur_taper = None
        continue
      if w != "longitudinalPlanSP":
        continue
      try:
        pa = m.longitudinalPlanSP.passingAssist
      except Exception:  # noqa: BLE001
        continue
      if cur_speed < 10.0 or cur_taper is None:
        continue

      std = float(pa.leftEdgeStd)
      paint_ok = float(pa.leftLineProb) >= MIN_ADJACENT_LINE_PROB
      width_ok = MIN_LANE_WIDTH_M <= float(pa.leftLaneWidth) <= MAX_LANE_WIDTH_M
      beyond_ok = float(pa.leftEdgeBeyond) >= MIN_EDGE_BEYOND_LINE_M
      others = paint_ok and width_ok and beyond_ok

      if bool(pa.leftGeometryOk):
        pop = "OPEN TODAY"
      elif others and std > MAX_ROAD_EDGE_STD:
        # The exact set dropping the std cutoff would newly open.
        pop = "WOULD ADMIT"
      else:
        pop = "refused anyway"
      taper[cur_hwy][pop].append(cur_taper)
      counts[cur_hwy][pop] += 1

  if not counts:
    sys.exit("no moving frames with a left edge and a left lane line on these routes")

  print(f"routes {args.route}   moving frames (>10 m/s) with both a left line and a left edge")
  print(f"  taper = (gap at idx {WIDEN_NEAR_IDX}) - (gap at idx {WIDEN_FAR_IDX}); "
        f"POSITIVE = road closing in ahead")
  print(f"  right-side sibling fires at MAX_WIDENING_M = {MAX_WIDENING_M} m of GROWTH\n")

  for hwy in sorted(counts, key=lambda h: -sum(counts[h].values())):
    tot = sum(counts[hwy].values())
    if tot < 500:
      continue
    print(f"  {hwy}   {tot} frames")
    print(f"    {'population':<16} {'n':>7} {'share':>7} | {'p50':>7} {'p90':>7} {'p99':>7} "
          f"{'max':>7}")
    for pop in ("OPEN TODAY", "WOULD ADMIT", "refused anyway"):
      v = taper[hwy].get(pop, [])
      if not v:
        continue
      print(f"    {pop:<16} {len(v):7d} {100*len(v)/tot:6.1f}% | {q(v,.5):7.2f} {q(v,.9):7.2f} "
          f"{q(v,.99):7.2f} {max(v):7.2f}")
    # WHAT A TAPER GATE WOULD COST, on the only two populations it could act on. A threshold is
    # only worth naming if it removes more of WOULD ADMIT than of OPEN TODAY -- otherwise it is
    # taxing the passes that already work to buy nothing.
    open_v = taper[hwy].get("OPEN TODAY", [])
    adm_v = taper[hwy].get("WOULD ADMIT", [])
    if open_v and adm_v:
      print(f"    {'':16} {'':7} {'':7} | frames a taper gate would refuse:")
      for cand in (1.0, 1.5, 2.0, 2.5, 3.0):
        o = sum(1 for x in open_v if x > cand)
        a = sum(1 for x in adm_v if x > cand)
        print(f"    {'':16} {'':7} {'':7} |   > {cand:.1f} m : "
              f"OPEN TODAY {o:5d} ({100*o/len(open_v):4.1f}%)   "
              f"WOULD ADMIT {a:5d} ({100*a/len(adm_v):4.1f}%)")
    print()

  print("  READ IT ON WHETHER 'WOULD ADMIT' HAS A HEAVIER TAIL THAN 'OPEN TODAY'.")
  print("  Heavier  -> the std cutoff was genuinely refusing narrowing road, its work-zone defense")
  print("              was real, and a taper gate has to go in BEFORE the cutoff comes out.")
  print("  Alike    -> the cutoff was refusing WIDE roads and nothing else. Its work-zone defense")
  print("              was an accident that never fired, and removing it costs nothing that was")
  print("              actually protecting anything.")
  print()
  print("  A THIN TAIL IS NOT PROOF THERE IS NO SIGNAL. Four ordinary drives may contain no work")
  print("  zone at all, and absence in a log is evidence about the log's conditions first. The")
  print("  threshold rows exist so a located cone event can be scored against these same numbers.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
