"""Replay his real drives through the OLD and NEW gain schedules and compare. Read-only.

He asked the right question: *"Make sure that it's actually fixed. See if you can simulate my
drives and see if this fixes it."* This does exactly that -- it does not re-derive the fix, it
re-runs BluePilot PR #192's arithmetic against the curvature and speed his car actually saw.

What PR #192 claims to fix, in the author's words:

    "Initial fast swing made car lurch on entering a curve, and if curve continued near previous
     interp range, oscillations would continue."

So the thing to measure is not the gain's VALUE but how violently it SWINGS. `curvature_factor`
multiplies the command directly (`path_angle = kappa_cmd * v_ego * curvature_factor`), so a factor
that jumps frame to frame throws the command with it, and that is the lurch.

    OLD   blend [13.5, 26.82]   kappa band [0.0007, 0.001]              (fixed, 400 m of radius)
    NEW   blend [11.18, 31.29]  kappa band [0.0005, high_gain_boundary] (speed-dependent, wide)

Reports, over the same frames, hands OFF and lateral active:
    factor swing per second      how hard the multiplier is being thrown around
    resulting path_angle swing   what that does to the actual command, in degrees

HIS SETTINGS ARE READ PER ROUTE from initData rather than assumed, because he changed them midway
through the trip and the two halves are different cars for this purpose.
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
ONLY_BAND = os.environ.get('ONLY_BAND') == '1'

OLD_V_BP = [13.5, 26.82]
NEW_V_BP = [11.18, 31.29]
OLD_K_BP = [0.0007, 0.001]


def interp(x, xp, fp):
  if x <= xp[0]:
    return fp[0]
  if x >= xp[-1]:
    return fp[-1]
  for i in range(1, len(xp)):
    if x <= xp[i]:
      t = (x - xp[i - 1]) / (xp[i] - xp[i - 1])
      return fp[i - 1] + t * (fp[i] - fp[i - 1])
  return fp[-1]


def seg_index(p):
  try:
    return int(os.path.basename(p).split("--")[2].split(".")[0])
  except (IndexError, ValueError):
    return -1


def factors(v, k, low_f, high_f, damp, v_bp, new_band):
  """curvature_factor under one schedule. gain_lowC/highC default to 1.0 as shipped."""
  low_gain = interp(v, v_bp, [1.0, 1.0 * damp])
  high_gain = interp(v, v_bp, [1.30 * low_f, 1.0 * high_f])
  if new_band:
    boundary = interp(v, v_bp, [0.02, 0.0045])
    return interp(abs(k), [0.0005, boundary], [low_gain, high_gain])
  return interp(abs(k), OLD_K_BP, [low_gain, high_gain])


def route_cfg(path):
  cfg = {"FordLowSpeedFactor_ang": 0.92, "FordHighSpeedFactor_ang": 0.83,
         "FordHighSpeedDampening_ang": 0.85}
  try:
    with open(path, "rb") as f:
      raw = zstandard.ZstdDecompressor().stream_reader(f).read()
    for m in log_capnp.Event.read_multiple_bytes(raw, traversal_limit_in_words=2 ** 32):
      if m.which() != "initData":
        continue
      params = {e.key: e.value for e in m.initData.params.entries}
      for k in list(cfg):
        v = params.get(k)
        if v:
          try:
            cfg[k] = float(v.decode().strip())
          except Exception:
            pass
      break
  except Exception:
    pass
  return cfg


def main():
  d = sys.argv[1]
  routes = set(sys.argv[2:])
  files = sorted(glob.glob(os.path.join(d, "*.rlog.zst")), key=seg_index)
  if routes:
    files = [f for f in files if os.path.basename(f).split("--")[0] in routes]

  by_route = collections.OrderedDict()
  for f in files:
    by_route.setdefault(os.path.basename(f).split("--")[0], []).append(f)

  tot = collections.Counter()
  print("=== REPLAY OF HIS DRIVES: OLD vs NEW GAIN SCHEDULE (BluePilot PR #192) ===")
  print()
  print("  %-10s %8s %11s %11s %10s %11s %11s %9s" % (
    "route", "frames", "old fac/s", "new fac/s", "improve", "old cmd/s", "new cmd/s", "improve"))

  for route, paths in by_route.items():
    cfg = route_cfg(paths[0])
    low_f = cfg["FordLowSpeedFactor_ang"]
    high_f = cfg["FordHighSpeedFactor_ang"]
    damp = cfg["FordHighSpeedDampening_ang"]

    lat = hands = False
    v = 0.0
    prev = None
    d_old = d_new = 0.0
    da_old = da_new = 0.0
    n = 0
    secs = 0.0
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
            k = float(m.controlsState.desiredCurvature)
            if not (lat and not hands and v >= 8.0):
              prev = None
              continue
            # FAIR TEST: PR #192 only acts where the OLD schedule was interpolating -- the
            # [0.0007, 0.001] band, radius 1000-1429 m. Averaged over a whole drive its effect is
            # diluted by every straight and every tight corner where both schedules agree. Restrict
            # to the band it targets, which is also where his worst episodes sat (894/1282/1327 m).
            if ONLY_BAND and not (0.0005 <= abs(k) <= 0.0015):
              prev = None
              continue
            fo = factors(v, k, low_f, high_f, damp, OLD_V_BP, False)
            fn = factors(v, k, low_f, high_f, damp, NEW_V_BP, True)
            ao = k * v * fo
            an = k * v * fn
            if prev is not None:
              d_old += abs(fo - prev[0])
              d_new += abs(fn - prev[1])
              da_old += abs(ao - prev[2])
              da_new += abs(an - prev[3])
              secs += 0.01
              n += 1
            prev = (fo, fn, ao, an)
        except Exception:
          continue

    if n < 1000:
      continue
    fo_r, fn_r = d_old / secs, d_new / secs
    ao_r, an_r = math.degrees(da_old / secs), math.degrees(da_new / secs)
    print("  %-10s %8d %11.3f %11.3f %9.0f%% %11.2f %11.2f %8.0f%%" % (
      route, n, fo_r, fn_r, -100.0 * (fn_r - fo_r) / max(fo_r, 1e-9),
      ao_r, an_r, -100.0 * (an_r - ao_r) / max(ao_r, 1e-9)))
    tot["n"] += n
    tot["fo"] += d_old
    tot["fn"] += d_new
    tot["ao"] += da_old
    tot["an"] += da_new
    tot["s"] += secs

  if tot["s"]:
    print()
    fo_r, fn_r = tot["fo"] / tot["s"], tot["fn"] / tot["s"]
    ao_r, an_r = math.degrees(tot["ao"] / tot["s"]), math.degrees(tot["an"] / tot["s"])
    print("  TOTAL      %8d %11.3f %11.3f %9.0f%% %11.2f %11.2f %8.0f%%" % (
      tot["n"], fo_r, fn_r, -100.0 * (fn_r - fo_r) / max(fo_r, 1e-9),
      ao_r, an_r, -100.0 * (an_r - ao_r) / max(ao_r, 1e-9)))
    print()
    print("  fac/s = how much curvature_factor moves per second. It MULTIPLIES the command, so")
    print("  throwing it around throws the steering with it -- that is the lurch the PR names.")
    print("  cmd/s = the same thing expressed as degrees of commanded path angle per second.")
    print()
    print("  A POSITIVE improve MEANS THE NEW SCHEDULE IS CALMER on his own recorded driving.")


if __name__ == "__main__":
  main()
