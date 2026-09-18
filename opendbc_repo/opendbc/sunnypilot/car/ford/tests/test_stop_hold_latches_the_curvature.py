"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

# FusionPilot: the stop hold's CURVATURE LATCH -- see ANGLE_HOLD_CAPTURE_MIN_MPH in
# lateral_angle_ext.py for the measurement that produced it.
#
# The failure it fixes cannot be reproduced by holding one curvature and stopping: the model TAKES
# THE TURN AWAY between 5 mph and a standstill, and the speed floor then multiplies a zero. So every
# test here drives an APPROACH (speed, a real request) followed by a STOP (no speed, no request),
# which is the shape every one of the 13 measured approaches had.
#
# Nothing re-implements the formula. Each test drives the real `update_angle_strategy` and reads
# what it published, because a mirror of the arithmetic passes whether or not the shipped code
# matches it.

import unittest

from opendbc.sunnypilot.car.ford.lateral_angle_ext import (
  ANGLE_HOLD_CAPTURE_MIN_MPH, ANGLE_HOLD_KAPPA_MIN, ANGLE_HOLD_LEFT_LEAD_M, _MPH_TO_MS,
)
from opendbc.sunnypilot.car.ford.tests.test_lateral_blend_horizon import (
  _Actuators, _CC, _CS, _FakeLiveDelay, _ForcedDetector, _Harness, _Model, _explorer_cp, T_IDXS,
)
from opendbc.sunnypilot.car.ford.tests.test_steering_holds_through_a_stop import _Params, _RadarState, _SM

TURN = 0.02                                        # 1/m, a 50 m radius -- a turn prepared for at a light
HOLD_MPH = 11.0                                    # what he set on 2026-09-17
HOLD_MS = HOLD_MPH * _MPH_TO_MS
APPROACH_MS = 8.0                                  # ~18 mph, above the capture floor and the hold band
GONE = 0.0002                                      # what the model actually asks at the standstill
CAPTURE_MS = ANGLE_HOLD_CAPTURE_MIN_MPH * _MPH_TO_MS


def _ext(hold_mph=HOLD_MPH, lead=None, radar=True, alive=True, hands=False):
  _FakeLiveDelay.lateralDelay = 0.38
  CP = _explorer_cp()
  ext = _Harness(CP)
  ext.human_turn_detector = _ForcedDetector(hands)
  ext.update_angle_params(_Params(hold_mph))
  sm = _SM()
  sm.radar = _RadarState(lead, radar)
  sm.alive["radarState"] = alive
  ext.sm = sm
  ext.CP = CP
  return ext


def _step(ext, v, desired, calls=40, lat=True, model_curvature=None):
  """Hold one (speed, request) for long enough that the rate limit has settled."""
  k_model = desired if model_curvature is None else model_curvature
  ext.model = _Model([k_model for _ in T_IDXS], max(v, 0.0))
  cs = _CS(vEgoRaw=v, vEgo=v)
  for _ in range(calls):
    ext.update_angle_strategy(_CC(latActive=lat), cs, _Actuators(curvature=desired), ext.CP)
  return ext


def _approach_then_stop(hold_mph=HOLD_MPH, turn=TURN, at_stop=GONE, lead=None, radar=True,
                        alive=True, approach_v=APPROACH_MS):
  """The measured shape: asking for a turn with speed, then stopped with the request gone."""
  ext = _ext(hold_mph, lead=lead, radar=radar, alive=alive)
  _step(ext, approach_v, turn, model_curvature=turn)
  _step(ext, 0.0, at_stop, model_curvature=0.0)
  return ext


def _held_command(ext):
  """What path_angle SHOULD be if the latch produced it: kappa * hold speed * the applied gain."""
  return ext.bp_kappa_cmd * HOLD_MS * ext.bp_curvature_factor


class TestTheBugItFixes(unittest.TestCase):
  """Route 0000047c t+80682: wheel +27.7 deg at 12.4 mph, model flipped by 10.2, wheel at the middle."""

  def tearDown(self):
    _FakeLiveDelay.lateralDelay = 0.2

  def test_the_speed_floor_alone_leaves_nothing_at_the_stop(self):
    # The hold is ON and the old controller still commands nothing, because the model let go on the
    # way down. This is the drive, and it is what the latch exists for -- it uses the shipped code
    # with the latch defeated by never giving it a speed to capture at.
    ext = _ext(HOLD_MPH)
    _step(ext, 1.0, TURN, model_curvature=TURN)        # below the capture floor: nothing latches
    _step(ext, 0.0, GONE, model_curvature=0.0)
    self.assertLess(abs(ext.bp_path_angle_final), 0.005)

  def test_the_latch_keeps_the_turn_the_model_gave_up(self):
    ext = _approach_then_stop()
    self.assertGreater(ext.bp_path_angle_final, 0.05,
                       "the wheel must still be turned after the model dropped the request")
    self.assertAlmostEqual(ext.bp_kappa_cmd, TURN, places=6,
                           msg="the held curvature is the one the model asked for with speed")
    self.assertAlmostEqual(ext.bp_path_angle_final, _held_command(ext), places=6,
                           msg="at a stop the speed term must still be the hold speed")

  def test_with_the_setting_off_the_stop_is_exactly_as_it_was(self):
    off = _approach_then_stop(hold_mph=0.0)
    self.assertLess(abs(off.bp_path_angle_final), 0.005)
    self.assertEqual(off.angle_hold_kappa, 0.0,
                     "no hold speed, nothing latched -- the feature is entirely off at 0")

  def test_the_whole_span_of_the_measured_collapse_is_covered(self):
    # 13 approaches: intact at 5 mph, gone by 2. Walking the real speeds down must not drop it.
    ext = _ext(HOLD_MPH)
    for v in (APPROACH_MS, HOLD_MS - 0.2, CAPTURE_MS + 0.3):
      _step(ext, v, TURN, calls=10, model_curvature=TURN)
    for v, k in ((2.0 * _MPH_TO_MS, TURN * 0.12), (0.0, -GONE)):
      _step(ext, v, k, calls=10, model_curvature=0.0)
    self.assertGreater(ext.bp_path_angle_final, 0.05)


