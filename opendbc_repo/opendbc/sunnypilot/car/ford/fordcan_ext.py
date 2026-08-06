"""
BluePilot Ford CAN message builder extensions.

Provides extended versions of the stock fordcan.py message builders with additional
parameters for BluePilot features:
  - Dynamic ramp_type and precision_type for lateral control
  - Split brake_actuate / precharge_actuate for smoother deceleration
  - BlueCruise cluster HUD status, TJA warnings, and hands-free messaging
  - Independent left/right lane line logic for LKAS UI
  - ICBM button injection for intelligent cruise button management

Each function mirrors the stock fordcan.py function it replaces, with an extended
parameter list. When stock carcontroller.py is refactored, it will import these
functions instead of the stock versions.
"""

from collections import namedtuple

from opendbc.car import structs
from opendbc.car.ford.fordcan import CanBus, calculate_lat_ctl2_checksum

HUDControl = structs.CarControl.HUDControl


_BP_LKA_SHADOW_CURVATURE_SCALE = 1e-6  # 1/meter per raw unit, matches ford.h's decode


def create_lka_msg(packer, CAN: CanBus, lat_active: bool, hud_control,
                    angle_mode_engaged: bool = False, shadow_curvature: float = 0.0):
  """
  Creates a CAN message for the Ford LKA Command.

  BluePilot extension: accepts lat_active and hud_control parameters for future
  lane departure warning integration (unused so far).

  Also carries angle_mode_engaged + shadow_curvature packed into bits with no DBC signal mapped to
  them -- confirmed unused (always 0, no cabana signal) on real F-150 dashcam routes. This message
  is one openpilot itself originates every cycle, and ford_tx_hook already reads other fields
  (LkaActvStats_D2_Req) directly out of these same bytes synchronously, in the same tx_hook call --
  no separate CAN ID, no RX round-trip needed (panda does not self-receive its own TX, confirmed
  2026-07-09 investigating a controlsMismatch caused by an earlier dedicated-message design).

  Byte layout (bits not covered by any Lane_Assist_Data1 DBC signal):
    byte 4 bit 0:     angle_mode_engaged
    byte 4 bits 1-4:  reserved (future bools)
    byte 5-6:         shadow_curvature (int16, scale 1e-6 1/m)
    byte 7:           reserved (future value)
  Must match the decode in ford.h's FORD_Lane_Assist_Data1 tx_hook check exactly.

  Frequency is 33Hz.
  """
  addr, dat, bus = packer.make_can_msg("Lane_Assist_Data1", CAN.main, {})
  dat = bytearray(dat)

  shadow_curvature_raw = int(round(shadow_curvature / _BP_LKA_SHADOW_CURVATURE_SCALE))
  shadow_curvature_raw = max(-32768, min(32767, shadow_curvature_raw)) & 0xFFFF

  dat[4] |= 1 if angle_mode_engaged else 0
  dat[5] = (shadow_curvature_raw >> 8) & 0xFF
  dat[6] = shadow_curvature_raw & 0xFF

  return addr, bytes(dat), bus


