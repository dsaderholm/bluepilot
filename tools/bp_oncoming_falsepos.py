#!/usr/bin/env python3
"""FusionPilot: is the oncoming veto firing on divided highway, where it cannot be right?

RUN THIS ON THE CAR:

    cd /data/openpilot && python tools/bp_oncoming_falsepos.py
    cd /data/openpilot && python tools/bp_oncoming_falsepos.py --route 0000038a

WHY IT EXISTS
passing_assist.py has owed this measurement since the oncoming veto was written:

    "What is still worth measuring from a drive: how often it fires on a divided road it should
     not, and whether 90 s of memory is the right number for the roads actually driven."

The failure it names is real and reported from the road -- "I was on I-15 for a while, and kept
saying two-way road" -- and each firing is a 90 second silence, so on a divided highway a leaky veto
is not a quiet stretch, it is the feature switched off for the drive.

A speed-scaled floor (ONCOMING_SPEED_FRACTION) was shipped to fix it and HAS NEVER BEEN VERIFIED ON
ROAD. This is that verification.

WHAT MADE IT MEASURABLE
`mapdOut.oneWay` under MapdV2 observe. On a divided highway each carriageway is a one-way way, so
oncoming traffic in an ADJACENT lane is impossible by construction. Veto active + oneWay true is
therefore a false positive that needs no human labelling.

`oneWay` alone is not enough on its own -- a one-way city street is also oneWay and has no oncoming
either, but it is not the road the complaint was about. So motorway frames are counted separately,
and that row is the one the fix is judged on.

THE MAP IS USED HERE AS EVIDENCE ABOUT A REFUSAL, WHICH IS THE ALLOWED DIRECTION. It is measuring
whether a veto was wrong, offline, after the fact. Nothing here runs on the car or opens anything.

Read-only.
"""

import argparse
import glob
import os
import sys
from collections import Counter


# Below this the oncoming veto is not the question -- see MIN_SPEED_MS in the detector's own gates.
MIN_SPEED_MS = 5.0


def find_segments(route):
  base = "/data/media/0/realdata"
  if route:
    segs = sorted(glob.glob(os.path.join(base, f"{route}--*")))
    if not segs:
      sys.exit(f"no segments for {route}")
    return segs
  routes = sorted({os.path.basename(p).split("--")[0] for p in glob.glob(os.path.join(base, "*--*"))})
  if not routes:
    sys.exit("no routes on this device")
  return sorted(glob.glob(os.path.join(base, f"{routes[-1]}--*")))


def spread(segs, cap):
  """EVENLY SPACED, never the first N. A front cap put a parked-car figure into CLAUDE.md twice in
  one day, from two sessions independently."""
  if len(segs) <= cap:
    return segs, len(segs)
  step = len(segs) / cap
  return [segs[int(i * step)] for i in range(cap)], len(segs)


class Tally:
  """Cross-tab of "was the veto up" against "does the map say this carriageway is one-way".

  Kept out of main() so the counting can be exercised without a route. The whole output is four
  numbers and a verdict, and getting the verdict backwards is the only way this tool can fail.
  """

  def __init__(self):
    self.frames = 0
    self.cells = Counter()        # (veto, oneway) -> n
    self.motorway_cells = Counter()
    self.mapd_frames = 0
    self.seen_speeds = 0

  def feed_segment(self, messages):
    veto = None
    speed = 0.0
    for m in messages:
      w = m.which()
      if w == "carState":
        speed = float(m.carState.vEgo)
        self.seen_speeds += 1
        continue
      if w == "longitudinalPlanSP":
        # The detector's own view, published every decision frame.
        try:
          veto = bool(m.longitudinalPlanSP.passingAssist.oncomingAnySide)
        except Exception:
          veto = None
        continue
      if w != "mapdOut":
        continue
      self.mapd_frames += 1
      if veto is None or speed < MIN_SPEED_MS:
        continue
      o = m.mapdOut
      # No tile, no claim. tileLoaded false means the map cannot answer, which is not the same as
      # answering "two-way" -- the same unavailable-is-not-clear rule the radar side runs on.
      if not bool(o.tileLoaded):
        continue
      self.frames += 1
      key = (veto, bool(o.oneWay))
      self.cells[key] += 1
      if str(o.highwayClass) == "motorway":
        self.motorway_cells[key] += 1


def _report(name, cells):
  total = sum(cells.values())
  if not total:
    print(f"{name}: no frames")
    return None
  fp = cells[(True, True)]          # veto up on a one-way carriageway: impossible to be right
  ok_quiet = cells[(False, True)]
  one_way_total = fp + ok_quiet
  print(f"{name}: {total} frames")
  print(f"  one-way carriageway: {one_way_total}   veto up on {fp}"
        f"{f' ({100.0 * fp / one_way_total:.1f}%)' if one_way_total else ''}")
  print(f"  two-way road:        {cells[(True, False)] + cells[(False, False)]}"
        f"   veto up on {cells[(True, False)]}")
  return (fp / one_way_total) if one_way_total else None


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--route", default=None)
  ap.add_argument("--max-segments", type=int, default=40)
  args = ap.parse_args()

  try:
    from openpilot.tools.lib.logreader import LogReader
  except Exception as e:
    sys.exit(f"no LogReader ({e}); run this from /data/openpilot")

  segs, total = spread(find_segments(args.route), args.max_segments)
  if len(segs) < total:
    print(f"# sampling {len(segs)} of {total} segments, spread evenly across the route")

  t = Tally()
  for seg in segs:
    f = os.path.join(seg, "rlog.zst")
    if os.path.exists(f):
      t.feed_segment(LogReader(f))

  if not t.mapd_frames:
    print("NO mapdOut IN THIS ROUTE.")
    print("Check `uptime` against the segment mtimes before concluding anything -- a route recorded")
    print("before the running build predates the feature, and v2 publishes nothing offroad.")
    return 0
  if not t.frames:
    print(f"mapdOut present ({t.mapd_frames} frames) but nothing scorable: no frames with a loaded")
    print("tile, a known veto state and speed over the floor. A short or slow drive does this.")
    return 0

  print()
  _report("ALL ROADS", t.cells)
  print()
  rate = _report("MOTORWAY ONLY (the row the fix is judged on)", t.motorway_cells)
  print()
  if rate is None:
    print("no motorway frames -- this drive cannot judge the I-15 case")
  elif rate == 0.0:
    print("-> CLEAN. The veto never fired on a divided carriageway. The speed-scaled floor holds.")
  elif rate < 0.02:
    print(f"-> ESSENTIALLY CLEAN at {rate * 100:.1f}%. Occasional, and each one costs 90 s of")
    print("   silence, so worth knowing but not worth changing a threshold over.")
  else:
    print(f"-> STILL LEAKING at {rate * 100:.1f}% of divided-carriageway frames. Each firing is a")
    print("   90 s silence, so this is the feature switched off for that share of the highway.")
    print("   The speed-scaled floor did not close it; look at ONCOMING_SPEED_FRACTION next.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
