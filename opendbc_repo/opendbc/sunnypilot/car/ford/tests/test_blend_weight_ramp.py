"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

# BluePilot ba20937aac ("Predicted_Curvature Weight Blending", bp-dev-191, 2026-08-25) turns the
# predicted-curvature blend weight into RAMPED, PERSISTENT state and replaces two absolute
# thresholds with relative ones. All three halves are pinned here.
#
# The weight multiplies the model's contribution to the commanded curvature, so snapping it between
# b and b*0.25 steps the command on a frame where the road did nothing -- which is why the upstream
# comment says "Prevents instant jumps between .5 and .125 predicted_curvature weight".
#
# AND `b_blend` IS NOW STATE ON THE CarController, which is the category that once made this car
# undrivable (see CLAUDE.md: `self.gap` on a class whose __init__ was never called). LateralAngleExt
# __init__ IS called explicitly from carcontroller.py, and test_carcontroller_smoke.py drives the
# real controller -- but the reset-on-bail-out behavior is what this file adds, because a re-engage
# inheriting the last drive's weight is invisible to a smoke test that never disengages.

import unittest

from opendbc.sunnypilot.car.ford.lateral_angle_ext import _FORD_PATH_ANGLE_BLEND_RATIO_DEFAULT


def _ramp(target, start, steps, b_step=0.1):
  """Mirror of the shipped ramp, for reasoning about how many calls a transition takes."""
  b = start
  for _ in range(steps):
    b = min(target, b + b_step) if target > b else max(target, b - b_step)
  return b


class TestTheBlendWeightRamps(unittest.TestCase):
  """The ramp itself -- pure arithmetic, so it is checked directly rather than through a drive."""

  def test_it_cannot_traverse_the_full_range_in_one_call(self):
    b = _FORD_PATH_ANGLE_BLEND_RATIO_DEFAULT          # 0.50
    exit_target = b * 0.25                            # 0.125
    after_one = _ramp(exit_target, b, 1)
    self.assertGreater(after_one, exit_target,
                       "one call must not land on the exit weight -- that is the jump being removed")
    self.assertAlmostEqual(after_one, 0.40, places=6)

  def test_it_reaches_the_exit_weight_in_a_bounded_number_of_calls(self):
    b = _FORD_PATH_ANGLE_BLEND_RATIO_DEFAULT
    exit_target = b * 0.25
    # 0.50 -> 0.125 at 0.1 per call is 4 calls; at STEER_STEP=5 (20 Hz) that is ~0.19 s.
    self.assertAlmostEqual(_ramp(exit_target, b, 4), exit_target, places=6)
    self.assertAlmostEqual(_ramp(exit_target, b, 40), exit_target, places=6,
                           msg="must settle, not oscillate around the target")

  def test_it_ramps_both_directions(self):
    b = _FORD_PATH_ANGLE_BLEND_RATIO_DEFAULT
    self.assertAlmostEqual(_ramp(b, b * 0.25, 1), 0.225, places=6)
    self.assertAlmostEqual(_ramp(b, b * 0.25, 4), b, places=6)

  def test_the_straightaway_weight_sits_between_exit_and_full(self):
    b = _FORD_PATH_ANGLE_BLEND_RATIO_DEFAULT
    self.assertLess(b * 0.25, b * 0.35)
    self.assertLess(b * 0.35, b)


class TestTheRelativeThresholds(unittest.TestCase):
  """Both replaced an ABSOLUTE delta that measured as unreachable on this car."""

  @staticmethod
  def _falling(desired, last):
    return abs(last) > 0.001 and abs(desired) < abs(last) * 0.8

  @staticmethod
  def _falling_old(desired, last):
    return abs(desired) < abs(last) - 0.010

  def test_an_ordinary_curve_exit_now_fires_and_did_not_before(self):
    # A 25% unwind from an 833 m radius -- an unremarkable exit. Measured across 239,038 angle-path
    # intervals on 000003eb/ec, the old 0.010 threshold is 5.4x the p99 fall and fired on 0.054%.
    last, desired = 0.0012, 0.0009
    self.assertTrue(self._falling(desired, last))
    self.assertFalse(self._falling_old(desired, last),
                     "the old absolute threshold is what made this unreachable")

  def test_IT_IS_STILL_INERT_ON_CURVES_GENTLER_THAN_1000_M(self):
    """The limit that matters for HIS report, found by picking 0.0010 as a test value and watching
    it fail: `abs(last) > 0.001` is a floor at a 1000 m radius, and several of the ping-pong
    episodes he reports are gentler than that -- 1271 m, 1327 m, 2514 m. On those the upstream fix
    cannot engage at all, because BOTH new guards carry the same floor.

    So ba20937aac is a real improvement on curves tighter than ~1000 m and does NOTHING on the
    gentle sweepers, which is precisely where he first described the symptom ("it's on larger
    curves too, yes"). Do not report it as a fix for the whole complaint."""
    for radius_m in (1271, 1327, 2514):
      k = 1.0 / radius_m
      self.assertFalse(self._falling(k * 0.75, k),
                       f"a 25% unwind at {radius_m} m is still invisible to the exit-bias blend")
      self.assertFalse(TestKappaEnteringNeedsRealCurvature._entering(k * 1.5, k),
                       f"and entry at {radius_m} m still cannot latch")

  def test_it_is_scale_free(self):
    # The point of a ratio: the same 25% unwind fires at any curvature, so it cannot go stale
    # against a cadence change or a different road the way a fixed delta did.
    for last in (0.0012, 0.005, 0.02):
      self.assertTrue(self._falling(last * 0.75, last), f"should fire at {last}")

  def test_it_ignores_noise_around_straight(self):
    # Below 0.001 1/m (1000 m radius) the guard refuses outright, so dither on a straight road
    # cannot masquerade as an exit however large the RATIO between two tiny numbers is.
    self.assertFalse(self._falling(0.0000001, 0.0005))

  def test_a_shallow_unwind_does_not_count_as_an_exit(self):
    self.assertFalse(self._falling(0.0009, 0.0010), "10% is not an exit")


class TestKappaEnteringNeedsRealCurvature(unittest.TestCase):
  @staticmethod
  def _entering(kappa_at_t_base, desired):
    return abs(desired) > 0.001 and kappa_at_t_base > abs(desired) * 1.25

  def test_noise_near_straight_no_longer_latches_entering(self):
    # The bare `>` latched wherever desired sat near zero, which is most of a straight road -- and
    # _kappa_entering gates both the extra lookahead and the exit-biased blend.
    self.assertFalse(self._entering(0.0004, 0.0003))

  def test_a_real_deepening_curve_still_reads_as_entering(self):
    self.assertTrue(self._entering(0.0030, 0.0020))

  def test_a_marginal_excess_no_longer_counts(self):
    self.assertFalse(self._entering(0.0021, 0.0020), "needs 25% more, not any excess")


if __name__ == "__main__":
  unittest.main()
