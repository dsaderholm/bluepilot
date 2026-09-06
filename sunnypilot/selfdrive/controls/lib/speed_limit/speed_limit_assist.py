"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import time

from cereal import custom, car
from openpilot.common.params import Params
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.controls.lib.drive_helpers import CONTROL_N
from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.sunnypilot import PARAMS_UPDATE_PERIOD
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit import PCM_LONG_REQUIRED_MAX_SET_SPEED, CONFIRM_SPEED_THRESHOLD
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.common import Mode
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.helpers import compare_cluster_target, set_speed_limit_assist_availability

ButtonType = car.CarState.ButtonEvent.Type
EventNameSP = custom.OnroadEventSP.EventName
SpeedLimitAssistState = custom.LongitudinalPlanSP.SpeedLimit.AssistState
SpeedLimitSource = custom.LongitudinalPlanSP.SpeedLimit.Source

ACTIVE_STATES = (SpeedLimitAssistState.active, SpeedLimitAssistState.adapting)

# FusionPilot: how long before SLA may announce taking the set speed again. This is the ALERT'S OWN
# DURATION (5.0 s, see `speedLimitChanged` / `speedLimitActive` in sunnypilot's events.py), not a
# number anyone picked: a second announcement inside that window arrives while the first is still on
# screen, so it can only ever be redundant. Route 00000427 fired three in 1.5 s off one flicker.
ANNOUNCE_COOLDOWN_S = 5.0
ANNOUNCE_COOLDOWN_FRAMES = int(ANNOUNCE_COOLDOWN_S / DT_MDL)

# FusionPilot 2026-09-05, ROUND TWO. `speedLimitAutoSet` needed the same guard and the first audit
# missed it. That audit checked the right thing on the wrong sample: on route 00000427 the alert
# fired ONCE, so its repeat column read 0 and it was left alone as "not a spam source". Across all
# five pulls it is 28 fires with **17 of them (61%) inside the previous one's on-screen window**.
#
# ITS DURATION IS 4.0 s, NOT 5.0 -- `speed_limit_auto_set_alert` renders for 4. Reusing the other
# constant would have been a number nobody measured, which is the whole failure this guard exists to
# avoid. One alert, one cooldown, each equal to its own time on screen.
AUTO_SET_COOLDOWN_S = 4.0
AUTO_SET_COOLDOWN_FRAMES = int(AUTO_SET_COOLDOWN_S / DT_MDL)
ENABLED_STATES = (SpeedLimitAssistState.preActive, SpeedLimitAssistState.pending, *ACTIVE_STATES)

DISABLED_GUARD_PERIOD = 0.5  # secs.
# secs. Time to wait after activation before considering temp deactivation signal.
PRE_ACTIVE_GUARD_PERIOD = {
  True: 15,
  False: 5,
}
SPEED_LIMIT_CHANGED_HOLD_PERIOD = 1  # secs. Time to wait after speed limit change before switching to preActive.

LIMIT_MIN_ACC = -1.5  # m/s^2 Maximum deceleration allowed for limit controllers to provide.
LIMIT_MAX_ACC = 1.0   # m/s^2 Maximum acceleration allowed for limit controllers to provide while active.
LIMIT_MIN_SPEED = 8.33  # m/s, Minimum speed limit to provide as solution on limit controllers.
LIMIT_SPEED_OFFSET_TH = -1.  # m/s Maximum offset between speed limit and current speed for adapting state.
V_CRUISE_UNSET = 255.

CRUISE_BUTTONS_PLUS = (ButtonType.accelCruise, ButtonType.resumeCruise)
CRUISE_BUTTONS_MINUS = (ButtonType.decelCruise, ButtonType.setCruise)
CRUISE_BUTTON_CONFIRM_HOLD = 0.5  # secs.


