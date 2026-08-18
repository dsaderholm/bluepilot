"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

from enum import StrEnum

import cereal.messaging as messaging
from opendbc.car import Bus, structs
from opendbc.car.common.conversions import Conversions as CV
from opendbc.can.parser import CANParser
from opendbc.car.ford.values import CAR, FordFlags
from opendbc.sunnypilot.car.ford.values_ext import BUTTONS
from openpilot.common.swaglog import cloudlog


# BluePilot: Traffic_RecognitnData (0x3CD) signal map, capnp field -> CAN signal. Logged whole so
# the camera's actual behavior can be read from a route. wrongWayAlert is handled separately
# because it is the one Bool.
_TSR_SIGNALS = (
  ("vLimit1", "TsrVLim1MsgTxt_D_Rq"),
  ("vLimit2", "TsrVLim2MsgTxt_D_Rq"),
  ("vLimitUnit", "TsrVlUnitMsgTxt_D_Rq"),
  ("vLimit1Status", "TsrVl1StatMsgTxt_D_Rq"),
  ("vLimit2Status", "TsrVl2StatMsgTxt_D_Rq"),
  ("vLimit1Restrict", "TsrVl1RstrcMsgTxt_D_Rq"),
  ("vLimit2Restrict", "TsrVl2RstrcMsgTxt_D_Rq"),
  ("vLimit1Restrict2", "TsrVl1RstrcMsgTxt2_D_Rq"),
  ("vLimit2Restrict2", "TsrVl2RstrcMsgTxt2_D_Rq"),
  ("vLimit1Permanent", "TsrVl1PrmntMsgTxt_D_Rq"),
  ("vLimit2Permanent", "TsrVl2PrmntMsgTxt_D_Rq"),
  ("overtakeMsg", "TsrOvtkMsgTxt_D_Rq"),
  ("overtakeMsg2", "TsrOvtkMsgTxt2_D_Rq"),
  ("overtakeStatus", "TsrOvtkStatMsgTxt_D_Rq"),
  ("tsrMsg", "TsrMsgTxt_D_Rq"),
  ("tsrStatus", "TsrStatMsgTxt_D_Rq"),
  ("overSpeedWarn", "TsrOswWarnMsgTxt_D_Rq"),
)


# BluePilot: HEV power flow mode text lookup (moved from helpers.py)
def get_hev_power_flow_text(mode_value):
  """Convert HEV power flow mode value to human-readable text.

  These values come from the Cluster_HEV_Data2 CAN message (PwrFlowTxt_D_Dsply signal).
  """
  power_flow_modes = {
    0: "",
    1: "Hybrid Drive",
    2: "Charging HV Battery",
    3: "Idle",
    4: "Idle with Charging",
    5: "Electric Drive",
    6: "Engine Drive",
    7: "Remote Start",
    8: "Charge Complete",
    9: "Fast Charge Complete",
    10: "Fast Charging",
    11: "Regen Braking",
    12: "Not Used",
    13: "Not Used",
    14: "Not Used",
    15: "Not Used",
  }
  return power_flow_modes.get(int(mode_value), "Unknown")


# BluePilot: HEV engine on reason text lookup (moved from helpers.py)
def get_hev_engine_on_reason_text(reason_value):
  """Convert HEV engine-on reason value to human-readable text.

  These values come from the Cluster_HEV_Data2 CAN message (EngOnMsg1_D_Dsply signal).
  """
  engine_on_reasons = {
    0: "",
    1: "Acceleration",
    2: "High Speed",
    3: "Heater Setting",
    4: "Neutral Gear",
    5: "Engine Cold",
    6: "Battery Charging",
    7: "Low Gear",
    8: "Normal Operation",
    9: "Oil Maintenance",
    10: "Fuel Maintenance",
    11: "Hill Descent Control",
    12: "Battery Temperature",
    13: "Drive Mode",
  }
  return engine_on_reasons.get(int(reason_value), "Unknown")


