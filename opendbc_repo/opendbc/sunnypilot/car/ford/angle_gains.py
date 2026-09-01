"""FusionPilot: the per-platform angle-mode gain anchors, and where the gain ramp goes flat.

Split out of `lateral_angle_ext.py` on 2026-09-01 so the SETTINGS SCREEN can compute the flat point
for the car it is actually running on. This module imports only `CAR` from opendbc's Ford values --
deliberately nothing heavy -- because the UI process has no business pulling the whole car layer in
to render a description.

WHY THE FLAT POINT IS PLATFORM-SPECIFIC, which is the bug this file exists to prevent:

    curvature_factor = interp(|kappa_cmd|, [0.0005, boundary], [low_gain, high_gain])
    low_gain  -> 1.00 * FordHighSpeedDampening_ang        at high speed
    high_gain -> anchor_high * FordHighSpeedFactor_ang    at high speed

so the ramp is flat when `anchor_high * High == Dampening`, i.e. at
`High = Dampening / anchor_high`. **`anchor_high` is 1.15 on a CAN Ford, 0.95 on a CAN-FD truck and
1.05 on a CAN-FD unibody SUV**, so one High value is flat on one platform and INVERTED on another.

An inverted ramp means the car steers LESS the harder the road bends -- calm on curves and no
authority on tight ones, which are the same fact and are what a 600-mile drive on this branch
diagnosed the hard way. The settings screen hardcoding 1.15 would have told an F-150 owner a flat
point that is nothing of the sort, which is worse than telling them nothing because they would tune
to it.
"""
from opendbc.car.ford.values import CAR

# Hard-coded per-platform gain defaults: (low-curvature anchor, high-curvature anchor).
# CAN vehicles (Escape MK4, Bronco Sport, Explorer, Maverick, Edge, Fusion MK5)
GAIN_CAN = (1.00, 1.15)
# CAN-FD body-on-frame trucks (F-150, Lightning, Expedition, Ranger)
GAIN_CANFD_BOF = (0.95, 0.95)
# CAN-FD unibody SUVs (Mustang Mach-E, Escape MK4.5)
GAIN_CANFD_SUV = (1.00, 1.05)

CANFD_BOF_CARS = frozenset({
  CAR.FORD_F_150_MK14,
  CAR.FORD_F_150_LIGHTNING_MK1,
  CAR.FORD_EXPEDITION_MK4,
  CAR.FORD_RANGER_MK2,
})
CANFD_SUV_CARS = frozenset({
  CAR.FORD_MUSTANG_MACH_E_MK1,
  CAR.FORD_ESCAPE_MK4_5,
})


def gain_pair(fingerprint) -> tuple[float, float]:
  """(low anchor, high anchor) for a platform. Unknown fingerprints fall back to the CAN pair,
  which is what `update_angle_params` has always done for anything not in the two CAN-FD sets."""
  if fingerprint in CANFD_BOF_CARS:
    return GAIN_CANFD_BOF
  if fingerprint in CANFD_SUV_CARS:
    return GAIN_CANFD_SUV
  return GAIN_CAN


def flat_high_speed_factor(dampening: float, fingerprint) -> float:
  """The `FordHighSpeedFactor_ang` at which gain stops depending on curve size, for THIS car.

  IT MOVES WITH DAMPENING as well as with the platform, which is the whole reason it is shown on
  the settings screen: change dampening alone and the other knob's meaning silently re-tilts.
  """
  _, anchor_high = gain_pair(fingerprint)
  return float(dampening) / float(anchor_high)
