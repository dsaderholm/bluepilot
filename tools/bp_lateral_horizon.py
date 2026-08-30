"""The blend mixes two DIFFERENT horizons, and that undoes the lag compensation. Read-only.

modeld hands the car a curvature that is already lag-compensated. Exactly:

    lat_delay    = LagdValueCache            0.393 on his device
    lat_action_t = lat_delay + DT_MDL + DT_MDL/2   = 0.468 s

so `actuators.curvature` is the path curvature 0.468 s AHEAD -- aimed there on purpose, because that
is how long the PSCM takes to get there.

`lateral_angle_ext` then blends a model sample of its own into it:

    curvature_lookup_time = clip(liveDelay, 0.1, 0.15) + DT_MDL + extra   = 0.20 s at highway speed
    requested = predicted(0.20 s) * b + desired(0.468 s) * (1 - b)        b = 0.50, hardcoded

**Those are not two estimates of one quantity. They are the path at two different places.** Averaging
them drags the aim point back from 0.468 s to roughly 0.33 s while the actuator still takes 0.468 s
to arrive -- so the car is commanded to a curvature the road wanted a tenth of a second ago. On
entry that under-commands, the error grows, the car catches up late, and the road has already
changed: turn in late, sail past, correct. Which is the report.

This measures the loss directly rather than arguing it. During ENTRY (the model path tightening
ahead), it computes what fraction of the compensated command the blend discards:

    loss = (|desired| - |requested|) / |desired|  =  b * (|desired| - |predicted|) / |desired|

A loss near zero means the two horizons happen to agree and the blend is harmless. A loss that grows
with speed is the mechanism, because the taper zeroes the extra lookahead at 55 mph and widens the
gap between the horizons exactly where he reports the symptom.

HANDS OFF, latActive. A hands-ON window is his steering, which is the split that produced the wrong
3.21 m/s^2 figure and contaminated the first ping-pong dump.

Pass --fixed for routes recorded on a build that carries the blend-horizon fix (a98f50a34b and
later). It swaps in the shipped `_t_blend_base` so the reported loss is what those drives ACTUALLY
paid, and on a fixed build that number should collapse toward zero. Running the default (old)
formula against a fixed build measures a car that no longer exists -- the same class of mistake as
scoring a drive against settings it was not driven with.

    python tools/bp_lateral_horizon.py <dir-of-rlog.zst> [--fixed] [route ...]
"""
import bisect
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
DT_MDL = 0.05
BLEND_B = 0.50            # _FORD_PATH_ANGLE_BLEND_RATIO_DEFAULT -- hardcoded, there is no param
VLT_T_EXTRA_MAX = 0.10
VLT_V_LOW_MS = 25.0 * 0.44704
VLT_V_HIGH_MS = 55.0 * 0.44704
VLT_KAPPA_FULL, VLT_KAPPA_TAPER = 0.005, 0.020
MIN_KAPPA = 5e-4
SPEED_BANDS = [(25, 45), (45, 55), (55, 65), (65, 80)]

# openpilot's model time index, needed to interpolate the predicted path.
T_IDXS = [0.0, 0.00976562, 0.0390625, 0.08789062, 0.15625, 0.24414062, 0.3515625,
          0.47851562, 0.625, 0.79101562, 0.9765625, 1.18164062, 1.40625, 1.65039062,
          1.9140625, 2.19726562, 2.5, 2.82226562, 3.1640625, 3.52539062, 3.90625,
          4.30664062, 4.7265625, 5.16601562, 5.625, 6.10351562, 6.6015625, 7.11914062,
          7.65625, 8.21289062, 8.7890625, 9.38476562, 10.0]


def seg_key(p):
  b = os.path.basename(p).split("--")
  try:
    return (b[0], int(b[2].split(".")[0]))
  except (IndexError, ValueError):
    return (os.path.basename(p), 0)


