"""
BluePilot: passing-assist observation. PHASE 1 -- LOG ONLY.

Nothing here alerts, steers, touches the set speed, or feeds any controller. The single output is
a message on longitudinalPlanSP describing what the system WOULD have suggested and, more usefully,
which gate stopped it. It exists to answer three questions that cannot be settled by reading code.

Why this shape at all
---------------------
openpilot cannot initiate a lane change on Ford: the turn signal is the driver's intent signal and
desire_helper gates on carState.leftBlinker/rightBlinker, which come from the SCCM's own
Steering_Data_FD1 on bus 0. So the reachable design is advisory -- tell the driver which side is
clear, they flick the blinker, and the existing AutoLaneChangeController takes it from there.

The three unknowns
------------------
1. ONCOMING TRAFFIC. This is the one that decides whether the idea survives. modelV2 publishes lane
   geometry, not direction of travel. On a two-lane undivided road, the lane to the left is
   oncoming traffic, and it looks exactly like a passing lane to every test below. Map data cannot
   currently help: LiveMapDataSP carries speed limit fields and roadName, with no `oneway` or
   `lanes` tag plumbed through mapd. So this phase measures how often the geometry test fires
   where it must not, and whether either evidence channel discriminates.

2. TSR OVERTAKING. Traffic_RecognitnData carries a latched no-overtaking zone state with its own
   confidence channel. If this market's camera populates it, it is a sound VETO. It is not a
   permit: absence of a no-passing sign says nothing about whether the left lane is same-direction,
   since those zones are only ever marked on undivided roads in the first place.

3. BLIS. carState.leftBlindspot is SodDetct*_D_Stat != 0 -- blind-spot OCCUPANCY. A vehicle closing
   from 150 m back does not light it until already alongside, which is far too late to base a
   passing suggestion on. Recorded here so its behaviour at decision time can be compared against
   what a safe gap actually looked like.

Thresholds are starting values, not derived constants. Refit them from logs; that is the point.
"""

from cereal import custom
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot import PARAMS_UPDATE_PERIOD
from openpilot.sunnypilot.selfdrive.controls.lib.rear_approach import RearApproach

Side = custom.LongitudinalPlanSP.PassingAssist.Side
Blocked = custom.LongitudinalPlanSP.PassingAssist.Blocked
Reason = custom.LongitudinalPlanSP.PassingAssist.Reason
Trigger = custom.LongitudinalPlanSP.PassingAssist.Trigger

# --- lane line indices. modelV2 publishes exactly 4 lines and 2 road edges. ---
# y is negative to the left and positive to the right in this frame: ldw.py tests the left line
# against -(1.08 + CAMERA_OFFSET) and the right against +(1.08 - CAMERA_OFFSET), and
# lateral_curv_ext computes width as laneLines[2].y + (-laneLines[1].y).
LL_FAR_LEFT, LL_LEFT, LL_RIGHT, LL_FAR_RIGHT = 0, 1, 2, 3
RE_LEFT, RE_RIGHT = 0, 1

# --- road widening (exit / on-ramp detection) ---
# modelV2 publishes 33 points along X_IDXS = 192 * (i/32)^2, so index 4 is ~3 m and index 20 is
# ~75 m. Near is not index 0 because the very first point is noisiest; far is not the last because
# beyond ~100 m the road edge gets unreliable and every curve starts to look like a divergence.
WIDEN_NEAR_IDX, WIDEN_FAR_IDX = 4, 20
# Growth in the lane-line-to-road-edge gap that reads as the road opening up rather than a shoulder
# varying. Roughly two thirds of a lane: enough that a real off-ramp trips it well before the gore
# point, small enough that ordinary shoulder variation does not. Starting value -- fit from logs.
MAX_WIDENING_M = 2.5

