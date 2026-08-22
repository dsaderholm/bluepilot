#!/usr/bin/env python3
"""FusionPilot: when the LEFT road edge's std explodes, is the POSITION still usable?

THE QUESTION, and it decides whether `MAX_ROAD_EDGE_STD` is the wrong lever or merely the wrong
value. `bp_left_gate_reach.py` measured the left pass gate opening on 0-30% of motorway, with the
edge-std term the sole refuser on 52-83% of frames, and found `leftEdgeStd` p50 at **6.60** on one
drive against a 1.2 threshold. Two readings of that are possible and they point opposite ways:

  THE STD IS PESSIMISTIC   the edge POSITION is still where the lane lines say it should be, and
                           the model is merely reporting low confidence. Then the gate is throwing
                           away a usable measurement and the threshold IS the lever.
  THE STD IS HONEST        the position is wandering, implausible, or inconsistent with the paint.
                           Then loosening admits garbage, and the threshold is the WRONG lever --
                           which is what "evidence that opens must never be cheaper than evidence
                           that refuses" forbids.

HOW IT IS DECIDED, and it is the technique that found the shoulder bias: **two witnesses, on the
same frame.** The road edge and the outermost left lane line both measure the left side of the
road. Their GAP is a shoulder -- a real, roughly constant, positive quantity. So:

  gap = |left road edge y| - |outer left lane line y|      on frames where both exist

If high-std frames keep a sane, stable gap, the position survived and the std is pessimistic. If
the gap goes wild or negative, the edge is genuinely lost and the std is telling the truth.

  python tools/bp_left_edge_truth.py <route-prefix>

FRAME-TO-FRAME JITTER is the second witness and needs no lane line at all: a position that is
usable cannot move metres between consecutive 20 Hz frames. Reported per std band, because a mean
gap can look healthy while the value oscillates around it.

SIGNS: modelV2 is left-NEGATIVE. Both quantities are taken as absolute distances from ego so the
gap is positive when the edge is outboard of the paint, which is the only physical arrangement.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

REALDATA = "/data/media/0/realdata"

LL_FAR_LEFT, LL_LEFT = 0, 1
RE_LEFT = 0

# Bands of leftEdgeStd. 1.2 is the live threshold, so the first band is what the gate accepts today
# and everything after it is what it refuses.
BANDS = [(0.0, 1.2), (1.2, 2.0), (2.0, 4.0), (4.0, 8.0), (8.0, 1e9)]


def near_y(series, idx):
  try:
    return float(series[idx].y[0])
  except (IndexError, AttributeError, TypeError):
    return None


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

  segs = sorted((d for d in os.listdir(REALDATA) if d.startswith(args.route)),
                key=lambda d: int(d.rsplit("--", 1)[-1]) if d.rsplit("--", 1)[-1].isdigit() else -1)
  if args.segments:
    segs = segs[:args.segments]
  if not segs:
    sys.exit(f"no segments matching {args.route}")

  gaps = defaultdict(list)      # band -> shoulder gap, edge outboard of the outer paint
  jumps = defaultdict(list)     # band -> |edge y this frame - edge y last frame|
  edge_abs = defaultdict(list)  # band -> |edge y|, to catch an edge parked at an absurd distance
  counts = defaultdict(int)
  no_line = defaultdict(int)
  prev_edge = None
  speed = 0.0

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
      if w == "carState":
        speed = float(m.carState.vEgo)
        continue
      if w != "modelV2":
        continue
      if speed < 10.0:
        prev_edge = None
        continue
      mv = m.modelV2
      try:
        std = float(mv.roadEdgeStds[RE_LEFT])
      except Exception:  # noqa: BLE001
        continue
      edge = near_y(mv.roadEdges, RE_LEFT)
      if edge is None:
        prev_edge = None
        continue
      band = next(i for i, (lo, hi) in enumerate(BANDS) if lo <= std < hi)
      counts[band] += 1
      edge_abs[band].append(abs(edge))
      if prev_edge is not None:
        jumps[band].append(abs(edge - prev_edge))
      prev_edge = edge

      # The OUTERMOST left line that the model actually believes in. Falling back to the inner one
      # matters: on a two-lane road far-left does not exist, and treating its placeholder as a
      # measurement is how a several-metre gap gets invented.
      probs = list(mv.laneLineProbs)
      line = None
      for idx in (LL_FAR_LEFT, LL_LEFT):
        if idx < len(probs) and probs[idx] >= 0.5:
          y = near_y(mv.laneLines, idx)
          if y is not None:
            line = y
            break
      if line is None:
        no_line[band] += 1
        continue
      gaps[band].append(abs(edge) - abs(line))

  if not counts:
    sys.exit("no moving frames with a left road edge on this route")

  total = sum(counts.values())
  print(f"route {args.route}   {total} moving modelV2 frames with a left road edge\n")
  print(f"  {'leftEdgeStd':<12} {'frames':>8} {'share':>7} | {'shoulder gap (m)':^24} | "
        f"{'frame jump (m)':^16} | {'|edge| p50':>10}")
  print(f"  {'':<12} {'':>8} {'':>7} | {'p10':>7} {'p50':>7} {'p90':>7} | {'p50':>7} {'p90':>7} |")
  for i, (lo, hi) in enumerate(BANDS):
    n = counts[i]
    if not n:
      continue
    label = f"{lo:.1f}-{hi:.1f}" if hi < 1e8 else f"{lo:.1f}+"
    g, j, e = gaps[i], jumps[i], edge_abs[i]
    print(f"  {label:<12} {n:8d} {100*n/total:6.1f}% | {q(g,.1):7.2f} {q(g,.5):7.2f} {q(g,.9):7.2f} | "
          f"{q(j,.5):7.2f} {q(j,.9):7.2f} | {q(e,.5):10.2f}")
    if no_line[i]:
      print(f"  {'':<12} {'':>8} {'':>7}   ({no_line[i]} of these had no believed left line to compare against)")
  print()
  print("  READ THE FIRST ROW AS THE CONTROL -- it is what the gate accepts today.")
  print("  IF the gap and the jump stay comparable as the std rises, the POSITION survived and the")
  print("  std is pessimistic: the threshold is then the right lever and loosening is defensible.")
  print("  IF the gap goes wild or NEGATIVE, or the jump grows to metres, the edge is genuinely")
  print("  lost and the std is honest -- loosening would admit garbage into a gate that OPENS a")
  print("  lane change, which is exactly what this fork's rules forbid.")
  print("  A negative gap means the road edge came INBOARD of the paint, which no real road does.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
