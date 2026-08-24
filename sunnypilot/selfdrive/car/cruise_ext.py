"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import numpy as np

from cereal import car, custom
from opendbc.car import structs
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_CTRL
from openpilot.common.params import Params
from openpilot.sunnypilot.selfdrive.car.intelligent_cruise_button_management.helpers import get_minimum_set_speed
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.speed_limit_assist import ACTIVE_STATES as SLA_ACTIVE_STATES
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.helpers import compare_cluster_target

ButtonType = car.CarState.ButtonEvent.Type
SpeedLimitAssistState = custom.LongitudinalPlanSP.SpeedLimit.AssistState

CRUISE_BUTTON_TIMER = {ButtonType.decelCruise: 0, ButtonType.accelCruise: 0,
                       ButtonType.setCruise: 0, ButtonType.resumeCruise: 0,
                       ButtonType.cancel: 0, ButtonType.mainCruise: 0}

# FusionPilot: A BUTTON TIMER THAT NEVER RETURNS TO ZERO FROZE A HOLD FOR 87 SECONDS. 2026-08-23.
#
# The loop below only zeroes a timer on a RELEASE event. On this car the SCCM clears the button bit
# between frames, so one physical press arrives as a burst of PRESS events at the frame rate -- and
# the release is not reliably among them. Route 000003ae, t+140..260:
#
#     88 events with pressed=True, ONE with pressed=False
#
# The last press was at t+154.48 and no event of any kind followed until t+269. So the timer sat
# above zero for the whole intervening 115 s, and ICBM's press-settle stand-down re-arms itself
# every frame any manual-override timer is non-zero -- which made `update_manual_override` return
# before its hold-clearing rule on every one of those frames. His report: the hold sat on SLA's own
# number from t+158.4 to t+245.6 and would not clear, for the third time.
#
# THE TIMER IS ALREADY "FRAMES SINCE THE LAST PRESS EVENT", because every press event resets it to
# 1 below. So a cap is all that is needed, and on THIS car it cannot cut a real press short: the
# bursts keep arriving while a button is held and keep resetting it, with a longest observed gap
# WITHIN a held press of 0.72 s.
#
# TEN SECONDS, NOT TWO, AND THE MARGIN IS THE WHOLE POINT. This function is SHARED -- ICBM's
# controller and `VCruiseHelperSP.enable_button_timers` both run it, and the latter uses it to hold
# engagement off until buttons are released. A car whose stalk sends ONE press event and one release
# gets no resets at all, so any cap expires mid-hold there, and the failure that produces is already
# written down: a cap expiring mid-hold froze the baseline and ICBM walked the set speed back down
# one increment at a time while the button was still pressed, reported exactly that way.
#
# 2 s was chosen first and is only ~3x the single measured worst-case gap, from one window of one
# route. 10 s is 14x it, still an order of magnitude below the 115 s stick this exists to fix, and
# longer than any plausible physical press -- a 10 s hold on this car moves the set speed ~50 mph.
#
# This is deliberately not a fix to the press-settle re-arm. That re-arm is correct and was itself
# added to fix the mid-hold report above. The defect is the input it trusts, not the rule.
BUTTON_STALE_S = 10.0

V_CRUISE_MIN = 8
V_CRUISE_MAX = 145
V_CRUISE_UNSET = 255


def update_manual_button_timers(CS: car.CarState, button_timers: dict[car.CarState.ButtonEvent.Type, int],
                                stale_s: float = BUTTON_STALE_S) -> None:
  # increment timer for buttons still pressed
  #
  # FusionPilot: `stale_s` is a PARAMETER because the two callers want different answers. The engage
  # gate in `VCruiseHelperSP` holds openpilot off until buttons are released, and being wrong there
  # engages the car mid-press -- so it keeps the long, conservative default. ICBM's copy only feeds
  # its press-settle stand-down, where being slow is what he reports as "the hold will not clear",
  # and 10 s of that is 10 s of a hold he has already dismissed still governing the car.
  stale = int(stale_s / DT_CTRL)
  for k in button_timers:
    if button_timers[k] > 0:
      button_timers[k] += 1
      # FusionPilot: and give up on a press whose release never arrived. See BUTTON_STALE_S.
      if button_timers[k] > stale:
        button_timers[k] = 0

  for b in CS.buttonEvents:
    if b.type.raw in button_timers:
      # Start/end timer and store current state on change of button pressed
      button_timers[b.type.raw] = 1 if b.pressed else 0


