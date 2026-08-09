"""
FusionPilot: radar-blind stopped-lead detection for Ford stock ACC.

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

Target speed is Ford's ACC floor, asked for the moment the lead is confirmed. This replaced pacing
the request along the MPC's plan, which sounds gentler and is not: the set speed is a REQUEST, not
a deceleration, and Ford's ACC brakes as hard as Ford's ACC brakes whatever number it is given.
Pacing only delayed the response, and it spent the distance that would have made the stop gentle --
reported from the road on 2026-08-06 as seeing the car early and still braking hard.

The consequence to keep in mind when reading the release conditions: because this asks for 20, Ford
starts braking to reach 20, so "is ACC braking" is evidence this detector MANUFACTURES. It is only
meaningful paired with a reason -- see the release block.

Thresholds here are starting values reviewed before first drive, not derived constants. The
range-sweep requirement is the main defense against bridge, overpass and guardrail false
positives, and along with the usable detection range it should be refitted from real drive logs.
"""

import math

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
# FusionPilot: a lead that is genuinely STOPPED is stronger evidence than one merely closing, and it
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
# How far above the requested set speed still counts as "at" it. ACC settles a little either side
# of a request rather than landing exactly on it, and the alternative to a margin here is the alert
# hanging on through the last 1-2 mph of every event.
FORD_FOLLOW_MARGIN_MS = 1.0 * CV.MPH_TO_MS

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
# The trigger is DEC's OWN slow-down detection, handed in by the planner. Dynamic Experimental
# Control already answers exactly this question -- "is the model planning to stop for something
# ahead" -- in order to decide when to switch to blended mode, and it has been answering it
# correctly on this car. Asking it beats inventing a second opinion that can drift from it.
#
# DEC compares the model's trajectory ENDPOINT (modelV2.position.x[32]) against how far the model
# ought to be seeing at the current speed (SLOW_DOWN_BP / SLOW_DOWN_DIST: 86 m at 30 km/h, 165 m at
# 60). A trajectory falling short means the model does not see the road continuing, which is what a
# red light or a stop sign looks like from the camera. It is also PREDICTIVE -- the trajectory
# shortens well before any deceleration request ramps up -- and it is already filtered, speed-scaled
# and hysteretic, so nothing here needs a threshold of its own.
#
# DEC computes it in _update_calculations, which runs unconditionally at the top of its update().
# So this is live whether or not DEC is enabled and whatever mode it has chosen.
#
# SHIPPED OFF as of 2026-08-06 -- and note what that does and does not mean. The owner turned this
# ON himself, so his stored value is HIS and the shipped default never reaches him. It worked on the
# road that same evening: it slowed for red lights, the paced ramp below felt right to him, and it
# produced two false positives. So the shipped-off default protects a device that has never had an
# opinion; it says nothing about his car. Read the default as a default, never as the live value.
#
# The original reason for shipping it off: On the road it fired almost
# continuously with no vehicle ahead and pulled the set speed to 20 every time, and he turned the
# feature off. DEC's slow-down is the right question asked at the wrong sensitivity: DEC uses it to
# pick blended mode, which is harmless when it is wrong, and a shortened trajectory happens for
# traffic, crests, dips and curves as readily as for a stop line.
#
# Two triggers have now been tried and both were wrong -- shouldStop could never fire, DEC's
# slow-down fires constantly. A third guess is not the answer. What this needs is a drive log with
# the trajectory endpoint, urgency and desiredAcceleration recorded through actual red lights and
# actual open road, so the separation can be measured instead of assumed. Until then the code stays
# and the default stays off.
# Short on purpose. DEC's urgency is already Kalman-filtered over a 5-sample window with a 0.85
# smoothing factor, so a full second of persistence on top is double-filtering -- and it is paid in
# distance at exactly the moment distance is the scarce thing: at 65 mph one second is 29 m out of
# the ~155 m available. This is only here to reject a single-frame glitch.
MODEL_STOP_PERSISTENCE_S = 0.3
MODEL_STOP_RELEASE_S = 0.5
# Horizon used to turn the model's desired acceleration into a set-speed target. Matches SCC-V's
# _NO_OVERSHOOT_TIME_HORIZON so the two produce comparably paced requests.
MODEL_STOP_HORIZON_S = 4.0
# Below this the geometry term is nonsense -- v^2/2d explodes and the request would slam to the
# floor on a near-zero endpoint. The acceleration term still applies there, which is the half of
# _model_stop_target that is any good close in.
MIN_STOP_DISTANCE_M = 5.0
# Aim to be stopped SHORT of the lead, not at it. dRel is measured to the object.
LEAD_STANDOFF_M = 5.0
# Above this required deceleration there is no room left to pace: ask for the floor at once.
#
# His objection, and the numbers back it: "it detects stopped cars late all the time and stopped
# cars can be on roads faster than roads with traffic lights". A stopped car at 180 m demands
# 3.55 m/s^2 at 80 mph and 2.35 at 65 -- at or past what Ford's ACC will deliver, so every frame
# spent easing into it is a frame wasted. At 45 mph the same 180 m demands 1.12, which is below the
# threshold that even lights the brake lamps, and there the paced ramp is free.
#
# 2.0 m/s^2 is Ford ACC's comfortable working limit; 1.3 is where UN R13-H lights the stop lamps and
# 3.5 is about all it has. So the split is physics, not a speed threshold -- highways go straight to
# the floor, arterials pace and tighten, and nothing has to know which road it is on.
URGENT_DECEL_MS2 = 2.0


