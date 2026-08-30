"""THIS TOOL'S PREMISE IS WRONG. Kept only because the correction is the finding. Read-only.

**It does NOT find the car abandoning a curve. It finds the car LAGGING A RELEASE.** Checked on the
wire (000003ed t+4792-4798, LateralMotionControl 0x3D3 decoded from sendcan) for the worst event it
reported, and the order of events is the opposite of what this tool assumes:

    t+4793.0   path_angle -0.110   desired 3.96   actual 3.82   steer -15.3 deg
    t+4793.8   path_angle -0.067   desired 2.53   actual 4.07   steer -16.2 deg
    t+4794.4   path_angle -0.034   desired 1.47   actual 3.07   steer -12.3 deg
    t+4794.6   path_angle -0.030   desired 1.48   actual 0.83   steer  -3.9 deg

`desired` collapsed FIRST, from 3.96 to 1.47. The commanded `path_angle` followed it down. The car
followed the command. Nothing abandoned anything and no limiter fired -- measured directly:
inside these windows `curvatureDeviationLimited` is 0.13%, `humanTurnLateralPaused` 0.00%,
`angleRateLimited` 1.14%, `stallBlipActive` 1.98%, so ~97% of the frames have no limiter at all.

**The detector's own guard is what fooled it.** `a0 >= 0.75 * d0` was meant to mean "the car had
reached the curve". At t+4794.1 the car was at 4.07 against a desired of 2.01 -- it was ABOVE
desired, lagging a release that had already happened, and that passes a >= test just as well as
genuinely holding the curve does. The KEEP_FRAC test then measured desired over a window that began
after the collapse, so desired looked steady.

So the 126 events on 000003ed are the **exit half of the oscillation**: the plan swings, the command
tracks it faithfully, and the car arrives ~0.4 s late, which carries `actual` past `desired` on
every reversal. That is the same limit cycle the rest of this file describes, seen from the other
side -- not a second mechanism.

**AND IT KILLS THE FEEDBACK FIX THIS WAS ABOUT TO JUSTIFY.** There is no delivery shortfall to
integrate away in these events: the car delivered 4.07 when asked for 2.01. An integral term on
(desired - actual) would push HARDER into exactly the excursions that hurt.

The general lesson, and it is the third instance in this file: **a detector whose qualifying test
can be satisfied by the opposite of the phenomenon will report the opposite of the phenomenon.**
Check a top-ranked event against raw data before believing a rate.

--- original docstring follows, describing what this was MEANT to find ---

Find the SNAP-BACK: the car abandons a curve the plan is still asking for.

*"The snap back on high gains is where it really gets bad. I'd find more examples to confirm that
this is happening."*

Seen on 000003ed t+7748: delivered curvature had tracked up to -2.43 1/km, matching the command,
and then inside half a second fell to -0.83 while the plan was still asking for -1.8 to -2.2. The
car simply stopped holding the curve. That is not lag and it is not gain -- a lagging car arrives
late, a low-gain car arrives short, and NEITHER walks away from a curve it had already reached.

    a SNAP-BACK is:  |actual| falls by >= DROP_FRAC of its own value within WINDOW_S
                     while |desired| stays at or above KEEP_FRAC of what it was
                     i.e. the plan did NOT release the curve -- only the car did

Reported per minute of curve-holding exposure so two drives can be compared (raw counts compare the
route, not the car -- see bp_lateral_rate.py).

HANDS OFF and latActive throughout the whole event, so a driver nudge cannot be mistaken for one.

    python tools/bp_lateral_snapback.py <dir-of-rlog.zst> [route ...]
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
WINDOW_S = 0.6          # how fast the abandonment has to be
DROP_FRAC = 0.35        # |actual| must lose this much of itself
KEEP_FRAC = 0.80        # while |desired| holds at least this much of its value
MIN_KAPPA = 1.0e-3      # only from a genuinely held curve (radius tighter than 1000 m)
MIN_V = 40.0 / MS_TO_MPH


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
    lat = False
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
          if w == "carControl":
            lat = bool(m.carControl.latActive)
          elif w == "controlsState":
            cs = m.controlsState
            ctrl.append((ts, float(cs.desiredCurvature), float(cs.curvature), lat))
          elif w == "carState":
            cs = m.carState
            car.append((ts, float(cs.vEgo), bool(cs.steeringPressed), float(cs.steeringAngleDeg)))
        except Exception:
          continue

    if len(ctrl) < 5000 or not car:
      continue
    ctrl.sort()
    car.sort()
    ts = np.array([r[0] for r in ctrl])
    des = np.array([r[1] for r in ctrl])
    act = np.array([r[2] for r in ctrl])
    latv = np.array([r[3] for r in ctrl], dtype=bool)
    ct = np.array([r[0] for r in car])
    idx = np.clip(np.searchsorted(ct, ts), 0, len(ct) - 1)
    v = np.array([r[1] for r in car])[idx]
    hands = np.array([r[2] for r in car], dtype=bool)[idx]
    ang = np.array([r[3] for r in car])[idx]

    w = int(WINDOW_S * HZ)
    held = (np.abs(des) >= MIN_KAPPA) & (v >= MIN_V) & (~hands) & latv
    exposure_min = float(held.sum()) / HZ / 60.0

    events = []
    i = w
    while i + w < len(ts):
      if not held[i]:
        i += 1
        continue
      a0, a1 = abs(act[i]), abs(act[i + w])
      d0, d1 = abs(des[i]), abs(des[i + w])
      # The car had actually reached the curve before abandoning it.
      if a0 < 0.75 * d0:
        i += 1
        continue
      if a0 > 0 and (a0 - a1) / a0 >= DROP_FRAC and d1 >= KEEP_FRAC * d0 and hands[i:i + w].sum() == 0:
        events.append((ts[i], a0, a1, d0, d1, v[i] * MS_TO_MPH,
                       float(np.ptp(ang[i:i + w]))))
        i += w * 2
      else:
        i += 1

    print("=== %s : SNAP-BACKS (car abandons a curve the plan still wants) ===" % route)
    print()
    print("  curve-holding exposure, hands off, >= %.0f mph : %.1f min" % (MIN_V * MS_TO_MPH, exposure_min))
    print("  snap-backs found : %d" % len(events))
    if exposure_min > 0:
      print("  per minute of curve holding : %.2f" % (len(events) / exposure_min))
    if events:
      drops = [(e[1] - e[2]) / e[1] for e in events]
      print("  median |actual| lost : %.0f%%   worst %.0f%%"
            % (100 * statistics.median(drops), 100 * max(drops)))
      print()
      print("  %10s %8s %8s %8s %8s %7s %10s" %
            ("t+", "act was", "act ->", "des was", "des ->", "mph", "steer p2p"))
      for e in sorted(events, key=lambda x: -((x[1] - x[2]) / x[1]))[:12]:
        print("  %10.1f %8.2f %8.2f %8.2f %8.2f %7.0f %9.1f deg" %
              (e[0], e[1] * 1000, e[2] * 1000, e[3] * 1000, e[4] * 1000, e[5], e[6]))
    print()


if __name__ == "__main__":
  main()
