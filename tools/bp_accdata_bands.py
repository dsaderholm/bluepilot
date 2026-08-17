#!/usr/bin/env python3
"""FusionPilot: would panda actually let Ford's own ACCDATA through?

THIS IS THE MEASUREMENT THE PASSTHROUGH WAS BUILT WITHOUT. Before writing that feature the brake
cap was checked -- 189,418 frames, Ford never exceeded -2.70 m/s^2 against a -3.4991 limit, so the
cap never binds. That number is real and it is also the WRONG signal to have checked alone.

`FORD_LONG_LIMITS` in opendbc/safety/modes/ford.h has three bands, not one:

    AccBrkTot_A_Rq   [-3.4991, 1.9999]                 <- the one that was measured
    AccPrpl_A_Rq     [-0.5, 2.0]  or exactly -5.0      <- never looked at
    AccPrpl_A_Pred   [-0.5, 2.0]  or exactly -5.0      <- never looked at

The gas band is four times narrower and it sits exactly where a coasting or engine-braking Ford
lives. And `CmbbDeny_B_Actl` is a fourth way out: `violation |= cmbb_deny` is unconditional there.

WHY A VIOLATION IS WORSE THAN IT SOUNDS: `ford_tx_hook` does not clamp, it returns false, and the
whole 8-byte message is dropped. So an inadmissible frame does not produce a slightly-wrong
command -- it makes a 50 Hz message stop for as long as Ford holds that value and then resume.
Intermittent absence is worse than either controller driving.

The code now refuses to forward an inadmissible frame and falls back to openpilot's own ACCDATA
(`fordcan_ext.passthrough_admissible`), so nothing here is load-bearing for safety. What it answers
is whether the passthrough is USEFUL: a refusal rate near zero means Ford drives essentially all the
time, and a high one means the feature hands back to openpilot at exactly the moments Ford was
doing something interesting -- which would make it a worse idea than it looks.

Run it on drives already recorded. Every ACCDATA frame on the camera bus is in the logs whether or
not op long was ever enabled, so this needs no new drive.

USAGE, on the device:

    cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 tools/bp_accdata_bands.py
    python tools/bp_accdata_bands.py --routes 6
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict

REALDATA = "/data/media/0/realdata"

# Straight out of ford.h, converted to engineering units by the DBC's own scaling.
ACCEL_MIN, ACCEL_MAX = -3.4991, 1.9999
GAS_MIN, GAS_MAX, GAS_INACTIVE = -0.5, 2.0, -5.0

ACCDATA_ADDR = 0x186
CAM_BUS = 2


def seg_index(name: str) -> int:
  tail = name.rsplit("--", 1)[-1]
  return int(tail) if tail.isdigit() else -1


def newest_routes(count: int):
  if not os.path.isdir(REALDATA):
    sys.exit(f"no {REALDATA} -- run this on the device")
  routes: dict[str, list[str]] = defaultdict(list)
  for d in os.listdir(REALDATA):
    if "--" in d and seg_index(d) >= 0:
      routes[d.rsplit("--", 1)[0]].append(d)
  if not routes:
    sys.exit("no route segments")

  def when(r: str) -> float:
    return max(os.path.getmtime(os.path.join(REALDATA, d)) for d in routes[r])
  return sorted(routes, key=when)[-count:], routes


def decode(dat: bytes) -> tuple[float, float, float, int]:
  """The four fields panda checks, extracted exactly the way ford_tx_hook extracts them.

  Deliberately NOT via the DBC. panda reads raw bytes and bit offsets, and the whole question here
  is what PANDA would decide -- so reproducing its arithmetic is the point, and a DBC that drifted
  from it would hide the very disagreement worth finding.
  """
  gas = ((dat[6] & 0x3) << 8) | dat[7]
  gas_pred = ((dat[2] & 0x3) << 8) | dat[3]
  accel = ((dat[0] & 0x1F) << 8) | dat[1]
  cmbb_deny = (dat[4] >> 5) & 1
  # DBC scaling: AccBrkTot_A_Rq is 0.0039 with offset -20; AccPrpl_* is 0.01 with offset -5.
  return accel * 0.0039 - 20.0, gas * 0.01 - 5.0, gas_pred * 0.01 - 5.0, cmbb_deny


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--routes", type=int, default=4)
  ap.add_argument("--max-segments", type=int, default=12)
  args = ap.parse_args()

  try:
    from openpilot.tools.lib.logreader import LogReader
  except ImportError as e:
    sys.exit(f"no LogReader ({e}); see the docstring for the interpreter to use")

  picked, routes = newest_routes(args.routes)
  print(f"# routes: {', '.join(picked)}")

  total = 0
  refused: Counter = Counter()
  worst_accel = 0.0
  worst_gas = (0.0, 0.0)
  gas_hist: Counter = Counter()

  for route in picked:
    for seg in sorted(routes[route], key=seg_index)[:args.max_segments]:
      path = os.path.join(REALDATA, seg, "rlog")
      if not os.path.exists(path):
        path += ".zst"
      if not os.path.exists(path):
        continue
      try:
        lr = LogReader(path)
      except Exception:  # noqa: BLE001
        continue

      for msg in lr:
        try:
          if msg.which() != "can":
            continue
          for c in msg.can:
            if c.address != ACCDATA_ADDR or c.src != CAM_BUS:
              continue
            accel, gas, gas_pred, cmbb_deny = decode(bytes(c.dat))
            total += 1

            if accel < worst_accel:
              worst_accel = accel
            # Bucket the gas request so the shape of the distribution is visible, not just the
            # verdict -- if it clusters just outside the band that is a different story from a
            # rare excursion.
            gas_hist[round(gas, 1)] += 1

            why = ""
            if cmbb_deny:
              why = "CmbbDeny_B_Actl"
            elif not ACCEL_MIN <= accel <= ACCEL_MAX:
              why = "AccBrkTot_A_Rq"
            elif not (abs(gas - GAS_INACTIVE) < 0.005 or GAS_MIN <= gas <= GAS_MAX):
              why = "AccPrpl_A_Rq"
              if abs(gas) > abs(worst_gas[0]):
                worst_gas = (gas, gas_pred)
            elif not (abs(gas_pred - GAS_INACTIVE) < 0.005 or GAS_MIN <= gas_pred <= GAS_MAX):
              why = "AccPrpl_A_Pred"
            if why:
              refused[why] += 1
        except Exception:  # noqa: BLE001
          continue

  if not total:
    print("no ACCDATA frames on the camera bus. Wrong bus, or these routes predate the logging.")
    return 0

  bad = sum(refused.values())
  print(f"\n=== {total:,} camera ACCDATA frames ===")
  print(f"  panda would have DROPPED  {bad:,}  ({100.0 * bad / total:.2f}%)")
  for why, n in refused.most_common():
    print(f"    {why:<18} {n:>8,}  {100.0 * n / total:5.2f}%")
  if not bad:
    print("    nothing. Ford's own command is admissible on every frame in these drives.")

  print(f"\n  hardest brake Ford asked for: {worst_accel:.2f} m/s^2  (panda stops at {ACCEL_MIN})")
  if worst_gas[0]:
    print(f"  worst out-of-band gas request: {worst_gas[0]:.2f} m/s^2 (pred {worst_gas[1]:.2f})")

  print(f"\n  AccPrpl_A_Rq distribution (band is [{GAS_MIN}, {GAS_MAX}] plus exactly {GAS_INACTIVE}):")
  for value, n in sorted(gas_hist.items())[:6]:
    print(f"    {value:6.1f}  {n:>8,}")
  print("    ...")
  for value, n in sorted(gas_hist.items())[-6:]:
    print(f"    {value:6.1f}  {n:>8,}")

  print("\n  READ IT THIS WAY: the code already falls back to openpilot's own ACCDATA on a refused")
  print("  frame, so a nonzero number here is not a safety problem. It is a USEFULNESS problem --")
  print("  every refused frame is one where the passthrough hands the car back to the controller")
  print("  the whole feature exists to avoid, and a high rate would mean this is a worse idea than")
  print("  it looks rather than a broken one.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
