#!/usr/bin/env python3
"""Which mapdOut fields actually carry anything ON HIS ROADS?

Ten of twenty-seven fields are consumed. The rest were never examined, and `advisorySpeed` is the
cautionary tale: it IS wired up and it is zero on every frame of every drive. A field that looks
useful in the schema and is empty in Utah is not a feature, it is a maintenance cost.

Reports, per field: how often it is non-empty, and what distinct values appear.
"""
import glob, os, re, sys
from collections import Counter

def segments_in_order(route):
  segs = glob.glob(f"/data/media/0/realdata/{route}--*")
  def idx(p):
    m = re.search(r"--(\d+)$", p)
    return int(m.group(1)) if m else -1
  return sorted(segs, key=idx)

TEXT = ["hazard", "nextHazard", "conditionalSpeedLimit", "wayName", "wayRef"]
NUM = ["nextHazardDistance", "advisorySpeed", "nextAdvisorySpeed", "nextAdvisorySpeedDistance",
       "estimatedRoadWidth", "speedLimitSuggestedSpeed", "distanceFromWayCenter"]
ENUM = ["roadContext", "waySelectionType", "highwayClass"]
BOOL = ["speedLimitAccepted", "oneWay", "tileLoaded"]

def main():
  routes = sys.argv[1:]
  sys.path.insert(0, "/data/openpilot")
  from openpilot.tools.lib.logreader import LogReader
  from cereal import custom

  have = set(custom.MapdOut.schema.fieldnames)
  for f in TEXT + NUM + ENUM + BOOL:
    if f not in have:
      sys.exit(f"mapdOut has no field {f!r} -- this tool would report a confident zero")

  total = 0
  vals = {f: Counter() for f in TEXT + ENUM + BOOL}
  nonzero = Counter()
  ranges = {f: [] for f in NUM}

  for route in routes:
    for s in segments_in_order(route):
      p = os.path.join(s, "rlog.zst")
      if not os.path.exists(p):
        continue
      for m in LogReader(p):
        if m.which() != "mapdOut":
          continue
        o = m.mapdOut
        total += 1
        for f in TEXT:
          v = str(getattr(o, f) or "").strip()
          vals[f][v if v else "(empty)"] += 1
          if v:
            nonzero[f] += 1
        for f in ENUM + BOOL:
          vals[f][str(getattr(o, f))] += 1
        for f in NUM:
          v = float(getattr(o, f))
          if v:
            nonzero[f] += 1
            ranges[f].append(v)

  if not total:
    sys.exit("no mapdOut frames -- is MapdV2 enabled and was the car moving?")
  print(f"{total} mapdOut frames across {len(routes)} routes")
  print()
  print("TEXT / NUMERIC fields -- how often is there anything at all?")
  for f in TEXT + NUM:
    n = nonzero[f]
    extra = ""
    if f in ranges and ranges[f]:
      r = sorted(ranges[f])
      extra = f"   min {r[0]:.2f}  p50 {r[len(r)//2]:.2f}  max {r[-1]:.2f}"
    elif f in vals:
      top = [k for k, _ in vals[f].most_common(4) if k != "(empty)"][:3]
      extra = "   values: " + (", ".join(top) if top else "none")
    print(f"  {f:<28} {n:>7} / {total}  {100.0*n/total:>5.1f}%{extra}")
  print()
  print("ENUM / BOOL fields -- what do they actually say?")
  for f in ENUM + BOOL:
    top = ", ".join(f"{k} {100.0*v/total:.0f}%" for k, v in vals[f].most_common(4))
    print(f"  {f:<28} {top}")

main()
