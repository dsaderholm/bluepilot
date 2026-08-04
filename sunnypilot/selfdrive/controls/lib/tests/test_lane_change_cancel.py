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
  LANE_CHANGE_STATS_WRITE_S,
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
  def _dh(window=DEFAULT_CANCEL_WINDOW_S, timer=AutoLaneChangeMode.TWO_SECONDS, revert=True):
    from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper
    dh = DesireHelper()
    dh.alc.lane_change_set_timer = timer
    dh.alc.lane_change_cancel_window = float(window)
    dh.alc.revert_enabled = bool(revert)
    dh.alc.read_params = lambda: None      # keep the test's values through update_params
    return dh

  @staticmethod
  def _cs(left=False, right=False, v=30.0, left_bs=False, right_bs=False):
    return NS(vEgo=v, leftBlinker=left, rightBlinker=right, leftBlindspot=left_bs,
              rightBlindspot=right_bs, steeringPressed=False, steeringTorque=0.0, brakePressed=False)

  def _start(self, dh):
    """Signal left and run until the change is actually underway."""
    for _ in range(int(4.0 / DT_MDL)):
      dh.update(self._cs(left=True), True, 0.5)
      if dh.lane_change_state == LaneChangeState.laneChangeStarting:
        return
    raise AssertionError("never reached laneChangeStarting")

  def test_dropping_the_blinker_steers_back(self):
    """From the road: "turning off my blinker mid lane change doesn't really seem to cancel. They
    usually just go into the lane anyway."

    Releasing the desire was never going to go back -- it re-centers on whichever lane the planner
    now believes it is in, and two seconds into a ~3.8 s change that is the NEW one. So the cancel
    fired and nothing visible happened, which from the seat is indistinguishable from no cancel.

    A lane change the other way IS this one reversed, so that is what it does now.
    """
    dh = self._dh()
    self._start(dh)
    assert dh.desire == log.Desire.laneChangeLeft
    dh.update(self._cs(), True, 0.5)          # stalk off
    assert dh.alc.reverting
    assert dh.lane_change_direction == LaneChangeDirection.right, "not steering back"
    assert dh.desire == log.Desire.laneChangeRight, "stopped steering instead of returning"

  def test_the_revert_does_not_cancel_itself_every_frame(self):
    """The trap this latch exists for: the blinker is OFF for the whole reverse crossing, which is
    the same condition that triggered the cancel. Without the latch it re-cancels every frame and
    flips direction back and forth."""
    dh = self._dh()
    self._start(dh)
    dh.update(self._cs(), True, 0.5)
    for _ in range(int(1.0 / DT_MDL)):
      dh.update(self._cs(), True, 0.5)
      assert dh.lane_change_direction == LaneChangeDirection.right, "flipped back mid-revert"
    assert dh.alc.changes_cancelled == 1, "counted the same cancel more than once"

  def test_the_reverse_crossing_finishes_and_releases(self):
    """It has to end, and end in a state a new lane change can start from."""
    dh = self._dh()
    self._start(dh)
    dh.update(self._cs(), True, 0.5)
    for _ in range(int(8.0 / DT_MDL)):
      dh.update(self._cs(), True, 0.0)        # model: no lane change happening any more
    assert dh.lane_change_state == LaneChangeState.off
    assert not dh.alc.reverting, "latched on -- the next cancel would be ignored"
    assert dh.desire == log.Desire.none

  def test_a_blocked_return_lane_finishes_the_change_instead(self):
    """We came from that lane seconds ago so it is nearly always clear -- but "nearly always" is
    not something to steer on. With the blind spot lit on the return side, going back is the worse
    of two imperfect options, so it falls back to simply releasing the desire."""
    dh = self._dh()
    self._start(dh)                            # changing LEFT, so the return side is the RIGHT
    dh.update(self._cs(right_bs=True), True, 0.5)
    assert not dh.alc.reverting
    assert dh.lane_change_state == LaneChangeState.off
    assert dh.desire == log.Desire.none
    assert dh.alc.changes_cancelled == 1, "still the driver calling it off, and still counted"

  def test_the_other_blind_spot_does_not_stop_a_revert(self):
    """The lane we are heading INTO being occupied is not a reason to refuse to go back -- it is a
    reason to go back. Checking the wrong side here would invert the whole gate."""
    dh = self._dh()
    self._start(dh)                            # changing LEFT
    dh.update(self._cs(left_bs=True), True, 0.5)
    assert dh.alc.reverting
    assert dh.lane_change_direction == LaneChangeDirection.right

  def test_turning_the_revert_off_restores_the_old_cancel(self):
    dh = self._dh(revert=False)
    self._start(dh)
    dh.update(self._cs(), True, 0.5)
    assert not dh.alc.reverting
    assert dh.lane_change_state == LaneChangeState.off
    assert dh.lane_change_direction == LaneChangeDirection.none
    assert dh.desire == log.Desire.none

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

  def test_a_cancel_is_not_undone_by_the_completion_check_below_it(self):
    """Every other test here feeds lane_change_prob=0.5, which hides this.

    The cancel sets the state to `off` and then FALLS THROUGH to stock's "98% certainty" check,
    which is still inside the same branch and can set the state straight back to
    laneChangeFinishing. Both its conditions are reachable at cancel time: lane_change_ll_prob
    reaches zero half a second into the change, and lane_change_prob is the MODEL's confidence that
    a change is happening -- which is low precisely while a gentle change has not moved the car yet.

    So the window where this bites is a cancel between 0.5 s and the end of the cancel window, on a
    change the model has not registered. That is not an exotic case; it is the early, tentative
    lane change this whole feature exists to let the driver call off.

    Run with the revert OFF, because that is the path this is about: the revert keeps the state in
    laneChangeStarting and never takes the else branch on the deciding frame, so it cannot be undone
    this way. The fall-through only ever threatened the plain release-the-desire cancel.
    """
    dh = self._dh(revert=False)
    self._start(dh)
    # Hold the blinker until the lane-line fade has run out. The change is underway and stock's
    # first condition is now permanently satisfied for the rest of this state.
    for _ in range(int(1.0 / DT_MDL)):
      dh.update(self._cs(left=True), True, 0.5)
    assert dh.lane_change_state == LaneChangeState.laneChangeStarting
    assert dh.lane_change_ll_prob < 0.01

    # Now the driver drops the stalk on a frame where the model's confidence has dipped. Both of
    # stock's conditions and the cancel are true together, which is the collision.
    dh.update(self._cs(), True, 0.0)
    assert dh.lane_change_state == LaneChangeState.off, "the completion check undid the cancel"
    assert dh.desire == log.Desire.none

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


