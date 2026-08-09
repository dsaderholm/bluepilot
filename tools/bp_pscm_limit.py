"""Read-only: at what lateral acceleration does this car's PSCM stop holding the commanded angle?

Every corner speed in the stack is computed from a COMFORT lateral-accel limit. This car's binding
limit is different and lower: the retrofit PSCM's authority. His words -- "the only reason I want to
go slow into curves is because of my stupid Ford PSCM limits."

latcontrol_angle.py measures exactly that, every frame:

    angle_control_saturated = abs(angle_steers_des - CS.steeringAngleDeg) > 2.5  # degrees

So bucket the tracking error by lateral acceleration and find where it breaks down. Lateral accel is
derived from the steering angle rather than a yaw sensor, because the question is about the angle
loop and that keeps both sides of the comparison in the same frame of reference.

RESULT, route 0000032f, 53484 engaged frames, path-angle mode:

    1.0-1.5 m/s^2   7.9% of frames saturated
    1.5-2.0         8.5%
    2.0-2.5        16.7%   <- doubles
    2.5-3.0        18.7%
    3.5-4.0        22.0%
    4.0-4.5        43.2%

~2.0 m/s^2 is where this PSCM starts losing the line, and _A_LAT_REG_MAX is 2.0 -- sunnypilot's
comfort limit and this car's steering limit are the same number. HighSpeedFactor and MapFactor were
set from this rather than by feel; see common/params_keys.h.

NOBODY HAS PUBLISHED THIS. openpilot's Ford wiki only says path angle "might be able to achieve
better control from the PSCM", and comma-steering-control maps lateral accel against steering TORQUE,
which is a different question for a different kind of car. An Edge PSCM in a Fusion is a combination
nobody else drives, so this measurement is the only characterization that will ever exist for it.

Re-run it after changing FordPrefLateralControl, the angle gains, or anything else lateral. The
number is specific to path-angle mode; curvature control would give out sooner.

USAGE, on the device:

    cd /data/openpilot && python tools/bp_pscm_limit.py <route>

The two low bands are worth ignoring: the worst errors sit at near-zero cornering, which is a
transient when the commanded angle jumps rather than a capability limit, and those are the
aggressive false positives behind "Take Control -- Turn Exceeds Steering Limit".
"""
import math
import os
import sys

from openpilot.tools.lib.logreader import LogReader

REALDATA = "/data/media/0/realdata"
STEER_RATIO = 17.07      # FORD_FUSION_MK5 CarSpecs
WHEELBASE = 2.85
SAT_THRESHOLD = 2.5      # degrees, STEER_ANGLE_SATURATION_THRESHOLD
MIN_V = 8.0              # m/s; below this the geometry is noisy and nobody cares

route = sys.argv[1]
segs = sorted(d for d in os.listdir(REALDATA) if d.startswith(route + "--"))

# lat-accel bucket -> [frames, saturated-frames, worst error]
buckets: dict[int, list] = {}
desired = None
n = 0

for seg in segs:
  path = None
  for name in ("rlog", "rlog.zst", "rlog.bz2"):
    p = os.path.join(REALDATA, seg, name)
    if os.path.exists(p):
      path = p
      break
  if path is None:
    continue
  for msg in LogReader(path):
    w = msg.which()
    try:
      if w == "carControl":
        act = msg.carControl.actuators
        if msg.carControl.latActive:
          desired = float(act.steeringAngleDeg)
        else:
          desired = None
      elif w == "carState" and desired is not None:
        cs = msg.carState
        v = cs.vEgo
        if v < MIN_V or cs.steeringPressed:
          continue
        actual = float(cs.steeringAngleDeg)
        # Lateral accel the CAR IS BEING ASKED FOR, from the commanded angle -- not the delivered
        # one. Using the actual angle would understate it exactly when the PSCM is failing, which is
        # the case being measured.
        curv = math.tan(math.radians(desired / STEER_RATIO)) / WHEELBASE
        lat = abs(v * v * curv)
        err = abs(desired - actual)
        b = int(lat * 2) / 2.0          # 0.5 m/s^2 buckets
        e = buckets.setdefault(b, [0, 0, 0.0])
        e[0] += 1
        e[1] += 1 if err > SAT_THRESHOLD else 0
        e[2] = max(e[2], err)
        n += 1
    except Exception:  # noqa: BLE001
      continue

print(f"# route {route}: {len(segs)} segments, {n} engaged frames above {MIN_V} m/s\n")
print("  latAccel   frames   over-2.5deg   worst err   share")
for b in sorted(buckets):
  frames, sat, worst = buckets[b]
  if frames < 40:                       # too few to mean anything
    continue
  print(f"  {b:4.1f}-{b + 0.5:<4.1f} {frames:8d} {sat:12d} {worst:10.1f} deg {100.0 * sat / frames:6.1f}%")

print("\n# The lowest band where the share climbs and stays up is where the PSCM starts losing it.")
print("# That number, not a comfort limit, is what corner speeds on this car should be built from.")
