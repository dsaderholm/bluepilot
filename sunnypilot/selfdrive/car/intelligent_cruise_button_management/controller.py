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

# BluePilot: states in which the radar-blind lead detector owns the target outright.
# There is no longer a companion "...and this one also cancels the driver's baseline" list: a
# baseline changes the number ICBM aims for, never whether it acts, so a hazard needs no exception
# to get through. It simply replaces v_target like it always did.
UNCONFIRMED_LEAD_COMMANDING = (UnconfirmedLeadState.active, UnconfirmedLeadState.restoring)

ALLOWED_SPEED_THRESHOLD = 1.8  # m/s, ~4 MPH
HYST_GAP = 0.0  # currently disabled; TODO-SP: might need to be brand-specific
INACTIVE_TIMER = 0.4

# BluePilot: buttons that count as the driver taking the set speed back from ICBM.
# gapAdjustCruise/lkas/mainCruise deliberately excluded -- they don't change the set speed.
MANUAL_OVERRIDE_BUTTONS = (ButtonType.accelCruise, ButtonType.decelCruise, ButtonType.setCruise)

# BluePilot: what a driver's set-speed press means.
#
# It is NOT "stop managing my cruise". It is "for this speed limit, I want a different number" --
# faster on a freeway, slower elsewhere. So the press records a BASELINE and every ICBM feature
# keeps running against it: curves still slow the car, the vision-lead trigger still fires, and
# when the reason for slowing passes the set speed returns to the baseline instead of to whatever
# Speed Limit Assist wanted.
#
# An earlier design made a press suspend ICBM outright. That was wrong in both directions: it lost
# curve slowing for the rest of the drive, and it needed an ever-growing set of exceptions to let
# hazards back through. Treating the press as an offset rather than an off switch removes the need
# for any of them.
RE_ARM_ON_CRUISE_CYCLE = True  # cancel (or any disengage) followed by re-engage drops the baseline
# The baseline applies to the speed-limit/cruise component only. A curve target is a physics limit,
# not something to add an offset to -- SCC-Vision asking for 40 means 40, whatever the baseline is.
BASELINE_SOURCES = (LongitudinalPlanSource.cruise, LongitudinalPlanSource.speedLimitAssist)
# How far the posted limit must move before the baseline is discarded and SLA takes over again.
# Fallback only; the live value comes from IcbmBaselineResetDelta.
DEFAULT_BASELINE_RESET_DELTA = 10  # display units (mph/kph)

# BluePilot: target-drop rate limiting. Ford's stock ACC brakes aggressively when the set speed
# falls by roughly 10 mph or more at once, but coasts for smaller drops. Capping each step below
# that threshold and walking larger drops down over several steps keeps the car coasting.
# The cap itself is tunable via the IcbmMaxTargetDrop param; this is only the fallback default.
DEFAULT_MAX_TARGET_DROP = 8  # display units (mph/kph)
# How close actual speed must get to the current step's floor before the next step is allowed.
DROP_STEP_SETTLE_MARGIN = 2  # display units (mph/kph)

