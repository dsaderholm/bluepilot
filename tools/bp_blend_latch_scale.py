"""How big the exit-blend latch can be, in degrees at the wheel, and how long it has to hold.

**RUN THIS BEFORE MOVING `_EXIT_LATCH_CALLS`, AND BEFORE CLAIMING THE LATCH DOES ANYTHING.** Both
numbers in that constant's comment come from here.

The latch changes exactly one thing: `b_blend`. Since `requested = predicted*b + desired*(1-b)`, a
change of `db` moves the command by precisely `db * (predicted - desired)` and nothing else. So the
question "is this perceptible" reduces to measuring that product -- which is the rule this repo keeps
having to relearn: **convert to degrees at the wheel BEFORE reporting that a change does anything.**
The ba20937aac cherry-pick was a correct fix to this same blend that moved the wheel 0.03 deg and
was invisible; it was written up as a mechanism and a direction with no magnitude.

`predicted` is not published, but everything around it is, so it is recovered by inversion:

    requested = kappaCmd - laneCenterCorrection          (the trim is added AFTER the blend)
    predicted = (requested - desired*(1 - b)) / b        (b = blendWeight, desired = controlsState)

Only on frames where the deviation clip did NOT bite, since that clip breaks the inversion.

**THE SAMPLING INTERVAL IS THE TRAP AND IT CAUGHT ME TWICE IN ONE HOUR.** `controllerStateBP` is
published from card at 100 Hz, but `update_angle_strategy` runs inside `STEER_STEP = 5` -- so every
value repeats five times. `_desired_falling` compares CONSECUTIVE ANGLE-PATH CALLS, so:

    compared every 100 Hz frame     the gate looks 5x too strict and the falls 3x too big
    detected by "the values changed"  under-counts calls on steady road (10.6 frames per call)
    RESAMPLED AT 20 Hz                correct, and what this tool does

Three different magnitudes came out of those three choices. Resample on TIME; do not try to detect
the call.

    python tools/bp_blend_latch_scale.py <dir>...

REQUIRES the 2026-09-01 telemetry (`blendWeight`, `kappaCmd`, `laneCenterCorrection`), so only
routes from that date onward can be scored.
"""
import argparse
import glob
import math
import os
import statistics

import capnp
import zstandard

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
capnp.remove_import_hook()
log_capnp = capnp.load(os.path.join(REPO, "cereal", "log.capnp"),
                       imports=[os.path.join(REPO, "cereal")])

# FORD_FUSION_MK5, opendbc/car/ford/values.py. Curvature (1/m) -> geometric wheel degrees.
DEG = 2.85 * 17.07 * 180.0 / math.pi
EXIT_WEIGHT = 0.50 * 0.25          # path_angle_blend_ratio * 0.25, the exit branch's target
CALL_DT = 0.05                     # STEER_STEP=5 at 100 Hz; the interval the gate compares across
DITHER_FLOOR = 0.30                # degrees he provably cannot feel -- see CLAUDE.md
MAX_GAP_CALLS = 40                 # 2 s; longer gaps are separate corners, not one unwind


def pct(xs, p):
  s = sorted(xs)
  return s[min(len(s) - 1, int(len(s) * p))] if s else float("nan")


