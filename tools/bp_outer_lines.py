#!/usr/bin/env python3
"""How far can the model see an OUTER lane line, and is left symmetric with right?

`lane_bounds_from_lines` calls both-outer-lines-absent a contradiction. On I-215 that threw away
303 frames where the witness knew he was in the left lane: beyond his left is an unpainted median,
and the far-right line is TWO lanes away through traffic. One of those absences is real and the
other is range.

`NO_LEFT_LINE_PROB` was measured against the outer LEFT line only and is reused for the right one
with no equivalent measurement. This measures both, keyed on a lane position established by
something other than the lines, so the reasoning is not circular.

Ground truth here is the ROAD EDGE: when it is trusted and close, we are in the rightmost lane.
"""
import glob, os, re, sys
from collections import Counter
from statistics import median

sys.path.insert(0, "/data/openpilot")


def segments_in_order(route):
  segs = glob.glob(f"/data/media/0/realdata/{route}--*")
  def idx(p):
    m = re.search(r"--(\d+)$", p)
    return int(m.group(1)) if m else -1
  return sorted(segs, key=idx)


def main():
  from openpilot.tools.lib.logreader import LogReader
  from openpilot.sunnypilot.selfdrive.controls.lib.lane_anchor import NO_LEFT_LINE_PROB

  speed = 0.0; lanes = 0; one_way = False; hwy = "?"
  fl_when_rightmost = []      # far-LEFT prob while the edge proves we are in lane 0
  fr_when_rightmost = []      # far-RIGHT prob in the same frames -- should be absent, a control
  both_absent = Counter()
  n_frames = 0

  for route in sys.argv[1:]:
    for s in segments_in_order(route):
      p = os.path.join(s, "rlog.zst")
      if not os.path.exists(p):
        continue
      for m in LogReader(p):
        w = m.which()
        if w == "carState":
          speed = float(m.carState.vEgo)
        elif w == "mapdOut":
          hwy = str(m.mapdOut.highwayClass); lanes = int(m.mapdOut.lanes)
          one_way = bool(m.mapdOut.oneWay)
        elif w == "modelV2":
          if speed < 15.0 or hwy != "motorway" or lanes < 3 or not one_way:
            continue
          try:
            probs = m.modelV2.laneLineProbs
            fl, fr = float(probs[0]), float(probs[3])
            std = float(m.modelV2.roadEdgeStds[1]); d = abs(float(m.modelV2.roadEdges[1].y[0]))
          except (IndexError, AttributeError):
            continue
          n_frames += 1
          if fl < NO_LEFT_LINE_PROB and fr < NO_LEFT_LINE_PROB:
            both_absent[lanes] += 1
          # THE EDGE AS GROUND TRUTH: trusted, and within a lane and a shoulder of us. The edge is
          # biased about one lane by the shoulder, so "close" means rightmost, which is the one
          # thing it is reliable about.
          if std <= 0.5 and d <= 6.0:
            fl_when_rightmost.append(fl)
            fr_when_rightmost.append(fr)

  def q(v, name):
    if not v:
      print(f"  {name}: no frames"); return
    v = sorted(v)
    lo = v[len(v) // 10]; mid = median(v); hi = v[9 * len(v) // 10]
    absent = sum(1 for x in v if x < NO_LEFT_LINE_PROB)
    print(f"  {name}: n={len(v)}  p10 {lo:.2f}  p50 {mid:.2f}  p90 {hi:.2f}   "
          f"read as ABSENT on {100.0 * absent / len(v):.1f}%")

  print(f"motorway, one-way, 3+ lanes: {n_frames} frames")
  print()
  print("BOTH OUTER LINES ABSENT (what the code calls a contradiction)")
  for n, v in sorted(both_absent.items()):
    print(f"  lanes={n:<3} {v:>6}  {100.0 * v / max(n_frames, 1):>5.1f}% of those frames")
  print()
  print("IN THE RIGHTMOST LANE (proved by the road edge, not by the lines):")
  q(fl_when_rightmost, "far-LEFT  line, 1 lane away ")
  q(fr_when_rightmost, "far-RIGHT line, off the road")
  print()
  print("If far-LEFT reads ABSENT often from the rightmost lane, then both-absent is genuinely")
  print("ambiguous and refusing is right. If it is almost always PRESENT there, both-absent means")
  print("leftmost with the far-right line simply out of range, and refusing throws that away.")


main()