class CarStateExt:
  """
  Extension class for Ford CarState to parse cruise control buttons.

  This class tracks button state transitions and emits ButtonEvent messages
  when buttons are pressed or released. This is required for ICBM and Speed
  Limit Assist to work correctly, as they need to track manual button presses
  to set vCruiseCluster.
  """

  def __init__(self, CP, CP_SP):
    self.CP = CP
    self.CP_SP = CP_SP

    self.button_events = []
    self.button_states = {button.event_type: False for button in BUTTONS}
    # Track which event type was actually emitted for combo buttons (to handle releases correctly)
    self.last_emitted_event = {}  # signal_name -> event_type
    # Track previous cruise state to detect transitions
    self.cruise_enabled_prev = False
    # Track if mainCruise was pressed recently (to handle delayed cruise enable)
    self.main_cruise_pressed_recently = False

  def update(self, ret: structs.CarState, ret_sp: structs.CarStateSP, can_parsers: dict[StrEnum, CANParser]):
    """
    Update button state tracking and emit ButtonEvent messages.

    For combo buttons, we emit the appropriate event based on cruise state:
    - CcAslButtnSetIncPress: emits setCruise if cruise disabled, accelCruise if enabled
    - CcAslButtnCnclResPress: emits cancel if cruise enabled, resumeCruise if disabled

    Args:
      ret: CarState structure to update
      ret_sp: CarStateSP structure (unused but required for interface compatibility)
      can_parsers: Dictionary of CAN parsers by bus
    """
    cp = can_parsers[Bus.pt]

    button_events = []
    cruise_enabled = ret.cruiseState.enabled

    # Detect cruise transition from disabled to enabled
    cruise_just_enabled = cruise_enabled and not self.cruise_enabled_prev
    main_cruise_just_pressed = False

    for button in BUTTONS:
      # Check if button signal is in the pressed state (value == 1)
      try:
        signal_value = cp.vl[button.can_addr][button.can_msg]
        state = (signal_value in button.values)
      except (KeyError, AttributeError):
        # Signal not available in this frame, skip
        continue

      # Handle combo buttons: emit only the appropriate event based on cruise state
      # Track which signals we've already processed to avoid duplicate events
      processed_signals = set()

      # CcAslButtnSetIncPress is the "RES +" button: resumeCruise (10) when cruise is disabled,
      # accelCruise (3) when enabled. It emitted setCruise (9) when disabled, which reported every
      # resume as a set and discarded the driver's hold -- see the BUTTONS comment in values_ext.
      if button.can_msg == "CcAslButtnSetIncPress" and button.can_msg not in processed_signals:
        processed_signals.add(button.can_msg)
        signal_state = state
        prev_accel_state = self.button_states.get(3, False)  # accelCruise
        prev_set_state = self.button_states.get(10, False)  # resumeCruise

        if signal_state and (prev_accel_state != signal_state or prev_set_state != signal_state):
          # Choose event type based on cruise state
          if cruise_enabled:
            event_type = 3  # accelCruise
          else:
            event_type = 10  # resumeCruise

          # Emit the appropriate event
          if self.button_states.get(event_type, False) != signal_state:
            event = structs.CarState.ButtonEvent.new_message()
            event.type = event_type
            event.pressed = signal_state
            button_events.append(event)
            # Remember which event type we emitted for this signal
            self.last_emitted_event[button.can_msg] = event_type

          # Update state for both event types
          self.button_states[3] = signal_state  # accelCruise
          self.button_states[10] = signal_state  # resumeCruise
        elif not signal_state and (prev_accel_state != signal_state or prev_set_state != signal_state):
          # Button released - emit release ONLY for the event type that was actually emitted on press
          last_emitted = self.last_emitted_event.get(button.can_msg)
          if last_emitted is not None:
            # Always emit release if we previously emitted a press event for this button
            # This ensures ICBM button timers are properly reset
            event = structs.CarState.ButtonEvent.new_message()
            event.type = last_emitted
            event.pressed = False
            button_events.append(event)
          # Clear the tracking
          self.last_emitted_event.pop(button.can_msg, None)
          # Update state for both event types -- THE SAME TWO THE PRESS PATH SET.
          #
          # This cleared 3 and 9, while the press path above sets 3 and 10. So `resumeCruise` (10)
          # was set on the first RES+ press with cruise off and NEVER CLEARED. On the second press
          # the guard `self.button_states.get(event_type) != signal_state` compared True against
          # True and emitted nothing -- so every resume after the first one silently did not exist.
          #
          # 9 is `setCruise`, which this button has not emitted since the 2026-08-04 correction in
          # the BUTTONS comment; clearing it here is a leftover from the version that did.
          self.button_states[3] = False   # accelCruise
          self.button_states[10] = False  # resumeCruise
        continue

      # CcAslButtnSetDecPress: setCruise (9) when disabled, decelCruise (4) when enabled
      if button.can_msg == "CcAslButtnSetDecPress" and button.can_msg not in processed_signals:
        processed_signals.add(button.can_msg)
        signal_state = state
        prev_decel_state = self.button_states.get(4, False)  # decelCruise
        prev_set_state = self.button_states.get(9, False)  # setCruise

        if signal_state and (prev_decel_state != signal_state or prev_set_state != signal_state):
          # Choose event type based on cruise state
          if cruise_enabled:
            event_type = 4  # decelCruise
          else:
            event_type = 9  # setCruise

          # Emit the appropriate event
          if self.button_states.get(event_type, False) != signal_state:
            event = structs.CarState.ButtonEvent.new_message()
            event.type = event_type
            event.pressed = signal_state
            button_events.append(event)
            # Remember which event type we emitted for this signal
            self.last_emitted_event[button.can_msg] = event_type

          # Update state for both event types
          self.button_states[4] = signal_state  # decelCruise
          self.button_states[9] = signal_state  # setCruise
        elif not signal_state and (prev_decel_state != signal_state or prev_set_state != signal_state):
          # Button released - emit release ONLY for the event type that was actually emitted on press
          last_emitted = self.last_emitted_event.get(button.can_msg)
          if last_emitted is not None:
            # Always emit release if we previously emitted a press event for this button
            # This ensures ICBM button timers are properly reset
            event = structs.CarState.ButtonEvent.new_message()
            event.type = last_emitted
            event.pressed = False
            button_events.append(event)
          # Clear the tracking
          self.last_emitted_event.pop(button.can_msg, None)
          # Update state for both event types
          self.button_states[4] = False  # decelCruise
          self.button_states[9] = False  # setCruise
        continue

      # CcAslButtnCnclResPress: cancel (5) when enabled, resumeCruise (10) when disabled
      if button.can_msg == "CcAslButtnCnclResPress" and button.can_msg not in processed_signals:
        processed_signals.add(button.can_msg)
        signal_state = state
        prev_cancel_state = self.button_states.get(5, False)  # cancel
        prev_resume_state = self.button_states.get(10, False)  # resumeCruise

        if signal_state and (prev_cancel_state != signal_state or prev_resume_state != signal_state):
          # Choose event type based on cruise state at the moment of press
          if cruise_enabled:
            event_type = 5  # cancel
          else:
            event_type = 10  # resumeCruise

          # Emit the appropriate event
          if self.button_states.get(event_type, False) != signal_state:
            event = structs.CarState.ButtonEvent.new_message()
            event.type = event_type
            event.pressed = signal_state
            button_events.append(event)
            # Remember which event type we emitted for this signal
            self.last_emitted_event[button.can_msg] = event_type

          # Update state for both event types
          self.button_states[5] = signal_state  # cancel
          self.button_states[10] = signal_state  # resumeCruise
        elif not signal_state and (prev_cancel_state != signal_state or prev_resume_state != signal_state):
          # Button released - emit release ONLY for the event type that was actually emitted on press
          last_emitted = self.last_emitted_event.get(button.can_msg)
          if last_emitted is not None:
            # Always emit release if we previously emitted a press event for this button
            # This ensures ICBM button timers are properly reset
            event = structs.CarState.ButtonEvent.new_message()
            event.type = last_emitted
            event.pressed = False
            button_events.append(event)
          # Clear the tracking
          self.last_emitted_event.pop(button.can_msg, None)
          # Update state for both event types
          self.button_states[5] = False  # cancel
          self.button_states[10] = False  # resumeCruise
        continue

      # Regular buttons (non-combo): emit event on state transition
      if self.button_states.get(button.event_type, False) != state:
        event = structs.CarState.ButtonEvent.new_message()
        event.type = button.event_type
        event.pressed = state
        button_events.append(event)
        # Track if mainCruise button was just pressed (transition from not pressed to pressed)
        if button.event_type == structs.CarState.ButtonEvent.Type.mainCruise and state:
          main_cruise_just_pressed = True

      # Update stored state for this ButtonEvent type
      self.button_states[button.event_type] = state

    # Track mainCruise press for delayed cruise enable detection
    if main_cruise_just_pressed:
      self.main_cruise_pressed_recently = True

    # When mainCruise enables cruise, also emit setCruise to set speed to current speed
    # This restores the old behavior where turning on cruise also sets it to current speed
    # Handle both immediate enable (same frame) and delayed enable (next frame)
    # FusionPilot: AND NOT WHEN ANOTHER BUTTON IS WHAT ENGAGED IT.
    #
    # `main_cruise_pressed_recently` is sticky and had no bound at all -- set on any MAIN press and
    # cleared only once cruise actually came on. So MAIN pressed early in a drive, then engaging
    # later with SET- or RES+, synthesized a `setCruise` on top of the real event. And `setCruise`
    # is the one button meaning that CLEARS THE DRIVER'S HOLD and hands the speed to SLA.
    #
    # That is his report: "I adjust my speed with +/- and it changes the ICBM little speed number,
    # not the max". The press created a hold and the synthesized setCruise discarded it in the same
    # frame, so the dash moved and MAX did not. Measured on route 389: one raw SET- edge with cruise
    # on produced BOTH `decelCruise` and `setCruise`.
    #
    # The synthesis itself is right and stays -- MAIN alone should engage at the current speed. It
    # just must not fire when the driver engaged with a button that carries its own meaning.
    engaged_by_another_button = any(
      e.pressed and e.type in (structs.CarState.ButtonEvent.Type.setCruise,
                               structs.CarState.ButtonEvent.Type.decelCruise,
                               structs.CarState.ButtonEvent.Type.accelCruise,
                               structs.CarState.ButtonEvent.Type.resumeCruise)
      for e in button_events)
    if cruise_just_enabled and not engaged_by_another_button \
       and (main_cruise_just_pressed or self.main_cruise_pressed_recently):
      set_cruise_event = structs.CarState.ButtonEvent.new_message()
      set_cruise_event.type = structs.CarState.ButtonEvent.Type.setCruise
      set_cruise_event.pressed = True
      button_events.append(set_cruise_event)

      # Also emit release to complete the button press cycle
      set_cruise_release = structs.CarState.ButtonEvent.new_message()
      set_cruise_release.type = structs.CarState.ButtonEvent.Type.setCruise
      set_cruise_release.pressed = False
      button_events.append(set_cruise_release)

      # Clear the flag after emitting setCruise
      self.main_cruise_pressed_recently = False

    # Clear the flag if cruise is already enabled (button press was for something else)
    if cruise_enabled and not cruise_just_enabled:
      self.main_cruise_pressed_recently = False

    # Update previous cruise state
    self.cruise_enabled_prev = cruise_enabled

    self.button_events = button_events

  def update_car_state_bp(self, cp, cp_cam):
    """Build the carStateBP message for HEV/PHEV telemetry and brake light status.

    Called each frame from CarState.update(). Reads Ford-specific CAN signals for
    hybrid drive data, battery data, and brake light status.

    Args:
      cp: Powertrain bus CAN parser
      cp_cam: Camera bus CAN parser

    Returns:
      cereal message for carStateBP topic
    """
    dat = messaging.new_message("carStateBP")
    dat.valid = True

    hybrid_drive = dat.carStateBP.hybridDrive
    hybrid_battery = dat.carStateBP.hybridBattery
    brake_light_status = dat.carStateBP.brakeLightStatus
    traffic_sign_data = dat.carStateBP.trafficSignData

    # Initialize with defaults
    hybrid_drive.dataAvailable = False
    hybrid_drive.throttleDemandPercent = 0.0
    hybrid_drive.throttleThresholdPercent = 0.0
    hybrid_drive.powerFlowMode = ""
    hybrid_drive.powerFlowModeValue = 0
    hybrid_drive.engineOnReason = ""
    hybrid_drive.engineOnReasonValue = 0

    hybrid_battery.dataAvailable = False
    hybrid_battery.voltHighLimit = 0.0
    hybrid_battery.voltLowLimit = 0.0
    hybrid_battery.voltActual = 0.0
    hybrid_battery.ampsActual = 0.0
    hybrid_battery.socMinPerc = 0.0
    hybrid_battery.socMaxPerc = 0.0
    hybrid_battery.socActual = 0.0

    traffic_sign_data.dataAvailable = False
    dat.carStateBP.accGap = 0

    brake_light_status.dataAvailable = False
    brake_light_status.brakeLightsOn = False
    brake_light_status.accDataAvailable = False
    brake_light_status.accDecelRequest = False
    brake_light_status.accPrechargeRequest = False
    brake_light_status.accAccelRequest = 0.0
    brake_light_status.accPropulsionRequest = 0.0

    # Brake light status — try BCM message first, then fallback to BrakeSysFeatures_2
    brake_lights_detected = False

    # Primary: BCM_Lamp_Stat_FD1 (Body Control Module)
    try:
      bcm_data = cp.vl["BCM_Lamp_Stat_FD1"]
      if bcm_data is not None:
        brake_light_status.dataAvailable = True
        if "StopLghtOn_B_Stat" in bcm_data:
          brake_light_status.brakeLightsOn = bool(bcm_data["StopLghtOn_B_Stat"])
          brake_lights_detected = True
        elif "RvrseLghtOn_B_Stat" in bcm_data:
          brake_light_status.brakeLightsOn = bcm_data["RvrseLghtOn_B_Stat"] == 1
          brake_lights_detected = True
        else:
          brake_light_status.dataAvailable = False
    except (KeyError, AttributeError):
      pass

    # Fallback: BrakeSysFeatures_2 (brake light request signal)
    if not brake_lights_detected:
      try:
        brake_data = cp.vl["BrakeSysFeatures_2"]
        if brake_data is not None:
          brake_light_status.dataAvailable = True
          brake_light_status.brakeLightsOn = brake_data["BrkLamp_B_Rq"] == 1
          brake_lights_detected = True
      except (KeyError, AttributeError):
        pass

    # BluePilot: expose what ACC is asking the brakes to do, separately from the lamp state.
    #
    # Read unconditionally, not only under openpilotLongitudinalControl. When op long is off this
    # is the STOCK ACC's own ACCDATA as sent by the camera, which is exactly the thing worth seeing
    # on a car running Ford ACC: it catches applications too light to trigger the stop lamps.
    # The lamp signal above stays untouched -- it is what traffic behind actually sees, and these
    # two are different events, not two measurements of one.
    try:
      acc_data = cp_cam.vl["ACCDATA"]
      brake_light_status.accDataAvailable = True
      brake_light_status.accDecelRequest = acc_data["AccBrkDecel_B_Rq"] == 1
      brake_light_status.accPrechargeRequest = acc_data["AccBrkPrchg_B_Rq"] == 1
      brake_light_status.accAccelRequest = float(acc_data["AccBrkTot_A_Rq"])
      brake_light_status.accPropulsionRequest = float(acc_data["AccPrpl_A_Rq"])
    except (KeyError, AttributeError):
      pass

    # ACC brake light overlay: only meaningful when openpilot is the one sending ACCDATA, since
    # then the lamp has not been commanded by anything the BCM signal above would have caught.
    if brake_lights_detected and self.CP.openpilotLongitudinalControl:
      acc_brake_active = brake_light_status.accPrechargeRequest or brake_light_status.accDecelRequest
      brake_light_status.brakeLightsOn = brake_light_status.brakeLightsOn or acc_brake_active

    # BluePilot: the ACC follow gap, straight off the camera. Read unconditionally and separately
    # from the block above, because this is the signal an earlier note wrongly recorded as coming
    # from the GWM and therefore unreadable -- it is in ACCDATA_3, which IPMA_ADAS sends and this
    # parser has always registered.
    try:
      dat.carStateBP.accGap = int(cp_cam.vl["ACCDATA_3"]["AccTGap_D_Dsply"])
    except (KeyError, TypeError, ValueError):
      pass

    # HEV cluster data (Cluster_HEV_Data2)
    try:
      if self.CP.flags & FordFlags.HEV_CLUSTER_DATA:
        hev_data = cp.vl["Cluster_HEV_Data2"]
        if hev_data is not None:
          hybrid_drive.dataAvailable = True
          hybrid_drive.throttleDemandPercent = hev_data["EffWhlLvl2_Pc_Dsply"]
          hybrid_drive.throttleThresholdPercent = hev_data["EffWhlThres_Pc_Dsply"]
          power_flow_value = int(hev_data["PwrFlowTxt_D_Dsply"])
          engine_reason_value = int(hev_data["EngOnMsg1_D_Dsply"])
          hybrid_drive.powerFlowMode = get_hev_power_flow_text(power_flow_value)
          hybrid_drive.powerFlowModeValue = power_flow_value
          hybrid_drive.engineOnReason = get_hev_engine_on_reason_text(engine_reason_value)
          hybrid_drive.engineOnReasonValue = engine_reason_value
    except (KeyError, AttributeError):
      pass

    # HEV battery data (Battery_Traction_1/3/4_FD1)
    try:
      if self.CP.flags & FordFlags.HEV_BATTERY_DATA:
        batt_data1 = cp.vl["Battery_Traction_1_FD1"]
        batt_data3 = cp.vl["Battery_Traction_3_FD1"]
        batt_data4 = cp.vl["Battery_Traction_4_FD1"]

        if all(x is not None for x in [batt_data1, batt_data3, batt_data4]):
          hybrid_battery.dataAvailable = True
          hybrid_battery.voltHighLimit = batt_data1["BattTrac_U_LimHi"]
          hybrid_battery.voltLowLimit = batt_data1["BattTrac_U_LimLo"]
          hybrid_battery.voltActual = batt_data1["BattTrac_U_Actl"]
          # BattTrac_I_Actl reads a constant -750 A on the Mach-E (bytes 0-1 of 0x07A are always 0)
          if self.CP.carFingerprint == CAR.FORD_MUSTANG_MACH_E_MK1:
            # motor current is positive when motoring; UI wants positive = charging, so negate
            hybrid_battery.ampsActual = -cp.vl["MtrTracData_1_FD1"]["MtrTrac2_I_Actl"]
          else:
            hybrid_battery.ampsActual = batt_data1["BattTrac_I_Actl"]
          hybrid_battery.socMinPerc = batt_data3["BattTracSoc_Pc_MnPrtct"]
          hybrid_battery.socMaxPerc = batt_data3["BattTracSoc_Pc_MxPrtct"]
          hybrid_battery.socActual = batt_data4["BattTracSoc2_Pc_Actl"]
    except (KeyError, AttributeError):
      pass

    # BluePilot: log every Traffic_RecognitnData signal, not just the two the resolver consumes.
    # What a given camera actually populates varies by market and build, and it is far cheaper to
    # read it off a route than to reason about it. See the capnp comment: there is no curve or
    # advisory field in this message at all, so nothing here can drive curve speed.
    if self.CP.flags & FordFlags.TSR:
      try:
        tsr = cp_cam.vl["Traffic_RecognitnData"]
        traffic_sign_data.dataAvailable = True
        for field, signal in _TSR_SIGNALS:
          setattr(traffic_sign_data, field, int(tsr[signal]))
        traffic_sign_data.wrongWayAlert = bool(tsr["WwaWarn_B_Rq"])
      except (KeyError, AttributeError):
        pass

    return dat

  def update_traffic_signals(self, cp_cam):
    """Parse traffic sign recognition data for speed limit.

    The IPMA camera broadcasts the posted limit in Traffic_RecognitnData on both
    Q4 (CAN FD) and Q3 (CAN) Fords, using the same signals and DBC. The unit
    (mph/kph) comes from TsrVlUnitMsgTxt_D_Rq within the message itself, so no
    separate IPMA_Data2 lookup is needed. On trims without TSR the message is
    absent (registered non-critical) and its signals default to 0/255, which
    this function maps to "no limit".

    Gated on FordFlags.TSR rather than read unconditionally: the flag is set from 0x3CD actually
    being present in the camera-bus fingerprint, so cars whose camera never sends it do not
    register the message at all.

    Args:
      cp_cam: Camera bus CAN parser

    Returns:
      Speed limit in m/s, or 0 if not available.
    """
    # The flag gate matters for more than skipping work: without TSR on a CAN FD car the message
    # is never registered with the parser, so reading it would raise rather than return a default.
    if not self.CP.flags & FordFlags.TSR:
      return 0

    v_limit = cp_cam.vl["Traffic_RecognitnData"]["TsrVLim1MsgTxt_D_Rq"]
    v_limit_unit = cp_cam.vl["Traffic_RecognitnData"]["TsrVlUnitMsgTxt_D_Rq"]

    speed_factor = CV.MPH_TO_MS if v_limit_unit == 2 else CV.KPH_TO_MS if v_limit_unit == 1 else 0
    return v_limit * speed_factor if v_limit not in (0, 255) else 0
