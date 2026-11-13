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

# Above this, an apparent opposing speed is a VEHICLE, not angle-error clutter. Deliberately well
# clear of the 5 m/s (11 mph) noise floor and of anything the speed-scaled floor reaches at the
# arterial speeds where the false edges live. Nothing here depends on the exact value -- it splits
# a verdict in a diagnostic and gates nothing on the car.
REAL_TRAFFIC_MPH = 20.0

# ...AND AN UPPER BOUND, because the band above had none and route 000003ba walked straight through
# the gap: `primary oneWay=True, ego 1 mph, target |v_abs| 95 mph`. Ninety-five miles an hour of
# opposing motion while the car is essentially stopped, on an arterial. That is not a vehicle across
# a median; it is a radar artifact, and the first version of this split called it real traffic
# purely because it cleared 20.
#
# Same mistake as the narrowing measurement made a day earlier -- a plausibility band with a floor
# and no ceiling -- which is why the fix is the same shape. A quantity that has a physical range
# needs BOTH ends stated, and it is the end nobody thought about that admits the garbage.
#
# 85 is above anything opposing traffic reaches on the roads this car drives, and well clear of the
# 39 and 29 mph sightings that motivated the median category in the first place.
REAL_TRAFFIC_MAX_MPH = 85.0


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("route", nargs="+", help="one or more route prefixes; edges are POOLED")
  ap.add_argument("--segments", type=int, default=0)
  args = ap.parse_args()

  from openpilot.tools.lib.logreader import LogReader
  # IMPORTED HERE, NOT AT THE SWEEP. The live rule is also needed inside the frame loop now, to
  # record what the floor WAS at each example edge -- and the sweep's own import sits ~100 lines
  # further down, so using it in the loop against that import is a NameError the moment an
  # impossible edge is found. Which is to say: only on the routes this tool exists for.
  from openpilot.sunnypilot.selfdrive.controls.lib.adjacent_lane import min_oncoming_ms

  # POOLED ACROSS ROUTES, and for this tool that is not a convenience. Its whole output is a small
  # count of rising edges, and single-drive readings from it have been published and withdrawn
  # TWICE -- "seven of ten are impossible" became 33% once ten more drives were added. Pooling is
  # the fix for the mistake this file's own docstring warns about.
  all_segs = []
  for route in args.route:
    segs = sorted((d for d in os.listdir(REALDATA) if d.startswith(route)),
                  key=lambda d: int(d.rsplit("--", 1)[-1]) if d.rsplit("--", 1)[-1].isdigit() else -1)
    if args.segments:
      segs = segs[:args.segments]
    if not segs:
      print(f"  (no segments matching {route})")
      continue
    all_segs += segs
  if not all_segs:
    sys.exit(f"no segments matching any of {args.route}")
  segs = all_segs

  prev = {"left": False, "right": False}
  edges = {"left": 0, "right": 0}
  # The map as it stood at the moment of the edge. Held from the last mapdOut rather than required
  # on the same frame -- mapdOut is 20 Hz and longitudinalPlanSP is not, so demanding both on one
  # frame would score almost every edge as "no map".
  cur = {"oneway": None, "hwy": None, "speed": 0.0}
  verdict = Counter()
  examples = {"left": [], "right": []}
  # EVERY EDGE, with what it was seen at. This is what makes the floor question answerable rather
  # than arguable: a candidate floor is scored by which of these it would have rejected.
  events = []
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
          # THE SWEEP'S TARGET SET, narrowed to match the verdict split above. A one-way edge that
          # is MOVING LIKE TRAFFIC is most likely a real vehicle across a median, so counting it as
          # a false positive the floor should kill would ask the floor to reject genuine oncoming
          # traffic -- the one thing it must never do. Only the slow, clutter-shaped edges are
          # scored as gains here.
          _v = abs(float(a.oncomingVAbs)) * 2.23694
          impossible = (ow is not None and (ow or hwy in ("motorway", "motorwayLink"))
                        and not (REAL_TRAFFIC_MPH <= _v <= REAL_TRAFFIC_MAX_MPH))
          events.append({"side": name, "v_ego": cur["speed"],
                         "v_abs": float(a.oncomingVAbs), "hwy": hwy, "oneway": ow,
                         "impossible": impossible, "known": ow is not None})
          if ow is None:
            verdict["no map data"] += 1
          elif ow or hwy in ("motorway", "motorwayLink"):
            # "ONE-WAY THEREFORE IMPOSSIBLE" IS TOO STRONG, AND ROUTE 000003b7 SHOWED IT.
            #
            # `oneWay=True` says THIS carriageway runs one way. It does not say no opposing traffic
            # is nearby -- a divided highway is one-way on BOTH carriageways and they sit next to
            # each other, and a `motorwayLink` ramp at an interchange usually has its opposite
            # number a few metres away. Across a narrow median, a real oncoming vehicle is exactly
            # what the radar should see.
            #
            # Two edges on 000003b7 came in at 39 mph and 29 mph of apparent opposing speed against
            # floors of 20 and 21. Nothing moving that fast is angle-error clutter, so calling them
            # "provably wrong" was wrong: the likely truth is a real vehicle on the other side of a
            # median, correctly detected and wrongly called ADJACENT.
            #
            # Split rather than merged, because the two need opposite fixes -- clutter near zero
            # wants a floor, an across-the-median sighting wants lateral binning or a median width,
            # and pooling them is how a floor sweep gets asked to solve a problem it cannot reach.
            vabs_mph = abs(float(a.oncomingVAbs)) * 2.23694
            if vabs_mph > REAL_TRAFFIC_MAX_MPH:
              verdict["one-way, IMPLAUSIBLY FAST -- radar artifact, impossible"] += 1
            elif vabs_mph >= REAL_TRAFFIC_MPH:
              verdict["one-way, but MOVING LIKE REAL TRAFFIC -- likely across a median"] += 1
            else:
              verdict["ON A ONE-WAY ROAD, slow -- clutter, impossible"] += 1
            if len(examples[name]) < 5:
              # THE TARGET'S OWN SPEED, and it was the missing number. The example line printed the
              # EGO speed, which is only half of what the floor compares -- `min_oncoming_ms` tests
              # `|v_abs|` against `max(MIN_ONCOMING_MS, fraction * v_ego)`, so without v_abs the
              # line cannot say whether an edge sits near the threshold or miles past it. On route
              # 000003b7 two impossible edges survived every floor in the sweep and the examples
              # could not say why; that is the difference between "raise the floor" and "the floor
              # is the wrong lever entirely".
              examples[name].append((hwy, ow, cur["speed"] * 2.23694,
                                     abs(float(a.oncomingVAbs)) * 2.23694,
                                     min_oncoming_ms(cur["speed"]) * 2.23694))
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
    for hwy, ow, mph, vabs, floor in examples[name]:
      print(f"    example {name}: highwayClass={hwy} oneWay={ow}   ego {mph:.0f} mph   "
            f"target |v_abs| {vabs:.0f} mph   floor was {floor:.0f} mph"
            f"{'   <- MILES past the floor' if vabs > floor * 1.5 else ''}")
  print()

  # ---- what a different floor would have cost ------------------------------------------------
  # The live rule, imported rather than restated so this cannot drift from what the car runs.
  from openpilot.sunnypilot.selfdrive.controls.lib.adjacent_lane import (
    MIN_ONCOMING_MS, ONCOMING_SPEED_FRACTION,
  )
  known = [e for e in events if e["known"]]
  if known:
    print(f"  WHAT A DIFFERENT FLOOR WOULD COST. Live rule: max({MIN_ONCOMING_MS:.1f}, "
          f"{ONCOMING_SPEED_FRACTION:.2f} * v_ego)")
    print(f"  {'flat floor':>10} {'fraction':>9} {'FALSE killed':>13} {'real LOST':>10}")
    for flat in (5.0, 7.0, 9.0, 11.0):
      # DEDUPED, AND ALWAYS OFFERING AN ALTERNATIVE TO THE LIVE VALUE. This was
      # `(ONCOMING_SPEED_FRACTION, 0.7)`, which was fine while the constant was 0.5 and became
      # useless the moment it was RAISED to 0.7 on this tool's own evidence: every row printed
      # twice, both marked live, and the sweep no longer explored anything.
      #
      # A sweep hardcoding the value it is meant to be questioning stops being a sweep as soon as
      # its recommendation is taken. Candidates now straddle whatever is live.
      for frac in sorted({ONCOMING_SPEED_FRACTION, 0.5, 0.7, 0.85}):
        killed = lost = 0
        for e in known:
          thresh = max(flat, frac * max(0.0, e["v_ego"]))
          survives = abs(e["v_abs"]) >= thresh
          if not survives:
            if e["impossible"]:
              killed += 1
            else:
              lost += 1
        star = "  <- live" if (flat == MIN_ONCOMING_MS and frac == ONCOMING_SPEED_FRACTION) else ""
        # REACHABLE false edges only. An implausibly-fast artifact sits ABOVE every candidate floor
        # and no floor can ever kill it, so counting it in the denominator makes the lever look
        # weaker than it is -- the same denominator error this file warns about, in the
        # under-selling direction. Pooled over seven drives the split is 3 slow and 2 fast, so the
        # honest gain at flat 7.0 is 1 of 3, not 1 of 5.
        n_imp = sum(1 for e in known
                    if e["impossible"]
                    and abs(e["v_abs"]) * 2.23694 <= REAL_TRAFFIC_MAX_MPH)
        n_ok = len(known) - n_imp
        print(f"  {flat:10.1f} {frac:9.2f} {killed:8d}/{n_imp:<4d} {lost:6d}/{n_ok:<4d}{star}")
    print("  FALSE killed is the gain; real LOST is what it costs -- edges on two-way roads that")
    print("  a higher floor would also reject. Both are counted over edges whose road the MAP knew.")
    print()

  print("  A rising edge on a one-way road is the flag being wrong on evidence already recorded --")
  print("  no 'when and where' needed. It is one input away from a refusal; the veto itself is")
  print("  separately guarded, so do not read a bad edge here as a bad refusal.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
