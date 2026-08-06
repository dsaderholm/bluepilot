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
range-sweep requirement is the main defense against bridge, overpass and guardrail false
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
# BluePilot: a lead that is genuinely STOPPED is stronger evidence than one merely closing, and it
# is the entire reason this feature exists -- radar ACC ignores stationary returns, so a stopped car
# at the end of a queue is the one thing Ford will drive into. It gets a shorter persistence.
#
# It does NOT get to skip the range sweep, which was the first attempt here. The sweep is a physics
# check, not a delay: it confirms the range is actually shrinking. A target reporting "stopped"
# whose range never closes is the bridge/overpass signature -- the model calls an overhead
# structure a stopped vehicle, and dropping the sweep makes that fire a 20 mph request on an open
# motorway. test_persistence_alone_does_not_trigger exists for exactly that and caught it.
#
# The sweep is also not the bottleneck it looked like. Against a stopped lead at 65 mph the range
# closes at ~29 m/s, so 15 m costs ~0.5 s and runs concurrently with persistence. What actually
# bounds how early this fires is whichever of TTC (IcbmLeadMaxTtc) and the distance cap
# (IcbmLeadMaxDistance) is tighter at the current speed. At the shipped defaults -- 7.0 s and
# 180 m -- they cross at about 57 mph: below that TTC binds, above it the distance cap does. So at
# 65 mph the limit is 180 m, not the 203 m the TTC alone would allow.
STOPPED_LEAD_SPEED_MS = 1.5        # |v_ego + vRel| below this is stopped, not slow
STOPPED_LEAD_PERSISTENCE_S = 0.3   # enough to reject a single bad model frame, not much more
# Camera confirmation stands in for most of the range sweep.
#
# The sweep was written as if a radar return alone could reach here, but it cannot: radard's
# get_lead publishes nothing unless the DRIVING MODEL's lead probability clears 0.5, for both the
# radar-matched and vision-only paths. So every candidate has already been classified as a vehicle
# by the camera, and modelProb says how sure it is. A guardrail or gantry is not a high-confidence
# model lead -- that is the classifier's job, and it is better at it than a kinematic proxy.
# (The one path that skips the model, potential_low_speed_lead, needs v_ego near zero and
# dRel < 25 m and reports modelProb 0; MIN_V_EGO_MS and MIN_MODEL_PROB both exclude it.)
#
# So above CONFIDENT_MODEL_PROB on a stopped target, trade kinematic evidence for classifier
# evidence and require only enough sweep to show the range is closing at all. Not zero: a few
# meters costs ~0.14 s at 65 mph and is the only guard left against a model that latches onto a
# static structure and holds it -- the signature test_persistence_alone_does_not_trigger covers.
CONFIDENT_MODEL_PROB = 0.85
CONFIDENT_RANGE_SWEEP_M = 4.0
# Ford's own stated limit: ACC "may not detect stationary or slow moving vehicles below 6 mph
# (10 km/h)". Above this, Ford is tracking the lead and this detector must stay out of the way;
# below it, radar confirmation from openpilot's side means nothing because Ford is not acting on it.
FORD_ACC_MIN_TRACKED_SPEED_MS = 6 * CV.MPH_TO_MS
DEFAULT_MAX_TTC_S = 7.0    # fallback; tunable via IcbmLeadMaxTtc (tenths of a second)
MAX_V_REL_MS = -2.0        # genuinely closing, not sensor noise
MAX_D_PATH_M = 1.2         # in-path, not an adjacent lane or roadside return
MIN_V_EGO_MS = 25 * CV.MPH_TO_MS  # below this a floor request is meaningless
# Deceleration on AccBrkTot_A_Rq above which Ford counts as having taken the lead over. Matches
# ACC_DEADBAND in the onroad ACC pill so the readout and the release agree -- ACC trims constantly
# at small values, and without a deadband any noise would read as a takeover.
FORD_BRAKING_DECEL = 0.15  # m/s^2

# --- release gates ---
# Release margin above the trigger TTC. Relative, not absolute: the trigger is tunable, and a
# fixed release value would erase the hysteresis entirely once the two met.
RELEASE_TTC_MARGIN_S = 2.0
LEAD_LOST_S = 0.5          # candidate gone this long -> released