class TestItIsAFloorAndNotAReplacement(unittest.TestCase):
  def tearDown(self):
    _FakeLiveDelay.lateralDelay = 0.2

  def test_the_model_wins_whenever_it_asks_for_more(self):
    ext = _approach_then_stop(at_stop=TURN * 2.0)
    self.assertAlmostEqual(ext.bp_kappa_cmd, TURN * 2.0, places=6)
    self.assertEqual(ext.bp_angle_hold_kappa, 0.0,
                     "the latch is not in use when the model is asking for more")

  def test_an_equal_request_is_not_the_latch(self):
    ext = _ext(HOLD_MPH)
    _step(ext, APPROACH_MS, TURN, model_curvature=TURN)
    _step(ext, 0.0, TURN, model_curvature=0.0)
    self.assertEqual(ext.bp_angle_hold_kappa, 0.0)
    self.assertGreater(ext.bp_path_angle_final, 0.05, "and the hold still applies, as it always did")


class TestTheReleases(unittest.TestCase):
  """Every one of these matters more than the hold itself."""

  def tearDown(self):
    _FakeLiveDelay.lateralDelay = 0.2

  def test_the_model_asking_the_other_way_drops_the_latch(self):
    # 9 of the 13 measured approaches end up asking the other way. A near-zero flip is noise and is
    # held through; a real request the other way is the model re-planning and must win.
    noise = _approach_then_stop(at_stop=-GONE)
    self.assertGreater(noise.bp_path_angle_final, 0.05, "a near-zero flip is not a re-plan")

    ext = _ext(HOLD_MPH)
    _step(ext, APPROACH_MS, TURN, model_curvature=TURN)
    _step(ext, 0.0, -ANGLE_HOLD_KAPPA_MIN * 1.5, model_curvature=0.0)
    self.assertEqual(ext.angle_hold_kappa, 0.0, "it wants the other way and means it")
    self.assertLessEqual(ext.bp_path_angle_final, 0.0)

  def test_pulling_away_hands_the_wheel_back(self):
    ext = _approach_then_stop()
    self.assertNotEqual(ext.angle_hold_kappa, 0.0)
    _step(ext, CAPTURE_MS + 0.5, GONE, model_curvature=0.0)
    self.assertEqual(ext.angle_hold_kappa, 0.0,
                     "back above the capture speed with nothing to ask for, the latch is cleared")

  def test_lateral_going_inactive_clears_it(self):
    ext = _approach_then_stop()
    _step(ext, 0.0, GONE, calls=2, lat=False, model_curvature=0.0)
    self.assertEqual(ext.angle_hold_kappa, 0.0)
    self.assertEqual(ext.bp_angle_hold_kappa, 0.0)

  def test_the_driver_steering_clears_it(self):
    ext = _approach_then_stop()
    ext.human_turn_detector = _ForcedDetector(True)
    _step(ext, 0.0, GONE, calls=2, model_curvature=0.0)
    self.assertEqual(ext.angle_hold_kappa, 0.0)
    self.assertEqual(ext.bp_angle_hold_kappa, 0.0)


class TestWhatItRefusesToLatch(unittest.TestCase):
  def tearDown(self):
    _FakeLiveDelay.lateralDelay = 0.2

  def test_a_car_that_was_already_stopped_invents_nothing(self):
    # Never had speed, so there is no turn to keep. Creeping in a queue must not manufacture one.
    ext = _ext(HOLD_MPH)
    _step(ext, 0.4, TURN, model_curvature=0.0)
    self.assertEqual(ext.angle_hold_kappa, 0.0)

  def test_a_bend_too_gentle_to_see_is_not_latched(self):
    ext = _approach_then_stop(turn=ANGLE_HOLD_KAPPA_MIN * 0.5)
    self.assertEqual(ext.angle_hold_kappa, 0.0)
    self.assertLess(abs(ext.bp_path_angle_final), 0.005)

  def test_a_straight_at_speed_clears_a_stale_latch(self):
    ext = _ext(HOLD_MPH)
    _step(ext, APPROACH_MS, TURN, model_curvature=TURN)
    _step(ext, APPROACH_MS, GONE, model_curvature=0.0)
    self.assertEqual(ext.angle_hold_kappa, 0.0,
                     "the road straightened while the car still had speed -- that is a real straightening")


