import math
import numpy as np
from opendbc.can import CANPacker
from opendbc.car import ACCELERATION_DUE_TO_GRAVITY, Bus, DT_CTRL, apply_hysteresis, structs
from opendbc.car.lateral import AVERAGE_ROAD_ROLL, ISO_LATERAL_ACCEL, apply_std_steer_angle_limits
from opendbc.car.ford import fordcan
from opendbc.car.ford.values import CarControllerParams, FordFlags, CAR
from opendbc.car.interfaces import CarControllerBase, V_CRUISE_MAX
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

# BluePilot: extension imports for lateral, longitudinal, and HUD control
from opendbc.sunnypilot.car.ford.lateral_curv_ext import LateralCurvExt, PrimaryLateralControl
from opendbc.sunnypilot.car.ford.lateral_angle_ext import LateralAngleExt
from opendbc.sunnypilot.car.ford.blinker_test_ext import BlinkerTestExt
from opendbc.sunnypilot.car.ford.passing_assist_blinker import PassingAssistBlinker
from opendbc.sunnypilot.car.ford.lane_display_test_ext import LaneDisplayTestExt
from opendbc.sunnypilot.car.ford.longitudinal_ext import LongitudinalExt
from opendbc.sunnypilot.car.ford.hud_ext import HudExt
# OVERRIDE_HZ, not a literal. `update` runs inside the ACC_CONTROL_STEP block, so the rate is 50 Hz
# and not the 100 Hz control rate -- a factor of two that has already hidden in this file's bounds
# once. stop_override.py's own note: "DERIVED, never restate it."
from opendbc.sunnypilot.car.ford.stop_override import OVERRIDE_HZ

# THE CANCEL-RECOVERY BOUNDS, in seconds, with the frame counts DERIVED. Never restate a frame
# count here: `update` runs inside the ACC_CONTROL_STEP block at 50 Hz, not the 100 Hz control
# rate, and that factor of two already hid in this feature's sibling bound once.
_CANCEL_INERT_S = 5.0                                            # cancel held this long = deadlock
_CANCEL_INERT_FRAMES = int(_CANCEL_INERT_S * OVERRIDE_HZ)
_CANCEL_RECOVERY_MAX_S = 30.0                                    # then stop pretending it will let go
_CANCEL_RECOVERY_MAX_FRAMES = int(_CANCEL_RECOVERY_MAX_S * OVERRIDE_HZ)
# How soon after the override lets go a new cancel run still counts as ITS cancel. The measured lag
# is 1.6 s and the run begins on the first frame after the override ends, so this is generous
# already -- it exists to make a cancel raised much later unmistakably the camera's own.
_CANCEL_ATTRIBUTION_S = 3.0
_CANCEL_ATTRIBUTION_FRAMES = int(_CANCEL_ATTRIBUTION_S * OVERRIDE_HZ)
from opendbc.sunnypilot.car.ford import fordcan_ext
from opendbc.sunnypilot.car.ford.icbm import IntelligentCruiseButtonManagementInterface
from opendbc.sunnypilot.car.ford.gap_control import FordGapController
from opendbc.sunnypilot.car.ford.stop_override import FordStopOverride

# FusionPilot: the deepest deceleration we will request while the car is ALREADY STOPPED, m/s^2.
# Ford's own standstill requests span -0.25 to +0.47 across 7,168 measured frames; this is twice its
# deepest, so it never binds where Ford would have asked for more. See the block that applies it.
_STANDSTILL_ACCEL_FLOOR = -0.5

LongCtrlState = structs.CarControl.Actuators.LongControlState
VisualAlert = structs.CarControl.HUDControl.VisualAlert

# CAN FD limits:
# Limit to average banked road since safety doesn't have the roll, higher actual roll lowers lateral acceleration
MAX_LATERAL_ACCEL = ISO_LATERAL_ACCEL - (ACCELERATION_DUE_TO_GRAVITY * AVERAGE_ROAD_ROLL)  # ~2.4 m/s^2


def anti_overshoot(apply_curvature, apply_curvature_last, v_ego):
  diff = 0.1
  tau = 5  # 5s smooths over the overshoot
  dt = DT_CTRL * CarControllerParams.STEER_STEP
  alpha = 1 - np.exp(-dt / tau)

  lataccel = apply_curvature * (v_ego ** 2)
  last_lataccel = apply_curvature_last * (v_ego ** 2)
  last_lataccel = apply_hysteresis(lataccel, last_lataccel, diff)
  last_lataccel = alpha * lataccel + (1 - alpha) * last_lataccel

  output_curvature = last_lataccel / (max(v_ego, 1) ** 2)

  return float(np.interp(v_ego, [5, 10], [apply_curvature, output_curvature]))


def apply_ford_curvature_limits(apply_curvature, apply_curvature_last, current_curvature, v_ego_raw, steering_angle, lat_active, CP):
  # No blending at low speed due to lack of torque wind-up and inaccurate current curvature
  if v_ego_raw > 9:
    apply_curvature = np.clip(apply_curvature, current_curvature - CarControllerParams.CURVATURE_ERROR,
                              current_curvature + CarControllerParams.CURVATURE_ERROR)

  # Curvature rate limit after driver torque limit
  apply_curvature = apply_std_steer_angle_limits(apply_curvature, apply_curvature_last, v_ego_raw, steering_angle, lat_active, CarControllerParams.ANGLE_LIMITS)

  # Ford Q4/CAN FD has more torque available compared to Q3/CAN so we limit it based on lateral acceleration.
  # Safety is not aware of the road roll so we subtract a conservative amount at all times
  if CP.flags & FordFlags.CANFD:
    # Limit curvature to conservative max lateral acceleration
    curvature_accel_limit = MAX_LATERAL_ACCEL / (max(v_ego_raw, 1) ** 2)
    apply_curvature = float(np.clip(apply_curvature, -curvature_accel_limit, curvature_accel_limit))

  return apply_curvature


def apply_creep_compensation(accel: float, v_ego: float) -> float:
  creep_accel = np.interp(v_ego, [1., 3.], [0.6, 0.])
  creep_accel = np.interp(accel, [0., 0.2], [creep_accel, 0.])
  accel -= creep_accel
  return float(accel)


