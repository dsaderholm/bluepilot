#!/usr/bin/env python3
"""FusionPilot: what each ACC gap setting actually buys, measured on this car.

THE QUESTION. Ford's ACC brakes far less while the blinker is on -- 63% vs 12.8% when closing at
6+ mph, measured across 92,000 frames. Passing assist cannot use that: it commands the blinker over
CAN, which contends with the gateway and probably does not read as signalling to whatever does
overtake awareness. So the plan is to move the distance at which ACC decides instead, by dropping
the time gap for the duration of an approach.

Which needs a number nobody has: how much later does gap 1 brake than gap 3?

NOT A SECOND GAP EXTRACTOR. `tools/bp_gap_seconds.py` answers what each setting IS in seconds; this
answers how often ACC brakes at each setting, bucketed by closing rate. Different questions, and the
second one is what decides whether a closer gap buys enough room to pass.

The setting comes from `carStateBP.accGap`, which ICBM added for exactly this. Raw CAN is kept only
as a FALLBACK for routes recorded before that field existed -- without it every drive up to
2026-08-15 is unreadable here, and those are the entire gap-3 baseline to compare against.

  python tools/bp_gap_braking.py                 # every route on the device
  python tools/bp_gap_braking.py 00000370        # one route

Run it on the device, where the routes are.
"""
from __future__ import annotations

import collections
import os
import sys

MS_TO_MPH = 2.23694
ACCDATA_3 = 394

# Only frames where a pass would be under consideration at all. Matches the ICBM query so the two
# are comparable: engaged, a real lead inside 80 m, and above the speed passing assist works at.
MAX_LEAD_M = 80.0
MIN_SPEED_MPH = 18.0

# Closing-rate buckets, mph. Negative vRel is closing. The first bucket is the passing approach and
# the only one where the 4.4x finding was large enough to change a design.
BUCKETS = [(-1e9, -6.0, "closing fast <=-6"), (-6.0, -2.0, "closing -6..-2"),
           (-2.0, 2.0, "steady -2..+2"), (2.0, 1e9, "opening >+2")]


def bucket_of(v_rel_mph: float) -> str:
  for lo, hi, name in BUCKETS:
    if lo <= v_rel_mph < hi:
      return name
  return BUCKETS[-1][2]


def segments(route: str | None):
  rd = "/data/media/0/realdata"
  for d in sorted(os.listdir(rd)):
    if "--" not in d or d.startswith("boot"):
      continue
    if route and not d.startswith(route):
      continue
    p = os.path.join(rd, d)
    logs = [f for f in os.listdir(p) if f.startswith("rlog")]
    if logs:
      yield d, os.path.join(p, logs[0])


def main() -> int:
  sys.path.insert(0, "/data/openpilot")
  from openpilot.tools.lib.logreader import LogReader

  route = sys.argv[1] if len(sys.argv) > 1 else None
  # (gap, bucket) -> [frames, braking frames]
  tally: dict[tuple[int, str], list[int]] = collections.defaultdict(lambda: [0, 0])
  gap_seen = collections.Counter()
  read = 0

  for name, path in segments(route):
    gap = None
    braking = None
    v_ego = None
    lead_d = lead_v = None
    engaged = False
    try:
      for msg in LogReader(path):
        w = msg.which()
        if w == "carStateBP":
          try:
            if msg.carStateBP.accGap:
              gap = int(msg.carStateBP.accGap)
              gap_seen[gap] += 1
          except Exception:
            pass
          try:
            braking = bool(msg.carStateBP.brakeLightStatus.accDecelRequest)
          except Exception:
            braking = None
        elif w == "can" and gap is None:
          # Only until carStateBP carries it. See the note above -- bits 34|3 big-endian is byte 4
          # masked to 0x07, verified against six segments reading 3 on 2026-08-14.
          for c in msg.can:
            if c.address == ACCDATA_3:
              gap = c.dat[4] & 0x07
              gap_seen[gap] += 1
        elif w == "carState":
          v_ego = msg.carState.vEgo * MS_TO_MPH
        elif w == "selfdriveState":
          engaged = bool(msg.selfdriveState.enabled)
        elif w == "radarState":
          lead = msg.radarState.leadOne
          lead_d = float(lead.dRel) if lead.status else None
          lead_v = float(lead.vRel) * MS_TO_MPH if lead.status else None

          # Sample on radarState, the slowest of the inputs, so one frame is one observation
          # rather than four of the same moment.
          if (gap and braking is not None and engaged and v_ego and v_ego >= MIN_SPEED_MPH
              and lead_d is not None and lead_d <= MAX_LEAD_M):
            t = tally[(gap, bucket_of(lead_v))]
            t[0] += 1
            t[1] += int(braking)
      read += 1
    except Exception as e:
      print(f"  {name}: {type(e).__name__}")

  print(f"segments read: {read}")
  print(f"gap settings seen: {dict(sorted(gap_seen.items()))}\n")
  if not tally:
    print("no frames matched -- engaged, lead inside 80 m, above 18 mph")
    return 0

  print(f"{'gap':>4} {'closing bucket':<20} {'frames':>8} {'ACC braking':>12}")
  for (gap, name) in sorted(tally, key=lambda k: (k[0], [b[2] for b in BUCKETS].index(k[1]))):
    frames, brakes = tally[(gap, name)]
    if frames < 50:
      continue  # too few to mean anything; printing them invites reading noise as signal
    print(f"{gap:>4} {name:<20} {frames:>8} {brakes / frames * 100:>11.1f}%")
  print("\nBuckets under 50 frames are omitted rather than shown as percentages.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
