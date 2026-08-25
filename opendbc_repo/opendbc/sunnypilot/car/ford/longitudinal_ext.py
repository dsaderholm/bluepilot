"""
BluePilot Ford longitudinal follow control extension.

Implements smoother highway following by classifying lead vehicle behavior
(gaining, pacing, trailing) and applying gas/accel limits per state. Also
adds split brake/precharge hysteresis for smoother deceleration.

Key features:
  - Speed deadband: BP long engages above 50 mph, disengages below 45 mph
  - Lead classification: gaining (closing in), pacing (matching), trailing (falling behind)
  - Gas limits per state: zero gas when gaining within 1.5s, capped gas when pacing
  - Rate-limited accel changes to avoid stomping the brakes
  - TTC-based emergency bypass for imminent collision scenarios
  - Ford's own deceleration blend: a real propulsion request alongside the brake, down to the
    handover at -1.1 m/s^2. `FordPropulsionBlend` off restores the old mutual exclusion.
"""

from collections import namedtuple

import numpy as np
from numpy import clip

from opendbc.car.ford.values import CarControllerParams


# FusionPilot: FORD'S OWN DECELERATION HIERARCHY, MEASURED OFF HIS CAR.
#
# The control literature is explicit that a vehicle should decelerate FIRST with engine drag, wind
# and rolling resistance, and reach for the friction brakes only when that cannot meet the demand.
# There is a measured comfort reason: engine braking produces roughly 0.51 m/s^3 of jerk against
# >1.35 for aggressive regen, and jerk rather than deceleration is what reads as abrupt.
#
# Ford does exactly that. `tools/bp_ford_decel_hierarchy.py`, routes 3bb/3bc/3bd/3be, 143,745 frames
# above 2 m/s with the driver's foot off the brake -- Ford's commanded accel against Ford's OWN
# `AccPrpl_A_Rq` on the same frame:
#
#     Ford commanded    Ford's propulsion request
#         -0.1                  +0.11
#         -0.2                  -0.09
#         -0.3                  -0.17
#         -0.5                  -0.34
#         -0.8                  -0.66
#       below -1.1              sentinel -- no propulsion request at all
#
# So Ford ramps engine braking to about -0.66 as deceleration builds toward -0.8, BLENDS it with the
# friction brakes across that whole band, and hands over completely below -1.1.
#
# openpilot could not do any of that, and it was not the thresholds -- it was one `if`:
#
#     if brake_actuate:  gas = INACTIVE_GAS
#
# The instant openpilot touched the brake at -0.14, its propulsion request became "not requesting".
# It could never be in the state the table describes -- asking the powertrain for -0.34 WHILE using
# the brakes -- at any threshold value. That is his *"I've never seen it coast"*.
#
# THE TABLE IS THE MEASUREMENT, INTERPOLATED, NOT A FITTED CURVE. A line through those points is a
# decent fit and it is still an invention; the bucket midpoints are what the car actually did.
#
# WHAT IS DELIBERATELY UNCHANGED: `brake_actuate_target`. `AccBrkTot_A_Rq` reaches `ABS_ESC`
# directly and whether the brake BITS gate the ABS or merely prepare it is not established, so
# moving that threshold is a separate question with its own drive. See bluepilot/FORD-ACC-PARITY.md.
#
# THE RISK, STATED RATHER THAN DISCOVERED. `AccBrkTot_A_Rq` is named TOTAL acceleration request, and
# Ford transmits both fields at these magnitudes simultaneously -- so the ABS is certainly designed
# to receive both at once and the propulsion share is meant to be subtracted from the total. If it
# is not, the car decelerates by up to the propulsion value MORE than commanded. That errs toward
# more braking, which is the direction he would notice immediately and complain about, it is bounded
# by 0.66 m/s^2, and it is what the toggle is for.
FORD_BLEND_ACCEL_BP = (-1.1, -0.8, -0.5, -0.3, -0.2, -0.1)
FORD_BLEND_PROPULSION_V = (-0.66, -0.66, -0.34, -0.17, -0.09, 0.11)

# Below this, Ford stops asking the powertrain for anything and the friction brakes have the car
# alone. Preserving THAT is as much a part of copying Ford as the blend above it.
FORD_BLEND_HANDOVER_ACCEL = -1.1

