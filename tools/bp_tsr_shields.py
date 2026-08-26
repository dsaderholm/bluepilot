#!/usr/bin/env python3
"""FusionPilot: is the camera reading ROUTE SHIELDS as speed limits?

His observation, 2026-08-25, and it reframes the whole TSR failure: *"it loves reading 30 mph signs
and interstate signs."*

That is a much more specific claim than "TSR is unreliable", and it explains the one reading that
has actually hurt him. On route 000003b6 a phantom **80** walked the set speed to 90 for thirteen
minutes on S 2165 E -- a surface street -- and he was certain he had passed no 80 sign. There is
none. But **I-80 runs through Salt Lake City**, and its route shield is a blue-and-red badge with a
large `80` on it. A recognizer that resolves a nearby `80` is not hallucinating; it is reading a
real sign and misclassifying what KIND of sign it is.

WHY THIS MATTERS MORE THAN "THE CAMERA IS BAD": 80 is a LEGAL US speed limit and Utah posts it on
I-15 and I-80, so the value cannot be rejected for being implausible. What separates a shield from a
limit is not the number, it is WHERE IT IS READ -- a surface street with a mapped limit of 30.

WHAT IT PRINTS, for every run of a camera-sourced limit:

    value / duration / position / speed / the MAP's limit and road name at the same moment

A run whose value is a plausible interstate ROUTE NUMBER (15, 80, 84, 215) read on a road the map
calls residential or tertiary at 30 mph is a shield. A run that agrees with the map is a sign.

NOT PROVEN BY THIS TOOL ALONE. It establishes the correlation; the confirmation is Street View at
the printed coordinate, which is exactly how the 2026-08-21 `30` read was verified.

    python tools/bp_tsr_shields.py --route 000003c3--124d7bae03
"""
from __future__ import annotations

import argparse
import os
import sys

from openpilot.tools.bp_logtime import DriveClock

REALDATA = "/data/media/0/realdata"
MS_TO_MPH = 2.23694

# Interstate and state route numbers that are ALSO plausible speed limits, which is what makes this
# failure mode invisible to any value-based filter. Utah: I-15, I-80, I-84, I-215.
ROUTE_NUMBERS = {15, 70, 80, 84, 215}

ONROAD_PARAM = "/data/params/d/IsOnroad"


def is_onroad() -> bool:
  try:
    with open(ONROAD_PARAM, "rb") as f:
      return f.read(1) == b"1"
  except OSError:
    return False


def seg_index(name: str) -> int:
  tail = name.rsplit("--", 1)[-1]
  return int(tail) if tail.isdigit() else -1


def _open(seg: str):
  from openpilot.tools.lib.logreader import LogReader
  for name in ("rlog", "rlog.zst", "rlog.bz2"):
    p = os.path.join(REALDATA, seg, name)
    if os.path.exists(p):
      return LogReader(p)
  return None


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--route", default=None, action="append")
  ap.add_argument("--max-segments", type=int, default=60)
  args = ap.parse_args()

  entries = sorted((d for d in os.listdir(REALDATA) if "--" in d), key=seg_index)
  routes = args.route or [entries[-1].rsplit("--", 1)[0]]
  segs = []
  for r in routes:
    segs += [d for d in entries if d.startswith(r + "--")][:args.max_segments]
  if not segs:
    sys.exit(f"no segments for {routes}")
  print(f"# {', '.join(routes)} -- {len(segs)} segments\n")

  clock = DriveClock()
  st = {"v": 0.0, "lat": 0.0, "lon": 0.0, "map": 0.0, "road": ""}
  runs = []          # [value, t0, t1, n, snapshot at start]
  cur = None

  for seg in segs:
    if is_onroad():
      print(f"\n!!! THE CAR STARTED DRIVING at {seg}. STOPPING -- results are PARTIAL.\n")
      break
    lr = _open(seg)
    if lr is None:
      continue
    for msg in lr:
      w = msg.which()
      ts = clock.seconds(msg.logMonoTime)
      try:
        if w == "carState":
          st["v"] = msg.carState.vEgo * MS_TO_MPH
          continue
        if w == "liveMapDataSP":
          lm = msg.liveMapDataSP
          st["map"] = float(lm.speedLimit) * MS_TO_MPH
          st["road"] = str(lm.roadName)
          continue
        if w in ("gpsLocationExternal", "gpsLocation"):
          g = getattr(msg, w)
          st["lat"], st["lon"] = float(g.latitude), float(g.longitude)
          continue
        if w != "carStateBP":
          continue
        v = int(msg.carStateBP.trafficSignData.vLimit1)
      except Exception:  # noqa: BLE001
        continue

      live = 0 < v < 255
      if live:
        if cur is None or cur[0] != v:
          if cur is not None:
            runs.append(cur)
          cur = [v, ts, ts, 0, dict(st)]
        cur[2] = ts
        cur[3] += 1
      elif cur is not None:
        runs.append(cur)
        cur = None
  if cur is not None:
    runs.append(cur)

  print(f"=== {len(runs)} camera-limit run(s) ===\n")
  print(f"  {'value':>5} {'dur':>7} {'frames':>7}  {'position':>24} {'mph':>4} {'map':>5}  road")
  for v, t0, t1, n, s in runs:
    pos = f"{s['lat']:.6f}, {s['lon']:.6f}" if s["lat"] else "(no fix yet)"
    mapv = f"{s['map']:5.0f}" if s["map"] > 0 else "   --"
    print(f"  {v:5d} {t1 - t0:6.1f}s {n:7d}  {pos:>24} {s['v']:4.0f} {mapv}  {s['road'][:28]}")

  print()
  shields = [r for r in runs if r[0] in ROUTE_NUMBERS and 0 < r[4]["map"] < r[0] - 10]
  signs = [r for r in runs if r not in shields]
  print(f"  LOOKS LIKE A ROUTE SHIELD: {len(shields)}   -- the value is an interstate number AND the")
  print("                                 map says this road is far slower")
  print(f"  looks like a real sign:    {len(signs)}")
  if shields:
    print()
    print("  Confirm each against Street View at the coordinate before believing it. That is how the")
    print("  2026-08-21 read of 30 was verified, and it is the only check that has ever settled one.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
