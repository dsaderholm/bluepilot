#!/usr/bin/env python3
"""FusionPilot: can the LEFT pass gate ever open, and where?

THE QUESTION. `left_geometry_ok` hard-requires `left_edge_std <= MAX_ROAD_EDGE_STD` (1.2):

    left_edge_ok = left_std <= MAX_ROAD_EDGE_STD and left_edge_beyond >= MIN_EDGE_BEYOND_LINE_M
    left_geometry_ok = paint ok and width ok and left_edge_ok

and CLAUDE.md records the LEFT road edge as trusted **0.0% of the time on multi-lane motorway**. If
that holds, the gate is structurally shut on exactly the roads passing matters most on, and no
amount of tuning the other three terms reaches it.

WHY bp_passing_report CANNOT ANSWER THIS, and it looks like it can. Its "road edge refused, by mph:
70+ 100%" is computed over REFUSED frames -- `_edge_by_speed[band][0]` counts refusals in the band,
not frames. So 100% means "when it refused at 70 mph, the edge was always one of the reasons", which
is compatible with the gate opening most of the time. Reading it as a reachability figure is the
denominator error this fork has made five times; this tool exists so the reachability question has
its own denominator.

WHAT IT MEASURES, over ALL frames where the detector was looking:

  leftGeometryOk share                 can a left pass be offered at all
  leftEdgeStd distribution             how far from the 1.2 threshold it sits
  ...both split by highwayClass        because "0% on motorway" is the claim under test

  python tools/bp_left_gate_reach.py <route-prefix>

A LOW SHARE IS NOT AUTOMATICALLY A BUG. The gate SHOULD be shut where there is no lane to move
into, which is most of a two-lane road. What would be a finding is the gate being shut on
multi-lane motorway, where there demonstrably is one.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

REALDATA = "/data/media/0/realdata"


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("route")
  ap.add_argument("--segments", type=int, default=0)
  args = ap.parse_args()

  from openpilot.tools.lib.logreader import LogReader
  from openpilot.sunnypilot.selfdrive.controls.lib.passing_assist import (
    MAX_ROAD_EDGE_STD, MIN_ADJACENT_LINE_PROB, MIN_LANE_WIDTH_M, MAX_LANE_WIDTH_M,
    MIN_EDGE_BEYOND_LINE_M,
  )

  segs = sorted((d for d in os.listdir(REALDATA) if d.startswith(args.route)),
                key=lambda d: int(d.rsplit("--", 1)[-1]) if d.rsplit("--", 1)[-1].isdigit() else -1)
  if args.segments:
    segs = segs[:args.segments]
  if not segs:
    sys.exit(f"no segments matching {args.route}")

  # Per road class: frames, gate open, and each term's own pass rate. Independent shares, because
  # the chain in _geometry reports only the FIRST failing term and that hides the rest.
  by_class = defaultdict(Counter)
  stds = defaultdict(list)
  # edge-std values on frames where the OTHER THREE terms already pass.
  others_std = defaultdict(list)
  cur_hwy = "?"
  cur_speed = 0.0

  for seg in segs:
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
      if w != "longitudinalPlanSP":
        continue
      try:
        pa = m.longitudinalPlanSP.passingAssist
      except Exception:  # noqa: BLE001
        continue
      # MOVING ONLY. Parked frames are a large share of a route and the gate is meaningless there.
      if cur_speed < 10.0:
        continue
      c = by_class[cur_hwy]
      c["frames"] += 1
      c["gateOpen"] += bool(pa.leftGeometryOk)
      std = float(pa.leftEdgeStd)
      stds[cur_hwy].append(std)
      c["edgeOk"] += std <= MAX_ROAD_EDGE_STD
      c["paintOk"] += float(pa.leftLineProb) >= MIN_ADJACENT_LINE_PROB
      # THE OTHER THREE TERMS, so the sweep can say what becomes binding once the edge stops
      # being. The gate's own chain reports only the FIRST failure, which is why this is needed.
      width_ok = MIN_LANE_WIDTH_M <= float(pa.leftLaneWidth) <= MAX_LANE_WIDTH_M
      beyond_ok = float(pa.leftEdgeBeyond) >= MIN_EDGE_BEYOND_LINE_M
      c["widthOk"] += width_ok
      c["beyondOk"] += beyond_ok
      if float(pa.leftLineProb) >= MIN_ADJACENT_LINE_PROB and width_ok and beyond_ok:
        # Everything EXCEPT the edge-std term passes. This frame is one the sweep can win.
        c["othersOk"] += 1
        others_std[cur_hwy].append(std)

  if not by_class:
    sys.exit("no moving frames with mapdOut on this route")

  print(f"route {args.route}   moving frames only (>10 m/s)   MAX_ROAD_EDGE_STD = {MAX_ROAD_EDGE_STD}")
  print(f"\n  {'road class':<14} {'frames':>8} {'GATE OPEN':>12} {'edge ok':>12} {'paint ok':>12} "
        f"{'edgeStd p50':>12} {'p10':>7}")
  for hwy in sorted(by_class, key=lambda h: -by_class[h]["frames"]):
    c = by_class[hwy]
    n = c["frames"]
    v = sorted(stds[hwy])
    p50 = v[len(v) // 2] if v else float("nan")
    p10 = v[len(v) // 10] if v else float("nan")
    print(f"  {hwy:<14} {n:8d} {c['gateOpen']:6d} {100*c['gateOpen']/n:4.0f}% "
          f"{c['edgeOk']:6d} {100*c['edgeOk']/n:4.0f}% {c['paintOk']:6d} {100*c['paintOk']/n:4.0f}% "
          f"{p50:12.2f} {p10:7.2f}")
  print()
  print("  IF THE EDGE-STD THRESHOLD MOVED -- gate-open share, motorway only")
  mw = [h for h in by_class if h in ("motorway", "motorwayLink")]
  for hwy in mw:
    n = by_class[hwy]["frames"]
    v = others_std[hwy]
    print(f"  {hwy}: {by_class[hwy]['othersOk']} of {n} frames ({100*by_class[hwy]['othersOk']/n:.0f}%) "
          f"pass paint+width+beyond, so those are the ceiling")
    row = []
    for cand in (1.2, 1.5, 1.8, 2.1, 2.5, 3.0):
      opened = sum(1 for x in v if x <= cand)
      row.append(f"{cand:.1f}->{100*opened/n:.0f}%")
    print("    gate open at threshold:  " + "   ".join(row))
  print("  THE CEILING IS THE POINT: past it, loosening the edge buys nothing because another term")
  print("  is already refusing. And this direction OPENS passes, so it needs more than a coverage")
  print("  argument -- evidence that opens must never be cheaper than evidence that refuses.")
  print()
  print("  GATE OPEN is the reachability number -- the share of moving frames on which a left pass")
  print("  COULD be offered. 'edge ok' is the term under suspicion, on its own denominator.")
  print("  A shut gate on a two-lane road is correct. A shut gate on multi-lane motorway is not.")
  print("  p10 says how far the good tail gets: if p10 is above the threshold, no realistic")
  print("  threshold change reaches it and the edge term is the wrong thing to tune.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