class VCruiseHelperSP:
  def __init__(self, CP: structs.CarParams, CP_SP: structs.CarParamsSP) -> None:
    self.CP = CP
    self.CP_SP = CP_SP
    self.v_cruise_kph = V_CRUISE_UNSET
    self.v_cruise_cluster_kph = V_CRUISE_UNSET
    self.params = Params()
    self.v_cruise_min = 0
    self.enabled_prev = False

    self.custom_acc_enabled = self.params.get_bool("CustomAccIncrementsEnabled")
    self.short_increment = self.params.get("CustomAccShortPressIncrement", return_default=True)
    self.long_increment = self.params.get("CustomAccLongPressIncrement", return_default=True)

    # BluePilot: a COPY. Binding the module-level dict directly shares these timers with every
    # other holder of CRUISE_BUTTON_TIMER -- ICBM's controller binds it too and runs its own
    # update_manual_button_timers over the same object.
    self.enable_button_timers = dict(CRUISE_BUTTON_TIMER)

    # Speed Limit Assist
    self.sla_state = SpeedLimitAssistState.disabled
    self.prev_sla_state = SpeedLimitAssistState.disabled
    self.has_speed_limit = False
    self.speed_limit_final_last = 0.
    self.speed_limit_final_last_kph = 0.
    self.prev_speed_limit_final_last_kph = 0.
    self.req_plus = False
    self.req_minus = False

  def read_custom_set_speed_params(self) -> None:
    self.custom_acc_enabled = self.params.get_bool("CustomAccIncrementsEnabled")
    self.short_increment = self.params.get("CustomAccShortPressIncrement", return_default=True)
    self.long_increment = self.params.get("CustomAccLongPressIncrement", return_default=True)

  def update_v_cruise_delta(self, long_press: bool, v_cruise_delta: float) -> tuple[bool, float]:
    if not self.custom_acc_enabled:
      v_cruise_delta = v_cruise_delta * (5 if long_press else 1)
      return long_press, v_cruise_delta

    # Apply user-specified multipliers to the base increment
    short_increment = np.clip(self.short_increment, 1, 10)
    long_increment = np.clip(self.long_increment, 1, 10)

    actual_increment = long_increment if long_press else short_increment
    round_to_nearest = actual_increment in (5, 10)
    v_cruise_delta = v_cruise_delta * actual_increment

    return round_to_nearest, v_cruise_delta

  def get_minimum_set_speed(self, is_metric: bool) -> None:
    if self.CP_SP.pcmCruiseSpeed:
      self.v_cruise_min = V_CRUISE_MIN
      return

    self.v_cruise_min = get_minimum_set_speed(is_metric)

  def update_enabled_state(self, CS: car.CarState, enabled: bool) -> bool:
    # special enabled state for non pcmCruiseSpeed, unchanged for non pcmCruise
    if not self.CP_SP.pcmCruiseSpeed:
      update_manual_button_timers(CS, self.enable_button_timers)
      button_pressed = any(self.enable_button_timers[k] > 0 for k in self.enable_button_timers)

      if enabled and not self.enabled_prev:
        self.enabled_prev = not button_pressed
        enabled = False
      elif not enabled:
        self.enabled_prev = enabled

      return enabled and self.enabled_prev

    return enabled

  def update_speed_limit_assist(self, is_metric, LP_SP: custom.LongitudinalPlanSP) -> None:
    resolver = LP_SP.speedLimit.resolver
    self.has_speed_limit = resolver.speedLimitValid or resolver.speedLimitLastValid
    self.speed_limit_final_last = LP_SP.speedLimit.resolver.speedLimitFinalLast
    self.speed_limit_final_last_kph = self.speed_limit_final_last * CV.MS_TO_KPH
    self.sla_state = LP_SP.speedLimit.assist.state
    self.req_plus, self.req_minus = compare_cluster_target(self.v_cruise_cluster_kph * CV.KPH_TO_MS,
                                                           self.speed_limit_final_last, is_metric)

  @property
  def update_speed_limit_final_last_changed(self) -> bool:
    return self.has_speed_limit and bool(self.speed_limit_final_last_kph != self.prev_speed_limit_final_last_kph)

  def update_speed_limit_assist_pre_active_confirmed(self, button_type: car.CarState.ButtonEvent.Type) -> bool:
    if self.sla_state == SpeedLimitAssistState.preActive or self.prev_sla_state == SpeedLimitAssistState.preActive:
      if button_type == ButtonType.decelCruise and self.req_minus:
        return True
      if button_type == ButtonType.accelCruise and self.req_plus:
        return True

    return False

  def update_speed_limit_assist_v_cruise_non_pcm(self) -> None:
    if self.sla_state in SLA_ACTIVE_STATES and (self.prev_sla_state not in SLA_ACTIVE_STATES or
                                                self.update_speed_limit_final_last_changed):
      self.v_cruise_kph = np.clip(round(self.speed_limit_final_last_kph, 1), self.v_cruise_min, V_CRUISE_MAX)

    self.prev_sla_state = self.sla_state
    self.prev_speed_limit_final_last_kph = self.speed_limit_final_last_kph
