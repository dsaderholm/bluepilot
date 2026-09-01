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
from opendbc.sunnypilot.car.ford.longitudinal_ext import LongitudinalExt
from opendbc.sunnypilot.car.ford.hud_ext import HudExt

# THE CANCEL-RECOVERY BOUNDS, in seconds, with the frame counts DERIVED. Never restate a frame
# count here: `update` runs inside the ACC_CONTROL_STEP block at 50 Hz, not the 100 Hz control
from opendbc.sunnypilot.car.ford import fordcan_ext
from opendbc.sunnypilot.car.ford.icbm import IntelligentCruiseButtonManagementInterface
from opendbc.sunnypilot.car.ford.gap_control import FordGapController

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
                    IntelligentCruiseButtonManagementInterface):
  def __init__(self, dbc_names, CP, CP_SP):
    CarControllerBase.__init__(self, dbc_names, CP, CP_SP)
    # BluePilot: initialize extension classes
    LateralCurvExt.__init__(self, CP, CP_SP)
    LateralAngleExt.__init__(self, CP, CP_SP)
    LongitudinalExt.__init__(self, CP, CP_SP)
    HudExt.__init__(self, CP, CP_SP)
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
    # once at init; this changes what a module on the bus is fed and is
    # not something to toggle mid-drive. Latched off on ANY failure, and the getattr default in
    # the send path is True so a missing attribute disables the feature rather than the car.
    self.apim_gps_enabled = self.params.get_bool("FordSynthesizeApimGps")
    self.apim_gps_failed = False
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
    fcw_alert = hud_control.visualAlert == VisualAlert.fcw

    # BluePilot: compute DM state (TJA message, warning, hands level)
    HudExt.update_dm(self, hud_control, main_on, CS.out.cruiseState.standstill, self.frame)

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
    # SUPPRESSION REVERTED, 2026-08-23, SAME DAY IT SHIPPED. He nearly went off an exit ramp.
    #
    # The idea was that ICBM pressing the set speed while openpilot authors ACCDATA is pointless,
    # which is TRUE and was measured -- 378 presses and 84 mph of dash travel during one inert
    # window. But suppressing it froze the set speed wherever the latch caught it, and he hit the
    # benign face of that three times ("stuck at 25 even though SLA wanted 35") before hitting the
    # dangerous one: frozen HIGH on the approach to an exit, with ICBM unable to bring it down.
    #
    # The follow-up made it WORSE. Blocking only DOWNWARD presses fixed being stuck low and left
    # being stuck high completely unaddressed -- it blocks exactly the direction an exit needs.
    # Two wrong rules in one evening, both shipped to a car being driven.
    #
    # WHY NOTHING NARROWER IS SAFE ENOUGH TO SHIP NOW: the correct rule is his -- move toward the
    # DRIVER'S AIM and stop there, which is neither "always" nor "never" nor either direction. That
    # needs the aim, which lives in ICBM's units in selfdrived, and the carcontroller has no
    # is_metric to compare against it. Building that boundary correctly is not an on-road hotfix.
    #
    # So ICBM presses unconditionally again, exactly as it did before today. That costs the hunting
    # on an inert drive, which is ANNOYING. The thing it buys back is the set speed always being
    # able to come down, which is not.
    icbm_can_sends, self.last_button_frame = IntelligentCruiseButtonManagementInterface.update(
      self, CC_SP, CS, self.packer, self.CAN, self.frame, self.last_button_frame
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

        # FusionPilot: angle-mode command + gain telemetry for controllerStateBP. Zeroed outside
        # angle mode rather than left stale -- a held-over value reads as a live one, which is how
        # a settings snapshot lied about a car that never existed.
        self.pathAngleFinal = float(getattr(self, 'bp_path_angle_final', 0.0)) if _angle_mode else 0.0
        self.kappaCmd = float(getattr(self, 'bp_kappa_cmd', 0.0)) if _angle_mode else 0.0
        self.curvatureFactor = float(getattr(self, 'bp_curvature_factor', 0.0)) if _angle_mode else 0.0
        self.laneCenterCorrection = float(getattr(self, 'bp_lane_center_correction', 0.0)) if _angle_mode else 0.0
        self.gainLowCurv = float(getattr(self, 'bp_gain_low_curv', 0.0)) if _angle_mode else 0.0
        self.gainHighCurv = float(getattr(self, 'bp_gain_high_curv', 0.0)) if _angle_mode else 0.0
        self.blendWeight = float(getattr(self, 'bp_blend_weight', 0.0)) if _angle_mode else 0.0

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
      # openpilot authors ACCDATA here. `create_acc_msg` clamps to panda's bands and never
      # touches the bits panda does not police.
      send_accel = lng.accel
      send_brake = lng.brake_actuate
      send_prchg = lng.precharge_actuate
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
        # active at a standstill and Ford asking -1.0 we would have sent -0.5 -- half the braking
        # Ford wanted, which is the precise failure the floor was added to make impossible.
        #
        # Ford's measured standstill requests top out at -0.25, so it does not bind on today's
        # data. That is luck, not structure. Taking `min` with Ford's own number means the floor
        # can only ever soften OUR excess, never Ford's request.
        send_accel = max(send_accel, _STANDSTILL_ACCEL_FLOOR)

      # TELL THE PCM A STOP IS HAPPENING. FusionPilot, 2026-08-23, and it is the best candidate
      # yet for why the camera declares ACC_Unavailable.
      #
      # `AccStopStat_B_Rq` is `stopping`, and `stopping` is `longControlState == stopping`, which
      # was measured across 21,936 frames as a STOPPED-CAR state -- never true above 3 mph. So the
      # had the car.
      #
      # AND FORD'S OWN STOP LOOKS COMPLETELY DIFFERENT. Route 000003b1, same car, same evening,
      # the first time this fork has ever seen Ford enter its own stop mode on this car, and it
      # shows the handshake works here when Ford drives it.
      #
      # PCM never enters stop mode, and the camera -- which receives `CcStat_D_Actl` and
      # `AccStopMde_D_Rq` directly, IPMA_ADAS is a listed receiver of both -- sees a car that has
      # stopped while its own powertrain says no stop is in progress. `ACC_Unavailable` is a
      # reasonable thing to conclude from that, and unlike every other theory tried today it
      # explains why the state is LATCHED rather than transient.
      #
      # honest signalling rather than a trick -- it is the same bit Ford asserts for the same
      # reason. Panda does not police it (see the unpoliced list in fordcan_ext).
      #
      # NOT PROVEN. It is a hypothesis with one strong correlation behind it, and the drive that
      can_sends.append(fordcan_ext.create_acc_msg(
        self.packer, self.CAN, CC.longActive, lng.gas, send_accel, lng.accel_pred_send,
        lng.stopping, send_brake, send_prchg, v_ego_kph=lng.target_speed
      ))

      self.accel = send_accel
      self.gas = lng.gas

    ### ui ###
    # BluePilot: HUD message generation via HudExt
    # Handles LKAS UI (1Hz), ACC UI (5Hz), bar persistence, and TJA/hands-free messaging.
    hud_can_sends = HudExt.update_hud(self, CC, CS, hud_control, main_on, fcw_alert,
                                     self.frame, self.packer, self.CAN, self.CP)
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
