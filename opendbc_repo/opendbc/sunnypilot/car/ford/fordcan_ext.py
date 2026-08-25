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

from opendbc.car import structs
from opendbc.car.ford.fordcan import CanBus, calculate_lat_ctl2_checksum
from opendbc.sunnypilot.car.ford import apim_gps
from opendbc.sunnypilot.car.ford.values_ext import FordSafetyFlagsSP

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
  # FusionPilot: CLAMP TO PANDA'S BAND, BECAUSE PANDA DROPS WHAT IT WILL NOT PASS.
  #
  # `stop_override.py` opens by saying this function "already clamps to panda's bands". It did not.
  # The clamp lived in `create_acc_msg_passthrough` -- a different function -- and only on the top.
  #
  # THE TWO NUMBERS DISAGREE BY 0.0009 m/s^2 AND THAT IS THE WHOLE BUG:
  #
  #     CarControllerParams.ACCEL_MIN  = -3.5      openpilot clips its request to this
  #     _PANDA_ACCEL_MIN               = -3.4991   panda refuses anything below this
  #
  # So every frame that reached the clip -- which is every hardest-braking frame -- was handed to
  # panda 0.0009 below its floor and REJECTED. A rejected frame is not a softer command, it is no
  # command at all, and ACCDATA carries a rolling counter that a gap breaks.
  #
  # MEASURED on route 000003b8, 2026-08-24, the drive where the stop override took a red light for
  # the first time: during its 2.8 s of authority, 220 frames went out (all clamped at -3.499) and
  # 15 were REJECTED at -3.503 .. -3.542. Ford ACC faulted 2.8 s in -- `accFaulted`, the disengage
  # he heard -- and never recovered; the next route came up "Cruise Fault: Restart the car".
  # Rejections under every other authority carried legal values and are a different question.
  #
  # Clamping loses 0.006 m/s^2 of braking at the extreme. Not clamping loses the entire frame.
  accel = min(max(float(accel), _PANDA_ACCEL_MIN + _PANDA_MARGIN), _PANDA_ACCEL_MAX - _PANDA_MARGIN)
  # Same treatment for the gas request, which has the same shape of band and the same raw write.
  # `_PANDA_GAS_INACTIVE` (-5.0) is the deliberate "not requesting" sentinel and is left alone --
  # it sits far outside the band on purpose and panda exempts it.
  if abs(float(gas) - _PANDA_GAS_INACTIVE) >= 0.005:
    gas = min(max(float(gas), _PANDA_GAS_MIN + _PANDA_MARGIN), _PANDA_GAS_MAX - _PANDA_MARGIN)
  # AND `AccPrpl_A_Pred` GETS IT TOO, because panda's gas band polices BOTH fields
  # (`longitudinal_gas_checks` is called on gas and gas_pred alike) and this one was going out RAW
  # while its two siblings were clamped. Found in review, 2026-08-24, right after the panda floor
  # was widened to -2.8: the widening quietly enlarged what this unclamped field could put on the
  # wire, which is the one thing that change was documented as NOT doing.
  #
  # It is a no-op today and that is exactly why it is worth pinning: `accel_pred_send` is the
  # constant `CarControllerParams.INACTIVE_GAS` in longitudinal_ext.py, so the safety of the
  # openpilot-authored path rests on a value defined in a different file that nobody would think
  # to check when changing this one. Same shape as every "a fixture held it constant" bug here.
  if abs(float(accel_pred) - _PANDA_GAS_INACTIVE) >= 0.005:
    accel_pred = min(max(float(accel_pred), _PANDA_GAS_MIN + _PANDA_MARGIN), _PANDA_GAS_MAX - _PANDA_MARGIN)

  values = {
    "AccBrkTot_A_Rq": accel,                          # Brake total accel request: [-20|11.9449] m/s^2
    "Cmbb_B_Enbl": 1 if long_active else 0,           # Enabled: 0=No, 1=Yes
    "AccPrpl_A_Rq": gas,                               # Acceleration request: [-5|5.23] m/s^2
    "AccPrpl_A_Pred": accel_pred,                      # Predicted accel; clamped above, see the note
    "AccResumEnbl_B_Rq": 1 if long_active else 0,
    "AccVeh_V_Trg": v_ego_kph,                         # Target speed: [0|255] km/h
    "AccBrkPrchg_B_Rq": 1 if precharge_actuate else 0, # Pre-charge brake request (independent hysteresis)
    "AccBrkDecel_B_Rq": 1 if brake_actuate else 0,     # Deceleration request (independent hysteresis)
    "AccStopStat_B_Rq": 1 if stopping else 0,
  }
  return packer.make_can_msg("ACCDATA", CAN.main, values)


