"""When the tracking error reverses, WHO moved -- the command or the car? Read-only, off-device.

*"It still did the turn too far and then over-correct."* Two completely different mechanisms produce
that feeling and no measurement here has ever separated them:

    the CAR overshoots     desired is smooth, actual sails past it       -> an actuation problem
    the COMMAND reverses   actual is smooth, desired steps back at it    -> a planner problem

They demand opposite fixes, and every metric in this fork so far -- reversal rates, delivery ratios,
the wire diff -- is blind to the difference, because each one collapses the pair into a single
error signal. A dump of one 70 mph episode showed desired stepping 9% back toward straight in a
single frame while actual continued smoothly through it, which is the second shape. This asks
whether that generalizes or whether that frame was a one-off.

METHOD. Walk the error e = actual - desired. At every sign change, measure how far each side moved
across the reversal window and attribute it:

    |d_desired| > 2 * |d_actual|   the COMMAND reversed under a steady car
    |d_actual| > 2 * |d_desired|   the CAR reversed under a steady command
    otherwise                      both moved -- genuinely coupled, attribute nothing

The 2x threshold is deliberate and deliberately blunt: a verdict rendered off a 27% difference is
what produced "THE PSCM IS THE OSCILLATOR", which had to be withdrawn. Anything inside it is
reported as coupled rather than forced into a side.

HANDS OFF, latActive, gentle-to-moderate curves only. A hands-ON window is his steering, which is
the split that produced the wrong 3.21 m/s^2 figure and contaminated the first ping-pong dump.

    python tools/bp_lateral_blame.py <dir-of-rlog.zst> [route ...]
"""
import bisect
import collections
import glob
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
REVERSAL_WIN = 5          # frames either side of the sign change to measure movement over
DOMINANCE = 2.0           # one side must move this many times the other to be blamed
MIN_KAPPA = 5e-4          # ignore near-straights; 2000 m and gentler carries no signal
MIN_ERR = 1.5e-5          # ignore reversals smaller than the quantisation floor
SPEED_BANDS = [(25, 45), (45, 55), (55, 65), (65, 80)]


def seg_key(p):
  b = os.path.basename(p).split("--")
  try:
    return (b[0], int(b[2].split(".")[0]))
  except (IndexError, ValueError):
    return (os.path.basename(p), 0)


def collect(paths):
  ctrl, car = [], []
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
          car.append((ts, float(cs.vEgo), bool(cs.steeringPressed)))
      except Exception:
        continue
  return ctrl, car


def nearest(times, values, t, tol=0.05):
  if not times:
    return None
  i = bisect.bisect_left(times, t)
  best = None
  for j in (i - 1, i):
    if 0 <= j < len(times) and abs(times[j] - t) <= tol:
      if best is None or abs(times[j] - t) < abs(times[best] - t):
        best = j
  return None if best is None else values[best]


def main():
  d = sys.argv[1]
  routes = set(sys.argv[2:])
  files = sorted(glob.glob(os.path.join(d, "*.rlog.zst")), key=seg_key)
  if routes:
    files = [f for f in files if os.path.basename(f).split("--")[0] in routes]

  by_route = collections.OrderedDict()
  for f in files:
    by_route.setdefault(os.path.basename(f).split("--")[0], []).append(f)

  tot = collections.Counter()
  by_speed = collections.defaultdict(collections.Counter)
  cmd_jumps = []

  for _route, paths in by_route.items():
    ctrl, car = collect(paths)
    if not ctrl or not car:
      continue
    ctrl.sort()
    car.sort()
    vt = [r[0] for r in car]
    vv = [(r[1], r[2]) for r in car]

    for i in range(REVERSAL_WIN, len(ctrl) - REVERSAL_WIN):
      t, des, act = ctrl[i]
      if abs(des) < MIN_KAPPA:
        continue
      e_prev = ctrl[i - 1][2] - ctrl[i - 1][1]
      e_now = act - des
      if e_prev == 0 or (e_prev > 0) == (e_now > 0):
        continue
      if max(abs(e_prev), abs(e_now)) < MIN_ERR:
        continue
      cs = nearest(vt, vv, t)
      if cs is None:
        continue
      v, hands = cs
      if hands or v < 5.0:
        continue
      mph = v * MS_TO_MPH
      sb = next((s for s in SPEED_BANDS if s[0] <= mph < s[1]), None)
      if sb is None:
        continue

      a = ctrl[i - REVERSAL_WIN]
      b = ctrl[i + REVERSAL_WIN]
      d_des = abs(b[1] - a[1])
      d_act = abs(b[2] - a[2])

      if d_des > DOMINANCE * d_act:
        verdict = "command"
        cmd_jumps.append((abs(b[1] - a[1]), mph, _route, t))
      elif d_act > DOMINANCE * d_des:
        verdict = "car"
      else:
        verdict = "coupled"
      tot[verdict] += 1
      by_speed[sb][verdict] += 1

  n = sum(tot.values())
  print("=== WHO REVERSED: THE COMMAND, OR THE CAR? ===")
  print()
  if not n:
    print("  no qualifying reversals (hands off, latActive, |kappa| > %.4f)" % MIN_KAPPA)
    return

  print("  %d qualifying error reversals, hands off, on curves tighter than 2000 m." % n)
  print()
  print("  %-10s %8s %7s   %s" % ("verdict", "count", "share", "meaning"))
  for k, meaning in (("command", "desired stepped; the car was steady   -> PLANNER"),
                     ("car", "actual moved; the command was steady  -> ACTUATION"),
                     ("coupled", "both moved together; attribute nothing")):
    print("  %-10s %8d %6.1f%%   %s" % (k, tot[k], 100.0 * tot[k] / n, meaning))
  print()

  print("  BY SPEED (share of attributable reversals blamed on the command):")
  print()
  print("  %-12s %8s %8s %8s %10s" % ("speed", "command", "car", "coupled", "cmd share"))
  for sb in SPEED_BANDS:
    c = by_speed[sb]
    attributable = c["command"] + c["car"]
    if not sum(c.values()):
      continue
    share = ("%.1f%%" % (100.0 * c["command"] / attributable)) if attributable else "--"
    print("  %-12s %8d %8d %8d %10s"
          % ("%d-%d mph" % sb, c["command"], c["car"], c["coupled"], share))
  print()

  if cmd_jumps:
    sizes = [j[0] for j in cmd_jumps]
    print("  COMMAND-SIDE REVERSALS, size of the step in desired curvature (1/m):")
    print("    median %.6f    p90 %.6f    max %.6f"
          % (statistics.median(sizes),
             sorted(sizes)[int(0.9 * (len(sizes) - 1))],
             max(sizes)))
    print()
    print("  the five largest, to dump and read frame by frame:")
    for sz, mph, route, t in sorted(cmd_jumps, reverse=True)[:5]:
      print("    %s  t+%-9.1f %5.1f mph   desired moved %.6f 1/m across %.2f s"
            % (route, t, mph, sz, 2.0 * REVERSAL_WIN / HZ))


if __name__ == "__main__":
  main()
