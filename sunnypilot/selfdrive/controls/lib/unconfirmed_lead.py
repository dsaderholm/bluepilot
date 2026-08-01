"""
BluePilot: radar-blind stopped-lead detection for Ford stock ACC.

Ford's ACC follows only radar-confirmed leads. The driving model regularly sees a stopped vehicle
that the radar never returns -- the classic case being a stopped car at the end of a queue. Stock
ACC will hold the set speed straight into it.

This module detects that case and asks ICBM to bring the set speed down toward Ford's ACC floor
(20 mph), while raising an alert at the moment of trigger so the driver has the full deceleration
as reaction time rather than being told once the car is already at the floor.

Scope and limits, deliberately:
  - This is NOT an AEB change and NOT an attempt at an automated stop. The only actuation channel
    is ICBM's existing cruise-button presses; no braking force is commanded anywhere.
  - Ford's ACC floor is 20 mph and it HOLDS that speed. Below the floor the driver brakes, full
    stop. Reaching the floor is the end of what this can do, not the start of a stop.
  - The best outcome is that the deceleration lets the radar acquire the lead, after which Ford's
    own ACC takes over and can follow to a complete stop. That is a release condition, not a
    failure, and it is the expected resolution path.

Target speed comes from openpilot's existing lead-follow MPC rather than a fixed step. The MPC
already ingests vision-only leads -- process_lead() gates on lead.status, and radard publishes
vision leads with status=True, radar=False -- so longitudinalPlan.speeds is already a
geometry-scaled deceleration curve for this exact lead: gradual when there is distance, sharp when
there is not. Reusing it means no new tuning, and it keeps this off ICBM's target-drop rate
limiter, which is scoped to routine speed-limit and curve changes.

Thresholds here are starting values reviewed before first drive, not derived constants. The
range-sweep requirement is the main defence against bridge, overpass and guardrail false
positives, and along with the usable detection range it should be refitted from real drive logs.
"""

from cereal import custom, log
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP

EventNameSP = custom.OnroadEventSP.EventName
State = custom.LongitudinalPlanSP.UnconfirmedLead.State

# Ford ACC's minimum settable speed. Not a workaround -- the hardware floor.
ACC_FLOOR_MS = 20 * CV.MPH_TO_MS

# --- trigger gates (all must hold simultaneously) ---
# radard's own lead gate is lead_prob > 0.5 (radard.py get_lead); sit meaningfully above it.
MIN_MODEL_PROB = 0.65
# Continuous time the candidate must survive before it can trigger. Kills single-frame blips.
MIN_PERSISTENCE_S = 1.0
# Distance the candidate must be tracked *through* before it can trigger. This is the overpass
# filter: bridges, gantries and guardrails tend to appear and vanish, while a real vehicle
# survives a closing range sweep. Least-justified threshold here -- refit from logs.
MIN_RANGE_SWEEP_M = 15.0
MAX_TTC_S = 4.0            # only act when the closing rate actually matters
MAX_V_REL_MS = -2.0        # genuinely closing, not sensor noise
MAX_D_PATH_M = 1.2         # in-path, not an adjacent lane or roadside return
MIN_V_EGO_MS = 25 * CV.MPH_TO_MS  # below this a floor request is meaningless

# --- release gates ---
RELEASE_TTC_S = 6.0        # hysteresis against chatter around MAX_TTC_S
LEAD_LOST_S = 0.5          # candidate gone this long -> released


