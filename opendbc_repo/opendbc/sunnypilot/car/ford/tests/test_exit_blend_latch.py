"""The exit-biased blend has to actually RUN, and it has to let go the instant the road bends again.

ba20937aac's exit gate and its 0.1-per-call ramp are each correct and their product is zero.
Measured over 44 segments of `blendWeight` telemetry: the gate fires on ~1% of angle-path calls,
**87.9% of firings are a single isolated call**, and walking b_blend from 0.500 to 0.125 takes FOUR
consecutive firings. The exit weight was reached exactly once in 44 segments -- a mechanism this
branch has described as dead since 2026-08-29, and the one thing in the stack aimed at the exit
overshoot he reports.

The latch holds the exit state for `_EXIT_LATCH_CALLS` after the gate last fired, so the ramp can
traverse. **The release is the half that matters more**, and it is why most of this file is about
letting go: a held exit weight leans the command on the planner's lag-adjusted target instead of the
model's prediction, which is right while the road opens out and wrong the moment it closes.

Everything here drives the REAL `update_angle_strategy` through the same harness
`test_lateral_blend_horizon.py` uses, imported rather than copied. A test that mirrors the
arithmetic passes whether or not the shipped code matches it, which is the vacuous-test shape this
repo keeps recording -- `test_blend_weight_ramp.py` was green against a mutation that deleted the
ramp entirely until the real-harness version was written.
"""
import unittest

from opendbc.sunnypilot.car.ford.lateral_angle_ext import _EXIT_LATCH_CALLS, _VLT_V_HIGH_MS
from opendbc.sunnypilot.car.ford.tests.test_lateral_blend_horizon import (
  _CC, _CS, _Actuators, _FakeLiveDelay, _ForcedDetector, _Harness, _Model, _explorer_cp, _ramp)

# Exit weight is path_angle_blend_ratio * 0.25; the seed is the ratio itself.
SEED = 0.50
EXIT_WEIGHT = SEED * 0.25
B_STEP = 0.1

# A curvature the model sees as FLAT, so `_kappa_entering` is false and the exit branch is
# reachable. It has to stay under |desired| * 1.25 at every step of the walk below.
FLAT = 0.0001

# `_desired_falling` is `abs(prev) > 0.001 and abs(now) < abs(prev) * 0.8`. 0.0020 -> 0.0012 is a
# 40% fall, comfortably past the gate, with both ends above the 0.001 floor.
FALL_FROM = 0.0020
FALL_TO = 0.0012


def _drive(desireds, v_ego=None, curvatures=None, delay=0.38, lat_active=True):
  """Run the shipped strategy once per entry in `desireds`, returning the harness.

  One value per angle-path CALL, because `_desired_falling` compares consecutive calls -- the same
  interval mistake that made the offline measurement of this gate wrong twice in one afternoon.
  """
  _FakeLiveDelay.lateralDelay = delay
  v = _VLT_V_HIGH_MS + 5.0 if v_ego is None else v_ego
  CP = _explorer_cp()
  ext = _Harness(CP)
  ext.human_turn_detector = _ForcedDetector(False)
  ext.model = _Model(_ramp(FLAT, FLAT) if curvatures is None else curvatures, v)
  cs = _CS(vEgoRaw=v, vEgo=v)
  for d in desireds:
    ext.update_angle_strategy(_CC(latActive=lat_active), cs, _Actuators(curvature=d), CP)
  return ext


class TestTheLatchMakesTheExitBlendReachable(unittest.TestCase):
  def tearDown(self):
    _FakeLiveDelay.lateralDelay = 0.2

  def test_the_latch_is_long_enough_for_the_ramp_to_traverse(self):
    """The defect was a 1-in-90 trigger multiplied by a 4-call ramp. A latch shorter than the ramp
    reproduces it exactly, so this is the invariant the constant exists to satisfy."""
    needed = (SEED - EXIT_WEIGHT) / B_STEP
    self.assertGreaterEqual(_EXIT_LATCH_CALLS, needed,
                            f"a {_EXIT_LATCH_CALLS}-call latch cannot walk {SEED} -> {EXIT_WEIGHT} "
                            f"at {B_STEP} per call; that needs {needed:.0f}")

  def test_ONE_isolated_fall_now_reaches_the_exit_weight(self):
    """The whole point. 88% of real firings are a single isolated call, and before the latch a
    single call moved b_blend one step and let it spring straight back."""
    ext = _drive([FALL_FROM] + [FALL_TO] * (_EXIT_LATCH_CALLS - 1))
    self.assertAlmostEqual(ext.b_blend, EXIT_WEIGHT, places=6)

  def test_without_the_hold_a_single_fall_would_move_one_step_only(self):
    """Pins what the old behavior was, so the fix cannot be quietly reverted into a no-op: two
    calls is one fall plus one, which is a single step of the ramp either way."""
    ext = _drive([FALL_FROM, FALL_TO])
    self.assertAlmostEqual(ext.b_blend, SEED - B_STEP, places=6)