# FusionPilot: every signal the DBC defines for ACCDATA (0x186). Listed rather than derived so a
# missing one is a NameError here instead of a silently zeroed field on the wire.
_ACCDATA_SIGNALS = (
  "AccBrkPulse_B_Rq", "AccAutoResum_D_Rq", "AccBrkTot_A_Rq", "AccPrpl_A_Pred", "AccVeh_V_Trg",
  "AccBrkPrkEl_B_Rq", "Cmbb_B_Enbl", "CmbbOvrrd_B_RqDrv", "CmbbDeny_B_Actl", "CmbbEngTqMn_B_Rq",
  "AccPrpl_A_Rq", "AccDeny_B_Rq", "AccResumEnbl_B_Rq", "AccCancl_B_Rq", "AccBrkPrchg_B_Rq",
  "AccBrkDecel_B_Rq", "AccStopStat_B_Rq",
)


def create_acc_msg_passthrough(packer, CAN: CanBus, stock_values: dict, clear_cancel: bool = False,
                               gas_floor: float | None = None):
  """FusionPilot: re-send the CAMERA's own ACC command, unchanged.

  Under openpilot longitudinal control the relay is open, so the camera's ACCDATA never reaches the
  car -- panda blocks it, by design, because openpilot is meant to author it instead. But the camera
  is still fed everything it needs (bus 0 is forwarded to bus 2) and is still computing and
  publishing ACC on the camera bus. So we can put Ford's OWN numbers back on the wire.

  **That is the whole point: this does not try to match Ford ACC, it forwards Ford ACC.** The owner's
  position, and the reason this exists at all: *"I trust how Ford ACC works."* Everything openpilot
  would have to get right about longitudinal tuning is skipped, because the tuning is Ford's.

  WHY A REPACK IS FAITHFUL HERE, measured rather than hoped:

    - ACCDATA carries NO COUNTER AND NO CHECKSUM. Nothing has to stay in sync, and handing control
      back after an override needs no resynchronization. That is normally what makes this class of
      idea impossible.
    - The DBC leaves EIGHT BITS unmapped (5, 18-23, 35), which a repack would zero. Checked across
      108,388 real ACCDATA frames on this car: all eight are zero in every one. So the repacked
      frame is byte-identical in practice.

  The second one is a MEASUREMENT, not a guarantee. If Ford ever sets one of those bits this drops
  it silently. They are undocumented reserved bits on a message with no integrity fields, so the
  risk is accepted -- but it is the reason this function lists signals explicitly rather than
  looking clever.
  """
  values = {s: stock_values[s] for s in _ACCDATA_SIGNALS}

  # CLEARING A CANCEL WE PROVOKED OURSELVES, 2026-08-22. Off unless the caller has established both
  # that the stop override ran and that the camera has been asking to cancel ever since -- see the
  # recovery block in `carcontroller.py`. Every other refusal in `passthrough_admissible` still
  # applies, so a frame that reaches here has already cleared the deny bits, the park brake and
  # both panda bands; the ONLY thing being ignored is the request to stop doing ACC.
  #
  # Without this the latch is permanent and it is OURS: the cancel makes every subsequent frame
  # inadmissible, so Ford's command never reaches the car again, so the camera never observes the
  # car obeying it, so it has no reason to release. Measured on route 000003a8 -- he re-engaged at
  # t+886 and the camera was computing a sane 46 kph target while still asserting cancel.
  if clear_cancel:
    values["AccCancl_B_Rq"] = 0

  # ONE FIELD IS OVERRIDDEN, and only because panda will not carry Ford's version of it.
  #
  # `AccPrpl_A_Pred` is the PREDICTED acceleration -- a feed-forward hint to the powertrain, not a
  # command. Panda holds it to [-0.5, 2.0] with a single legal escape at exactly -5.0, and drive A
  # showed Ford sweeping it through -1.79 -> -1.29 while coasting: 25.2% of engaged frames, and the
  # second-largest reason a frame could not be forwarded.
  #
  # -5.0 is not an invented value. It is what UPSTREAM openpilot hardcodes here, so it is exactly
  # what this car's PCM sees on every normal op-long frame. Pinning it costs Ford a hint the PCM
  # already lives without, and buys back nearly ten points of forwarding.
  values["AccPrpl_A_Pred"] = _PANDA_GAS_INACTIVE

  # AND `AccPrpl_A_Rq` IS CLAMPED AT THE TOP, WHICH IS THE WHOLE OF "OP LONG LAUNCHES SLOWLY".
  #
  # Measured on route 00000393, 2026-08-19: of 994 frames where the passthrough fell back, 624 were
  # under 15 mph, and the reason is a smear of values sitting just over panda's ceiling --
  #
  #     AccPrpl_A_Rq  2.000  2.020  2.030  2.050  2.060  2.070  2.080  2.090 ...
  #
  # Ford's ordinary pull-away propulsion IS 2.0 m/s^2, which is exactly `_PANDA_GAS_MAX`, and the
  # 0.005 margin puts even a clean 2.000 outside. So essentially every launch was refused and handed
  # to openpilot, whose launch is far gentler. He reported it as the car switching to op long and
  # going "ridiculously slow" -- and he was right that it was not a tuning preference: nothing ever
  # chose openpilot for launches, this refusal is all it was.
  #
  # CLAMPING BEATS REFUSING because refusing throws away the WHOLE frame -- Ford's brake command,
  # its stop status, everything -- and substitutes a different controller's idea of the moment.
  # Clamping keeps every other signal Ford authored and gives up 0.005 m/s^2 of propulsion.
  #
  # BOTH ENDS, as of 2026-08-24. The low side used to be refused, on the reasoning that clamping UP
  # from -0.77 to -0.495 asks for less ENGINE BRAKING than Ford wanted. That reasoning assumed the
  # fallback controller would do at least as much braking. IT DOES NOT -- it does far more, in a
  # lurch.
  #
  # MEASURED on route 000003bc, t+103.59..103.87, one of eleven such refusals on that drive:
  #
  #     FORD wanted brake  -0.11 .. -0.13 m/s^2 throughout
  #     WE sent            -0.18, -0.25, ... -1.09, -1.16   then snapped back to -0.13
  #
  # 0.28 s of up to NINE TIMES Ford's braking, because openpilot's longitudinal controller has been
  # watching the car ignore it while Ford drove and arrives already wound up (see the note on
  # `_urgent_speed_gap`: it sits at its -3.5 floor for over 10% of engaged frames). Handing it the
  # frame for a quarter second is a jolt, not a safer deceleration.
  #
  # So the trade is really: give up 0.125 m/s^2 of POWERTRAIN braking -- Ford's own brake command
  # still goes out untouched on the same frame -- or take a 1 m/s^2 spike. Clamp.
  #
  # `AccBrkTot_A_Rq` IS STILL REFUSED AT THE BOTTOM and must stay that way: that field IS the brake,
  # and asking for less of it than Ford wanted is the one direction no measurement excuses.
  #
  # AND THE FLOOR IS NOW FORD'S OWN, NOT UPSTREAM'S. FusionPilot, 2026-08-24. The clamp above was
  # the right call against a -0.5 floor, but the owner was right that it still costs real braking:
  # 756 frames on one drive, median 0.415 and worst 1.095 m/s^2 of powertrain deceleration given
  # up, in episodes up to 17.5 s. The -0.5 was never Ford's number -- it is a generic openpilot
  # constant, and Ford commands below it on 2.86% of all frames in ordinary driving.
  #
  # So panda's floor widens to -2.8 while the passthrough is on (FordSafetyFlagsSP.PASSTHROUGH_LONG)
  # and the clamp here follows it. The clamp does NOT go away: Ford's measured minimum is -2.710
  # and the band has to end somewhere, so a frame past the new floor is still softened rather than
  # thrown to a wound-up controller.
  #
  # `gas_floor` MUST come from the same flag bit panda was given -- see `passthrough_gas_floor`.
  # Defaulting to the NARROW floor is the safe direction: clamping more than panda requires costs
  # a little braking, while clamping less makes panda drop the whole frame at 50 Hz.
  gas_min = _PANDA_GAS_MIN if gas_floor is None else gas_floor
  gas = float(values["AccPrpl_A_Rq"])
  if abs(gas - _PANDA_GAS_INACTIVE) >= 0.005:
    if gas > (_PANDA_GAS_MAX - _PANDA_MARGIN):
      values["AccPrpl_A_Rq"] = _PANDA_GAS_MAX - _PANDA_MARGIN
    elif gas < (gas_min + _PANDA_MARGIN):
      values["AccPrpl_A_Rq"] = gas_min + _PANDA_MARGIN

  # AND `AccBrkTot_A_Rq` HAS THE SAME CEILING AND WAS NEVER CLAMPED. FusionPilot, 2026-08-23.
  #
  # Straight off his swaglog, every one of these a refused frame and a hand to openpilot:
  #
  #     AccBrkTot_A_Rq  1.996  2.019  2.043  2.066  2.090  2.105  2.125  2.140
  #
  # `_PANDA_ACCEL_MAX` is 1.9999, so with the margin anything from 1.995 up was thrown away --
  # and despite the name this field is Ford's TOTAL acceleration request, positive when it is
  # accelerating. Ford sits on that ceiling pulling away exactly as it sits on the gas ceiling.
  #
  # THE SAME ASYMMETRY DECIDES IT, and it is why this is a clamp and not a wider band. Clamping
  # DOWN from 2.14 to 1.995 asks for LESS acceleration than Ford wanted -- conservative. Clamping
  # UP from -3.6 to -3.494 would ask for less BRAKING than Ford wanted, which is not, so the
  # bottom stays a refusal and still falls back.
  #
  # The note further down this file saying `AccBrkTot_A_Rq` is "carried verbatim, where a silent
  # softening would be indistinguishable from working until it mattered" was written about the
  # BRAKING side and is still right about it. It was read as covering the whole field, which is
  # how a ceiling Ford touches on every launch went a week without being looked at -- the same
  # week the identical bug on `AccPrpl_A_Rq` was found, measured and fixed.
  accel = float(values["AccBrkTot_A_Rq"])
  if accel > (_PANDA_ACCEL_MAX - _PANDA_MARGIN):
    values["AccBrkTot_A_Rq"] = _PANDA_ACCEL_MAX - _PANDA_MARGIN

  return packer.make_can_msg("ACCDATA", CAN.main, values)


