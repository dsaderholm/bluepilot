"""Find the ACTUAL ping-pong events -- the ones big enough to feel.

EVERY AGGREGATE I COMPUTED TONIGHT WAS MEASURING THE WRONG THING. Reversal rates said the steering
oscillates ~1.2 Hz, which is true, but the amplitude column finally asked how BIG those reversals
are: 0.10-0.30 degrees peak-to-peak. Nobody feels a third of a degree at the wheel. His report --
"it oversteers and then corrects itself and ping pongs" -- is a thing you can SEE the wheel do.

So the events are EPISODIC and my rates averaged them away. A few seconds of real oscillation
inside 300 miles of quiet driving barely moves a per-second mean, which is why every number came
back small and inconclusive.

This looks for episodes instead: windows where the steering angle actually swings, with hands OFF
and lateral active, and reports what the command was doing during each. Ranked by size, because the
ones he remembers are the big ones.
"""
import collections
import glob
import os
import sys

import capnp
import zstandard

REPO = r"C:\Users\D.J. Saderholm\Documents\GitHub\Sandbox\bluepilot-icbm"
capnp.remove_import_hook()
log_capnp = capnp.load(os.path.join(REPO, "cereal", "log.capnp"),
                       imports=[os.path.join(REPO, "cereal")])

MS_TO_MPH = 2.23694
WIN_S = 2.0            # the timescale of a felt wobble
MIN_SWING_DEG = 2.0    # peak-to-peak steering angle that would actually be noticed
MIN_REVERSALS = 3      # and it has to go back and forth, not just turn


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

  found = []
  for route, paths in by_route.items():
    t0 = None
    rows = []
    lat = hands = False
    v = 0.0
    des = 0.0
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
            hands = bool(cs.steeringPressed)
            v = cs.vEgo
            if lat and not hands and v >= 8.0:
              rows.append((mono - t0, float(cs.steeringAngleDeg), v, des))
        except Exception:
          continue

    if len(rows) < 400:
      continue
    hz = 100
    w = int(WIN_S * hz)
    i = 0
    while i + w < len(rows):
      seg = rows[i:i + w]
      angs = [r[1] for r in seg]
      swing = max(angs) - min(angs)
      if swing >= MIN_SWING_DEG:
        rev, prev_dir, last = 0, 0, None
        for a in angs:
          if last is None:
            last = a
            continue
          if abs(a - last) < 0.3:
            continue
          dd = 1 if a > last else -1
          if prev_dir and dd != prev_dir:
            rev += 1
          prev_dir, last = dd, a
        if rev >= MIN_REVERSALS:
          vs = sum(r[2] for r in seg) / len(seg)
          ds = sum(abs(r[3]) for r in seg) / len(seg)
          rad = (1.0 / ds) if ds > 1e-9 else 1e9
          found.append((route, seg[0][0], swing, rev, vs * MS_TO_MPH, rad))
          i += w
          continue
      i += w // 2

  print("=== REAL PING-PONG EPISODES (>= %.0f deg swing, >= %d reversals in %.0fs) ===" % (
    MIN_SWING_DEG, MIN_REVERSALS, WIN_S))
  print()
  if not found:
    print("  NONE FOUND. If the wheel is not swinging degrees while hands are off, what he feels")
    print("  is not in the steering angle -- look at lane position or lateral acceleration next.")
    return
  print("  %-9s %9s %9s %6s %8s %10s" % ("route", "t+", "swing", "revs", "mph", "radius m"))
  for r, t, sw, rev, mph, rad in sorted(found, key=lambda x: -x[2])[:25]:
    print("  %-9s %9.1f %8.2f deg %5d %8.0f %10.0f" % (r, t, sw, rev, mph, min(rad, 99999)))
  print()
  print("  total episodes: %d" % len(found))
  byspeed = collections.Counter()
  for r, t, sw, rev, mph, rad in found:
    byspeed[(int(mph) // 15) * 15] += 1
  print("  by speed: %s" % dict(sorted(byspeed.items())))


if __name__ == "__main__":
  main()
