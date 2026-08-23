"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from enum import Enum

from cereal import messaging, log, car, custom
from openpilot.common.params import Params
from openpilot.selfdrive.ui.sunnypilot.layouts.settings.display import OnroadBrightness
from openpilot.sunnypilot.sunnylink.sunnylink_state import SunnylinkState
from openpilot.system.ui.lib.application import gui_app

OpenpilotState = log.SelfdriveState.OpenpilotState
MADSState = custom.ModularAssistiveDrivingSystem.ModularAssistiveDrivingSystemState

ONROAD_BRIGHTNESS_TIMER_PAUSED = -1


class OnroadTimerStatus(Enum):
  NONE = 0
  PAUSE = 1
  RESUME = 2


class UIStateSP:
  def __init__(self):
    self.params = Params()
    self.CP_SP: custom.CarParamsSP | None = None
    self.has_icbm: bool = False
    self.is_sp_release: bool = self.params.get_bool("IsReleaseSpBranch")
    self.sm_services_ext = [
      "modelManagerSP", "selfdriveStateSP", "longitudinalPlanSP", "backupManagerSP",
      "gpsLocation", "liveTorqueParameters", "carStateSP", "liveMapDataSP", "carParamsSP", "liveDelay",
      "controllerStateBP",  # BluePilot: lateral uncertainty for torque bar
    ]

    self.sunnylink_state = SunnylinkState()

    self.active_bundle = None
    self.blindspot: bool = False
    self.chevron_metrics = None
    self.custom_interactive_timeout: int = 0
    self.developer_ui = None
    self.hide_v_ego_ui: bool = False
    self.onroad_brightness: int = 0
    self.onroad_brightness_timer: int = 0
    self.onroad_brightness_timer_param: int = 0
    self.rainbow_path: bool = False
    self.road_name_toggle: bool = False
    self.rocket_fuel: bool = False
    self.speed_limit_mode = None
    self.standstill_timer: bool = False
    self.sunnylink_enabled: bool = False
    self.torque_bar: bool = False
    self.enforce_torque_control: bool = False
    self.custom_torque_params: bool = False
    self.torque_override_enabled: bool = False
    self._sp_initialized: bool = False

  def update(self) -> None:
    if self.sunnylink_enabled:
      self.sunnylink_state.start()
    else:
      self.sunnylink_state.stop()

  def onroad_brightness_handle_alerts(self, _ui_state, alert):
    if _ui_state.sm.recv_frame["carState"] < _ui_state.started_frame:
      return

    has_alert = _ui_state.started and self.onroad_brightness != OnroadBrightness.AUTO and alert is not None

    self.update_onroad_brightness(has_alert)
    if has_alert:
      self.reset_onroad_sleep_timer()

  def update_onroad_brightness(self, has_alert: bool) -> None:
    if has_alert:
      return

    if self.onroad_brightness_timer > 0:
      self.onroad_brightness_timer -= 1

  def reset_onroad_sleep_timer(self, timer_status: OnroadTimerStatus = OnroadTimerStatus.NONE) -> None:
    # Toggling from active state to inactive
    if timer_status == OnroadTimerStatus.PAUSE and self.onroad_brightness_timer != ONROAD_BRIGHTNESS_TIMER_PAUSED:
      self.onroad_brightness_timer = ONROAD_BRIGHTNESS_TIMER_PAUSED
    # Toggling from a previously inactive state or resetting an active timer
    elif (self.onroad_brightness_timer_param >= 0 and self.onroad_brightness != OnroadBrightness.AUTO and
          self.onroad_brightness_timer != ONROAD_BRIGHTNESS_TIMER_PAUSED) or timer_status == OnroadTimerStatus.RESUME:
      if self.onroad_brightness == OnroadBrightness.AUTO_DARK:
        self.onroad_brightness_timer = 15 * gui_app.target_fps
      else:
        self.onroad_brightness_timer = self.onroad_brightness_timer_param * gui_app.target_fps

  @property
  def onroad_brightness_timer_expired(self) -> bool:
    return self.onroad_brightness != OnroadBrightness.AUTO and self.onroad_brightness_timer == 0

  @property
  def auto_onroad_brightness(self) -> bool:
    return self.onroad_brightness in (OnroadBrightness.AUTO, OnroadBrightness.AUTO_DARK)

  @staticmethod
  def update_status(ss, ss_sp, onroad_evt) -> str:
    state = ss.state
    mads = ss_sp.mads
    mads_state = mads.state

    if state == OpenpilotState.preEnabled:
      return "override"

    if state == OpenpilotState.overriding:
      if not mads.available:
        return "override"

      if any(e.overrideLongitudinal for e in onroad_evt):
        return "override"

    if mads_state in (MADSState.paused, MADSState.overriding):
      return "override"

    # MADS specific statuses
    if not mads.available:
      return "engaged" if ss.enabled else "disengaged"

    if not mads.enabled and not ss.enabled:
      return "disengaged"

    if mads.enabled and ss.enabled:
      return "engaged"

    if mads.enabled:
      return "lat_only"

    if ss.enabled:
      return "long_only"

    return "disengaged"

  def update_params(self) -> None:
    CP_SP_bytes = self.params.get("CarParamsSPPersistent")
    if CP_SP_bytes is not None:
      self.CP_SP = messaging.log_from_bytes(CP_SP_bytes, custom.CarParamsSP)
      self.has_icbm = self.CP_SP.intelligentCruiseButtonManagementAvailable and self.params.get_bool("IntelligentCruiseButtonManagement")

    self._enforce_constraints()
    self.active_bundle = self.params.get("ModelManager_ActiveBundle")
    self.blindspot = self.params.get_bool("BlindSpot")
    self.chevron_metrics = self.params.get("ChevronInfo")
    self.custom_interactive_timeout = self.params.get("InteractivityTimeout", return_default=True)
    self.developer_ui = self.params.get("DevUIInfo")
    self.hide_v_ego_ui = self.params.get_bool("HideVEgoUI")
    self.onroad_brightness = int(float(self.params.get("OnroadScreenOffBrightness", return_default=True)))
    self.onroad_brightness_timer_param = self.params.get("OnroadScreenOffTimer", return_default=True)
    self.rainbow_path = self.params.get_bool("RainbowMode")
    self.road_name_toggle = self.params.get_bool("RoadNameToggle")
    self.rocket_fuel = self.params.get_bool("RocketFuel")
    self.speed_limit_mode = self.params.get("SpeedLimitMode", return_default=True)
    self.standstill_timer = self.params.get_bool("StandstillTimer")
    self.sunnylink_enabled = self.params.get_bool("SunnylinkEnabled")
    self.torque_bar = self.params.get_bool("TorqueBar")
    self.enforce_torque_control = self.params.get_bool("EnforceTorqueControl")
    self.custom_torque_params = self.params.get_bool("CustomTorqueParams")
    self.torque_override_enabled = self.params.get_bool("TorqueParamsOverrideEnabled")
    self.torque_override_lat_accel_factor = float(self.params.get("TorqueParamsOverrideLatAccelFactor", return_default=True))
    self.torque_override_friction = float(self.params.get("TorqueParamsOverrideFriction", return_default=True))
    self.true_v_ego_ui = self.params.get_bool("TrueVEgoUI")
    self.turn_signals = self.params.get_bool("ShowTurnSignals")
    self.boot_offroad_mode = self.params.get("DeviceBootMode", return_default=True)
    self.always_offroad = self.params.get_bool("OffroadMode")

    if not self._sp_initialized:
      self._sp_initialized = True
      self.reset_onroad_sleep_timer()

  def _enforce_constraints(self) -> None:
    has_long = self.has_longitudinal_control
    CP = self.CP

    if CP is not None:
      if self.params.get_bool("EnforceTorqueControl") and self.params.get_bool("NeuralNetworkLateralControl"):
        self.params.put_bool("EnforceTorqueControl", False, block=True)
        self.params.put_bool("NeuralNetworkLateralControl", False, block=True)

      # Angle steering: no torque-based lateral controls
      if CP.steerControlType == car.CarParams.SteerControlType.angle:
        self.params.remove("EnforceTorqueControl")
        self.params.remove("NeuralNetworkLateralControl")

      # Alpha longitudinal: clear if not available
      if not CP.alphaLongitudinalAvailable:
        self.params.remove("AlphaLongitudinalEnabled")

      # BSM not available: clear BSM-dependent settings
      if not CP.enableBsm:
        self.params.remove("AutoLaneChangeBsmDelay")
    else:
      # NOTHING IS CLEARED HERE ANY MORE. FusionPilot, 2026-08-23.
      #
      # This branch is reached whenever CarParams has not been READ yet -- every UI start before
      # `CarParamsPersistent` loads -- and "we do not know yet" is not the same as "the car does not
      # support it". Destroying a PERSISTENT setting on missing evidence is the exact defect this
      # fork already fixed twice: the fifth ICBM gate deleted `IntelligentCruiseButtonManagement` on
      # essentially every boot for the same reason, and cost two drives before it was found.
      #
      # The chain it opens is worse than the immediate loss, because it is silent and two boots
      # long: delete `AlphaLongitudinalEnabled` here, and on the NEXT boot CarParams loads with op
      # long reading off, `has_long` is False, and the block below then deletes `ExperimentalMode`
      # and `DynamicExperimentalControl` as well. Guarding the second removal on `CP is not None`
      # -- which was done -- only delays that by one boot; it does not break the chain, because the
      # cause is here.
      #
      # NOT the reason his Experimental Mode was off on 2026-08-23: `AlphaLongitudinalEnabled` was
      # last written 2026-08-17 and had not been touched since, so this never fired. It is a latent
      # bug found while chasing that, and it is fixed on its own merits -- an ignition cycle with an
      # unlucky load order silently disabling openpilot longitudinal is not a thing to carry into a
      # 2,000 mile trip.
      #
      # The safety argument for clearing them does not survive contact with the alternative: these
      # are read at CAR INIT from CarParams, which by then is loaded. A stale param cannot enable
      # something the car does not support, because the car is what decides. Report unavailable for
      # display; never destroy the stored setting.
      pass

    # No longitudinal control: no experimental mode or DEC
    #
    # FusionPilot: `CP is not None`, third instance of the same shape and the one with the most to
    # lose. `has_long` reads False both when longitudinal is genuinely off AND when CarParams has
    # simply not been read, so on any boot that reaches here before `CarParamsPersistent` loads,
    # `ExperimentalMode` is DELETED -- and the stop override cannot arm without it (`is_e2e` gates
    # `modelV2.action.shouldStop` on exactly that param).
    #
    # Masked on his car today by load order, same as the three below. Found by generalizing the
    # fifth-gate test to every `remove()` in this function rather than to the one that had bitten:
    # it named these two immediately, and neither had ever been looked at.
    if CP is not None and not has_long:
      self.params.remove("ExperimentalMode")
      self.params.remove("DynamicExperimentalControl")

    # ICBM: clear if not available or if full longitudinal control is actually DRIVING.
    #
    # FusionPilot: THE FOURTH ICBM GATE, and the one that outlived the fix to the other three. He
    # reported "when I turn ICBM on, all its settings are grayed out" -- with the passthrough on,
    # which is the configuration where ICBM is supposed to work.
    #
    # `has_long` alone is the wrong question now. Under the stock-ACC passthrough openpilot carries
    # Ford's command rather than authoring one, so the set speed still governs and ICBM is still the
    # thing that moves it. `op_long_drives` is the same expression cruise.py uses for its own gate;
    # they are one decision and both files have to ask it the same way.
    #
    # And this one does not merely disable -- it REMOVES the param, on every render of any screen.
    # That is why re-enabling ICBM never stuck and why the device read `unset` afterwards: the
    # settings page would light the toggle, this would delete it a frame later, and `has_icbm` going
    # false greyed out every child setting underneath.
    op_long_drives = has_long and not self.params.get_bool("StockAccPassthrough")
    if self.CP_SP is not None:
      if not self.CP_SP.intelligentCruiseButtonManagementAvailable or op_long_drives:
        self.params.remove("IntelligentCruiseButtonManagement")
        self.has_icbm = False
    else:
      # NO CarParamsSP IS "NOT KNOWN YET", NOT "NOT SUPPORTED". THE FIFTH GATE, and the one that
      # actually deleted his setting.
      #
      # `CP_SP` is None until `CarParamsSPPersistent` has been read -- every UI start, before a car
      # has ever been seen, and any frame where that param is briefly unreadable. Deleting the
      # setting there means the UI removes it on essentially every boot, so `card` reads
      # `IntelligentCruiseButtonManagement` as FALSE at car init and never clears `pcmCruiseSpeed`.
      #
      # What that costs, and it is both of his 2026-08-18 complaints from ONE flag:
      #   - `v_cruise` stops being openpilot's and mirrors the dash, so MAX and the ICBM number are
      #     the SAME number -- "it's still having me change the ICBM speed instead". There is no
      #     separate max speed to move.
      #   - `pcm_op_long` becomes True, so Speed Limit Assist runs the PCM state machine, which
      #     requires the set speed to sit at `PCM_LONG_REQUIRED_MAX_SET_SPEED`. That is the "set
      #     your speed to 70 for it to work" -- a protocol for cars that have no button injection,
      #     reached because this car was reporting it had none.
      #
      # Verified on the device: the file was `1` earlier in the session and simply GONE afterwards,
      # with `icbm_enabled=False` while every other condition held.
      #
      # So: report unavailable for display, never destroy the stored setting on missing evidence.
      # Removing a PERSISTENT param is not a way to express "I do not know yet".
      self.has_icbm = False

    # Cruise features requiring longitudinal or ICBM
    #
    # FusionPilot: `CP is not None` -- the SAME delete-on-missing-evidence shape as the ICBM gate
    # directly above, on three more PERSISTENT params, found reviewing that fix.
    #
    # Both terms go False when CarParams has not been read, so on a device that has never seen a car
    # this deletes `CustomAccIncrementsEnabled`, `SmartCruiseControlVision` and
    # `SmartCruiseControlMap` -- two of which are his curve controllers.
    #
    # It is MASKED on his car today, and only incidentally: `CP` is populated before `CP_SP`, so
    # `has_long` is already True by the time this line runs. That same ordering is exactly why the
    # ICBM param DID die -- its branch fires on `CP_SP is None` alone. Relying on load order to keep
    # a setting alive is not a guarantee, it is a coincidence that held.
    if CP is not None and not (has_long or self.has_icbm):
      self.params.remove("CustomAccIncrementsEnabled")
      self.params.remove("SmartCruiseControlVision")
      self.params.remove("SmartCruiseControlMap")