# FusionPilot: panda's ACCDATA bands, from opendbc/safety/modes/ford.h's FORD_LONG_LIMITS, in the
# engineering units the DBC scales to. THE BRAKE CAP IS NOT THE TIGHT ONE -- that was the review's
# correction. The gas band is [-0.5, 2.0] with a single legal escape value of exactly -5.0, and it
# is checked against BOTH AccPrpl_A_Rq and AccPrpl_A_Pred.
_PANDA_ACCEL_MIN = -3.4991
_PANDA_ACCEL_MAX = 1.9999
_PANDA_GAS_MIN = -0.5
# FusionPilot: the floor panda uses INSTEAD of the above while the stock ACC passthrough is
# forwarding Ford's own frame. Must match FORD_MIN_GAS_PASSTHROUGH in safety/modes/ford.h
# (raw 220 = (-2.8 + 5.0) * 100).
_PANDA_GAS_MIN_PASSTHROUGH = -2.8
_PANDA_GAS_MAX = 2.0
_PANDA_GAS_INACTIVE = -5.0
# Half a quantum of the coarsest field (AccPrpl_* at 0.01 m/s^2 per bit), which is all the guard a
# DBC round-trip can need. It was 0.02 and that refused ten frames sitting exactly on the -0.5
# boundary that panda would have accepted. Ten frames is nothing; the PRINCIPLE is not, because
# refusing a frame panda would have carried is the whole mechanism behind the drive-A cascade.
_PANDA_MARGIN = 0.005


