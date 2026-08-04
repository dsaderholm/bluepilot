"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from cereal import log

from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL


class AutoLaneChangeMode:
  OFF = -1
  NUDGE = 0  # default
  NUDGELESS = 1
  HALF_SECOND = 2
  ONE_SECOND = 3
  TWO_SECONDS = 4
  THREE_SECONDS = 5


AUTO_LANE_CHANGE_TIMER = {
  AutoLaneChangeMode.OFF: 0.0,            # Off
  AutoLaneChangeMode.NUDGE: 0.0,          # Nudge
  AutoLaneChangeMode.NUDGELESS: 0.05,     # Nudgeless
  AutoLaneChangeMode.HALF_SECOND: 0.5,    # 0.5-second delay
  AutoLaneChangeMode.ONE_SECOND: 1.0,     # 1-second delay
  AutoLaneChangeMode.TWO_SECONDS: 2.0,    # 2-second delay
  AutoLaneChangeMode.THREE_SECONDS: 3.0,  # 3-second delay
}

ONE_SECOND_DELAY = -1

# BluePilot: how long AFTER the blind spot goes clear before the lane change may start.
#
# Diagnosed from the owner's own report: sunnypilot's BSM delay moved over too close to a car it
# had just waited for. It is not a tuning fault, it is arithmetic. While the blind spot is
# occupied the wait timer is pinned exactly ONE_SECOND_DELAY below the threshold, so the instant
# BSM clears it needs precisely one second to cross it -- whatever delay the driver configured.
#
# One second after a car leaves your blind spot it is a meter or two off your rear quarter, and if
# it was overtaking you it is still closing. That is the maneuver reported.
#
# No timer value fixes this properly, because BLIS answers the wrong question: it goes clear when
# the car is no longer BESIDE you and says nothing about whether it is now ahead or behind, or how
# fast. A rear-facing sensor is the real answer (see BP-REAR-RADAR-PLAN.md). Until one is fitted,
# a longer hold is the honest stand-in -- it buys distance on the assumption we cannot measure it.
#
# Default 3 s rather than 1. At a 10 mph overtaking difference that is ~13 m of extra separation.
DEFAULT_BSM_HOLD_S = 3

# BluePilot: how long after a lane change begins that CANCELLING THE BLINKER calls it off.
#
# Stock behavior is that it cannot be called off at all. Once laneChangeStarting is entered, the
# blinker is never looked at again -- the state machine runs to completion on the model's own
# lane-change probability. Reported from the car: "there is no way to cancel it."
#
# Which makes the existing lane change the MANUAL VERSION of passing assist, and the same design
# rule applies: abort criteria narrow as driver intent strengthens. The driver chose this maneuver,
# so a gate must not undo it -- but the driver withdrawing that choice is the strongest signal
# there is, and it was being ignored.
#
# WHY A WINDOW RATHER THAN ANY TIME. Reverting is itself a maneuver, and there is a point where it
# is worse than finishing: most of the way across, "go back" means a second crossing through the
# space you just left. openpilot has no reverse-lane-change desire either -- clearing the desire
# makes the planner re-center on whatever lane it now believes it is in, which late in the change
# is the NEW one. So cancelling late would not go back, it would finish while confusing the
# planner about why.
#
# Two seconds covers the part of a ~4 s change where the car is still substantially in the lane it
# started in, which is the part a driver could plausibly change their mind during.
DEFAULT_CANCEL_WINDOW_S = 2

