"""Lateral delivery split by PHASE -- turning in, holding, unwinding -- and by what caused it.

**RUN THIS BEFORE ANY GAIN RECOMMENDATION LEAVES THIS REPO.** That is the whole reason it exists.

On 2026-09-04 a steady-state measurement said delivery was 0.86 and our gain was short, and a
"raise FordLowSpeedFactor_ang and FordHighSpeedDampening_ang" recommendation was built on it and
given to the owner. He refused it from the seat -- *"Changing those settings higher will lead to
more oversteer, though. It oversteered on that tight turn today."* -- and he was right. Split by
phase, the same drive reads:

    tight <500 m    turning in 0.664    holding 0.868    UNWINDING 1.017   (53% of frames over 1.0)

Steady state is ONE POINT IN THE MIDDLE of a spread that runs 0.50 to 1.08, and the spread is the
car's ~0.39 s lag. **A gain multiplies the whole ratio; it slides the spread and cannot narrow it.**
Raising the low factor 1.007 -> 1.20 to fix the 0.66 turn-in takes the unwind p90 from 1.33 to 1.58,
worsening precisely what he reported. The driver feels the ENDS; a median cannot see them.

    python tools/bp_lateral_phases.py <dir> [--route 00000423] [--segs 0-15] [--speed 9]

WHY THE DEGREES COLUMN IS THE ONE THAT DECIDES. A ratio of 1.017 sounds harmless and 0.66 sounds
catastrophic, and both are misleading -- this car's measured imperceptible-dither floor is 0.10-0.30
degrees of wheel, so a ratio only matters after it is multiplied by the angle the corner actually
needs. The same drive in degrees:

    tight <500 m    turning in  median -3.32d      UNWINDING  median +0.16d  p90 +3.21d  p99 +10.81d

which says the typical exit overshoot is imperceptible and the TAIL is what he feels. Report both
columns or the finding is wrong in one direction or the other.

THE DECOMPOSITION COLUMNS. `desired --[trim + clip]--> kappa_cmd --[gain]--> command --[PSCM]-->
actual`, each link measured separately, because the flat 0.86 delivery means different things at
different speeds: at 70 mph on a highway curve the gain is 0.834 and the car tracks 0.952 (ours,
and settings close it), while at 40 mph on a turn the gain is already 1.005 and the car returns
0.784 (the PSCM, and no setting in this fork reaches it). They are medians of ratios and do NOT
multiply to the delivery column exactly; the pattern is the finding, not an arithmetic identity.

Definitions, so a future reader can tell whether a quoted number came from here:

    phase       slope of |desiredCurvature| across a 0.3 s trailing window, as a fraction of its
                current value: > +0.15 turning in, < -0.15 unwinding, else holding
    gates       latActive, hands off, v >= --speed, curvatureFactor published (non-zero)
    delivery    |curvature| / |desiredCurvature|  -- above 1.0 the car is turning MORE than asked
    degrees     (|curvature| - |desiredCurvature|) * wheelbase * steerRatio, i.e. the EXCESS wheel
                angle; positive means turned in further than the plan asked
    PSCM        |curvature| / |kappaCmd * curvatureFactor| -- what the car did with the command it
                was actually given, after our trim, our clip and our gain

REQUIRES the 2026-09-01 telemetry (`kappaCmd`, `curvatureFactor`, `laneCenterCorrection` on
`ControllerStateBP`). Routes older than that print INSUFFICIENT rather than a wrong number.
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

MPH = 2.23694
WHEELBASE = 2.85          # FORD_FUSION_MK5, opendbc/car/ford/values.py
STEER_RATIO = 17.07
DEG = WHEELBASE * STEER_RATIO * 180.0 / math.pi   # curvature (1/m) -> geometric wheel degrees

WIN = 30                  # 0.3 s of carState at 100 Hz
SLOPE = 0.15              # fraction of the current value; below this the road is "holding"
DITHER_FLOOR = 0.30       # degrees he provably cannot feel -- see CLAUDE.md

# Radius bands. 500-2000 m is the band his complaints land in (bp_lateral_by_radius, 2026-08-29).
BANDS = [("tight  <500 m", 0.002, 1.0),
         ("highway 500-2000 m", 0.0005, 0.002)]
PHASES = ("turning in", "holding", "UNWINDING")


def segno(path):
  try:
    return int(os.path.basename(path).split("--")[2].split(".")[0])
  except (IndexError, ValueError):
    return -1


def route_of(path):
  return os.path.basename(path).split("--")[0]


def scan(files, speed_floor):
  # per band, per phase: ratios, excess degrees; and the decomposition
  acc = {b[0]: {p: {"ratio": [], "deg": []} for p in PHASES} for b in BANDS}
  dec = {b[0]: {"trimclip": [], "gain": [], "pscm": [], "mph": []} for b in BANDS}
  frames = 0
  no_telemetry = 0

  for path in files:
    try:
      with open(path, "rb") as fh:
        raw = zstandard.ZstdDecompressor().stream_reader(fh).read()
      evs = log_capnp.Event.read_multiple_bytes(raw, traversal_limit_in_words=2 ** 32)
    except Exception:
      continue

    lat = False
    des = cur = 0.0
    cf = kc = 0.0
    hist = []
    steady = []
    while True:
      try:
        m = next(evs)
      except StopIteration:
        break
      except Exception:
        break
      try:
        w = m.which()
        if w == "carControl":
          lat = bool(m.carControl.latActive)
        elif w == "controllerStateBP":
          c = m.controllerStateBP
          cf = float(c.curvatureFactor)
          kc = float(c.kappaCmd)
        elif w == "controlsState":
          des = float(m.controlsState.desiredCurvature)
          cur = float(m.controlsState.curvature)
        elif w == "carState":
          cs = m.carState
          v = float(cs.vEgo)
          if not lat or bool(cs.steeringPressed) or v * MPH < speed_floor:
            hist = []
            steady = []
            continue
          if cf <= 0.01:
            # Pre-2026-09-01 route, or a frame where the angle strategy did not run. Counting it
            # as a zero would invent a shortfall that never happened.
            no_telemetry += 1
            hist = []
            steady = []
            continue

          a = abs(des)
          hist.append(a)
          if len(hist) > WIN:
            hist.pop(0)
          if len(hist) < WIN or a < BANDS[-1][1]:
            continue

          frames += 1
          slope = (hist[-1] - hist[0]) / a
          phase = "turning in" if slope > SLOPE else ("UNWINDING" if slope < -SLOPE else "holding")
          ratio = abs(cur) / a
          excess = (abs(cur) - a) * DEG

          for name, klo, khi in BANDS:
            if not (klo <= a < khi):
              continue
            acc[name][phase]["ratio"].append(ratio)
            acc[name][phase]["deg"].append(excess)

            # The decomposition needs STEADY state, or the lag contaminates every link. Tighter
            # gate than the phase split above deliberately: within 5% for a full 0.5 s.
            steady.append(a)
            if len(steady) > 50:
              steady.pop(0)
            if len(steady) == 50 and (max(steady) - min(steady)) <= 0.05 * a and abs(kc) > 1e-5:
              dec[name]["trimclip"].append(abs(kc) / a)
              dec[name]["gain"].append(cf)
              dec[name]["pscm"].append(abs(cur) / abs(kc * cf))
              dec[name]["mph"].append(v * MPH)
            break
      except Exception:
        continue
  return acc, dec, frames, no_telemetry


def pct(xs, p):
  s = sorted(xs)
  return s[min(len(s) - 1, int(len(s) * p))]


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("directory")
  ap.add_argument("--speed", type=float, default=9.0, help="mph floor (default 9)")
  ap.add_argument("--route", action="append", help="limit to route(s)")
  ap.add_argument("--segs", help="segment range within a single route, e.g. 0-15")
  ap.add_argument("--min-frames", type=int, default=60,
                  help="below this a cell reports thin rather than a number")
  args = ap.parse_args()

  files = sorted(glob.glob(os.path.join(args.directory, "*.rlog.zst")), key=segno)
  if args.route:
    files = [f for f in files if route_of(f) in set(args.route)]
  if args.segs:
    lo, hi = (int(x) for x in args.segs.split("-"))
    files = [f for f in files if lo <= segno(f) <= hi]
  if not files:
    print("no matching segments")
    return

  routes = sorted({route_of(f) for f in files})
  acc, dec, frames, no_tel = scan(files, args.speed)

  print("=== LATERAL DELIVERY BY PHASE ===")
  print(f"   {len(files)} segments, routes {', '.join(routes)}, hands off, latActive, "
        f">= {args.speed:.0f} mph")
  print(f"   {frames} qualifying frames"
        + (f"; {no_tel} skipped for missing gain telemetry" if no_tel else ""))
  print()
  print("   RATIO = actual / desired curvature. Above 1.00 the car turned MORE than it was asked.")
  print("   DEGREES = the excess wheel angle. Positive means turned in further than asked.")
  print(f"   Anything under {DITHER_FLOOR:.2f} deg is inside this car's measured dither floor.")
  print()
  print(f"  {'band':>20}{'phase':>13}{'n':>7}{'ratio':>8}{'p90':>7}"
        f"{'median':>10}{'p90 deg':>10}{'p99 deg':>10}")
  for name, _, _ in BANDS:
    for phase in PHASES:
      cell = acc[name][phase]
      n = len(cell["ratio"])
      if n < args.min_frames:
        print(f"  {name:>20}{phase:>13}{n:>7}   thin")
        continue
      print(f"  {name:>20}{phase:>13}{n:>7}"
            f"{statistics.median(cell['ratio']):>8.3f}{pct(cell['ratio'], 0.90):>7.2f}"
            f"{statistics.median(cell['deg']):>9.2f}d{pct(cell['deg'], 0.90):>9.2f}d"
            f"{pct(cell['deg'], 0.99):>9.2f}d")

  print()
  print("=== WHERE THE MISSING CURVATURE GOES (steady state only: desired within 5% for 0.5 s) ===")
  print("   desired --[trim+clip]--> kappa_cmd --[our gain]--> command --[PSCM]--> actual")
  print("   PSCM is the only column no setting in this fork can reach.")
  print()
  print(f"  {'band':>20}{'n':>7}{'mph':>7}{'trim+clip':>12}{'our gain':>10}{'PSCM':>8}")
  for name, _, _ in BANDS:
    d = dec[name]
    if len(d["pscm"]) < args.min_frames:
      print(f"  {name:>20}{len(d['pscm']):>7}   thin")
      continue
    print(f"  {name:>20}{len(d['pscm']):>7}{statistics.median(d['mph']):>7.1f}"
          f"{statistics.median(d['trimclip']):>12.3f}{statistics.median(d['gain']):>10.3f}"
          f"{statistics.median(d['pscm']):>8.3f}")
  print()
  print("  Medians of ratios; they do NOT multiply to the delivery column exactly.")
  print()
  print("  READ THE PHASES BEFORE RECOMMENDING A GAIN. A gain multiplies the whole ratio, so it")
  print("  slides the turning-in and unwinding rows together and cannot narrow the spread between")
  print("  them -- that spread is the car's ~0.39 s lag. Raising a gain to fix a lazy turn-in makes")
  print("  the exit overshoot worse by the same factor, which is the road report that killed this")
  print("  exact recommendation on 2026-09-04.")


if __name__ == "__main__":
  main()