def passthrough_gas_floor(CP_SP) -> float:
  """FusionPilot: the AccPrpl_A_Rq floor panda is actually enforcing, this drive.

  READ FROM `CP_SP.safetyParam` RATHER THAN FROM THE PARAM, deliberately. That int is the exact
  value delivered to the safety firmware as current_safety_param_sp (USB 0xdf), so this cannot
  disagree with what panda decided -- and a disagreement in the permissive direction is the
  expensive one: we forward -2.0, panda still holds -0.5, and it drops the WHOLE frame. A 50 Hz
  message vanishing and reappearing is worse than either controller driving, which is the whole
  reason `passthrough_admissible` exists.

  Falls back to the narrow floor on anything unexpected. That direction only costs braking.

  Note `CP_SP.safetyParam` is set by `_initialize_ford` during `setup_interfaces`, which card.py
  runs AFTER the CarController is constructed -- so this must be called per frame, not cached in
  __init__, or it reads a zero that has since been filled in.
  """
  try:
    if int(getattr(CP_SP, "safetyParam", 0)) & FordSafetyFlagsSP.PASSTHROUGH_LONG:
      return _PANDA_GAS_MIN_PASSTHROUGH
  except (TypeError, ValueError):
    pass
  return _PANDA_GAS_MIN


def passthrough_admissible(stock_values: dict, long_active: bool, allow_cancel: bool = False) -> str:
  """FusionPilot: would panda actually let this camera frame through? "" if yes, else the reason.

  THIS IS THE FIX FOR THE THING THAT MAKES THIS FEATURE FAIL WORST. `ford_tx_hook` does not clamp a
  bad value -- it drops the WHOLE MESSAGE. So a forwarded frame panda dislikes does not produce a
  slightly-wrong command, it produces a 50 Hz message that simply stops for as long as Ford holds
  that value, and then resumes. Intermittent absence is a far worse failure than either extreme.

  Three ways the camera's own frame is inadmissible, all read straight out of ford.h:

    - `CmbbDeny_B_Actl` set. `violation |= cmbb_deny` is unconditional there. This is not
      hypothetical on this car: ford/carstate.py already maps that same bit to `ret.accFaulted`.
    - Any of the three scaled requests outside its band. The brake cap (-3.4991) was measured across
      189,418 frames and never bound; the GAS band was never measured at all, and it is four times
      narrower and sits exactly where a coasting or engine-braking Ford lives.
    - openpilot longitudinal not active. `get_longitudinal_allowed()` is false, and then the only
      frame panda passes is the inactive one -- so every real Ford command is dropped.

  Returning a REASON rather than a bool is deliberate: whether these ever fire on his roads is the
  open question this feature turns on, and the answer has to end up in the log rather than only in
  a branch taken.
  """
  if not long_active:
    return "openpilot longitudinal inactive"
  if stock_values.get("CmbbDeny_B_Actl"):
    return "camera set CmbbDeny_B_Actl"

  # THE PARKING BRAKE. Drive A, 2026-08-18: pressing resume behind a stopped car applied the
  # ELECTRIC PARK BRAKE. `AccBrkPrkEl_B_Rq` is bit 38 of this message, its receivers are GWM and
  # ABS_ESC, `create_acc_msg` never sets it, and the relay blocks the camera's own copy -- so the
  # only path to the ABS was this function forwarding it.
  #
  # WHAT IS NOT KNOWN, and was stated too confidently the first time: whether Ford ACC normally
  # does this at all. The owner has never heard it on this car, and **no indicator light came on**,
  # which does not fit a genuine EPB application -- the lamp is driven off actual EPB state. So
  # there are two live explanations and the logs decide between them:
  #
  #   - the ABS applied HYDRAULIC brake hold rather than the park brake, which is lampless, and the
  #     signal name is describing a request the ABS services its own way; or
  #   - the park brake really was applied and the lamp path was broken by the open relay, since the
  #     cluster is told about ACC state over messages the camera no longer delivers.
  #
  # Either way the fault is ours, not Ford's: we relayed a stop-and-hold request out of a controller
  # whose model of the car had diverged from the car, into an actuator panda does not police.
  # `carState.parkingBrake` plus the raw ACCDATA bits in drive A's log settle which one it was.
  #
  # THAT IS THE GENERAL LESSON AND IT IS BIGGER THAN THIS BIT. Panda checks five things in ACCDATA.
  # It does NOT check AccBrkPrkEl_B_Rq, AccStopStat_B_Rq, AccCancl_B_Rq, AccDeny_B_Rq,
  # AccResumEnbl_B_Rq, AccAutoResum_D_Rq, AccBrkPulse_B_Rq, Cmbb_B_Enbl, CmbbOvrrd_B_RqDrv or
  # CmbbEngTqMn_B_Rq. "Panda would allow it" was never the same question as "we understand it", and
  # the first version of this function only asked the first one.
  #
  # Refusing rather than zeroing, deliberately: zeroing would hand the car a frame Ford never sent,
  # mid-stop, which is its own new behaviour. Falling back to the authored command is a controller
  # we already ship.
  # AND THE ONE THAT ACTUALLY MATTERS, measured on drive A: the camera asserted `AccCancl_B_Rq` in
  # **70.6% of its frames** (21,090 of 29,890). It was not quietly computing ACC with the relay
  # open -- it spent most of the drive asking the car to CANCEL, and we relayed that request 219
  # times. Forwarding a cancel is not a degraded version of forwarding a command; it is actuation
  # in its own right, and the PCM's cruise status faulted 82 times over the drive.
  # AccStopStat_B_Rq WAS ON THIS LIST AND SHOULD NOT HAVE BEEN. Removed 2026-08-18 on measurement,
  # after it cost him the thing this feature exists to protect.
  #
  # It went on the list because drive A applied the park brake, and everything unpoliced went on at
  # once. That was guilt by association: the bit actually implicated was `AccBrkPrkEl_B_Rq`, which
  # STAYS refused. What the measurement says about this one specifically:
  #
  #   AccStopStat_B_Rq   asserted on 330 frames of route 388 and 10.5% of route 389 -- and it never
  #                      once co-occurs with `carState.parkingBrake` on any frame of either. It is
  #                      what Ford asserts while HOLDING a stop, which is why it only started
  #                      appearing now: before 389 this car had never held one.
  #
  # (Sampling note: the park-brake attribution itself is not re-litigated here. The scan behind this
  # change covered the first six segments of each route, which is enough to establish that
  # AccStopStat is ordinary stop-hold traffic and not enough to prove any bit NEVER fires. So the
  # rest of the list is left exactly as it was.)
  #
  # What the refusal DID do is hand every stop-in-traffic to openpilot
  # longitudinal -- 10.5% of drive 389 -- because `AccStopStat_B_Rq` is exactly what Ford asserts
  # while holding a stop. That is the single case where Ford's stop-and-go is most valuable and
  # openpilot's is least trusted, and he saw it directly: the OP LONG pill coming on while stopped
  # behind a car, "where Ford ACC does just fine".
  #
  # The others stay. They cost nothing observed -- AccBrkPrkEl_B_Rq has never fired, and a camera
  # cancel is actuation in its own right (drive A relayed 219 of them). Refusing a bit that never
  # appears is free; refusing one Ford uses on every stop is not.
  unpoliced = ["AccCancl_B_Rq", "AccDeny_B_Rq", "AccBrkPrkEl_B_Rq",
               "AccBrkPulse_B_Rq", "AccAutoResum_D_Rq"]
  # `allow_cancel` drops ONE bit from that list, for the recovery path only. It is NOT a relaxation
  # of drive A's finding -- relaying a cancel the camera raised on its own is still what caused the
  # 70 re-engagement cycles. It applies solely where the override provoked the cancel and the
  # refusal has become self-sustaining. AccDeny and CmbbDeny are never dropped: "I will not let ACC
  # run" is a different statement from "stop the ACC that is running".
  if allow_cancel:
    unpoliced.remove("AccCancl_B_Rq")
  for name in unpoliced:
    if stock_values.get(name):
      return "camera asserted %s -- unpoliced actuation, see drive A" % name

  # THE BOTTOM ONLY. The top is clamped in `create_acc_msg_passthrough` -- see the asymmetry
  # argument there. Refusing the top threw away Ford's whole frame on every pull-away, which is
  # the "it switched to op long and went ridiculously slow" report a second time, on a second
  # field, after the first was fixed.
  accel = float(stock_values.get("AccBrkTot_A_Rq", 0.0))
  if accel < (_PANDA_ACCEL_MIN + _PANDA_MARGIN):
    return "AccBrkTot_A_Rq %.3f below panda's band" % accel

  # AccPrpl_A_Pred is NOT checked here: create_acc_msg_passthrough pins it to the inactive value,
  # so Ford's number never reaches the wire and cannot make panda drop the frame.
  # AccPrpl_A_Rq is checked at the BOTTOM ONLY. `create_acc_msg_passthrough` clamps the top, because
  # Ford's ordinary launch propulsion is 2.0 m/s^2 -- exactly panda's ceiling -- and refusing there
  # threw away the entire frame on every pull-away (624 of 994 fallback frames on route 00000393,
  # all under 15 mph). Clamping down asks for less acceleration than Ford wanted, which is safe.
  #
  # THE BOTTOM IS NO LONGER A REFUSAL, as of 2026-08-24. `create_acc_msg_passthrough` clamps it, and
  # the note there carries the measurement: the fallback this refusal handed the car to applied up
  # to -1.16 m/s^2 while Ford was asking -0.13, for 0.28 s, eleven times on route 000003bc alone.
  # "Falling back to a controller we already ship" was the honest answer only while nobody had
  # checked what that controller does when it arrives wound up.
  return ""