# Floor for a blended request. -0.7 covers Ford's measured -0.66 with a hair to spare, and it is
# deliberately tighter than panda's -2.8 envelope -- panda is the outer bound, this is the
# conservatism. It should never bind, and a test asserts that it does not, so if it ever starts to
# then the table moved and somebody should know.
FORD_BLEND_PROPULSION_FLOOR = -0.7


def ford_propulsion_request(accel: float, gas: float) -> float:
  """The propulsion request Ford would send for this commanded deceleration.

  Above the band this returns upstream's own `gas` untouched -- accelerating and coasting are not
  what this is about. Below the handover it returns the -5.0 sentinel, which is both what Ford does
  and what openpilot already did.
  """
  if accel > FORD_BLEND_ACCEL_BP[-1]:
    return gas
  if accel < FORD_BLEND_HANDOVER_ACCEL:
    return CarControllerParams.INACTIVE_GAS
  return float(np.interp(accel, FORD_BLEND_ACCEL_BP, FORD_BLEND_PROPULSION_V))


# Result namedtuple returned by LongitudinalExt.update()
LongitudinalResult = namedtuple('LongitudinalResult', [
  'accel',
  'gas',
  'brake_actuate',
  'precharge_actuate',
  'accel_pred_send',
  'stopping',
  'target_speed',
  'bp_long_used',
])