class TestStatsSurviveABadParam:
  """Found by reading. The seeded flag was set BEFORE the reads that could throw, so one bad param
  read left the flag true and the base values missing -- every later save then raised, was
  swallowed by the same except, and the drive silently recorded nothing at all.

  Silent and permanent is the worst shape a failure can take here: nothing looks wrong, the drive
  just produces no numbers.
  """

  def _alc(self, get):
    alc = AutoLaneChangeController(_DH())
    alc.params.get = get
    alc.written = []
    alc.params.put = lambda k, v: alc.written.append(v)
    alc.changes_completed = 1
    alc.change_seconds = 4.0
    return alc

  def test_a_throwing_read_does_not_disable_saving_forever(self):
    calls = {"n": 0}

    def flaky(key, *a, **k):
      calls["n"] += 1
      if calls["n"] == 1:
        raise ValueError("corrupt")
      return {"changes": 5, "abandoned": 1, "cancelled": 0, "seconds": 3.0}

    alc = self._alc(flaky)
    alc._stats_write_s = LANE_CHANGE_STATS_WRITE_S
    alc._save_stats()                       # first attempt throws
    assert not alc.written
    alc._stats_write_s = LANE_CHANGE_STATS_WRITE_S
    alc._save_stats()                       # must recover, not stay broken
    assert alc.written, "one bad read silenced the whole drive"
    assert alc.written[-1]["changes"] == 6

  def test_a_fresh_device_starts_from_zero(self):
    alc = self._alc(lambda *a, **k: None)
    alc._stats_write_s = LANE_CHANGE_STATS_WRITE_S
    alc._save_stats()
    assert alc.written[-1] == {"changes": 1, "abandoned": 0, "cancelled": 0, "seconds": 4.0}

  def test_the_lifetime_mean_weights_by_how_many_changes(self):
    """Not a mean of means: 5 changes at 3.0 s and 1 at 9.0 s is 4.0 s, not 6.0."""
    alc = self._alc(lambda *a, **k: {"changes": 5, "abandoned": 0, "cancelled": 0, "seconds": 3.0})
    alc.change_seconds = 9.0
    alc._stats_write_s = LANE_CHANGE_STATS_WRITE_S
    alc._save_stats()
    assert alc.written[-1]["seconds"] == 4.0


class TestMeasurementCannotStopTheCar:
  """DesireHelper runs inside modeld. Anything that raises there kills the model process, and with
  no model there is no steering at all.

  The stats are measurement -- entirely optional -- so the rule is that no optional thing may be
  able to stop the car driving. The param write was guarded from the start; the accounting around
  it was not, which is a twenty-line state machine one bad assumption away from the same outcome.
  """

  def test_a_fault_inside_the_accounting_does_not_escape(self):
    alc = AutoLaneChangeController(_DH())
    alc._update_stats = lambda: (_ for _ in ()).throw(RuntimeError("anything at all"))
    alc.update_stats()      # must not raise

  def test_and_the_lane_change_carries_on_regardless(self):
    dh = TestItIsActuallyWiredIn._dh()
    dh.alc._update_stats = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    TestItIsActuallyWiredIn()._start(dh)
    assert dh.lane_change_state == LaneChangeState.laneChangeStarting
    assert dh.desire == log.Desire.laneChangeLeft
