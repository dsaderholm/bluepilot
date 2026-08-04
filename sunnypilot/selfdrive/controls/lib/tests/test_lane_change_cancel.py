"""
BluePilot: calling off a lane change already underway.

Reported from the car: "when I do a sunnypilot lane change there is no way to cancel it." True --
once laneChangeStarting is entered, stock never looks at the blinker again and the state machine
runs to completion on the model's own probability.

Which makes the existing lane change the MANUAL version of passing assist, and the same rule
applies: abort criteria narrow as driver intent strengthens. The driver chose this maneuver, so a
gate must not undo it -- but the driver withdrawing that choice is the strongest signal there is,
and it was being ignored entirely.

These test the controller's decision, not the steering. Whether the car physically returns to its
lane is the planner's business and cannot be checked offline.
"""

from types import SimpleNamespace as NS

from cereal import log
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot.selfdrive.controls.lib.auto_lane_change import (
  AutoLaneChangeController, AutoLaneChangeMode, DEFAULT_CANCEL_WINDOW_S,
)

LaneChangeState = log.LaneChangeState
LaneChangeDirection = log.LaneChangeDirection


class _DH:
  """The parent DesireHelper, reduced to what the controller reads from it."""

  def __init__(self):
    from cereal import log
    self.lane_change_state = log.LaneChangeState.off
    self.lane_change_direction = log.LaneChangeDirection.none


def controller(window=DEFAULT_CANCEL_WINDOW_S):
  alc = AutoLaneChangeController(_DH())
  alc.lane_change_cancel_window = float(window)
  return alc


class TestCancelling:
  def test_dropping_the_blinker_early_calls_it_off(self):
    alc = controller()
    assert alc.should_cancel(one_blinker=False, elapsed_s=0.5)

  def test_holding_the_blinker_does_not(self):
    """The ordinary case, and by far the most common -- this must not fire on a normal change."""
    alc = controller()
    assert not alc.should_cancel(one_blinker=True, elapsed_s=0.5)

  def test_past_the_window_it_finishes(self):
    """The point of no return. Reverting from most of the way across is a second crossing through
    the space you just left, not an undo -- and openpilot has no reverse-lane-change desire, so
    clearing it late makes the planner re-center on the lane it has mostly reached anyway."""
    alc = controller()
    assert not alc.should_cancel(one_blinker=False, elapsed_s=DEFAULT_CANCEL_WINDOW_S + 0.1)

  def test_right_on_the_boundary_finishes(self):
    alc = controller()
    assert not alc.should_cancel(one_blinker=False, elapsed_s=DEFAULT_CANCEL_WINDOW_S)

  def test_zero_restores_the_stock_behavior(self):
    """Off must mean genuinely uncancellable, the way it shipped -- not "cancellable for zero
    seconds", which would be the same thing but by accident."""
    alc = controller(window=0)
    assert not alc.should_cancel(one_blinker=False, elapsed_s=0.0)
    assert not alc.should_cancel(one_blinker=False, elapsed_s=0.5)

  def test_a_longer_window_extends_it(self):
    alc = controller(window=5)
    assert alc.should_cancel(one_blinker=False, elapsed_s=4.5)
    assert not alc.should_cancel(one_blinker=False, elapsed_s=5.5)

  def test_the_window_is_read_from_the_setting(self):
    """Not a constant behind a setting that never reaches it -- the failure this whole fork keeps
    finding."""
    alc = AutoLaneChangeController(_DH())
    alc.read_params()
    assert alc.lane_change_cancel_window == float(DEFAULT_CANCEL_WINDOW_S)


class TestItDoesNotDisturbAnythingElse:
  def test_a_fresh_controller_has_not_cancelled(self):
    assert not AutoLaneChangeController(_DH()).cancelled

  def test_one_frame_of_blinker_bounce_still_cancels(self):
    """Deliberate. A stalk that bounces off for a frame IS the driver cancelling as far as this can
    tell, and the failure direction is right: it stops a maneuver rather than starting one."""
    alc = controller()
    assert alc.should_cancel(one_blinker=False, elapsed_s=DT_MDL)


