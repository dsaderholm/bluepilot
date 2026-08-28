"""Does the car DELIVER the curvature it is commanded, and does the shortfall track the gain blend?

One clean hands-off episode showed actual curvature at ~72% of desired with the gap GROWING as the
curve tightened. That is under-delivery on entry, not oscillation -- and it reframes the whole
lateral complaint. This measures it properly, across every qualifying frame rather than one episode.

THE NATURAL EXPERIMENT IS SPEED, NOT HIS MID-TRIP EDIT. He changed FordHighSpeedFactor_ang from
0.818 to 0.830 partway through, which is a 1.5% difference and far too small to separate anything.
But the factor is BLENDED BY SPEED in lateral_angle_ext:

    low_gain_calc  = interp(v_ego, [13.5, 26.82], [1.0, gain_lowC_highV * user_dampening])
    high_gain_calc = interp(v_ego, [13.5, 26.82], [1.30 * low_speed_factor, gain_highC_highV * high_speed_factor])

so the effective multiplier sweeps a large range between 30 and 60 mph on every drive he has ever
done. If the delivery shortfall tracks that sweep, the factor is implicated and is a setting he can
move. If delivery is flat with speed, the factor is not the cause and changing it would be tuning
against the wrong variable -- which is what "nothing really made steering perfect" already suggests.

STEADY-STATE ONLY. During curve ENTRY the actual curvature legitimately lags the desired, so a ratio
taken there measures the transient and not the delivery. Frames are kept only where the desired
curvature has been stable for `STEADY_S`, which is what makes the ratio mean "what the car settles
at" rather than "how fast it gets there".

    python tools/bp_lateral_delivery.py <dir-of-rlog.zst> [route ...]
"""
import collections
import glob
import math
import os
import sys

import capnp
import zstandard

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
capnp.remove_import_hook()
log_capnp = capnp.load(os.path.join(REPO, "cereal", "log.capnp"),
                       imports=[os.path.join(REPO, "cereal")])

MS_TO_MPH = 2.23694
STEADY_S = 0.5            # desired curvature must have been stable this long
STEADY_TOL = 1.5e-4       # 1/m, how much it may drift and still count as steady
MIN_KAPPA = 3e-4          # ignore near-straights; radius > ~3300 m carries no signal
HZ = 100

# The blend band from lateral_angle_ext, so the speed bins line up with the thing being tested.
V_BLEND_LO, V_BLEND_HI = 13.5, 26.82


def say(s=""):
  print(s)
  sys.stdout.flush()


def seg_index(p):
  try:
    return int(os.path.basename(p).split("--")[2].split(".")[0])
  except (IndexError, ValueError):
    return -1


def main():
  d = sys.argv[1]
  routes = set(sys.argv[2:])
  files = sorted(glob.glob(os.path.join(d, "*.rlog.zst")), key=seg_index)
  if routes:
    files = [f for f in files if os.path.basename(f).split("--")[0] in routes]

  by_route = collections.OrderedDict()
  for f in files:
    by_route.setdefault(os.path.basename(f).split("--")[0], []).append(f)

  bins = collections.defaultdict(list)
  per_route = collections.defaultdict(list)

  for route, paths in by_route.items():
    lat = hands = False
    v = 0.0
    hist = collections.deque(maxlen=int(STEADY_S * HZ))
    for p in paths:
      try:
        with open(p, "rb") as f:
          raw = zstandard.ZstdDecompressor().stream_reader(f).read()
        evs = log_capnp.Event.read_multiple_bytes(raw, traversal_limit_in_words=2 ** 32)
      except Exception:
        continue
      while True:
        try:
          m = next(evs)
        except (StopIteration, Exception):
          break
        w = m.which()
        try:
          if w == "carControl":
            lat = bool(m.carControl.latActive)
          elif w == "carState":
            hands = bool(m.carState.steeringPressed)
            v = m.carState.vEgo
          elif w == "controlsState":
            cs = m.controlsState
            des, act = float(cs.desiredCurvature), float(cs.curvature)
            hist.append(des)
            if not (lat and not hands and v >= 8.0):
              continue
            if abs(des) < MIN_KAPPA:
              continue
            if len(hist) < hist.maxlen:
              continue
            if (max(hist) - min(hist)) > STEADY_TOL:
              continue                      # still transitioning: not a delivery measurement
            ratio = act / des
            if 0.0 < ratio < 3.0:
              mph = v * MS_TO_MPH
              bins[(int(mph) // 10) * 10].append(ratio)
              per_route[route].append(ratio)
        except Exception:
          continue

  say("=== DOES THE CAR DELIVER THE COMMANDED CURVATURE? (steady-state only) ===")
  say("  ratio = actual curvature / desired curvature. 1.00 = delivers exactly what is asked.")
  say()
  say("  %-12s %8s %9s %9s %9s   %s" % ("speed", "frames", "p25", "MEDIAN", "p75", "effective gain blend"))
  for k in sorted(bins):
    r = sorted(bins[k])
    if len(r) < 300:
      continue
    n = len(r)
    v_mid = (k + 5) / MS_TO_MPH
    # where this speed sits in the blend: 0 at/below 13.5 m/s, 1 at/above 26.82
    frac = min(max((v_mid - V_BLEND_LO) / (V_BLEND_HI - V_BLEND_LO), 0.0), 1.0)
    say("  %3d-%-3d mph %8d %9.3f %9.3f %9.3f   %3.0f%% toward the high-speed factor" % (
      k, k + 10, n, r[n // 4], r[n // 2], r[3 * n // 4], 100 * frac))

  say()
  say("=== BY ROUTE (his mid-trip factor change, for what it is worth) ===")
  for route in sorted(per_route):
    r = sorted(per_route[route])
    if len(r) < 300:
      continue
    say("  %-10s %7d frames   median %.3f" % (route, len(r), r[len(r) // 2]))

  say()
  say("  IF the median falls as speed rises, delivery tracks the gain blend and the factor is")
  say("  implicated -- a setting he can move. IF it is flat, the factor is not the cause and")
  say("  tuning it is aiming at the wrong variable, which is what his own sweep already suggested.")


if __name__ == "__main__":
  main()