def create_lat_ctl_msg(packer, CAN: CanBus, lat_active: bool, ramp_type: int, precision_type: int,
                       path_offset: float, path_angle: float, curvature: float, curvature_rate: float):
  """
  Creates a CAN message for the Ford TJA/LCA Command (non-CAN FD).

  BluePilot extension: dynamic ramp_type and precision_type parameters.
  Stock hardcodes ramp_type=0 (Slow) and precision_type=1 (Precise).

  Ford lane centering uses a third-order polynomial to describe the road centerline:
    c0 (path_offset): lateral offset between vehicle and centerline (positive is right)
    c1 (path_angle): heading angle between vehicle and centerline (positive is right)
    c2 (curvature): curvature of the centerline (positive is left)
    c3 (curvature_rate): rate of change of curvature

  Frequency is 20Hz.
  """
  values = {
    "LatCtlRng_L_Max": 0,                       # Unknown [0|126] meter
    "HandsOffCnfm_B_Rq": 0,                     # Unknown: 0=Inactive, 1=Active [0|1]
    "LatCtl_D_Rq": 1 if lat_active else 0,      # Mode: 0=None, 1=ContinuousPathFollowing, 2=InterventionLeft,
                                                 #       3=InterventionRight, 4-7=NotUsed [0|7]
    "LatCtlRampType_D_Rq": ramp_type,           # Ramp speed: 0=Slow, 1=Medium, 2=Fast, 3=Immediate [0|3]
    "LatCtlPrecision_D_Rq": precision_type,     # Precision: 0=Comfortable, 1=Precise, 2/3=NotUsed [0|3]
    "LatCtlPathOffst_L_Actl": path_offset,      # Path offset [-5.12|5.11] meter
    "LatCtlPath_An_Actl": path_angle,           # Path angle [-0.5|0.5235] radians
    "LatCtlCurv_NoRate_Actl": curvature_rate,   # Curvature rate [-0.001024|0.00102375] 1/meter^2
    "LatCtlCurv_No_Actl": curvature,            # Curvature [-0.02|0.02094] 1/meter
  }
  return packer.make_can_msg("LateralMotionControl", CAN.main, values)


def create_lat_ctl2_msg(packer, CAN: CanBus, mode: int, ramp_type: int, precision_type: int,
                        path_offset: float, path_angle: float, curvature: float,
                        curvature_rate: float, counter: int):
  """
  Creates a CAN message for the Ford Lane Centering command (CAN FD).

  BluePilot extension: dynamic ramp_type and precision_type parameters.
  Stock hardcodes ramp_type=0 (Slow) and precision_type=1 (Precise).

  This message replaces LateralMotionControl on CAN FD platforms and includes
  counter and checksum fields.

  Frequency is 20Hz.
  """
  values = {
    "LatCtl_D2_Rq": mode,                       # Mode: 0=None, 1=PathFollowingLimitedMode, 2=PathFollowingExtendedMode,
                                                 #       3=SafeRampOut, 4-7=NotUsed [0|7]
    "LatCtlRampType_D_Rq": ramp_type,           # 0=Slow, 1=Medium, 2=Fast, 3=Immediate [0|3]
    "LatCtlPrecision_D_Rq": precision_type,     # 0=Comfortable, 1=Precise, 2/3=NotUsed [0|3]
    "LatCtlPathOffst_L_Actl": path_offset,      # [-5.12|5.11] meter
    "LatCtlPath_An_Actl": path_angle,           # [-0.5|0.5235] radians
    "LatCtlCurv_No_Actl": curvature,            # [-0.02|0.02094] 1/meter
    "LatCtlCrv_NoRate2_Actl": curvature_rate,   # [-0.001024|0.001023] 1/meter^2
    "HandsOffCnfm_B_Rq": 0,                     # 0=Inactive, 1=Active [0|1]
    "LatCtlPath_No_Cnt": counter,               # [0|15]
    "LatCtlPath_No_Cs": 0,                      # [0|255]
  }

  # Calculate checksum (reuse stock function)
  dat = packer.make_can_msg("LateralMotionControl2", 0, values)[1]
  values["LatCtlPath_No_Cs"] = calculate_lat_ctl2_checksum(mode, counter, dat)

  return packer.make_can_msg("LateralMotionControl2", CAN.main, values)


