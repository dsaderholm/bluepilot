#!/usr/bin/env python3
"""FusionPilot: how much harder does he accelerate through a pass than Ford's ACC ever does?

THE SENTENCE THIS EXISTS TO TURN INTO A NUMBER, 2026-08-25:

    "Today I was behind some very slow drivers that I wanted to get around fast, so cruise was off,
     if you know what I mean."

He was told he passes with cruise off 40% of the time and answered that it is deliberate. That
retires the reading that it is a habit: **he disengages because of what the system does, in exactly
the situation passing assist exists for.** It also confirms, from the seat, the cost that
BLINKER-ACC-SUPPRESSION predicted and nobody had measured -- "the first half of a commanded pass is
slower than the same pass made by hand".

    python tools/bp_pass_accel.py <route-prefix> [<route-prefix> ...]

WHAT IT COMPARES, and both sides come from his own driving so neither is a specification:

  HIS PASS       aEgo while he is passing with cruise OFF -- left stalk up, moving, lead ahead,
                 openpilot longitudinal not engaged. What he chooses when nothing is in the way.
  FORD'S ACC     aEgo while ACC is driving and ASKING FOR ACCELERATION. Restricted to frames where
                 the car is below its set speed and not braking, because ACC holding a steady speed
                 is not a sample of what it can do -- including those would drag the comparison to
                 zero and prove nothing.

**THE DIFFERENCE IS WHAT A COMMANDED PASS COSTS HIM.** If Ford's best acceleration is close to what
he uses, the slow-pass concern is smaller than feared and the gap button is the whole remaining
lever. If it is far below, then under ICBM -- where longitudinal authority is Ford's and passing
assist can only ask for a gap and a set speed -- the feature cannot make the pass he wants, and that
is a design fact rather than a tuning one.

WHY aEgo AND NOT THE COMMANDED ACCEL: what matters is what the car DID, and on the ACC side the
commanded value is Ford's, not ours. aEgo is the same measurement on both sides, which is the only
way the two numbers mean anything next to each other.

HONEST LIMITS, stated because this comparison is easy to over-read:

  * a manual pass is not always flat-out. He accelerates as hard as he wants to, which is a floor on
    his capability and not a measurement of it.
  * ACC frames below set speed include gentle catch-up, so its distribution is diluted by cases
    where it had no reason to hurry. The MAX row is the fairer comparison for capability; the p50
    rows say what each typically does.
  * this needs no new drive, and no drive was made for it.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

REALDATA = "/data/media/0/realdata"

# How long after the stalk goes up still counts as "the pass". Long enough to cover the pull-out and
# the run past, short enough not to swallow ordinary cruising afterwards.
PASS_WINDOW_S = 12.0


def q(v, f):
  if not v:
    return float("nan")
  v = sorted(v)
  return v[min(len(v) - 1, int(f * len(v)))]


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("route", nargs="+")
  ap.add_argument("--segments", type=int, default=0)
  args = ap.parse_args()

  from openpilot.tools.lib.logreader import LogReader

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

  his = []          # aEgo during his cruise-off passes
  acc = []          # aEgo while ACC is driving and wants to accelerate
  passes = 0
  prev_blinker = False
  since_stalk = 1e3

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
      if m.which() != "carState":
        continue
      cs = m.carState
      try:
        v = float(cs.vEgo)
        a = float(cs.aEgo)
        blinker = bool(cs.leftBlinker)
        enabled = bool(cs.cruiseState.enabled)
        set_v = float(cs.cruiseState.speedCluster)
        brake = bool(cs.brakePressed)
      except Exception:  # noqa: BLE001
        continue
      if v < 10.0:
        prev_blinker = blinker
        continue

      since_stalk += 0.01
      if blinker and not prev_blinker:
        since_stalk = 0.0
        if not enabled:
          passes += 1
      prev_blinker = blinker

      # HIS SIDE: inside the window after a cruise-off left stalk, and accelerating.
      if since_stalk <= PASS_WINDOW_S and not enabled and not brake and a > 0.0:
        his.append(a)

      # FORD'S SIDE: ACC engaged, below its own set speed, not braking, actually accelerating.
      # Below set speed is the load-bearing part -- ACC holding a steady speed is not a sample of
      # what it is willing to do, and including those frames drags the whole distribution to zero.
      if enabled and not brake and a > 0.0 and set_v > 0.0 and v < set_v - 1.0:
        acc.append(a)

  if not his or not acc:
    sys.exit(f"not enough samples: his={len(his)} acc={len(acc)} "
             f"(cruise-off left-stalk passes found: {passes})")

  print(f"routes {args.route}")
  print(f"  cruise-off left-stalk passes: {passes}\n")
  print(f"  {'aEgo, m/s^2':<26} {'n':>8} {'p50':>7} {'p90':>7} {'p99':>7} {'MAX':>7}")
  print(f"  {'HIS pass, cruise off':<26} {len(his):8d} {q(his,.5):7.2f} {q(his,.9):7.2f} "
        f"{q(his,.99):7.2f} {max(his):7.2f}")
  print(f"  {'FORD ACC, below set speed':<26} {len(acc):8d} {q(acc,.5):7.2f} {q(acc,.9):7.2f} "
        f"{q(acc,.99):7.2f} {max(acc):7.2f}")
  print()
  print(f"  MAX ratio, his / Ford's: {max(his) / max(acc):.2f}x")
  print(f"  p90 ratio:               {q(his,.9) / q(acc,.9):.2f}x" if q(acc, .9) > 0 else "")
  print()
  print("  READ THE MAX ROW FOR CAPABILITY and the p50 rows for what each typically does. If Ford's")
  print("  best is close to his, the slow-pass cost is smaller than feared and the follow-gap button")
  print("  is the remaining lever. If it is far below, then under ICBM -- where the longitudinal")
  print("  authority is Ford's and passing assist can only ask for a gap and a set speed -- the")
  print("  feature cannot make the pass he wants, and that is a design fact, not a tuning one.")
  print()
  print("  HIS NUMBER IS A FLOOR ON WHAT HE CAN DO, not a measurement of it: he accelerates as hard")
  print("  as he wanted to on the day, which is not necessarily as hard as the car will go.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
