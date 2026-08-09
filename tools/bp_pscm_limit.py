"""BROKEN AS AN ANSWER, KEPT AS A LESSON. Do not use these numbers.

Written 2026-08-08 to find the lateral acceleration at which this car's PSCM stops holding the
commanded angle, by binning the angle error latcontrol_angle computes:

    angle_control_saturated = abs(angle_steers_des - CS.steeringAngleDeg) > 2.5  # degrees

It produced a clean-looking result -- saturation share doubling at 2.0 m/s^2 and reaching 43% by 4.0
-- and two settings defaults were changed on the strength of it. Both are reverted.

WHY IT IS WRONG. angle_steers_des is openpilot's KINEMATICALLY IDEAL wheel angle, from
VM.get_steer_from_curvature. Under BluePilot's angle scheme that is not what gets sent: the PSCM
receives path_angle = 1/2 * kappa * d_ref as c1, scaled by FordLowSpeedFactor_ang /
FordHighSpeedFactor_ang, at the PSCM's ~1.4 m lookahead. Different quantity, different signal, and
the gains here are 0.92 / 0.87 -- deliberately BELOW 1.0, which BluePilot's own release notes
describe as making the wheel turn less.

So the two sides were never meant to agree, and a fixed ~13% shortfall becomes an absolute error in
degrees that GROWS WITH ANGLE SIZE -- which is exactly the rising trend that looked like a capability
limit. The measurement mostly recovered the gain calibration.

It also overturned a correct earlier finding on bad evidence: that the take-over alerts are
tracking-lag artifacts and the angle gains are a PSCM calibration rather than a detune. Both of those
were right.

WHAT A REAL VERSION WOULD COMPARE. The angle actually commanded through lateral_angle_ext -- after
the gain, in the same units the PSCM receives -- against the delivered angle. Anything that reads
actuators.steeringAngleDeg on a car running BluePilot angle control is reading a signal that is not
in the loop.

Left in the tree deliberately. The trap is subtle, the output looked authoritative, and the next
person to reach for latcontrol_angle's error on this car deserves to find this first.
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