# BluePilot: MEASURE THE DRIVER'S OWN LANE CHANGES, because they are the only real ones.
#
# Passing assist cannot steer, so every constant it holds about what a lane change IS came from
# reasoning rather than observation. Two of them are guesses that this can replace outright:
#
#   CHANGE_DURATION_S = 4.0 in passing_maneuver.py -- how long the crossing takes. Invented. Every
#   change the driver makes measures the real thing, on this car, at his speeds.
#
#   THIS ONE CONVERGES FAST AND THEN STOPS BEING NEWS, which the owner pointed out: a nudgeless
#   change takes the same time every time. Its shape comes from the model and the lateral tuning
#   -- lane_change_factor_high_ang scales the requested curvature during a change, and on this car
#   (angle branch) it is 1.0, so nothing softens it. Traffic does not enter into it. So expect one
#   number after a handful of changes, and after that treat it as a REGRESSION DETECTOR: if it
#   moves, somebody changed lateral tuning. The overtake duration is the one that genuinely varies,
#   and that is measured separately in overtake_progress.py.
#
#   The abort count has no human baseline. If he abandons one signal in ten himself, then passing
#   assist backing out one in ten is NORMAL and the number means nothing until compared. Without a
#   baseline it is a figure with no scale.
#
# Written to a param on the same terms as the drive summary: periodically, never blocking, and it
# survives being parked because a number nobody can read is a number nobody took.
LANE_CHANGE_STATS_WRITE_S = 30


