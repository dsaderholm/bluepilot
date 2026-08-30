"""WHICH curves does it actually struggle on? Read-only, off-device.

*"You should know what curves it struggles on because you have all the logs."* Correct, and asking
him was the wrong move. This bins ping-pong episodes by the RADIUS of the curve they happened on and
divides by exposure, so the answer is a rate per curve class rather than a count dominated by
whichever radius he happened to drive most.

TWO MEASURES, because they disagree and the disagreement is the finding:

    absolute swing     degrees at the wheel. What he physically feels.
    swing / nominal    the same swing divided by the steering that curve actually REQUIRES
                       (nominal = atan(kappa * WB) * SR). A 28 deg swing on a 1500 m curve that
                       needs 1.8 deg is a controller failing; the same 28 deg on a 100 m curve that
                       needs 28 deg is just cornering.

A count of episodes cannot separate those, which is why the earlier top-N list -- 86 deg at 98 m
next to 15 deg at 1271 m -- read as "tight curves are worst" when normalising says otherwise.

Hands off, latActive, and only windows where the road is genuinely bent for the whole window.

    python tools/bp_lateral_by_radius.py <dir-of-rlog.zst> [route ...]
"""
import collections
import glob
import math
import os
import statistics
import sys

import capnp
import zstandard

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
capnp.remove_import_hook()
log_capnp = capnp.load(os.path.join(REPO, "cereal", "log.capnp"),
                       imports=[os.path.join(REPO, "cereal")])

MS_TO_MPH = 2.23694
HZ = 100
WIN_S = 2.0
MIN_SWING_DEG = 2.0
MIN_REVERSALS = 3
SR, WB = 17.07, 2.85
MIN_V = 8.0 / MS_TO_MPH

# Radius bands, tight -> open. Named by what they are on his roads.
BANDS = [
  (0, 200, "under 200 m  (intersections, tight ramps)"),
  (200, 500, "200-500 m    (ramps, canyon)"),
  (500, 1000, "500-1000 m   (fast sweeper)"),
  (1000, 2000, "1000-2000 m  (large highway curve)"),
  (2000, 100000, "over 2000 m  (very large / near straight)"),
]


def seg_index(p):
  try:
    return int(os.path.basename(p).split("--")[2].split(".")[0])
  except (IndexError, ValueError):
    return -1


def band_of(r):
  for lo, hi, name in BANDS:
    if lo <= r < hi:
      return name
  return None


def nominal_deg(radius_m):
  """Steering the curve itself requires, so a swing can be scored against it."""
  return math.degrees(math.atan((1.0 / max(radius_m, 1.0)) * WB) * SR)


def main():
  d = sys.argv[1]
  routes = set(sys.argv[2:])
  files = sorted(glob.glob(os.path.join(d, "*.rlog.zst")), key=seg_index)
  if routes:
    files = [f for f in files if os.path.basename(f).split("--")[0] in routes]

  by_route = collections.OrderedDict()
  for f in files:
    by_route.setdefault(os.path.basename(f).split("--")[0], []).append(f)

  exposure = collections.Counter()
  episodes = collections.defaultdict(list)

  for _route, paths in by_route.items():
    rows = []
    t0 = None
    lat = False
    des = 0.0
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
        w = m.which()
        try:
          if w == "carControl":
            lat = bool(m.carControl.latActive)
          elif w == "controlsState":
            des = float(m.controlsState.desiredCurvature)
          elif w == "carState":
            cs = m.carState
            if lat and not bool(cs.steeringPressed) and float(cs.vEgo) >= MIN_V:
              rows.append((mono - t0, float(cs.steeringAngleDeg), float(cs.vEgo), des))
        except Exception:
          continue

    rows.sort()
    for _t, _a, _v, dc in rows:
      if abs(dc) > 1e-6:
        b = band_of(1.0 / abs(dc))
        if b:
          exposure[b] += 1

    win = int(WIN_S * HZ)
    i = 0
    while i + win < len(rows):
      seg = rows[i:i + win]
      if seg[-1][0] - seg[0][0] > WIN_S * 2:
        i += 1
        continue
      angs = [r[1] for r in seg]
      swing = max(angs) - min(angs)
      if swing < MIN_SWING_DEG:
        i += 1
        continue
      rev, prev = 0, 0
      for j in range(1, len(angs)):
        dcur = angs[j] - angs[j - 1]
        s = 1 if dcur > 0.02 else (-1 if dcur < -0.02 else 0)
        if s and prev and s != prev:
          rev += 1
        if s:
          prev = s
      if rev >= MIN_REVERSALS:
        kap = statistics.median([abs(r[3]) for r in seg])
        if kap > 1e-6:
          r_m = 1.0 / kap
          b = band_of(r_m)
          if b:
            episodes[b].append((swing, swing / max(nominal_deg(r_m), 0.05),
                                statistics.mean([r[2] for r in seg]) * MS_TO_MPH))
        i += win
      else:
        i += 1

  print("=== WHERE IT STRUGGLES, BY CURVE RADIUS ===")
  print()
  print("  %-42s %8s %7s %9s %10s %9s" %
        ("curve radius", "minutes", "eps", "per min", "med swing", "x nominal"))
  for _lo, _hi, name in BANDS:
    mins = exposure[name] / HZ / 60.0
    eps = episodes.get(name, [])
    if mins < 0.3:
      print("  %-42s %8.1f %7d %9s %10s %9s" % (name, mins, len(eps), "--", "--", "--"))
      continue
    sw = statistics.median([e[0] for e in eps]) if eps else 0.0
    rel = statistics.median([e[1] for e in eps]) if eps else 0.0
    print("  %-42s %8.1f %7d %9.2f %9.1f deg %8.1fx" %
          (name, mins, len(eps), len(eps) / mins, sw, rel))
  print()
  print("  'x nominal' is the swing divided by the steering that curve actually requires.")
  print("  A high multiple means the wheel is moving far more than the road asks for --")
  print("  which is the complaint. A low multiple on a big swing is just a tight corner.")


if __name__ == "__main__":
  main()
