"""Does the 55 mph lookahead cliff survive MATCHED CURVATURE? Read-only, runs off-device.

Delivery pooled by speed reads 0.81 at 40-50, 0.68 at 50-60, 0.93 at 60-70 -- a dip sitting exactly
on `_VLT_V_HIGH_MS = 55 mph`, where the variable lookahead taper reaches zero. That is suggestive
and it is NOT evidence, because speed and curvature are confounded on a real drive: 50-60 mph is
canyon and arterial, 60-80 mph is interstate, and a gentler curve is easier to deliver whatever the
lookahead is doing. Acting on a pooled number is how three vision-factor changes were spent on a
complaint SCC-Map was causing.

So this holds CURVATURE fixed and varies speed across the line. If the taper is the cause, the same
radius is delivered worse above 55 than below it. If the dip is the confound, the matched rows show
no step and the pooled number is explained by road type.

Two ground truths side by side, because `controlsState.curvature` is the steering angle through a
vehicle model carrying our derived steerRatio, and a steerRatio error would manufacture the whole
finding:

    vehicle model   controlsState.curvature
    IMU             livePose.angularVelocityDevice.z / v_ego   -- owes nothing to steerRatio

STEADY-STATE AND HANDS OFF. During curve entry the actual curvature legitimately lags, so a ratio
taken there measures the transient rather than what the car settles at. And a window that is
hands-ON measures HIS steering -- the split that produced the wrong 3.21 m/s^2 figure and
contaminated the first ping-pong dump.

    python tools/bp_lateral_matched.py <dir-of-rlog.zst> [route ...]
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
STEADY_S = 0.5
STEADY_TOL = 1.5e-4
HZ = 100
MIN_CELL = 50

# The constants under test, from lateral_angle_ext.
VLT_V_LOW_MPH, VLT_V_HIGH_MPH = 25.0, 55.0

# Curvature bands named by the radius they describe. Gentle curves are where he reports the symptom,
# so the bands are dense there and stop short of the hairpins that never ring.
KAPPA_BANDS = [
  (0.0005, 0.0010, "2000-1000 m"),
  (0.0010, 0.0020, "1000- 500 m"),
  (0.0020, 0.0035, " 500- 286 m"),
  (0.0035, 0.0060, " 286- 167 m"),
]
SPEED_BANDS = [(35, 45), (45, 55), (55, 65), (65, 80)]


def seg_key(p):
  b = os.path.basename(p).split("--")
  try:
    return (b[0], int(b[2].split(".")[0]))
  except (IndexError, ValueError):
    return (os.path.basename(p), 0)


def collect(paths):
  ctrl, pose, car = [], [], []
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
        elif w == "livePose":
          pose.append((ts, float(m.livePose.angularVelocityDevice.z)))
        elif w == "carState":
          cs = m.carState
          car.append((ts, float(cs.vEgo), bool(cs.steeringPressed)))
      except Exception:
        continue
  return ctrl, pose, car


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

  cells = collections.defaultdict(list)

  for _route, paths in by_route.items():
    ctrl, pose, car = collect(paths)
    if not ctrl or not car:
      continue
    ctrl.sort()
    pose.sort()
    car.sort()
    pt = [r[0] for r in pose]
    pv = [r[1] for r in pose]
    vt = [r[0] for r in car]
    vv = [(r[1], r[2]) for r in car]

    need = int(STEADY_S * HZ)
    for i in range(need, len(ctrl)):
      t, des, act = ctrl[i]
      k = abs(des)
      band = next((b for b in KAPPA_BANDS if b[0] <= k < b[1]), None)
      if band is None:
        continue
      win = [ctrl[j][1] for j in range(i - need, i + 1)]
      if max(win) - min(win) > STEADY_TOL:
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
      d_vm = act / des
      if not 0.0 < d_vm < 2.5:
        continue
      yaw = nearest(pt, pv, t)
      d_imu = (yaw / v) / des if yaw is not None else None
      cells[(band[2], sb)].append((d_vm, d_imu))

  print("=== DELIVERY AT MATCHED CURVATURE, ACROSS THE %.0f MPH LOOKAHEAD CLIFF ===" % VLT_V_HIGH_MPH)
  print()
  print("  Extra lookahead is FULL at %.0f mph and ZERO at or above %.0f mph." % (VLT_V_LOW_MPH, VLT_V_HIGH_MPH))
  print("  Read ACROSS each row: a real cliff steps DOWN between the 45-55 and 55-65 columns.")
  print()
  hdr = "  %-12s" % "radius"
  for lo, hi in SPEED_BANDS:
    hdr += "  %14s" % ("%d-%d mph" % (lo, hi))
  print(hdr)
  print("  " + "-" * (12 + 16 * len(SPEED_BANDS)))

  for _lo, _hi, name in KAPPA_BANDS:
    row_vm = "  %-12s" % name
    row_imu = "  %-12s" % "  (imu)"
    have_imu = False
    for sb in SPEED_BANDS:
      vals = cells.get((name, sb), [])
      if len(vals) < MIN_CELL:
        row_vm += "  %14s" % ("n=%d" % len(vals))
        row_imu += "  %14s" % ""
        continue
      row_vm += "  %8.3f n=%-3d" % (statistics.median(v for v, _ in vals), min(len(vals), 999))
      imus = [i for _, i in vals if i is not None and 0.0 < i < 2.5]
      if len(imus) >= MIN_CELL:
        have_imu = True
        row_imu += "  %14s" % ("%.3f" % statistics.median(imus))
      else:
        row_imu += "  %14s" % ""
    print(row_vm)
    if have_imu:
      print(row_imu)
  print()

  print("  THE STEP AT THE LINE, per curvature band (below 55 mph vs at/above 55 mph):")
  print()
  steps = []
  for _lo, _hi, name in KAPPA_BANDS:
    below = [v for sb in SPEED_BANDS if sb[1] <= 55 for v, _ in cells.get((name, sb), [])]
    above = [v for sb in SPEED_BANDS if sb[0] >= 55 for v, _ in cells.get((name, sb), [])]
    if len(below) < MIN_CELL or len(above) < MIN_CELL:
      print("    %-12s  insufficient matched data (below n=%d, above n=%d)" % (name, len(below), len(above)))
      continue
    b, a = statistics.median(below), statistics.median(above)
    steps.append(a - b)
    verdict = ("worse above the line" if a - b < -0.05 else
               "BETTER above the line" if a - b > 0.05 else "no step")
    print("    %-12s  below %.3f (n=%-5d)  above %.3f (n=%-5d)  step %+.3f   %s"
          % (name, b, len(below), a, len(above), a - b, verdict))
  print()
  if steps and all(s > -0.05 for s in steps):
    print("  NO band is delivered worse above the line. The pooled-by-speed dip is road type,")
    print("  NOT the taper, and _VLT_V_HIGH_MS is not the cause of it.")
  elif steps and all(s < -0.05 for s in steps):
    print("  EVERY band is delivered worse above the line. That is the taper, not the road.")


if __name__ == "__main__":
  main()
