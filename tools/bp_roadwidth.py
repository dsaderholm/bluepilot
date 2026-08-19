#!/usr/bin/env python3
"""Is estimatedRoadWidth a MEASUREMENT, or just lanes x 3.7?

It reads 100% populated, which makes it look like the best-covered field mapd publishes -- and it
was my candidate for measuring the shoulder. Every quantile is an exact multiple of 3.7, which is
what a derived number looks like.
"""
import glob, os, re, sys
from collections import Counter

def segments_in_order(route):
  segs = glob.glob(f"/data/media/0/realdata/{route}--*")
  def idx(p):
    m = re.search(r"--(\d+)$", p)
    return int(m.group(1)) if m else -1
  return sorted(segs, key=idx)

def main():
  sys.path.insert(0, "/data/openpilot")
  from openpilot.tools.lib.logreader import LogReader
  pairs = Counter()
  exact = 0
  total = 0
  for route in sys.argv[1:]:
    for s in segments_in_order(route):
      p = os.path.join(s, "rlog.zst")
      if not os.path.exists(p):
        continue
      for m in LogReader(p):
        if m.which() != "mapdOut":
          continue
        o = m.mapdOut
        w = float(o.estimatedRoadWidth)
        n = int(o.lanes)
        if not w:
          continue
        total += 1
        pairs[(n, round(w, 2))] += 1
        if n and abs(w - n * 3.7) < 0.005:
          exact += 1
  print(f"{total} frames with a road width")
  print(f"width == lanes * 3.7 EXACTLY: {exact} ({100.0*exact/max(total,1):.1f}%)")
  print()
  print("lanes -> width, most common pairings")
  for (n, w), c in pairs.most_common(12):
    print(f"  lanes={n:<3} width={w:<7} {c:>6}   {'= lanes*3.7' if n and abs(w-n*3.7)<0.005 else ''}")

main()
