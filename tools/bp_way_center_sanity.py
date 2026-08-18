#!/usr/bin/env python3
"""Is `distanceFromWayCenter` physically possible, given `estimatedRoadWidth` on the same frame?

WHY THIS AND NOT A CAMERA COMPARISON. `distanceFromWayCenter` came back with a p90 of 11.58 m and a
max of 30.74 m, which is wider than any real road, and the note left on it said it must be checked
against the camera before carrying a lane-position gate. That check turned out to be the wrong first
move: the left ROAD EDGE is trusted 0.0% of the time on multi-lane motorway (see
bp_left_edge_profile.py), so the camera cannot referee this at freeway speed.

But mapd publishes `estimatedRoadWidth` on the SAME frame, and the two fields constrain each other
with no third party needed. A car on the road must sit within half a road width of the centerline:

    |distanceFromWayCenter|  <=  estimatedRoadWidth / 2

Frames violating that are self-inconsistent, whatever the camera would have said. This is the
cheapest possible test and it needed no new data.

WHAT THE ANSWER MEANS EITHER WAY:

  mostly consistent   the tail is real geometry -- wide roads, or the way match sitting on a
                      parallel carriageway -- and a lane gate might survive with a confidence
                      filter on waySelectionType.
  mostly impossible   the field does not mean "metres from the centerline of the road we are on",
                      and no filtering rescues it. Stop trying to build lane position from it.

The `placement` hypothesis is the specific thing to look for in a middling result:
`placement:forward=left_of:1` on 74 of 90 US 6 ways says the way GEOMETRY is drawn along a lane
boundary rather than the road centerline. That would bias the ratio toward a consistent ~1.0 rather
than scattering it, because the offset would be about half a road width nearly everywhere.
"""
import glob
import os
import sys
from collections import defaultdict


def q(sorted_vals, p):
  if not sorted_vals:
    return float("nan")
  return sorted_vals[min(len(sorted_vals) - 1, int(p * len(sorted_vals)))]


def main():
  route = sys.argv[1] if len(sys.argv) > 1 else "00000383"
  sys.path.insert(0, "/data/openpilot")
  from openpilot.tools.lib.logreader import LogReader

  segs = sorted(glob.glob(f"/data/media/0/realdata/{route}--*"))
  if not segs:
    sys.exit(f"no segments for {route}")

  rows = []
  for s in segs:
    f = os.path.join(s, "rlog.zst")
    if not os.path.exists(f):
      continue
    for m in LogReader(f):
      if m.which() != "mapdOut":
        continue
      o = m.mapdOut
      try:
        d = abs(float(o.distanceFromWayCenter))
        w = float(o.estimatedRoadWidth)
      except (AttributeError, TypeError, ValueError):
        continue
      if w <= 0:
        continue
      rows.append((d, w, str(o.waySelectionType), str(o.highwayClass)))

  print(f"route {route}: {len(rows)} frames carrying both fields")
  if not rows:
    return

  over = [r for r in rows if r[0] > r[1] / 2.0]
  print(f"IMPOSSIBLE (|dfwc| > roadWidth/2): {len(over)}  ({100.0 * len(over) / len(rows):.1f}%)")

  ratios = sorted(r[0] / (r[1] / 2.0) for r in rows)
  print(f"ratio |dfwc| / (width/2):  p50 {q(ratios, .50):.2f}   p90 {q(ratios, .90):.2f}   "
        f"p99 {q(ratios, .99):.2f}   max {ratios[-1]:.2f}")
  print("  1.0 means exactly at the road edge. Under 1.0 is on the road. Over 1.0 is off it.")

  # A wrong way match is the innocent explanation, so let the confidence label speak.
  print()
  print("by waySelectionType:")
  by = defaultdict(list)
  for r in rows:
    by[r[2]].append(r)
  for t in sorted(by, key=lambda k: -len(by[k])):
    sub = by[t]
    bad = sum(1 for r in sub if r[0] > r[1] / 2.0)
    sr = sorted(r[0] / (r[1] / 2.0) for r in sub)
    print(f"  {t:<12} n={len(sub):<6} impossible {100.0 * bad / len(sub):>5.1f}%   "
          f"ratio p50 {q(sr, .50):.2f}")

  print()
  print("by highwayClass:")
  by = defaultdict(list)
  for r in rows:
    by[r[3]].append(r)
  for t in sorted(by, key=lambda k: -len(by[k])):
    sub = by[t]
    if len(sub) < 50:
      continue
    bad = sum(1 for r in sub if r[0] > r[1] / 2.0)
    sr = sorted(r[0] / (r[1] / 2.0) for r in sub)
    print(f"  {t:<14} n={len(sub):<6} impossible {100.0 * bad / len(sub):>5.1f}%   "
          f"ratio p50 {q(sr, .50):.2f}")


if __name__ == "__main__":
  main()