def create_acc_msg(packer, CAN: CanBus, long_active: bool, gas: float, accel: float, accel_pred: float,
                   stopping: bool, brake_actuate: bool, precharge_actuate: bool, v_ego_kph: float):
  """
  Creates a CAN message for the Ford ACC Command.

  BluePilot extension: split brake control into brake_actuate and precharge_actuate
  (each with independent hysteresis thresholds) for smoother deceleration. Also
  accepts accel_pred as a parameter instead of hardcoding -5.0.

  Precharge engages slightly before full brake for smoother initial decel feel.
  Both use configurable hysteresis to avoid binary on/off feel.

  Frequency is 50Hz.
  """
  values = {
    "AccBrkTot_A_Rq": accel,                          # Brake total accel request: [-20|11.9449] m/s^2
    "Cmbb_B_Enbl": 1 if long_active else 0,           # Enabled: 0=No, 1=Yes
    "AccPrpl_A_Rq": gas,                               # Acceleration request: [-5|5.23] m/s^2
    "AccPrpl_A_Pred": accel_pred,                      # Predicted accel (from carcontroller, not hardcoded)
    "AccResumEnbl_B_Rq": 1 if long_active else 0,
    "AccVeh_V_Trg": v_ego_kph,                         # Target speed: [0|255] km/h
    "AccBrkPrchg_B_Rq": 1 if precharge_actuate else 0, # Pre-charge brake request (independent hysteresis)
    "AccBrkDecel_B_Rq": 1 if brake_actuate else 0,     # Deceleration request (independent hysteresis)
    "AccStopStat_B_Rq": 1 if stopping else 0,
  }
  return packer.make_can_msg("ACCDATA", CAN.main, values)


def create_acc_ui_msg(packer, CAN: CanBus, CP, main_on: bool, enabled: bool, fcw_alert: bool,
                      standstill: bool, hud_control, stock_values: dict, send_hands_free_msg: bool,
                      send_ui: bool, send_bars: bool, tja_warn: int, tja_msg: int):
  """
  Creates a CAN message for the Ford IPC adaptive cruise, FCW and TJA status.

  BluePilot extension: replaces stock show_distance_bars with explicit send_ui,
  send_bars, and TJA parameters. Adds BlueCruise status 7 for hands-free cluster
  UI. TJA warn/msg are set from DM state computation rather than stock passthrough.

  Stock functionality is maintained by passing through unmodified signals.

  Frequency is 5Hz.
  """

  # Tja_D_Stat: TJA status for cluster display
  if enabled:
    if hud_control.leftLaneDepart:
      status = 3  # ActiveInterventionLeft
    elif hud_control.rightLaneDepart:
      status = 4  # ActiveInterventionRight
    elif send_hands_free_msg:
      status = 7  # BlueCruise UI in the cluster
    else:
      status = 2  # Active
  elif main_on:
    if hud_control.leftLaneDepart:
      status = 5  # ActiveWarningLeft
    elif hud_control.rightLaneDepart:
      status = 6  # ActiveWarningRight
    else:
      status = 1  # Standby
  elif standstill:
    status = 0  # Off
  else:
    status = 1  # Standby

  values = {s: stock_values[s] for s in [
    "HaDsply_No_Cs",
    "HaDsply_No_Cnt",
    "AccStopStat_D_Dsply",       # ACC stopped status message
    "AccTrgDist2_D_Dsply",       # ACC target distance
    "AccStopRes_B_Dsply",
    # TjaWarn_D_Rq and TjaMsgTxt_D_Dsply are set explicitly below, not passed through
    "IaccLamp_D_Rq",             # iACC status icon
    "AccMsgTxt_D2_Rq",           # ACC text
    "FcwDeny_B_Dsply",           # FCW disabled
    "FcwMemStat_B_Actl",         # FCW enabled setting
    "AccTGap_B_Dsply",           # ACC time gap display setting
    "CadsAlignIncplt_B_Actl",
    "AccFllwMde_B_Dsply",        # ACC follow mode display setting
    "CadsRadrBlck_B_Actl",
    "CmbbPostEvnt_B_Dsply",      # AEB event status
    "AccStopMde_B_Dsply",        # ACC stop mode display setting
    "FcwMemSens_D_Actl",         # FCW sensitivity setting
    "FcwMsgTxt_D_Rq",            # FCW text
    "AccWarn_D_Dsply",           # ACC warning
    "FcwVisblWarn_B_Rq",         # FCW visible alert
    "FcwAudioWarn_B_Rq",         # FCW audio alert
    "AccTGap_D_Dsply",           # ACC time gap
    "AccMemEnbl_B_RqDrv",        # ACC adaptive/normal setting
    "FdaMem_B_Stat",             # FDA enabled setting
  ]}

  values.update({
    "Tja_D_Stat": status,         # TJA status
    "TjaWarn_D_Rq": tja_warn,    # TJA warning (from DM state, not stock passthrough)
    "TjaMsgTxt_D_Dsply": tja_msg, # TJA text (from DM state, not stock passthrough)
  })

  if CP.openpilotLongitudinalControl:
    values.update({
      "AccStopStat_D_Dsply": 2 if standstill else 0,              # Stopping status text
      "AccMsgTxt_D2_Rq": 0,                                       # ACC text
      "AccTGap_B_Dsply": 1 if send_bars else 0,                   # Show time gap control UI
      "AccFllwMde_B_Dsply": 1 if hud_control.leadVisible else 0,  # Lead indicator
      "AccStopMde_B_Dsply": 1 if standstill else 0,
      "AccWarn_D_Dsply": 0,                                        # ACC warning
      "AccTGap_D_Dsply": hud_control.leadDistanceBars,            # Time gap
    })

  # Forward FCW alert from IPMA
  if fcw_alert:
    values["FcwVisblWarn_B_Rq"] = 1  # FCW visible alert
    values["FcwAudioWarn_B_Rq"] = 1  # FCW audio alert

  return packer.make_can_msg("ACCDATA_3", CAN.main, values)