def scan(files):
  gaps_exit, exit_b, exit_deg, exit_kappa, between = [], [], [], [], []
  gaps_all, b_seen = [], []
  calls = fires = 0

  for path in files:
    try:
      with open(path, "rb") as fh:
        raw = zstandard.ZstdDecompressor().stream_reader(fh).read()
      evs = log_capnp.Event.read_multiple_bytes(raw, traversal_limit_in_words=2 ** 32)
    except Exception:
      continue

    desired = 0.0
    lat = pressed = False
    v = 0.0
    last_sample_t = None
    prev_desired = None
    call_idx = 0
    last_fire = None

    while True:
      try:
        m = next(evs)
      except StopIteration:
        break
      except Exception:
        break
      try:
        w = m.which()
        if w == "controlsState":
          desired = float(m.controlsState.desiredCurvature)
          continue
        if w == "carControl":
          lat = bool(m.carControl.latActive)
          continue
        if w == "carState":
          pressed = bool(m.carState.steeringPressed)
          v = float(m.carState.vEgo)
          continue
        if w != "controllerStateBP":
          continue

        t = m.logMonoTime / 1e9
        if last_sample_t is not None and t - last_sample_t < CALL_DT * 0.9:
          continue
        last_sample_t = t

        c = m.controllerStateBP
        b = float(c.blendWeight)
        kc = float(c.kappaCmd)
        if b <= 1e-6 or kc == 0.0:
          continue
        if not lat or pressed or v < 4.0:
          prev_desired = None
          last_fire = None
          continue
        if bool(getattr(c, "curvatureDeviationLimited", False)):
          prev_desired = desired
          continue

        calls += 1
        call_idx += 1
        predicted = ((kc - float(c.laneCenterCorrection)) - desired * (1.0 - b)) / b
        gap = abs(predicted - desired)
        gaps_all.append(gap)
        b_seen.append(b)

        # ba20937aac's exit gate, on the interval it actually spans.
        if prev_desired is not None and abs(prev_desired) > 0.001 \
           and abs(desired) < abs(prev_desired) * 0.8:
          fires += 1
          gaps_exit.append(gap)
          exit_b.append(b)
          exit_kappa.append(abs(desired))
          exit_deg.append(max(0.0, b - EXIT_WEIGHT) * gap * DEG)
          if last_fire is not None and 0 < call_idx - last_fire <= MAX_GAP_CALLS:
            between.append(call_idx - last_fire)
          last_fire = call_idx
        prev_desired = desired
      except Exception:
        continue

  return gaps_all, b_seen, gaps_exit, exit_b, exit_deg, exit_kappa, between, calls, fires


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("directory", nargs="+")
  args = ap.parse_args()

  files = []
  for d in args.directory:
    files += glob.glob(os.path.join(d, "*.rlog.zst"))
  files.sort()
  if not files:
    print("no matching segments")
    return

  gaps_all, b_seen, gaps_exit, exit_b, exit_deg, exit_kappa, between, calls, fires = scan(files)
  print("=== THE EXIT-BLEND LATCH: HOW BIG, AND HOW LONG ===")
  print(f"   {len(files)} segments, {calls} qualifying angle-path calls resampled at "
        f"{1.0 / CALL_DT:.0f} Hz")
  if not calls:
    print("   nothing to measure -- routes older than the 2026-09-01 blendWeight telemetry?")
    return
  print(f"   blendWeight in force: p10 {pct(b_seen, 0.10):.3f}  p50 "
        f"{statistics.median(b_seen):.3f}  p90 {pct(b_seen, 0.90):.3f}")
  print(f"   exit gate fires on {fires} calls ({100.0 * fires / calls:.2f}%)")
  if not fires:
    return

  print()
  print("  |predicted - desired| -- the whole lever the latch has to work with")
  print(f"  {'population':>12}{'n':>8}{'p50 1/m':>11}{'p90':>11}{'p99':>11}")
  for name, xs in (("all calls", gaps_all), ("exit calls", gaps_exit)):
    print(f"  {name:>12}{len(xs):>8}{statistics.median(xs):>11.6f}"
          f"{pct(xs, 0.90):>11.6f}{pct(xs, 0.99):>11.6f}")

  print()
  print("  WHAT THE LATCH CAN ACTUALLY REACH, in degrees at the wheel. The full 0.375 swing is NOT")
  print("  available -- b is already low on straight road -- so this is (b at the fire) - 0.125,")
  print("  times the gap above.")
  print(f"    b at the fire      p10 {pct(exit_b, 0.10):.3f}  p50 {statistics.median(exit_b):.3f}"
        f"  p90 {pct(exit_b, 0.90):.3f}")
  print(f"    reachable, deg     p50 {statistics.median(exit_deg):.2f}"
        f"  p90 {pct(exit_deg, 0.90):.2f}  p99 {pct(exit_deg, 0.99):.2f}"
        f"  max {max(exit_deg):.2f}")
  print(f"    |desired| there    p50 {statistics.median(exit_kappa):.5f} 1/m"
        f"  -> radius {1.0 / max(statistics.median(exit_kappa), 1e-9):.0f} m")
  over = sum(1 for d in exit_deg if d >= DITHER_FLOOR)
  print(f"    at or over {DITHER_FLOOR:.2f} deg  {over} of {len(exit_deg)} "
        f"({100.0 * over / len(exit_deg):.0f}%)  <- below this he cannot feel it")

  if between:
    print()
    print("  HOW LONG THE LATCH MUST HOLD. Calls between consecutive fires inside one unwind,")
    print(f"  counting only gaps under {MAX_GAP_CALLS * CALL_DT:.0f} s so separate corners are not "
          f"merged:")
    print(f"    n {len(between)}   p50 {statistics.median(between):.0f}"
          f"   p75 {pct(between, 0.75):.0f}   p90 {pct(between, 0.90):.0f}"
          f"   p95 {pct(between, 0.95):.0f}   max {max(between)}")
    for n in (4, 6, 8, 10, 12, 16):
      bridged = sum(1 for x in between if x <= n)
      print(f"    a {n:>2}-call ({n * CALL_DT:.2f} s) latch bridges {bridged:>4} of {len(between)}"
            f"  ({100.0 * bridged / len(between):.0f}%)")
    print()
    print("  The ramp needs FOUR calls to walk 0.500 -> 0.125 at 0.1 per call, so a latch shorter")
    print("  than that reproduces the defect exactly. Past the knee each extra call buys a point or")
    print("  two and spends more of the corner committed to the exit weight.")


if __name__ == "__main__":
  main()