# --- geometry gates ---
# Confidence that a painted line exists BEYOND ego's own lane line. Matches the 0.5 that ldw.py
# uses for "lane visible"; raised slightly because acting on it is a stronger claim than warning.
MIN_ADJACENT_LINE_PROB = 0.6
# Drivable width between ego's lane line and the road edge that counts as a real lane. A US lane
# is 3.7 m; a wide shoulder is under 3. Sitting between them is deliberate -- too low and every
# breakdown lane reads as passable.
MIN_LANE_WIDTH_M = 3.0
# Road edge measurements get unreliable at distance and in poor conditions. modelV2 publishes a
# per-edge std; above this the edge gap is not trusted and the side is reported unavailable.
MAX_ROAD_EDGE_STD = 0.5

# --- lead gates ---
MIN_V_EGO_MS = 40 * CV.MPH_TO_MS  # below this, passing is not the manoeuvre being considered
MAX_LEAD_D_PATH_M = 1.5           # in our lane, not an adjacent-lane return
# Far bound. Not "when does it hold us back" -- a slower car in our lane always will -- but "is
# this real": a lead at 250 m may exit, speed up, or turn out not to be there. Generous, because
# the whole point is to decide before any speed is given away.
MAX_LEAD_D_REL_M = 220.0

# The real knob, and the whole judgement: is that car slower than the speed I asked for. Two mph,
# not eight -- a driver who sets 80 and finds someone doing 78 in front of them is being slowed
# down, and "how far below my set speed" is the question, not "is it dramatically slower".
DEFAULT_MIN_DEFICIT_MPH = 2
# How long the slower lead must persist before suggesting. Short by design: waiting is the whole
# behaviour this exists to remove. Long enough only to reject a single bad frame of lead tracking.
DEFAULT_PERSISTENCE_S = 2

# --- the one question ---
# "Is there a vehicle in my lane slow enough to cost me speed?"
#
# There is no second question. Closing on a slower car and sitting behind one are the same
# situation at two moments: either we are about to brake for it or we already have. Splitting them
# produced a machine that waited in one branch for a condition the driver never lets happen.
#
# On stock Ford ACC the cost is concrete: ACC brakes for a lead we were always going to pass, then
# fuel is spent winning the speed back in the other lane. Deciding early avoids both halves, and
# whether a given suggestion actually beat ACC to it is recorded rather than assumed -- see
# accBrakingAtDecision, which is what `trigger` now reports.
#
# TTC is a sanity bound, not the trigger, and now a very loose one. At a small speed difference the
# closing rate is low, so TTC gets large fast: 80 vs 65 mph closes at 6.7 m/s, which puts a lead
# 200 m out at 30 s. A tight bound therefore silently blocked exactly the early decision this is
# built for. Distance (MAX_LEAD_D_REL_M) is the real limit; this only stops us reacting to
# something we would never actually reach.
DEFAULT_APPROACH_TTC_S = 60.0
# Below this closing rate the TTC figure is meaningless, so it simply is not applied. A lead we are
# already pacing has no closing rate and still counts -- that is the point.
MIN_APPROACH_CLOSING_MS = 1.0

# --- anti-weave ---
# After a pass is suggested, hold off suggesting the return for this long. Without it, a three-lane
# road with a slow left lane produces exactly the ping-ponging that makes a system feel unfinished:
# move left, find it no faster, get told to move right, repeat. A settle period does not need to
# know what the adjacent lane is doing -- it just refuses to reverse a decision it only just made.
DEFAULT_SETTLE_TIME_S = 20

# Returned when not closing, so callers can compare numerically without special-casing.
NO_TTC_S = 999.0

# --- keep right ---
# "Keep right except to pass" is the mirror of the passing question: nothing is holding us back and
# a lane exists to our right, so we should not be sitting out here. Deliberately slower to fire
# than the pass suggestion -- returning right is never urgent, and a short delay would nag on every
# brief gap in traffic while genuinely overtaking a line of cars.
DEFAULT_KEEP_RIGHT_DELAY_S = 10

