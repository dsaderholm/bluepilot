"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

# FusionPilot: FordLowSpeedAngleHold_ang -- the steering holds through a stop instead of falling to
# the middle and swinging back. Mechanism and measurements are in lateral_angle_ext.py above
# ANGLE_HOLD_MAX_MPH.
#
# Every test here drives the REAL `update_angle_strategy` through the harness the blend-horizon tests
# already use, and reads what it published. Nothing re-implements the formula: a mirror of the
# arithmetic passes whether or not the shipped code matches it.

import json
import pathlib
import re
import unittest

from opendbc.sunnypilot.car.ford.lateral_angle_ext import ANGLE_HOLD_MAX_MPH, _MPH_TO_MS
from opendbc.sunnypilot.car.ford.tests.test_lateral_blend_horizon import (
  _Actuators, _CC, _CS, _FakeLiveDelay, _ForcedDetector, _Harness, _Model, _explorer_cp, T_IDXS,
)

REPO = pathlib.Path(__file__).resolve().parents[6]
TURN = 0.02            # 1/m -- a 50 m radius, the kind of turn prepared for at a light
HOLD_MPH = 10.0


class _Params:
  def __init__(self, hold):
    self.hold = hold

  def get(self, key, return_default=False):
    if key == "FordLowSpeedAngleHold_ang":
      return None if self.hold is None else str(self.hold).encode()
    return None

  def get_bool(self, key):
    return False


def _run(hold_mph, v_ego, desired=TURN, model_curvature=None, calls=40, steering_pressed=False):
  """Drive the shipped strategy with a stationary input until the rate limit has settled."""
  _FakeLiveDelay.lateralDelay = 0.38
  CP = _explorer_cp()
  ext = _Harness(CP)
  ext.human_turn_detector = _ForcedDetector(False)
  ext.update_angle_params(_Params(hold_mph))
  # At a stop a model that plans to stay stopped has no yaw -- orientationRate = k * v = 0.
  k = desired if model_curvature is None else model_curvature
  ext.model = _Model([k for _ in T_IDXS], max(v_ego, 0.0))
  cs = _CS(vEgoRaw=v_ego, vEgo=v_ego, steeringPressed=steering_pressed)
  for _ in range(calls):
    ext.update_angle_strategy(_CC(latActive=True), cs, _Actuators(curvature=desired), CP)
  return ext


class TestTheBugItFixes(unittest.TestCase):
  def tearDown(self):
    _FakeLiveDelay.lateralDelay = 0.2

  def test_without_the_hold_a_stopped_car_commands_nothing(self):
    # Route 00000471: at a standstill the model asked for 0.0087 and the wire carried 0.00. This is
    # that, reproduced -- and it must stay true at 0, which is what ships.
    ext = _run(0.0, 0.0)
    self.assertEqual(ext.bp_path_angle_final, 0.0)

  def test_with_the_hold_a_stopped_car_keeps_the_models_request(self):
    ext = _run(HOLD_MPH, 0.0)
    self.assertGreater(abs(ext.bp_path_angle_final), 0.05,
                       "a held 50 m turn at a stop must still be a real command, not a rounding error")
    self.assertAlmostEqual(ext.bp_path_angle_final,
                           ext.bp_kappa_cmd * HOLD_MPH * _MPH_TO_MS * ext.bp_curvature_factor, places=6,
                           msg="at a stop the speed term must be the hold speed")

  def test_the_held_command_is_the_models_own_request(self):
    # The prediction is yaw rate / speed, and a stopped plan has no yaw. Left in, it halves the held
    # request -- so at a stop the command must be the desired curvature, untouched.
    ext = _run(HOLD_MPH, 0.0, model_curvature=0.0)
    self.assertAlmostEqual(ext.bp_kappa_cmd, TURN, places=9)
    self.assertEqual(ext.bp_blend_weight, 0.0, "the published weight is the one that multiplied")

  def test_without_the_hold_the_stopped_prediction_halves_the_request(self):
    # The other half of the bug, pinned so the fade cannot be deleted without a failure.
    ext = _run(0.0, 0.0, model_curvature=0.0)
    self.assertLess(abs(ext.bp_kappa_cmd), TURN * 0.9)