# BluePilot: the same treatment in the other direction. An earlier comment claimed increases were
# "rate-limited naturally by ICBM emitting one button press per cycle" -- that is not true on Ford.
# icbm.py holds CcAslButtnSetIncPress high for as long as the state machine sits in `increasing`,
# and Ford reads a held button as a continuous ramp, so recovering from a curve or a speed-limit
# drop slammed the set speed back up as fast as the car could take it. Capping each step and
# waiting for actual speed to catch up turns that back into a series of short presses.
DEFAULT_MAX_TARGET_RISE = 5  # display units (mph/kph)
RISE_STEP_SETTLE_MARGIN = 2  # display units (mph/kph)


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
    self.v_target_overridden = 0   # the SLA target in force when the baseline was set
    self.v_baseline = 0            # the driver's chosen speed; 0 = no baseline, follow SLA
    self.v_target_raw = 0
    self.plan_source = LongitudinalPlanSource.cruise
    self.baseline_reset_delta = DEFAULT_BASELINE_RESET_DELTA
    self.cruise_enabled_prev = False
    self.v_target_valid = False

    # BluePilot: target-drop rate limiting
    self.params = Params()
    self.frame = 0
    self.max_target_drop = DEFAULT_MAX_TARGET_DROP
    self.drop_anchor = 0
    self.max_target_rise = DEFAULT_MAX_TARGET_RISE
    self.rise_anchor = 0

    # BluePilot: radar-blind lead detector currently owns the target
    self.unconfirmed_lead_commanding = False
    self.unconfirmed_lead_state = UnconfirmedLeadState.inactive

  def update_params(self) -> None:
    if self.frame % int(PARAMS_UPDATE_PERIOD / DT_CTRL) == 0:
      self.max_target_drop = self.params.get("IcbmMaxTargetDrop", return_default=True)
      self.max_target_rise = self.params.get("IcbmMaxTargetRise", return_default=True)
      self.baseline_reset_delta = self.params.get("IcbmBaselineResetDelta", return_default=True)

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

    # BluePilot: keep the planner's own target before the limiters touch it. Every override
    # decision compares against this, never against self.v_target -- the limiters clamp toward the
    # cluster, so a limited value drifts for reasons that have nothing to do with what was planned.
    # Comparing post-limiter values is exactly how the original re-arm bug worked.
    self.v_target_raw = self.v_target
    self.plan_source = LP_SP.longitudinalPlanSource
    self.v_target = self.apply_baseline(self.v_target)

    v_ego_conv = round(CS.vEgo * speed_conv)
    self.v_target = self.apply_target_drop_limit(v_ego_conv)

    # BluePilot: the radar-blind lead detector supersedes everything above, including the drop
    # limiter. That limiter exists to keep Ford's ACC coasting through routine speed-limit and
    # curve changes; metering out a hazard decel over several settling steps is exactly wrong.
    # Its target is already the MPC's own geometry-scaled plan, floored at Ford's 20 mph minimum,
    # so it needs no rate limiting of its own. The same channel carries the restore request that
    # returns the set speed once the event resolves.
    unconfirmed_lead = LP_SP.unconfirmedLead
    self.unconfirmed_lead_state = unconfirmed_lead.state
    self.unconfirmed_lead_commanding = unconfirmed_lead.state in UNCONFIRMED_LEAD_COMMANDING
    if self.unconfirmed_lead_commanding:
      self.v_target = round(unconfirmed_lead.vTarget * speed_conv)
      self.v_target_valid = True
      self.drop_anchor = 0

    # BluePilot: the rise limiter runs last, and unlike the drop limiter it is NOT bypassed for
    # the radar-blind lead. Rising is never the urgent direction -- an ACTIVE hazard only ever
    # lowers the target -- so the only thing this can meter is the RESTORING half, which returns
    # the set speed after the hazard has cleared and has no reason to be abrupt.
    self.v_target = self.apply_target_rise_limit(v_ego_conv)

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

  def apply_baseline(self, v_target: int) -> int:
    """BluePilot: substitute the driver's chosen speed for the speed-limit component.

    With no baseline this is the identity. With one:

      speed limit / cruise  -> the baseline outright. This is the component the driver overrode,
                               so their number replaces it. Above or below the posted limit; the
                               baseline wins either way and SLA does not pull them back.
      curve / map / lead    -> min(planned, baseline). A curve target is a physics limit and is
                               honoured as-is; the baseline only ever caps, never raises it. This
                               is what keeps SCC slowing you down while overridden.

    Because the baseline is a value rather than a mode, everything downstream -- the state machine,
    the rate limiters, the hazard path -- keeps working unchanged.
    """
    if self.v_baseline <= 0:
      return v_target

    if self.plan_source in BASELINE_SOURCES:
      return self.v_baseline

    return min(v_target, self.v_baseline)

  def apply_target_rise_limit(self, v_ego_conv: int) -> int:
    """BluePilot: cap how far above the set speed ICBM may command in one step.

    The mirror of apply_target_drop_limit, and needed for the same reason the drop version is:
    ICBM does not tap the button, it holds it. Coming out of a curve or leaving a low-limit zone,
    the target jumps back to cruise speed all at once and Ford ramps continuously until it gets
    there, which is a much harder acceleration than a driver would ask for.

    Hold at (anchor + max_target_rise) and only take the next step once actual speed has caught
    up. Net acceleration ends up the same, but it arrives in stages instead of one pull.
    """
    if self.max_target_rise <= 0:  # 0 disables the limiter
      self.rise_anchor = 0
      return self.v_target

    if self.v_target <= self.v_cruise_cluster:
      self.rise_anchor = 0
      return self.v_target

    if self.rise_anchor == 0:
      self.rise_anchor = self.v_cruise_cluster

    ceiling = self.rise_anchor + self.max_target_rise
    if self.v_target <= ceiling:
      return self.v_target  # the whole requested rise fits inside one step

    if self.v_cruise_cluster >= ceiling and v_ego_conv >= ceiling - RISE_STEP_SETTLE_MARGIN:
      self.rise_anchor = self.v_cruise_cluster
      ceiling = self.rise_anchor + self.max_target_rise

    return min(self.v_target, ceiling)

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
    """BluePilot: capture, hold and discard the driver's baseline.

    Every ButtonEvent reaching here is a genuine driver press. ICBM's own virtual presses cannot
    appear: panda returns transmitted frames with src = bus | CAN_RETURNED_BUS_OFFSET (0x80), and
    Ford's CANParser binds Steering_Data_FD1 to bus 0, so the injected frames are dropped by the
    parser before carstate_ext ever decodes them. No sent-command bookkeeping is needed to tell the
    two apart -- if a set-speed button shows up in CS.buttonEvents, a human pressed it.
    """
    cruise_enabled = CS.cruiseState.available and CS.cruiseState.enabled
    cruise_cycled = cruise_enabled and not self.cruise_enabled_prev
    self.cruise_enabled_prev = cruise_enabled

    # Cancel + re-engage is the driver starting over.
    if RE_ARM_ON_CRUISE_CYCLE and cruise_cycled:
      self.clear_baseline()
      return

    # While the driver is pressing, the baseline follows the cluster. It therefore settles wherever
    # they stop, and holding the button through several increments records the final speed rather
    # than the first. v_target_overridden captures the SLA target being rejected, once per override.
    if any(b.type.raw in MANUAL_OVERRIDE_BUTTONS and b.pressed for b in CS.buttonEvents):
      if self.override_state != OverrideState.manual:
        self.v_target_overridden = self.v_target_raw
      self.override_state = OverrideState.manual
      self.v_baseline = self.v_cruise_cluster
      return

    if self.override_state != OverrideState.manual:
      return

    # Keep tracking while the press is still in flight -- the cluster lags the button by a few
    # frames, so freezing on the first frame after the event would record a stale speed.
    if any(self.cruise_button_timers[k] > 0 for k in self.cruise_button_timers):
      self.v_baseline = self.v_cruise_cluster
      return

    if not self.v_target_valid:
      return

    # Discard the baseline when the posted limit itself moves materially. A new zone is a new
    # situation the driver has not ruled on, and carrying a 55-zone baseline into a 35 zone is
    # exactly the failure worth avoiding.
    #
    # Source-gated deliberately. Magnitude alone cannot tell "entered a school zone" from
    # "SCC-Vision is slowing for a bend", and a curve must never discard the baseline: it ends by
    # itself in seconds, whereas a limit change persists. Only speedLimitAssist counts.
    if (self.plan_source == LongitudinalPlanSource.speedLimitAssist and
        abs(self.v_target_raw - self.v_target_overridden) >= self.baseline_reset_delta):
      self.clear_baseline()

  def clear_baseline(self) -> None:
    self.override_state = OverrideState.auto
    self.v_baseline = 0
    self.v_target_overridden = 0

  def run(self, CS: car.CarState, CC: car.CarControl, LP_SP: custom.LongitudinalPlanSP, is_metric: bool) -> None:
    if self.CP_SP.pcmCruiseSpeed:
      return

    self.is_metric = is_metric

    self.update_params()
    self.update_calculations(CS, LP_SP)
    self.update_readiness(CS, CC)
    self.update_manual_override(CS)

    # BluePilot: the state machine runs unconditionally. A baseline changes WHAT ICBM aims for,
    # not WHETHER it aims -- see apply_baseline. The previous design forced State.inactive here,
    # which is why curve slowing silently stopped working for the rest of a drive after a single
    # button press, and why hazards needed an explicit exception to get back through.
    #
    # With the baseline folded into v_target there is nothing left to except: an ACTIVE radar-blind
    # lead already owns v_target outright further up, and the driver's number cannot suppress it.
    self.cruise_button = self.update_state_machine()
    self.is_ready_prev = self.is_ready

    self.frame += 1
