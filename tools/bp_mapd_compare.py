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

# liveMapDataSP is 1 Hz. Three seconds without one means v1 has stopped talking, not
# that it is standing by its last answer.
V1_STALE_NS = 3_000_000_000


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
  ap.add_argument("--min-speed", type=float, default=5.0,
                  help="mph below which frames are not scored (see the staleness note below)")
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

  # (valid, limit, logMonoTime) -- the timestamp is load-bearing, see V1_STALE_NS below.
  v1 = None
  v2_frames = 0
  v1_frames = 0
  v1_stale = 0
  v1_stopped = 0
  vego_mph = 0.0
  v1_consumed = True   # nothing to compare until the first v1 sample arrives
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
        # V1 SERVES STALE VALUES AND THEY LOOK EXACTLY LIKE LIVE ONES. /dev/shm/params keeps
        # MapSpeedLimit and RoadName from the PREVIOUS drive -- tmpfs survives ignition cycles within
        # a boot -- and mapd_manager republishes liveMapDataSP from them every tick regardless. So at
        # the start of a drive v1 confidently reports last trip's speed limit while v2, correctly,
        # publishes nothing until it has a position.
        #
        # Scored naively that is a run of "only v1 had a limit" rows, which is THE number the cutover
        # decision rests on, poisoned in the direction of never switching -- by v1 being wrong rather
        # than v2 being deficient. Found by the passing-assist session parked in a driveway, where
        # v1 looked alive and v2 looked broken and it was the other way round.
        #
        # Requiring the car to be moving is enough: by then GPS is live and v1 is updating from a
        # real position. Same reasoning as bp_map_vs_sla.py's own min-speed gate.
        if w == "carState":
          vego_mph = msg.carState.vEgo * MS_TO_MPH
        elif w == "liveMapDataSP":
          m = msg.liveMapDataSP
          v1 = (bool(m.speedLimitValid), float(m.speedLimit), msg.logMonoTime)
          v1_frames += 1
          v1_consumed = False
        elif w == "mapdOut":
          m = msg.mapdOut
          v2_frames += 1
          tile_loaded[bool(m.tileLoaded)] += 1
          way_selection[str(m.waySelectionType)] += 1
          highway_class[str(m.highwayClass)] += 1
          if m.roadName:
            road_names[str(m.roadName)] += 1

          if vego_mph < args.min_speed:
            v1_stopped += 1
            continue
          if v1 is None:
            continue
          v1_has, v1_limit, v1_time = v1

          # STALENESS FIRST, and the order matters. If mapd_manager or v1 dies mid-drive,
          # liveMapDataSP simply stops while mapdOut keeps going, and a frozen last value must not
          # be scored against the rest of the route. Counting every unscored v2 frame here is also
          # the diagnostic that says v1 went quiet, which is worth knowing on its own.
          #
          # Behind the consumed check it could never fire, since the frozen sample is consumed once
          # and every later frame returns above -- a guard that cannot run, which is exactly what
          # this review flagged elsewhere.
          if msg.logMonoTime - v1_time > V1_STALE_NS:
            v1_stale += 1
            continue

          # ONE comparison per v1 sample, not per v2 frame. v1 publishes at 1 Hz and v2 at 20, so
          # comparing on every v2 frame counted each v1 sample twenty times: a one-second transient
          # at a speed-limit boundary became twenty "differ" rows, and the percentages described v2
          # frames while reading as percentages of the drive. This is the number that decides the
          # cutover, so it gets the honest denominator.
          if v1_consumed:
            continue
          v1_consumed = True
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
  if v1_stopped:
    print(f"\n  {v1_stopped} frames below {args.min_speed:.0f} mph were NOT scored -- v1 serves the")
    print("  previous drive's speed limit out of /dev/shm/params while stationary, and it is")
    print("  indistinguishable from a live one. Scoring those counts v1 right and v2 blind.")
  if v1_stale:
    print(f"\n  {v1_stale} v2 frames had no fresh v1 sample within 3 s and were NOT scored.")
    print("  A large number here means v1 stopped publishing mid-drive -- check mapd_manager.")

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
