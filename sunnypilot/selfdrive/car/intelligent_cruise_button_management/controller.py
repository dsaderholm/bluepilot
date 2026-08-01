"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from cereal import car, custom
from opendbc.car import structs, apply_hysteresis
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.common.realtime import DT_CTRL
from openpilot.sunnypilot import PARAMS_UPDATE_PERIOD
from openpilot.sunnypilot.selfdrive.car.intelligent_cruise_button_management.helpers import get_minimum_set_speed
from openpilot.sunnypilot.selfdrive.car.cruise_ext import CRUISE_BUTTON_TIMER, V_CRUISE_MAX, update_manual_button_timers

ButtonType = car.CarState.ButtonEvent.Type
LongitudinalPlanSource = custom.LongitudinalPlanSP.LongitudinalPlanSource
State = custom.IntelligentCruiseButtonManagement.IntelligentCruiseButtonManagementState
SendButtonState = custom.IntelligentCruiseButtonManagement.SendButtonState
OverrideState = custom.IntelligentCruiseButtonManagement.OverrideState
UnconfirmedLeadState = custom.LongitudinalPlanSP.UnconfirmedLead.State

# BluePilot: states in which the radar-blind lead detector owns the target outright
UNCONFIRMED_LEAD_COMMANDING = (UnconfirmedLeadState.active, UnconfirmedLeadState.restoring)

ALLOWED_SPEED_THRESHOLD = 1.8  # m/s, ~4 MPH
HYST_GAP = 0.0  # currently disabled; TODO-SP: might need to be brand-specific
INACTIVE_TIMER = 0.4

# BluePilot: buttons that count as the driver taking the set speed back from ICBM.
# gapAdjustCruise/lkas/mainCruise deliberately excluded -- they don't change the set speed.
MANUAL_OVERRIDE_BUTTONS = (ButtonType.accelCruise, ButtonType.decelCruise, ButtonType.setCruise)

# BluePilot: re-arm policy for the manual override latch. Both conditions are ORed.
# Named rather than inlined so the behavior is greppable and tunable from one place.
RE_ARM_ON_CRUISE_CYCLE = True  # cancel (or any disengage) followed by re-engage returns to AUTO
RE_ARM_ON_NEW_TARGET = True    # a genuinely new target returns to AUTO mid-drive
# How far a new target must sit from BOTH the rejected target and the driver's own set speed
# before it counts as "newly detected" rather than an echo of either. Display units (mph/kph).
RE_ARM_TARGET_DELTA = 2

# BluePilot: target-drop rate limiting. Ford's stock ACC brakes aggressively when the set speed
# falls by roughly 10 mph or more at once, but coasts for smaller drops. Capping each step below
# that threshold and walking larger drops down over several steps keeps the car coasting.
# The cap itself is tunable via the IcbmMaxTargetDrop param; this is only the fallback default.
DEFAULT_MAX_TARGET_DROP = 8  # display units (mph/kph)
# How close actual speed must get to the current step's floor before the next step is allowed.
DROP_STEP_SETTLE_MARGIN = 2  # display units (mph/kph)


SEND_BUTTONS = {
  State.increasing: SendButtonState.increase,
  State.decreasing: SendButtonState.decrease,
}