def create_acc_ui_msg(packer, CAN: CanBus, CP, main_on: bool, enabled: bool, fcw_alert: bool,
                      standstill: bool, hud_control, stock_values: dict, send_hands_free_msg: bool,
                      send_ui: bool, send_bars: bool, tja_warn: int, tja_msg: int,
                      gap_is_fords: bool = False):
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
    })
    # THE GAP ON THE DASH MUST BE THE GAP THAT IS DRIVING THE CAR.
    #
    # `AccTGap_D_Dsply` is already passed through from the camera at the top of this function, and
    # this line used to overwrite it with `hud_control.leadDistanceBars` -- openpilot's PERSONALITY,
    # which has three states drawn on a five-state indicator.
    #
    # Under normal op long that is right: openpilot is the follow controller, so its personality IS
    # the gap. Under the stock-ACC passthrough it is wrong, and measurably so. Drive B, 2026-08-18:
    # seven physical presses, seven camera gap changes at the same timestamps, the camera cycling
    # 4-3-2-1 through Ford's five settings -- while the dash drew 3-2-1. **His button was working
    # the whole time and the display was showing something else**, which is why it read as an
    # aggressiveness control that did nothing.
    if not gap_is_fords:
      values["AccTGap_D_Dsply"] = hud_control.leadDistanceBars

  # Forward FCW alert from IPMA
  if fcw_alert:
    values["FcwVisblWarn_B_Rq"] = 1  # FCW visible alert
    values["FcwAudioWarn_B_Rq"] = 1  # FCW audio alert

  return packer.make_can_msg("ACCDATA_3", CAN.main, values)


