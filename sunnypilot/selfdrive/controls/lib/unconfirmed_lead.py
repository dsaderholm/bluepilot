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
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot import PARAMS_UPDATE_PERIOD
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP

EventNameSP = custom.OnroadEventSP.EventName
State = custom.LongitudinalPlanSP.UnconfirmedLead.State
Trigger = custom.LongitudinalPlanSP.UnconfirmedLead.Trigger

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

# --- model stop intent (stop signs, red lights) ---
#
# modelV2.action.shouldStop is published every cycle whatever the longitudinal mode is; is_e2e()
# in longitudinal_planner only decides whether the planner consumes it. It is the same signal that
# stops the car under openpilot longitudinal with experimental mode, which is reported to work well
# on this vehicle for signs and signals -- the reason for running stock Ford ACC is the rest of
# op long's behaviour, not this part of it.
#
# This is the only signal available for the case the lead trigger structurally cannot catch: a sign
# or signal with no vehicle at it produces no lead, so there is no dRel, vRel or TTC to gate on.
# Persistence and the speed floor are therefore the whole filter, which is why it is separately
# switchable via IcbmModelStopEnabled.
MODEL_STOP_PERSISTENCE_S = 1.0
MODEL_STOP_RELEASE_S = 0.5
# Horizon used to turn the model's desired acceleration into a set-speed target. Matches SCC-V's
# _NO_OVERSHOOT_TIME_HORIZON so the two produce comparably paced requests.
MODEL_STOP_HORIZON_S = 4.0


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
    self.trigger = Trigger.none

    self._persistence_s = 0.0
    self._lost_s = 0.0
    self._sweep_start_d_rel = 0.0
    self._model_stop_s = 0.0
    self._model_clear_s = 0.0

    self.params = Params()
    self.frame = 0
    self.max_lead_distance = 120
    self.model_stop_enabled = True

  def update_params(self) -> None:
    if self.frame % int(PARAMS_UPDATE_PERIOD / DT_MDL) == 0:
      self.max_lead_distance = self.params.get("IcbmLeadMaxDistance", return_default=True)
      self.model_stop_enabled = self.params.get_bool("IcbmModelStopEnabled")

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
    # Ford ACC deals with close leads perfectly well. This exists for the distant stopped car, so
    # the far bound is the tunable one. Note MAX_TTC_S also bounds range implicitly -- against a
    # stopped lead, TTC = dRel / v_ego, so at 65 mph a 4 s TTC is already about 116 m. Raising this
    # past that does nothing on its own.
    if lead.dRel > self.max_lead_distance:
      return False
    if v_ego < MIN_V_EGO_MS:
      return False
    if brake_pressed:
      return False
    return True

  def _reset_evidence(self) -> None:
    """Clear LEAD evidence only.

    Deliberately does not touch the model-stop timers. The inactive branch calls this on every
    frame without a lead candidate, which is exactly when model-stop evidence is accumulating --
    clearing it here would reset the counter immediately after each increment and the model-stop
    trigger could never reach its threshold.
    """
    self._persistence_s = 0.0
    self._sweep_start_d_rel = 0.0

  def _release(self) -> None:
    """Leave active. Restore the set speed if we lowered it, otherwise go idle."""
    self._reset_evidence()
    self._lost_s = 0.0
    self._model_stop_s = 0.0
    self._model_clear_s = 0.0
    self.trigger = Trigger.none
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
    self.update_params()
    self.frame += 1

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
      self.trigger = Trigger.none
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

    # ---- ACTIVE (model stop): resolve on the model letting go ----
    if self.state == State.active and self.trigger == Trigger.modelStop:
      if self.model_should_stop:
        self._model_clear_s = 0.0
      else:
        self._model_clear_s += DT_MDL
        if self._model_clear_s >= MODEL_STOP_RELEASE_S:
          self._release()
          return

      # Below the floor the driver has taken over with the pedal; there is nothing left to ask for.
      if v_ego < ACC_FLOOR_MS or CS.brakePressed:
        self._release()
        return

      self.v_target = self._model_stop_target(v_ego)
      events_sp.add(EventNameSP.modelStopBraking)
      return

    # ---- ACTIVE (vision lead): hold the request until something resolves it ----
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

    # ---- model stop intent: the only signal for a sign or signal with no vehicle at it ----
    # Evaluated before the lead path so a real lead always takes precedence: if there is something
    # to see, the lead trigger's geometry filters are strictly better evidence than shouldStop.
    if self.model_stop_enabled and not candidate:
      radar_has_it = lead.status and lead.radar
      model_candidate = (self.model_should_stop and not radar_has_it and
                         v_ego >= MIN_V_EGO_MS and not CS.brakePressed)
      if model_candidate:
        self._model_stop_s += DT_MDL
        if self._model_stop_s >= MODEL_STOP_PERSISTENCE_S:
          self.state = State.active
          self.trigger = Trigger.modelStop
          self.restore_set_speed = v_cruise_cluster
          self.v_target = self._model_stop_target(v_ego)
          self._model_clear_s = 0.0
          events_sp.add(EventNameSP.modelStopBraking)
          return
      else:
        self._model_stop_s = 0.0

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
      self.trigger = Trigger.visionLead
      self.restore_set_speed = v_cruise_cluster
      self.v_target = max(float(v_desired_trajectory[-1]), ACC_FLOOR_MS)
      self._lost_s = 0.0
      # Alert at trigger, not at the floor: the whole deceleration is the driver's reaction time.
      events_sp.add(EventNameSP.unconfirmedLeadBraking)

  def _model_stop_target(self, v_ego: float) -> float:
    """Set-speed target from the model's own desired deceleration.

    The MPC plan is no use here: with no lead, it plans normally and shows no deceleration at all,
    because in ACC mode the planner never consumes the model's stop intent. So the target is built
    from modelV2.action.desiredAcceleration directly -- projected over a fixed horizon, floored at
    what Ford's ACC can hold, and never allowed to request above current speed.

    This paces the request by how hard the model wants to stop: gentle for a distant sign, sharper
    for one already close.
    """
    projected = v_ego + self.model_desired_accel * MODEL_STOP_HORIZON_S
    return max(min(projected, v_ego), ACC_FLOOR_MS)
