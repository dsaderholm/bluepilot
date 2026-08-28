"""Is the ~87% "under-delivery" real, or is our steerRatio just wrong? Read-only.

The delivery measurement said actual curvature is a median ~87% of desired, dipping to 65% at
50-60 mph, and that looked like the car failing to deliver what it is commanded. **But
`controlsState.curvature` is NOT MEASURED.** It is computed from the steering angle through the
vehicle model, which uses `steerRatio` -- and steerRatio on this car is OURS and is DERIVED, not
measured: 17.07, rescaled from a paramsd estimate of 17.23 that was learned while the car was
configured with the Edge's 2.824 m wheelbase.

If steerRatio is ~15% off, `curvature` reads ~15% low on every frame and the entire under-delivery
finding is an artifact of the conversion rather than anything the car did.

THE IMU SETTLES IT. `livePose.angularVelocityDevice.z` is the yaw rate straight off the gyro, and

    true curvature = yaw_rate / v

owes nothing to steerRatio, wheelbase or the vehicle model. Three quantities on the same frames:

    desired      what the controller asked for
    vehicle      controlsState.curvature -- steerRatio-derived, the suspect
    IMU          yaw_rate / v -- ground truth

    IMU tracks DESIRED, vehicle reads low   -> steerRatio is wrong, there is NO under-delivery,
                                               and the delivery finding must be withdrawn
    IMU tracks VEHICLE, both read low       -> the car really is under-delivering and the finding
                                               stands

Restricted to steady-state (desired stable, so entry transients are excluded) and to real curves,
hands off, lateral active -- the same filter the delivery tool used, so the two are comparable.
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
STEADY_S = 0.5
STEADY_TOL = 1.5e-4
MIN_KAPPA = 3e-4
HZ = 100


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

  lat = hands = False
  v = 0.0
  yaw = None
  hist = collections.deque(maxlen=int(STEADY_S * HZ))
  veh_ratio, imu_ratio, imu_vs_veh = [], [], []
  by_speed = collections.defaultdict(lambda: ([], []))

  for p in files:
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
        elif w == "livePose":
          lp = m.livePose
          if lp.sensorsOK:
            yaw = float(lp.angularVelocityDevice.z)
        elif w == "controlsState":
          cs = m.controlsState
          des, veh = float(cs.desiredCurvature), float(cs.curvature)
          hist.append(des)
          if yaw is None or not (lat and not hands and v >= 8.0):
            continue
          if abs(des) < MIN_KAPPA or len(hist) < hist.maxlen:
            continue
          if (max(hist) - min(hist)) > STEADY_TOL:
            continue
          imu = yaw / max(v, 1e-3)
          r_v = veh / des
          r_i = imu / des
          if 0.0 < r_v < 3.0 and -1.0 < r_i < 3.0:
            veh_ratio.append(r_v)
            imu_ratio.append(r_i)
            if abs(veh) > 1e-9:
              imu_vs_veh.append(imu / veh)
            k = (int(v * MS_TO_MPH) // 10) * 10
            by_speed[k][0].append(r_v)
            by_speed[k][1].append(r_i)
      except Exception:
        continue

  def med(a):
    if not a:
      return float("nan")
    b = sorted(a)
    return b[len(b) // 2]

  print("=== IS THE UNDER-DELIVERY REAL, OR IS steerRatio WRONG? ===")
  print()
  print("  frames: %d" % len(veh_ratio))
  if not veh_ratio:
    print("  no qualifying frames (livePose may be absent from these logs)")
    return
  print()
  print("  vehicle-model curvature / desired : %.3f   <- the 'under-delivery' figure" % med(veh_ratio))
  print("  IMU yaw-rate curvature  / desired : %.3f   <- ground truth, no steerRatio involved" % med(imu_ratio))
  print("  IMU / vehicle-model               : %.3f   <- >1 means the model reads LOW" % med(imu_vs_veh))
  print()
  print("  %-12s %8s %12s %12s" % ("speed", "frames", "vehicle/des", "IMU/des"))
  for k in sorted(by_speed):
    a, b = by_speed[k]
    if len(a) < 200:
      continue
    print("  %3d-%-3d mph %8d %12.3f %12.3f" % (k, k + 10, len(a), med(a), med(b)))
  print()
  iv = med(imu_vs_veh)
  if iv > 1.08:
    print("  >>> THE IMU SEES MORE TURNING THAN THE VEHICLE MODEL REPORTS (%.0f%% more)." % (100 * (iv - 1)))
    print("  >>> steerRatio is too HIGH, `curvature` reads low, and the under-delivery finding is")
    print("  >>> at least partly an artifact. steerRatio 17.07 is ours and derived, not measured.")
  elif iv < 0.92:
    print("  >>> the vehicle model reports MORE turning than the IMU measures -- steerRatio too low.")
  else:
    print("  >>> the two agree within 8%%. steerRatio is fine and the under-delivery is REAL.")


if __name__ == "__main__":
  main()
