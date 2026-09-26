"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

# FusionPilot: the hold, tested as its own thing for the first time.
#
# Until 2026-09-25 this policy only existed inside the ICBM controller and was only ever reachable
# through a cruise-button fixture, so "what does a hold DO to a plan" could not be asked without
# also simulating a state machine, a tap band and a set of rate limiters. That is the whole reason
# it is being moved: a hold is a statement about speed policy and has nothing to do with buttons.

import unittest

from cereal import custom
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.hold import HELD_SOURCES, SpeedHold

LongitudinalPlanSource = custom.LongitudinalPlanSP.LongitudinalPlanSource
BaselineSource = custom.IntelligentCruiseButtonManagement.BaselineSource
OverrideState = custom.IntelligentCruiseButtonManagement.OverrideState

CRUISE = LongitudinalPlanSource.cruise
SLA = LongitudinalPlanSource.speedLimitAssist
CURVE = LongitudinalPlanSource.sccVision


class TestWhatAHoldDoesToAPlan(unittest.TestCase):
  def test_no_hold_is_the_identity(self):
    self.assertEqual(SpeedHold().aim(42, SLA), 42)
    self.assertEqual(SpeedHold().aim(42, CURVE), 42)

  def test_it_replaces_the_component_the_driver_overrode(self):
    h = SpeedHold()
    h.capture(70, BaselineSource.press, target_raw=55)
    self.assertEqual(h.aim(55, SLA), 70, "a hold ABOVE the limit must win")
    self.assertEqual(h.aim(80, CRUISE), 70, "and a hold BELOW cruise must win too")

  def test_it_only_ever_caps_a_physics_limit(self):
    """The property that keeps curve slowing working while the driver is overridden -- and the one
    a 'the hold wins' simplification would quietly destroy."""
    h = SpeedHold()
    h.capture(70, BaselineSource.press, target_raw=55)
    self.assertEqual(h.aim(30, CURVE), 30, "a curve asking for 30 must not be raised to the hold")
    self.assertEqual(h.aim(90, CURVE), 70, "but the hold still caps a target above it")

  def test_every_held_source_is_a_road_speed_judgement(self):
    # If a physics limit ever lands in this tuple, a hold starts RAISING a curve target.
    self.assertEqual(set(HELD_SOURCES), {CRUISE, SLA})


class TestTheFiveFieldsMoveTogether(unittest.TestCase):
  """They were set by hand at four sites, each writing a slightly different subset -- the same
  shape as the `cluster_moved_since_press` pair bug, where a latch and its anchor were reset in
  one place and not another."""

  def test_capture_sets_all_of_it(self):
    h = SpeedHold()
    h.capture(65, BaselineSource.press, target_raw=55)
    self.assertEqual(h.value, 65)
    self.assertEqual(h.source, BaselineSource.press)
    self.assertEqual(h.override_state, OverrideState.manual)
    self.assertEqual(h.target_at_capture, 55)
    self.assertFalse(h.diverged)
    self.assertTrue(h.active)
    self.assertTrue(h.manual)

  def test_a_second_capture_does_not_re_anchor_the_plan_number(self):
    """Once manual, the number the plan had when the hold was FIRST taken is what the reset delta
    is measured against. Re-anchoring on every capture would make the limit-moved rule unreachable
    on a road where the limit drifts."""
    h = SpeedHold()
    h.capture(65, BaselineSource.press, target_raw=55)
    h.capture(70, BaselineSource.press, target_raw=35)
    self.assertEqual(h.target_at_capture, 55, "the anchor moved on a re-capture")
    self.assertEqual(h.value, 70, "...but the driver's number itself must follow")

  def test_clear_leaves_nothing_behind(self):
    h = SpeedHold()
    h.capture(65, BaselineSource.press, target_raw=55)
    h.diverged = True
    h.clear()
    self.assertEqual(h.value, 0)
    self.assertEqual(h.target_at_capture, 0)
    self.assertFalse(h.diverged)
    self.assertFalse(h.active)
    self.assertFalse(h.manual)
    self.assertEqual(h.override_state, OverrideState.auto)


class TestTheLimitMovingAway(unittest.TestCase):
  def test_a_big_move_retires_the_hold(self):
    h = SpeedHold()
    h.capture(70, BaselineSource.press, target_raw=55)
    self.assertTrue(h.limit_moved_away(35, reset_delta=10), "55 -> 35 is a different road")
    self.assertFalse(h.limit_moved_away(50, reset_delta=10), "55 -> 50 is the same road")

  def test_it_is_symmetric(self):
    h = SpeedHold()
    h.capture(70, BaselineSource.press, target_raw=55)
    self.assertTrue(h.limit_moved_away(75, reset_delta=10), "a limit RISING must count too")


if __name__ == "__main__":
  unittest.main()
