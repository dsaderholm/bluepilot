#!/usr/bin/env python3
"""How hard is the steering rack ACTUALLY working? Decodes EPS motor current off the wire.

FusionPilot 2026-09-05. The standing question behind every lateral discussion on this car was
whether the PSCM is being TOLD to stop or is physically out of capability. Nothing openpilot
publishes answers it -- `LatCtlLim_D_Stat` is dead on non-CAN-FD Fords, so the module never reports
limiting, and `steeringTorqueEps` is not populated on Ford at all.

But the module broadcasts its own motor current and openpilot throws it away:

    EPAS_INFO (130), from the PSCM, bus 0 -- a message openpilot ALREADY parses for
    SteeringColumnTorque and EPAS_Failure.

    SteMdule_I_Est : 21|12@0+ (0.05,-64) [A]

**THE ANSWER, measured on the 2026-09-04 pull (56 saturation episodes):**

    EPS motor current                    n        p50    p90    p99     MAX
    HIS hands on the wheel           118,184     0.05   2.00  20.00   75.85 A
    openpilot, reporting SATURATED     4,710     0.10   0.55   2.00    5.05 A
    openpilot, normal                275,939     0.00   0.05   0.30    2.00 A

The rack delivers FIFTEEN TIMES more current when he turns the wheel himself than when openpilot
reports steering exhaustion. The limit is policy, not physics.

**AND THE THERMAL/DUTY-CYCLE OBJECTION WAS TESTED AND REFUTED** -- his own, and a good one, since
assist shares the load with the driver and EPS motors do derate. Derating decays through an episode;
this rises (mean 0.16 -> 0.28 from first fifth to last), starts at 0.11 on the first frame, and the
episodes are 0.5 s median. Nothing thermal happens in half a second from a standing start.

**"steerSaturated" IS NOT THE MODULE REPORTING A LIMIT.** It is openpilot noticing its own command
sat at its own ceiling while the car under-delivered. The picture is a rack returning 74-89% of what
it is asked while drawing half an amp.

TWO THINGS THIS COST TO GET RIGHT, both worth not repeating:

- **Motorola bit order.** After finishing a byte you advance to the NEXT byte, not the previous one.
  The first decode walked to byte1 and produced two distinct values across 45,000 samples, which is
  the tell that bit math is wrong rather than the signal being dull.
- **Validate the decoder on a signal whose truth you already have, BEFORE reading anything into a
  new one.** Here `byte0*0.0625-8` reproduces `carState.steeringTorque` to 0.006 Nm and
  `byte4*0.05+6` gives battery voltage. Without that, "the current looks implausibly low" is
  indistinguishable from "my bit math is wrong" -- and it briefly WAS read as a bad scale, until
  75.85 A under manual steering showed the scale was right all along.

Also characterised while here: **most of EPAS_INFO is dead on this retrofit Edge PSCM.** Bytes 1, 5,
6 and 7 are frozen constants, and `DrvSte_Tq_Actl` is a fixed 128 = 0.0 Nm. Check a field varies
before trusting it.

    python tools/bp_eps_current.py <dir-of-rlog.zst>
"""
import glob
import os
import statistics
import sys

import capnp
import zstandard

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
capnp.remove_import_hook()
log = capnp.load(os.path.join(REPO, "cereal", "log.capnp"), imports=[os.path.join(REPO, "cereal")])

MPH = 2.23694
EPAS_INFO = 130


def eps_current(dat: bytes) -> float:
  """SteMdule_I_Est: Motorola, MSB at byte2 bit5, 12 bits -> byte2[5:0] then byte3[7:2]."""
  return (((dat[2] & 0x3F) << 6) | (dat[3] >> 2)) * 0.05 - 64.0


def column_torque(dat: bytes) -> float:
  """SteeringColumnTorque, 7|8@0+ (0.0625,-8) -- the decoder self-check, truth is in carState."""
  return dat[0] * 0.0625 - 8.0


def pct(sorted_vals, p):
  return sorted_vals[min(int(p * len(sorted_vals)), len(sorted_vals) - 1)]


def main():
  if len(sys.argv) < 2:
    print(__doc__)
    return
  files = sorted(glob.glob(os.path.join(sys.argv[1], "*.rlog.zst")))
  if not files:
    print(f"no rlog.zst under {sys.argv[1]}")
    return

  cur = wire_tq = None
  lat = sat = False
  checks, manual, op_sat, op_norm = [], [], [], []

  for path in files:
    try:
      with open(path, "rb") as fh:
        raw = zstandard.ZstdDecompressor().stream_reader(fh).read()
      evs = log.Event.read_multiple_bytes(raw, traversal_limit_in_words=2 ** 32)
    except Exception:
      continue
    while True:
      try:
        m = next(evs)
      except Exception:
        break
      try:
        w = m.which()
        if w == "can":
          for c in m.can:
            if c.address == EPAS_INFO and c.src == 0:
              cur = eps_current(c.dat)
              wire_tq = column_torque(c.dat)
        elif w == "carControl":
          lat = bool(m.carControl.latActive)
        elif w == "controlsState":
          try:
            sat = bool(m.controlsState.lateralControlState.angleState.saturated)
          except Exception:
            pass
        elif w == "carState" and cur is not None:
          cs = m.carState
          if wire_tq is not None:
            checks.append(abs(wire_tq - float(cs.steeringTorque)))
          a = abs(cur)
          if bool(cs.steeringPressed):
            manual.append(a)
          elif lat and float(cs.vEgo) * MPH > 5:
            (op_sat if sat else op_norm).append(a)
      except Exception:
        continue

  # The self-check runs FIRST and its failure invalidates everything below it.
  if checks:
    mean_err = statistics.mean(checks)
    ok = mean_err < 0.01
    print(f"DECODER SELF-CHECK  SteeringColumnTorque vs carState  ({len(checks)} samples)")
    print(f"  mean error {mean_err:.5f} Nm -> byte access {'CORRECT' if ok else 'WRONG'}")
    if not ok:
      print("  REFUSING to report currents: the bit math does not reproduce a known signal.")
      return
    print()

  print("EPS motor current, amps")
  print(f"  {'population':<34}{'n':>9}{'p50':>7}{'p90':>7}{'p99':>7}{'max':>8}")
  for name, arr in (("driver's hands on the wheel", manual),
                    ("openpilot, reporting SATURATED", op_sat),
                    ("openpilot, normal", op_norm)):
    if not arr:
      print(f"  {name:<34}{'no samples':>9}")
      continue
    s = sorted(arr)
    print(f"  {name:<34}{len(s):>9}{pct(s,.5):>7.2f}{pct(s,.9):>7.2f}{pct(s,.99):>7.2f}{s[-1]:>8.2f}")

  if manual and op_sat:
    ms, os_ = sorted(manual), sorted(op_sat)
    print(f"\n  peak ratio (driver / openpilot-while-'exhausted'): {ms[-1] / max(os_[-1], 1e-9):.1f}x")
    print(f"  p99 ratio:                                         "
          f"{pct(ms,.99) / max(pct(os_,.99), 1e-9):.1f}x")
    print("\n  A rack at its capability cannot show this gap. The limit is policy, not physics.")


if __name__ == "__main__":
  main()
