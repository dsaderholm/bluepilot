"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

BluePilot: how long an auto lane change waits after the blind spot goes clear.

Reported from a real drive: the BSM-aware auto lane change moved over too close to a car it had
just waited for. That is arithmetic, not tuning. While the blind spot is occupied the wait timer
was pinned exactly `ONE_SECOND_DELAY` below its threshold, so clearing the indicator bought
precisely one second before the maneuver began -- no matter what delay the driver had configured.

The tests below pin the behavior that replaced it, and one of them asserts the old bug is gone by
measuring the wait rather than by inspecting a constant. Measuring is the point: the failure was
invisible in the source, where `= self.lane_change_delay + ONE_SECOND_DELAY` reads like a delay
being applied rather than one being cancelled.
"""

from cereal import log
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot.selfdrive.controls.lib.auto_lane_change import (
  AutoLaneChangeController, AutoLaneChangeMode, AUTO_LANE_CHANGE_TIMER, DEFAULT_BSM_HOLD_S,
)


class FakeDH:
  """Only the two fields AutoLaneChangeController.reset() reads."""
  lane_change_state = log.LaneChangeState.laneChangeStarting
  lane_change_direction = log.LaneChangeDirection.left


def controller(mode=AutoLaneChangeMode.TWO_SECONDS, hold=DEFAULT_BSM_HOLD_S, bsm_delay=True):
  alc = AutoLaneChangeController(FakeDH())
  alc.lane_change_set_timer = mode
  alc.lane_change_bsm_delay = bsm_delay
  alc.lane_change_bsm_hold = float(hold)
  alc.lane_change_wait_timer = 0.0
  return alc


def seconds_until_allowed(alc, blindspot_frames=40):
  """Hold the blind spot occupied, then release it and count until the change is permitted."""
  for _ in range(blindspot_frames):
    alc.update_lane_change_timers(blindspot_detected=True)

  waited = 0.0
  for _ in range(int(30 / DT_MDL)):
    alc.update_lane_change_timers(blindspot_detected=False)
    waited += DT_MDL
    if alc.update_allowed():
      return waited
  return None


class TestBsmHold:
  def test_the_wait_is_the_configured_hold(self):
    for hold in (1, 3, 5, 8):
      alc = controller(hold=hold)
      waited = seconds_until_allowed(alc)
      assert waited is not None, f"never allowed at hold={hold}"
      assert abs(waited - hold) < 0.15, f"hold={hold} waited {waited:.2f}"

  def test_the_old_one_second_behavior_is_gone(self):
    """The reported bug, measured rather than read.

    With the default hold the wait must be meaningfully longer than the one second the previous
    arithmetic produced -- that second is what put the car alongside a vehicle which had only just
    stopped being beside it.
    """
    waited = seconds_until_allowed(controller())
    assert waited > 2.0, f"only waited {waited:.2f} s after the blind spot cleared"

  def test_nudgeless_gets_the_full_hold_too(self):
    """The old code special-cased nudgeless to a bare -1, so it got one second like everything
    else. Nudgeless is the mode that starts soonest, which makes it the one that most needs the
    hold, not the one that should skip it."""
    waited = seconds_until_allowed(controller(mode=AutoLaneChangeMode.NUDGELESS))
    assert waited is not None
    assert abs(waited - DEFAULT_BSM_HOLD_S) < 0.15

  def test_the_hold_only_applies_while_the_blind_spot_delay_is_on(self):
    # With the delay switched off the timer is never pinned, so a change is permitted as soon as
    # the ordinary lane-change delay elapses.
    alc = controller(bsm_delay=False)
    waited = seconds_until_allowed(alc, blindspot_frames=40)
    assert waited is not None
    assert waited < 0.2, f"waited {waited:.2f} s with the BSM delay disabled"

  def test_an_occupied_blind_spot_never_permits_the_change(self):
    alc = controller()
    for _ in range(int(30 / DT_MDL)):
      alc.update_lane_change_timers(blindspot_detected=True)
      assert not alc.update_allowed()


class TestOrdinaryDelayUnaffected:
  """The BSM hold must not change what the delay setting means when nothing is beside you."""

  def test_each_mode_still_waits_its_own_delay(self):
    for mode in (AutoLaneChangeMode.HALF_SECOND, AutoLaneChangeMode.ONE_SECOND,
                 AutoLaneChangeMode.TWO_SECONDS, AutoLaneChangeMode.THREE_SECONDS):
      alc = controller(mode=mode)
      expected = AUTO_LANE_CHANGE_TIMER[mode]
      waited = 0.0
      for _ in range(int(20 / DT_MDL)):
        alc.update_lane_change_timers(blindspot_detected=False)
        waited += DT_MDL
        if alc.update_allowed():
          break
      assert abs(waited - expected) < 0.15, f"mode {mode}: expected {expected}, waited {waited:.2f}"

  def test_nudge_and_off_never_auto_change(self):
    for mode in (AutoLaneChangeMode.OFF, AutoLaneChangeMode.NUDGE):
      alc = controller(mode=mode)
      for _ in range(int(10 / DT_MDL)):
        alc.update_lane_change_timers(blindspot_detected=False)
        assert not alc.update_allowed(), f"mode {mode} permitted an automatic change"