# BluePilot: what the cluster is being asked to say, per side. Built in hud_ext from the planner.
#
#   suggestion      Side it wants to move to now: 0 none, 1 left, 2 right.
#   maneuver_side   Side a maneuver has committed to, once it is past deciding.
#   maneuver_moving True from the blinker going on until the sequence lets go.
#   oncoming_left   A vehicle detected RIGHT NOW coming at us in that lane. Live sighting only --
#   oncoming_right  the 90 s memory is a decision input, and a line held yellow for a minute and a
#                   half down every canyon road is noise, not information.
ClusterPassing = namedtuple("ClusterPassing",
                            "suggestion maneuver_side maneuver_moving oncoming_left oncoming_right")
CLUSTER_PASSING_IDLE = ClusterPassing(0, 0, False, False, False)

# LaActvStats_D_Dsply, per side. The names are the DBC's.
LANE_NONE, LANE_AVAILABLE, LANE_SUPPRESS, LANE_WARNING, LANE_INTERVENE = 0, 1, 2, 3, 4
LANE_LA_OFF = 30          # whole-display value, outside the 5x5 matrix
LANE_WARN_LEFT_ONLY = 3   # upstream's departure values, for the not-steering branch
LANE_WARN_RIGHT_ONLY = 15


def _passing_line(side: int, visible: bool, pa: ClusterPassing) -> int:
  """One lane line as a four-level meter for its own side. Highest thing true wins.

  The old display used one state for one meaning and left the other four unused, which is why it
  could only ever say "over there" and never how strongly or why. Read top to bottom this is the
  order a driver needs it in: a hazard outranks a commitment, a commitment outranks a wish.
  """
  oncoming = pa.oncoming_left if side == 1 else pa.oncoming_right
  if oncoming:
    # DO NOT GO THERE. The departure look is the car's own vocabulary for danger on a side, and for
    # opposing traffic in the lane we were about to take it is the literally correct thing to say --
    # unlike a passing suggestion, which is what made borrowing it wrong before.
    return LANE_WARNING
  if pa.maneuver_moving and pa.maneuver_side == side:
    return LANE_INTERVENE   # going now: blinker on, or already crossing
  if pa.suggestion == side:
    return LANE_SUPPRESS    # the lane is open -- the line gives way toward the gap
  if visible:
    return LANE_AVAILABLE   # normal
  return LANE_NONE          # nothing to draw