# TsrOvtkMsgTxt_D_Rq. 0 Null, 1 OvertakingAllowed, 2-7 are all "Lim*" -- a limitation in force or
# its explicit cancellation. Only the cancel codes clear the zone; the rest mean restricted.
TSR_OVTK_CANCELLED = (4, 7)       # LimAllCancelled, LimForTrucksCancelled
TSR_OVTK_UNRESTRICTED = (0, 1) + TSR_OVTK_CANCELLED
# TsrOvtkStatMsgTxt_D_Rq. 2 = LimitReliable (the DBC spells it "LimitReiable"). Anything else is
# Null, LimitChanged or LimitOutdated -- not a basis for a veto.
TSR_OVTK_STATUS_RELIABLE = 2


class PassingAssistDetector:
  def __init__(self):
    self.suggestion = Side.none
    self.blocked_by = Blocked.disabled
    self.reason = Reason.none
    self.approach_seconds = 0.0
    self.keep_right_seconds = 0.0

    self.has_lead = False
    self.lead_d_rel = 0.0
    self.lead_v_lead = 0.0
    self.speed_deficit = 0.0
    self.lead_ttc = 0.0
    self.approach_seconds = 0.0
    self.trigger = Trigger.none
    self.acc_braking_at_decision = False
    self.acc_braking_available = False
    self.suspended_seconds = 0.0

    self.left_line_prob = 0.0
    self.right_line_prob = 0.0
    self.left_edge_gap = 0.0
    self.right_edge_gap = 0.0
    self.left_geometry_ok = False
    self.right_geometry_ok = False
    self.lane_beyond_right = False
    self.right_widening_m = 0.0
    self.right_widening = False

    self.left_blindspot = False
    self.right_blindspot = False
    self.blindspot_available = False

    self.overtake_restricted = False
    self.overtake_msg = 0
    self.overtake_status = 0
    self.tsr_available = False
    self.road_name = ""
    self.rear = RearApproach()

    self.params = Params()
    self.frame = 0
    self.enabled = True
    self.min_deficit_ms = DEFAULT_MIN_DEFICIT_MPH * CV.MPH_TO_MS
    self.persistence_s = float(DEFAULT_PERSISTENCE_S)
    self.keep_right_enabled = True
    self.keep_right_delay_s = float(DEFAULT_KEEP_RIGHT_DELAY_S)
    self.avoid_outermost = False
    self.settle_time_s = float(DEFAULT_SETTLE_TIME_S)
    self.suspend_minutes = 15
    # Starts settled: at boot we have not just passed anyone, and a fresh detector must not
    # spend its first settle period refusing to suggest a return.
    self._settle_s = 1e3
    self._right_bs_prev = False
    self.approach_ttc_s = DEFAULT_APPROACH_TTC_S

  def update_params(self) -> None:
    if self.frame % int(PARAMS_UPDATE_PERIOD / DT_MDL) == 0:
      self.enabled = self.params.get_bool("PassingAssistLogEnabled")
      self.min_deficit_ms = self.params.get("PassingAssistMinDeficit", return_default=True) * CV.MPH_TO_MS
      self.persistence_s = float(self.params.get("PassingAssistStuckTime", return_default=True))
      self.keep_right_enabled = self.params.get_bool("PassingAssistKeepRight")
      self.keep_right_delay_s = float(self.params.get("PassingAssistKeepRightDelay", return_default=True))
      self.avoid_outermost = self.params.get_bool("PassingAssistAvoidOutermost")
      self.settle_time_s = float(self.params.get("PassingAssistSettleTime", return_default=True))
      self.suspend_minutes = self.params.get("PassingAssistSuspendMinutes", return_default=True)
      self.approach_ttc_s = self.params.get("PassingAssistApproachTtc", return_default=True) / 10.

  def _reset_outputs(self, blocked: int) -> None:
    self.suggestion = Side.none
    self.blocked_by = blocked
    self.reason = Reason.none
    self.trigger = Trigger.none

  @staticmethod
  def _edge_gap(model, line_idx: int, edge_idx: int) -> float:
    """Drivable width between ego's lane line and the road edge on that side, in metres.

    Returned as a positive magnitude on both sides so the two are directly comparable. Uses y[0],
    the nearest point, because that is where the measurement is most reliable and because a lane
    that exists beside us now is what matters -- not one 50 m ahead.
    """
    try:
      line_y = model.laneLines[line_idx].y[0]
      edge_y = model.roadEdges[edge_idx].y[0]
    except (IndexError, AttributeError):
      return 0.0
    return abs(edge_y - line_y)

  def _road_widening(self, model, right_std: float) -> None:
    """Does the road open up to our right between here and ~75 m ahead?

    This is the cue a human uses to spot an off-ramp without reading the signs: a through lane runs
    parallel, an exit peels away. Measured as the growth in the gap between ego's right lane line
    and the right road edge, which cancels curvature -- both bend together through a corner, so
    only a genuine divergence shows up.

    It also fires on on-ramps, rest areas and truck pullouts. That is correct rather than a false
    positive: none of them is somewhere to move over into.

    Reported even when the edge is untrusted, so a log can show whether the measurement or the
    threshold is what needs work.
    """
    self.right_widening_m = 0.0
    self.right_widening = False
    if right_std > MAX_ROAD_EDGE_STD:
      return
    try:
      line = model.laneLines[LL_RIGHT].y
      edge = model.roadEdges[RE_RIGHT].y
      if len(line) <= WIDEN_FAR_IDX or len(edge) <= WIDEN_FAR_IDX:
        return
      near = float(edge[WIDEN_NEAR_IDX]) - float(line[WIDEN_NEAR_IDX])
      far = float(edge[WIDEN_FAR_IDX]) - float(line[WIDEN_FAR_IDX])
    except (IndexError, AttributeError, TypeError):
      return

    # Only growth counts. The road narrowing ahead is a lane ending, which the availability test
    # already handles, and treating it as a divergence would double-count it.
    self.right_widening_m = max(0.0, far - near)
    self.right_widening = self.right_widening_m > MAX_WIDENING_M

  def _geometry(self, model) -> None:
    """Evaluate whether a lane exists either side, recording both evidence channels separately.

    They are NOT redundant and are deliberately not combined into one score:
      - lineProb asks "is there paint beyond my lane line" -- present on a multi-lane road, but
        equally present for the oncoming lane of an undivided road.
      - edgeGap asks "is there drivable width out to the road edge" -- collapses to a shoulder when
        we are already in the outermost lane, which is the case lineProb handles badly.
    Which one discriminates better, and whether either separates divided from undivided, is the
    open question this phase exists to answer.
    """
    probs = model.laneLineProbs
    stds = model.roadEdgeStds

    self.left_line_prob = float(probs[LL_FAR_LEFT]) if len(probs) > LL_FAR_LEFT else 0.0
    self.right_line_prob = float(probs[LL_FAR_RIGHT]) if len(probs) > LL_FAR_RIGHT else 0.0
    self.left_edge_gap = self._edge_gap(model, LL_LEFT, RE_LEFT)
    self.right_edge_gap = self._edge_gap(model, LL_RIGHT, RE_RIGHT)

    left_std = float(stds[RE_LEFT]) if len(stds) > RE_LEFT else 1e3
    right_std = float(stds[RE_RIGHT]) if len(stds) > RE_RIGHT else 1e3

    # Both channels must agree before a side is called available. Requiring agreement is the
    # conservative reading and keeps phase 2 honest if this ever stops being log-only.
    # BluePilot: is there another lane beyond the one to our right? Exit and merge lanes are
    # always the outermost, so a target lane with a further lane outboard of it cannot be one.
    # Measured from the far-right lane line (laneLines[3]) out to the right road edge: on a
    # three-lane road that gap is another lane, and on a two-lane road it collapses to the
    # shoulder. This is what makes "move right" safe from exits without any map data.
    self._road_widening(model, right_std)

    beyond_gap = self._edge_gap(model, LL_FAR_RIGHT, RE_RIGHT)
    self.lane_beyond_right = (float(probs[LL_FAR_RIGHT]) >= MIN_ADJACENT_LINE_PROB and
                              beyond_gap >= MIN_LANE_WIDTH_M and right_std <= MAX_ROAD_EDGE_STD)

    self.left_geometry_ok = (self.left_line_prob >= MIN_ADJACENT_LINE_PROB and
                             self.left_edge_gap >= MIN_LANE_WIDTH_M and
                             left_std <= MAX_ROAD_EDGE_STD)
    self.right_geometry_ok = (self.right_line_prob >= MIN_ADJACENT_LINE_PROB and
                              self.right_edge_gap >= MIN_LANE_WIDTH_M and
                              right_std <= MAX_ROAD_EDGE_STD)

  def _update_suspend(self) -> None:
    """Consume a tap and run the countdown.

    The request arrives as a one-shot param the UI sets and this clears, rather than a param the UI
    holds -- so the timing lives here where DT_MDL is, and the UI cannot leave the system off by
    crashing mid-suspend. A second tap while suspended cancels it, because the same control turning
    a thing off and back on is the only one that can be operated without looking.
    """
    try:
      requested = self.params.get_bool("PassingAssistSuspend")
    except (AttributeError, TypeError):
      return
    if requested:
      self.params.put_bool("PassingAssistSuspend", False)
      # Toggle: tapping while suspended resumes immediately.
      self.suspended_seconds = 0.0 if self.suspended_seconds > 0 else self.suspend_minutes * 60.0
      return

    if self.suspended_seconds > 0:
      self.suspended_seconds = max(0.0, self.suspended_seconds - DT_MDL)

  def _acc_braking(self, car_state_bp) -> None:
    """Is Ford's ACC already asking for brakes?

    The quality metric for the preemptive path. A suggestion made while this is False is one that
    could have avoided the deceleration entirely; made while True, ACC has already started paying
    for the lead and the pass is only recovering. Recorded rather than acted on -- the threshold
    that would beat ACC consistently has to be fitted from drives, not guessed.
    """
    self.acc_braking_at_decision = False
    self.acc_braking_available = False
    if car_state_bp is None:
      return
    bls = getattr(car_state_bp, 'brakeLightStatus', None)
    if bls is None or not bls.accDataAvailable:
      return
    self.acc_braking_available = True
    self.acc_braking_at_decision = bool(bls.accDecelRequest or bls.accPrechargeRequest)

  def _blindspot(self, car_state_bp) -> None:
    """Is BLIS actually reporting, as opposed to silently reading 'clear' because it is absent?

    Critical to record: carState.leftBlindspot defaults False, so an unavailable sensor is
    indistinguishable from a clear lane at the point of decision. Without this flag every logged
    suggestion from before the canbox lands would look blind-spot-checked when it was not.
    """
    self.blindspot_available = False
    if car_state_bp is None:
      return
    left = getattr(car_state_bp, 'blisLeft', None)
    right = getattr(car_state_bp, 'blisRight', None)
    self.blindspot_available = bool((left is not None and left.dataAvailable) or
                                    (right is not None and right.dataAvailable))

  def _traffic_signs(self, car_state_bp) -> None:
    """Read the TSR overtaking zone state.

    Restricted means: a limitation code is in force AND the camera says its own reading is
    reliable. Both halves matter -- LimitOutdated on a stale zone would otherwise veto passes for
    the rest of the drive.
    """
    self.overtake_restricted = False
    self.overtake_msg = 0
    self.overtake_status = 0
    self.tsr_available = False

    tsr = getattr(car_state_bp, 'trafficSignData', None)
    if tsr is None or not tsr.dataAvailable:
      return

    self.tsr_available = True
    self.overtake_msg = int(tsr.overtakeMsg)
    self.overtake_status = int(tsr.overtakeStatus)
    self.overtake_restricted = (self.overtake_msg not in TSR_OVTK_UNRESTRICTED and
                                self.overtake_status == TSR_OVTK_STATUS_RELIABLE)

  def _should_pass(self, lead, v_cruise: float) -> bool:
    """The one question: is there a vehicle in our lane slow enough to cost us speed?

    Deliberately does NOT ask whether we are closing on it or already behind it. Those are the same
    situation at two moments -- about to brake, or already braked -- and treating them separately is
    what made the old version wait for a state this driver never reaches.

    So: in our lane, slower than the SET speed by a margin worth the manoeuvre, near enough to be
    real. The margin is the judgement; everything else is a sanity bound.
    """
    if abs(lead.dPath) > MAX_LEAD_D_PATH_M or lead.dRel > MAX_LEAD_D_REL_M:
      self.approach_seconds = 0.0
      return False

    if self.speed_deficit < self.min_deficit_ms:
      self.approach_seconds = 0.0
      return False

    # TTC only bounds the case where we are actually catching something. A lead already being paced
    # has no meaningful closing rate and must still qualify -- it is the case where ACC has ALREADY
    # taken the speed, which is the outcome this exists to prevent, not a reason to stay silent.
    closing = -lead.vRel
    if closing >= MIN_APPROACH_CLOSING_MS and self.lead_ttc > self.approach_ttc_s:
      self.approach_seconds = 0.0
      return False

    self.approach_seconds += DT_MDL
    return self.approach_seconds >= self.persistence_s

  def _lead_state(self, lead, v_cruise: float) -> None:
    """Record what the lead is doing, whichever trigger ends up using it."""
    self.has_lead = bool(lead.status)
    self.lead_d_rel = float(lead.dRel)
    self.lead_v_lead = float(lead.vLead)
    self.speed_deficit = float(v_cruise - lead.vLead)
    closing = -float(lead.vRel)
    self.lead_ttc = (lead.dRel / closing) if closing > MIN_APPROACH_CLOSING_MS else NO_TTC_S

  def update(self, sm, v_cruise: float, long_enabled: bool) -> None:
    """
    Args:
      sm: SubMaster with carState, radarState, modelV2 and (BluePilot, Ford) carStateBP
      v_cruise: current set speed in m/s -- the speed we would be doing without this lead
      long_enabled: cruise engaged

    Publishes nothing itself; the planner copies the fields out. Gates are evaluated in order and
    the FIRST failure is recorded in blocked_by, so the log shows which one is actually binding
    rather than just that nothing happened.
    """
    self.update_params()
    self.frame += 1

    CS = sm['carState']
    lead = sm['radarState'].leadOne

    # The set speed is the number on YOUR dash, read straight from Ford's own Veh_V_DsplyCcSet via
    # cruiseState.speedCluster -- not carState.vCruiseCluster, which comes from VCruiseHelper and
    # depends on pcmCruise/pcmCruiseSpeed wiring that differs once ICBM is managing the target.
    # A lead 15 mph slower than an 80 mph set speed was reporting "nothing slower ahead" because the
    # number being differenced was not the number the driver set. Falls back to the passed value if
    # the cluster reports nothing.
    set_speed = float(CS.cruiseState.speedCluster)
    if set_speed <= 0:
      set_speed = v_cruise
    v_cruise = set_speed

    # BLIS is read every cycle regardless of the gates below -- its behaviour approaching a pass
    # is exactly what needs measuring, including on the frames where nothing is suggested.
    self.left_blindspot = bool(CS.leftBlindspot)
    self.right_blindspot = bool(CS.rightBlindspot)

    # carStateBP is BluePilot-and-Ford only. Availability comes from the message's own
    # dataAvailable flags rather than SubMaster liveness, because that is the flag that actually
    # answers the question: on this car BLIS stays unavailable until the canbox routes
    # Side_Detect_L/R_Stat from MS-CAN onto the bus openpilot reads.
    # Where we are, recorded with every decision. See the capnp comment: this is the candidate
    # divided-highway gate, logged before it is trusted.
    try:
      self.road_name = str(sm['liveMapDataSP'].roadName or "")
    except (KeyError, AttributeError):
      self.road_name = ""

    # NOT `if 'carStateBP' in sm`. SubMaster defines __getitem__ and no __contains__, so `in`
    # falls back to the old sequence-iteration protocol and calls sm[0] -- which raises
    # KeyError: 0 out of its internal dict. Catching the lookup is the only correct membership
    # test here, and it is what a plain dict in a test fixture will never tell you.
    try:
      car_state_bp = sm['carStateBP']
    except KeyError:
      car_state_bp = None
    self.rear.update(sm)
    self._blindspot(car_state_bp)
    self._acc_braking(car_state_bp)
    self._traffic_signs(car_state_bp)
    self._geometry(sm['modelV2'])

    # Advances every cycle regardless of the gates below, so it measures real elapsed time rather
    # than time-spent-in-a-particular-branch.
    self._settle_s = min(self._settle_s + DT_MDL, 1e3)  # capped; only the threshold matters

    self._update_suspend()
    if self.suspended_seconds > 0:
      # Suspended beats every other gate, including the ones that would report something more
      # specific. The driver has said "not here", and a panel reporting "no lane to move into"
      # while suspended would misrepresent why it is silent.
      self.approach_seconds = 0.0
      self.keep_right_seconds = 0.0
      self._reset_outputs(Blocked.suspended)
      return

    if not self.enabled:
      self.approach_seconds = 0.0
      self.keep_right_seconds = 0.0
      self._reset_outputs(Blocked.disabled)
      return

    if not long_enabled:
      self.approach_seconds = 0.0
      self.keep_right_seconds = 0.0
      self._reset_outputs(Blocked.notEngaged)
      return

    if CS.vEgo < MIN_V_EGO_MS:
      self.approach_seconds = 0.0
      self.keep_right_seconds = 0.0
      self._reset_outputs(Blocked.tooSlow)
      return

    # The driver is already doing something about it. Suggesting a pass mid-manoeuvre is noise,
    # and it would corrupt the stuck timer for the far more interesting no-input case.
    if CS.leftBlinker or CS.rightBlinker or CS.brakePressed or CS.steeringPressed:
      self.approach_seconds = 0.0
      self.keep_right_seconds = 0.0
      self._reset_outputs(Blocked.driverActive)
      return

    if not lead.status:
      self.approach_seconds = 0.0
      self.has_lead = False
      self.lead_ttc = NO_TTC_S
      self._reset_outputs(Blocked.noLead)
      self._keep_right()
      return

    self._lead_state(lead, v_cruise)

    if not self._should_pass(lead, v_cruise):
      self._reset_outputs(Blocked.notStuck)
      self._keep_right()
      return

    # trigger now reports the OUTCOME rather than the mechanism: did the suggestion land before
    # Ford's ACC started braking for a lead we were always going to pass, or after. That is the
    # only distinction worth recording, and it is measured rather than inferred.
    pending_trigger = Trigger.heldUp if self.acc_braking_at_decision else Trigger.approaching

    # Past here a pass is warranted, so we are not sitting in a lane we should be leaving.
    self.keep_right_seconds = 0.0

    if not (self.left_geometry_ok or self.right_geometry_ok):
      self._reset_outputs(Blocked.noLaneAvailable)
      return

    # TSR veto before the blind-spot check: a no-overtaking zone makes the blind spot irrelevant,
    # and ordering it this way means blockedBy distinguishes "would have been clear but the sign
    # said no" from "the sign was silent and BLIS stopped it".
    if self.overtake_restricted:
      self._reset_outputs(Blocked.overtakeRestricted)
      return

    # Rear approach. Sits here -- after geometry and the sign veto, before the side is chosen --
    # because it is per-side: a car closing on the left must not veto a pass on the right.
    #
    # An UNAVAILABLE side does not block. That is the honest behaviour while no rear sensor is
    # fitted (blocking would disable the feature outright and hide the real reason), and it is why
    # rearAvailable is published and shown: a suggestion made with no rear sensing must be legible
    # as such rather than pass for a checked one. When a source is fitted this becomes a real gate
    # with no code change here.
    left_ok = self.left_geometry_ok and not self.left_blindspot and not self.rear.left.blocks_lane_change
    right_ok = self.right_geometry_ok and not self.right_blindspot and not self.rear.right.blocks_lane_change

    if not (left_ok or right_ok):
      # Name rear approach only when it is what actually decided it -- otherwise the blind spot or
      # the geometry is the more useful thing to report.
      rear_blocked = ((self.left_geometry_ok and not self.left_blindspot and self.rear.left.blocks_lane_change) or
                      (self.right_geometry_ok and not self.right_blindspot and self.rear.right.blocks_lane_change))
      self._reset_outputs(Blocked.rearApproaching if rear_blocked else Blocked.blindspotOccupied)
      return

    # Left is preferred where both are available: passing on the right is the wrong default, and
    # on a divided highway the right side being "available" usually means a slower lane or an
    # exit-only lane rather than somewhere to pass.
    if left_ok:
      self.suggestion = Side.left
      self.blocked_by = Blocked.none
      self.reason = Reason.passing
      self.trigger = pending_trigger
      self._settle_s = 0.0
    else:
      self.suggestion = Side.right
      self.blocked_by = Blocked.none
      self.reason = Reason.passing
      self.trigger = pending_trigger
      self._settle_s = 0.0

  def _keep_right(self) -> None:
    """BluePilot: "keep right except to pass", the mirror of the passing question.

    Evaluated ONLY on the paths where no pass is warranted -- no lead, or a lead that is not
    holding us back. That ordering is the whole design: if a pass is on, we are out here for a
    reason and should not be told to move over mid-overtake.

    A lane existing to the right is the entire positive signal, and it is a decent one: on a
    two-lane-each-way highway, rightGeometryOk collapses to the shoulder once you ARE in the right
    lane, so the suggestion stops on its own without needing to know which lane we occupy.

    What this cannot see, and why it stays observation-only: an exit-only or merge lane is
    geometrically identical to a through lane, so "move right" could mean "take the exit". The
    same modelV2 limitation that cannot tell an oncoming lane from a passing lane applies here,
    and phase 1 exists to measure how often it bites.
    """
    # Do not reverse a pass we just suggested. This is what stops a three-lane road with a slow
    # left lane turning into a weave.
    if self._settle_s < self.settle_time_s:
      self.keep_right_seconds = 0.0
      return

    if not self.keep_right_enabled or not self.right_geometry_ok:
      self.keep_right_seconds = 0.0
      return

    # Never suggest moving into the OUTERMOST lane. Exit-only and merge lanes are always outermost
    # and are geometrically identical to a through lane, so requiring a further lane beyond the
    # target removes the "move right" -> "take the exit" failure entirely.
    #
    # The cost is real and worth stating: on a two-lane-each-way road the right lane IS the
    # outermost, so keep-right never fires there -- which is most of an interstate outside cities.
    # That is the deliberate trade. A suggestion that is silent where it cannot be sure beats one
    # that is occasionally confidently wrong about an exit.
    # The road opening up ahead means an exit, on-ramp or pullout, and none of those is a lane to
    # settle into. Unlike the outermost rule this works on a two-lane road, because it asks what
    # the lane DOES rather than merely whether another lane exists beyond it.
    if self.right_widening:
      self.keep_right_seconds = 0.0
      return

    if self.avoid_outermost and not self.lane_beyond_right:
      self.keep_right_seconds = 0.0
      return

    # Blind spot is a hard gate here, unlike geometry: moving into an occupied lane is the failure
    # mode, and returning right is never urgent enough to justify acting on stale evidence.
    # Resetting here is what makes the delay below mean "time since the blind spot went clear"
    # rather than "time since a lane appeared". That is the driver's own cue -- wait for the lamp
    # to go out -- and the delay on top of it lands nearer the textbook "both headlights in the
    # mirror", which is a little later. One timer, not two: an extra margin stage before this one
    # would double-count the same wait.
    if self.right_blindspot or self.rear.right.blocks_lane_change:
      self.keep_right_seconds = 0.0
      return

    self.keep_right_seconds += DT_MDL
    if self.keep_right_seconds >= self.keep_right_delay_s:
      self.suggestion = Side.right
      self.blocked_by = Blocked.none
      self.reason = Reason.keepRight