class TestItChangesNothingItShouldNot(unittest.TestCase):
  def tearDown(self):
    _FakeLiveDelay.lateralDelay = 0.2

  def test_above_the_hold_speed_every_output_is_identical(self):
    v = HOLD_MPH * _MPH_TO_MS + 3.0
    off = _run(0.0, v, model_curvature=TURN * 0.6)
    on = _run(HOLD_MPH, v, model_curvature=TURN * 0.6)
    for field in ("bp_path_angle_final", "bp_kappa_cmd", "bp_blend_weight", "b_blend", "bp_curvature_factor"):
      self.assertEqual(getattr(off, field), getattr(on, field), field)

  def test_the_ramped_blend_state_is_left_alone_at_a_stop(self):
    # b_blend is persistent, ramped state. The hold reads a faded COPY; leaving a zeroed weight
    # behind would change the first seconds after pulling away, which is not what was asked for.
    off = _run(0.0, 0.0, model_curvature=0.0)
    on = _run(HOLD_MPH, 0.0, model_curvature=0.0)
    self.assertEqual(off.b_blend, on.b_blend)

  def test_the_command_is_continuous_across_the_hold_speed(self):
    edge = HOLD_MPH * _MPH_TO_MS
    below = _run(HOLD_MPH, edge - 0.01)
    above = _run(HOLD_MPH, edge + 0.01)
    self.assertAlmostEqual(below.bp_path_angle_final, above.bp_path_angle_final, delta=0.002,
                           msg="crossing the hold speed must not step the wheel")

  def test_the_rate_limit_still_applies_to_a_held_command(self):
    # One call from zero, asking for a large held angle: the soft ROC (0.055 rad/call below 9 m/s,
    # mirrored by ford.h path_angle_cmd_checks) must still bound what reaches the rack.
    ext = _run(ANGLE_HOLD_MAX_MPH, 0.0, desired=0.2, calls=1)
    self.assertLessEqual(abs(ext.bp_path_angle_final), 0.055 + 1e-9)

  def test_rolling_backward_is_treated_as_stopped(self):
    ext = _run(HOLD_MPH, -0.4, model_curvature=0.0)
    self.assertEqual(ext.bp_blend_weight, 0.0)
    self.assertAlmostEqual(ext.bp_path_angle_final,
                           ext.bp_kappa_cmd * HOLD_MPH * _MPH_TO_MS * ext.bp_curvature_factor, places=6)


class TestRightTurnsOnly(unittest.TestCase):
  """His rule: never leave the car pointed across traffic. A left keeps the old controller exactly."""

  def tearDown(self):
    _FakeLiveDelay.lateralDelay = 0.2

  def test_a_left_turn_is_never_held_at_a_stop(self):
    off = _run(0.0, 0.0, desired=-TURN)
    on = _run(HOLD_MPH, 0.0, desired=-TURN)
    self.assertEqual(on.bp_path_angle_final, 0.0,
                     "a left held at a light points the car at the opposing lanes")
    for field in ("bp_path_angle_final", "bp_kappa_cmd", "bp_blend_weight", "b_blend"):
      self.assertEqual(getattr(off, field), getattr(on, field), field)

  def test_a_left_turn_is_unchanged_at_every_speed_below_the_hold(self):
    for v in (0.5, 1.5, 3.0, HOLD_MPH * _MPH_TO_MS - 0.2):
      off = _run(0.0, v, desired=-TURN, model_curvature=-TURN * 0.5)
      on = _run(HOLD_MPH, v, desired=-TURN, model_curvature=-TURN * 0.5)
      for field in ("bp_path_angle_final", "bp_kappa_cmd", "bp_blend_weight"):
        self.assertEqual(getattr(off, field), getattr(on, field), "%s at %.1f m/s" % (field, v))

  def test_the_same_turn_to_the_right_is_held(self):
    # The mirror image of the test above -- so a sign flip in the gate fails one of the two.
    on = _run(HOLD_MPH, 0.0, desired=TURN)
    self.assertGreater(on.bp_path_angle_final, 0.05)

  def test_right_is_positive_curvature_because_controlsd_negates_the_steering_angle(self):
    # The whole gate rests on this line. With the steering angle positive-LEFT, it is what makes
    # controls curvature positive-RIGHT on every car -- measured on his drives as 99.3% of 5,220
    # right-blinker turning frames positive, and 1.0% of 4,859 left-blinker frames. If upstream
    # ever drops the minus sign, the hold would start holding LEFT turns, and this fails first.
    src = (REPO / "selfdrive/controls/controlsd.py").read_text(encoding="utf-8")
    self.assertRegex(src, r"self\.curvature\s*=\s*-\s*self\.VM\.calc_curvature\(")