class AutoLaneChangeController:
  def __init__(self, desire_helper):
    self.DH = desire_helper
    self.params = Params()

    self.lane_change_wait_timer = 0.0
    self.param_read_counter = 0
    self.lane_change_delay = 0.0

    self.lane_change_set_timer = self.params.get("AutoLaneChangeTimer", return_default=True)
    self.lane_change_bsm_delay = False
    self.lane_change_bsm_hold = float(DEFAULT_BSM_HOLD_S)

    self.prev_brake_pressed = False
    self.auto_lane_change_allowed = False
    self.prev_lane_change = False
    self.lane_change_cancel_window = float(DEFAULT_CANCEL_WINDOW_S)
    self.cancelled = False

    # See LANE_CHANGE_STATS_WRITE_S. Counts this drive; totals carried in from previous ones.
    self.changes_completed = 0
    self.changes_abandoned = 0      # signalled, then thought better of it before moving
    self.changes_cancelled = 0      # called off mid-change
    self.change_seconds = 0.0       # running mean of how long a completed one took
    self._prev_state = log.LaneChangeState.off
    self._change_started_s = 0.0
    self._stats_write_s = 0.0
    self._stats_seeded = False

    self.read_params()

  def reset(self) -> None:
    # Auto reset if parent state indicates we should
    if self.DH.lane_change_state == log.LaneChangeState.off and \
       self.DH.lane_change_direction == log.LaneChangeDirection.none:
      self.lane_change_wait_timer = 0.0
      self.prev_brake_pressed = False
      self.prev_lane_change = False

  def read_params(self) -> None:
    self.lane_change_bsm_delay = self.params.get_bool("AutoLaneChangeBsmDelay")
    self.lane_change_set_timer = self.params.get("AutoLaneChangeTimer", return_default=True)
    # BluePilot: see DEFAULT_BSM_HOLD_S.
    self.lane_change_bsm_hold = float(self.params.get("AutoLaneChangeBsmHoldTime", return_default=True))
    # BluePilot: see DEFAULT_CANCEL_WINDOW_S.
    self.lane_change_cancel_window = float(self.params.get("AutoLaneChangeCancelWindow", return_default=True))

  def update_params(self) -> None:
    if self.param_read_counter % 50 == 0:
      self.read_params()
    self.param_read_counter += 1

  def update_stats(self) -> None:
    """Watch the state machine and record what a real lane change did. See LANE_CHANGE_STATS_*.

    Reads only the parent's state, so it cannot influence the maneuver -- if this is ever the
    reason a lane change behaves differently, something is very wrong.
    """
    state = self.DH.lane_change_state
    prev, self._prev_state = self._prev_state, state

    if state in (log.LaneChangeState.laneChangeStarting, log.LaneChangeState.laneChangeFinishing):
      self._change_started_s += DT_MDL
    elif prev == log.LaneChangeState.preLaneChange and state == log.LaneChangeState.off:
      # Signalled and then did not go. His own change-of-mind rate, which is the baseline the
      # system's abort count has to be judged against.
      self.changes_abandoned += 1
    elif prev == log.LaneChangeState.laneChangeStarting and state == log.LaneChangeState.off:
      self.changes_cancelled += 1
      self._change_started_s = 0.0
    elif prev == log.LaneChangeState.laneChangeFinishing and state != log.LaneChangeState.laneChangeFinishing:
      if self._change_started_s > 0.0:
        self.changes_completed += 1
        # Running mean, so one unusually slow change cannot stand in for the drive.
        self.change_seconds += (self._change_started_s - self.change_seconds) / self.changes_completed
      self._change_started_s = 0.0

    self._save_stats()

  def _save_stats(self) -> None:
    self._stats_write_s += DT_MDL
    if self._stats_write_s < LANE_CHANGE_STATS_WRITE_S:
      return
    self._stats_write_s = 0.0
    if not (self.changes_completed or self.changes_abandoned or self.changes_cancelled):
      return
    try:
      if not self._stats_seeded:
        self._stats_seeded = True
        old = self.params.get("LaneChangeStats") or {}
        self._base = {k: old.get(k, 0) for k in ("changes", "abandoned", "cancelled")}
        self._base_secs = float(old.get("seconds", 0.0))
        self._base_n = int(old.get("changes", 0))
      total = self._base["changes"] + self.changes_completed
      # Weighted so the lifetime mean is over every change ever made, not a mean of means.
      secs = ((self._base_secs * self._base_n + self.change_seconds * self.changes_completed) / total
              if total else 0.0)
      self.params.put("LaneChangeStats", {
        "changes": total,
        "abandoned": self._base["abandoned"] + self.changes_abandoned,
        "cancelled": self._base["cancelled"] + self.changes_cancelled,
        "seconds": round(secs, 2),
      })
    except Exception:  # noqa: BLE001 - a param write must never reach the model process
      pass

  def update_lane_change_timers(self, blindspot_detected: bool) -> None:
    self.lane_change_delay = AUTO_LANE_CHANGE_TIMER.get(self.lane_change_set_timer,
                                                        AUTO_LANE_CHANGE_TIMER[AutoLaneChangeMode.NUDGE])

    self.lane_change_wait_timer += DT_MDL

    if self.lane_change_bsm_delay and blindspot_detected and self.lane_change_delay > 0:
      # BluePilot: pin the timer a full hold below the threshold, so clearing the blind spot buys
      # `lane_change_bsm_hold` seconds rather than the one second the original arithmetic gave.
      # The nudgeless special case folds in: its threshold is 0.05, so this lands at 0.05 - hold
      # and still yields `hold` seconds of wait. See DEFAULT_BSM_HOLD_S.
      self.lane_change_wait_timer = self.lane_change_delay - self.lane_change_bsm_hold

  def should_cancel(self, one_blinker: bool, elapsed_s: float) -> bool:
    """Has the driver called off a lane change already underway? See DEFAULT_CANCEL_WINDOW_S.

    Lives here rather than in desire_helper so the upstream file carries one line rather than a
    policy. Answers False past the window, which is the point of no return -- reverting from most
    of the way across is a second crossing, not an undo.
    """
    if self.lane_change_cancel_window <= 0.0:
      return False
    return not one_blinker and elapsed_s < self.lane_change_cancel_window

  def update_allowed(self) -> bool:
    # Auto lane change allowed if:
    # 1. A valid delay is set (non-zero)
    # 2. Brake wasn't previously pressed
    # 3. We've waited long enough

    if self.lane_change_set_timer in (AutoLaneChangeMode.OFF, AutoLaneChangeMode.NUDGE):
      return False

    if self.prev_brake_pressed:
      return False

    if self.prev_lane_change:
      return False

    return bool(self.lane_change_wait_timer > self.lane_change_delay)

  def update_lane_change(self, blindspot_detected: bool, brake_pressed: bool) -> None:
    if brake_pressed and not self.prev_brake_pressed:
      self.prev_brake_pressed = brake_pressed

    self.update_lane_change_timers(blindspot_detected)

    self.auto_lane_change_allowed = self.update_allowed()

  def update_state(self):
    if self.DH.lane_change_state == log.LaneChangeState.laneChangeStarting:
      self.prev_lane_change = True

    self.reset()