class UnconfirmedLeadDetector:
  def __init__(self):
    self.state = State.inactive
    self.v_target = 0.0
    self.restore_set_speed = 0.0
    self.d_rel = 0.0
    self.ttc = 0.0

    # DEC's answer to "is the model planning to stop for something ahead", supplied by the planner,
    # and how far ahead it expects to be stopped.
    self.model_slow_down = False
    self.model_stop_distance = float('inf')
    # model_should_stop is logging only -- see the capnp comment. It is kept because it is genuinely
    # informative at the END of a stop, but nothing may gate on it: it is false at every speed this
    # path can run at. See the model stop intent block above.
    self.model_should_stop = False
    self.model_desired_accel = 0.0
    self.has_lead = False
    self.trigger = Trigger.none

    self._persistence_s = 0.0
    self._lost_s = 0.0
    self._sweep_start_d_rel = 0.0
    self._model_stop_s = 0.0
    self._model_stop_floor = float('inf')
    self._model_clear_s = 0.0

    self.params = Params()
    self.frame = 0
    self.max_lead_distance = 180
    self.max_ttc = DEFAULT_MAX_TTC_S
    self.model_stop_enabled = False
    self.model_stop_min_decel = 1.0

  def update_params(self) -> None:
    if self.frame % int(PARAMS_UPDATE_PERIOD / DT_MDL) == 0:
      self.max_lead_distance = self.params.get("IcbmLeadMaxDistance", return_default=True)
      self.max_ttc = self.params.get("IcbmLeadMaxTtc", return_default=True) / 10.
      self.model_stop_enabled = self.params.get_bool("IcbmModelStopEnabled")
      self.model_stop_min_decel = self.params.get("IcbmModelStopMinDecel", return_default=True) / 10.

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

    Defensive: carStateBP is FusionPilot-conditional and absent on other platforms. Missing data
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
    self._model_stop_floor = float('inf')
    self.trigger = Trigger.none
    if self.restore_set_speed > 0:
      self.state = State.restoring
    else:
      self.state = State.inactive

  def update(self, sm, v_desired_trajectory, v_cruise_cluster: float, long_enabled: bool,
             events_sp: EventsSP, model_slow_down: bool = False,
             model_stop_distance: float = float('inf')) -> None:
    """
    Args:
      sm: SubMaster with radarState and carState
      v_desired_trajectory: the stock planner's MPC speed plan (m/s). Already accounts for the
        vision-only lead, so it is the deceleration curve we want.
      v_cruise_cluster: current set speed (m/s), captured as the restore point on trigger.
      long_enabled: cruise engaged and under our control
      events_sp: alert sink
      model_slow_down: DEC's slow-down detection for this frame -- the model's trajectory falling
        short of what it should see at this speed. The stop-sign / red-light trigger; see the model
        stop intent block above. Defaults False so a caller that does not supply it simply never
        fires the model path, rather than firing it on stale state.
      model_stop_distance: DEC's trajectory endpoint (m) -- roughly where the model expects to be
        stopped. Paces the request; see _model_stop_target. Defaults inf, which falls back to the
        acceleration estimate alone.
    """
    self.update_params()
    self.frame += 1

    CS = sm['carState']
    lead = sm['radarState'].leadOne
    v_ego = CS.vEgo

    # Only from a frame that actually carries a lead. Same dropped-frame hazard as the request
    # below: radard publishes {"status": False} with every other field at its capnp default, so a
    # blink reads dRel == 0. This pair is not just diagnostics -- d_rel is published as
    # unconfirmedLead.dRel and is the number the ALERT shows the driver, so an ungated assignment
    # flashes "Vision only at 0 ft" during the blink, at the moment he is most likely reading it.
    # Holding the request but not the distance fixes half of one bug.
    #
    # ttc rides along for the same reason: _ttc(0, 0) is 0, which silently satisfies the TTC release
    # check below rather than failing it. That happens to be the outcome we want during a blink, and
    # a correct behavior resting on a degenerate input is one edit away from becoming a bug.
    #
    # The hold is scoped to ACTIVE rather than applied everywhere, so these do not go stale. Once
    # the grace window releases within LEAD_LOST_S, the next frame takes the real value again --
    # zero when there is genuinely nothing there, which is what the logs should say.
    if lead.status or self.state != State.active:
      self.d_rel = lead.dRel
      self.ttc = self._ttc(lead.dRel, lead.vRel)

    # Diagnostics for the stop-sign / red-light question. Logged unconditionally, including while
    # this detector is inactive, because the interesting case is exactly when there is no lead.
    self.model_slow_down = bool(model_slow_down)
    self.model_stop_distance = float(model_stop_distance)
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
      # A LEAD APPEARING ENDS THIS PATH, because the entry condition has to hold for as long as the
      # path runs and nothing was maintaining it.
      #
      # Reported 2026-08-08: "it said stopping for a red light even though there was a car in front
      # of me. Yeah sure it was a red light, but there was a car in front of me stopped there
      # already." The trigger is correct and fired legitimately -- at the range where the model
      # first sees the light, the queued car is often still outside radar acquisition, so there
      # genuinely is no lead. Then it resolves as he closes, and the three release conditions below
      # are the model letting go, dropping under the floor, and the brake. None of them is
      # "something is in the way now", so it kept announcing an empty intersection at a car.
      #
      # Releasing outright would be wrong in the case that matters: _release() routes to RESTORING
      # when a set speed is stored, which RAISES the set speed -- toward a stopped car. So this
      # splits on who is going to do the braking.
      if lead.status:
        if self._ford_tracks(lead, v_ego):
          self._release()      # moving fast enough for Ford's ACC to follow; it owns this now
          return
        # Ford will drive into it, which is the radar-blind lead case exactly. Same request, handed
        # to the path that is about that, so the alert names a VEHICLE instead of a sign. No
        # evidence sweep needed: model-stop persistence has already run, and a radar return the
        # model also predicted is strictly more evidence than either alone.
        self.trigger = Trigger.visionLead
        self._lost_s = 0.0
        self.v_target = self._lead_target(v_ego, lead.dRel)
        events_sp.add(EventNameSP.unconfirmedLeadBraking)
        return
      # Same signal as the trigger. DEC's filter carries its own hysteresis, so there is nothing to
      # add here. This used to read model_should_stop, which is false at every speed this path can
      # run at -- so the clear timer ran from the moment it triggered and would have released it
      # half a second later even if the trigger had ever fired.
      if self.model_slow_down:
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

      # RATCHET DOWN ONLY. Reported 2026-08-08: "it only ever got down to 28 and almost started
      # going up before I hit the brakes... went down to 28 and kind of fluctuated there."
      #
      # _model_stop_target takes max(geometry, acceleration), and the acceleration term is
      # modelV2.action.desiredAcceleration, which is noisy frame to frame. So the request wanders,
      # and ICBM faithfully chases it back up -- the same failure the curve ceiling fixed, except
      # that ceiling keys on SCC-Vision being active and a red light is not a curve.
      #
      # Nothing about a stop justifies asking for MORE speed while still committed to it. If the
      # light goes green the model stops asking, model_slow_down clears, and the release above
      # handles it in half a second. Until then the request only goes down.
      target = self._model_stop_target(v_ego)
      self._model_stop_floor = min(self._model_stop_floor, target)
      self.v_target = self._model_stop_floor
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
      # "IS FORD BRAKING" IS NOT THE QUESTION. It looked like it, and it is wrong, and the owner is
      # the one who spotted why: this detector asks for 20 mph, so Ford brakes to REACH 20. Its own
      # request produces the evidence it was reading as "Ford has taken over". On its own that
      # releases moments after every trigger and hands the set speed back with the stopped car still
      # sitting there -- the exact opposite of the feature. Braking has to be paired with something
      # that says WHY Ford is braking.
      #
      # Two pairings, and between them they cover both of the shapes seen on the road:
      #
      #   1. Radar-confirmed lead AND ACC braking. Chasing a set speed does not coincide with the
      #      radar acquiring a target; the two together mean Ford is braking FOR something. This is
      #      the case reported on 2026-08-06 -- "Ford ACC had started braking because it saw the
      #      car, not just because it was going down to 20" -- where the alert kept firing while
      #      Ford was visibly handling it, well above the floor.
      #
      #   2. ACC braking while already at or below what we asked for. Past the request there is
      #      nothing left for ACC to chase, so continued braking can only be following. This is the
      #      stop-and-go regime below ~20 mph where Ford does follow stationary vehicles, which the
      #      owner observed directly.
      #
      # _ford_tracks stays as the third, independent way in: a lead moving fast enough that Ford
      # was always going to follow it, no braking evidence required.
      radar_has_it = bool(lead.status and lead.radar)
      ford_braking = self._ford_is_braking(sm)
      at_or_below_request = v_ego <= self.v_target + FORD_FOLLOW_MARGIN_MS
      ford_took_over = ((ford_braking and radar_has_it)
                        or (ford_braking and at_or_below_request)
                        # Third: at the floor with the radar holding it, Ford owns it whether or not
                        # it happens to be commanding decel this instant -- holding 20 behind a
                        # stopped car is following, not coasting. Dropping this term broke
                        # test_stopped_lead_releases_once_we_reach_the_acc_floor, which is the exact
                        # end state this feature is designed to reach.
                        or (radar_has_it and at_or_below_request)
                        or self._ford_tracks(lead, v_ego))
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

      # Recomputed every cycle as the range closes, so the request tightens on its own and reaches
      # the floor by ~90 m at 65 mph. It cannot drift back UP: dRel only shrinks while this is
      # active, and a lead that stops closing releases through the TTC margin instead.
      #
      # Only while the lead is actually there. radard publishes {"status": False} with every other
      # field left at its capnp default, so a single dropped frame inside the LEAD_LOST_S grace
      # window above arrives as dRel == 0 -- which _stopping_target reads as "too close to be
      # meaningful" and answers with v_ego. That is the request jumping from the floor back to 65 mph
      # for the frames the radar blinks, with the alert still up, and it is the one direction the
      # comment above promises cannot happen. Hold the last request instead.
      if lead.status:
        self.v_target = self._lead_target(v_ego, lead.dRel)
      # Re-raised every cycle, not once at trigger: this alert has to stay up for as long as the
      # driver is the only thing that can stop the car.
      events_sp.add(EventNameSP.unconfirmedLeadBraking)
      return

    # ---- model stop intent: the only signal for a sign or signal with no vehicle at it ----
    # Gated on `not candidate` so a real lead always takes precedence: if there is something to
    # see, the lead trigger's geometry filters are strictly better evidence than a shortened
    # trajectory, which cannot tell a stop line from a vehicle.
    if self.model_stop_enabled and not candidate:
      # ANY lead at all disqualifies this, not just one Ford is tracking.
      #
      # It used to ask _ford_tracks, which requires the lead to be moving above 6 mph. Cars queued
      # at a red light are not moving, so this fired on them -- on exactly the case the block header
      # says it does not handle: "a sign or signal with NO vehicle at it produces no lead". Reported
      # 2026-08-06: "I'm getting slowing for stop sign or traffic light when I'm coming up to
      # vehicles that are stopped at a stop sign or traffic light... cruise control is already doing
      # that because it sees the cars."
      #
      # If there is a vehicle ahead then the vehicle is the thing to react to, and either Ford's own
      # ACC or the lead path above owns it. This path is for the empty intersection, where there is
      # nothing to measure and the model's trajectory is the only evidence there is.
      # ...AND the stop has to actually need braking. DEC's slow-down flag is deliberately early --
      # that is why it was chosen over shouldStop, which can never fire at these speeds -- so
      # nothing downstream bounded how far out this acted. Measured 2026-08-08 on route 0000032c:
      # it fired at 34 mph with 193 m to run, which needs 0.60 m/s^2. That is gentler than coasting.
      #
      # Below IcbmModelStopMinDecel the car arrives in time by lifting off and a set-speed request
      # buys nothing, so this waits. inf endpoint (no trajectory reading) keeps the old behavior
      # rather than silently disabling the path.
      a_required = float('inf')
      if math.isfinite(self.model_stop_distance) and self.model_stop_distance > MIN_STOP_DISTANCE_M:
        a_required = v_ego * v_ego / (2. * self.model_stop_distance)
      model_candidate = (self.model_slow_down and not lead.status and
                         a_required >= self.model_stop_min_decel and
                         v_ego >= MIN_V_EGO_MS and not CS.brakePressed)
      if model_candidate:
        self._model_stop_s += DT_MDL
        if self._model_stop_s >= MODEL_STOP_PERSISTENCE_S:
          self.state = State.active
          self.trigger = Trigger.modelStop
          self.restore_set_speed = v_cruise_cluster
          self.v_target = self._model_stop_target(v_ego)
          self._model_stop_floor = self.v_target
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
      # STRAIGHT TO THE FLOOR, not the MPC's paced plan. Reported 2026-08-06: it saw a stopped car
      # well ahead, walked the set speed down along the plan, and the brake when it came was hard.
      # His reading, and it is right: Ford's ACC cannot brake harder than Ford's ACC brakes. The set
      # speed is a request, not a deceleration -- asking for 20 immediately hands the whole problem
      # to a system with its own bounded authority, and it arrives with the maximum distance in
      # hand. Pacing the request only ever delays the response; it never softens the stop.
      self.v_target = self._lead_target(v_ego, lead.dRel)
      self._lost_s = 0.0
      # Alert at trigger, not at the floor: the whole deceleration is the driver's reaction time.
      events_sp.add(EventNameSP.unconfirmedLeadBraking)

  @staticmethod
  def _stopping_target(v_ego: float, distance_m: float) -> float:
    """Set speed for a stop at `distance_m`: a = v^2/2d, applied over the horizon.

    Shared by both triggers because the geometry is the same question -- a stopped car and a stop
    line are both a fixed point you must not reach at speed. Floored at what Ford's ACC can hold and
    never above current speed. Returns v_ego (no request) for a distance too small to be meaningful,
    where v^2/2d explodes.
    """
    if not math.isfinite(distance_m) or distance_m <= MIN_STOP_DISTANCE_M:
      return v_ego

    # No margin left -- every frame spent easing in is a frame wasted. See URGENT_DECEL_MS2.
    a_required = v_ego * v_ego / (2. * distance_m)
    if a_required >= URGENT_DECEL_MS2:
      return ACC_FLOOR_MS

    return max(min(v_ego - a_required * MODEL_STOP_HORIZON_S, v_ego), ACC_FLOOR_MS)

  def _lead_target(self, v_ego: float, d_rel: float) -> float:
    """FusionPilot: pace the lead request by geometry rather than dropping straight to the floor.

    His call, 2026-08-06, after driving both. This is NOT a return to the MPC-plan pacing that
    caused the hard brake -- that was openpilot's follow planner, a comfort curve that does not know
    Ford is the actuator, and it dawdled. This asks what deceleration reaches zero at the car ahead,
    and the answer is a large request IMMEDIATELY: at 65 mph with a lead at 155 m it asks for
    40 mph, then 32 at 120 m, then the floor by 90 m.

    Dropping straight to 20 spent the whole reduction at first sight and then held 20 while still
    far away. Same braking authority where it matters, and a false positive -- a gantry, a bridge --
    costs a modest speed change instead of slamming to 20 on a motorway. Two false positives on the
    stop-sign path in one drive is what made that concrete, and he liked the paced feel there.

    The standoff exists because dRel is measured to the object, and stopping AT it is not the goal.
    """
    return self._stopping_target(v_ego, d_rel - LEAD_STANDOFF_M)

  def _model_stop_target(self, v_ego: float) -> float:
    """Set-speed target for a stop the radar cannot see.

    The MPC plan is no use here: with no lead, it plans normally and shows no deceleration at all,
    because in ACC mode the planner never consumes the model's stop intent.

    Two independent estimates, and the request is whichever asks for more, because they fail at
    opposite ends of the approach:

    GEOMETRY -- how hard you must decelerate to be stopped at the point where the model stops
    predicting road: a = v^2 / 2d, applied over the horizon. This is what makes the request early.
    The trigger is a trajectory that has shortened, and the trajectory shortens BEFORE the model
    starts asking to slow down, so at the moment of trigger the acceleration estimate below is
    typically still ~0 and on its own would command no change at all -- arriving late for exactly
    the reason DEC's signal was chosen for being early. At 65 mph with the endpoint at 155 m this
    asks for 41 mph immediately.

    ACCELERATION -- modelV2.action.desiredAcceleration projected over the same horizon. This is the
    one that keeps working once the endpoint is close and the geometry term saturates at the floor,
    and it is the better estimate of how hard the model actually intends to brake.

    Floored at what Ford's ACC can hold, and never allowed to request above current speed.
    """
    projected = v_ego + self.model_desired_accel * MODEL_STOP_HORIZON_S

    # inf when the trajectory was not full-length; small values are meaningless and would divide
    # into an enormous deceleration.
    projected = min(projected, self._stopping_target(v_ego, self.model_stop_distance))
    return max(min(projected, v_ego), ACC_FLOOR_MS)