class TestTheSideRulesAreUnchanged(unittest.TestCase):
  """Rights always; a left only while a radar-confirmed car sits inside ANGLE_HOLD_LEFT_LEAD_M."""

  def tearDown(self):
    _FakeLiveDelay.lateralDelay = 0.2

  def test_a_left_is_not_held_with_nothing_ahead(self):
    off = _approach_then_stop(hold_mph=0.0, turn=-TURN, at_stop=-GONE)
    on = _approach_then_stop(turn=-TURN, at_stop=-GONE)
    self.assertEqual(on.bp_angle_hold_kappa, 0.0)
    for field in ("bp_path_angle_final", "bp_kappa_cmd", "bp_blend_weight"):
      self.assertEqual(getattr(off, field), getattr(on, field), field)

  def test_a_left_is_held_behind_a_stopped_car(self):
    ext = _approach_then_stop(turn=-TURN, at_stop=-GONE, lead=ANGLE_HOLD_LEFT_LEAD_M - 2.0)
    self.assertLess(ext.bp_path_angle_final, -0.05)
    self.assertAlmostEqual(ext.bp_kappa_cmd, -TURN, places=6)

  def test_a_vision_only_lead_does_not_arm_a_left(self):
    ext = _approach_then_stop(turn=-TURN, at_stop=-GONE,
                              lead=ANGLE_HOLD_LEFT_LEAD_M - 2.0, radar=False)
    self.assertEqual(ext.bp_angle_hold_kappa, 0.0)

  def test_a_lead_too_far_ahead_does_not_arm_a_left(self):
    ext = _approach_then_stop(turn=-TURN, at_stop=-GONE, lead=ANGLE_HOLD_LEFT_LEAD_M + 5.0)
    self.assertEqual(ext.bp_angle_hold_kappa, 0.0)

  def test_the_side_is_decided_by_the_latch_not_the_models_noise(self):
    # At the standstill the model's own request is a near-zero of arbitrary sign. If the side test
    # read THAT, a left latch would be served whenever the noise happened to land positive.
    ext = _approach_then_stop(turn=-TURN, at_stop=+GONE)
    self.assertEqual(ext.bp_angle_hold_kappa, 0.0, "no lead: a left stays refused whatever the noise says")

  def test_a_right_is_held_with_no_lead_at_all(self):
    ext = _approach_then_stop(lead=None)
    self.assertGreater(ext.bp_path_angle_final, 0.05)


class TestItChangesNothingItShouldNot(unittest.TestCase):
  def tearDown(self):
    _FakeLiveDelay.lateralDelay = 0.2

  def test_above_the_hold_speed_every_output_is_identical(self):
    off = _ext(0.0)
    on = _ext(HOLD_MPH)
    for ext in (off, on):
      _step(ext, APPROACH_MS, TURN, model_curvature=TURN)
      _step(ext, HOLD_MS + 2.0, TURN * 0.3, model_curvature=TURN * 0.2)
    for field in ("bp_path_angle_final", "bp_kappa_cmd", "bp_blend_weight", "b_blend", "bp_curvature_factor"):
      self.assertEqual(getattr(off, field), getattr(on, field), field)

  def test_exit_detection_still_sees_the_models_own_request(self):
    # The latch is a command-side floor. Feeding it into _desired_curvature_last would make the
    # planner look like it never dropped the turn, which drives the exit-biased blend.
    ext = _approach_then_stop()
    self.assertAlmostEqual(ext._desired_curvature_last, GONE, places=9)

  def test_the_rate_limit_still_bounds_a_latched_command(self):
    ext = _ext(HOLD_MPH)
    _step(ext, APPROACH_MS, 0.2, calls=40, model_curvature=0.2)
    before = ext.bp_path_angle_final
    _step(ext, 0.0, GONE, calls=1, model_curvature=0.0)
    self.assertLessEqual(abs(ext.bp_path_angle_final - before), 0.055 + 1e-9)


class TestTheTelemetry(unittest.TestCase):
  def tearDown(self):
    _FakeLiveDelay.lateralDelay = 0.2

  def test_it_publishes_the_latched_value_actually_used(self):
    ext = _approach_then_stop()
    self.assertAlmostEqual(ext.bp_angle_hold_kappa, TURN, places=6)
    self.assertIsInstance(ext.bp_angle_hold_kappa, float,
                          "a numpy scalar here is the assignment that killed plannerd once")

  def test_it_is_zero_when_the_latch_is_not_what_drove_the_command(self):
    ext = _approach_then_stop(hold_mph=0.0)
    self.assertEqual(ext.bp_angle_hold_kappa, 0.0)


if __name__ == "__main__":
  unittest.main()
