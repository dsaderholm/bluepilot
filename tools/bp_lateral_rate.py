"""Ping-pong episodes per MINUTE OF EXPOSURE, by speed band. Read-only, off-device.

A raw episode count cannot compare two drives. 000003ed produced 1425 episodes against 254 on
000003eb/ec, which reads as a large regression until you notice 1352 of those 1425 are at 60-75 mph
because that drive was mostly interstate, while yesterday's was mixed surface roads. Counting
events without counting the time you were exposed to them measures the ROUTE, not the car.

This divides both by the same denominator: minutes spent hands-off and latActive in each speed
band. That is the only form in which two different drives can be compared at all, and it is the
same denominator error this repo has now recorded four times in other guises (the 70.6% camera
cancel, the 42.8% Ford authority, the 21.79% SCC veto).

The detector is deliberately identical to bp_lateral_episodes.py -- same window, same swing, same
reversal count, same hands-off and latActive gates -- so the two tools cannot disagree about what
an episode is. If that file's thresholds change, change them here.

    python tools/bp_lateral_rate.py <dir-of-rlog.zst> [route ...]
"""
import collections
import glob
import os
import sys

import capnp
import zstandard

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
capnp.remove_import_hook()
log_capnp = capnp.load(os.path.join(REPO, "cereal", "log.capnp"),
                       imports=[os.path.join(REPO, "cereal")])

MS_TO_MPH = 2.23694
WIN_S = 2.0            # identical to bp_lateral_episodes.py
MIN_SWING_DEG = 2.0
MIN_REVERSALS = 3
CS_HZ = 100

SPEED_BANDS = [(8, 30), (30, 45), (45, 60), (60, 75), (75, 95)]


def seg_index(p):
  try:
    return int(os.path.basename(p).split("--")[2].split(".")[0])
  except (IndexError, ValueError):
    return -1


def band_of(mph):
  for lo, hi in SPEED_BANDS:
    if lo <= mph < hi:
      return (lo, hi)
  return None


def main():
  global MIN_SWING_DEG
  d = sys.argv[1]
  args = list(sys.argv[2:])
  # --swing N raises the amplitude bar. The default 2 deg counts every small wobble, and at 12-17
  # episodes/minute the rate is dominated by them -- but the report is about turning too far and
  # over-correcting, which is a big one. Rates at two thresholds answer different questions and
  # the small-swing rate must not be quoted as if it were the felt one.
  if "--swing" in args:
    k = args.index("--swing")
    MIN_SWING_DEG = float(args[k + 1])
    del args[k:k + 2]
  routes = set(args)
  files = sorted(glob.glob(os.path.join(d, "*.rlog.zst")), key=seg_index)
  if routes:
    files = [f for f in files if os.path.basename(f).split("--")[0] in routes]

  exposure = collections.Counter()     # band -> qualifying frames
  episodes = collections.Counter()     # band -> episodes
  by_route = collections.OrderedDict()
  for f in files:
    by_route.setdefault(os.path.basename(f).split("--")[0], []).append(f)

  for _route, paths in by_route.items():
    rows = []
    t0 = None
    lat = hands = False
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
          elif w == "carState":
            cs = m.carState
            hands = bool(cs.steeringPressed)
            v = float(cs.vEgo)
            if lat and not hands and v >= 8.0 / MS_TO_MPH:
              rows.append((mono - t0, float(cs.steeringAngleDeg), v))
        except Exception:
          continue

    rows.sort()
    for _t, _a, v in rows:
      b = band_of(v * MS_TO_MPH)
      if b:
        exposure[b] += 1

    # Same sliding window as bp_lateral_episodes.py, attributed to the band at its start.
    win = int(WIN_S * CS_HZ)
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
      rev = 0
      prev = 0
      for j in range(1, len(angs)):
        dcur = angs[j] - angs[j - 1]
        s = 1 if dcur > 0.02 else (-1 if dcur < -0.02 else 0)
        if s and prev and s != prev:
          rev += 1
        if s:
          prev = s
      if rev >= MIN_REVERSALS:
        b = band_of(sum(r[2] for r in seg) / len(seg) * MS_TO_MPH)
        if b:
          episodes[b] += 1
        i += win          # non-overlapping, so one wobble is one episode
      else:
        i += 1

  print("=== PING-PONG EPISODES PER MINUTE OF EXPOSURE (>= %.0f deg swing) ===" % MIN_SWING_DEG)
  print()
  print("  hands off, latActive. Exposure is time actually spent in the band, which is what makes")
  print("  two different drives comparable at all.")
  print()
  print("  %-14s %12s %12s %14s" % ("speed", "minutes", "episodes", "per minute"))
  tot_min = tot_ep = 0.0
  for b in SPEED_BANDS:
    mins = exposure[b] / CS_HZ / 60.0
    eps = episodes[b]
    tot_min += mins
    tot_ep += eps
    if mins < 0.5:
      continue
    print("  %-14s %12.1f %12d %14.2f" % ("%d-%d mph" % b, mins, eps, eps / mins))
  print("  " + "-" * 54)
  if tot_min > 0:
    print("  %-14s %12.1f %12d %14.2f" % ("ALL", tot_min, int(tot_ep), tot_ep / tot_min))


if __name__ == "__main__":
  main()