class DeviceSP:
  @staticmethod
  def _set_awake(on: bool, _ui_state):
    if _ui_state.boot_offroad_mode == 1 and not on:
      _ui_state.params.put_bool("OffroadMode", True)

  @staticmethod
  def set_onroad_brightness(_ui_state, awake: bool, cur_brightness: float) -> float:
    if not awake or not _ui_state.started:
      return cur_brightness

    if _ui_state.onroad_brightness_timer != 0:
      if _ui_state.onroad_brightness == OnroadBrightness.AUTO_DARK:
        return max(30.0, cur_brightness)
      return cur_brightness

    # 0: Auto (Default), 1: Auto (Dark), 2: Screen Off
    if _ui_state.onroad_brightness == OnroadBrightness.AUTO:
      return cur_brightness
    if _ui_state.onroad_brightness == OnroadBrightness.AUTO_DARK:
      return cur_brightness
    if _ui_state.onroad_brightness == OnroadBrightness.SCREEN_OFF:
      return 0.0

    # 3-22: 5% - 100%
    return float((_ui_state.onroad_brightness - 2) * 5)

  @staticmethod
  def set_min_onroad_brightness(_ui_state, min_brightness: int) -> int:
    if _ui_state.onroad_brightness == OnroadBrightness.AUTO_DARK:
      min_brightness = 10

    return min_brightness

  @staticmethod
  def wake_from_dimmed_onroad_brightness(_ui_state, evs) -> None:
    if _ui_state.started and (_ui_state.onroad_brightness_timer_expired or _ui_state.onroad_brightness == OnroadBrightness.AUTO_DARK):
      if any(ev.left_down for ev in evs):
        if _ui_state.onroad_brightness_timer_expired:
          gui_app.mouse_events.clear()
        _ui_state.reset_onroad_sleep_timer()