def create_lkas_ui_msg(packer, CAN: CanBus, main_on: bool, enabled: bool, hands: int,
                       hud_control, stock_values: dict, passing: 'ClusterPassing | None' = None,
                       lane_test: int | None = None):
  """
  Creates a CAN message for the Ford IPC IPMA/LKAS status.

  BluePilot extension: replaces stock steer_alert bool with hands int (0-3),
  and uses independent left/right lane line status logic.

  hands values:
    0 = HandsOn
    1 = Level1 (warning without chime)
    2 = Level2 (warning with chime)
    3 = Suppressed

  LaActvStats_D_Dsply value table (left \\ right):
    Right →    | Intervene | Warning | Suppress | Available | None
    Intervene  | 24        | 19      | 14       | 9         | 4
    Warning    | 23        | 18      | 13       | 8         | 3
    Suppress   | 22        | 17      | 12       | 7         | 2
    Available  | 21        | 16      | 11       | 6         | 1
    None       | 20        | 15      | 10       | 5         | 0

  Stock functionality is maintained by passing through unmodified signals.

  Frequency is 1Hz.
  """

  pa = passing if passing is not None else CLUSTER_PASSING_IDLE

  if hud_control is None:
    lines = LANE_NONE

  elif not enabled:
    # NOT STEERING. Upstream branches three ways here on main_on and enabled; BluePilot used
    # neither argument and computed the engaged-style values unconditionally, so the lines went
    # green whenever the model saw paint whether or not anything was holding the lane. That is
    # almost certainly "my LKA display just shows green on both sides of my car all the time, no
    # matter what", and it costs the green its meaning: if it is on when nothing is steering, it
    # cannot also mean openpilot has the wheel.
    #
    # Restored to upstream's shape. Green now means openpilot is steering, which is what makes
    # everything below readable at a glance.
    if main_on:
      lines = LANE_NONE
    elif hud_control.leftLaneDepart:
      lines = LANE_WARN_LEFT_ONLY
    elif hud_control.rightLaneDepart:
      lines = LANE_WARN_RIGHT_ONLY
    else:
      lines = LANE_LA_OFF

  else:
    # STEERING. THE CLUSTER IS THE PASSING-ASSIST INSTRUMENT.
    #
    # His idea, then his follow-up: "this entire screen on my car can be reused for passing assist
    # or other features... I'm fine if we overhaul that screen to make it more useful." So each
    # line stops being a lane marker and becomes a four-level meter for its own side -- see
    # _passing_line for the order. The pair then reads as a direction AND an intensity without the
    # driver learning anything: the side that changes is the side it means, and how far it changes
    # is how serious it is.
    #
    # Nothing here can collide with lane departure, and that is structural rather than a judgement
    # about acceptable risk. ldw.py line 21:
    #
    #     ldw_allowed = CS.vEgo > LDW_MIN_SPEED and not recent_blinker and not CC.latActive
    #
    # openpilot does not COMPUTE departure while it is steering -- the thing keeping the car in its
    # lane does not also warn about leaving it -- so the departure branches belong to the not-
    # steering case above and nowhere else. test_ldw_does_not_run_while_steering asserts that
    # against openpilot's own source, so an upstream change to the condition fails before it
    # reaches the car.
    lines = (_passing_line(1, hud_control.leftLaneVisible, pa) +
             5 * _passing_line(2, hud_control.rightLaneVisible, pa))

  # The display test overrides everything, including the departure branches above. It only ever runs
  # at a standstill, where none of them can be true, and it has to be able to SEND the departure
  # states -- learning what they look like is the entire point. Raw, because LA_Off is 30, outside
  # the 5x5 matrix. See lane_display_test_ext.
  if lane_test is not None:
    lines = int(lane_test)

  values = {s: stock_values[s] for s in [
    "FeatConfigIpmaActl",
    "FeatNoIpmaActl",
    "PersIndexIpma_D_Actl",
    "AhbcRampingV_D_Rq",     # AHB ramping
    "LaDenyStats_B_Dsply",   # LKAS error
    "CamraDefog_B_Req",      # Windshield heater?
    "CamraStats_D_Dsply",    # Camera status
    "DasAlrtLvl_D_Dsply",    # DAS alert level
    "DasStats_D_Dsply",      # DAS status
    "DasWarn_D_Dsply",       # DAS warning
    "AhbHiBeam_D_Rq",       # AHB status
    "Passthru_63",
    "Passthru_48",
  ]}

  values.update({
    "LaActvStats_D_Dsply": lines,  # LKAS status (lane lines) [0|31]
    "LaHandsOff_D_Dsply": hands,   # 0=HandsOn, 1=Level1 (w/o chime), 2=Level2 (w/ chime), 3=Suppressed
  })
  return packer.make_can_msg("IPMA_Data", CAN.main, values)