class LongitudinalExt:
  """
  BluePilot longitudinal follow control extension for Ford vehicles.

  Mixed into CarController via multiple inheritance. The stock carcontroller
  computes op_accel/op_gas using upstream logic, then calls
  LongitudinalExt.update() to apply BP follow control on top.

  The SubMaster (for radarState) is owned by LateralCurvExt and shared via self.sm
  since both classes are mixed into the same CarController instance.
  """

  def __init__(self, CP, CP_SP):
    # BP longitudinal state
    self._bp_long_active_last = False
    self.bp_gas_last = 0.0
    self.bp_accel_last = 0.0
    self.bpSpeedAllow = False

    # Thresholds
    self.MAX_URBAN_SPEED_MPH = 45.0
    self.following_accel_ROC = 0.002  # max accel change per scan in following mode

    # Brake hysteresis thresholds
    self.brake_actuate_target = -0.14   # engage brakes below this accel
    self.brake_actuate_release = -0.06  # release brakes above this accel
    self.precharge_actuate_target = -0.12
    self.precharge_actuate_release = -0.06
    self.op_brake_actuate_last = False

    # Toggles (updated from Params each frame)
    self.disable_BP_long_UI = False
    self.disable_downhill_comp_UI = True
    self.propulsion_blend = True

    # BluePilot: standstill resume gate
    self.resume_gate_enabled = False
    self.resume_min_gap_m = 6.0
    self.resume_min_lead_speed_ms = 5 * 0.44704
    self.resume_gate_blocking = False

  def update_long_params(self, params):
    """Read longitudinal-related Params from the UI. Called each frame."""
    self.disable_BP_long_UI = params.get_bool("disable_BP_long_UI")
    self.disable_downhill_comp_UI = params.get_bool("disable_downhill_comp_UI")
    self.propulsion_blend = params.get_bool("FordPropulsionBlend")
    self.resume_gate_enabled = params.get_bool("IcbmResumeGateEnabled")
    self.resume_min_gap_m = float(params.get("IcbmResumeMinGap", return_default=True))
    self.resume_min_lead_speed_ms = float(params.get("IcbmResumeMinLeadSpeed", return_default=True)) * 0.44704

  def resume_allowed(self, sm) -> bool:
    """BluePilot: should openpilot's standstill resume request actually be sent?

    controlsd sets CC.cruiseControl.resume from its own MPC plan (shouldStop), which is the right
    judgement when openpilot is driving. On a car running stock Ford ACC it is the wrong one: the
    MPC decides there is room to move, openpilot presses RESUME, and Ford's ACC -- which was not
    consulted -- takes that as "go" and accelerates toward the set speed. If the lead has crept
    forward only a foot or two, Ford's radar then finds it immediately and brakes hard. Two
    planners, one car, neither aware of the other.

    So gate on the lead itself: resume only once there is a real gap or the lead is genuinely
    moving. With no lead at all there is nothing to wait for.

    ON by default (IcbmResumeGateEnabled = 1). An earlier version of this docstring said "off by
    default", which never matched the declared default -- do not trust that reading. Withholding
    the resume button is the conservative direction anyway: the car waits where stock Ford ACC
    would also have waited, and the driver's own resume press still goes through untouched.
    """
    if not self.resume_gate_enabled:
      self.resume_gate_blocking = False
      return True

    try:
      lead = sm['radarState'].leadOne
    except (KeyError, AttributeError):
      self.resume_gate_blocking = False
      return True

    if not lead.status:
      self.resume_gate_blocking = False
      return True

    allowed = lead.dRel >= self.resume_min_gap_m or lead.vLead >= self.resume_min_lead_speed_ms
    self.resume_gate_blocking = not allowed
    return allowed

  def update(self, CC, CS, op_accel, op_gas, accel_due_to_pitch, v_ego_mph, stopping, target_speed):
    """
    Apply BluePilot longitudinal follow control on top of stock op_accel/op_gas.

    Called at 50Hz from CarController.update() inside the ACC_CONTROL_STEP block,
    after stock creep compensation and rate limiting have been applied.

    Args:
      CC: CarControl with longActive
      CS: CarState with vEgo, gasPressed, brakePressed
      op_accel: Stock openpilot accel after creep comp + rate limit (m/s^2)
      op_gas: Stock openpilot gas value (m/s^2)
      accel_due_to_pitch: Pitch compensation value (m/s^2, may be clamped by downhill toggle)
      v_ego_mph: Current speed in mph
      stopping: True if in stopping state
      target_speed: Target cruise speed (km/h)

    Returns:
      LongitudinalResult namedtuple with final accel, gas, brake, precharge values.
    """
    # Downhill compensation disable: clamp negative pitch to 0 (already applied by caller,
    # but this is where the logic lives conceptually)

    # Op brake actuate hysteresis
    accel_pitch_compensated = op_accel + accel_due_to_pitch
    op_brake_actuate = self.op_brake_actuate_last
    if accel_pitch_compensated > self.brake_actuate_release or not CC.longActive:
      op_brake_actuate = False
    elif accel_pitch_compensated < self.brake_actuate_target:
      op_brake_actuate = True

    # Speed deadband: engage above 50 mph, disallow below 45 mph
    bpSpeedTooSlow = v_ego_mph < self.MAX_URBAN_SPEED_MPH
    bpSpeedHighEnough = v_ego_mph > self.MAX_URBAN_SPEED_MPH + 5
    if bpSpeedHighEnough:
      self.bpSpeedAllow = True
    if bpSpeedTooSlow:
      self.bpSpeedAllow = False

    # BP longitudinal follow control
    if not self.disable_BP_long_UI:
      # Read lead vehicle data from radarState (SubMaster is on self via mixin)
      v_ego = max(CS.out.vEgo, 0.5)
      lead_time_sec = 999.0
      lead = None
      v_rel = 0.0
      v_lead = 0.0

      if self.sm.valid.get('radarState', False):
        rs = self.sm['radarState']
        lead = getattr(rs, 'leadOne', None)
        if lead is not None and getattr(lead, 'status', 0) != 1:
          lead = None
        if lead:
          d_rel = float(getattr(lead, 'dRel', 0))
          v_rel = float(getattr(lead, 'vRel', 0))
          v_lead = float(getattr(lead, 'vLead', 0))
          if d_rel > 0:
            lead_time_sec = d_rel / v_ego

      lead_time_sec = float(np.clip(lead_time_sec, 0.0, 999.0))
      v_lead_mph = v_lead * 2.23694

      # Time to collision
      ttc_sec = 120.0
      if lead:
        d_rel = float(getattr(lead, 'dRel', 0))
        v_rel = float(getattr(lead, 'vRel', 0))
        if d_rel > 0 and v_rel < 0:
          ttc_sec = d_rel / (-v_rel)
        else:
          ttc_sec = 60.0
      ttc_sec = float(np.clip(ttc_sec, 0.2, 120.0))

      # Classify lead state: gaining, pacing, or trailing
      gaining = False
      pacing = False
      trailing = False
      max_follow_gas = op_gas
      min_follow_gas = op_gas
      max_follow_accel = op_accel
      min_follow_accel = op_accel
      bp_brake_actuate = False
      bp_precharge_actuate = False

      if lead:
        if v_rel < -0.1:
          gaining = True
        elif v_rel > 0.1:
          trailing = True
        else:
          pacing = True

      # Gas/accel limits per state
      if gaining:
        if lead_time_sec < 1.5:
          max_follow_gas = 0.0  # within 1.5s and gaining — no gas
          min_follow_gas = 0.0
        else:
          max_follow_gas = op_gas
          min_follow_gas = op_gas
        max_follow_accel = op_accel
        min_follow_accel = op_accel

      if pacing:
        max_follow_gas = 0.2 + accel_due_to_pitch  # cap gas when pacing
        min_follow_gas = 0.0
        max_follow_accel = op_accel
        min_follow_accel = op_accel

      if trailing:
        max_follow_gas = op_gas
        min_follow_gas = op_gas
        max_follow_accel = op_accel
        min_follow_accel = op_accel

      if lead is None:
        max_follow_gas = op_gas
        min_follow_gas = op_gas
        max_follow_accel = 0
        min_follow_accel = 0

      # Apply BP gas and accel targets
      bp_gas = clip(op_gas, min_follow_gas, max_follow_gas)
      bp_accel = clip(op_accel, min_follow_accel, max_follow_accel)

      # Rate limit downward accel changes (dampen initial brake hit)
      # Skip rate limit if imminent collision risk
      if ttc_sec > 8.0 and lead_time_sec > 0.5:
        bp_accel = clip(bp_accel, self.bp_accel_last - self.following_accel_ROC, 999)

      # BP brake/precharge hysteresis
      if bp_accel < self.brake_actuate_target:
        bp_brake_actuate = True
      if bp_accel > self.brake_actuate_release:
        bp_brake_actuate = False
      if bp_accel < self.precharge_actuate_target:
        bp_precharge_actuate = True
      if bp_accel > self.precharge_actuate_release:
        bp_precharge_actuate = False

      # Decide whether to apply BP long
      gasPressed = CS.out.gasPressed
      brakePressed = CS.out.brakePressed
      apply_bp_long = (not self.disable_BP_long_UI and self.bpSpeedAllow and
                       not gasPressed and not brakePressed and
                       (lead is None or v_lead_mph > 40.0))

      if apply_bp_long and CC.longActive:
        accel = bp_accel
        gas = bp_gas
        brake_actuate = bp_brake_actuate
        precharge_actuate = bp_precharge_actuate
      else:
        accel = op_accel
        gas = op_gas
        brake_actuate = op_brake_actuate
        precharge_actuate = op_brake_actuate

      self.bp_gas_last = bp_gas
      self.bp_accel_last = bp_accel
      bp_long_used = apply_bp_long
    else:
      # BP long disabled — pass through stock values
      accel = op_accel
      gas = op_gas
      brake_actuate = op_brake_actuate
      precharge_actuate = op_brake_actuate
      bp_long_used = False

    # FusionPilot: BLEND, the way Ford does, instead of the mutual exclusion. See the table at the
    # top of this file. With the toggle off this is byte-for-byte the old behaviour.
    gas_floor = CarControllerParams.MIN_GAS
    if self.propulsion_blend:
      gas = ford_propulsion_request(accel, gas)
      gas_floor = FORD_BLEND_PROPULSION_FLOOR
    elif brake_actuate:
      # Mutual exclusion: no brake and gas at the same time
      gas = CarControllerParams.INACTIVE_GAS

    # Clip to ford.h ACCDATA safety limits
    accel = float(clip(accel, CarControllerParams.ACCEL_MIN, CarControllerParams.ACCEL_MAX))
    if gas != CarControllerParams.INACTIVE_GAS:
      gas = float(clip(gas, gas_floor, CarControllerParams.ACCEL_MAX))
    accel_pred_send = CarControllerParams.INACTIVE_GAS

    self._bp_long_active_last = bp_long_used
    self.op_brake_actuate_last = op_brake_actuate

    return LongitudinalResult(
      accel=accel,
      gas=gas,
      brake_actuate=brake_actuate,
      precharge_actuate=precharge_actuate,
      accel_pred_send=accel_pred_send,
      stopping=stopping,
      target_speed=target_speed,
      bp_long_used=bp_long_used,
    )
