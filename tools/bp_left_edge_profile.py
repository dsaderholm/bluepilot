#!/usr/bin/env python3
"""What does the model report to our LEFT on a freeway, and does an HOV boundary change it?

THE QUESTION, AND WHY IT DECIDES A SAFETY GATE. Passing assist will move into the lane on our left.
Where that lane is a California express lane, it must not. Three boundary types exist and the map
cannot distinguish them -- `separation=flex_post` is ONE object worldwide and the whole `separation`
key is 652, so no request to mapd can ever answer this:

    concrete or pylon wall   a real road edge. The existing left-edge logic already refuses.
    double white line        paint. No edge. Utah's I-15 boundary, per the owner.
    candlestick delineators  flexible posts, ~3 ft, tens of feet apart. California. UNKNOWN.

The failure that matters is not "no edge" or "edge", both of which are decidable. It is FLICKER: an
edge that appears at each post and vanishes in the gaps lets a pass open between candlesticks.

UTAH IS THE CONTROL. The owner reports Utah uses a double white line and no posts, so every existing
route is the paint case. Run this now to fix the baseline, run it again after the California drive,
and the contrast is the answer. Nothing new has to be logged for that to work -- modelV2 and mapdOut
are both already in every route.

WHAT TO EXPECT IF POSTS PRODUCE AN EDGE. The left edge distance collapses toward one lane width
(~3.7 m) instead of sitting at the far side of the roadway, and the trusted fraction rises. If they
produce flicker, the trusted fraction stays middling while the STD churns -- which is why the
std distribution is reported rather than just a mean.

  python tools/bp_left_edge_profile.py 00000383
  python tools/bp_left_edge_profile.py 00000383 --min-speed 20
"""
import argparse
import glob
import os
import re
import statistics
import sys


def segments_in_order(route):
  """Segment dirs for a route, in DRIVE order.

  sorted(glob(...)) is a STRING sort, so --10 lands before --2 and the drive is walked out of
  order. Harmless for whole-drive percentages, fatal for "what happened at the start", which is
  exactly the question a road report asks. Sort on the trailing integer instead.
  """
  segs = glob.glob(f"/data/media/0/realdata/{route}--*")
  def idx(p):
    m = re.search(r"--(\d+)$", p)
    return int(m.group(1)) if m else -1
  return sorted(segs, key=idx)


MIN_SPEED_DEFAULT = 15.0     # m/s. Freeway only; a parked or crawling car says nothing here.
LANE_M = 3.7


def pct(values, q):
  if not values:
    return float("nan")
  s = sorted(values)
  return s[min(len(s) - 1, int(q * len(s)))]


def summarize(name, dists, stds, trusted, total):
  print(f"\n=== {name} ===")
  print(f"  frames {total}")
  if not total:
    return
  print(f"  left edge trusted (std <= {MAX_STD}):  {trusted}  ({100.0 * trusted / total:.1f}%)")
  if dists:
    print(f"  distance when trusted:  p10 {pct(dists, .10):.1f}  p50 {pct(dists, .50):.1f}  "
          f"p90 {pct(dists, .90):.1f} m")
    within = sum(1 for d in dists if d <= 1.5 * LANE_M)
    print(f"    within 1.5 lane widths ({1.5 * LANE_M:.1f} m): {within} "
          f"({100.0 * within / len(dists):.0f}%)   <- a boundary beside US, not across the road")
  if stds:
    print(f"  edge std overall:       p10 {pct(stds, .10):.2f}  p50 {pct(stds, .50):.2f}  "
          f"p90 {pct(stds, .90):.2f}")
    print(f"  std variability (stdev of std): {statistics.pstdev(stds):.3f}  "
          f"<- HIGH means the edge is flickering rather than absent or present")


MAX_STD = 0.5


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("route", nargs="?", default="00000383")
  ap.add_argument("--min-speed", type=float, default=MIN_SPEED_DEFAULT)
  # HIS IDEA, 2026-08-19: if the RIGHT edge is trusted, it anchors the rightmost lane and
  # OSM `lanes` counts leftward from there. The left edge was measured dead; the right
  # one is a shoulder rather than a median and was never measured at all.
  ap.add_argument("--side", choices=["left", "right"], default="left")
  args = ap.parse_args()

  sys.path.insert(0, "/data/openpilot")
  from openpilot.tools.lib.logreader import LogReader
  from openpilot.sunnypilot.selfdrive.controls.lib import adjacent_lane as AL

  segs = segments_in_order(args.route)
  if not segs:
    sys.exit(f"no segments for {args.route}")

  speed = 0.0
  hwy = ""
  lanes = 0
  buckets = {}

  for s in segs:
    f = os.path.join(s, "rlog.zst")
    if not os.path.exists(f):
      continue
    for m in LogReader(f):
      w = m.which()
      if w == "carState":
        speed = float(m.carState.vEgo)
      elif w == "mapdOut":
        hwy = str(m.mapdOut.highwayClass)
        try:
          lanes = int(m.mapdOut.lanes)
        except (AttributeError, TypeError, ValueError):
          lanes = 0
      elif w == "modelV2":
        if speed < args.min_speed:
          continue
        try:
          idx = AL.RE_LEFT if args.side == "left" else AL.RE_RIGHT
          std = float(m.modelV2.roadEdgeStds[idx])
        except (AttributeError, IndexError, TypeError, ValueError):
          continue
        # Bucket by road class and lane count: a 2-lane road's left edge is the shoulder, a
        # 5-lane freeway's is the median, and averaging them together hides the whole effect.
        key = f"{hwy or 'unknown'}, {lanes if lanes else '?'} lanes"
        b = buckets.setdefault(key, {"d": [], "s": [], "t": 0, "n": 0})
        b["n"] += 1
        b["s"].append(std)
        if std <= MAX_STD:
          b["t"] += 1
          try:
            d = AL.road_edge_offset(m.modelV2, args.side, 0.0)
            if d is not None:
              b["d"].append(abs(float(d)))
          except (AttributeError, TypeError, ValueError):
            pass

  if not buckets:
    sys.exit("no qualifying frames -- check the route and --min-speed")

  print(f"SIDE: {args.side.upper()}")
  print(f"route {args.route}, above {args.min_speed:.0f} m/s "
        f"({args.min_speed * 2.237:.0f} mph)")
  print("BASELINE RUN: Utah uses a double white line and no candlestick posts, so these numbers")
  print("are the PAINT case. Compare a California run against them.")
  for key in sorted(buckets, key=lambda k: -buckets[k]["n"]):
    b = buckets[key]
    if b["n"] < 50:
      continue
    summarize(key, b["d"], b["s"], b["t"], b["n"])


if __name__ == "__main__":
  main()