class TestItIsActuallyWiredIn:
  """The decision above is worthless if desire_helper never asks for it, and the first version of
  these tests did not check that -- deleting the call from the state machine left them all green.

  So these drive the real DesireHelper through a real lane change.
  """

  @staticmethod
  def _dh(window=DEFAULT_CANCEL_WINDOW_S, timer=AutoLaneChangeMode.TWO_SECONDS):
    from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper
    dh = DesireHelper()
    dh.alc.lane_change_set_timer = timer
    dh.alc.lane_change_cancel_window = float(window)
    dh.alc.read_params = lambda: None      # keep the test's values through update_params
    return dh

  @staticmethod
  def _cs(left=False, right=False, v=30.0):
    return NS(vEgo=v, leftBlinker=left, rightBlinker=right, leftBlindspot=False,
              rightBlindspot=False, steeringPressed=False, steeringTorque=0.0, brakePressed=False)

  def _start(self, dh):
    """Signal left and run until the change is actually underway."""
    for _ in range(int(4.0 / DT_MDL)):
      dh.update(self._cs(left=True), True, 0.5)
      if dh.lane_change_state == LaneChangeState.laneChangeStarting:
        return
    raise AssertionError("never reached laneChangeStarting")

  def test_dropping_the_blinker_stops_a_change_in_progress(self):
    dh = self._dh()
    self._start(dh)
    dh.update(self._cs(), True, 0.5)          # stalk off
    assert dh.lane_change_state == LaneChangeState.off
    assert dh.lane_change_direction == LaneChangeDirection.none
    assert dh.desire == log.Desire.none, "still steering across after being called off"

  def test_it_keeps_going_while_the_blinker_is_held(self):
    dh = self._dh()
    self._start(dh)
    for _ in range(int(1.0 / DT_MDL)):
      dh.update(self._cs(left=True), True, 0.5)
    assert dh.lane_change_state == LaneChangeState.laneChangeStarting
    assert dh.desire == log.Desire.laneChangeLeft

  def test_past_the_window_the_blinker_no_longer_stops_it(self):
    dh = self._dh(window=1)
    self._start(dh)
    for _ in range(int(1.5 / DT_MDL)):
      dh.update(self._cs(left=True), True, 0.5)
    dh.update(self._cs(), True, 0.5)
    assert dh.lane_change_state == LaneChangeState.laneChangeStarting
    assert dh.desire == log.Desire.laneChangeLeft

  def test_off_leaves_the_stock_behavior_exactly_as_it_was(self):
    dh = self._dh(window=0)
    self._start(dh)
    dh.update(self._cs(), True, 0.5)
    assert dh.lane_change_state == LaneChangeState.laneChangeStarting


class TestMeasuringRealLaneChanges:
  """Passing assist cannot steer, so every constant it holds about what a lane change IS came from
  reasoning rather than observation. CHANGE_DURATION_S = 4.0 was invented outright, and its
  backed-out count has no human baseline to be judged against.

  The driver's own changes are the only real ones this car performs, so they are the measurement.
  """

  def _dh(self):
    dh = TestItIsActuallyWiredIn._dh()
    dh.alc.params.put = lambda *a, **k: None      # never touch the real param store in a test
    return dh

  def test_a_completed_change_is_timed(self):
    dh = self._dh()
    TestItIsActuallyWiredIn()._start(dh)
    for _ in range(int(8.0 / DT_MDL)):
      dh.update(TestItIsActuallyWiredIn._cs(left=True), True, 0.0)   # prob 0 -> it completes
    assert dh.alc.changes_completed == 1
    assert 0.5 < dh.alc.change_seconds < 8.0, "a real duration, not zero and not the whole run"

  def test_signalling_and_thinking_better_of_it_is_counted_apart(self):
    """His own change-of-mind rate. If he abandons one signal in ten, then passing assist backing
    out one in ten is normal -- and without this that number has no scale at all."""
    dh = self._dh()
    for _ in range(int(0.5 / DT_MDL)):
      dh.update(TestItIsActuallyWiredIn._cs(left=True), True, 0.5)
    assert dh.lane_change_state == LaneChangeState.preLaneChange
    dh.update(TestItIsActuallyWiredIn._cs(), True, 0.5)
    assert dh.alc.changes_abandoned == 1
    assert dh.alc.changes_completed == 0

  def test_a_cancelled_change_is_neither_completed_nor_abandoned(self):
    """Three different events. Lumping any two would hide the one that says something."""
    dh = self._dh()
    TestItIsActuallyWiredIn()._start(dh)
    dh.update(TestItIsActuallyWiredIn._cs(), True, 0.5)
    assert dh.alc.changes_cancelled == 1
    assert dh.alc.changes_completed == 0
    assert dh.alc.changes_abandoned == 0

  def test_measuring_never_touches_the_maneuver(self):
    """It reads the parent's state and writes none of it. If this is ever the reason a lane change
    behaves differently, something is very wrong."""
    a, b = self._dh(), self._dh()
    b.alc.update_stats = lambda: None
    for _ in range(int(6.0 / DT_MDL)):
      cs = TestItIsActuallyWiredIn._cs(left=True)
      a.update(cs, True, 0.5)
      b.update(cs, True, 0.5)
    assert a.lane_change_state == b.lane_change_state
    assert a.desire == b.desire
