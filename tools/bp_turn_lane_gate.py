#!/usr/bin/env python3
"""FusionPilot: on a TWO-WAY road, can `beyond` refuse alone once the edge-std cutoff is gone?

THE ONE QUESTION BLOCKING THE LARGEST LEVER IN THIS FEATURE. Removing `MAX_ROAD_EDGE_STD` takes
motorway reachability from 22% to 65%, and every measurement supporting it was taken on motorway.
It was attempted on 2026-08-23 and reverted, because `TestTrafficMayNotStandInForTheRoadEdge` says:

    "A center turn lane is painted like a travel lane, sized like one, and has cars moving down it
     in our direction. EVERY TERM THAT SURVIVED THE WAIVER PASSES ON IT. The waived terms were the
     only ones that did not -- so the waiver was, on that road, the whole gate."

The waived terms are the two EDGE-derived ones: the std cutoff and `beyond`. Drop the std and
`beyond` holds that case alone. Nothing has ever measured whether it can.

    python tools/bp_turn_lane_gate.py <route-prefix> [<route-prefix> ...]

WHAT IT MEASURES, restricted to the frames where the question lives: TWO-WAY roads, moving, with
PAINT AND WIDTH BOTH PASSING -- because those are exactly the terms the docstring says a center turn
lane satisfies. Among those:

    std refuses / beyond refuses / BOTH / NEITHER

**`NEITHER` IS THE ANSWER.** Those are frames the gate opens today only because... it does not: with
the std in place they are refused by it. Remove the std and every frame in the `std refuses but
beyond does not` column becomes newly open on a two-way road. If that column is ~0, `beyond` was
carrying the case all along and the removal is safe here. If it is large, the std cutoff IS the
turn-lane defense and removing it globally re-creates a failure that already cost three incidents.

THE MAP LABELS, IT DOES NOT GATE. `oneWay` is used to SELECT which frames to look at, which is the
legitimate use recorded throughout this fork -- the same way the lane anchor labels the separation
test. Nothing here proposes letting the map decide anything.

TWO-WAY IS A SUPERSET OF TURN-LANE, DELIBERATELY. OSM does not reliably tag a center turn lane, so
there is no way to select exactly the trap. Every two-way road is scored instead, which is
CONSERVATIVE: if `beyond` refuses across all two-way frames it certainly refuses on the turn-lane
subset. A clean result here is therefore trustworthy, and a dirty one is not automatically damning.

AND THE TRAP'S OWN SIGNATURE IS REPORTED SEPARATELY. The 2026-08-09 incident needed same-direction
traffic moving down the candidate lane -- that is what made a turn lane look like a travel lane. So
`sameDirectionRecent` splits every row. That subset is the closest thing to the actual failure this
data can produce.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

REALDATA = "/data/media/0/realdata"


def pct(n, d):
  return f"{100.0 * n / d:5.1f}%" if d else "    --"


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("route", nargs="+")
  ap.add_argument("--segments", type=int, default=0)
  args = ap.parse_args()

  from openpilot.tools.lib.logreader import LogReader
  from openpilot.sunnypilot.selfdrive.controls.lib.passing_assist import (
    MAX_ROAD_EDGE_STD, MIN_ADJACENT_LINE_PROB, MIN_LANE_WIDTH_M, MAX_LANE_WIDTH_M,
    MIN_EDGE_BEYOND_LINE_M,
  )

  # FAIL LOUDLY on a field that does not exist -- a broad try around a schema read turns a typo into
  # a confident zero, and a confident zero here would read as "beyond carries it, ship the change".
  from cereal import custom
  pa = set(custom.LongitudinalPlanSP.PassingAssist.schema.fieldnames)
  for f in ("leftLineProb", "leftLaneWidth", "leftEdgeStd", "leftEdgeBeyond", "adjacentLeft"):
    if f not in pa:
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

  # keyed by (road kind, same-direction traffic seen left)
  tally = defaultdict(Counter)
  hwy_seen = Counter()
  cur = {"oneway": None, "hwy": "?", "speed": 0.0}

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
          cur["oneway"] = bool(m.mapdOut.oneWay)
          cur["hwy"] = str(m.mapdOut.highwayClass) or "?"
        except Exception:  # noqa: BLE001
          pass
        continue
      if w == "carState":
        cur["speed"] = float(m.carState.vEgo)
        continue
      if w != "longitudinalPlanSP":
        continue
      try:
        p_a = m.longitudinalPlanSP.passingAssist
      except Exception:  # noqa: BLE001
        continue
      if cur["speed"] < 10.0 or cur["oneway"] is None:
        continue

      # THE FRAMES THE QUESTION LIVES IN: paint and width both pass, which is what the docstring
      # says a center turn lane does. Anything else is refused for a reason this change does not
      # touch, so including it would dilute the very number being read.
      paint_ok = float(p_a.leftLineProb) >= MIN_ADJACENT_LINE_PROB
      width_ok = MIN_LANE_WIDTH_M <= float(p_a.leftLaneWidth) <= MAX_LANE_WIDTH_M
      if not (paint_ok and width_ok):
        continue

      # Two-way is the case under test. A motorway frame is scored too, as the CONTROL -- the
      # removal is already known to be safe there, so it is what a clean column looks like.
      kind = "TWO-WAY" if not cur["oneway"] else "one-way"
      try:
        same_dir = bool(p_a.adjacentLeft.available) and bool(p_a.adjacentLeft.sameDirectionRecent)
      except Exception:  # noqa: BLE001
        same_dir = False
      key = (kind, same_dir)
      hwy_seen[(kind, cur["hwy"])] += 1

      std_refuses = float(p_a.leftEdgeStd) > MAX_ROAD_EDGE_STD
      beyond_refuses = float(p_a.leftEdgeBeyond) < MIN_EDGE_BEYOND_LINE_M
      c = tally[key]
      c["frames"] += 1
      if std_refuses and beyond_refuses:
        c["both"] += 1
      elif std_refuses:
        c["STD ONLY"] += 1          # <- newly opened by the removal. THE number.
      elif beyond_refuses:
        c["beyond only"] += 1
      else:
        c["neither"] += 1

  if not tally:
    sys.exit("no moving frames with map data and paint+width passing on these routes")

  print(f"routes {args.route}")
  print("  frames where PAINT and WIDTH both pass -- the terms a center turn lane satisfies\n")
  print(f"  {'road':<9} {'same-dir left':<14} {'frames':>8} {'STD ONLY':>10} {'beyond only':>12} "
        f"{'both':>8} {'neither':>9}")
  for kind in ("TWO-WAY", "one-way"):
    for same in (True, False):
      c = tally.get((kind, same))
      if not c:
        continue
      n = c["frames"]
      print(f"  {kind:<9} {str(same):<14} {n:8d} {pct(c['STD ONLY'], n):>10} "
            f"{pct(c['beyond only'], n):>12} {pct(c['both'], n):>8} {pct(c['neither'], n):>9}")
  print()
  print("  road classes seen, per kind:")
  for (kind, hwy), n in sorted(hwy_seen.items(), key=lambda kv: -kv[1])[:10]:
    print(f"    {kind:<9} {hwy:<14} {n}")
  print()
  print("  'STD ONLY' IS THE ANSWER. Those frames are refused TODAY by the edge-std cutoff and by")
  print("  nothing else, so removing it opens every one of them.")
  print()
  print("  Near zero on the TWO-WAY rows -> `beyond` was carrying the turn-lane case all along and")
  print("     the removal does not re-create the 2026-08-09 failure.")
  print("  Large on TWO-WAY, especially with same-dir left traffic TRUE -> the std cutoff IS the")
  print("     turn-lane defense. Removing it globally is refused, and no motorway measurement can")
  print("     overrule this one, because a freeway has no turn lane in it.")
  print()
  print("  TWO-WAY IS A SUPERSET of turn-lane roads -- OSM does not tag the trap -- so a CLEAN")
  print("  result here is trustworthy and a dirty one is a reason to look closer, not a verdict.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