def create_button_msg(packer, bus: int, stock_values: dict, cancel=False, resume=False,
                      tja_toggle=False, icbm_button=None, turn_signal=None):
  """
  Creates a CAN message for the Ford SCCM buttons/switches.

  BluePilot extension: adds icbm_button parameter for Intelligent Cruise Button
  Management. When set, the specified CAN signal is set to 1 in the outgoing
  message, enabling openpilot to emulate cruise button presses for speed adjustment.

  Args:
    icbm_button: Optional string signal name (e.g., "CcAslButtnSetIncPress",
                 "CcAslButtnSetDecPress") for ICBM button injection.

  Frequency is 10Hz.
  """
  values = {s: stock_values[s] for s in [
    "HeadLghtHiFlash_D_Stat",  # SCCM Passthrough the remaining buttons
    "TurnLghtSwtch_D_Stat",    # SCCM Turn signal switch
    "WiprFront_D_Stat",
    "LghtAmb_D_Sns",
    "AccButtnGapDecPress",
    "AccButtnGapIncPress",
    "AslButtnOnOffCnclPress",
    "AslButtnOnOffPress",
    "LaSwtchPos_D_Stat",
    "CcAslButtnCnclResPress",
    "CcAslButtnDeny_B_Actl",
    "CcAslButtnIndxDecPress",
    "CcAslButtnIndxIncPress",
    "CcAslButtnOffCnclPress",
    "CcAslButtnOnOffCncl",
    "CcAslButtnOnPress",
    "CcAslButtnResDecPress",
    "CcAslButtnResIncPress",
    "CcAslButtnSetDecPress",
    "CcAslButtnSetIncPress",
    "CcAslButtnSetPress",
    "CcButtnOffPress",
    "CcButtnOnOffCnclPress",
    "CcButtnOnOffPress",
    "CcButtnOnPress",
    "HeadLghtHiFlash_D_Actl",
    "HeadLghtHiOn_B_StatAhb",
    "AhbStat_B_Dsply",
    "AccButtnGapTogglePress",
    "WiprFrontSwtch_D_Stat",
    "HeadLghtHiCtrl_D_RqAhb",
  ]}

  values.update({
    "CcAslButtnCnclPress": 1 if cancel else 0,      # CC cancel button
    "CcAsllButtnResPress": 1 if resume else 0,       # CC resume button
    "TjaButtnOnOffPress": 1 if tja_toggle else 0,    # LCA/TJA toggle button
  })

  # ICBM button support — set the specified button signal to 1
  if icbm_button is not None:
    values[icbm_button] = 1

  # BluePilot: turn-signal override for the stationary blinker actuation test. None means keep the
  # driver's own switch position, which is the passthrough above and the case on every normal
  # frame. Only BlinkerTestExt ever passes a value here.
  if turn_signal is not None:
    values["TurnLghtSwtch_D_Stat"] = turn_signal

  return packer.make_can_msg("Steering_Data_FD1", bus, values)
