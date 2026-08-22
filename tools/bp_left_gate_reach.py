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


def anchor_verdict(pa) -> str:
  """What the LANE ANCHOR says about a lane to our left. GROUND TRUTH ONLY, never a gate input.

  It rests on mapdOut `lanes`, and map data MAY REFUSE, MUST NEVER OPEN -- so this can label a
  measurement and can never carry one. Using it to score whether a CAMERA term separates the two
  classes is exactly the legitimate use.
  """
  if bool(pa.noLaneLeft):
    return "leftmost"
  idx, total = int(pa.laneIndex), int(pa.lanesTotal)
  lo, hi = int(pa.laneBoundLo), int(pa.laneBoundHi)
  if idx >= 0 and total > 0 and idx < total - 1:
    return "lane exists"
  if lo >= 0 and hi >= 0 and total > 0 and hi < total - 1:
    return "lane exists"
  return "unknown"


def q(v, f):
  if not v:
    return float("nan")
  v = sorted(v)
  return v[min(len(v) - 1, int(f * len(v)))]


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

  # FAIL LOUDLY ON A FIELD THAT DOES NOT EXIST. Every read below sits inside a broad try that
  # treats a missing field as no data -- right for a field genuinely absent on some frames, and
  # catastrophic for a misspelled one, which it turns into a confident zero. This tool now decides
  # whether a term SEPARATES two classes, and a typo would report "no separation" indistinguishably
  # from a term that truly carries nothing.
  from cereal import custom
  pa_fields = set(custom.LongitudinalPlanSP.PassingAssist.schema.fieldnames)
  for f in ("leftGeometryOk", "leftEdgeStd", "leftLineProb", "leftLaneWidth", "leftEdgeBeyond",
            "noLaneLeft", "laneIndex", "lanesTotal", "laneBoundLo", "laneBoundHi"):
    if f not in pa_fields:
      sys.exit(f"passingAssist has no field {f!r} -- this tool would silently report zeros")

  # Per road class: frames, gate open, and each term's own pass rate. Independent shares, because
  # the chain in _geometry reports only the FIRST failing term and that hides the rest.
  by_class = defaultdict(Counter)
  stds = defaultdict(list)
  # edge-std values on frames where the OTHER THREE terms already pass.
  others_std = defaultdict(list)
  # THE SEPARATION TEST. For a camera term to REPLACE the edge-std term it has to answer "is there
  # a travel lane to my left" in the POSITIVE direction, and the way to find out is whether its
  # value differs between frames where a lane exists and frames where we are leftmost. Labelled by
  # the anchor (map-derived, ground truth only); measured on camera-only quantities.
  sep = defaultdict(lambda: defaultdict(list))
  cur_hwy = "?"
  cur_speed = 0.0
  cur_farleft = 0.0

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
      if w == "modelV2":
        # THE CAMERA-ONLY WITNESS. The anchor's "a lane exists to my left" rests on the MAP's lane
        # count, and map data may refuse but must never OPEN -- so it cannot carry a gate that
        # opens a pass. The far-left LANE LINE is the same claim from the camera alone, and it can.
        try:
          probs = list(m.modelV2.laneLineProbs)
          cur_farleft = float(probs[0]) if probs else 0.0
        except Exception:  # noqa: BLE001
          cur_farleft = 0.0
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
      # EVERY moving frame, not just the sole-refuser subset -- the question is whether the term
      # discriminates at all, and restricting to frames the edge already refused would answer a
      # narrower question with a biased sample.
      v = anchor_verdict(pa)
      sep[cur_hwy][f"{v} width"].append(float(pa.leftLaneWidth))
      sep[cur_hwy][f"{v} beyond"].append(float(pa.leftEdgeBeyond))
      sep[cur_hwy][f"{v} prob"].append(float(pa.leftLineProb))
      sep[cur_hwy][f"{v} std"].append(std)
      if float(pa.leftLineProb) >= MIN_ADJACENT_LINE_PROB and width_ok and beyond_ok:
        # Everything EXCEPT the edge-std term passes. This frame is one the sweep can win.
        c["othersOk"] += 1
        others_std[cur_hwy].append(std)
        # CAN THE LANE ANCHOR CARRY THIS FRAME? The edge-std term measures the wrong thing here
        # (it tracks distance, see bp_left_edge_truth.py), and the instrument built for "is there a
        # lane to my left" is the anchor. So on exactly the frames the edge term is the sole
        # refuser, ask what the anchor says -- that decides whether it could replace the term or
        # whether it is just as blind.
        if std > MAX_ROAD_EDGE_STD:
          c["edgeSoleRefuser"] += 1
          if cur_farleft >= MIN_ADJACENT_LINE_PROB:
            c["farLeftLine"] += 1
          idx = int(pa.laneIndex)
          total = int(pa.lanesTotal)
          lo, hi = int(pa.laneBoundLo), int(pa.laneBoundHi)
          if bool(pa.noLaneLeft):
            # The lines say we are LEFTMOST. Correct refusal, and the anchor knew.
            c["anchor: leftmost, refuse"] += 1
          elif idx >= 0 and total > 0 and idx < total - 1:
            # A pinned index with room above it: there IS a lane to the left.
            c["anchor: LANE EXISTS"] += 1
          elif lo >= 0 and hi >= 0 and total > 0 and hi < total - 1:
            # A RANGE, but even its top leaves a lane above. Still a definite yes.
            c["anchor: LANE EXISTS (range)"] += 1
          else:
            c["anchor: unknown"] += 1

  if not by_class:
    sys.exit("no moving frames with mapdOut on this route")

  print(f"route {args.route}   moving frames only (>10 m/s)   MAX_ROAD_EDGE_STD = {MAX_ROAD_EDGE_STD}")
  print(f"\n  {'road class':<14} {'frames':>8} {'GATE OPEN':>10} {'edge':>9} {'paint':>9} "
        f"{'width':>9} {'beyond':>9} {'edgeStd p50':>12} {'p10':>7}")
  for hwy in sorted(by_class, key=lambda h: -by_class[h]["frames"]):
    c = by_class[hwy]
    n = c["frames"]
    v = sorted(stds[hwy])
    p50 = v[len(v) // 2] if v else float("nan")
    p10 = v[len(v) // 10] if v else float("nan")
    # ALL FOUR TERMS. The first version collected widthOk and beyondOk and printed neither -- this
    # fork's oldest bug, inside the tool written to diagnose the gate. It mattered: left_lane_width
    # is the far-left line's POSITION relative to ego's own line, which is exactly the "positional
    # test" that was about to be proposed as new work. It already exists.
    print(f"  {hwy:<14} {n:8d} {100*c['gateOpen']/n:9.0f}% {100*c['edgeOk']/n:8.0f}% "
          f"{100*c['paintOk']/n:8.0f}% {100*c['widthOk']/n:8.0f}% {100*c['beyondOk']/n:8.0f}% "
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
  print()
  print("  ON THE FRAMES WHERE EDGE-STD IS THE SOLE REFUSER, WHAT DOES THE LANE ANCHOR SAY?")
  for hwy in sorted(by_class, key=lambda h: -by_class[h]["frames"]):
    c = by_class[hwy]
    n = c["edgeSoleRefuser"]
    if not n:
      continue
    parts = [f"{k.split(': ')[1]} {v} ({100*v/n:.0f}%)" for k, v in sorted(c.items())
             if k.startswith("anchor: ")]
    fl = c["farLeftLine"]
    print(f"    {hwy:<14} {n:6d} frames   " + "   ".join(parts))
    print(f"    {'':<14} {'':>6}   far-left LANE LINE believed (camera only): {fl} ({100*fl/n:.0f}%)")
  print("    A high 'LANE EXISTS' share means the anchor could carry this gate and the edge term")
  print("    is replaceable. A high 'unknown' means the anchor is just as blind and it cannot.")
  print()
  print("  THE CEILING IS THE POINT: past it, loosening the edge buys nothing because another term")
  print("  is already refusing. And this direction OPENS passes, so it needs more than a coverage")
  print("  argument -- evidence that opens must never be cheaper than evidence that refuses.")
  print()
  print("  GATE OPEN is the reachability number -- the share of moving frames on which a left pass")
  print("  COULD be offered. 'edge ok' is the term under suspicion, on its own denominator.")
  print("  A shut gate on a two-lane road is correct. A shut gate on multi-lane motorway is not.")
  print("  p10 says how far the good tail gets: if p10 is above the threshold, no realistic")
  print("  threshold change reaches it and the edge term is the wrong thing to tune.")
  print()

  # ---- the separation test --------------------------------------------------------------------
  print("  DOES ANY CAMERA TERM SEPARATE 'A LANE EXISTS' FROM 'LEFTMOST'? Labelled by the ANCHOR,")
  print("  which is map-derived and may label a measurement but may never open a gate.")
  for hwy in sorted(sep, key=lambda h: -by_class[h]["frames"]):
    if by_class[hwy]["frames"] < 500:
      continue
    d = sep[hwy]
    print(f"\n    {hwy}")
    print(f"      {'term':<10} {'':>18} {'p10':>8} {'p50':>8} {'p90':>8}   {'n':>7}")
    for term in ("width", "beyond", "prob", "std"):
      for verdict in ("lane exists", "leftmost", "unknown"):
        v = d.get(f"{verdict} {term}", [])
        if not v:
          continue
        print(f"      {term:<10} {verdict:>18} {q(v,.1):8.2f} {q(v,.5):8.2f} {q(v,.9):8.2f}   "
              f"{len(v):7d}")
  print()
  print("    READ IT ON THE p50 GAP BETWEEN THE FIRST TWO ROWS OF EACH TERM. A term whose value is")
  print("    the same in both classes carries NO information about whether a lane is there, however")
  print("    well it correlates with anything else -- that is what sank the far-left line's")
  print("    PROBABILITY (believed ~99% of the time even from the rightmost lane). A term that")
  print("    separates is the positive camera signal this gate has never had.")
  print("    `width` is the far-left line's POSITION relative to ego's own left line, so it is the")
  print("    positional test directly, and it is ALREADY IN THE GATE -- if it separates, the fix is")
  print("    to lean on it rather than to add anything.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
