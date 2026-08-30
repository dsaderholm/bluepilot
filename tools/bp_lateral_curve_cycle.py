"""SLOW oscillation while holding a curve -- the thing he actually reports. Read-only, off-device.

*"It was just ping ponging so much on curves."*

`bp_lateral_episodes.py` and `bp_lateral_rate.py` use a 2 SECOND window, and the limit cycle found
on 000003ed has a period of about 4.7 s. A 2 s window cannot contain one cycle of it, so those
tools measure fast wobble and are structurally blind to this. Ranking two gain settings with them
produced a recommendation to restore gains he had just spent 3.3 hours rejecting -- the metric
disagreed with the driver because it was not measuring what he felt.

THIS MEASURES THE CURVE-HOLDING CASE ONLY. A window qualifies when the road is genuinely bent for
its whole length (|desired| stays above MIN_KAPPA, so the model never thinks it is straight) and
his hands are off. Within it:

    mean_kappa    what the curve actually is
    swing         peak-to-peak of desired around that mean, as a FRACTION of it
    reversals     sign changes of (desired - mean), i.e. the plan crossing its own centre line

A steady curve held steadily scores swing ~0 and reversals 0. A curve the planner keeps
overshooting and re-crossing scores high on both, and THAT is the report. The steering angle is
reported beside it because that is what he feels, but the qualifying test is on desired curvature:
angle scales with gain, so testing on angle would mark any high-gain setting as worse by
construction -- which is the trap this whole file keeps stepping in.

    python tools/bp_lateral_curve_cycle.py <dir-of-rlog.zst> [route ...]
"""
import collections
import glob
import os
import statistics
import sys

import capnp
import numpy as np
import zstandard

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
capnp.remove_import_hook()
log_capnp = capnp.load(os.path.join(REPO, "cereal", "log.capnp"),
                       imports=[os.path.join(REPO, "cereal")])

MS_TO_MPH = 2.23694
HZ = 100
WIN_S = 6.0            # long enough to contain a full ~4.7 s cycle
MIN_KAPPA = 6e-4       # the road is bent -- radius tighter than ~1700 m
MIN_V = 45.0 / MS_TO_MPH
CROSS_FRAC = 0.15      # a crossing counts once it is this far past the mean, to reject dither


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

  for route, paths in by_route.items():
    ctrl, car = [], []
    t0 = None
    for p in paths:
      try:
        with open(p, "rb") as fh:
          raw = zstandard.ZstdDecompressor().stream_reader(fh).read()
        evs = log_capnp.Event.read_multiple_bytes(raw, traversal_limit_in_words=2 ** 32)
      except Exception:
        continue
      while True:
        try:
          m = next(evs)
        except StopIteration:
          break
        except Exception:
          break
        mono = m.logMonoTime / 1e9
        if t0 is None or mono < t0:
          t0 = mono
        ts = mono - t0
        w = m.which()
        try:
          if w == "controlsState":
            cs = m.controlsState
            ctrl.append((ts, float(cs.desiredCurvature), float(cs.curvature)))
          elif w == "carState":
            cs = m.carState
            car.append((ts, float(cs.steeringAngleDeg), float(cs.vEgo), bool(cs.steeringPressed)))
        except Exception:
          continue

    if len(ctrl) < 5000 or not car:
      continue
    ctrl.sort()
    car.sort()
    ts = np.array([r[0] for r in ctrl])
    des = np.array([r[1] for r in ctrl])
    act = np.array([r[2] for r in ctrl])
    ct = np.array([r[0] for r in car])
    ca = np.array([r[1] for r in car])
    cv = np.array([r[2] for r in car])
    ch = np.array([r[3] for r in car], dtype=bool)
    idx = np.clip(np.searchsorted(ct, ts), 0, len(ct) - 1)
    ang, v, hands = ca[idx], cv[idx], ch[idx]

    win = int(WIN_S * HZ)
    curve_min = 0.0
    bad = []
    swings = []
    by_deliv = []
    i = 0
    while i + win < len(des):
      dwin = des[i:i + win]
      if hands[i:i + win].any() or (v[i:i + win] < MIN_V).any():
        i += win // 2
        continue
      # The whole window must be on one side of straight and genuinely bent.
      if np.min(np.abs(dwin)) < MIN_KAPPA or np.ptp(np.sign(dwin)) > 0:
        i += win // 2
        continue
      curve_min += WIN_S / 60.0
      mean = float(np.mean(dwin))
      swing = float(np.ptp(dwin) / abs(mean))
      dev = dwin - mean
      thr = CROSS_FRAC * abs(mean)
      sign = 0
      cross = 0
      for x in dev:
        s = 1 if x > thr else (-1 if x < -thr else 0)
        if s and sign and s != sign:
          cross += 1
        if s:
          sign = s
      swings.append(swing)
      # Does the plan swing MORE where the car is delivering LESS? That is the closed-loop
      # under-delivery story: the model asks, the car falls short, the model asks harder, the car
      # arrives late, the model backs off. If swing is flat against delivery, that story is wrong
      # and the oscillation is the model's own regardless of what the car does.
      deliv = float(np.mean(act[i:i + win]) / mean) if mean != 0 else 0.0
      if 0.3 < deliv < 1.6:
        by_deliv.append((deliv, swing))
      if cross >= 2:
        bad.append((route, ts[i], swing, cross,
                    float(np.mean(v[i:i + win])) * MS_TO_MPH,
                    float(np.ptp(ang[i:i + win])), 1.0 / abs(mean)))
      i += win // 2

    print("=== %s : SLOW OSCILLATION WHILE HOLDING A CURVE (%.0f s windows) ===" % (route, WIN_S))
    print()
    if curve_min <= 0:
      print("  no qualifying curve-holding time")
      print()
      continue
    print("  curve-holding time, hands off, >= %.0f mph : %.1f min" % (MIN_V * MS_TO_MPH, curve_min))
    print("  windows where the plan re-crossed its own mean twice or more : %d" % len(bad))
    print("  that is %.2f per minute of curve holding" % (len(bad) / curve_min))
    if swings:
      swings.sort()
      print("  swing of desired about the curve mean: median %.0f%%  p90 %.0f%%"
            % (100 * statistics.median(swings), 100 * swings[int(0.9 * (len(swings) - 1))]))
    if len(by_deliv) >= 40:
      print()
      print("  DOES THE PLAN SWING MORE WHERE THE CAR DELIVERS LESS?")
      edges = [(0.0, 0.80), (0.80, 0.90), (0.90, 1.00), (1.00, 1.60)]
      print("    %-16s %10s %8s" % ("delivery", "med swing", "n"))
      for lo, hi in edges:
        sel = [s for dv, s in by_deliv if lo <= dv < hi]
        if len(sel) < 10:
          continue
        print("    %-16s %9.0f%% %8d" % ("%.2f-%.2f" % (lo, hi), 100 * statistics.median(sel), len(sel)))
    if bad:
      print()
      print("  worst, by how far the plan swung relative to the curve it was holding:")
      print("    %10s %8s %7s %7s %10s %10s" % ("t+", "swing", "cross", "mph", "steer p2p", "radius m"))
      for r in sorted(bad, key=lambda x: -x[2])[:6]:
        print("    %10.1f %7.0f%% %7d %7.0f %9.1f deg %10.0f" % (r[1], 100 * r[2], r[3], r[4], r[5], r[6]))
    print()


if __name__ == "__main__":
  main()
