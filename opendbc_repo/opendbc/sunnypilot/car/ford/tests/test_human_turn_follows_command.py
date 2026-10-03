"""FusionPilot 2026-10-03: a press that rides along with openpilot's own turn is not a takeover.

On 000004ce/4cf/4d0 every human-turn override latched near the apex of an openpilot-driven turn on
column torque WITH the turn and a wheel within ~20 deg of the command -- the sensor reading the rack
carrying out the turn -- and mode 0 then left the unwind to caster. These pin the narrower rule: a
press that agrees with the command never latches, one that pushes against it or winds past it does,
and a latch formed by a real takeover is not released by the wheel "agreeing" with the measured
curvature published during it.
"""
import unittest

from opendbc.sunnypilot.car.ford.human_turn import (
  HUMAN_TURN_HOLD_S, HUMAN_TURN_HOLD_PRETURNED_S, FOLLOW_WHEEL_MARGIN_DEG, FOLLOW_WHEEL_SCALE, HumanTurnDetector, _STEER_DT,
  press_follows_command,
)
from opendbc.sunnypilot.car.ford.tests.test_lateral_angle_ext import (
  _Actuators, _CC, _CS, _Harness, _explorer_cp,
)

SR, WB = 17.07, 2.85
KAPPA_LEFT = -0.02   # + is RIGHT; geo ~ +56 deg at the wheel
DESIRED_LEFT = -0.10  # actuators.curvature as the harness takes it: negative winds the wheel LEFT


def _geo(kappa, sr=SR, wb=WB):
  import math
  return -math.degrees(math.atan(kappa * wb)) * sr


class TestPressFollowsCommand(unittest.TestCase):
  def test_riding_along_follows(self):
    g = _geo(KAPPA_LEFT)
    self.assertTrue(press_follows_command(g, +2.5, KAPPA_LEFT, SR, WB))
    self.assertTrue(press_follows_command(g * 0.3, +2.5, KAPPA_LEFT, SR, WB))   # wheel lagging the command

  def test_torque_against_the_turn_is_a_takeover(self):
    self.assertFalse(press_follows_command(_geo(KAPPA_LEFT), -2.5, KAPPA_LEFT, SR, WB))

  def test_wheel_past_the_command_is_a_takeover(self):
    past = abs(_geo(KAPPA_LEFT)) * FOLLOW_WHEEL_SCALE + FOLLOW_WHEEL_MARGIN_DEG + 1.0
    self.assertFalse(press_follows_command(past, +2.5, KAPPA_LEFT, SR, WB))

  def test_wheel_opposite_the_command_is_a_takeover(self):
    self.assertFalse(press_follows_command(-60.0, -2.5, KAPPA_LEFT, SR, WB))

  def test_no_command_or_bad_params_never_follow(self):
    self.assertFalse(press_follows_command(60.0, +2.5, 0.0, SR, WB))
    self.assertFalse(press_follows_command(60.0, +2.5, KAPPA_LEFT, 0.0, WB))
    self.assertFalse(press_follows_command(60.0, +2.5, float('nan'), SR, WB))


class TestDetector(unittest.TestCase):
  N_HOLD = int(round(HUMAN_TURN_HOLD_S / _STEER_DT))

  def _run(self, det, ticks, follows, angle=120.0):
    for _ in range(ticks):
      det.update(True, True, 10.0 if det.hold_timer_s == 0 and not det._pressed_last else angle, follows)
    return det.active

  def test_following_press_never_latches(self):
    self.assertFalse(self._run(HumanTurnDetector(), 3 * self.N_HOLD, follows=True))

  def test_takeover_still_latches(self):
    self.assertTrue(self._run(HumanTurnDetector(), self.N_HOLD + 2, follows=False))

  def test_latched_override_is_not_released_by_following(self):
    det = HumanTurnDetector()
    self.assertTrue(self._run(det, self.N_HOLD + 2, follows=False))
    self.assertTrue(self._run(det, self.N_HOLD, follows=True))

  def test_default_argument_is_the_old_rule(self):
    det = HumanTurnDetector()
    det.update(True, True, 10.0)
    for _ in range(self.N_HOLD + 2):
      det.update(True, True, 120.0)
    self.assertTrue(det.active)


class TestAngleModeEndToEnd(unittest.TestCase):
  """Through update_angle_strategy with the real detector, 11 mph, a left wound in over 2 s."""
  V = 5.0

  def setUp(self):
    self.CP = _explorer_cp()
    self.ext = _Harness(self.CP)
    self.cs = _CS(vEgoRaw=self.V, vEgo=self.V)
    for k in range(60):   # ramp the desired curvature in gradually -- a one-frame jump trips limits
      self._tick(DESIRED_LEFT * min(1.0, k / 40.0), pressed=False, torque=0.0, wheel=None)
    self.geo = _geo(self.ext.follow_kappa_cmd, self.CP.steerRatio, self.CP.wheelbase)
    self.assertGreater(self.geo, 60.0, "setup must command a real left")

  def _tick(self, desired, pressed, torque, wheel):
    self.cs.out.steeringPressed = pressed
    self.cs.out.steeringTorque = torque
    if wheel is None:
      wheel = _geo(self.ext.follow_kappa_cmd, self.CP.steerRatio, self.CP.wheelbase)
    self.cs.out.steeringAngleDeg = wheel
    self.cs.out.yawRate = 0.0
    return self.ext.update_angle_strategy(_CC(latActive=True), self.cs, _Actuators(curvature=desired), self.CP)

  def _press(self, torque, wheel_scale, seconds):
    res = None
    for _ in range(int(seconds / _STEER_DT)):
      res = self._tick(DESIRED_LEFT, True, torque, self.geo * wheel_scale)
    return res

  def test_rack_press_with_the_turn_keeps_openpilot_steering(self):
    res = self._press(+2.5, 1.0, 2 * HUMAN_TURN_HOLD_PRETURNED_S)
    self.assertFalse(self.ext.angle_human_turn_active)
    self.assertNotEqual(res.path_angle, 0.0)

  def test_push_against_the_turn_hands_over(self):
    self._press(-2.5, 1.0, HUMAN_TURN_HOLD_PRETURNED_S + 0.2)   # a mid-turn grab: the longer hold
    self.assertTrue(self.ext.angle_human_turn_active)

  def test_winding_past_the_command_hands_over(self):
    self._press(+2.5, 2.0, HUMAN_TURN_HOLD_PRETURNED_S + 0.2)
    self.assertTrue(self.ext.angle_human_turn_active)


if __name__ == "__main__":
  unittest.main()