class TestTheSettingIsBounded(unittest.TestCase):
  def test_the_setting_reads_in_mph(self):
    ext = _Harness(_explorer_cp())
    ext.update_angle_params(_Params(10))
    self.assertAlmostEqual(ext.angle_hold_speed_ms, 4.4704, places=6)

  def test_a_value_past_the_cap_is_clamped_to_it(self):
    ext = _Harness(_explorer_cp())
    ext.update_angle_params(_Params(40))
    self.assertAlmostEqual(ext.angle_hold_speed_ms, ANGLE_HOLD_MAX_MPH * _MPH_TO_MS, places=6)

  def test_an_unreadable_value_leaves_it_off(self):
    ext = _Harness(_explorer_cp())
    ext.update_angle_params(_Params("banana"))
    self.assertEqual(ext.angle_hold_speed_ms, 0.0)

  def test_the_cap_stays_below_the_panda_check_the_fade_could_trip(self):
    # The prediction fade changes kappa_cmd, which ford.h's shadow-curvature proximity check reads --
    # but that check only runs above FORD_PATH_ANGLE_LIMITS.angle_error_min_speed. Read it out of the
    # header rather than restating it, so tightening the check in C fails here.
    src = (REPO / "opendbc_repo/opendbc/safety/modes/ford.h").read_text(encoding="utf-8")
    block = re.search(r"FORD_PATH_ANGLE_LIMITS = \{(.*?)\n\};", src, re.S)
    self.assertIsNotNone(block)
    min_speed = float(re.search(r"\.angle_error_min_speed\s*=\s*([0-9.]+)", block.group(1)).group(1))
    self.assertLess(ANGLE_HOLD_MAX_MPH * _MPH_TO_MS, min_speed)

  def test_the_param_ships_off(self):
    src = (REPO / "common/params_keys.h").read_text(encoding="utf-8")
    m = re.search(r'\{"FordLowSpeedAngleHold_ang",\s*\{[^}]*FLOAT,\s*"([0-9.]+)"\}\}', src)
    self.assertIsNotNone(m, "the key must be declared, or the device raises on the read")
    self.assertEqual(float(m.group(1)), 0.0)

  def test_both_settings_surfaces_share_the_cap(self):
    ui = json.loads((REPO / "sunnypilot/sunnylink/settings_ui.json").read_text(encoding="utf-8"))
    found = []

    def walk(node):
      if isinstance(node, dict):
        if node.get("key") == "FordLowSpeedAngleHold_ang":
          found.append(node)
        for v in node.values():
          walk(v)
      elif isinstance(node, list):
        for v in node:
          walk(v)
    walk(ui)
    self.assertEqual(len(found), 1, "exactly one SunnyLink entry")
    self.assertEqual(float(found[0]["max"]), ANGLE_HOLD_MAX_MPH)
    screen = (REPO / "selfdrive/ui/bp/layouts/settings/bluepilot.py").read_text(encoding="utf-8")
    item = re.search(r'param="FordLowSpeedAngleHold_ang",\s*min_value=([0-9.]+),\s*max_value=([0-9.]+)', screen)
    self.assertIsNotNone(item)
    self.assertEqual(float(item.group(2)), ANGLE_HOLD_MAX_MPH)


if __name__ == "__main__":
  unittest.main()