class SpeedLimitAssist:
  _speed_limit_final_last: float
  _distance: float
  v_ego: float
  a_ego: float
  v_offset: float

  def __init__(self, CP: car.CarParams, CP_SP: custom.CarParamsSP):
    self.params = Params()
    self.CP = CP
    self.CP_SP = CP_SP
    self.frame = -1
    self.long_engaged_timer = 0
    self.pre_active_timer = 0
    self.is_metric = self.params.get_bool("IsMetric")
    set_speed_limit_assist_availability(self.CP, self.CP_SP, self.params)
    self.enabled = self.params.get("SpeedLimitMode", return_default=True) == Mode.assist
    # BluePilot: bidirectional following + ceiling. Upstream sunnypilot deliberately never raises
    # the set speed without driver confirmation; this fork follows the limit in both directions.
    self.auto_follow = self.params.get_bool("SpeedLimitAutoFollow")
    self.max_set_speed = self.params.get("SpeedLimitMaxSetSpeed", return_default=True)
    self.long_enabled = False
    self.long_enabled_prev = False
    self.is_enabled = False
    self.is_active = False
    self.output_v_target = V_CRUISE_UNSET
    self.output_a_target = 0.
    self.v_ego = 0.
    self.a_ego = 0.
    self.v_offset = 0.
    self.target_set_speed_conv = 0
    self.prev_target_set_speed_conv = 0
    self.v_cruise_cluster = 0.
    self.v_cruise_cluster_prev = 0.
    self.v_cruise_cluster_conv = 0
    self.prev_v_cruise_cluster_conv = 0
    self._has_speed_limit = False
    self._speed_limit = 0.
    self._speed_limit_final_last = 0.
    self.speed_limit_prev = 0.
    self._frames_since_announce = 1 << 30   # seeded high: never gag the first one
    self._frames_since_auto_set = 1 << 30   # same, for the auto-set announcement
    self.speed_limit_final_last_conv = 0
    self.prev_speed_limit_final_last_conv = 0
    self._gas_pressed = False
    self._distance = 0.
    self.state = SpeedLimitAssistState.disabled
    self._state_prev = SpeedLimitAssistState.disabled
    # FusionPilot: `and CP_SP.pcmCruiseSpeed` -- THE THIRD CONDITION, and without it SLA demands a
    # protocol this car does not need.
    #
    # `pcm_op_long` means "openpilot is braking BUT the car's own PCM owns the set speed", so SLA
    # cannot move that number and instead rides below a fixed ceiling -- which is why
    # `target_set_speed_conv` becomes `PCM_LONG_REQUIRED_MAX_SET_SPEED` below. That is the "set your
    # speed to 70 for it to work" he reported twice, and asked, fairly: "why in God's green earth
    # would I ever want to set my speed to 70 just to have it follow the speed limit?"
    #
    # He would not. The premise is false here. `CP.pcmCruise` is True on this car even under op
    # long, but ICBM MOVES THE SET SPEED with button presses -- so the PCM is not the sole owner and
    # the ceiling protocol is for a car this is not. `CP_SP.pcmCruiseSpeed` is exactly the flag that
    # says so: False means something other than the PCM manages the setpoint.
    #
    # Same third-state problem as everything else under the passthrough. Checked rather than
    # assumed on 2026-08-18: fixing the ICBM param alone leaves this True, because `pcmCruiseSpeed`
    # appears nowhere in the original expression -- so the 70 would have survived that fix.
    self.pcm_op_long = CP.openpilotLongitudinalControl and CP.pcmCruise and CP_SP.pcmCruiseSpeed

    self._plus_hold = 0.
    self._minus_hold = 0.
    self._last_carstate_ts = 0.

    # TODO-SP: SLA's own output_a_target for planner
    # Solution functions mapped to respective states
    self.acceleration_solutions = {
      SpeedLimitAssistState.disabled: self.get_current_acceleration_as_target,
      SpeedLimitAssistState.inactive: self.get_current_acceleration_as_target,
      SpeedLimitAssistState.preActive: self.get_current_acceleration_as_target,
      SpeedLimitAssistState.pending: self.get_current_acceleration_as_target,
      SpeedLimitAssistState.adapting: self.get_adapting_state_target_acceleration,
      SpeedLimitAssistState.active: self.get_active_state_target_acceleration,
    }

  @property
  def speed_limit_changed(self) -> bool:
    return self._has_speed_limit and bool(self._speed_limit != self.speed_limit_prev)

  @property
  def v_cruise_cluster_changed(self) -> bool:
    return bool(self.v_cruise_cluster_conv != self.prev_v_cruise_cluster_conv)

  @property
  def target_set_speed_confirmed(self) -> bool:
    return bool(self.v_cruise_cluster_conv == self.target_set_speed_conv)

  @property
  def v_cruise_cluster_below_confirm_speed_threshold(self) -> bool:
    return bool(self.v_cruise_cluster_conv < CONFIRM_SPEED_THRESHOLD[self.is_metric])

  def update_active_event(self, events_sp: EventsSP) -> None:
    """Announce SLA taking the set speed -- but only when it is actually taking it.

    FusionPilot 2026-09-05, from his report: *"It's still telling me set speed changed to the speed
    limit all the time now, even when the set speed didn't change at all"* and *"we were on SLA and
    not a hold and it just kept telling me it changed even though it didn't."*

    **THIS IS A DIFFERENT ALERT FROM THE ONE FIXED ON 2026-08-27, WHICH IS WHY HE SAID "STILL".**
    That fix deferred `speedLimitAutoSet` ("Raising/Lowering set speed to N mph speed limit") to a
    hold. The alert he is describing is `speedLimitChanged`, whose text is literally "Set speed
    changed" -- and its trigger has nothing to do with the set speed changing. It fires on the ENTRY
    EDGE into an active state, and picks the wording purely from whether the cluster is below
    `CONFIRM_SPEED_THRESHOLD` (50 mph here). Measured on route 00000427:

        12 announcements in 13 minutes; 6 of them with the dash NOT MOVING AT ALL
        four of those inside 30 s, dash pinned at 22 with the limit at 22

    **AND THE ENTRY EDGES ARE NOT A BUG -- 9 of the 12 are `disabled -> active`**, i.e. him cycling
    cruise at lights on a surface street (35.5% of that drive had cruise off). So the churn is his
    driving and the announcement is the thing that is wrong: re-engaging cruise on a road whose
    limit the set speed already sits at is not a change and must not be announced as one.

    TWO GUARDS, and they kill different halves of the 12:

    1. `target_set_speed_confirmed` -- the set speed ALREADY equals the target, so nothing is going
       to happen. Saying nothing is correct; rewording it to `speedLimitActive` ("Auto adjusting to
       speed limit") would be equally untrue and chimes identically.
    2. The re-announce cooldown, which is the ALERT'S OWN DURATION rather than an invented number.
       Both alerts render for 5.0 s, so a second announcement inside that window lands while the
       first is still on screen -- definitionally redundant. Route 00000427 t+375 fired three times
       in 1.5 s off an `active -> inactive -> active` flicker: three chimes, one real change.
    """
    if self.target_set_speed_confirmed:
      return

    if self._frames_since_announce < ANNOUNCE_COOLDOWN_FRAMES:
      return
    self._frames_since_announce = 0

    if self.v_cruise_cluster_below_confirm_speed_threshold:
      events_sp.add(EventNameSP.speedLimitChanged)
    else:
      events_sp.add(EventNameSP.speedLimitActive)

  def get_v_target_from_control(self) -> float:
    if self._has_speed_limit:
      if self.pcm_op_long and self.is_enabled:
        return self._speed_limit_final_last
      if not self.pcm_op_long and self.is_active:
        return self._speed_limit_final_last

    # Fallback
    return V_CRUISE_UNSET

  # TODO-SP: SLA's own output_a_target for planner
  def get_a_target_from_control(self) -> float:
    return self.a_ego

  def update_params(self) -> None:
    if self.frame % int(PARAMS_UPDATE_PERIOD / DT_MDL) == 0:
      self.is_metric = self.params.get_bool("IsMetric")
      set_speed_limit_assist_availability(self.CP, self.CP_SP, self.params)
      self.enabled = self.params.get("SpeedLimitMode", return_default=True) == Mode.assist
      self.auto_follow = self.params.get_bool("SpeedLimitAutoFollow")
      self.max_set_speed = self.params.get("SpeedLimitMaxSetSpeed", return_default=True)

  def update_car_state(self, CS: car.CarState) -> None:
    now = time.monotonic()
    self._last_carstate_ts = now
    self._gas_pressed = bool(CS.gasPressed)

    for b in CS.buttonEvents:
      if not b.pressed:
        if b.type in CRUISE_BUTTONS_PLUS:
          self._plus_hold = max(self._plus_hold, now + CRUISE_BUTTON_CONFIRM_HOLD)
        elif b.type in CRUISE_BUTTONS_MINUS:
          self._minus_hold = max(self._minus_hold, now + CRUISE_BUTTON_CONFIRM_HOLD)

  def _get_button_release(self, req_plus: bool, req_minus: bool) -> bool:
    now = time.monotonic()
    if req_plus and now <= self._plus_hold:
      self._plus_hold = 0.
      return True
    elif req_minus and now <= self._minus_hold:
      self._minus_hold = 0.
      return True

    # expired
    if now > self._plus_hold:
      self._plus_hold = 0.
    if now > self._minus_hold:
      self._minus_hold = 0.
    return False

  def update_calculations(self, v_cruise_cluster: float) -> None:
    speed_conv = CV.MS_TO_KPH if self.is_metric else CV.MS_TO_MPH
    self.v_cruise_cluster = v_cruise_cluster

    # Update current velocity offset (error)
    self.v_offset = self._speed_limit_final_last - self.v_ego

    self.speed_limit_final_last_conv = round(self._speed_limit_final_last * speed_conv)
    self.v_cruise_cluster_conv = round(self.v_cruise_cluster * speed_conv)

    cst_low, cst_high = PCM_LONG_REQUIRED_MAX_SET_SPEED[self.is_metric]
    pcm_long_required_max = cst_low if self._has_speed_limit and self.speed_limit_final_last_conv < CONFIRM_SPEED_THRESHOLD[self.is_metric] else \
                            cst_high
    pcm_long_required_max_set_speed_conv = round(pcm_long_required_max * speed_conv)

    self.target_set_speed_conv = pcm_long_required_max_set_speed_conv if self.pcm_op_long else self.speed_limit_final_last_conv

  @property
  def max_set_speed_ms(self) -> float:
    return self.max_set_speed * (CV.KPH_TO_MS if self.is_metric else CV.MPH_TO_MS)

  @property
  def cluster_converging(self) -> bool:
    """BluePilot: True when the set speed moved toward the target rather than away from it.

    With ICBM driving the cluster, SLA's "driver changed the set speed, stand down" check would
    otherwise fire on ICBM's own convergence and immediately deactivate the assist it is serving.
    Movement toward the target is ICBM working; movement away is the driver, and item 1's manual
    override latch handles that case on the ICBM side.
    """
    if not self.auto_follow:
      return False
    before = abs(self.prev_v_cruise_cluster_conv - self.target_set_speed_conv)
    after = abs(self.v_cruise_cluster_conv - self.target_set_speed_conv)
    return after < before

  @property
  def apply_confirm_speed_threshold(self) -> bool:
    # BluePilot: bidirectional auto-follow never asks for confirmation, in either direction.
    # The ceiling bounds what can be requested and the manual override latch is the safety valve.
    if self.auto_follow:
      return False

    # below CST: always require user confirmation
    if self.v_cruise_cluster_below_confirm_speed_threshold:
      return True

    # at/above CST:
    # - new speed limit >= CST: auto change
    # - new speed limit < CST: user confirmation required
    return bool(self.speed_limit_final_last_conv < CONFIRM_SPEED_THRESHOLD[self.is_metric])

  def get_current_acceleration_as_target(self) -> float:
    return self.a_ego

  def get_adapting_state_target_acceleration(self) -> float:
    if self._distance > 0:
      return (self._speed_limit_final_last ** 2 - self.v_ego ** 2) / (2. * self._distance)

    return self.v_offset / float(ModelConstants.T_IDXS[CONTROL_N])

  def get_active_state_target_acceleration(self) -> float:
    return self.v_offset / float(ModelConstants.T_IDXS[CONTROL_N])

  def _update_confirmed_state(self):
    if self._has_speed_limit:
      if self.v_offset < LIMIT_SPEED_OFFSET_TH:
        self.state = SpeedLimitAssistState.adapting
      else:
        self.state = SpeedLimitAssistState.active
    else:
      self.state = SpeedLimitAssistState.pending

  def _update_non_pcm_long_confirmed_state(self) -> bool:
    if self.target_set_speed_confirmed:
      return True

    # BluePilot: in auto-follow there is nothing to confirm -- ICBM drives the cluster to the
    # target itself, so the assist activates as soon as a limit is available.
    if self.auto_follow and self._has_speed_limit:
      return True

    if self.state != SpeedLimitAssistState.preActive:
      return False

    req_plus, req_minus = compare_cluster_target(self.v_cruise_cluster, self._speed_limit_final_last, self.is_metric)

    return self._get_button_release(req_plus, req_minus)

  def update_state_machine_pcm_op_long(self):
    self.long_engaged_timer = max(0, self.long_engaged_timer - 1)
    self.pre_active_timer = max(0, self.pre_active_timer - 1)

    # ACTIVE, ADAPTING, PENDING, PRE_ACTIVE, INACTIVE
    if self.state != SpeedLimitAssistState.disabled:
      if not self.long_enabled or not self.enabled:
        self.state = SpeedLimitAssistState.disabled

      else:
        # ACTIVE
        if self.state == SpeedLimitAssistState.active:
          if self.v_cruise_cluster_changed:
            self.state = SpeedLimitAssistState.inactive
          elif self.speed_limit_changed and self.apply_confirm_speed_threshold:
            self.state = SpeedLimitAssistState.preActive
            self.pre_active_timer = int(PRE_ACTIVE_GUARD_PERIOD[self.pcm_op_long] / DT_MDL)
          elif self._has_speed_limit and self.v_offset < LIMIT_SPEED_OFFSET_TH:
            self.state = SpeedLimitAssistState.adapting

        # ADAPTING
        elif self.state == SpeedLimitAssistState.adapting:
          if self.v_cruise_cluster_changed:
            self.state = SpeedLimitAssistState.inactive
          elif self.speed_limit_changed and self.apply_confirm_speed_threshold:
            self.state = SpeedLimitAssistState.preActive
            self.pre_active_timer = int(PRE_ACTIVE_GUARD_PERIOD[self.pcm_op_long] / DT_MDL)
          elif self.v_offset >= LIMIT_SPEED_OFFSET_TH:
            self.state = SpeedLimitAssistState.active

        # PENDING
        elif self.state == SpeedLimitAssistState.pending:
          if self.target_set_speed_confirmed:
            self._update_confirmed_state()
          elif self.speed_limit_changed:
            self.state = SpeedLimitAssistState.preActive
            self.pre_active_timer = int(PRE_ACTIVE_GUARD_PERIOD[self.pcm_op_long] / DT_MDL)

        # PRE_ACTIVE
        elif self.state == SpeedLimitAssistState.preActive:
          # BluePilot: For PCM cars using ICBM, check target_set_speed_confirmed directly
          # ICBM adjusts cluster via CAN, so button events won't be in CS.buttonEvents
          # We need to check if cluster matches target after ICBM has adjusted it
          if self.CP_SP.intelligentCruiseButtonManagementAvailable:
            # BluePilot: For ICBM vehicles, rely on target_set_speed_confirmed check instead of button detection
            # ICBM sends button presses via CAN directly, not through CS.buttonEvents
            if self.target_set_speed_confirmed:
              self._update_confirmed_state()
            elif self.pre_active_timer <= 0:
              # Timeout - session ended
              self.state = SpeedLimitAssistState.inactive
          elif self.target_set_speed_confirmed:
            self._update_confirmed_state()
          elif self.pre_active_timer <= 0:
            # Timeout - session ended
            self.state = SpeedLimitAssistState.inactive

        # INACTIVE
        elif self.state == SpeedLimitAssistState.inactive:
          pass

    # DISABLED
    elif self.state == SpeedLimitAssistState.disabled:
      if self.long_enabled and self.enabled:
        # start or reset preActive timer if initially enabled or manual set speed change detected
        # FusionPilot: `and not self.cluster_converging` -- WITHOUT IT SLA NEVER LEAVES DISABLED.
        #
        # This timer is the "wait for the driver to stop fiddling" guard, and `v_cruise_cluster_changed`
        # is its proxy for the driver. That proxy is false on this car: SCC-Map, SCC-Vision and ICBM
        # all move `v_cruise_cluster` themselves, so the timer was reset on nearly every frame and
        # never reached zero. Route 389, 2026-08-18: `disabled` 1009 frames, `preActive` 978,
        # `inactive` 4013 and **`active` exactly ZERO** across the whole drive, while the map supplied
        # good limits (40/25/20/30 mph) throughout. He reported it as "I'm not sure if SLA was even
        # working". It was not.
        #
        # `cluster_converging` is the existing answer to exactly this question -- set speed moving
        # TOWARD our target is the system working, moving away is the driver. The ACTIVE branch
        # already uses it; these two were written before it existed and never picked it up. It
        # returns False whenever auto-follow is off, so this changes nothing for anyone not using it.
        # THE COST OF THIS, stated because it is a real trade and not a free win. In `disabled`
        # SLA is not serving a target, so `cluster_converging` cannot mean what its docstring says
        # ("ICBM driving the cluster toward the target") -- it only means the number moved toward
        # the limit. Winding 45 down to 30 in traffic passes through 40, and those steps read as
        # converging, so SLA can come on at 40 while he is still lowering. The ACTIVE branch then
        # releases it as soon as he continues past.
        #
        # Taken deliberately: the alternative measured on route 389 is SLA never activating at all,
        # for the whole drive, and auto-follow is him asking SLA to manage the number in the first
        # place. Narrowing this to `and self.is_active` was tried and is WRONG -- `is_active` is
        # False by definition inside the `disabled` branch, so that guard cannot fire and silently
        # restores the broken behaviour.
        if not self.long_enabled_prev or (self.v_cruise_cluster_changed and not self.cluster_converging):
          self.long_engaged_timer = int(DISABLED_GUARD_PERIOD / DT_MDL)

        elif self.long_engaged_timer <= 0:
          if self.target_set_speed_confirmed:
            self._update_confirmed_state()
          elif self._has_speed_limit:
            self.state = SpeedLimitAssistState.preActive
            self.pre_active_timer = int(PRE_ACTIVE_GUARD_PERIOD[self.pcm_op_long] / DT_MDL)
          else:
            self.state = SpeedLimitAssistState.pending

    enabled = self.state in ENABLED_STATES
    active = self.state in ACTIVE_STATES

    return enabled, active

  def update_state_machine_non_pcm_long(self):
    self.long_engaged_timer = max(0, self.long_engaged_timer - 1)
    self.pre_active_timer = max(0, self.pre_active_timer - 1)

    # ACTIVE, ADAPTING, PENDING, PRE_ACTIVE, INACTIVE
    if self.state != SpeedLimitAssistState.disabled:
      if not self.long_enabled or not self.enabled:
        self.state = SpeedLimitAssistState.disabled

      else:
        # ACTIVE
        if self.state == SpeedLimitAssistState.active:
          # BluePilot: THE GAS PEDAL IS NOT A SET-SPEED PRESS.
          #
          # `v_cruise_cluster_changed` is this state machine's proxy for "the driver took the set
          # speed back", and on this car the set speed moves for reasons that are not the driver:
          # `_update_v_cruise` floors it at `max(v_cruise_kph, vEgo)`, so overriding on the throttle
          # drags it up to whatever speed the car reaches, one display unit at a time.
          #
          # MEASURED on route 000003b7, 2026-08-24: across 20 gas-override episodes SLA left and
          # re-entered `active` 339 times with ZERO driver button events in them -- 96 flips in one
          # 32 s pull where `vCruiseCluster` walked 75 -> 85 while the real dash sat at 75. Every
          # re-entry announces "Set speed changed" with a chime and flips the set-speed number
          # between green and white. He reported it exactly that way: *"it made the noise and said
          # changing set speed when I overrode cruise with the gas"*.
          #
          # `cluster_converging` cannot cover this: the movement is AWAY from the target, which is
          # precisely what that property is built to call a driver takeover. Nothing else here
          # changes -- a real press while off the gas still stands SLA down on the same frame.
          if self.v_cruise_cluster_changed and not self.cluster_converging and not self._gas_pressed:
            self.state = SpeedLimitAssistState.inactive

          elif self.speed_limit_changed and self.apply_confirm_speed_threshold:
            self.state = SpeedLimitAssistState.preActive
            self.pre_active_timer = int(PRE_ACTIVE_GUARD_PERIOD[self.pcm_op_long] / DT_MDL)

        # PRE_ACTIVE
        elif self.state == SpeedLimitAssistState.preActive:
          if self._update_non_pcm_long_confirmed_state():
            self.state = SpeedLimitAssistState.active
          elif self.pre_active_timer <= 0:
            # Timeout - session ended
            self.state = SpeedLimitAssistState.inactive

        # INACTIVE
        elif self.state == SpeedLimitAssistState.inactive:
          if self.speed_limit_changed:
            self.state = SpeedLimitAssistState.preActive
            self.pre_active_timer = int(PRE_ACTIVE_GUARD_PERIOD[self.pcm_op_long] / DT_MDL)
          elif self._update_non_pcm_long_confirmed_state():
            self.state = SpeedLimitAssistState.active

    # DISABLED
    elif self.state == SpeedLimitAssistState.disabled:
      if self.long_enabled and self.enabled:
        # start or reset preActive timer if initially enabled or manual set speed change detected
        # FusionPilot: `and not self.cluster_converging` -- WITHOUT IT SLA NEVER LEAVES DISABLED.
        #
        # This timer is the "wait for the driver to stop fiddling" guard, and `v_cruise_cluster_changed`
        # is its proxy for the driver. That proxy is false on this car: SCC-Map, SCC-Vision and ICBM
        # all move `v_cruise_cluster` themselves, so the timer was reset on nearly every frame and
        # never reached zero. Route 389, 2026-08-18: `disabled` 1009 frames, `preActive` 978,
        # `inactive` 4013 and **`active` exactly ZERO** across the whole drive, while the map supplied
        # good limits (40/25/20/30 mph) throughout. He reported it as "I'm not sure if SLA was even
        # working". It was not.
        #
        # `cluster_converging` is the existing answer to exactly this question -- set speed moving
        # TOWARD our target is the system working, moving away is the driver. The ACTIVE branch
        # already uses it; these two were written before it existed and never picked it up. It
        # returns False whenever auto-follow is off, so this changes nothing for anyone not using it.
        # THE COST OF THIS, stated because it is a real trade and not a free win. In `disabled`
        # SLA is not serving a target, so `cluster_converging` cannot mean what its docstring says
        # ("ICBM driving the cluster toward the target") -- it only means the number moved toward
        # the limit. Winding 45 down to 30 in traffic passes through 40, and those steps read as
        # converging, so SLA can come on at 40 while he is still lowering. The ACTIVE branch then
        # releases it as soon as he continues past.
        #
        # Taken deliberately: the alternative measured on route 389 is SLA never activating at all,
        # for the whole drive, and auto-follow is him asking SLA to manage the number in the first
        # place. Narrowing this to `and self.is_active` was tried and is WRONG -- `is_active` is
        # False by definition inside the `disabled` branch, so that guard cannot fire and silently
        # restores the broken behaviour.
        if not self.long_enabled_prev or (self.v_cruise_cluster_changed and not self.cluster_converging):
          self.long_engaged_timer = int(DISABLED_GUARD_PERIOD / DT_MDL)

        elif self.long_engaged_timer <= 0:
          if self._update_non_pcm_long_confirmed_state():
            self.state = SpeedLimitAssistState.active
          elif self._has_speed_limit:
            self.state = SpeedLimitAssistState.preActive
            self.pre_active_timer = int(PRE_ACTIVE_GUARD_PERIOD[self.pcm_op_long] / DT_MDL)
          else:
            self.state = SpeedLimitAssistState.inactive

    enabled = self.state in ENABLED_STATES
    active = self.state in ACTIVE_STATES

    return enabled, active

  def update_events(self, events_sp: EventsSP, v_baseline_conv: float = 0.0) -> None:
    self._frames_since_announce = min(self._frames_since_announce + 1, 1 << 30)
    self._frames_since_auto_set = min(self._frames_since_auto_set + 1, 1 << 30)
    # BluePilot: announce every automatic set-speed change, raise or lower, so a bad limit is
    # seen rather than only felt. Fires on the target changing, which is when the assist commits.
    #
    # FusionPilot: A HOLD OUTRANKS THE POSTED LIMIT, SO DO NOT ANNOUNCE A CHANGE THAT CANNOT
    # HAPPEN. 2026-08-27, from his report: "it said setting speed to speed limit when it was
    # actually setting it to a hold... even though it was already set to a hold and the speed
    # limit must've updated."
    #
    # The alert text is `f"{direction} set speed to {target} {unit} speed limit"` where `target`
    # is SLA's own number. Under ICBM the car is driven to `apply_baseline(...)` -- the hold --
    # so with a hold in force the announcement names a speed the set speed will never reach, and
    # it fires again on every map limit change while nothing on the car moves. That is exactly
    # the "computed correctly and rendered wrongly" shape this fork keeps hitting, except here
    # the value is right and the SENTENCE is wrong.
    #
    # Gated on the hold DIFFERING from the new limit: a hold that equals the limit is about to be
    # cleared by the clearing rule, and the set speed genuinely does end up at the limit, so that
    # announcement is true and is kept.
    if (self.auto_follow and self.is_active and
        self.speed_limit_final_last_conv != self.prev_speed_limit_final_last_conv):
      hold_wins = v_baseline_conv > 0 and round(v_baseline_conv) != round(self.speed_limit_final_last_conv)
      # The cooldown sits BELOW the hold check on purpose: an announcement the hold suppressed was
      # never made, so it must not spend the window and mute a real one behind it. Same ordering as
      # `target_set_speed_confirmed` in update_active_event, and a test pins it.
      if not hold_wins and self._frames_since_auto_set >= AUTO_SET_COOLDOWN_FRAMES:
        self._frames_since_auto_set = 0
        events_sp.add(EventNameSP.speedLimitAutoSet)

    if self.state == SpeedLimitAssistState.preActive:
      events_sp.add(EventNameSP.speedLimitPreActive)

    if self.state == SpeedLimitAssistState.pending and self._state_prev != SpeedLimitAssistState.pending:
      events_sp.add(EventNameSP.speedLimitPending)

    if self.is_active:
      if self._state_prev not in ACTIVE_STATES:
        self.update_active_event(events_sp)

      # only notify if we acquire a valid speed limit
      # do not check has_speed_limit here
      elif self._speed_limit != self.speed_limit_prev:
        if self.speed_limit_prev <= 0:
          self.update_active_event(events_sp)
        elif self.speed_limit_prev > 0 and self._speed_limit > 0:
          self.update_active_event(events_sp)

  def update(self, long_enabled: bool, long_override: bool, v_ego: float, a_ego: float, v_cruise_cluster: float, speed_limit: float,
             speed_limit_final_last: float, has_speed_limit: bool, distance: float, events_sp: EventsSP,
             v_baseline_conv: float = 0.0) -> None:
    self.long_enabled = long_enabled
    self.v_ego = v_ego
    self.a_ego = a_ego

    self._has_speed_limit = has_speed_limit
    self._speed_limit = speed_limit
    self._speed_limit_final_last = speed_limit_final_last
    self._distance = distance

    # BluePilot: never request above the configured ceiling, whatever the detected limit says.
    # Clamped before update_calculations so the confirm/target logic all sees the capped value.
    if self.auto_follow:
      self._speed_limit_final_last = min(self._speed_limit_final_last, self.max_set_speed_ms)

    self.update_params()
    self.update_calculations(v_cruise_cluster)

    self._state_prev = self.state
    if self.pcm_op_long:
      self.is_enabled, self.is_active = self.update_state_machine_pcm_op_long()
    else:
      self.is_enabled, self.is_active = self.update_state_machine_non_pcm_long()

    self.update_events(events_sp, v_baseline_conv)

    # Update change tracking variables
    self.speed_limit_prev = self._speed_limit
    self.v_cruise_cluster_prev = self.v_cruise_cluster
    self.long_enabled_prev = self.long_enabled
    self.prev_target_set_speed_conv = self.target_set_speed_conv
    self.prev_v_cruise_cluster_conv = self.v_cruise_cluster_conv
    self.prev_speed_limit_final_last_conv = self.speed_limit_final_last_conv

    self.output_v_target = self.get_v_target_from_control()
    self.output_a_target = self.get_a_target_from_control()

    self.frame += 1