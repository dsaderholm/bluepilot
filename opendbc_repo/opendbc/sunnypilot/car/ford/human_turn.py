"""
BluePilot: shared manual-steering-override ("human turn") detection for Ford lateral control.

Both Ford lateral strategies hand control back to the driver on a sustained manual turn, and the
detection is identical, so it lives here as one class instead of being copied into each mode. What
each mode DOES with the signal differs: curvature-primary (``lateral_curv_ext``) zeroes its command
(reset_steering + post-reset ramp); angle-primary (``lateral_angle_ext``) forces lateral inactive
(mode 0 on the wire) and ramps path_angle back in from zero through its soft ROC on release.
"""
import math

from opendbc.car import DT_CTRL
from opendbc.car.ford.values import CarControllerParams

# Require sustained hands-on AND a large angle (avoids resetting on small wheel nudges in a curve).
HUMAN_TURN_ANGLE_DEG = 45.0
HUMAN_TURN_HOLD_S = 1.5
# When the wheel was ALREADY past HUMAN_TURN_ANGLE_DEG at first contact -- lateral control had it
# turned mid-curve -- the angle condition is pre-satisfied, so a brief corrective nudge would latch
# after only HUMAN_TURN_HOLD_S of light contact and kill steering mid-curve. Require a longer hold
# there before reading it as an intentional takeover. Route 000000bd (2026-07-14) showed the
# discriminator holds on-road: all 7 deliberate turns began with the wheel below the threshold
# (driver wound it up through 45 deg); only mid-maneuver grabs/nudges began beyond it.
HUMAN_TURN_HOLD_PRETURNED_S = 3.0
_STEER_DT = CarControllerParams.STEER_STEP * DT_CTRL  # 20 Hz lateral tick

# FusionPilot 2026-10-03: a press that AGREES with lateral control's own turn is not a takeover.
# The Fusion's column torque sensor reads the rack carrying out a large command as a push (route
# 4c2, 2026-09-29: "pressed" on a wheel nobody touched), so in every openpilot-driven intersection
# turn the old rule latched near the apex and handed the unwind to caster: on 000004ce/4cf/4d0 all
# 23 overrides had torque WITH the turn and the wheel within ~20 deg of the command, and the wheel
# then sat up to 73 deg past what the model asked for on the way out. A real takeover pushes the
# wheel AGAINST the command or well PAST it, and either still latches. The driver loses nothing:
# the PSCM yields to his hands whether or not lateral is in mode 0.
FOLLOW_WHEEL_SCALE = 1.2      # the PSCM gives more wheel per command at walking pace (4c2: ~1.05x)
FOLLOW_WHEEL_MARGIN_DEG = 15.0


def press_follows_command(wheel_deg: float, torque: float, kappa_cmd: float,
                          steer_ratio: float, wheelbase: float) -> bool:
  """True when the wheel and the driver torque both go the way the commanded curvature turns, and
  the wheel is not past that turn. Curvature + is RIGHT, wheel angle + is LEFT."""
  if not (steer_ratio > 0.0 and wheelbase > 0.0 and math.isfinite(kappa_cmd)):
    return False
  geo = -math.degrees(math.atan(kappa_cmd * wheelbase)) * steer_ratio
  return (wheel_deg * torque > 0.0 and wheel_deg * geo > 0.0
          and abs(wheel_deg) <= abs(geo) * FOLLOW_WHEEL_SCALE + FOLLOW_WHEEL_MARGIN_DEG)


class HumanTurnDetector:
  """Latches ``active`` once the driver holds real steering pressure AND ``|wheel angle|`` >
  ``HUMAN_TURN_ANGLE_DEG`` continuously for ``HUMAN_TURN_HOLD_S`` — long enough to tell an
  intentional turn from a brief nudge. ``just_released`` pulses True on the first frame after the
  override clears, so a mode can re-seed its command as it re-engages.

  Call ``update`` once per lateral tick while control is active. Modes that want the timer to zero
  on disengage call ``reset`` on their inactive path; modes that want it to persist simply stop
  calling ``update`` (the timer holds its value).
  """

  def __init__(self):
    self.hold_timer_s = 0.0
    self.active = False
    self._active_last = False
    self._pressed_last = False
    self._press_started_preturned = False

  def update(self, enabled: bool, steering_pressed: bool, steering_angle_deg: float,
             follows_command: bool = False) -> bool:
    self._active_last = self.active
    # Was the wheel already past the angle threshold when this press began? If so the driver is
    # touching a wheel that lateral control turned (mid-curve nudge), not driving a turn -- hold
    # the longer HUMAN_TURN_HOLD_PRETURNED_S before latching.
    if steering_pressed and not self._pressed_last:
      self._press_started_preturned = abs(steering_angle_deg) > HUMAN_TURN_ANGLE_DEG
    self._pressed_last = steering_pressed
    if not enabled:
      self.hold_timer_s = 0.0
    # follows_command only stops a latch from FORMING. Once latched, the caller publishes the
    # measured curvature as its command, which the wheel always "follows" -- honoring it there would
    # release a genuine takeover on the next tick.
    elif (steering_pressed and abs(steering_angle_deg) > HUMAN_TURN_ANGLE_DEG
          and (self.active or not follows_command)):
      self.hold_timer_s += _STEER_DT
    else:
      self.hold_timer_s = 0.0
    hold_req = HUMAN_TURN_HOLD_PRETURNED_S if self._press_started_preturned else HUMAN_TURN_HOLD_S
    self.active = self.hold_timer_s >= hold_req
    return self.active

  @property
  def just_released(self) -> bool:
    return self._active_last and not self.active

  def reset(self) -> None:
    self.hold_timer_s = 0.0
    self.active = False
    self._active_last = False
    self._pressed_last = False
    self._press_started_preturned = False
