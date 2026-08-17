#!/usr/bin/env python3
"""FusionPilot: v1 against v2, on the same drive, before anything depends on the new one.

The cutover step. With the v2 binary installed it runs as an observer -- publishing `mapdOut` at
20 Hz while v1 still feeds Speed Limit Assist through `liveMapDataSP`. Both are now in the route, so
for the first time the two map programs can be compared on identical input instead of on separate
drives taken on different days.

WHAT TO LOOK FOR, in the order that matters:

  1. `only v1` rows near zero. v2 finding a limit everywhere v1 does is the bar for flipping over.
     A handful of disagreements at a boundary is a matching difference; a systematic gap is not.
  2. `only v2` rows are a WIN, not a discrepancy -- those are places v1 had nothing. The measured
     US 40/189 case is exactly this shape: the tile holds 65 mph and SLA showed nothing.
  3. `differ` rows. Same place, different number. Read a few by hand before believing either.

It also prints what v1 structurally cannot say, which is most of the reason for the migration:
whether a tile was loaded at all, whether the matcher was confident or lost, and the road class.

Nothing here writes anything or changes any setting.

USAGE, on the device:

    cd /data/openpilot && python tools/bp_mapd_compare.py
    python tools/bp_mapd_compare.py --route 00000380--aa11bb22cc --max-segments 30
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

REALDATA = "/data/media/0/realdata"
MS_TO_MPH = 2.23694


def seg_index(name: str) -> int:
  """Segment order is NUMERIC -- sorted() puts --10 before --2. See bp_why_slow.py."""
  tail = name.rsplit("--", 1)[-1]
  return int(tail) if tail.isdigit() else -1


def find_segments(route: str | None) -> list[str]:
  if not os.path.isdir(REALDATA):
    sys.exit(f"no {REALDATA} -- run this on the device")
  entries = sorted((d for d in os.listdir(REALDATA) if "--" in d), key=seg_index)
  if not entries:
    sys.exit("no route segments")
  if route is None:
    route = entries[-1].rsplit("--", 1)[0]
    print(f"# newest route: {route}")
  segs = [os.path.join(REALDATA, d) for d in entries if d.startswith(route + "--")]
  if not segs:
    sys.exit(f"no segments for {route}")
  return segs


def rlog(seg: str) -> str | None:
  for name in ("rlog", "rlog.zst", "rlog.bz2"):
    p = os.path.join(seg, name)
    if os.path.exists(p):
      return p
  return None


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--route", default=None)
  ap.add_argument("--max-segments", type=int, default=20)
  ap.add_argument("--tolerance", type=float, default=1.0, help="mph the two may differ by")
  args = ap.parse_args()

  try:
    from openpilot.tools.lib.logreader import LogReader
  except ImportError as e:
    sys.exit(f"no LogReader ({e}); run from /data/openpilot")

  segs = find_segments(args.route)
  total_segs = len(segs)
  if len(segs) > args.max_segments:
    # Evenly spaced, not the first N: the front of a route is the driveway, and sampling it is how
    # two sessions independently concluded Speed Limit Assist was broken on 2026-08-16.
    step = len(segs) / args.max_segments
    segs = [segs[int(i * step)] for i in range(args.max_segments)]
    print(f"# sampling {args.max_segments} of {total_segs} segments, spread across the route")

  v1 = None            # last liveMapDataSP: (valid, limit)
  v2_frames = 0
  v1_frames = 0
  agree = both_none = only_v1 = only_v2 = differ = 0
  differences: list[str] = []
  tile_loaded = Counter()
  way_selection = Counter()
  highway_class = Counter()
  road_names: Counter = Counter()

  for seg in segs:
    path = rlog(seg)
    if path is None:
      continue
    for msg in LogReader(path):
      w = msg.which()
      try:
        if w == "liveMapDataSP":
          m = msg.liveMapDataSP
          v1 = (bool(m.speedLimitValid), float(m.speedLimit))
          v1_frames += 1
        elif w == "mapdOut":
          m = msg.mapdOut
          v2_frames += 1
          tile_loaded[bool(m.tileLoaded)] += 1
          way_selection[str(m.waySelectionType)] += 1
          highway_class[str(m.highwayClass)] += 1
          if m.roadName:
            road_names[str(m.roadName)] += 1

          if v1 is None:
            continue
          v1_has, v1_limit = v1
          v2_has = float(m.speedLimit) > 0
          v2_limit = float(m.speedLimit)
          if not v1_has and not v2_has:
            both_none += 1
          elif v1_has and not v2_has:
            only_v1 += 1
            if len(differences) < 15:
              differences.append(f"  only v1: {v1_limit * MS_TO_MPH:3.0f} mph   "
                                 f"v2 way={str(m.waySelectionType):9} tile={bool(m.tileLoaded)!s:5} "
                                 f"{m.roadName or m.wayRef or ''}")
          elif v2_has and not v1_has:
            only_v2 += 1
            if len(differences) < 15:
              differences.append(f"  only v2: {v2_limit * MS_TO_MPH:3.0f} mph   "
                                 f"{str(m.highwayClass):13} way {m.wayId}  {m.roadName or m.wayRef or ''}")
          elif abs(v1_limit - v2_limit) * MS_TO_MPH <= args.tolerance:
            agree += 1
          else:
            differ += 1
            if len(differences) < 15:
              differences.append(f"  differ:  v1 {v1_limit * MS_TO_MPH:3.0f} vs v2 "
                                 f"{v2_limit * MS_TO_MPH:3.0f} mph  {str(m.highwayClass):13} "
                                 f"{m.roadName or m.wayRef or ''}")
      except Exception:  # noqa: BLE001
        continue

  print(f"\nliveMapDataSP (v1) frames: {v1_frames}")
  print(f"mapdOut       (v2) frames: {v2_frames}")
  if not v2_frames:
    print("\nNO mapdOut IN THIS ROUTE. Either the v2 binary is not installed, or it is not running.")
    print("  check:  ls -la /data/openpilot/third_party/mapd_pfeiferj/mapd_v2")
    print("          grep -h 'mapd' /data/log/* | tail -20")
    return 1
  if not v1_frames:
    print("\nNo liveMapDataSP -- nothing to compare against.")
    return 1

  compared = agree + both_none + only_v1 + only_v2 + differ
  pct = lambda v: f"{100.0 * v / compared:5.1f}%" if compared else "  n/a"  # noqa: E731
  print(f"\ncompared on {compared} frames where both had spoken:\n")
  print(f"  both agree on a limit      {agree:7d}  {pct(agree)}")
  print(f"  both say no limit          {both_none:7d}  {pct(both_none)}")
  print(f"  ONLY v1 had a limit        {only_v1:7d}  {pct(only_v1)}   <-- must be near zero to flip")
  print(f"  only v2 had a limit        {only_v2:7d}  {pct(only_v2)}   <-- a win: v1 was blind here")
  print(f"  differ by >{args.tolerance:.0f} mph          {differ:7d}  {pct(differ)}")

  print("\nwhat v1 could never report:")
  print(f"  tileLoaded:      {dict(tile_loaded)}")
  print(f"  waySelectionType {dict(way_selection.most_common())}")
  print(f"  highwayClass     {dict(highway_class.most_common(8))}")
  if road_names:
    print(f"  roads seen:      {', '.join(n for n, _ in road_names.most_common(6))}")

  fail = way_selection.get("fail", 0)
  if fail:
    print(f"\n  waySelectionType=fail on {fail} frames -- the matcher LOST the road there. That is the")
    print("  state v1 has no way to express, and the one behind a limit that silently never arrives.")

  if differences:
    print("\nfirst disagreements:")
    print("\n".join(differences))

  print("\nFlip MapdV2 on when `ONLY v1` is near zero. Until then v1 is still feeding SLA and")
  print("nothing about the car's behavior has changed by installing v2.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