class UnconfirmedLeadDetector:
  def __init__(self):
    self.state = State.inactive
    self.v_target = 0.0
    self.restore_set_speed = 0.0
    self.d_rel = 0.0
    self.ttc = 0.0

    # Logging only -- see the capnp comment. Nothing reads these to make a decision.
    self.model_should_stop = False
    self.model_desired_accel = 0.0
    self.has_lead = False

    self._persistence_s = 0.0
    self._lost_s = 0.0
    self._sweep_start_d_rel = 0.0

  @property
  def is_active(self) -> bool:
    return self.state == State.active

  @property
  def is_restoring(self) -> bool:
    return self.state == State.restoring

  @staticmethod
  def _ttc(d_rel: float, v_rel: float) -> float:
    # v_rel is negative when closing; guard the divide and return a large TTC when not closing.
    closing = max(-v_rel, 0.1)
    return d_rel / closing

  def _candidate(self, lead: log.RadarState.LeadData, v_ego: float, brake_pressed: bool) -> bool:
    """Frame-level gates. Persistence and range sweep are accumulated by the caller."""
    if not lead.status or lead.radar:
      return False  # no lead, or radar already confirms it -- Ford ACC handles that case itself
    if lead.modelProb < MIN_MODEL_PROB:
      return False
    if lead.vRel > MAX_V_REL_MS:
      return False
    if abs(lead.dPath) > MAX_D_PATH_M:
      return False
    if v_ego < MIN_V_EGO_MS:
      return False
    if brake_pressed:
      return False
    return True

  def _reset_evidence(self) -> None:
    self._persistence_s = 0.0
    self._sweep_start_d_rel = 0.0

  def _release(self) -> None:
    """Leave active. Restore the set speed if we lowered it, otherwise go idle."""
    self._reset_evidence()
    self._lost_s = 0.0
    if self.restore_set_speed > 0:
      self.state = State.restoring
    else:
      self.state = State.inactive

  def update(self, sm, v_desired_trajectory, v_cruise_cluster: float, long_enabled: bool,
             events_sp: EventsSP) -> None:
    """
    Args:
      sm: SubMaster with radarState and carState
      v_desired_trajectory: the stock planner's MPC speed plan (m/s). Already accounts for the
        vision-only lead, so it is the deceleration curve we want.
      v_cruise_cluster: current set speed (m/s), captured as the restore point on trigger.
      long_enabled: cruise engaged and under our control
      events_sp: alert sink
    """
    CS = sm['carState']
    lead = sm['radarState'].leadOne
    v_ego = CS.vEgo

    self.d_rel = lead.dRel
    self.ttc = self._ttc(lead.dRel, lead.vRel)

    # Diagnostics for the stop-sign / red-light question. Logged unconditionally, including while
    # this detector is inactive, because the interesting case is exactly when there is no lead.
    model_action = sm['modelV2'].action
    self.model_should_stop = bool(model_action.shouldStop)
    self.model_desired_accel = float(model_action.desiredAcceleration)
    self.has_lead = bool(lead.status)

    if not long_enabled:
      # Disengaged: drop everything, including any pending restore. Ford restores its own set
      # speed on re-engage, and ICBM re-arms to AUTO on the cruise cycle.
      self.state = State.inactive
      self.restore_set_speed = 0.0
      self._reset_evidence()
      return

    # ---- RESTORING: return the set speed and hold until the cluster gets there ----
    # Runs while stopped as well as while moving. That is deliberate: if the radar acquired the
    # lead and Ford's ACC brought the car to a stop, the set speed is still sitting at the floor,
    # and ACC would resume to 20 mph rather than the original speed. Raising it during the
    # standstill hold commands no acceleration -- ACC is holding for the lead -- and it has to
    # happen before the resume window, because controlsd asserts cruiseControl.resume there and
    # ICBM's readiness check goes deaf while it is set.
    if self.state == State.restoring:
      self.v_target = self.restore_set_speed
      if v_cruise_cluster >= self.restore_set_speed - 0.5:
        self.state = State.inactive
        self.restore_set_speed = 0.0
      return

    candidate = self._candidate(lead, v_ego, CS.brakePressed)

    # ---- ACTIVE: hold the request until something resolves it ----
    if self.state == State.active:
      radar_acquired = lead.status and lead.radar
      if radar_acquired:
        # The good outcome: the deceleration bought a radar detection. Ford ACC owns it now and
        # can follow to a full stop, which this never could.
        self._release()
        return

      if not lead.status:
        self._lost_s += DT_MDL
        if self._lost_s >= LEAD_LOST_S:
          self._release()
          return
      else:
        self._lost_s = 0.0

      if self.ttc > RELEASE_TTC_S:
        self._release()
        return

      # Still active: track the MPC's plan, floored. Never request below what Ford can hold.
      self.v_target = max(float(v_desired_trajectory[-1]), ACC_FLOOR_MS)
      # Re-raised every cycle, not once at trigger: this alert has to stay up for as long as the
      # driver is the only thing that can stop the car.
      events_sp.add(EventNameSP.unconfirmedLeadBraking)
      return

    # ---- INACTIVE / TRACKING: accumulate evidence ----
    if not candidate:
      self.state = State.inactive
      self._reset_evidence()
      return

    if self.state == State.inactive:
      self.state = State.tracking
      self._sweep_start_d_rel = lead.dRel

    self._persistence_s += DT_MDL

    swept = self._sweep_start_d_rel - lead.dRel
    if (self._persistence_s >= MIN_PERSISTENCE_S and swept >= MIN_RANGE_SWEEP_M
        and self.ttc <= MAX_TTC_S):
      self.state = State.active
      self.restore_set_speed = v_cruise_cluster
      self.v_target = max(float(v_desired_trajectory[-1]), ACC_FLOOR_MS)
      self._lost_s = 0.0
      # Alert at trigger, not at the floor: the whole deceleration is the driver's reaction time.
      events_sp.add(EventNameSP.unconfirmedLeadBraking)
