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

  def update_params(self) -> None:
    if self.param_read_counter % 50 == 0:
      self.read_params()
    self.param_read_counter += 1

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