# BluePilot: CarController inherits from LateralCurvExt, LateralAngleExt, LongitudinalExt, HudExt,
# and ICBM for 4-signal lateral control (curvature- or angle-primary), follow-aware longitudinal,
# and enhanced HUD messaging.
# Init order: CarControllerBase first (sets self.CP, self.frame), then ext classes.
class CarController(CarControllerBase, LateralCurvExt, LateralAngleExt, LongitudinalExt, HudExt,
                    BlinkerTestExt, LaneDisplayTestExt, IntelligentCruiseButtonManagementInterface):
  def __init__(self, dbc_names, CP, CP_SP):
    CarControllerBase.__init__(self, dbc_names, CP, CP_SP)
    # BluePilot: initialize extension classes
    LateralCurvExt.__init__(self, CP, CP_SP)
    LateralAngleExt.__init__(self, CP, CP_SP)
    LongitudinalExt.__init__(self, CP, CP_SP)
    HudExt.__init__(self, CP, CP_SP)
    BlinkerTestExt.__init__(self)
    # FusionPilot: the same blink engine, driven by the planner instead of a button.
    self.pa_blinker = PassingAssistBlinker()
    LaneDisplayTestExt.__init__(self)
    # ICBM: base class sets state used at runtime, init for robustness
    # IntelligentCruiseButtonManagementInterface.__init__(self, CP, CP_SP)

    self.params = Params()
    self.packer = CANPacker(dbc_names[Bus.pt])
    self.CAN = fordcan.CanBus(CP)

    self.apply_curvature_last = 0
    self.anti_overshoot_curvature_last = 0
    self.disable_BP_lat_UI = False
    self.accel = 0.0
    self.gas = 0.0
    self.last_button_frame = 0  # BluePilot: ICBM button press tracking
    # BluePilot: ICBM gap controller state. It lives HERE, on the CarController, because
    # IntelligentCruiseButtonManagementInterface.update is called class-style two lines below with
    # `self` = this CarController -- its own __init__ is commented out above and never runs. An
    # __init__ added to that class is dead code; attributes it sets do not exist at runtime.
    # Getting this wrong took the car off the road on 2026-08-15 with an AttributeError inside
    # card's control loop.
    self.icbm_gap = FordGapController()
    self.icbm_gap_failed = False
    # FusionPilot: synthesize the two APIM GPS messages the IPMA never receives, so it can leave
    # NoNavDataAvailable and enter Fusion mode -- the state in which it actually reads signs. Read
    # once at init like the passthrough above; this changes what a module on the bus is fed and is
    # not something to toggle mid-drive. Latched off on ANY failure, and the getattr default in
    # the send path is True so a missing attribute disables the feature rather than the car.
    self.apim_gps_enabled = self.params.get_bool("FordSynthesizeApimGps")
    self.apim_gps_failed = False
    # FusionPilot: stock ACC passthrough -- forward the camera's own ACCDATA under op long rather
    # than authoring our own. Read once at init: this decides which controller drives the car, and
    # swapping it mid-drive would hand over between two different longitudinal behaviors with no
    # transition. Same reasoning as mapd_manager reading MapdV2 once.
    self.stock_acc_passthrough = self.params.get_bool("StockAccPassthrough")
    # Last reason the forwarded frame was refused, so the log carries transitions and not 50 Hz of
    # the same line. Lives here for the same reason everything else does -- see the note above.
    self.passthrough_reason_last = "?"
    self.passthrough_cancel_frames = 0
    # FusionPilot 2026-08-22: the cancel-recovery state. Attribution is what decides whether a
    # cancel is OURS to mask, and it is a distance in frames from the override letting go rather
    # than a per-drive bool -- a bool meant a cancel the camera raised on its own an hour later was
    # still treated as ours. Starts effectively infinite so nothing is attributable before the
    # override has ever run.
    self.frames_since_override = 1 << 30
    self.override_last_frame = False
    self.icbm_blind_said = False
    self.cancel_is_ours = False
    self.cancel_recovery_frames = 0
    self.cancel_recovery_said = False
    # FusionPilot: the stop override -- the last few mph the set speed cannot ask for. Same
    # placement reasoning as icbm_gap above; and same latch-off-on-exception discipline, because an
    # exception here reaches card and stops the car.
    self.stop_override = FordStopOverride()
    self.stop_override_enabled = self.params.get_bool("StockAccStopOverride")
    # Read once at init like every other toggle here -- see the note at the top of __init__ on why
    # per-drive state lives on the Ford CarController rather than on the mixin classes.
    self.stop_auto_resume_enabled = self.params.get_bool("StockAccStopAutoResume")
    self.stop_override_failed = False
    self.stop_override_last = False
    # Latched when the override brought the car to a stop, and cleared once it is moving again.
    # `resume_allowed` reads it: a stop WE authored is not resumed from automatically.
    self.stop_override_stopped_us = False
    # WHO IS AUTHORING ACCDATA, for the screen. Set every ACCDATA frame from the decision that was
    # actually taken rather than inferred downstream from the numbers -- see the AccAuthority
    # comment in custom.capnp for why the inference could not tell `opStop` from `inert`.
    self.acc_authority = structs.ControllerStateBP.AccAuthority.stock
    # Note: main_on_last, lkas_enabled_last, steer_alert_last, lead_distance_bars_last,
    # distance_bar_frame are initialized by HudExt.__init__() above

  def _urgent_speed_gap(self, CS) -> float:
    """FusionPilot: how far below the current speed something URGENT wants us, m/s. 0.0 if nothing.

    His framing, 2026-08-20, and it is the right generalisation: *"If a red light, stop sign, or
    unconfirmed lead can't be reached by ICBM fast enough, we need to switch to OP long sooner."*
    The stalk closes a gap at 3.3 mph/s and does not care what opened it.

    TWO SOURCES QUALIFY, and the exclusions matter as much as the inclusions:

      SCC-Vision / SCC-Map   a corner. Physics, and it arrives on a schedule the road sets.
      an unconfirmed lead    a stopped car the radar cannot see. Measured on his drives: episodes
                             at 48.6 and 52.4 mph opened gaps of 28 and 32 mph, which the stalk
                             needs 8-10 s to close. That is the near-miss he reported -- *"it didn't
                             seem to set my speed down that much at all"* -- and it was never a
                             detection failure.

      NOT a speed limit. A limit change is not urgent: nothing is arriving, ICBM walking the number
      down over a few seconds is exactly right, and taking the command for it would contradict the
      camera several times a drive for no gain.
      NOT a radar lead either. That is Ford's stop-and-go, which is better than ours, and the
      override refuses one anyway.

    Never raises: a missing or invalid plan reads as no gap.
    """
    try:
      if not (self.sm.alive.get('longitudinalPlanSP') and self.sm.valid.get('longitudinalPlanSP')):
        return 0.0
      lp = self.sm['longitudinalPlanSP']
      v_ego = float(CS.out.vEgo)
      gap = 0.0
      if str(lp.longitudinalPlanSource) in ("sccVision", "sccMap"):
        gap = max(gap, v_ego - float(lp.vTarget))
      ul = lp.unconfirmedLead
      if str(ul.state) in ("active", "tracking"):
        target = float(ul.vTarget)
        # A zero target means "no request", not "stop immediately" -- the same trap the endpoint
        # gate documents. Reading it as a target would make every idle frame a maximum gap.
        if target > 0.0:
          gap = max(gap, v_ego - target)
      return max(0.0, gap)
    except Exception:  # noqa: BLE001 -- see docstring
      return 0.0

  def update(self, CC, CC_SP, CS, now_nanos):
    can_sends = []

    # BluePilot: update SubMaster (modelV2, liveParameters, selfdriveState, radarState) and vehicle model
    LateralCurvExt.update_sm(self)

    # BluePilot: read runtime params from UI
    LateralCurvExt.update_lateral_params(self, self.params)
    LateralAngleExt.update_angle_params(self, self.params)
    self.disable_BP_lat_UI = self.params.get_bool("disable_BP_lat_UI")
    LongitudinalExt.update_long_params(self, self.params)
    HudExt.update_hud_params(self, self.params, self.CP)

    actuators = CC.actuators
    hud_control = CC.hudControl
    main_on = CS.out.cruiseState.available
    steer_alert = hud_control.visualAlert in (VisualAlert.steerRequired, VisualAlert.ldw)
    fcw_alert = hud_control.visualAlert == VisualAlert.fcw

    # BluePilot: compute DM state (TJA message, warning, hands level)
    HudExt.update_dm(self, hud_control, main_on, CS.out.cruiseState.standstill, self.frame)

    # BluePilot: stationary cluster lane-display walk. None on every normal frame.
    lane_test = LaneDisplayTestExt.update_lane_display_test(self, CS)

    # BluePilot: stationary turn-signal actuation test, and THE ONLY THING IN THIS FORK THAT EVER
    # COMMANDS THE TURN SIGNAL. Returns SIGNAL_NONE on every normal frame, in which case
    # create_button_msg keeps passing the driver's own switch position through untouched. Only an
    # explicitly requested, standstill-gated pulse returns anything else -- so nothing openpilot
    # does on the road, a lane change or a revert included, moves his blinker.
    turn_signal = BlinkerTestExt.update_blinker_test(self, CS)
    # FusionPilot: and the passing-assist lane change, which is the OTHER thing that commands it.
    # Only when the bench test wants nothing, so the two can never contend for the switch -- the
    # test is standstill-only and a lane change is not, so in practice they never overlap anyway.
    # Returns SIGNAL_NONE unless the planner published actuating AND blinkerWouldBeOn; see
    # passing_assist_blinker.py, and `actuating` in custom.capnp for why that bit has to exist.
    if not turn_signal:
      turn_signal = self.pa_blinker.update(self.sm, getattr(CS, 'steering_data_ts', 0))
    # update_blinker_test rate-limits itself to BUTTONS_STEP -- see its docstring. The rate lives
    # there rather than here because this file cannot be tested offline, and sending this frame too
    # fast is precisely the bug that reached the car.
    if turn_signal:
      can_sends.append(fordcan_ext.create_button_msg(self.packer, self.CAN.main, CS.buttons_stock_values,
                                                     turn_signal=turn_signal))

    ### acc buttons ###
    if CC.cruiseControl.cancel:
      can_sends.append(fordcan.create_button_msg(self.packer, self.CAN.camera, CS.buttons_stock_values, cancel=True))
      can_sends.append(fordcan.create_button_msg(self.packer, self.CAN.main, CS.buttons_stock_values, cancel=True))
    elif CC.cruiseControl.resume and (self.frame % CarControllerParams.BUTTONS_STEP) == 0:
      # BluePilot: the branch is still taken when the gate blocks, so the elif chain below behaves
      # exactly as before -- only the button message is withheld. See LongitudinalExt.resume_allowed.
      if LongitudinalExt.resume_allowed(self, self.sm):
        can_sends.append(fordcan.create_button_msg(self.packer, self.CAN.camera, CS.buttons_stock_values, resume=True))
        can_sends.append(fordcan.create_button_msg(self.packer, self.CAN.main, CS.buttons_stock_values, resume=True))
    # if stock lane centering isn't off, send a button press to toggle it off
    # the stock system checks for steering pressed, and eventually disengages cruise control
    elif CS.acc_tja_status_stock_values["Tja_D_Stat"] != 0 and (self.frame % CarControllerParams.ACC_UI_STEP) == 0:
      can_sends.append(fordcan.create_button_msg(self.packer, self.CAN.camera, CS.buttons_stock_values, tja_toggle=True))

    # BluePilot: Intelligent Cruise Button Management (ICBM)
    #
    # FusionPilot 2026-08-23: NOT WHILE OPENPILOT IS AUTHORING THE ACC COMMAND. His question, and
    # he had the right instinct -- "when OP long is driving, we shouldn't affect its speed with
    # ICBM?" Measured on route 000003ae, over the 60 s the passthrough spent `inert`:
    #
    #     inert  6012 frames   378 ICBM button frames   84 mph of dash travel
    #                          decrease=210, increase=168
    #
    # That is his "the speed went up and down". ICBM was hunting a set speed that governed NOTHING,
    # because a latched camera means openpilot authors every ACCDATA frame, and the number the
    # buttons move is not in that loop at all.
    #
    # `_op_long_drives()` cannot catch this: it decides whether ICBM runs ONCE, at car init, and
    # under the passthrough it correctly answers "Ford drives, so ICBM stays". Authority is a
    # per-frame fact and it changed hours into the drive.
    #
    # ONLY `inert` AND `openpilot`, deliberately. `fallback` is a scattered per-frame refusal that
    # Ford resumes from on the next frame -- suppressing there would make the buttons stutter every
    # time a band clipped. `inert` is five straight seconds of cancel, which means the passthrough
    # is finished for this drive. `opStop` is NOT suppressed either: the override raising the set
    # speed while stopped is deliberate, so Ford does not lurch back to 20 when it resumes.
    #
    # Suppressing TRANSMISSION rather than telling ICBM to stand down, because ICBM lives in
    # selfdrived across a capnp boundary and the carcontroller is where authority is known. It
    # keeps wanting; nothing actuates. It already tolerates a press that never moves the cluster
    # -- that is what PRESS_SETTLE_MAX_FRAMES is for.
    _blind = (self.acc_authority in (structs.ControllerStateBP.AccAuthority.inert,
                                     structs.ControllerStateBP.AccAuthority.openpilot))
    #
    # SUPPRESSED, NOT SKIPPED. The first version of this returned without calling `update` at all,
    # which also skips `_update_gap` -- and that method's own comment records the same mistake being
    # made and fixed once already: a gap lease that stops being asserted does not pause, it lands
    # its remainder as a SECOND press. So the flag goes in and the follow-gap keeps running.
    if _blind and not self.icbm_blind_said:
      self.icbm_blind_said = True
      cloudlog.warning("ICBM set-speed buttons suppressed: openpilot is authoring ACCDATA, so the "
                       "set speed drives nothing. The follow-gap is unaffected.")
    elif not _blind:
      self.icbm_blind_said = False
    icbm_can_sends, self.last_button_frame = IntelligentCruiseButtonManagementInterface.update(
      self, CC_SP, CS, self.packer, self.CAN, self.frame, self.last_button_frame,
      suppress_set_speed=_blind
    )
    can_sends.extend(icbm_can_sends)

    ### lateral control ###
    # BluePilot: keep stock lateral path in carcontroller, and run BP 4-signal lateral
    # only when bypass is disabled.
    if (self.frame % CarControllerParams.STEER_STEP) == 0:
      current_curvature = -CS.out.yawRate / max(CS.out.vEgoRaw, 0.1)
      # BluePilot: bypass flag is owned by stock carcontroller path.
      bypass_bp_lat = self.disable_BP_lat_UI
      if bypass_bp_lat:
        # Stock curvature-only path only. Anti-overshoot is not used when BP lateral is active (disable_BP_lat_UI off).
        if self.CP.carFingerprint in (CAR.FORD_BRONCO_SPORT_MK1, CAR.FORD_F_150_MK14):
          self.anti_overshoot_curvature_last = anti_overshoot(actuators.curvature, self.anti_overshoot_curvature_last, CS.out.vEgoRaw)
          apply_curvature = self.anti_overshoot_curvature_last
        else:
          apply_curvature = actuators.curvature

        self.apply_curvature_last = apply_ford_curvature_limits(
          apply_curvature, self.apply_curvature_last, current_curvature,
          CS.out.vEgoRaw, 0., CC.latActive, self.CP)
        if self.CP.flags & FordFlags.CANFD:
          mode = 1 if CC.latActive else 0
          counter = (self.frame // CarControllerParams.STEER_STEP) % 0x10
          can_sends.append(fordcan.create_lat_ctl2_msg(self.packer, self.CAN, mode, 0., 0., -self.apply_curvature_last, 0., counter))
        else:
          can_sends.append(fordcan.create_lat_ctl_msg(self.packer, self.CAN, CC.latActive, 0., 0., -self.apply_curvature_last, 0.))
      else:
        # BluePilot: select the BP lateral strategy by primary control variable.
        #   1 (angle)     -> LateralAngleExt: κ → path_angle (c1), apply_curvature held at 0.
        #   0 (curvature) -> LateralCurvExt: full 4-signal curvature-primary (default).
        # Both return a LateralResult packed identically below.
        # Do not run apply_ford_curvature_limits here or overwrite apply_curvature_last before the
        # strategy runs. Panda rate-checks desired_curvature vs the last TX on the bus; that must match
        # the prior frame's lat.apply_curvature only (not an intermediate stock-limited value).
        if self.primary_lateral_control == PrimaryLateralControl.angle:
          lat = LateralAngleExt.update_angle_strategy(self, CC, CS, actuators, self.CP)
        else:
          lat = LateralCurvExt.update(self, CC, CS, actuators, self.apply_curvature_last, self.CP)
        self.apply_curvature_last = lat.apply_curvature
        self.lateralUncertainty = lat.lateralUncertainty
        # BluePilot: rate-limit diagnostics for controllerStateBP. update_angle_strategy sets these on
        # self (angle mode); curvature mode leaves them False (the path_angle ROC / sim aren't run there).
        _angle_mode = self.primary_lateral_control == PrimaryLateralControl.angle
        self.angleRateLimited = getattr(self, 'bp_angle_rate_limited', False) if _angle_mode else False
        self.curvatureRateLimited = getattr(self, 'bp_curvature_rate_limited', False) if _angle_mode else False
        # BluePilot: current-curvature deviation-clip diagnostic. Set by whichever strategy just ran
        # (both lateral_curv_ext.update and lateral_angle_ext.update_angle_strategy set this), so it's
        # meaningful in both modes -- not gated by _angle_mode like the two above.
        self.curvatureDeviationLimited = getattr(self, 'bp_curvature_deviation_limited', False)
        self.humanTurnLateralPaused = self.angle_human_turn_active if _angle_mode else False
        self.stallBlipActive = self.angle_stall_blip_active if _angle_mode else False

        # BluePilot: angle-mode human-turn override -- send lateral inactive (mode 0) while the
        # driver manually turns, so the PSCM releases cleanly instead of stalling 2-3 s on
        # re-engage (observed on Mach-E). Panda-clean: every ford.h check has a legitimate
        # !steer_control_enabled branch for the zeroed frames; on release, path_angle ramps back
        # from 0 through the soft ROC (no reset-bypass latch involvement). Curvature mode keeps
        # its own reset_steering path (zeroed signals with mode still active) in LateralCurvExt.
        # The stall blip (lateral_angle_ext.py) rides the same mode-0 path: a short pulse that
        # resets the PSCM's post-override attenuation when the deviation clip deadlocks hands-free.
        lat_active = CC.latActive and not (_angle_mode and (self.angle_human_turn_active or self.angle_stall_blip_active))
        if self.CP.flags & FordFlags.CANFD:
          mode = 1 if lat_active else 0
          counter = (self.frame // CarControllerParams.STEER_STEP) % 0x10
          can_sends.append(fordcan_ext.create_lat_ctl2_msg(
            self.packer, self.CAN, mode, lat.ramp_type, lat.precision_type,
            -lat.path_offset, -lat.path_angle, -lat.apply_curvature, -lat.curvature_rate, counter
          ))
        else:
          can_sends.append(fordcan_ext.create_lat_ctl_msg(
            self.packer, self.CAN, lat_active, lat.ramp_type, lat.precision_type,
            -lat.path_offset, -lat.path_angle, -lat.apply_curvature, -lat.curvature_rate
          ))

    # send lka msg at 33Hz
    if (self.frame % CarControllerParams.LKA_STEP) == 0:
      # BluePilot: tell ford.h whether angle mode is engaged, out-of-band from LMC/LMC2, packed into
      # Lane_Assist_Data1's unused bits (read synchronously in ford_tx_hook, no separate CAN ID/RX
      # needed). bypass_bp_lat means BP lateral is off entirely, so angle mode can't be engaged then.
      # shadow_curvature is only meaningful in angle mode (self.bp_kappa_cmd is stale/unused CurvExt
      # state otherwise, so force it to 0 there).
      # Negated to match the sign convention path_angle/apply_curvature use on the wire (see
      # -lat.path_angle/-lat.apply_curvature just above) -- ford.h's angle_meas (measured curvature,
      # from raw yaw rate with no negation) is calibrated against that wire convention, not
      # bp_kappa_cmd's internal one. Confirmed via safety_replay against a real route (2026-07-10):
      # un-negated, shadow_curvature and angle_meas were consistently opposite-signed, so the
      # deviation check found a "divergence" on every frame once speed crossed angle_error_min_speed.
      angle_mode_engaged = (not self.disable_BP_lat_UI) and (self.primary_lateral_control == PrimaryLateralControl.angle)
      shadow_curvature = -self.bp_kappa_cmd if angle_mode_engaged else 0.0
      can_sends.append(fordcan_ext.create_lka_msg(
        self.packer, self.CAN, CC.latActive, hud_control, angle_mode_engaged, shadow_curvature
      ))

    ### longitudinal control ###
    # send acc msg at 50Hz
    if self.CP.openpilotLongitudinalControl and (self.frame % CarControllerParams.ACC_CONTROL_STEP) == 0:
      # Stock creep compensation and rate limiting (upstream-identical)
      op_accel = actuators.accel
      op_gas = op_accel

      if CC.longActive:
        op_accel = apply_creep_compensation(op_accel, CS.out.vEgo)
        op_accel = max(op_accel, self.accel - (3.5 * CarControllerParams.ACC_CONTROL_STEP * DT_CTRL))

      op_accel = float(np.clip(op_accel, CarControllerParams.ACCEL_MIN, CarControllerParams.ACCEL_MAX))
      op_gas = float(np.clip(op_gas, CarControllerParams.ACCEL_MIN, CarControllerParams.ACCEL_MAX))

      if not CC.longActive or op_gas < CarControllerParams.MIN_GAS:
        op_gas = CarControllerParams.INACTIVE_GAS

      # Pitch compensation (upstream-identical)
      accel_due_to_pitch = 0.0
      if len(CC.orientationNED) == 3:
        accel_due_to_pitch = math.sin(CC.orientationNED[1]) * ACCELERATION_DUE_TO_GRAVITY

      # BluePilot: downhill compensation disable
      if self.disable_downhill_comp_UI:
        if accel_due_to_pitch < 0:
          accel_due_to_pitch = 0

      stopping = CC.actuators.longControlState == LongCtrlState.stopping
      target_speed = V_CRUISE_MAX
      v_ego_mph = CS.out.vEgo * 2.23694

      # BluePilot: longitudinal follow control via LongitudinalExt
      # Classifies lead vehicle state (gaining/pacing/trailing) and applies gas/accel limits,
      # rate-limited braking, and split brake/precharge hysteresis.
      lng = LongitudinalExt.update(self, CC, CS, op_accel, op_gas, accel_due_to_pitch,
                                    v_ego_mph, stopping, target_speed)

      # FusionPilot: STOCK ACC PASSTHROUGH. Forward the camera's own ACC command instead of ours.
      #
      # openpilot longitudinal on this car is not trusted -- his words, "I trust how Ford ACC
      # works" -- so rather than trying to match Ford's tuning, this forwards it. The camera is
      # still computing ACC (bus 0 is forwarded to it, so it has all its inputs); the relay only
      # stops its ACCDATA reaching the car. We put its own numbers back on the wire.
      #
      # THE FAIL-SAFE IS THE POINT OF THE `can_valid` CHECK. If the camera bus drops, forwarding a
      # stale command is the worst possible behavior -- it would hold whatever brake or throttle
      # request happened to be in flight. Falling back to our own computed ACCDATA means the car
      # keeps a real controller rather than a frozen frame.
      #
      # Note this path is only reachable under op long, so it is NOT an Icbm* feature: it is
      # meaningless when ICBM is the actuator, which is the naming test in CLAUDE.md.
      # AND THE SECOND FAIL-SAFE IS PANDA. `ford_tx_hook` does not clamp a value it dislikes, it
      # drops the entire message -- so a frame Ford is entitled to send but panda refuses would make
      # ACCDATA vanish intermittently, which is worse than either controller. `passthrough_admissible`
      # asks that question first and falls back to our own authored command when the answer is no.
      # It also covers `CC.longActive`: with openpilot longitudinal inactive panda passes only the
      # inactive frame, and forwarding Ford's would both be blocked AND leave Cmbb_B_Enbl asserted
      # after openpilot had disengaged.
      # FusionPilot: THE STOP OVERRIDE decides this BEFORE admissibility, because it is a decision
      # about whose command to send rather than about whether Ford's is carriable. It authors
      # nothing -- it selects the openpilot frame that `create_acc_msg` builds below, which already
      # clamps to panda's bands and never touches the unpoliced bits.
      override = False
      if self.stop_override_enabled and not self.stop_override_failed:
        try:
          lead_d = 0.0
          rs = self.sm['radarState'] if self.sm.alive.get('radarState') else None
          if rs is not None and rs.leadOne.status:
            lead_d = float(rs.leadOne.dRel)
          # `was_active` used to be read here, because the latch below was edge-triggered on the
          # override ENDING at a standstill. It holds through the standstill now, so there is no
          # such edge and the latch reads `holding` directly -- see below. Removed rather than left
          # assigned: a variable that survives the reason it existed is the next person's evidence
          # for a gate that is no longer there.
          override = self.stop_override.update(
            long_active=bool(CC.longActive),
            v_ego=float(CS.out.vEgo),
            # alive AND valid: alive only means a message arrived recently, valid is the planner
            # saying its own output is sound. Arming a stop off a plan plannerd has disowned is
            # exactly what the other consumers in this fork check for.
            has_slow_down=bool(self.sm['longitudinalPlanSP'].dec.hasSlowDown)
            if (self.sm.alive.get('longitudinalPlanSP') and self.sm.valid.get('longitudinalPlanSP'))
            else False,
            # The model's own stop point, metres. 0.0 means it has none -- `endpoint_x()` is inf
            # when the plan is not full length and inf is clamped to 0 on the wire. This ARMS the
            # override now, in place of `stopping`, which was measured to be a stopped-car state
            # and made the trigger circular. Same alive/valid guard as has_slow_down above: arming
            # a stop off a plan plannerd has disowned is exactly what that check exists for.
            stop_endpoint_m=float(self.sm['longitudinalPlanSP'].dec.slowDownEndpoint)
            if (self.sm.alive.get('longitudinalPlanSP') and self.sm.valid.get('longitudinalPlanSP'))
            else 0.0,
            lead_distance=lead_d,
            # THE URGENT SPEED GAP: how far below the current speed a corner or a radar-blind
            # stopped car wants us, and therefore how much the stalk has to close at 3.3 mph/s.
            # Zero unless one of those actually wants something, so this cannot fire for a speed
            # limit or an ordinary lead.
            #
            # NOT `actuators.accel`, which was the first attempt: under ICBM openpilot's
            # longitudinal controller is not driving, watches the car ignore it, and winds up to its
            # -3.5 floor for over 10% of engaged frames. See SLOWDOWN_ARM_GAP.
            slowdown_gap=self._urgent_speed_gap(CS),
            # WITHOUT THIS THE STOP PATH ARMS OFF THE MODEL AND BRAKES OFF AN MPC THAT NEVER
            # PLANNED THE STOP -- see the gate in `stop_override.update`. Defaults to FALSE when
            # the message is missing, which is the conservative direction here: refusing to arm
            # costs a stop he takes himself, arming without the plan costs him Ford ACC.
            experimental_mode=bool(self.sm['selfdriveState'].experimentalMode)
            if (self.sm.alive.get('selfdriveState') and self.sm.valid.get('selfdriveState'))
            else False,
          )
          # Latch that THIS stop was ours, so the resume gate knows not to pull away from it on the
          # model's say-so. Keyed on the override's OWN outcome rather than on a speed window: the
          # first version tested `override and vEgo < 0.5 m/s`, but the override ends at 0.2235 m/s,
          # so it depended on frames landing inside a 0.28 m/s sliver. It worked, by about seven
          # frames, and would have stopped working silently on a harder stop.
          #
          # EDGE-TRIGGERED, and that is the whole correctness of it. `last_result` is a string that
          # persists until the next arm or end, so testing it on its own re-latches on every later
          # stop for the rest of the drive -- including the queue-cleared open-road case the gate
          # is supposed to let through, where he would sit at a green light waiting for a resume
          # that never comes. The transition into "stopped" happens on exactly one frame.
          # KEYED ON `holding` SINCE 2026-08-20, and the rename is not cosmetic. The override used
          # to `_end("stopped")` the moment the car reached a standstill, so this edge existed. It
          # now HOLDS the car instead -- Ford does not hold a stop without a lead, which is the
          # creep he reported -- so that end never comes and this latch would never fire. openpilot
          # would then be free to pull away from a stop the override itself authored.
          #
          # Level, not edge, and deliberately: the gate has to hold for the WHOLE standstill, not
          # just the frame it began on. The edge-triggering that mattered was about `last_result`
          # being a string that persists and re-latches on unrelated later stops; `holding` is state
          # that is true only while this override actually has the car, and `_end` clears it.
          if self.stop_override.holding:
            self.stop_override_stopped_us = True

          if override != self.stop_override_last:
            self.stop_override_last = override
            cloudlog.warning("stop override %s: %s", "ON" if override else "off",
                             self.stop_override.last_result)
        # Deliberately broad. Nothing this decision can raise may reach card, because a raise here
        # kills the whole ACCDATA block and with it the passthrough -- see the gap path for the
        # same reasoning.
        except Exception:
          self.stop_override_failed = True
          override = False
          # Release the resume hold too. It lives inside the guard above, so a failure here would
          # otherwise freeze it ON and block openpilot's automatic resume for the rest of the drive
          # -- including the ordinary queue-cleared case that has nothing to do with this feature.
          # Latching a feature off must not latch a neighbouring one on.
          self.stop_override_stopped_us = False
          cloudlog.exception("stop override disabled for this drive")

      # Moving again clears the resume hold, outside the guard above so that a disabled or failed
      # override cannot leave it asserted.
      if float(CS.out.vEgo) > 1.5:
        self.stop_override_stopped_us = False

      # (helper defined on the class; see _urgent_speed_gap)
      use_passthrough = False
      clear_cancel = False
      # HOW LONG AGO THE OVERRIDE LET GO, which is what makes a cancel attributable to it. Replaces
      # a permanent `override_ran` bool: that latched for the whole drive, so a cancel raised for
      # the camera's OWN reasons forty minutes later was still treated as ours and masked.
      #
      # Counted rather than timed because everything else in this block is in frames, and held at 0
      # for the whole standstill hold -- the override is still `active` there, so a 45 s hold does
      # not age out its own cancel.
      # A CANCEL RUN THAT SPANS THE OVERRIDE MUST NOT DECIDE ATTRIBUTION. FusionPilot, 2026-08-23.
      #
      # Attribution is decided once, on the frame a cancel RUN opens -- `passthrough_cancel_frames
      # == 0`. The counter is only touched inside `if not override`, so a run that was already open
      # when the override began survives it untouched: the override ends, the counter is still
      # non-zero, the `== 0` test never fires, and `cancel_is_ours` keeps whatever it was set to
      # BEFORE the override -- which is False, because no override had run yet. Recovery is then
      # blocked for the rest of the drive by a decision made before the thing it is attributing.
      #
      # Zeroing it on the override edge makes the first cancel frame after we hand back open a new
      # run, which is the only run that means anything: the camera cancelling while WE are
      # authoring is expected and says nothing, and the question recovery asks is whether it is
      # still cancelling once Ford has the car back.
      #
      # NOT PROVEN TO BE WHAT BIT HIM on routes ae/af -- the 4.99 s gap measured there is what a
      # fresh run looks like, so attribution ought to have passed and something else declined.
      # `RECOVERY DECLINED` below is what will say which. This is fixed because it is wrong on its
      # own terms, not because it is the diagnosis.
      if override and not self.override_last_frame:
        self.passthrough_cancel_frames = 0
      self.override_last_frame = override
      self.frames_since_override = 0 if override else self.frames_since_override + 1
      if not override and self.stock_acc_passthrough and getattr(CS, "acc_cam_valid", False) and getattr(CS, "acc_stock_values", None):
        reason = fordcan_ext.passthrough_admissible(CS.acc_stock_values, CC.longActive)
        use_passthrough = not reason
        if reason != self.passthrough_reason_last:
          self.passthrough_reason_last = reason
          cloudlog.warning("stock ACC passthrough: %s", reason or "forwarding Ford's command")

        # A LATCHED CANCEL MUST NOT BE SILENT. On drive A the camera asserted cancel at t+229.43 and
        # never released it -- it was still asserting 262 s later at the end of the drive. From that
        # moment the passthrough is inert and openpilot longitudinal is driving, which is precisely
        # the controller this feature exists to avoid, and nothing on the screen or in the log said
        # so. Count it and say it once.
        if reason.startswith("camera asserted AccCancl"):
          # A NEW RUN IS WHERE ATTRIBUTION IS DECIDED, once, rather than re-asked every frame. The
          # override asserts cancel about 1.6 s in and holds it, so the first frame after the
          # override lets go is the frame this run starts on -- anything that starts later than
          # ATTRIBUTION_FRAMES after it belongs to the camera, not to us.
          if self.passthrough_cancel_frames == 0:
            self.cancel_is_ours = self.frames_since_override <= _CANCEL_ATTRIBUTION_FRAMES
          self.passthrough_cancel_frames += 1
          if self.passthrough_cancel_frames == _CANCEL_INERT_FRAMES:
            cloudlog.error("stock ACC passthrough INERT: camera has asked to cancel for 5 s "
                           "straight. openpilot longitudinal is driving from here.")

          # RECOVERY: GIVE THE CAMERA A WAY BACK IN, because refusing it is what makes the latch
          # permanent. 2026-08-22, from three real override episodes on routes a8/a9/aa.
          #
          # The camera cancels ~1.6 s after the override takes authority -- it watches the car
          # decelerate harder than it asked and gives up. That part is INHERENT: a stop needs 5-8 s
          # of contradiction and the camera tolerates about 1.5, so every override will provoke one.
          # What is NOT inherent is that it never releases. A cancel makes the frame inadmissible,
          # so Ford's command stops reaching the car, so the camera can never observe the car
          # obeying it again. He had to pull over and restart the ignition, twice.
          #
          # So once the deadlock is established (5 s) AND the override is what caused it, forward
          # Ford's frame again with the cancel bit cleared. Ford drives, the camera watches the car
          # do what it asked, and it gets the chance to release that it has never had.
          #
          # BOUNDED, because whether it releases is exactly the unknown. 30 s of trying; if the
          # camera is still asserting after that it is not going to release for this reason and we
          # stop pretending otherwise, which also keeps the drive readable afterwards.
          # NEVER WHILE WE ARE HOLDING A STOP WE AUTHORED. Found 2026-08-22 by tracing the
          # radar-never-acquires case, hours after the rest of this shipped.
          #
          # The override holds a standstill for up to MAX_HOLD_S and then ends. If the car is
          # stopped behind a vehicle Ford's radar never returned -- which is the entire premise of
          # `unconfirmed_lead.py` -- handing Ford back there means forwarding an ACC command with
          # no lead in it and a set speed of 20. The camera's own `AccBrkTot_A_Rq` measured **+0.05
          # m/s^2** in the seconds after the stop on route a8: not braking. The car would pull away
          # into the stopped vehicle.
          #
          # `stop_override_stopped_us` is exactly the right flag: it is true while a standstill WE
          # authored is still unresumed, and it is cleared by the car moving above 1.5 mph. So the
          # recovery resumes being possible the moment he drives away, which is when Ford having
          # the car is what he wants.
          # AND SAY WHY WHEN IT DOES NOT ACT. FusionPilot, 2026-08-23.
          #
          # Routes ae and af: the override fired, the camera latched, INERT logged four times,
          # and RECOVERY logged ZERO. Three of the gates were then ruled out from the routes --
          # attribution by a measured 4.99 s gap between the last opStop frame and the first
          # inert one, `CC.longActive` because `inert` is unreachable without it, and the panda
          # bands by replaying them over all 8,750 camera frames of the two inert windows, which
          # refused none. That left two gates and NO WAY TO TELL WHICH, because not one of them
          # is published or logged.
          #
          # Third time today that a rule could not be explained from a drive. Fixing the rule is
          # guesswork until the drive can say which term declined, so this says it -- once per
          # cancel run, naming the gate, at the moment the recovery would otherwise have started.
          elif (self.passthrough_cancel_frames == _CANCEL_INERT_FRAMES + 1
                and not (self.cancel_is_ours and CC.longActive
                         and not self.stop_override_stopped_us
                         and self.cancel_recovery_frames < _CANCEL_RECOVERY_MAX_FRAMES)):
            cloudlog.error("stock ACC passthrough RECOVERY DECLINED: cancel_is_ours=%s "
                           "longActive=%s stop_override_stopped_us=%s recovery_frames=%d/%d",
                           self.cancel_is_ours, CC.longActive, self.stop_override_stopped_us,
                           self.cancel_recovery_frames, _CANCEL_RECOVERY_MAX_FRAMES)

          elif (self.cancel_is_ours and CC.longActive
                and self.passthrough_cancel_frames > _CANCEL_INERT_FRAMES
                and not self.stop_override_stopped_us
                and self.cancel_recovery_frames < _CANCEL_RECOVERY_MAX_FRAMES):
            # WRAPPED, because everything else that touches `acc_stock_values` in this method is.
            # An exception here does not disable a feature, it propagates out of `update`, through
            # card, and stops the car -- the 2026-08-15 failure, in a block that runs 50 times a
            # second. Failing closed means no recovery, which costs him a hand-back and nothing else.
            try:
              if not fordcan_ext.passthrough_admissible(CS.acc_stock_values, CC.longActive,
                                                        allow_cancel=True):
                self.cancel_recovery_frames += 1
                use_passthrough = True
                clear_cancel = True
                if not self.cancel_recovery_said:
                  self.cancel_recovery_said = True
                  cloudlog.error("stock ACC passthrough RECOVERY: the stop override provoked this "
                                 "cancel, so Ford's command is being forwarded with AccCancl_B_Rq "
                                 "cleared. Watching for the camera to release.")
                if self.cancel_recovery_frames == _CANCEL_RECOVERY_MAX_FRAMES:
                  # SAY SO WHEN IT GIVES UP. Without this the bound is silent, `cancel_recovery_said`
                  # stays latched, and a second genuine attempt later in the drive logs nothing at
                  # all -- which reads in the route as "the recovery never triggered".
                  cloudlog.error("stock ACC passthrough RECOVERY GAVE UP: %.0f s of forwarding and "
                                 "the camera is still asking to cancel.", _CANCEL_RECOVERY_MAX_S)
            except Exception:
              self.cancel_is_ours = False
              use_passthrough = False
              clear_cancel = False
              cloudlog.exception("stock ACC cancel recovery disabled for this cancel")
        else:
          # ONLY AN EMPTY REASON MEANS THE CAMERA LET GO. Review finding, 2026-08-22, and it broke
          # the one instrument this whole experiment has.
          #
          # `passthrough_admissible` returns "openpilot longitudinal inactive" and "camera set
          # CmbbDeny_B_Actl" BEFORE it ever looks at the cancel bit. So this branch is reached on an
          # ordinary disengagement, and the old code logged RECOVERY WORKED there -- claiming the
          # camera had released a cancel that was still asserted, every time he lifted off cruise.
          # It also zeroed both counters, so re-engaging handed out a fresh 30 s window and the
          # bound could be walked past indefinitely by braking at each light.
          if not reason and self.cancel_recovery_frames:
            cloudlog.error("stock ACC passthrough RECOVERY WORKED: camera released its cancel "
                           "after %.1f s of forwarding.",
                           self.cancel_recovery_frames / OVERRIDE_HZ)
            self.cancel_recovery_frames = 0
            self.cancel_recovery_said = False
          # The cancel RUN still breaks on any other refusal -- it only ever meant "consecutive
          # frames of cancel", and this branch is every reason that is not one.
          self.passthrough_cancel_frames = 0

      # WHO IS AUTHORING, decided here where the decision actually happens. Order matters: `inert`
      # outranks `fallback` because they look identical frame-to-frame and only the duration tells
      # them apart -- a scattered non-carriable frame is ordinary (8.9% of drive B) while five
      # straight seconds of cancel means the passthrough is finished for the drive.
      _AA = structs.ControllerStateBP.AccAuthority
      if override:
        self.acc_authority = _AA.opStop
      elif use_passthrough:
        # `recovery` OUTRANKS `ford` here even though the numbers on the wire are Ford's, because a
        # suppressed actuation bit is a different state and every offline tool scores this field.
        # Folding it into `ford` would count masked frames as clean Ford authorship, which is the
        # denominator mistake this fork has now made three separate times.
        self.acc_authority = _AA.recovery if clear_cancel else _AA.ford
      elif not CC.longActive:
        # NOBODY IS DRIVING, and calling that a fallback was wrong. `passthrough_admissible` returns
        # "openpilot longitudinal inactive" whenever cruise is not engaged, which is not a refusal at
        # all -- it is the disengaged state, and the frame that goes out is the inactive one.
        # Counting it as `fallback` made 23% of drive 389 read as openpilot substituting for Ford
        # when in fact nothing was being asked of either. Measured, 2026-08-18.
        self.acc_authority = _AA.stock
      elif not self.stock_acc_passthrough:
        self.acc_authority = _AA.openpilot
      elif self.passthrough_cancel_frames >= 250:
        self.acc_authority = _AA.inert
      else:
        self.acc_authority = _AA.fallback

      if use_passthrough:
        can_sends.append(fordcan_ext.create_acc_msg_passthrough(self.packer, self.CAN,
                                                               CS.acc_stock_values,
                                                               clear_cancel=clear_cancel))
        # Record what actually went on the wire, not what we computed and discarded. Every offline
        # tool reads actuatorsOutput, and the whole point of the first passthrough drive is to
        # compare Ford's numbers against openpilot's -- logging ours would falsify that comparison.
        self.accel = float(CS.acc_stock_values["AccBrkTot_A_Rq"])
        self.gas = float(CS.acc_stock_values["AccPrpl_A_Rq"])
      else:
        send_accel = lng.accel
        send_brake = lng.brake_actuate
        send_prchg = lng.precharge_actuate
        # THE OVERRIDE MAY ONLY EVER ADD BRAKING, NEVER REMOVE IT. Measured 2026-08-20, and it is
        # the most serious thing this feature has done.
        #
        # Taking authority means Ford's command stops reaching the car. Nothing made ours at least
        # as strong as the one it displaced, so on three of four measured episodes the override
        # braked SOFTER than the camera was already asking:
        #
        #     armed 40.0 mph   camera -1.22   ours -1.47    (harder -- fine)
        #     armed 32.9 mph   camera -1.14   ours -0.10    <-- 8.9 s of this
        #     armed 26.3 mph   camera -0.82   ours -0.41
        #     armed 28.3 mph   camera -1.07   ours -0.76
        #
        # The 32.9 mph case is a stop override that spent nearly nine seconds requesting a TENTH of
        # the braking the car would have had if this feature had not existed. He reported the
        # consequence from the seat the same day: a stopped car ahead, *"it didn't seem to set my
        # speed down that much at all"*, and a hard manual brake to avoid it.
        #
        # `min` on a signed accel takes the harder brake. Ford's number is the FLOOR, so:
        #   - while Ford is braking and we are weaker, we send Ford's -- byte-identical to the
        #     passthrough, no divergence, and nothing for the camera to object to.
        #   - below Ford's floor, where it has given up and asks for nothing, `.get` reads 0.0 and
        #     our braking stands unchanged. That case is the entire feature.
        #
        # It cannot weaken braking under any input: 0.0 is the neutral element of `min` against any
        # deceleration we would author, so a missing, stale or absent camera frame degrades to
        # exactly today's behaviour rather than to less braking.
        #
        # SCOPED TO `override` DELIBERATELY. `fallback` authors our command precisely when Ford's
        # frame was judged uncarriable -- an asserted cancel, or a value outside panda's band -- and
        # adopting a number from a frame we just refused would undo that refusal.
        if override:
          stock = CS.acc_stock_values or {}
          ford_accel = float(stock.get("AccBrkTot_A_Rq", 0.0))
          if ford_accel < send_accel:
            send_accel = ford_accel
            # Follow the request with the bits that make the car act on it. Deepening the number
            # while leaving these clear asks for a deceleration and does not request the actuation.
            send_brake = send_brake or bool(stock.get("AccBrkDecel_B_Rq", 0))
            send_prchg = send_prchg or bool(stock.get("AccBrkPrchg_B_Rq", 0))

        # AND NEVER DEEPER THAN FORD ITSELF ASKS WHILE THE CAR IS ALREADY STOPPED.
        #
        # openpilot's stopping state ramps the request toward `stopAccel` to pin a stationary car,
        # which is ordinary upstream behaviour and fine on a car openpilot fully controls. On this
        # one it is far outside the envelope the ACC system ever sees. Measured over every
        # standstill frame of routes 0000039d and 0000039f -- `AccBrkTot_A_Rq` in m/s^2:
        #
        #     FORD   n=7168   min -0.25   median -0.02
        #     OURS   n=6730   min -2.61   p5  -2.61
        #
        # Ten times Ford's deepest, on 5% of stopped frames. FORD DOES NOT HOLD A STOP WITH A
        # DECELERATION NUMBER -- it holds it with `AccStopStat_B_Rq`, which we already assert, and
        # asks for essentially nothing on top.
        #
        # Observed on route 0000039d: at a dead standstill the request ramped -1.03 -> -2.61 over
        # four seconds and sat there, then re-armed and ramped again while he was on the brake
        # pedal. The next ignition came up with `CcStat_D_Actl = Denied` -- cruise refused before
        # the drive began -- and cost him a pull-over and two restarts to clear. Attribution is
        # circumstantial, but a sustained near-maximum brake request against a stationary car is the
        # only thing in that window outside anything Ford does, and it is wrong on its own terms.
        #
        # -0.5 is twice Ford's deepest observed, so this never binds where Ford would have asked for
        # more, and it only applies once `standstill` is true -- the approach is untouched.
        if CS.out.standstill:
          # NEVER ABOVE WHAT FORD ASKED FOR. Found by review 2026-08-20: this clamp runs AFTER the
          # override's Ford floor and could raise the request back up past it, so with the override
          # active at a standstill and Ford asking -1.0 we would have sent -0.5 -- half the braking
          # Ford wanted, which is the precise failure the floor was added to make impossible.
          #
          # Ford's measured standstill requests top out at -0.25, so it does not bind on today's
          # data. That is luck, not structure. Taking `min` with Ford's own number means the floor
          # can only ever soften OUR excess, never Ford's request.
          floor = _STANDSTILL_ACCEL_FLOOR
          if override:
            floor = min(floor, float((CS.acc_stock_values or {}).get("AccBrkTot_A_Rq", 0.0)))
          send_accel = max(send_accel, floor)

        # TELL THE PCM A STOP IS HAPPENING. FusionPilot, 2026-08-23, and it is the best candidate
        # yet for why the camera declares ACC_Unavailable.
        #
        # `AccStopStat_B_Rq` is `stopping`, and `stopping` is `longControlState == stopping`, which
        # was measured across 21,936 frames as a STOPPED-CAR state -- never true above 3 mph. So the
        # override brakes the car from 20 to 0 while telling the PCM "not stopping" the whole way.
        # Measured on route 000003af: `AccStopMde_D_Rq` reads NoStop on ALL 888 frames the override
        # had the car.
        #
        # AND FORD'S OWN STOP LOOKS COMPLETELY DIFFERENT. Route 000003b1, same car, same evening,
        # override never fired: `AccStopMde_D_Rq` reads **Hold on 498 frames** under `ford`. That is
        # the first time this fork has ever seen Ford enter its own stop mode on this car, and it
        # shows the handshake works here when Ford drives it.
        #
        # So the override was bringing the car to a standstill OUTSIDE Ford's stop protocol: the
        # PCM never enters stop mode, and the camera -- which receives `CcStat_D_Actl` and
        # `AccStopMde_D_Rq` directly, IPMA_ADAS is a listed receiver of both -- sees a car that has
        # stopped while its own powertrain says no stop is in progress. `ACC_Unavailable` is a
        # reasonable thing to conclude from that, and unlike every other theory tried today it
        # explains why the state is LATCHED rather than transient.
        #
        # The override only ever runs when we are deliberately stopping, so asserting this is
        # honest signalling rather than a trick -- it is the same bit Ford asserts for the same
        # reason. Panda does not police it (see the unpoliced list in fordcan_ext).
        #
        # NOT PROVEN. It is a hypothesis with one strong correlation behind it, and the drive that
        # tests it is the next stop the override takes.
        can_sends.append(fordcan_ext.create_acc_msg(
          self.packer, self.CAN, CC.longActive, lng.gas, send_accel, lng.accel_pred_send,
          lng.stopping or override, send_brake, send_prchg, v_ego_kph=lng.target_speed
        ))

        self.accel = send_accel
        self.gas = lng.gas

    ### ui ###
    # BluePilot: HUD message generation via HudExt
    # Handles LKAS UI (1Hz), ACC UI (5Hz), bar persistence, and TJA/hands-free messaging.
    hud_can_sends = HudExt.update_hud(self, CC, CS, hud_control, main_on, fcw_alert,
                                       self.frame, self.packer, self.CAN, self.CP, lane_test)
    can_sends.extend(hud_can_sends)

    ### FusionPilot: synthesized APIM GPS toward the IPMA ###
    # 1Hz, matching the rate the APIM sends 0x462 at. Stands down entirely if the car turns out to
    # send the real messages -- one received frame latches carstate_ext.apim_gps_nav_seen and we
    # never compete with the APIM. Whole block is latched off on any exception: an unhandled one
    # here propagates through card's control loop and stops the car (2026-08-15).
    if (self.apim_gps_enabled and not getattr(self, "apim_gps_failed", True)
        and self.frame % CarControllerParams.LKAS_UI_STEP == 0):
      try:
        if not getattr(CS, "apim_gps_nav_seen", False):
          gps = getattr(self, "gps", None)
          if gps is not None:
            can_sends.append(fordcan_ext.create_apim_gps_nav2_msg(self.packer, self.CAN, gps))
            can_sends.append(fordcan_ext.create_apim_gps_nav3_msg(self.packer, self.CAN, gps))
      except Exception:  # noqa: BLE001 -- a GPS convenience must never take the car off the road
        self.apim_gps_failed = True
        cloudlog.exception("FusionPilot: synthesized APIM GPS disabled for this drive")

    new_actuators = actuators.as_builder()
    new_actuators.curvature = float(self.apply_curvature_last)
    new_actuators.accel = float(self.accel)
    new_actuators.gas = float(self.gas)

    self.frame += 1
    return new_actuators, can_sends