def main():
  d = sys.argv[1]
  argv = [a for a in sys.argv[2:] if a != "--fixed"]
  fixed = "--fixed" in sys.argv
  routes = set(argv)
  files = sorted(glob.glob(os.path.join(d, "*.rlog.zst")), key=seg_key)
  if routes:
    files = [f for f in files if os.path.basename(f).split("--")[0] in routes]

  by_route = collections.OrderedDict()
  for f in files:
    by_route.setdefault(os.path.basename(f).split("--")[0], []).append(f)

  losses = collections.defaultdict(list)
  horizons = collections.defaultdict(list)
  rate_bins = collections.defaultdict(list)

  # Curvature change rate (1/m per s) across the lookahead, bucketed. Bins, not a correlation:
  # a single r against a heavy-tailed variable hides exactly the tail that matters here.
  RATE_EDGES = [0.0005, 0.002, 0.005, 0.015]

  def bin_of(rate):
    for i, e in enumerate(RATE_EDGES):
      if rate < e:
        return i
    return len(RATE_EDGES)

  RATE_NAMES = ["< 0.0005 (straight)", "0.0005-0.002", "0.002-0.005",
                "0.005-0.015", "> 0.015 (fast entry)"]
  lat_action_ts = []
  n_entry = n_exit = 0
  entry_loss, exit_loss = [], []

  for _route, paths in by_route.items():
    lagd = None
    model = []      # (t, curvature profile)
    ctrl = []       # (t, desired)
    car = []        # (t, v, hands)
    delay = []      # (t, lateralDelay)
    t0 = None
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
          if w == "modelV2":
            oz = m.modelV2.orientationRate.z
            if len(oz) >= 17:
              model.append((ts, np.array(oz, dtype=float)))
          elif w == "controlsState":
            ctrl.append((ts, float(m.controlsState.desiredCurvature)))
          elif w == "carState":
            cs = m.carState
            car.append((ts, float(cs.vEgo), bool(cs.steeringPressed)))
          elif w == "liveDelay":
            delay.append((ts, float(m.liveDelay.lateralDelay)))
        except Exception:
          continue

    if not model or not ctrl or not car:
      continue
    lat_action_t = (lagd if lagd else 0.38) + DT_MDL + DT_MDL / 2.0
    lat_action_ts.append(lat_action_t)

    ctrl.sort(); car.sort(); delay.sort(); model.sort()
    ct = [r[0] for r in ctrl]
    cv = [r[1] for r in ctrl]
    vt = [r[0] for r in car]
    vv = [(r[1], r[2]) for r in car]
    dt_ = [r[0] for r in delay]
    dv = [r[1] for r in delay]

    def near(times, values, t, tol=0.06):
      if not times:
        return None
      i = bisect.bisect_left(times, t)
      best = None
      for j in (i - 1, i):
        if 0 <= j < len(times) and abs(times[j] - t) <= tol:
          if best is None or abs(times[j] - t) < abs(times[best] - t):
            best = j
      return None if best is None else values[best]

    for t, oz in model:
      cs = near(vt, vv, t)
      des = near(ct, cv, t)
      if cs is None or des is None:
        continue
      v, hands = cs
      if hands or v < 5.0 or abs(des) < MIN_KAPPA:
        continue
      curvatures = oz / max(0.01, v)
      ld = near(dt_, dv, t, tol=1.0)
      ld = 0.38 if ld is None else ld

      t_base = min(max(ld, 0.1), 0.15) + DT_MDL
      speed_factor = float(np.interp(v, [VLT_V_LOW_MS, VLT_V_HIGH_MS], [1.0, 0.0]))
      kappa_at_t_base = abs(float(np.interp(t_base, T_IDXS[:len(curvatures)], curvatures)))
      if kappa_at_t_base > abs(des):
        kappa_factor = 1.0
      else:
        kappa_factor = float(np.interp(abs(des), [VLT_KAPPA_FULL, VLT_KAPPA_TAPER], [1.0, 0.0]))
      # The blend base. On a fixed build this is the planner's own compensation horizon; before
      # the fix it was the clipped DECISION base, which is the whole defect.
      blend_base = (min(max(ld, 0.1), 0.45) + DT_MDL + DT_MDL / 2.0) if fixed else t_base
      lookup = blend_base + VLT_T_EXTRA_MAX * speed_factor * kappa_factor
      pred = float(np.interp(lookup, T_IDXS[:len(curvatures)], curvatures))
      requested = pred * BLEND_B + des * (1.0 - BLEND_B)

      # `pred` is sampled NEARER than `des`, so comparing them reads the sign of the curvature
      # gradient ahead. |pred| < |des| means curvature RISES with time -- the road is tightening,
      # and we are entering. The inequality is easy to get backwards; it was, once.
      entering = abs(pred) < abs(des)
      loss = (abs(des) - abs(requested)) / abs(des)
      if entering:
        n_entry += 1
        entry_loss.append(loss)
      else:
        n_exit += 1
        exit_loss.append(loss)
      mph = v * MS_TO_MPH
      sb = next((s for s in SPEED_BANDS if s[0] <= mph < s[1]), None)
      if sb is None:
        continue
      losses[sb].append(loss)
      horizons[sb].append((lookup, lat_action_t))
      # How fast is the road changing right here? The blend's cost is a HORIZON error, so it can
      # only bite where the path differs between the two horizons -- i.e. where curvature is moving.
      # If the loss is flat against this, the mechanism cannot be the ping-pong whatever its median.
      rate = abs(float(np.interp(lookup, T_IDXS[:len(curvatures)], curvatures)) -
                 float(np.interp(0.0, T_IDXS[:len(curvatures)], curvatures))) / max(lookup, 1e-3)
      rate_bins[bin_of(rate)].append(loss)

  print("=== WHAT THE PREDICTED/DESIRED BLEND COSTS ===")
  print()
  print("  blend base: %s" % ("FIXED (planner's own horizon)" if fixed else "pre-fix (clipped decision base)"))
  print()
  if lat_action_ts:
    print("  modeld aims the command %.3f s ahead (LagdValueCache + DT_MDL + DT_MDL/2)."
          % statistics.mean(lat_action_ts))
  print("  lateral_angle_ext blends in a model sample from its own, much nearer horizon,")
  print("  at a hardcoded b = %.2f. The two horizons per speed band:" % BLEND_B)
  print()
  print("  %-12s %10s %10s %10s   %14s %10s" %
        ("speed", "model T", "command T", "gap", "cmd lost", "n"))
  for sb in SPEED_BANDS:
    ls = losses.get(sb, [])
    hs = horizons.get(sb, [])
    if len(ls) < 50:
      continue
    lookup = statistics.median(h[0] for h in hs)
    action = statistics.median(h[1] for h in hs)
    print("  %-12s %9.3fs %9.3fs %9.3fs   %13.1f%% %10d" %
          ("%d-%d mph" % sb, lookup, action, action - lookup,
           100.0 * statistics.median(ls), len(ls)))
  print()
  allls = [x for sb in SPEED_BANDS for x in losses.get(sb, [])]
  if allls:
    print("  Across every qualifying frame the blend discards a median %.1f%% of the"
          % (100.0 * statistics.median(allls)))
    print("  compensated command, p90 %.1f%%." % (100.0 * sorted(allls)[int(0.9 * (len(allls) - 1))]))
    print()
    print("  A POSITIVE number means the command sent is SMALLER than the lag-compensated one:")
    print("  the car turns in less than the road asked for, arrives late, and overshoots the")
    print("  correction. A negative number would mean the blend over-commands instead.")
    print()
    print("  THE TWO FACES OF THE SAME MISMATCH:")
    print()
    for name, vals, meaning in (
        ("road TIGHTENING ahead", entry_loss, "we command LESS than asked -> turn in late"),
        ("road OPENING OUT", exit_loss, "we command MORE than asked -> slow unwind")):
      if len(vals) < 50:
        continue
      vals = sorted(vals)
      print("    %-22s median %+6.1f%%  p90 %+6.1f%%  n=%-6d %s"
            % (name, 100.0 * statistics.median(vals),
               100.0 * vals[int(0.9 * (len(vals) - 1))], len(vals), meaning))
    print()
    print("  BY HOW FAST THE ROAD IS CHANGING -- does the loss concentrate where it could matter?")
    print()
    print("  %-24s %10s %10s %10s" % ("curvature change rate", "median", "p90", "n"))
    for i, name in enumerate(RATE_NAMES):
      vals = rate_bins.get(i, [])
      if len(vals) < 50:
        print("  %-24s %10s %10s %10d" % (name, "--", "--", len(vals)))
        continue
      vals.sort()
      print("  %-24s %9.1f%% %9.1f%% %10d"
            % (name, 100.0 * statistics.median(vals),
               100.0 * vals[int(0.9 * (len(vals) - 1))], len(vals)))


if __name__ == "__main__":
  main()