class IntelligentCruiseButtonManagement:
  def __init__(self, CP: structs.CarParams, CP_SP: structs.CarParamsSP):
    self.CP = CP
    self.CP_SP = CP_SP

    self.v_target = 0
    self.v_cruise_cluster = 0
    self.v_cruise_min = 0
    self.cruise_button = SendButtonState.none
    self.state = State.inactive
    self.pre_active_timer = 0

    self.is_ready = False
    self.is_ready_prev = False
    self.v_target_ms_last = 0.0
    self.is_metric = False

    self.cruise_button_timers = CRUISE_BUTTON_TIMER

    # BluePilot: manual override latch. AUTO = ICBM drives the set speed toward v_target;
    # MANUAL = the driver has taken it back and ICBM stops chasing entirely.
    self.override_state = OverrideState.auto
    self.v_target_overridden = 0
    self.cruise_enabled_prev = False
    self.v_target_valid = False

    # BluePilot: target-drop rate limiting
    self.params = Params()
    self.frame = 0
    self.max_target_drop = DEFAULT_MAX_TARGET_DROP
    self.drop_anchor = 0

    # BluePilot: radar-blind lead detector currently owns the target
    self.unconfirmed_lead_commanding = False

  def update_params(self) -> None:
    if self.frame % int(PARAMS_UPDATE_PERIOD / DT_CTRL) == 0:
      self.max_target_drop = self.params.get("IcbmMaxTargetDrop", return_default=True)

  @property
  def v_cruise_equal(self) -> bool:
    return self.v_target == self.v_cruise_cluster

  def update_calculations(self, CS: car.CarState, LP_SP: custom.LongitudinalPlanSP) -> None:
    speed_conv = CV.MS_TO_KPH if self.is_metric else CV.MS_TO_MPH
    ms_conv = CV.KPH_TO_MS if self.is_metric else CV.MPH_TO_MS

    self.v_target_ms_last = apply_hysteresis(LP_SP.vTarget, self.v_target_ms_last, HYST_GAP * ms_conv)

    self.v_target = round(self.v_target_ms_last * speed_conv)
    self.v_cruise_min = get_minimum_set_speed(self.is_metric)
    self.v_cruise_cluster = round(CS.cruiseState.speedCluster * speed_conv)

    # BluePilot: reject planner targets that aren't real requests, rather than substituting a
    # remembered speed for them.
    #
    # longitudinal_planner clamps vTarget to V_CRUISE_MAX (145 kph) whenever carState.vCruise is
    # still V_CRUISE_UNSET, and publishes 0 before it has run at all. Neither is a speed anyone
    # asked for, so ICBM holds the current cluster speed instead of chasing them.
    #
    # This replaces an earlier fallback that substituted a kph-valued engage speed (vEgo * MS_TO_KPH)
    # into a target that is mph-valued on an imperial device -- at 65 mph that produced v_target=105,
    # which then re-tripped the same >= 90 guard and pinned the state machine. 145 kph rounds to
    # exactly 90 mph, which is why the guard fired on every unset frame.
    v_target_unset = round(V_CRUISE_MAX * CV.KPH_TO_MS * speed_conv)
    self.v_target_valid = 0 < self.v_target < v_target_unset
    if not self.v_target_valid:
      self.v_target = self.v_cruise_cluster

    self.v_target = self.apply_target_drop_limit(round(CS.vEgo * speed_conv))

    # BluePilot: the radar-blind lead detector supersedes everything above, including the drop
    # limiter. That limiter exists to keep Ford's ACC coasting through routine speed-limit and
    # curve changes; metering out a hazard decel over several settling steps is exactly wrong.
    # Its target is already the MPC's own geometry-scaled plan, floored at Ford's 20 mph minimum,
    # so it needs no rate limiting of its own. The same channel carries the restore request that
    # returns the set speed once the event resolves.
    unconfirmed_lead = LP_SP.unconfirmedLead
    self.unconfirmed_lead_commanding = unconfirmed_lead.state in UNCONFIRMED_LEAD_COMMANDING
    if self.unconfirmed_lead_commanding:
      self.v_target = round(unconfirmed_lead.vTarget * speed_conv)
      self.v_target_valid = True
      self.drop_anchor = 0

  def apply_target_drop_limit(self, v_ego_conv: int) -> int:
    """BluePilot: cap how far below the set speed ICBM may command in one step.

    Ford's stock ACC treats a large single drop in set speed as a reason to brake hard; smaller
    drops it handles by coasting. So rather than commanding a curve or speed-limit target all at
    once, hold at (anchor - max_target_drop) and only take the next step once the car has actually
    slowed to that floor. Net deceleration is the same, but it arrives as coasting.

    Only decreases are limited -- increases are what the driver or the ceiling asked for and are
    rate-limited naturally by ICBM emitting one button press per cycle.
    """
    if self.max_target_drop <= 0:  # 0 disables the limiter
      self.drop_anchor = 0
      return self.v_target

    if self.v_target >= self.v_cruise_cluster:
      self.drop_anchor = 0
      return self.v_target

    if self.drop_anchor == 0:
      self.drop_anchor = self.v_cruise_cluster

    floor = self.drop_anchor - self.max_target_drop
    if self.v_target >= floor:
      return self.v_target  # the whole requested drop fits inside one step

    # Requested drop is larger than one step. Advance the anchor only once the cluster has reached
    # the current floor AND actual speed has caught up to it, so each step is a separate, gentle
    # request rather than a continuous slide that Ford reads as one big drop.
    if self.v_cruise_cluster <= floor and v_ego_conv <= floor + DROP_STEP_SETTLE_MARGIN:
      self.drop_anchor = self.v_cruise_cluster
      floor = self.drop_anchor - self.max_target_drop

    return max(self.v_target, floor)

  def update_state_machine(self) -> custom.IntelligentCruiseButtonManagement.SendButtonState:
    self.pre_active_timer = max(0, self.pre_active_timer - 1)

    # HOLDING, ACCELERATING, DECELERATING, PRE_ACTIVE
    if self.state != State.inactive:
      if not self.is_ready:
        self.state = State.inactive

      else:
        # PRE_ACTIVE
        if self.state == State.preActive:
          if self.pre_active_timer <= 0:
            if self.v_cruise_equal:
              self.state = State.holding

            elif self.v_target > self.v_cruise_cluster:
              # BluePilot: don't push a cluster the driver hasn't set yet -- wait for their first SET.
              # The former MAX_REASONABLE_TARGET / MAX_INITIAL_INCREASE caps are gone: unreasonable
              # targets are now rejected in update_calculations, and the upper bound on what ICBM may
              # request belongs to the configurable speed ceiling, not to a fixed +5 from engage speed.
              if self.v_cruise_cluster == 0:
                self.state = State.holding
              else:
                self.state = State.increasing

            elif self.v_target < self.v_cruise_cluster and self.v_cruise_cluster > self.v_cruise_min:
              self.state = State.decreasing

        # HOLDING
        elif self.state == State.holding:
          if not self.v_cruise_equal:
            self.state = State.preActive

        # ACCELERATING
        elif self.state == State.increasing:
          if self.v_target <= self.v_cruise_cluster:
            self.state = State.holding

        # DECELERATING
        elif self.state == State.decreasing:
          if self.v_target >= self.v_cruise_cluster or self.v_cruise_cluster <= self.v_cruise_min:
            self.state = State.holding

    # INACTIVE
    elif self.state == State.inactive:
      if self.is_ready and not self.is_ready_prev:
        self.pre_active_timer = int(INACTIVE_TIMER / DT_CTRL)
        self.state = State.preActive

    send_button = SEND_BUTTONS.get(self.state, SendButtonState.none)

    return send_button

  def update_readiness(self, CS: car.CarState, CC: car.CarControl) -> None:
    update_manual_button_timers(CS, self.cruise_button_timers)

    ready = CC.enabled and not CC.cruiseControl.override and not CC.cruiseControl.cancel and not CC.cruiseControl.resume
    button_pressed = any(self.cruise_button_timers[k] > 0 for k in self.cruise_button_timers)

    # BluePilot: Clear button timers when cruise is disabled to prevent stale presses
    # This ensures that when cruise is re-enabled, ICBM doesn't see stale button presses
    if not ready:
      for k in self.cruise_button_timers:
        self.cruise_button_timers[k] = 0

    self.is_ready = ready and not button_pressed

  def update_manual_override(self, CS: car.CarState) -> None:
    """BluePilot: AUTO/MANUAL latch.

    Every ButtonEvent reaching here is a genuine driver press. ICBM's own virtual presses cannot
    appear: panda returns transmitted frames with src = bus | CAN_RETURNED_BUS_OFFSET (0x80), and
    Ford's CANParser binds Steering_Data_FD1 to bus 0, so the injected frames are dropped by the
    parser before carstate_ext ever decodes them. No sent-command bookkeeping is needed to tell the
    two apart -- if a set-speed button shows up in CS.buttonEvents, a human pressed it.
    """
    cruise_enabled = CS.cruiseState.available and CS.cruiseState.enabled
    cruise_cycled = cruise_enabled and not self.cruise_enabled_prev
    self.cruise_enabled_prev = cruise_enabled

    # Re-arm on cruise cancel + re-engage.
    if RE_ARM_ON_CRUISE_CYCLE and cruise_cycled:
      self.override_state = OverrideState.auto
      return

    # A real driver press latches MANUAL and records the target they rejected.
    if any(b.type.raw in MANUAL_OVERRIDE_BUTTONS and b.pressed for b in CS.buttonEvents):
      if self.override_state != OverrideState.manual:
        self.v_target_overridden = self.v_target
      self.override_state = OverrideState.manual
      return

    if self.override_state != OverrideState.manual or not RE_ARM_ON_NEW_TARGET:
      return

    # Re-arm on a newly-detected target: one that differs from the target the driver rejected AND
    # from the set speed they chose instead. Requiring both keeps a press from instantly re-arming
    # itself -- the driver's own press moves carState.vCruise, and therefore v_target, within a few
    # frames, so a bare "target changed" test would never hold MANUAL at all.
    differs_from_rejected = abs(self.v_target - self.v_target_overridden) >= RE_ARM_TARGET_DELTA
    differs_from_driver = abs(self.v_target - self.v_cruise_cluster) >= RE_ARM_TARGET_DELTA
    if self.v_target_valid and differs_from_rejected and differs_from_driver:
      self.override_state = OverrideState.auto

  def run(self, CS: car.CarState, CC: car.CarControl, LP_SP: custom.LongitudinalPlanSP, is_metric: bool) -> None:
    if self.CP_SP.pcmCruiseSpeed:
      return

    self.is_metric = is_metric

    self.update_params()
    self.update_calculations(CS, LP_SP)
    self.update_readiness(CS, CC)
    self.update_manual_override(CS)

    # BluePilot: in MANUAL, stop chasing the target entirely and let the driver's press stand.
    # is_ready_prev is still advanced so the inactive -> preActive edge fires correctly on re-arm.
    #
    # The radar-blind lead detector is the one thing that overrides MANUAL. A driver adjusting
    # their set speed earlier in the drive should not disable hazard response for the rest of it,
    # and the same applies to the restore that follows. The latch is cleared rather than merely
    # bypassed so that behaviour after the event is unambiguous.
    if self.unconfirmed_lead_commanding:
      self.override_state = OverrideState.auto

    if self.override_state == OverrideState.manual:
      self.state = State.inactive
      self.cruise_button = SendButtonState.none
    else:
      self.cruise_button = self.update_state_machine()

    self.is_ready_prev = self.is_ready
    self.frame += 1