def create_lkas_ui_msg(packer, CAN: CanBus, main_on: bool, enabled: bool, hands: int,
                       hud_control, stock_values: dict):
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

  lines = 0

  if hud_control is not None:
    # Determine left lane status independently
    if hud_control.leftLaneDepart:
      left_status = 4   # Intervene (Yellow)
    elif hud_control.leftLaneVisible:
      left_status = 1   # Available
    else:
      left_status = 2   # Suppress

    # Determine right lane status independently
    if hud_control.rightLaneDepart:
      right_status = 20  # Intervene (Yellow)
    elif hud_control.rightLaneVisible:
      right_status = 5   # Available
    else:
      right_status = 10  # Suppress

    # Combine left and right lane status
    lines = left_status + right_status

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
                      tja_toggle=False, icbm_button=None):
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

  return packer.make_can_msg("Steering_Data_FD1", bus, values)


def create_apim_gps_nav2_msg(packer, CAN: CanBus, gps):
  """FusionPilot: synthesize APIMGPS_Data_Nav_2_FD1 (0x463) toward the IPMA.

  The APIM on this car transmits 0x462 (position) but never 0x463 or 0x464, which
  is what the camera reports as U0253 "Missing Message". See apim_gps.py for the
  measurement. Sent on the CAMERA bus, because that is the side the IPMA sits on
  once openpilot has the relay; the vehicle side is unaffected.

  1Hz, matching the observed rate of the 0x462 the APIM does send.
  """
  return packer.make_can_msg("APIMGPS_Data_Nav_2_FD1", CAN.camera, apim_gps.nav2_signals(gps))


def create_apim_gps_nav3_msg(packer, CAN: CanBus, gps):
  """FusionPilot: synthesize APIMGPS_Data_Nav_3_FD1 (0x464) toward the IPMA.

  See create_apim_gps_nav2_msg. 1Hz.
  """
  return packer.make_can_msg("APIMGPS_Data_Nav_3_FD1", CAN.camera, apim_gps.nav3_signals(gps))
