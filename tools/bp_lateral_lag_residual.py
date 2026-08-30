"""How late is the car ACTUALLY, and is it still under-compensated? Read-only, off-device.

*"Nothing it did wrong today felt small... make sure you look at everything related to latency."*

The compensation chain is supposed to close exactly:

    lagd learns          liveDelay.lateralDelay        ~0.39 s on this car
    modeld aims          lat_action_t = delay + DT_MDL + DT_MDL/2   = ~0.47 s ahead
    the PSCM takes       about that long to get there

so `desiredCurvature(t)` should line up with `curvature(t + lat_action_t)`. This measures whether
it does, by shifting the actual signal against the desired and finding the shift that minimises the
error. That BEST-FIT LAG is the car's true end-to-end delay, and the gap between it and
lat_action_t is residual under- or over-compensation:

    best_fit > lat_action_t     still arriving late  -> under-compensated, lag-shaped overshoot
    best_fit ~ lat_action_t     the loop is closed; whatever is left is not latency
    best_fit < lat_action_t     over-compensated, which would produce early turn-in

WHY A SHIFT AND NOT A CORRELATION. Curvature is dominated by long straights where both signals sit
near zero and any shift correlates beautifully, so a raw cross-correlation is mostly measuring the
straights. This scores only frames where the road is actually turning, and reports the error at
each shift so the minimum can be seen to be a real minimum rather than a flat floor.

AND IT IS MEASURED SEPARATELY DURING THE BIG EPISODES, because that is the complaint. A loop can be
well compensated on average and late exactly where it matters; reporting only the pooled number is
how "the lag is compensated" got recorded as settled once already.

HANDS OFF, latActive. Hands-on frames are his steering, not the controller's.

    python tools/bp_lateral_lag_residual.py <dir-of-rlog.zst> [route ...]
"""
import collections
import glob
import os
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
DT_MDL = 0.05
MAX_SHIFT_FRAMES = 90          # 0.90 s, well past any plausible actuator delay
MIN_KAPPA = 8e-4               # only frames where the road is genuinely turning
BIG_SWING_DEG = 8.0            # matches bp_lateral_rate.py --swing 8
WIN_S = 2.0


def seg_index(p):
  try:
    return int(os.path.basename(p).split("--")[2].split(".")[0])
  except (IndexError, ValueError):
    return -1


def best_shift(des, act, mask):
  """Return (best_shift_frames, errors_by_shift). Error is mean |act[t+s] - des[t]| over mask."""
  n = len(des)
  errs = []
  for s in range(0, MAX_SHIFT_FRAMES + 1):
    d = des[:n - s][mask[:n - s]]
    a = act[s:][mask[:n - s]]
    if d.size < 200:
      errs.append(np.inf)
      continue
    errs.append(float(np.mean(np.abs(a - d))))
  errs = np.array(errs)
  return int(np.argmin(errs)), errs


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
    ctrl = []      # (t, desired, actual)
    car = []       # (t, angle, v, hands)
    delay = []
    lagd = None
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
        w = m.which()
        if w == "initData" and lagd is None:
          for e in m.initData.params.entries:
            if e.key == "LagdValueCache":
              try:
                lagd = float(e.value.decode(errors="replace").strip())
              except Exception:
                pass
          continue
        mono = m.logMonoTime / 1e9
        if t0 is None or mono < t0:
          t0 = mono
        ts = mono - t0
        try:
          if w == "controlsState":
            cs = m.controlsState
            ctrl.append((ts, float(cs.desiredCurvature), float(cs.curvature)))
          elif w == "carState":
            cs = m.carState
            car.append((ts, float(cs.steeringAngleDeg), float(cs.vEgo), bool(cs.steeringPressed)))
          elif w == "liveDelay":
            delay.append(float(m.liveDelay.lateralDelay))
        except Exception:
          continue

    if len(ctrl) < 5000:
      continue
    ctrl.sort()
    car.sort()
    des = np.array([r[1] for r in ctrl])
    act = np.array([r[2] for r in ctrl])
    ts = np.array([r[0] for r in ctrl])

    # Hands / speed, sampled onto the controlsState grid by nearest neighbour.
    ct = np.array([r[0] for r in car])
    ca = np.array([r[1] for r in car])
    ch = np.array([r[3] for r in car], dtype=bool)
    idx = np.clip(np.searchsorted(ct, ts), 0, len(ct) - 1)
    hands = ch[idx]
    angle = ca[idx]

    turning = (np.abs(des) >= MIN_KAPPA) & (~hands)

    lat_action_t = (lagd if lagd else (np.median(delay) if delay else 0.38)) + DT_MDL + DT_MDL / 2.0
    s_best, errs = best_shift(des, act, turning)

    # The same measurement restricted to the frames inside a big swing.
    win = int(WIN_S * HZ)
    big = np.zeros(len(ts), dtype=bool)
    i = 0
    while i + win < len(angle):
      seg = angle[i:i + win]
      if np.max(seg) - np.min(seg) >= BIG_SWING_DEG and not hands[i:i + win].any():
        big[i:i + win] = True
        i += win
      else:
        i += 1
    big_mask = turning & big
    s_big, errs_big = best_shift(des, act, big_mask) if big_mask.sum() > 500 else (None, None)

    print("=== %s ===" % route)
    print("  learned lateralDelay (LagdValueCache) : %.3f s" % (lagd if lagd else float('nan')))
    print("  what modeld aims for (lat_action_t)   : %.3f s" % lat_action_t)
    print("  turning frames, hands off             : %d" % int(turning.sum()))
    print()
    print("  BEST-FIT LAG (shift that minimises |actual - desired|)")
    print("    all turning frames  : %.3f s   residual %+.3f s" %
          (s_best / HZ, s_best / HZ - lat_action_t))
    if s_big is not None:
      print("    inside >=%.0f deg swings: %.3f s   residual %+.3f s   (n=%d)" %
            (BIG_SWING_DEG, s_big / HZ, s_big / HZ - lat_action_t, int(big_mask.sum())))
    print()
    print("  error vs shift (1/m), every 50 ms -- check the minimum is real, not a flat floor:")
    line = "   "
    for s in range(0, MAX_SHIFT_FRAMES + 1, 5):
      if np.isfinite(errs[s]):
        line += " %.0f:%.5f" % (s / HZ * 1000, errs[s])
    print(line)
    print()


if __name__ == "__main__":
  main()
