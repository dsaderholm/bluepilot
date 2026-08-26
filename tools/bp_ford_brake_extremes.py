#!/usr/bin/env python3
"""FusionPilot: how hard does Ford ACC ACTUALLY brake, when it is actually driving?

He asked the question that stopped a safety limit being widened on bad evidence: *"How many drives
have you measured?"* The answer was four, capped at ten segments each -- and checking that turned up
a straight contradiction between two numbers in this fork's own notes:

    2026-08-17   6 routes, 189,418 braking frames   hardest Ford ever commanded   -2.70 m/s^2
    2026-08-26   4 routes, 113,142 frames           hardest Ford asked for        -4.63 m/s^2

`bp_accdata_bands.py` produced the second, and its decode is CORRECT -- `AccBrkTot_A_Rq` is
`4|13@0+`, so the low 5 bits of byte 0 then byte 1, which is exactly what it does. The value really
is on the wire. What that tool does NOT do is filter: it exists to answer "would panda drop this
frame if we forwarded it", so it reads every camera frame including the ones where ACC is not
running and the field means nothing.

**That is the denominator error this fork has recorded three times**, and the rule it produced is
the one being applied here: restrict to the frames where the feature is LIVE before reading any
number into it.

WHAT THIS PRINTS, restricted to frames where Ford's ACC is genuinely driving:

    cruise ENGAGED, car MOVING, driver's foot OFF the brake, camera not asserting cancel

and reports the extremes with their percentiles, so a single outlier frame cannot set the envelope
on its own -- a max is one sample and p99.9 is a distribution.

    python tools/bp_ford_brake_extremes.py --route 000003c0--d46d098434 --route 000003c2--ef1505443d
"""
from __future__ import annotations

import argparse
import os
import sys

REALDATA = "/data/media/0/realdata"
ACCDATA = 390
MS_TO_MPH = 2.23694

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


def decode(dat: bytes):
  """Exactly panda's arithmetic, and exactly bp_accdata_bands'. Same decode, different FILTER --
  that is the whole point of this tool, so the two must not diverge here."""
  accel = ((dat[0] & 0x1F) << 8) | dat[1]
  gas = ((dat[6] & 0x3) << 8) | dat[7]
  # AccCancl_B_Rq is `39|1@0+`. DBC bit N lives at byte N//8, shift N%8 -- so byte 4, shift 7.
  # Written as `>> 0` first, which reads a completely different bit. Checked against a known
  # neighbour rather than re-derived: CmbbDeny_B_Actl is `37|1@0+` and bp_accdata_bands reads it as
  # `(dat[4] >> 5) & 1`, which fixes the convention beyond argument.
  cancel = (dat[4] >> 7) & 1
  return accel * 0.0039 - 20.0, gas * 0.01 - 5.0, cancel


def pct(vals, q):
  if not vals:
    return float("nan")
  s = sorted(vals)
  i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
  return s[i]


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--route", action="append", default=None)
  ap.add_argument("--max-segments", type=int, default=60)
  args = ap.parse_args()

  entries = sorted((d for d in os.listdir(REALDATA) if "--" in d), key=seg_index)
  routes = args.route or [entries[-1].rsplit("--", 1)[0]]
  segs = []
  for r in routes:
    segs += [d for d in entries if d.startswith(r + "--")][:args.max_segments]
  if not segs:
    sys.exit(f"no segments for {routes}")
  print(f"# {len(routes)} route(s), {len(segs)} segments -- NO --max-segments cap unless asked\n")

  st = {"enab": False, "v": 0.0, "brake": False}
  live_accel, live_gas = [], []
  all_accel = []
  n_all = n_live = 0

  for seg in segs:
    if is_onroad():
      print(f"\n!!! THE CAR STARTED DRIVING at {seg}. STOPPING -- results are PARTIAL.\n")
      break
    lr = _open(seg)
    if lr is None:
      continue
    for msg in lr:
      w = msg.which()
      try:
        if w == "carState":
          cs = msg.carState
          st["enab"] = bool(cs.cruiseState.enabled)
          st["v"] = cs.vEgo * MS_TO_MPH
          st["brake"] = bool(cs.brakePressed)
          continue
        if w != "can":
          continue
        for c in msg.can:
          if c.address != ACCDATA or len(c.dat) != 8 or int(c.src) != 2:
            continue
          a, g, cancel = decode(bytes(c.dat))
          n_all += 1
          all_accel.append(a)
          # THE FILTER. Every clause earns its place:
          #   enabled  -- ACC not running means the field is idle, not a command
          #   moving   -- a parked car's ACCDATA says nothing about braking
          #   no pedal -- the driver braking is not Ford deciding to
          #   no cancel-- the camera declining to run is not a command either
          if st["enab"] and st["v"] > 2.0 and not st["brake"] and not cancel:
            n_live += 1
            live_accel.append(a)
            live_gas.append(g)
      except Exception:  # noqa: BLE001
        continue

  print(f"ALL camera ACCDATA frames        {n_all:>8}")
  print(f"Ford ACC ACTUALLY DRIVING        {n_live:>8}   ({100.0 * n_live / max(n_all, 1):.1f}%)\n")

  print("=== AccBrkTot_A_Rq, Ford's total acceleration request ===")
  print(f"  {'':<26} {'min':>9} {'p00.1':>9} {'p01':>9} {'p99':>9} {'p99.9':>9} {'max':>9}")
  for name, vals in (("ALL frames (unfiltered)", all_accel), ("ONLY WHILE DRIVING", live_accel)):
    if not vals:
      print(f"  {name:<26} (none)")
      continue
    print(f"  {name:<26} {min(vals):9.2f} {pct(vals, 0.001):9.2f} {pct(vals, 0.01):9.2f}"
          f" {pct(vals, 0.99):9.2f} {pct(vals, 0.999):9.2f} {max(vals):9.2f}")

  if live_gas:
    print("\n=== AccPrpl_A_Rq, propulsion, WHILE DRIVING ===")
    real = [g for g in live_gas if abs(g + 5.0) > 0.005]     # exclude the -5.0 sentinel
    if real:
      print(f"  {'':<26} {'min':>9} {'p01':>9} {'p99':>9} {'p99.9':>9} {'max':>9}")
      print(f"  {'real requests':<26} {min(real):9.2f} {pct(real, 0.01):9.2f}"
            f" {pct(real, 0.99):9.2f} {pct(real, 0.999):9.2f} {max(real):9.2f}")
      print(f"  sentinel frames excluded: {len(live_gas) - len(real)}")

  print()
  print("  A MAX IS ONE SAMPLE. If max and p99.9 are far apart, the extreme is an outlier and an")
  print("  envelope built on it is built on noise -- widen to the percentile, not to the max.")
  print("  And the gap between the two ROWS above is the whole reason this tool exists: the")
  print("  unfiltered row is what bp_accdata_bands reports, and it is the wrong question for this.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