class TestTheLatchLetsGo(unittest.TestCase):
  """The safety half. Every test here is about the exit state ENDING."""

  def tearDown(self):
    _FakeLiveDelay.lateralDelay = 0.2

  def test_a_new_turn_in_releases_it_on_the_same_call(self):
    """`_kappa_entering` -- the model seeing 25% more curvature ahead than the planner is asking
    for -- is the road closing again. A stale exit weight there under-commands the entry, which is
    the failure the whole blend exists to avoid."""
    ext = _drive([FALL_FROM, FALL_TO, FALL_TO])
    self.assertLess(ext.b_blend, SEED, "setup failed: the exit state never engaged")

    # Same harness, but now the model sees a curve deepening well past desired * 1.25.
    ext.model = _Model(_ramp(0.0100, 0.0100), _VLT_V_HIGH_MS + 5.0)
    CP = _explorer_cp()
    cs = _CS(vEgoRaw=_VLT_V_HIGH_MS + 5.0, vEgo=_VLT_V_HIGH_MS + 5.0)
    before = ext.b_blend
    ext.update_angle_strategy(_CC(latActive=True), cs, _Actuators(curvature=FALL_TO), CP)
    self.assertEqual(ext._exit_latch_calls, 0, "turn-in must zero the latch on the frame it appears")
    self.assertGreater(ext.b_blend, before, "and the weight must start climbing back immediately")

  def test_a_turn_in_ON_THE_SAME_CALL_as_the_gate_wins(self):
    """The two are not exclusive: the planner's target can fall 20% on a frame where the model
    still sees more curvature ahead, which is ordinary on this car's noisy plan. `_kappa_entering`
    has to be tested FIRST or the latch arms from a frame where the road was CLOSING and then holds
    the exit weight into the entry.

    Only mutation testing found this -- swapping the two branches left all nine other tests green,
    because `_on_exit_near_limit` carries its own `not _kappa_entering` and hides the bad arm until
    the following call.
    """
    ext = _drive([FALL_FROM, FALL_TO], curvatures=_ramp(0.0100, 0.0100))
    self.assertEqual(ext._exit_latch_calls, 0,
                     "a fall that coincides with a turn-in must not arm the latch")

  def test_it_expires_on_its_own_when_the_road_stays_open(self):
    """A latch that never expires is a permanent 0.125, which is a different car."""
    steady = [FALL_TO] * (_EXIT_LATCH_CALLS + 5)
    ext = _drive([FALL_FROM] + steady)
    self.assertEqual(ext._exit_latch_calls, 0)

  def test_it_does_not_survive_a_disengagement(self):
    """b_blend is reset at every bail-out because a re-engage must not inherit the last drive's
    weight. The latch is the same kind of state and has to be reset with it -- unseeded persistent
    state on this controller is what made the car undrivable once."""
    ext = _drive([FALL_FROM, FALL_TO, FALL_TO])
    self.assertGreater(ext._exit_latch_calls, 0, "setup failed: the latch never armed")
    CP = _explorer_cp()
    cs = _CS(vEgoRaw=_VLT_V_HIGH_MS + 5.0, vEgo=_VLT_V_HIGH_MS + 5.0)
    ext.update_angle_strategy(_CC(latActive=False), cs, _Actuators(curvature=FALL_TO), CP)
    self.assertEqual(ext._exit_latch_calls, 0)
    self.assertAlmostEqual(ext.b_blend, SEED, places=6)

  def test_the_weight_never_goes_below_the_exit_target(self):
    """The ramp clamps at the target. A latch held for a long open stretch must not walk past it."""
    ext = _drive([FALL_FROM] + [FALL_TO] * (_EXIT_LATCH_CALLS * 3))
    self.assertGreaterEqual(ext.b_blend, EXIT_WEIGHT - 1e-9)


class TestItDoesNotFireWhereItShouldNot(unittest.TestCase):
  def tearDown(self):
    _FakeLiveDelay.lateralDelay = 0.2

  def test_a_steady_curve_never_arms_it(self):
    """Holding a bend is not an exit. If this arms, the car spends every corner at the exit weight."""
    ext = _drive([FALL_FROM] * 12)
    self.assertEqual(ext._exit_latch_calls, 0)
    self.assertAlmostEqual(ext.b_blend, SEED, places=6)

  def test_a_gentle_straightening_below_the_gate_never_arms_it(self):
    """20% is ba20937aac's threshold; 10% must not reach it, or the latch fires on any easing."""
    ext = _drive([0.0020, 0.0018, 0.0018, 0.0018])
    self.assertEqual(ext._exit_latch_calls, 0)


if __name__ == "__main__":
  unittest.main()
