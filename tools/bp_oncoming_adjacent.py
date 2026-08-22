#!/usr/bin/env python3
"""FusionPilot: is `oncomingAdjacent` ever set on a road the MAP says is one-way?

WHY THE FRAME SHARE WAS THE WRONG NUMBER, and this tool exists because that mistake was made here
first. `bp_passing_unread.py` reported `oncomingAdjacent` true on 28.9% of left-side frames on a
freeway drive and that reads like a firing rate. It is not:
`AdjacentLane.oncoming_adjacent_seconds` is set to `memory_s` on a corroborated sighting and then
DECAYS by dt every cycle, and the published flag is simply `> 0.0`. So the share measures how long
the memory window stays open, not how often anything was seen. A handful of sightings with a 90 s
window fills a large fraction of a drive by construction.

That is the same denominator error this fork has now made four times, most recently on the oncoming
VETO earlier the same day -- a share that is perfectly correct over a numerator too small to act
on. So this counts RISING EDGES, which are sightings.

THE QUESTION IT ACTUALLY ANSWERS, and it needs no "when and where" from the owner:

  `oncomingAdjacent` means opposing traffic in the lane RIGHT NEXT to us. On a divided highway
  that is impossible. If a rising edge lands on a frame where mapdOut says `oneWay` -- or
  `highwayClass` is motorway -- the flag is provably wrong, on evidence already recorded.

  Being wrong there is only interesting because it is ONE INPUT away from a refusal. The veto
  itself is guarded and fired 6.7 s on the drive that produced the 28.9%; do not read a bad edge
  here as a bad refusal.

  python tools/bp_oncoming_adjacent.py <route-prefix>

MAP COVERAGE IS REPORTED, NOT ASSUMED. `mapdOut` only exists when MapdV2 is at observe or on, and
a route recorded with it off says nothing either way. A zero here with no map frames is a statement
about the route, not about the flag -- see the note this fork keeps relearning about absence in a
log being evidence about the log's conditions first.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

REALDATA = "/data/media/0/realdata"


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

  prev = {"left": False, "right": False}
  edges = {"left": 0, "right": 0}
  # The map as it stood at the moment of the edge. Held from the last mapdOut rather than required
  # on the same frame -- mapdOut is 20 Hz and longitudinalPlanSP is not, so demanding both on one
  # frame would score almost every edge as "no map".
  cur = {"oneway": None, "hwy": None, "speed": 0.0}
  verdict = Counter()
  examples = {"left": [], "right": []}
  map_frames = 0
  pa_frames = 0

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
        map_frames += 1
        try:
          cur["oneway"] = bool(m.mapdOut.oneWay)
          cur["hwy"] = str(m.mapdOut.highwayClass)
        except Exception:  # noqa: BLE001
          pass
        continue
      if w == "carState":
        cur["speed"] = float(m.carState.vEgo)
        continue
      if w != "longitudinalPlanSP":
        continue
      try:
        pa = m.longitudinalPlanSP.passingAssist
      except Exception:  # noqa: BLE001
        continue
      pa_frames += 1
      for name, a in (("left", pa.adjacentLeft), ("right", pa.adjacentRight)):
        now = bool(a.available) and bool(a.oncomingAdjacent)
        if now and not prev[name]:
          edges[name] += 1
          ow, hwy = cur["oneway"], cur["hwy"]
          if ow is None:
            verdict["no map data"] += 1
          elif ow or hwy in ("motorway", "motorwayLink"):
            # PROVABLY WRONG: opposing traffic cannot be in the adjacent lane of a one-way road.
            verdict["ON A ONE-WAY ROAD -- impossible"] += 1
            if len(examples[name]) < 5:
              examples[name].append((hwy, ow, cur["speed"] * 2.23694))
          else:
            verdict["two-way road -- plausible"] += 1
        prev[name] = now

  print(f"route {args.route}   {len(segs)} segments   {pa_frames} plan frames   {map_frames} mapdOut frames")
  if not map_frames:
    print("\n  NO mapdOut ON THIS ROUTE. MapdV2 was off when it was recorded, so this route cannot")
    print("  answer the question either way. That is a fact about the route, not about the flag.")
  print(f"\n  oncomingAdjacent RISING EDGES (sightings, not frames):  left {edges['left']}  right {edges['right']}")
  for k, n in verdict.most_common():
    print(f"    {k:34s} {n:5d}")
  for name in ("left", "right"):
    for hwy, ow, mph in examples[name]:
      print(f"    example {name}: highwayClass={hwy} oneWay={ow} at {mph:.0f} mph")
  print()
  print("  A rising edge on a one-way road is the flag being wrong on evidence already recorded --")
  print("  no 'when and where' needed. It is one input away from a refusal; the veto itself is")
  print("  separately guarded, so do not read a bad edge here as a bad refusal.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
