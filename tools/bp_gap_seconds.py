#!/usr/bin/env python3
"""FusionPilot: what Ford's follow-gap settings 1-5 actually ARE, in seconds.

The owner set his gap by feel and believes 3 of 5 gives him about two seconds. Nobody knows. Every
decision about whether a closer gap buys enough room to pass -- and whether closing it is worth
doing at all -- rests on a number that has never been measured on this car.

It is measurable from any route, because both halves are already logged: the setting is
`AccTGap_D_Dsply` in ACCDATA_3, and the lead distance is in radarState. Headway is dRel / vEgo.

WHAT MAKES THIS HARDER THAN A DIVISION
--------------------------------------
Most frames with a lead say nothing about the gap SETTING, because ACC is not holding a gap in
them. If the set speed binds -- lead far ahead, cruising freely -- headway is whatever the geometry
happens to be and has no relationship to the setting at all. Averaging those in drags every bucket
toward the same meaningless number, which is exactly the shape of result that looks plausible.

So only steady following counts: a lead present, closing rate near zero for a sustained stretch,
above a speed floor, no pedal input. That is ACC sitting at the distance it wants, which is the
only state that reports the setting.

READING THE SETTING
-------------------
Prefers `carStateBP.accGap`, added 2026-08-14. Falls back to decoding address 394 out of raw CAN,
so this works on routes recorded before that field existed -- which includes every route on the
device today. Byte 4, low 3 bits: DBC start bit 34, length 3, big-endian, so bits 34/33/32 are
byte 4 bits 2/1/0.

USAGE, on the device:

    cd /data/openpilot && python tools/bp_gap_seconds.py
    python tools/bp_gap_seconds.py --route 00000042--aa11bb22cc --max-segments 20
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict, deque

from openpilot.tools.bp_logtime import DriveClock

REALDATA = "/data/media/0/realdata"
MS_TO_MPH = 2.23694
ACCDATA_3_ADDR = 394

# Steady following. Each of these exists to throw away a state that would otherwise report a gap
# setting it knows nothing about.
MIN_SPEED_MS = 11.0        # ~25 mph; below this Ford ACC behaves differently and stop-and-go creeps in
MAX_CLOSING_MS = 0.5       # |vRel| -- the lead is being tracked, not caught or dropped
STEADY_S = 2.0             # how long that has to hold before a frame counts
MAX_HEADWAY_S = 4.0        # beyond this ACC is not gap-limited, whatever the radar sees
MIN_SAMPLES = 50           # a bucket below this is reported but not trusted


def seg_index(name: str) -> int:
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


def pct(values: list[float], q: float) -> float:
  if not values:
    return 0.0
  s = sorted(values)
  i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
  return s[i]


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--route", default=None)
  ap.add_argument("--max-segments", type=int, default=20)
  args = ap.parse_args()

  try:
    from openpilot.tools.lib.logreader import LogReader
  except ImportError as e:
    sys.exit(f"no LogReader ({e}); run from /data/openpilot")

  segs = find_segments(args.route)
  if len(segs) > args.max_segments:
    print(f"# {len(segs)} segments; reading the first {args.max_segments} (--max-segments to change)")
    segs = segs[:args.max_segments]

  clock = DriveClock()
  gap = 0
  gap_from_can = False
  v_ego = 0.0
  engaged = False
  pedal = False
  lead_d = 0.0
  lead_v = 0.0
  lead_ok = False

  steady: deque = deque()      # (ts,) while the closing rate has stayed low
  samples: dict[int, list[float]] = defaultdict(list)
  by_speed: dict[tuple[int, int], list[float]] = defaultdict(list)
  changes: list[tuple[float, int, int]] = []
  seen_gaps: dict[int, int] = defaultdict(int)

  for seg in segs:
    path = rlog(seg)
    if path is None:
      continue
    for msg in LogReader(path):
      w = msg.which()
      ts = clock.seconds(msg.logMonoTime)
      try:
        if w == "carStateBP":
          if msg.carStateBP.accGap:
            new = int(msg.carStateBP.accGap)
            if new != gap:
              changes.append((ts, gap, new))
            gap = new
          continue
        if w == "can" and not gap_from_can:
          # Only consulted while carStateBP has never reported a gap, so a route recorded before
          # that field existed still measures. Never mixed with it -- two sources for one value is
          # how they end up disagreeing.
          for c in msg.can:
            if c.address == ACCDATA_3_ADDR and len(c.dat) >= 5:
              new = c.dat[4] & 0x07
              if new != gap:
                changes.append((ts, gap, new))
              gap = new
          continue
        if w == "carState":
          cs = msg.carState
          v_ego = float(cs.vEgo)
          engaged = bool(cs.cruiseState.enabled)
          pedal = bool(cs.gasPressed or cs.brakePressed)
          continue
        if w == "radarState":
          ld = msg.radarState.leadOne
          lead_ok = bool(ld.status)
          lead_d = float(ld.dRel)
          lead_v = float(ld.vRel)
        else:
          continue
      except Exception:  # noqa: BLE001 -- a malformed frame is not worth losing the route over
        continue

      if gap:
        seen_gaps[gap] += 1

      # Steady following, or not. The deque holds the run of consecutive radar frames in which the
      # closing rate stayed small; the frame only counts once that run is STEADY_S long.
      ok = lead_ok and engaged and not pedal and v_ego >= MIN_SPEED_MS and abs(lead_v) <= MAX_CLOSING_MS
      if not ok:
        steady.clear()
        continue
      steady.append(ts)
      while steady and ts - steady[0] > 60.0:
        steady.popleft()
      if ts - steady[0] < STEADY_S:
        continue

      headway = lead_d / max(v_ego, 0.1)
      if headway > MAX_HEADWAY_S or not gap:
        continue
      samples[gap].append(headway)
      by_speed[(gap, int(v_ego * MS_TO_MPH) // 10 * 10)].append(headway)

  if changes:
    print("\n=== when the setting changed ===")
    for ts, old, new in changes[:40]:
      print(f"  t+{ts:7.0f}s  {old or '?'} -> {new}")
    if len(changes) > 40:
      print(f"  ... and {len(changes) - 40} more")
  print(f"\n# gap settings seen at all: {dict(sorted(seen_gaps.items())) or 'none'}")

  print("\n=== steady-following headway by gap setting ===")
  print("  gap   n      p25     median    p75     median distance @ 65 mph")
  if not samples:
    print("  nothing qualified. Either no steady following in this route, or the setting never")
    print("  changed -- one bucket answers nothing, the comparison between settings is the point.")
  for g in sorted(samples):
    vals = samples[g]
    med = pct(vals, 0.5)
    flag = "" if len(vals) >= MIN_SAMPLES else "   (too few to trust)"
    print(f"   {g}   {len(vals):5d}  {pct(vals, 0.25):5.2f}s  {med:5.2f}s  {pct(vals, 0.75):5.2f}s"
          f"     {med * 65 / MS_TO_MPH:5.0f} m{flag}")

  if len(samples) >= 2:
    print("\n=== does it hold across speeds? ===")
    print("  Ford may hold a constant TIME, a constant DISTANCE, or something in between. If the")
    print("  medians below drift with speed, it is not a pure time gap and 'seconds' is the wrong")
    print("  unit to plan a pass in.")
    speeds = sorted({s for _, s in by_speed})
    print("  gap  " + "".join(f"{s:>4d}+ " for s in speeds))
    for g in sorted(samples):
      row = "".join(f"{pct(by_speed[(g, s)], 0.5):5.2f} " if len(by_speed[(g, s)]) >= 20 else "   -- "
                    for s in speeds)
      print(f"   {g}   {row}")

  print("\n  Headway is dRel / vEgo while ACC is holding station: lead present, closing rate under")
  print(f"  {MAX_CLOSING_MS} m/s for {STEADY_S:.0f}s, above {MIN_SPEED_MS * MS_TO_MPH:.0f} mph, no pedal. Frames where the set speed")
  print("  binds are excluded on purpose -- they contain no information about the gap setting.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