# --- model stop intent (stop signs, red lights) ---
#
# This is the only signal available for the case the lead trigger structurally cannot catch: a sign
# or signal with no vehicle at it produces no lead, so there is no dRel, vRel or TTC to gate on.
# Persistence and the speed floor are therefore the whole filter, which is why it is separately
# switchable via IcbmModelStopEnabled.
#
# NOT modelV2.action.shouldStop, which is what this used to gate on and is why it never once fired.
# shouldStop does not mean "there is a stop line ahead". Both branches of get_action_from_model in
# modeld require the car to be ALREADY STOPPED:
#
#   should_stop = (v_ego < 0.3 and desired_accel < 0.1)              # model_output has 'action'
#   should_stop = (v_now < vEgoStopping and a_target < 0.1)          # via get_accel_from_plan
#
# vEgoStopping is 0.3 m/s. So shouldStop can only be true below 0.67 mph, while this path requires
# MIN_V_EGO_MS (25 mph) to do anything -- mutually exclusive by a factor of thirty-seven. It means
# "stopped, stay stopped", and under experimental mode what actually slows the car for a red light
# is action.desiredAcceleration; shouldStop only decides the hold at the end.
#
# So gate on the deceleration the model is asking for, which is the same quantity _model_stop_target
# already builds the request from. With no lead and nothing on radar, a sustained request this hard
# is the model seeing something it wants to stop for.
MODEL_STOP_DECEL_MS2 = -1.0
# Hysteresis. Releasing at the trigger value would chatter on the way down, where the model's
# request naturally eases as the car slows.
MODEL_STOP_RELEASE_DECEL_MS2 = -0.4
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

    # model_should_stop is logging only -- see the capnp comment. It is kept because it is genuinely
    # informative at the END of a stop, but nothing may gate on it; see MODEL_STOP_DECEL_MS2.
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
    self.max_lead_distance = 180
    self.max_ttc = DEFAULT_MAX_TTC_S
    self.model_stop_enabled = False

  def update_params(self) -> None:
    if self.frame % int(PARAMS_UPDATE_PERIOD / DT_MDL) == 0:
      self.max_lead_distance = self.params.get("IcbmLeadMaxDistance", return_default=True)
      self.max_ttc = self.params.get("IcbmLeadMaxTtc", return_default=True) / 10.
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

  @staticmethod
  def _ford_is_braking(sm) -> bool:
    """Is stock ACC actively asking for brakes right now?

    TWO signals, not one, and the second is the fix for a real report: "Ford ACC was definitely
    braking for it, the warning was still on the screen."

    AccBrkDecel_B_Rq is a discrete flag. AccBrkTot_A_Rq -- confusingly named accAccelRequest here,
    but it is the BRAKE total -- is the deceleration Ford is actually asking for, in m/s^2. Ford can
    ask for real deceleration on the magnitude channel without setting the flag, and checking only
    the flag meant the detector kept warning while the car was visibly slowing for the same lead.
    The ACC pill has always used both (see hud_renderer_bp: accDecelRequest OR accAccelRequest below
    -ACC_DEADBAND), so the screen could read BRAKE while this said Ford had not taken over. Two
    readouts of the same fact disagreeing is worse than either being wrong.

    Precharge is deliberately NOT included, matching the pill: it pressurises the system without
    commanding meaningful deceleration, and treating it as a takeover would release the warning
    before anything had actually slowed.

    Defensive: carStateBP is BluePilot-conditional and absent on other platforms. Missing data
    means "cannot tell", which must read as not-braking so the detector keeps working.
    """
    try:
      if not sm.valid['carStateBP']:
        return False
      bls = sm['carStateBP'].brakeLightStatus
      if not bls.accDataAvailable:
        return False
      return bool(bls.accDecelRequest or bls.accAccelRequest < -FORD_BRAKING_DECEL)
    except (KeyError, AttributeError):
      return False

  @staticmethod
  def _ford_tracks(lead: log.RadarState.LeadData, v_ego: float) -> bool:
    """Will Ford's ACC actually follow this lead? Not the same question as "does radar see it".

    Conflating the two disabled this feature in precisely the case it exists for. openpilot reads
    the Delphi MRR's RAW detections (MRR_Detection_001..064), filtered only on validity and
    minimum range -- there is no stationary rejection anywhere in that path, so a stopped car does
    produce points, does cluster, and does arrive as a radar-confirmed lead. Ford's ACC module
    consumes the same sensor but applies its own Doppler filtering, and its manual states plainly
    that ACC "may not detect stationary or slow moving vehicles below 6 mph (10 km/h)".
    Suppressing zero-Doppler returns is standard practice: otherwise signs, guardrails and
    overhead structures trigger phantom braking.

    So radar confirmation only means "hands off" while the lead is moving fast enough for Ford to
    track it. Below that, Ford is going to drive into it.
    """
    return bool(lead.status and lead.radar and
                abs(v_ego + lead.vRel) > FORD_ACC_MIN_TRACKED_SPEED_MS)

  def _candidate(self, lead: log.RadarState.LeadData, v_ego: float, brake_pressed: bool) -> bool:
    """Frame-level gates. Persistence and range sweep are accumulated by the caller."""
    if not lead.status:
      return False
    if self._ford_tracks(lead, v_ego):
      return False  # Ford ACC is following this one itself; leave it alone
    if lead.modelProb < MIN_MODEL_PROB:
      return False
    if lead.vRel > MAX_V_REL_MS:
      return False
    if abs(lead.dPath) > MAX_D_PATH_M:
      return False
    # Ford ACC deals with close leads perfectly well. This exists for the distant stopped car, so
    # the far bound is a sanity limit -- but at the shipped defaults it is NOT a dormant one.
    #
    # Against a stopped lead TTC = dRel / v_ego, so IcbmLeadMaxTtc (7.0 s) allows 203 m at 65 mph
    # while this cap allows 180 m: above roughly 57 mph THIS is the gate that binds, and raising
    # the TTC alone changes nothing at highway speed. Below 57 mph the TTC binds instead. An
    # earlier version of this comment assumed a 4 s TTC and concluded the opposite.
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
      # Same signal as the trigger, with hysteresis. This used to read model_should_stop, which is
      # false at every speed this path can run at -- so the clear timer ran the moment it triggered
      # and would have released it half a second later even if the trigger had ever fired.
      if self.model_desired_accel <= MODEL_STOP_RELEASE_DECEL_MS2:
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
      # The driver braking ends it immediately. This is the alert doing its job -- it exists to buy
      # reaction time, and once the driver reacts there is nothing left to warn about. Continuing
      # to shout at someone already on the pedal is the fastest way to teach them to ignore it.
      #
      # It did stop before, but only indirectly: braking cancels ACC, long_enabled goes false a
      # frame or two later, and the reset above catches it. Depending on cruise state to propagate
      # is a poor way to silence an alarm, and it would fail outright on any brake press that did
      # not disengage.
      if CS.brakePressed:
        self._release()
        return

      # The good outcome: the deceleration bought a radar detection Ford will actually follow.
      #
      # Two ways that happens, and only having the first was a bug. _ford_tracks requires the lead
      # to be MOVING above 6 mph, which a stopped car never is -- so for the exact case this
      # feature exists for, the release was unreachable. It stayed active indefinitely, re-raising
      # its alert every cycle while Ford was visibly handling the car itself. Reported as "it
      # never confirms the lead so it keeps yelling at me".
      #
      # The second way: we have slowed to ACC's floor with the lead radar-confirmed. Below ~20 mph
      # Ford is in its stop-and-go regime and does follow stationary vehicles -- which the owner
      # observed directly, ACC stopping for the car. At that point this detector has done
      # everything it can do anyway; its whole output is a set-speed floor it has now reached.
      # Third and most direct: stock ACC is asking for brakes. Reported from the road -- the alert
      # kept firing while Ford was plainly slowing for the same car, still well above the floor, so
      # neither of the conditions below had fired yet. If Ford is braking, this detector has
      # nothing left to contribute and should get out of the way.
      radar_has_it = bool(lead.status and lead.radar)
      ford_took_over = (self._ford_is_braking(sm)
                        or self._ford_tracks(lead, v_ego)
                        or (radar_has_it and v_ego <= ACC_FLOOR_MS))
      if ford_took_over:
        # Ford ACC owns it now and can follow to a full stop, which this never could.
        self._release()
        return

      if not lead.status:
        self._lost_s += DT_MDL
        if self._lost_s >= LEAD_LOST_S:
          self._release()
          return
      else:
        self._lost_s = 0.0

      if self.ttc > self.max_ttc + RELEASE_TTC_MARGIN_S:
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
      # Same distinction as the lead path: a stationary radar return does not mean Ford is on it.
      radar_has_it = self._ford_tracks(lead, v_ego)
      model_candidate = (self.model_wants_to_stop and not radar_has_it and
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

    # A stopped lead needs less persistence, and a stopped lead the camera is confident about
    # needs far less sweep -- but every lead still has to prove the range is closing.
    stopped = abs(v_ego + lead.vRel) <= STOPPED_LEAD_SPEED_MS
    confident = stopped and lead.modelProb >= CONFIDENT_MODEL_PROB
    needed_persistence = STOPPED_LEAD_PERSISTENCE_S if stopped else MIN_PERSISTENCE_S
    needed_sweep = CONFIDENT_RANGE_SWEEP_M if confident else MIN_RANGE_SWEEP_M
    swept = self._sweep_start_d_rel - lead.dRel
    if (self._persistence_s >= needed_persistence and swept >= needed_sweep
        and self.ttc <= self.max_ttc):
      self.state = State.active
      self.trigger = Trigger.visionLead
      self.restore_set_speed = v_cruise_cluster
      self.v_target = max(float(v_desired_trajectory[-1]), ACC_FLOOR_MS)
      self._lost_s = 0.0
      # Alert at trigger, not at the floor: the whole deceleration is the driver's reaction time.
      events_sp.add(EventNameSP.unconfirmedLeadBraking)

  @property
  def model_wants_to_stop(self) -> bool:
    """The model asking for real deceleration. See MODEL_STOP_DECEL_MS2 for why this is not
    modelV2.action.shouldStop."""
    return self.model_desired_accel <= MODEL_STOP_DECEL_MS2

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
