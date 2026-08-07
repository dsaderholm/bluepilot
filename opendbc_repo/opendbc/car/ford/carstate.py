from opendbc.can import CANDefine, CANParser
from opendbc.car import Bus, create_button_events, structs
from opendbc.car.common.conversions import Conversions as CV
from openpilot.common.params import Params
from opendbc.car.ford.fordcan import CanBus
from opendbc.car.ford.values import DBC, CarControllerParams, FordFlags

from opendbc.car.interfaces import CarStateBase
from opendbc.sunnypilot.car.ford.mads import MadsCarState
from opendbc.sunnypilot.car.ford.carstate_ext import CarStateExt

ButtonType = structs.CarState.ButtonEvent.Type

# BluePilot: below this the car is stopped whatever VehStop_D_Stat says. Well under any
# real motion and above the quantization of Veh_V_ActlBrk, which is reported in km/h.
STANDSTILL_SPEED = 0.1  # m/s
GearShifter = structs.CarState.GearShifter
TransmissionType = structs.CarParams.TransmissionType


class CarState(CarStateBase, MadsCarState, CarStateExt):
  def __init__(self, CP, CP_SP):
    CarStateBase.__init__(self, CP, CP_SP)
    MadsCarState.__init__(self, CP, CP_SP)
    CarStateExt.__init__(self, CP, CP_SP)
    can_define = CANDefine(DBC[CP.carFingerprint][Bus.pt])
    self.params = Params()

    if CP.transmissionType == TransmissionType.automatic:
      if CP.flags & FordFlags.CANFD:
        self.shifter_values = can_define.dv["Gear_Shift_by_Wire_FD1"]["TrnRng_D_RqGsm"]
      elif CP.flags & FordFlags.ALT_STEER_ANGLE:
        self.shifter_values = can_define.dv["TransGearData"]["GearLvrPos_D_Actl"]
      else:
        self.shifter_values = can_define.dv["PowertrainData_10"]["TrnRng_D_Rq"]

    self.cluster_min_speed = CV.KPH_TO_MS * 1.5
    self.cluster_speed_hyst_gap = CV.KPH_TO_MS / 2.
    self.distance_button = 0
    self.lc_button = 0
    # BluePilot: fix uninitialized attribute (used by ALT_STEER_ANGLE steering angle calc)
    self.steering_angle_offset_deg = 0.0

    # BluePilot: Save HEV data available flags to params for UI
    self.params.put_bool("FordPrefHevDataAvailable", True if CP.flags & FordFlags.HEV_CLUSTER_DATA else False)
    self.params.put_bool("FordPrefHevBattDataAvailable", True if CP.flags & FordFlags.HEV_BATTERY_DATA else False)

  def update(self, can_parsers) -> tuple[structs.CarState, structs.CarStateSP]:
    cp = can_parsers[Bus.pt]
    cp_cam = can_parsers[Bus.cam]

    ret = structs.CarState()
    ret_sp = structs.CarStateSP()

    if self.CP.flags & FordFlags.ALT_STEER_ANGLE:
      self.vehicle_sensors_valid = (
        int((cp.vl["ParkAid_Data"]["ExtSteeringAngleReq2"] + 1000) * 10) not in (32766, 32767)
        and cp.vl["ParkAid_Data"]["EPASExtAngleStatReq"] == 0
        and cp.vl["ParkAid_Data"]["ApaSys_D_Stat"] in (0, 1)
      )
    else:
   	  # Occasionally on startup, the ABS module recalibrates the steering pinion offset, so we need to block engagement
      # The vehicle usually recovers out of this state within a minute of normal driving
      ret.vehicleSensorsInvalid = cp.vl["SteeringPinion_Data"]["StePinCompAnEst_D_Qf"] != 3

    # car speed
    ret.vEgoRaw = cp.vl["BrakeSysFeatures"]["Veh_V_ActlBrk"] * CV.KPH_TO_MS
    ret.vEgo, ret.aEgo = self.update_speed_kf(ret.vEgoRaw)
    if self.CP.flags & FordFlags.CANFD:
      ret.vEgoCluster = ((cp.vl["Cluster_Info_3_FD1"]["DISPLAY_SPEED_SCALING"]/100) * cp.vl["EngVehicleSpThrottle2"]["Veh_V_ActlEng"] +
                         cp.vl["Cluster_Info_3_FD1"]["DISPLAY_SPEED_OFFSET"]) * CV.KPH_TO_MS

    ret.yawRate = cp.vl["Yaw_Data_FD1"]["VehYaw_W_Actl"]
    # BluePilot: OR'd with actual speed. Confirmed on the road 2026-08-05 -- standstill was reading
    # FALSE on this car while it was stopped, so anything gated on it silently never ran.
    #
    # Ford derives standstill from a discrete status signal rather than from wheel speed, which
    # almost every other brand in opendbc does not: Chrysler, Honda, Hyundai, GM, Mazda and Nissan
    # all compare a speed against a threshold. PSA is the one other exception (VEHICLE_STANDSTILL
    # off HS2_DYN_UCF_MDD_32D), so "the only brand" -- which this comment said until it was
    # checked -- was wrong. It is unusual, not unique, and the point stands either way: it is a bus
    # message some other module has to volunteer rather than something derived from data we have.
    # VehStop_D_Stat comes from ABS_ESC, its DBC start value is 2 (NoDataExists), and the DBC marks
    # it transmitted on only two of the four platforms it documents -- so it is an optional signal,
    # not a guaranteed one. This retrofit kept the Fusion's own ABS; swapping the camera downstream
    # does not make the brake module start publishing a flag it never published.
    #
    # What it broke, in the order it was noticed:
    #   * Resume at a stop. selfdrived pairs two events on the same two signals -- preEnableStandstill
    #     (`brakePressed and standstill`, PRE_ENABLE, "Release Brake to Engage") and pedalPressed
    #     (`brakePressed and (not prev_brakePressed or not standstill)`, NO_ENTRY). With the brake
    #     HELD, standstill decides which one fires, and a stuck-false value picked the blocking one
    #     every time. Hence "openpilot unavailable pedal pressed" where stock ACC resumes happily.
    #   * The standstill timer never appeared, which is what confirmed the diagnosis.
    #   * The driver monitor's standstill_exemption never applied, so distraction escalation ran at
    #     full strictness at every red light.
    #
    # controlsd already refuses to trust the signal alone for lateral -- `abs(vEgo) <= 0.3 or
    # standstill`. This applies the same reasoning at the source: a car reading zero road speed is
    # stopped, whatever a status byte says. vEgoRaw rather than vEgo because it is the direct
    # wheel-speed reading and does not lag through the Kalman filter.
    #
    # Every other consumer is improved or unaffected by it being true when the car genuinely is not
    # moving: the speedTooLow guard, the brake-hold path, the steer-warning suppression and the
    # longitudinal planner's accel-constraint reset all want it true at a stop.
    #
    # WHAT THIS EXPOSED, 2026-08-06. Resume at a stop works now, and "controls mismatch" started
    # appearing at complete stops -- which takes everything down, since controlsMismatch is
    # ET.IMMEDIATE_DISABLE. It is not caused here; it was HIDDEN here. Previously pedalPressed
    # disengaged openpilot at every stop before anything else had the chance to go wrong.
    #
    # CAUSE FOUND 2026-08-06, from a route rather than from reasoning. Two earlier guesses -- auto
    # start-stop, and a lapsing rx-checked message -- were both wrong. "It only happens when I press
    # resume, never just stopping" is what ruled them out: a CAN message does not care about buttons.
    #
    # The log shows selfdriveState.enabled going FALSE -> TRUE while pandaState.controlsAllowed stays
    # FALSE, with carState.cruiseState.enabled TRUE the whole time and safetyRxChecksInvalid False.
    # Two seconds of that is mismatch_counter >= 200, and controlsMismatch is ET.IMMEDIATE_DISABLE.
    #
    # openpilot and panda read the SAME signal -- EngBrakeData / CcStat_D_Actl, engaged on (4, 5).
    # They disagree on the RULE:
    #
    #   openpilot   cruiseState.enabled is a LEVEL. True whenever the car says engaged.
    #   panda       pcm_cruise_check (safety.h) allows only on a RISING EDGE:
    #                 if (!cruise_engaged)                      controls_allowed = false;
    #                 if (cruise_engaged && !cruise_engaged_prev) controls_allowed = true;
    #
    # So once controls_allowed has been cleared, ONLY a fresh off->on transition of Ford's cruise
    # state can restore it. At a standstill Ford holds CcStat_D_Actl in (4,5) continuously -- the
    # driver pressing SET or RES moves the set speed without ever leaving engaged -- so openpilot
    # re-enables on the level and panda never sees an edge. That is exactly the reported shape.
    #
    # ford.h also calls speed_mismatch_check, which clears controls_allowed with no edge to restore
    # it, so a single speed disagreement earlier in the drive can latch this off for good.
    #
    # NOT FIXED HERE, and not to be fixed by guessing. The two candidate fixes live in other
    # people's safety-critical code -- openpilot declining to enable without controls_allowed, or
    # panda's Ford mode allowing on level rather than edge -- and the second is a real safety
    # loosening. Worth reporting upstream with this log.
    #
    # Workaround that costs nothing: CNCL then RES+ rather than SET at a stop. Cancelling drops
    # CcStat_D_Actl out of (4,5), and resuming gives panda the rising edge it is waiting for.
    #
    # "Costs nothing" is literal rather than a figure of speech, and it is worth knowing before this
    # reads as a compromise. Against the button contract in CLAUDE.md, RES+ with cruise off is
    # resumeCruise, which KEEPS his hold; SET- with cruise off is setCruise, which CLEARS it and
    # hands the speed back to SLA. So the workaround is strictly better than the gesture it replaces
    # -- he keeps the number he chose, which SET would have discarded anyway.
    ret.standstill = cp.vl["DesiredTorqBrk"]["VehStop_D_Stat"] == 1 or ret.vEgoRaw < STANDSTILL_SPEED

    # gas pedal
    ret.gasPressed = cp.vl["EngVehicleSpThrottle"]["ApedPos_Pc_ActlArb"] / 100. > 1e-6

    # brake pedal
    ret.brakePressed = cp.vl["EngBrakeData"]["BpedDrvAppl_D_Actl"] == 2
    ret.parkingBrake = cp.vl["DesiredTorqBrk"]["PrkBrkStatus"] in (1, 2)

    # steering wheel
    if self.CP.flags & FordFlags.ALT_STEER_ANGLE:
      steering_angle_init = cp.vl["SteeringPinion_Data_Alt"]["StePinRelInit_An_Sns"]
      if self.vehicle_sensors_valid:
        steering_angle_est = cp.vl["ParkAid_Data"]["ExtSteeringAngleReq2"]
        self.steering_angle_offset_deg = steering_angle_est - steering_angle_init
      ret.steeringAngleDeg = steering_angle_init + self.steering_angle_offset_deg
    else:
      ret.steeringAngleDeg = cp.vl["SteeringPinion_Data"]["StePinComp_An_Est"]
    ret.steeringTorque = cp.vl["EPAS_INFO"]["SteeringColumnTorque"]
    ret.steeringPressed = self.update_steering_pressed(abs(ret.steeringTorque) > CarControllerParams.STEER_DRIVER_ALLOWANCE, 5)
    ret.steerFaultTemporary = cp.vl["EPAS_INFO"]["EPAS_Failure"] == 1
    ret.steerFaultPermanent = cp.vl["EPAS_INFO"]["EPAS_Failure"] in (2, 3)
    ret.espDisabled = cp.vl["Cluster_Info1_FD1"]["DrvSlipCtlMde_D_Rq"] != 0  # 0 is default mode

    if self.CP.flags & FordFlags.CANFD:
      # this signal is always 0 on non-CAN FD cars
      ret.steerFaultTemporary |= cp.vl["Lane_Assist_Data3_FD1"]["LatCtlSte_D_Stat"] not in (1, 2, 3)

    # cruise state
    is_metric = cp.vl["INSTRUMENT_PANEL"]["METRIC_UNITS"] == 1 if not self.CP.flags & FordFlags.CANFD else cp_cam.vl["IPMA_Data2"]["IsaVLimUnit_D_Rq"] == 1
    ret.cruiseState.speed = cp.vl["EngBrakeData"]["Veh_V_DsplyCcSet"] * (CV.KPH_TO_MS if is_metric else CV.MPH_TO_MS)
    ret.cruiseState.speedCluster = ret.cruiseState.speed  # ICBM needs speedCluster to read current cruise setpoint
    ret.cruiseState.enabled = cp.vl["EngBrakeData"]["CcStat_D_Actl"] in (4, 5)
    ret.cruiseState.available = cp.vl["EngBrakeData"]["CcStat_D_Actl"] in (3, 4, 5)
    ret.cruiseState.nonAdaptive = cp.vl["Cluster_Info1_FD1"]["AccEnbl_B_RqDrv"] == 0
    ret.cruiseState.standstill = cp.vl["EngBrakeData"]["AccStopMde_D_Rq"] == 3
    ret.accFaulted = cp.vl["EngBrakeData"]["CcStat_D_Actl"] in (1, 2)

    # BluePilot: traffic sign recognition (delegated to carstate_ext)
    if self.CP.flags & FordFlags.TSR:
      ret_sp.speedLimit = CarStateExt.update_traffic_signals(self, cp_cam)

    if not self.CP.openpilotLongitudinalControl:
      ret.accFaulted = ret.accFaulted or cp_cam.vl["ACCDATA"]["CmbbDeny_B_Actl"] == 1

    # gear
    if self.CP.transmissionType == TransmissionType.automatic:
      if self.CP.flags & FordFlags.CANFD:
        gear = self.shifter_values.get(cp.vl["Gear_Shift_by_Wire_FD1"]["TrnRng_D_RqGsm"])
      elif self.CP.flags & FordFlags.ALT_STEER_ANGLE:
          gear = self.shifter_values.get(cp.vl["TransGearData"]["GearLvrPos_D_Actl"])
      else:
        gear = self.shifter_values.get(cp.vl["PowertrainData_10"]["TrnRng_D_Rq"])

      ret.gearShifter = self.parse_gear_shifter(gear)
    elif self.CP.transmissionType == TransmissionType.manual:
      if bool(cp.vl["BCM_Lamp_Stat_FD1"]["RvrseLghtOn_B_Stat"]):
        ret.gearShifter = GearShifter.reverse
      else:
        ret.gearShifter = GearShifter.drive

    # safety
    ret.stockFcw = bool(cp_cam.vl["ACCDATA_3"]["FcwVisblWarn_B_Rq"])
    ret.stockAeb = bool(cp_cam.vl["ACCDATA_2"]["CmbbBrkDecel_B_Rq"])

    # button presses
    ret.leftBlinker = cp.vl["Steering_Data_FD1"]["TurnLghtSwtch_D_Stat"] == 1
    ret.rightBlinker = cp.vl["Steering_Data_FD1"]["TurnLghtSwtch_D_Stat"] == 2
    # TODO: block this going to the camera otherwise it will enable stock TJA
    ret.genericToggle = bool(cp.vl["Steering_Data_FD1"]["TjaButtnOnOffPress"])
    prev_distance_button = self.distance_button
    prev_lc_button = self.lc_button
    self.distance_button = cp.vl["Steering_Data_FD1"]["AccButtnGapTogglePress"]
    self.lc_button = bool(cp.vl["Steering_Data_FD1"]["TjaButtnOnOffPress"])

    # lock info
    ret.doorOpen = any([cp.vl["BodyInfo_3_FD1"]["DrStatDrv_B_Actl"], cp.vl["BodyInfo_3_FD1"]["DrStatPsngr_B_Actl"],
                        cp.vl["BodyInfo_3_FD1"]["DrStatRl_B_Actl"], cp.vl["BodyInfo_3_FD1"]["DrStatRr_B_Actl"]])
    ret.seatbeltUnlatched = cp.vl["RCMStatusMessage2_FD1"]["FirstRowBuckleDriver"] == 2

    # blindspot sensors
    if self.CP.enableBsm:
      cp_bsm = cp_cam if self.CP.flags & FordFlags.CANFD else cp
      ret.leftBlindspot = cp_bsm.vl["Side_Detect_L_Stat"]["SodDetctLeft_D_Stat"] != 0
      ret.rightBlindspot = cp_bsm.vl["Side_Detect_R_Stat"]["SodDetctRight_D_Stat"] != 0

    # Stock steering buttons so that we can passthru blinkers etc.
    self.buttons_stock_values = cp.vl["Steering_Data_FD1"]
    # Stock values from IPMA so that we can retain some stock functionality
    self.acc_tja_status_stock_values = cp_cam.vl["ACCDATA_3"]
    self.lkas_status_stock_values = cp_cam.vl["IPMA_Data"]

    MadsCarState.update_mads(self, ret, can_parsers)
    CarStateExt.update(self, ret, ret_sp, can_parsers)

    ret.buttonEvents = [
      *create_button_events(self.distance_button, prev_distance_button, {1: ButtonType.gapAdjustCruise}),
      *create_button_events(self.lc_button, prev_lc_button, {1: ButtonType.lkas}),
      *self.button_events,
    ]

    # BluePilot: HEV telemetry and brake light status (delegated to carstate_ext)
    self.car_state_bp_msg = CarStateExt.update_car_state_bp(self, cp, cp_cam)
    return ret, ret_sp

  @staticmethod
  def get_can_parsers(CP, CP_SP):
    pt_messages = [
      # sig_address, frequency
      ("VehicleOperatingModes", 100),
      ("BrakeSysFeatures", 50),
      ("BrakeSysFeatures_2", 50),
      ("BCM_Lamp_Stat_FD1", float('nan')),  # Brake light status - non-critical, ignore for CAN validity
      ("Yaw_Data_FD1", 100),
      ("DesiredTorqBrk", 50),
      ("EngVehicleSpThrottle", 100),
      ("EngVehicleSpThrottle2", 50),
      ("BrakeSnData_4", 50),
      ("EngBrakeData", 10),
      ("Cluster_Info1_FD1", 10),
      ("EPAS_INFO", 50),
      ("Steering_Data_FD1", 10),
      ("BodyInfo_3_FD1", 2),
      ("RCMStatusMessage2_FD1", 10),
    ]

    # HEV overlay messages - use float('nan') to mark as non-critical for CAN validity
    # These messages may arrive at irregular intervals depending on vehicle state
    if CP.flags & FordFlags.HEV_CLUSTER_DATA:
      pt_messages.append(("Cluster_HEV_Data2", float('nan')))

    if CP.flags & FordFlags.HEV_BATTERY_DATA:
      pt_messages.append(("Battery_Traction_1_FD1", float('nan')))
      pt_messages.append(("Battery_Traction_3_FD1", float('nan')))
      pt_messages.append(("Battery_Traction_4_FD1", float('nan')))
      pt_messages.append(("MtrTracData_1_FD1", float('nan')))

    if CP.flags & FordFlags.ALT_STEER_ANGLE:
      pt_messages += [
        ("SteeringPinion_Data_Alt", 100),
        ("ParkAid_Data", 50),
        ("TransGearData",10),
      ]
    else:
      pt_messages += [
        ("SteeringPinion_Data", 100),
      ]
      if CP.transmissionType == TransmissionType.automatic:
        pt_messages += [
          ("PowertrainData_10",10)
        ]

    if CP.flags & FordFlags.CANFD:
      pt_messages += [
        ("Lane_Assist_Data3_FD1", 33),
        ("Cluster_Info_3_FD1", 10),
      ]
    else:
      pt_messages += [
        ("INSTRUMENT_PANEL", 1),
      ]

    if CP.transmissionType == TransmissionType.automatic:
      pt_messages += [
        ("Gear_Shift_by_Wire_FD1", 10),
      ]
    elif CP.transmissionType == TransmissionType.manual:
      pt_messages += [
        ("Engine_Clutch_Data", 33),
      ]

    if CP.enableBsm and not (CP.flags & FordFlags.CANFD):
      pt_messages += [
        ("Side_Detect_L_Stat", 5),
        ("Side_Detect_R_Stat", 5),
      ]

    cam_messages = [
      # sig_address, frequency
      ("ACCDATA", 50),
      ("ACCDATA_2", 50),
      ("ACCDATA_3", 5),
      ("IPMA_Data", 1),
    ]

    if CP.flags & FordFlags.CANFD:
      cam_messages += [
        ("IPMA_Data2", 1),
      ]

    # BluePilot: Q3 Ford IPMA also broadcasts the TSR speed limit on the camera bus. Marked
    # non-critical (nan) because traffic sign recognition is an optional Co-Pilot360 camera
    # feature -- trims without it simply never send this message, and it must not invalidate the
    # rest of carState when absent.
    #
    # ONE registration, covering both conditions. This existed twice: upstream bp-7.0 added a copy
    # inside the non-CANFD branch above, and this fork already had a TSR-flag-gated copy below it.
    # Both fired on a non-CANFD car carrying the TSR flag -- flags 18 on the retrofit Fusion --
    # and CANParser raised "Duplicate Message Check: 973" while building the camera parser, so
    # card died at startup and the device never got past "waiting to start". Neither branch is
    # redundant on its own: TSR comes from the camera fingerprint rather than the platform, so a
    # CANFD car can carry it, and non-CANFD cars get it regardless of the flag.
    if (CP.flags & FordFlags.TSR) or not (CP.flags & FordFlags.CANFD):
      cam_messages += [
        ("Traffic_RecognitnData", float('nan')),
      ]

    if CP.enableBsm and CP.flags & FordFlags.CANFD:
      cam_messages += [
        ("Side_Detect_L_Stat", 5),
        ("Side_Detect_R_Stat", 5),
      ]

    return {
      Bus.pt: CANParser(DBC[CP.carFingerprint][Bus.pt], pt_messages, CanBus(CP).main),
      Bus.cam: CANParser(DBC[CP.carFingerprint][Bus.pt], cam_messages, CanBus(CP).camera),
    }
