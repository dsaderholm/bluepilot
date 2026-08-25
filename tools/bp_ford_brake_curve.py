"""FusionPilot: at what deceleration does FORD reach for the friction brakes?

His idea, 2026-08-25: *"So we basically need to train OP long on Ford ACC?"* -- and the data for it
already exists. Every drive carries Ford's own ACCDATA on bus 2, which is a recording of Ford's
controller responding to real traffic. Nothing needs collecting.

THE FIRST QUESTION IS NOT A MODEL, IT IS ONE NUMBER. `longitudinal_ext.py` sets
`brake_actuate_target = -0.14 m/s^2`: openpilot asks the ABS for the pedal at fourteen HUNDREDTHS of
a g-tenth of deceleration. Ford's own measured propulsion range goes to -2.710, so the suspicion is
that Ford engine-brakes through a band where openpilot is already braking -- which is exactly his
standing complaint that op long "cannot coast".

This measures it directly rather than guessing. For every camera ACCDATA frame it reads what Ford
COMMANDED (`AccBrkTot_A_Rq`, its total accel request) against whether Ford ASSERTED the brake bits
(`AccBrkPrchg_B_Rq` precharge, `AccBrkDecel_B_Rq` decel). The crossover -- the deceleration at which
Ford starts asserting them -- is the threshold openpilot should be using, taken from his car instead
of from a constant nobody sourced.

READ THE HISTOGRAM, NOT THE MEAN. If Ford's brake assertion is a sharp step, the threshold is that
step. If it is a gradual ramp, Ford is blending and a single threshold is the wrong model -- which is
itself worth knowing before anyone tunes anything.

    python tools/bp_ford_brake_curve.py 000003b5 000003b7 000003bb
"""
import os
import sys
from collections import defaultdict

REALDATA = os.environ.get("REALDATA", "/data/media/0/realdata")
sys.path.insert(0, "/data/openpilot")
from openpilot.tools.lib.logreader import LogReader  # noqa: E402

ACCDATA = 390
OUR_BRAKE_TARGET = -0.14      # longitudinal_ext.py brake_actuate_target
OUR_MIN_GAS = -0.5            # CarControllerParams.MIN_GAS

# 0.1 m/s^2 buckets over the range that matters
BUCKET = 0.1
LO, HI = -3.5, 0.5


def be(data, start, nbits):
  v = int.from_bytes(data, "big")
  idx = (start // 8) * 8 + (7 - (start % 8))
  return (v >> (len(data) * 8 - idx - nbits)) & ((1 << nbits) - 1)


def seg_index(n):
  try:
    return int(n.rsplit("--", 1)[1])
  except Exception:
    return -1


def main(routes):
  total = defaultdict(int)      # bucket -> frames
  braking = defaultdict(int)    # bucket -> frames with a brake bit asserted
  gas_below_our_floor = 0
  gas_frames = 0
  frames = 0

  for route in routes:
    segs = sorted([d for d in os.listdir(REALDATA) if d.startswith(route)], key=seg_index)
    for s in segs:
      p = os.path.join(REALDATA, s, "rlog")
      if not os.path.exists(p):
        p += ".zst"
      if not os.path.exists(p):
        continue
      try:
        lr = LogReader(p)
      except Exception:
        continue
      for m in lr:
        if m.which() != "can":
          continue
        for c in m.can:
          if c.address != ACCDATA or c.src != 2 or len(c.dat) != 8:
            continue
          # Only frames where Ford's ACC is actually running: cancel clear, Cmbb enabled.
          if be(c.dat, 39, 1) or not be(c.dat, 50, 1):
            continue
          frames += 1
          accel = be(c.dat, 4, 13) * 0.0039 - 20
          gas = be(c.dat, 49, 10) * 0.01 - 5
          prchg = be(c.dat, 54, 1)
          decel = be(c.dat, 55, 1)

          if abs(gas + 5.0) >= 0.005:          # not the inactive sentinel
            gas_frames += 1
            if gas < OUR_MIN_GAS:
              gas_below_our_floor += 1

          if LO <= accel <= HI:
            b = round(accel / BUCKET) * BUCKET
            total[b] += 1
            braking[b] += bool(prchg or decel)

  if not frames:
    print("no Ford ACCDATA frames found"); return

  print(f"\n{frames} frames of Ford ACC actually running, across {len(routes)} route(s)\n")
  print(f"{'Ford commanded':>16}  {'frames':>8}  {'Ford used brakes':>17}   {'ours would':>10}")
  print("-" * 62)
  crossover = None
  for b in sorted(total, reverse=True):
    n = total[b]
    if n < 25:
      continue
    pct = 100.0 * braking[b] / n
    ours = "BRAKE" if b < OUR_BRAKE_TARGET else "coast"
    if crossover is None and pct >= 50.0:
      crossover = b
    print(f"{b:>13.1f}    {n:>8}  {pct:>16.1f}%   {ours:>10}")

  print("-" * 62)
  print(f"  ours reaches for the pedal at   {OUR_BRAKE_TARGET:+.2f} m/s^2")
  if crossover is not None:
    print(f"  FORD crosses 50% brake usage at {crossover:+.2f} m/s^2")
    print(f"  -> openpilot brakes {abs(crossover - OUR_BRAKE_TARGET):.2f} m/s^2 EARLIER than Ford")
  else:
    print("  Ford never crossed 50% brake usage in any populated bucket")

  if gas_frames:
    print(f"\n  Ford's propulsion request was below our MIN_GAS ({OUR_MIN_GAS}) on "
          f"{gas_below_our_floor}/{gas_frames} frames ({100.0 * gas_below_our_floor / gas_frames:.2f}%)"
          "\n  -- that is engine braking openpilot cannot ask for at all.")


main(sys.argv[1:])
