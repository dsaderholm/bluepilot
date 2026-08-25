#!/usr/bin/env python3
"""FusionPilot: can RADAR-SEEN TRAFFIC prove a left lane exists, without re-creating the turn lane?

THE QUESTION, AND THIS FILE EXISTS BECAUSE CLAUDE.md ASKED FOR IT BY NAME. The left geometry gate is
the top blocker on 53-87% of every drive, and it is a CAMERA question -- "is there a lane over
there" -- which the camera answers badly: the model draws a phantom far-left line a full lane out
even from the leftmost lane (3.49 m vs 3.45 m, indistinguishable). Meanwhile the thing radar is
genuinely better than a human at -- is that car slower, and by how much -- was never the bottleneck.

A vehicle travelling our way in that lane PROVES the lane exists. That was built, shipped, and
REVERTED the same evening on 2026-08-09:

    "It tried to change lanes into the center turn lane median thing 3 times!"

A center turn lane is painted like a travel lane, sized like one, and has cars moving down it in our
direction. The revert note set the condition for ever re-proposing it:

    "geoLeftTravelProven is the same share with the speed test applied; the gap between them is the
     turn-lane exposure. Read both, ON A ROAD WITH A TURN LANE, before proposing this again -- and
     note that neither number needs map data, which is the point: this has to work where tileLoaded
     is false."

  python tools/bp_radar_lane_proof.py <route-prefix> [<route-prefix> ...]

WHAT IT MEASURES, restricted to frames where the LEFT GEOMETRY GATE REFUSED -- the coverage actually
at stake -- and split by whether the map calls the road two-way, which is where turn lanes live:

    sameDirRecent     radar saw same-direction traffic left. The 2026-08-09 evidence, unfiltered.
    ever ovtk         a vehicle has overtaken us on that side AT ANY POINT this drive.
    ovtk<Ns           ...and it happened within OVERTAKE_RECENT_S. THIS is the speed test: a car
                      easing into a turn lane does not go PAST us, and one that went past a minute
                      ago was on a different piece of road.
    oncomingAdjacent  opposing traffic seen left. On a two-way road this is the tell that the lane
                      is theirs, and it is the guard the reverted version did not have.

**THE `ever` AND `<Ns` COLUMNS EXIST BECAUSE THE FIRST VERSION OF THIS TOOL CONFLATED THEM**, and
the result was wrong in the flattering direction. It tested `overtakenVAbs > 0`, which reads like
"a vehicle went past us" and is not: custom.capnp calls it "ground speed of the LAST one", it is
LATCHED, and `adjacent_lane` deliberately carries it across resets so a dropout cannot erase it. On
a freeway it becomes true in the first minute and stays true for the drive. That produced a 6:1
separation partly built on freeways simply having more overtakes than arterials.

`overtakenSeconds` is the recency, and `overtakenCount` disambiguates its zero -- the field means
both "seconds since the last one" and "0 = never seen", so 0 alone cannot separate "just happened"
from "never happened".

**THE GAP BETWEEN sameDir AND ovtk<Ns IS THE TURN-LANE EXPOSURE**, which is the number the revert
note asked for. The oncoming column is what a safe version would additionally require.

HOW TO READ IT:

  ONE-WAY rows large, TWO-WAY rows small   -> traffic-as-proof recovers real coverage on exactly the
                                              roads it is safe on, and the map is not needed to tell
                                              them apart because ONCOMING already does.
  BOTH large                               -> the evidence cannot distinguish a passing lane from a
                                              turn lane, which is what the road already proved once.
                                              Do not rebuild it.

THE MAP LABELS, IT DOES NOT GATE. `oneWay` selects which frames to look at. Any rule built on this
has to work with `tileLoaded` false, so the CANDIDATE GUARD is `oncomingAdjacent` -- a radar fact --
never the map label. That is the whole point of measuring them side by side: if oncoming separates
the two classes as well as the map does, the guard can be radar-only.

AND THIS IS AN OPENING CHANGE, so the bar is the high one. Evidence that opens a maneuver must never
be cheaper than evidence that refuses. Nothing here proposes shipping anything; it decides whether
the question is worth another hour.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

REALDATA = "/data/media/0/realdata"

# HOW RECENT AN OVERTAKE HAS TO BE to count as evidence the lane has moving traffic in it NOW.
# A vehicle that went past a minute ago was on a different piece of road, and the whole failure this
# rule has to avoid is treating a stale fact as a present one. Reported at several windows below
# rather than defended as a single value -- if the answer depends heavily on the window, that is
# itself the finding.
OVERTAKE_RECENT_S = 15.0


def pct(n, d):
  return f"{100.0 * n / d:5.1f}%" if d else "    --"


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("route", nargs="+")
  ap.add_argument("--segments", type=int, default=0)
  args = ap.parse_args()

  from openpilot.tools.lib.logreader import LogReader

  # FAIL LOUDLY on a missing field -- a broad try around a schema read turns a typo into a confident
  # zero, and a confident zero here reads as "traffic proof is unavailable", which would retire a
  # candidate on a spelling mistake.
  from cereal import custom
  pa = set(custom.LongitudinalPlanSP.PassingAssist.schema.fieldnames)
  for f in ("leftGeometryOk", "adjacentLeft"):
    if f not in pa:
      sys.exit(f"passingAssist has no field {f!r}")
  adj = set(custom.LongitudinalPlanSP.PassingAssist.AdjacentLane.schema.fieldnames)
  for f in ("available", "sameDirectionRecent", "oncomingAdjacent",
            "overtakenSeconds", "overtakenCount"):
    if f not in adj:
      sys.exit(f"adjacentLeft has no field {f!r} -- this tool would silently report zeros")

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

  tally = defaultdict(Counter)
  hwy_seen = Counter()
  cur = {"oneway": None, "hwy": "?", "speed": 0.0}

  for seg in all_segs:
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
        try:
          cur["oneway"] = bool(m.mapdOut.oneWay)
          cur["hwy"] = str(m.mapdOut.highwayClass) or "?"
        except Exception:  # noqa: BLE001
          pass
        continue
      if w == "carState":
        cur["speed"] = float(m.carState.vEgo)
        continue
      if w != "longitudinalPlanSP":
        continue
      try:
        p_a = m.longitudinalPlanSP.passingAssist
      except Exception:  # noqa: BLE001
        continue
      if cur["speed"] < 10.0 or cur["oneway"] is None:
        continue
      # ONLY FRAMES THE GEOMETRY GATE REFUSED. This is the coverage at stake -- frames it already
      # opens need no extra evidence, and including them would dilute every share below with the
      # cases that are already working.
      if bool(p_a.leftGeometryOk):
        continue

      kind = "TWO-WAY" if not cur["oneway"] else "one-way"
      hwy_seen[(kind, cur["hwy"])] += 1
      c = tally[kind]
      c["refused"] += 1
      try:
        a = p_a.adjacentLeft
        if not bool(a.available):
          c["radar blind"] += 1
          continue
        same = bool(a.sameDirectionRecent)
        onc = bool(a.oncomingAdjacent)
        # THE SPEED TEST HAS TO BE RECENT, AND THE FIRST VERSION OF THIS TOOL GOT IT WRONG.
        #
        # It used `overtakenVAbs > 0.0`, which reads as "a vehicle went past us". It is not:
        # custom.capnp calls it "ground speed of the LAST one", it is LATCHED, and adjacent_lane
        # carries it across resets in the `held` tuple precisely so a sensor dropout cannot erase
        # it. So on a freeway it goes true within the first minute of the drive and stays true --
        # the column was measuring "somebody overtook us at some point", which is nearly a constant.
        #
        # That inflated the one-way row and produced a 6:1 separation that was partly an artifact of
        # freeways having more overtakes than arterials, rather than of the lane being proven.
        #
        # `overtakenSeconds` is the recency, and `overtakenCount` disambiguates its zero -- the
        # field is "seconds since the last one" AND "0 = never seen", so 0 alone cannot tell
        # "just happened" from "never happened".
        n_over = int(a.overtakenCount)
        secs = float(a.overtakenSeconds)
        travelling = n_over > 0 and secs <= OVERTAKE_RECENT_S
        c["ever overtaken"] += n_over > 0
        c["sameDir"] += same
        c["travelling"] += travelling
        c["oncoming"] += onc
        if same and not onc:
          c["sameDir, no oncoming"] += 1
        if travelling and not onc:
          c["TRAVELLING, no oncoming"] += 1
      except Exception:  # noqa: BLE001
        c["radar blind"] += 1

  if not tally:
    sys.exit("no moving frames with map data where the left gate refused")

  print(f"routes {args.route}")
  print("  frames where the LEFT GEOMETRY GATE REFUSED -- the coverage actually at stake\n")
  print(f"  {'road':<9} {'refused':>9} {'sameDir':>9} {'ever ovtk':>10} "
        f"{'ovtk<' + str(int(OVERTAKE_RECENT_S)) + 's':>10} {'oncoming':>9} {'RECENT,no onc':>14}")
  for kind in ("one-way", "TWO-WAY"):
    c = tally.get(kind)
    if not c:
      continue
    n = c["refused"]
    print(f"  {kind:<9} {n:9d} {pct(c['sameDir'], n):>9} {pct(c['ever overtaken'], n):>10} "
          f"{pct(c['travelling'], n):>10} {pct(c['oncoming'], n):>9} "
          f"{pct(c['TRAVELLING, no oncoming'], n):>14}")
  print()
  print("  'ever ovtk' vs 'ovtk<Ns' IS THE CORRECTION. The first version of this tool used the")
  print("  LATCHED overtakenVAbs, which is the 'ever' column -- true for the rest of the drive")
  print("  after one overtake, and therefore nearly a constant on a freeway. Compare the two")
  print("  columns: the gap is how much of the original separation was that artifact.")
  print()
  print("  road classes seen:")
  for (kind, hwy), n in sorted(hwy_seen.items(), key=lambda kv: -kv[1])[:8]:
    print(f"    {kind:<9} {hwy:<14} {n}")
  print()
  print("  THE GAP BETWEEN sameDir AND TRAVELLING IS THE TURN-LANE EXPOSURE -- the number the")
  print("  2026-08-09 revert note asked for before this may be re-proposed.")
  print()
  print("  'TRAV, no onc' is the candidate rule: a vehicle went PAST us on the left, and no")
  print("  opposing traffic has been seen there. On a one-way road that is a passing lane. On a")
  print("  two-way road it is what a center turn lane looks like WHEN NOBODY HAPPENS TO BE COMING.")
  print()
  print("  LARGE on one-way and SMALL on TWO-WAY -> the rule recovers coverage where it is safe and")
  print("     stays quiet where it is not, using no map data, which is the requirement.")
  print("  LARGE ON BOTH -> the evidence cannot tell a passing lane from a turn lane. The road")
  print("     already proved that once, in three lane changes. Do not rebuild it.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
