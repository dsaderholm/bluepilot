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
1. ONCOMING TRAFFIC -- ANSWERED, see adjacent_lane.py. This was the one that decided whether the
   idea survived. modelV2 publishes lane geometry, not direction of travel, so on a two-lane
   undivided road the lane to the left is oncoming traffic and looks exactly like a passing lane to
   every geometry test below. Map data could not help and still cannot on this build: mapd v1.12.0
   ships here and writes no oneway tag and no lane count. (mapd v2 publishes oneWay, lanes and
   highwayClass on a MapdOut message, which would make this a cross-check rather than the only
   source, whenever sunnypilot moves to it.)

   The front radar settles it directly. An oncoming vehicle's absolute ground speed is roughly
   minus its own, which nothing travelling our way and no roadside object can produce, and the
   lateral band excludes an opposing carriageway across a median for free. The veto is per side and
   held for a while after the last sighting; the reasoning is all in adjacent_lane.py.

   What is still worth measuring from a drive: how often it fires on a divided road it should not
   (undividedRoad and oncomingSeen are logged with every decision), and whether 90 s of memory is
   the right number for the roads actually driven.

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
from openpilot.sunnypilot.selfdrive.controls.lib.adjacent_lane import (
  AdjacentLane, path_offset, DEFAULT_ONCOMING_MEMORY_S,
)
from openpilot.sunnypilot.selfdrive.controls.lib.passing_manoeuvre import PassingManoeuvre
from openpilot.sunnypilot.selfdrive.controls.lib.rear_approach import RearApproach

Side = custom.LongitudinalPlanSP.PassingAssist.Side
Blocked = custom.LongitudinalPlanSP.PassingAssist.Blocked
Reason = custom.LongitudinalPlanSP.PassingAssist.Reason
Trigger = custom.LongitudinalPlanSP.PassingAssist.Trigger
RefSource = custom.LongitudinalPlanSP.PassingAssist.ReferenceSource

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
# In our lane, not an adjacent-lane return. Measured from the MODEL PATH, not from the car's
# straight-ahead axis, and computed here rather than read off radarState.
#
# This used to test lead.dPath, which is a dead field: nothing in openpilot has populated it since
# the LeadData struct was written, so it arrives as 0.0 and `abs(0.0) > 1.5` never rejected
# anything. The gate read as a filter and was not one. Found while checking how twilsonco's fork
# groups radar points into lanes -- its get_path_adjacent_leads uses dPath the same way, on an
# older openpilot where it was still real.
MAX_LEAD_D_PATH_M = 1.5

# The real knob, and the whole judgement: is that car slower than the speed I asked for. The
# question is "how far below my set speed", not "is it dramatically slower" -- which is why this is
# 4 and not the 8 it started at.
#
# Not 2, though, and the reason is measurement rather than taste: ordinary traffic varies by a mph
# or two. A driver holding 65 oscillates, another car's cruise hunts on grades, and vLead is a
# filtered estimate. At 2 mph the threshold sits inside that band, so it fires on cars that are not
# actually slower -- just momentarily varying. Four clears the noise and still catches the case
# this exists for.
#
# speedDeficit is logged on every decision, so this can be refitted from a drive instead of argued
# about.
DEFAULT_MIN_DEFICIT_MPH = 4
# How long the slower lead must persist before suggesting. Short by design: waiting is the whole
# behaviour this exists to remove. Long enough only to reject a single bad frame of lead tracking.
DEFAULT_PERSISTENCE_S = 2

# --- and why that timer is no longer destroyed by one bad frame ---
#
# Both of these come from the same report: "when a car is going in between the speed I want to pass
# at and the speed I don't. Same with it coming in and out of radar range."
#
# That was raised as a cosmetic complaint about the display, and half of it was. The other half was
# a real refusal to pass. Every failing frame used to zero the confirmation timer outright, so a
# vehicle sitting exactly on the threshold -- or blinking at the edge of radar range -- reset the
# clock faster than it could ever run, and NEVER produced a suggestion. Not late: never. The one
# case a passing aid most obviously exists for is a car only slightly slower than you.
#
# HYSTERESIS fixes the speed boundary. Once a vehicle has been judged slow enough to be worth
# passing it stays judged slow until it is a clear margin FASTER than the threshold, so a car
# hovering on the line is decided once instead of re-decided every frame. 1 mph is chosen against
# the same measurement noise that set the 4 mph threshold: ordinary traffic varies by about that
# much, so this is the width of the noise, not a second judgement.
DEFICIT_HYSTERESIS_MPH = 1

# A GRACE WINDOW then decay fixes the dropout, and the grace window is the part that matters.
#
# Decay alone was not enough, which is worth spelling out because it looked like it was. Decaying
# at 3x while accumulating at 1x means a track has to be present 75% of frames just to break even;
# below that the timer NEVER reaches the threshold however long the car sits there. That is exactly
# the reported failure -- "it would go in and out of range and so the pass would keep resetting" --
# so a fix that only slowed the resetting down had not fixed it at all.
#
# So a short gap now costs NOTHING. liveTracks arrives at ~8.3 Hz and the detector wants 3
# consecutive messages to re-confirm a vehicle, so 0.4 s covers a lost return and its recovery with
# margin. Only absence beyond that decays, and then quickly: from the 2 s cap, ~0.7 s of continued
# silence clears it, which is what a car genuinely leaving looks like.
LEAD_GAP_GRACE_S = 0.4
CONFIRM_DECAY_RATE = 3.0

# The same problem at the range boundary rather than the speed one. A lead sitting either side of
# the look-ahead distance would alternate in and out; once it is being tracked it may drift this
# much further out before it counts as gone.
RANGE_HYSTERESIS_M = 20.0

# Deliberately NOT applied to the deficit test: a lead that is genuinely faster than the release
# threshold is a changed situation, not a noisy one, and should reset properly.

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
# There is no time-based bound. TTC was one, and it was actively backwards: at a small speed
# difference the closing rate is low, so a fixed TTC translates into a SHORT distance. Three mph
# under closes at 1.3 m/s, which a 60 s bound turns into about 80 m -- the gentler the difference,
# the later it would notice, which is the opposite of deciding early.
#
# Distance is the honest limit, and it means what it says: how far ahead to look. Beyond the reach
# of lead tracking there is nothing to decide on anyway.
DEFAULT_MAX_DISTANCE_M = 220
# Kept only for the log -- how long until we reach this lead at the current closing rate. Nothing
# gates on it.
MIN_APPROACH_CLOSING_MS = 1.0
NO_TTC_S = 999.0

# --- what counts as "Ford ACC is already paying for this lead" ---
# Kept in sync with hud_renderer_bp.py, which derived these while building the ACC pill. Duplicated
# rather than imported because that is UI and this is controls; if one changes, change both.
#
# AccPrpl_A_Rq's floor is a "no request" sentinel, not a -5 m/s^2 demand -- opendbc sends
# INACTIVE_GAS = -5.0 whenever longitudinal is off or the request falls below MIN_GAS. Anything at
# or below this carries no information.
ACC_PROPULSION_INACTIVE = -4.5   # m/s^2
# Below this the propulsion request is real engine braking rather than trim around zero.
ACC_ENGINE_BRAKE_MS2 = -0.15

# --- anti-weave ---
# After a pass is suggested, hold off suggesting the return for this long. Without it, a three-lane
# road with a slow left lane produces exactly the ping-ponging that makes a system feel unfinished:
# move left, find it no faster, get told to move right, repeat. A settle period does not need to
# know what the adjacent lane is doing -- it just refuses to reverse a decision it only just made.
DEFAULT_SETTLE_TIME_S = 20


# --- keep right ---
# "Keep right except to pass" is the mirror of the passing question: nothing is holding us back and
# a lane exists to our right, so we should not be sitting out here. Deliberately slower to fire
# than the pass suggestion -- returning right is never urgent, and a short delay would nag on every
# brief gap in traffic while genuinely overtaking a line of cars.
DEFAULT_KEEP_RIGHT_DELAY_S = 10

# How long the lane to our right must have existed CONTINUOUSLY before moving into it is suggested.
#
# The owner's idea, and it is a better exit test than the two already here: an exit lane did not
# exist a moment ago and now does. A through lane has been beside us the whole time. So instead of
# asking what the lane looks like -- which is what MIN_LANE_WIDTH_M and the road-widening check do,
# and why they cannot tell an exit from a through lane -- ask how long it has been there.
#
# Complementary rather than a replacement. Road widening spots an exit OPENING UP AHEAD; this spots
# one that has JUST APPEARED beside us. They catch the same thing at different moments and both
# fail safe.
#
# Every way this is wrong is the harmless way. A lane the model briefly loses -- occluded by a
# lorry, faded paint, a shadow -- comes back looking new, and the cost is a quiet keep-right for a
# few seconds. Merging onto a highway also starts the clock late, which is correct: the lane really
# is new to us there.
#
# 15 s at 70 mph is about 470 m of continuous presence. An exit lane is rarely that long before the
# gore; a through lane has usually been there for minutes.
DEFAULT_MIN_LANE_AGE_S = 15

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
    # Latched by the hysteresis above: is the lead currently judged slow enough to be worth
    # passing. Latched rather than recomputed so the answer cannot chatter frame to frame.
    self.lead_is_slow = False
    self._lead_gap_s = 0.0
    # The side that is clear RIGHT NOW, before the confirmation timer has anything to say about it.
    # This is what lights the blinker; `suggestion` is what commits to moving.
    self.clear_side = Side.none

    self.has_lead = False
    self.lead_d_rel = 0.0
    self.lead_v_lead = 0.0
    self.speed_deficit = 0.0
    # See leadRadarConfirmed in custom.capnp. Recorded, not gated on.
    self.lead_radar_confirmed = False
    self.lead_model_prob = 0.0
    self.lead_ttc = 0.0
    self.lead_d_path = 0.0
    self.trigger = Trigger.none
    self.acc_braking_at_decision = False
    self.acc_precharge_at_decision = False
    self.acc_braking_available = False
    self.suspended_seconds = 0.0
    self.reference_speed = 0.0
    self.reference_source = RefSource.cluster

    self.left_line_prob = 0.0
    self.right_line_prob = 0.0
    self.left_edge_gap = 0.0
    self.right_edge_gap = 0.0
    self.left_geometry_ok = False
    self.right_geometry_ok = False
    self.right_widening_m = 0.0
    self.right_widening = False
    self.right_lane_age_s = 0.0

    self.left_blindspot = False
    self.right_blindspot = False
    self.blindspot_available = False

    self.overtake_restricted = False
    self.overtake_msg = 0
    self.overtake_status = 0
    self.tsr_available = False
    self.road_name = ""
    self.rear = RearApproach()
    self.adjacent = AdjacentLane()

    self.params = Params()
    self.frame = 0
    self.enabled = True
    self.min_deficit_ms = DEFAULT_MIN_DEFICIT_MPH * CV.MPH_TO_MS
    self.persistence_s = float(DEFAULT_PERSISTENCE_S)
    self.keep_right_enabled = True
    self.keep_right_delay_s = float(DEFAULT_KEEP_RIGHT_DELAY_S)
    self.min_lane_age_s = float(DEFAULT_MIN_LANE_AGE_S)
    self.adjacent_enabled = True
    self.oncoming_veto = True
    self.strict_two_way = True
    self.oncoming_memory_s = float(DEFAULT_ONCOMING_MEMORY_S)
    self.settle_time_s = float(DEFAULT_SETTLE_TIME_S)
    self.suspend_minutes = 15
    # Starts settled: at boot we have not just passed anyone, and a fresh detector must not
    # spend its first settle period refusing to suggest a return.
    self._settle_s = 1e3
    self._lka_prev = False
    self.max_distance_m = float(DEFAULT_MAX_DISTANCE_M)
    # The dry run: what a fully-automatic pass would be doing right now. Actuates nothing.
    self.manoeuvre = PassingManoeuvre()

  def update_params(self) -> None:
    if self.frame % int(PARAMS_UPDATE_PERIOD / DT_MDL) == 0:
      self.enabled = self.params.get_bool("PassingAssistLogEnabled")
      self.min_deficit_ms = self.params.get("PassingAssistMinDeficit", return_default=True) * CV.MPH_TO_MS
      self.persistence_s = float(self.params.get("PassingAssistConfirmTime", return_default=True))
      self.keep_right_enabled = self.params.get_bool("PassingAssistKeepRight")
      self.keep_right_delay_s = float(self.params.get("PassingAssistKeepRightDelay", return_default=True))
      self.min_lane_age_s = float(self.params.get("PassingAssistMinLaneAge", return_default=True))
      self.adjacent_enabled = self.params.get_bool("PassingAssistAdjacentLane")
      self.oncoming_veto = self.params.get_bool("PassingAssistOncomingVeto")
      self.strict_two_way = self.params.get_bool("PassingAssistStrictTwoWay")
      self.oncoming_memory_s = float(self.params.get("PassingAssistOncomingMemory", return_default=True))
      self.manoeuvre.blinker_lead_s = float(self.params.get("PassingAssistBlinkerLead", return_default=True))
      self.settle_time_s = float(self.params.get("PassingAssistSettleTime", return_default=True))
      self.suspend_minutes = self.params.get("PassingAssistSuspendMinutes", return_default=True)
      self.max_distance_m = float(self.params.get("PassingAssistMaxDistance", return_default=True))

  def _reset_outputs(self, blocked: int) -> None:
    self.clear_side = Side.none
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

    self.left_geometry_ok = (self.left_line_prob >= MIN_ADJACENT_LINE_PROB and
                             self.left_edge_gap >= MIN_LANE_WIDTH_M and
                             left_std <= MAX_ROAD_EDGE_STD)
    self.right_geometry_ok = (self.right_line_prob >= MIN_ADJACENT_LINE_PROB and
                              self.right_edge_gap >= MIN_LANE_WIDTH_M and
                              right_std <= MAX_ROAD_EDGE_STD)

    # How long that lane has been there WITHOUT INTERRUPTION. See DEFAULT_MIN_LANE_AGE_S: a lane
    # that did not exist a moment ago and now does is an exit or an on-ramp, and this is the only
    # test here that can tell that apart from a through lane -- every other one asks what the lane
    # looks like, and they look identical.
    if self.right_geometry_ok:
      self.right_lane_age_s = min(self.right_lane_age_s + DT_MDL, 1e3)
    else:
      self.right_lane_age_s = 0.0

  def _lka_toggle(self, car_state_bp) -> bool:
    """Rising edge of the stalk-end LKA button.

    A physical control for the thing most often wanted while driving. Edge-triggered, not level:
    the signal reads Pressed for as long as it is held, and a held button must be one request.
    """
    try:
      pressed = bool(car_state_bp.lkaButtonPressed) if car_state_bp is not None else False
    except AttributeError:
      pressed = False
    edge = pressed and not self._lka_prev
    self._lka_prev = pressed
    return edge

  def _update_suspend(self, lka_edge: bool = False) -> None:
    """Consume a tap and run the countdown.

    The request arrives as a one-shot param the UI sets and this clears, rather than a param the UI
    holds -- so the timing lives here where DT_MDL is, and the UI cannot leave the system off by
    crashing mid-suspend. A second tap while suspended cancels it, because the same control turning
    a thing off and back on is the only one that can be operated without looking.
    """
    try:
      requested = self.params.get_bool("PassingAssistSuspend")
    except (AttributeError, TypeError):
      requested = False
    # The stalk button and the panel tap are the same request by different routes.
    requested = requested or lka_edge
    if requested:
      try:
        self.params.put_bool("PassingAssistSuspend", False)
      except (AttributeError, TypeError):
        pass
      # Toggle: tapping while suspended resumes immediately.
      self.suspended_seconds = 0.0 if self.suspended_seconds > 0 else self.suspend_minutes * 60.0
      return

    if self.suspended_seconds > 0:
      self.suspended_seconds = max(0.0, self.suspended_seconds - DT_MDL)

  def _acc_braking(self, car_state_bp) -> None:
    """Is Ford's ACC already SLOWING THE CAR for this lead?

    The quality metric for the preemptive path. A suggestion made while this is False could have
    avoided the deceleration entirely; made while True, ACC has already started paying for the lead
    and the pass is only recovering.

    Two corrections, both from what the ICBM work established while building the ACC pill:

    PRECHARGE IS NOT BRAKING. It pressurises the system so a later application arrives without
    slack -- no meaningful deceleration, no stop lamps, no pad wear. Counting it here labelled a
    genuinely preemptive suggestion as reactive, which is backwards for a metric whose entire job
    is to measure how often we beat ACC to the decision. It gets its own field instead, because
    "we beat even the precharge" is a stronger claim worth being able to make separately.

    ENGINE BRAKING IS BRAKING, for this purpose. Ford documents ACC slowing by transmission
    downshift to avoid wearing the pads. No stop lamps and no pad wear, but the car IS losing speed
    for the lead, which is exactly what this measures. Missing it under-reported the reactive case.

    The two errors pushed in opposite directions, so neither cancelled the other -- they just made
    the number mean nothing in particular.
    """
    self.acc_braking_at_decision = False
    self.acc_precharge_at_decision = False
    self.acc_braking_available = False
    if car_state_bp is None:
      return
    bls = getattr(car_state_bp, 'brakeLightStatus', None)
    if bls is None or not bls.accDataAvailable:
      return

    self.acc_braking_available = True
    self.acc_precharge_at_decision = bool(bls.accPrechargeRequest)

    propulsion = float(getattr(bls, 'accPropulsionRequest', 0.0) or 0.0)
    engine_braking = ACC_PROPULSION_INACTIVE < propulsion < ACC_ENGINE_BRAKE_MS2
    self.acc_braking_at_decision = bool(bls.accDecelRequest) or engine_braking

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

  def _should_pass(self, lead, v_cruise: float, model=None) -> bool:
    """The one question: is there a vehicle in our lane slow enough to cost us speed?

    Deliberately does NOT ask whether we are closing on it or already behind it. Those are the same
    situation at two moments -- about to brake, or already braked -- and treating them separately is
    what made the old version wait for a state this driver never reaches.

    THE GOAL IS TO NEVER BE STUCK WAITING FOR THIS, and the measure of it is how little Ford's ACC
    has to brake. Getting stuck behind a car is not always avoidable and this does not pretend
    otherwise -- with no lane open there is nowhere to go, and sitting there is the correct
    outcome, reported as noLaneAvailable. What is avoidable is being stuck because the system was
    still making up its mind.
    So the only timer left in this path is a CONFIRMATION timer -- long enough that radar noise
    cannot trigger a manoeuvre, and no longer -- rather than a "have we suffered enough yet" timer.
    Every frame it costs is a frame nearer ACC deciding to shed speed for a car we were always
    going to pass, which is the expensive sequence: brake, then win the speed back in the next
    lane. trigger/accBrakingAtDecision is what measures whether we beat it.

    So: in our lane, slower than the SET speed by a margin worth the manoeuvre, near enough to be
    real. The margin is the judgement; everything else is a sanity bound.
    """
    # radarState yRel is left-POSITIVE like the radar's; flip to the camera frame before comparing
    # against the path. See MAX_LEAD_D_PATH_M for why this is no longer lead.dPath.
    self.lead_d_path = abs(-float(lead.yRel) - path_offset(model, float(lead.dRel))) if model is not None else 0.0
    # Once a lead is being tracked it may drift a little further out before it counts as gone --
    # see RANGE_HYSTERESIS_M. Without this a car hovering at the look-ahead distance alternates in
    # and out of range and the confirmation never completes.
    max_d = self.max_distance_m + (RANGE_HYSTERESIS_M if self.approach_seconds > 0.0 else 0.0)
    if self.lead_d_path > MAX_LEAD_D_PATH_M or lead.dRel > max_d:
      # Momentarily outside a bound. Free for a short while, then decaying. See LEAD_GAP_GRACE_S.
      self._lead_gap()
      return False

    # See DEFICIT_HYSTERESIS_MPH. Harder to become slow than to stay slow.
    threshold = self.min_deficit_ms - (DEFICIT_HYSTERESIS_MPH * CV.MPH_TO_MS if self.lead_is_slow else 0.0)
    self.lead_is_slow = self.speed_deficit >= threshold
    if not self.lead_is_slow:
      self._clear_confirmation()
      return False

    # A good frame. Closes the gap window so an intermittent track keeps making progress rather
    # than trading one step forward for three back.
    self._lead_gap_s = 0.0

    # CAPPED at the threshold, which matters now that failing frames decay instead of erasing:
    # uncapped, a lead followed for five minutes would carry five minutes of credit and take over
    # a minute of absence to clear. The field means "confirmation progress" -- there is nothing to
    # be more confirmed than confirmed -- which is also what makes the progress bar 0..1 honestly.
    self.approach_seconds = min(self.approach_seconds + DT_MDL, self.persistence_s)
    # Returns SPOTTED, not confirmed. The caller runs every gate from this point, so a lane can be
    # found -- and a blinker lit -- while the confirmation is still running underneath. See
    # `confirmed` there for what actually commits the car to moving.
    return True

  def _lead_gap(self) -> None:
    """One frame where the lead failed a bound or was not there at all.

    Free inside the grace window -- a lost radar return is the same car, and charging for it is
    what made an intermittent track impossible to confirm. Beyond the window it decays quickly, so
    a car that has really gone still clears.
    """
    self._lead_gap_s += DT_MDL
    if self._lead_gap_s <= LEAD_GAP_GRACE_S:
      return
    self.approach_seconds = max(0.0, self.approach_seconds - CONFIRM_DECAY_RATE * DT_MDL)
    if self.approach_seconds == 0.0:
      self.lead_is_slow = False

  def _clear_confirmation(self) -> None:
    """A changed situation rather than a noisy one: start over properly."""
    self.approach_seconds = 0.0
    self.lead_is_slow = False
    self._lead_gap_s = 0.0

  def _lead_state(self, lead, v_cruise: float) -> None:
    """Record what the lead is doing, whichever trigger ends up using it."""
    self.has_lead = bool(lead.status)
    self.lead_radar_confirmed = bool(getattr(lead, 'radar', False))
    self.lead_model_prob = float(getattr(lead, 'modelProb', 0.0))
    self.lead_d_rel = float(lead.dRel)
    self.lead_v_lead = float(lead.vLead)
    self.speed_deficit = float(v_cruise - lead.vLead)
    closing = -float(lead.vRel)
    self.lead_ttc = (lead.dRel / closing) if closing > MIN_APPROACH_CLOSING_MS else NO_TTC_S

  def _reference_speed(self, CS, sm, v_cruise: float, speed_limit_target: float) -> float:
    """The speed the driver asked for -- the operand the deficit is measured against.

    NOT the number on the dash. With ICBM running, Veh_V_DsplyCcSet is the CURRENT commanded set
    speed, which ICBM lowers for curves, speed limits and the radar-blind lead and then restores.
    Differencing against it means that the moment anything slows the car, every lead stops looking
    slow -- exactly when a pass is most wanted.

    A MANUAL OVERRIDE TAKES THE SPEED LIMIT OUT OF IT. If ICBM reports the driver took the set
    speed back, the limit stops being evidence of anything: they have said what they want. That is
    the fix for a real and ordinary case -- set 60 where the limit plus offset is 70, and the old
    max() measured against 70, so a car ahead doing 62 (FASTER than the driver asked to go) read as
    8 under and produced a pass suggestion. Deliberately driving below the limit is not a condition
    to be talked out of.

    The dash still floors it, even under an override. ICBM sets v_baseline from the cluster when the
    override latches, so the two agree in normal operation; if they ever drift apart the dash is the
    more trustworthy of the two, because that is the number the car is physically driving toward. A
    lead below it really is holding us back whatever ICBM's record says.

    Otherwise the intent is the highest of what is left, and the max is what makes that safe: with
    no override, ICBM only ever moves the dash value DOWN from the driver's baseline, so a maximum
    recovers the baseline without needing to know which feature lowered it or why.

    Source is recorded, because an operand that is silently wrong produces a system that looks
    correct and never fires -- which is how this was wrong twice.
    """
    cluster = float(CS.cruiseState.speedCluster)

    # The driver took the set speed back and ICBM is holding their number. Checked FIRST and
    # returned immediately: an explicit choice is not one of several candidates.
    try:
      icbm = sm['selfdriveStateSP'].intelligentCruiseButtonManagement
      baseline = float(icbm.vBaseline)
      if int(icbm.overrideState) == 1 and baseline > 0:
        held = max(baseline, cluster)
        self.reference_speed = held
        self.reference_source = RefSource.icbmHold if baseline >= cluster else RefSource.cluster
        return held
    except (KeyError, AttributeError, TypeError, ValueError):
      pass

    best, source = (cluster if cluster > 0 else v_cruise), RefSource.cluster

    # SLA following the limit plus offset. Only reaches here when the driver has not overridden,
    # and the planner only supplies it when Speed Limit Assist is actually switched on.
    if speed_limit_target > best:
      best, source = speed_limit_target, RefSource.speedLimit

    self.reference_speed = best
    self.reference_source = source
    return best

  def update(self, sm, v_cruise: float, long_enabled: bool, speed_limit_target: float = 0.0) -> None:
    """Decide, then advance the dry run of the manoeuvre that decision would produce."""
    self._decide(sm, v_cruise, long_enabled, speed_limit_target)
    self._run_manoeuvre(sm['carState'])

  def _run_manoeuvre(self, CS) -> None:
    """Feed the dry run. See passing_manoeuvre.py -- this actuates nothing.

    Scoped to PASSING only. Keep-right is a different manoeuvre with different gates and a
    different urgency, and folding it in here would make the abort count -- the one number this
    produces -- mean two things at once.
    """
    confirmed = self.approach_seconds >= self.persistence_s
    self.manoeuvre.update(
      clear=self.clear_side,
      suggested=self.suggestion if self.reason == Reason.passing else Side.none,
      confirming=self.approach_seconds > 0.0 and not confirmed,
      confirmed=confirmed,
      # Exactly the inputs the detector already treats as the driver taking over. Reusing the same
      # test rather than restating it means the dry run cannot disagree with the gate above it.
      driver_override=bool(CS.leftBlinker or CS.rightBlinker or CS.brakePressed or CS.steeringPressed),
    )

  def _decide(self, sm, v_cruise: float, long_enabled: bool, speed_limit_target: float = 0.0) -> None:
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

    v_cruise = self._reference_speed(CS, sm, v_cruise, speed_limit_target)

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
    # Runs every cycle, before any gate. What the next lane over is doing is worth logging on the
    # frames where nothing is suggested too -- that is how the band and the debounce get fitted.
    if self.adjacent_enabled:
      self.adjacent.update(sm, float(CS.vEgo), self.max_distance_m,
                           dt=DT_MDL, memory_s=self.oncoming_memory_s,
                           strict=self.strict_two_way)
    else:
      self.adjacent.reset()
    self._blindspot(car_state_bp)
    self._acc_braking(car_state_bp)
    self._traffic_signs(car_state_bp)
    self._geometry(sm['modelV2'])

    # Advances every cycle regardless of the gates below, so it measures real elapsed time rather
    # than time-spent-in-a-particular-branch.
    self._settle_s = min(self._settle_s + DT_MDL, 1e3)  # capped; only the threshold matters

    self._update_suspend(self._lka_toggle(car_state_bp))
    if self.suspended_seconds > 0:
      # Suspended beats every other gate, including the ones that would report something more
      # specific. The driver has said "not here", and a panel reporting "no lane to move into"
      # while suspended would misrepresent why it is silent.
      self._clear_confirmation()
      self.keep_right_seconds = 0.0
      self._reset_outputs(Blocked.suspended)
      return

    if not self.enabled:
      self._clear_confirmation()
      self.keep_right_seconds = 0.0
      self._reset_outputs(Blocked.disabled)
      return

    if not long_enabled:
      self._clear_confirmation()
      self.keep_right_seconds = 0.0
      self._reset_outputs(Blocked.notEngaged)
      return

    if CS.vEgo < MIN_V_EGO_MS:
      self._clear_confirmation()
      self.keep_right_seconds = 0.0
      self._reset_outputs(Blocked.tooSlow)
      return

    # The driver is already doing something about it. Suggesting a pass mid-manoeuvre is noise,
    # and it would corrupt the confirmation timer for the far more interesting no-input case.
    if CS.leftBlinker or CS.rightBlinker or CS.brakePressed or CS.steeringPressed:
      self._clear_confirmation()
      self.keep_right_seconds = 0.0
      self._reset_outputs(Blocked.driverActive)
      return

    in_grace = False
    if not lead.status:
      # A single missed radar return is the same car, not a different situation. Free inside the
      # grace window, decaying after it -- exactly as for the range bound above.
      self._lead_gap()
      self.has_lead = False
      self.lead_ttc = NO_TTC_S
      if self._lead_gap_s > LEAD_GAP_GRACE_S or self.approach_seconds == 0.0:
        self._reset_outputs(Blocked.noLead)
        self._keep_right()
        return
      # Inside the window with a live confirmation: carry on. Absorbing a dropped return in the
      # timer but NOT in the verdict would be the worst of both -- the confirmation survives while
      # the suggestion blinks off for a frame, which is exactly what aborts a signalling sequence.
      # Only the lead's own numbers are stale here, by at most LEAD_GAP_GRACE_S; every gate below
      # is re-evaluated live.
      in_grace = True
    else:
      self._lead_state(lead, v_cruise)

    spotted = self.lead_is_slow if in_grace else self._should_pass(lead, v_cruise, sm['modelV2'])
    if not spotted:
      self._reset_outputs(Blocked.nothingSlower)
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

    # Two-way road. Evaluated before the sign veto, and it is the one this whole design was waiting
    # on: geometry cannot tell an oncoming lane from a passing lane, so until now every gate below
    # would happily clear a pass into head-on traffic on any two-lane road.
    #
    # PER SIDE, not per road, and that is what keeps this from costing more than it should. On a
    # four-lane undivided arterial sitting in the left lane, the oncoming lane is one over to the
    # LEFT and a perfectly ordinary through lane is one over to the RIGHT. A whole-road veto gives
    # up on both; this gives up only on the side the opposing traffic is actually on.
    #
    # On a true two-lane road it costs nothing extra: there is no lane to the right, so
    # right_geometry_ok collapses to the shoulder and nothing is suggested there anyway.
    onc_left = self.oncoming_veto and self.adjacent.left.blocks_oncoming
    onc_right = self.oncoming_veto and self.adjacent.right.blocks_oncoming

    # If oncoming rules out every side geometry offered, report it NOW, ahead of the sign veto.
    # That is the two-lane case, where the two are true together constantly and the road fact is
    # the more useful of the two: a no-passing zone explains a passing silence, a two-way road
    # explains the whole road. Reaching this line means geometry offered something, so oncoming is
    # necessarily what took it away.
    if not ((self.left_geometry_ok and not onc_left) or (self.right_geometry_ok and not onc_right)):
      self._reset_outputs(Blocked.oncomingLane)
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
    # Adjacent lane occupancy, from the front radar's off-path tracks. Last of the per-side gates
    # and deliberately so: it is the only one that can be wrong in a merely wasteful direction. The
    # blind spot and rear approach answer "is this move unsafe"; this answers "is it worth making",
    # so it must not be able to mask either of them in blockedBy.
    #
    # Beat the LEAD, not our own set speed. We are not asking whether the other lane is fast, we are
    # asking whether it is faster than what we are stuck behind -- a queue crawling at 45 is still
    # worth moving into if the lead is doing 40 and we want 70. The margin is the same deficit that
    # decided the pass was worth wanting, so one knob governs both halves of the judgement.
    adj_left = self.adjacent.left.blocks_move(self.lead_v_lead, self.min_deficit_ms)
    adj_right = self.adjacent.right.blocks_move(self.lead_v_lead, self.min_deficit_ms)

    left_ok = (self.left_geometry_ok and not onc_left and not self.left_blindspot and
               not self.rear.left.blocks_lane_change and not adj_left)
    right_ok = (self.right_geometry_ok and not onc_right and not self.right_blindspot and
                not self.rear.right.blocks_lane_change and not adj_right)

    if not (left_ok or right_ok):
      # Name the gate that actually decided it, most severe first. Oncoming outranks everything:
      # it is the only one here about a dangerous manoeuvre rather than a wasted one, and it
      # explains a SUSTAINED silence where the others explain a passing one -- a driver reading
      # "two-way road" understands the feature is off for this whole road.
      if (self.left_geometry_ok and onc_left) or (self.right_geometry_ok and onc_right):
        blocked = Blocked.oncomingLane
      elif ((self.left_geometry_ok and not self.left_blindspot and self.rear.left.blocks_lane_change) or
            (self.right_geometry_ok and not self.right_blindspot and self.rear.right.blocks_lane_change)):
        blocked = Blocked.rearApproaching
      elif ((self.left_geometry_ok and not self.left_blindspot and adj_left) or
            (self.right_geometry_ok and not self.right_blindspot and adj_right)):
        blocked = Blocked.adjacentSlow
      else:
        blocked = Blocked.blindspotOccupied
      self._reset_outputs(blocked)
      return

    # Left is preferred where both are available: passing on the right is the wrong default, and
    # on a divided highway the right side being "available" usually means a slower lane or an
    # exit-only lane rather than somewhere to pass.
    self.clear_side = Side.left if left_ok else Side.right

    # The confirmation gates COMMITTING, not spotting. Everything above has already run, so the
    # lane is known clear and the blinker is already on -- the two clocks overlap rather than
    # stacking into a four second wait for a manoeuvre wanted immediately.
    if self.approach_seconds < self.persistence_s:
      self.blocked_by = Blocked.nothingSlower
      self.suggestion = Side.none
      self.reason = Reason.none
      self.trigger = Trigger.none
      return

    self.suggestion = self.clear_side
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

    # The road opening up ahead means an exit, on-ramp or pullout, and none of those is a lane to
    # settle into. Unlike the outermost rule this works on a two-lane road, because it asks what
    # the lane DOES rather than merely whether another lane exists beyond it.
    if self.right_widening:
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

    # The oncoming gate applies here too, and it was missed the first time round -- the pass path
    # got it and this one did not, which is precisely the ordering bug the rear-approach interface
    # was designed early to avoid.
    #
    # Rare but real, and Utah has the road: a reversible flex lane can put opposing traffic in the
    # lane to our RIGHT, and 5400 South runs three of them. Keeping right into that would be the
    # worst suggestion this system could make, and nothing else in this function would have stopped
    # it -- the blind spot only lights once they are already alongside.
    #
    # Costs nothing on an ordinary undivided road: opposing traffic there is on the left, so
    # right.blocks_oncoming stays false and keep-right works normally.
    if self.oncoming_veto and self.adjacent.right.blocks_oncoming:
      self.keep_right_seconds = 0.0
      return

    # Do not move over behind a car we would immediately want to pass. "Keep right except to pass"
    # assumes the right lane is moving; dropping in behind traffic slow enough to trip the passing
    # threshold buys a pair of lane changes and no progress, and does it at exactly the moment the
    # settle timer is expired and least able to stop the second one.
    #
    # Expressed as the passing threshold read backwards -- slower than the set speed by the deficit
    # margin -- so the two behaviours cannot disagree about what "slow" means.
    if self.adjacent.right.blocks_move(self.reference_speed - self.min_deficit_ms, 0.0):
      self.keep_right_seconds = 0.0
      return

    self.keep_right_seconds += DT_MDL

    # The lane also has to have BEEN there a while -- the owner's own exit test, and a better one
    # than road widening because it needs no guess about what the edge is doing ahead. An exit lane
    # appears; a through lane has been beside us for miles.
    #
    # This is deliberately NOT one of the resets above. Every gate up there is "the situation is
    # unsafe right now", so restarting the clear-lane clock is the whole point of them. A young
    # lane is not unsafe, it is merely unproven, and zeroing the clock here would chain the two
    # waits end to end: 15 s of age and THEN 10 s of clear, 25 s in total, by which time the
    # suggestion is stale. Run concurrently, both clocks start when the lane appears and the wait
    # is max(15, 10) = 15 s, which is what the two numbers on the settings screen say.
    #
    # Failing safe in every direction: a lane the model briefly loses comes back looking new and
    # costs a few quiet seconds, nothing more.
    if self.right_lane_age_s < self.min_lane_age_s:
      return

    if self.keep_right_seconds >= self.keep_right_delay_s:
      self.suggestion = Side.right
      self.blocked_by = Blocked.none
      self.reason = Reason.keepRight

  def publish(self, passingAssist) -> None:
    """Copy this observer's state onto the capnp message.

    Lives here rather than in the planner deliberately. It is forty lines of mechanical field
    copying that belong to this feature, and every one of them sitting in an upstream file is a
    merge conflict paid on every future sunnypilot rebase, forever. The planner keeps one call.

    Takes the sub-message rather than the whole plan so it cannot reach anything else.
    """
    pa = self
    passingAssist.suggestion = pa.suggestion
    passingAssist.blockedBy = pa.blocked_by
    # One timer now. The field keeps its name so older logs stay comparable.
    passingAssist.confirmSeconds = float(pa.approach_seconds)
    passingAssist.hasLead = pa.has_lead
    passingAssist.leadDRel = float(pa.lead_d_rel)
    passingAssist.leadVLead = float(pa.lead_v_lead)
    passingAssist.speedDeficit = float(pa.speed_deficit)
    passingAssist.leftLineProb = float(pa.left_line_prob)
    passingAssist.rightLineProb = float(pa.right_line_prob)
    passingAssist.leftEdgeGap = float(pa.left_edge_gap)
    passingAssist.rightEdgeGap = float(pa.right_edge_gap)
    passingAssist.leftGeometryOk = pa.left_geometry_ok
    passingAssist.rightGeometryOk = pa.right_geometry_ok
    passingAssist.leftBlindspot = pa.left_blindspot
    passingAssist.rightBlindspot = pa.right_blindspot
    passingAssist.blindspotAvailable = pa.blindspot_available
    passingAssist.overtakeRestricted = pa.overtake_restricted
    passingAssist.overtakeMsg = pa.overtake_msg
    passingAssist.overtakeStatus = pa.overtake_status
    passingAssist.tsrAvailable = pa.tsr_available
    passingAssist.reason = pa.reason
    passingAssist.keepRightSeconds = float(pa.keep_right_seconds)
    passingAssist.roadName = pa.road_name
    for dest, side in ((passingAssist.rearLeft, pa.rear.left), (passingAssist.rearRight, pa.rear.right)):
      dest.available = side.available
      dest.detected = side.detected
      dest.closing = side.closing
      dest.dRel = float(side.d_rel)
      dest.vRel = float(side.v_rel)
      dest.ttc = float(side.ttc)
      dest.source = side.source
    passingAssist.rightWideningM = float(pa.right_widening_m)
    passingAssist.rightWidening = pa.right_widening
    passingAssist.trigger = pa.trigger
    passingAssist.leadTtc = float(pa.lead_ttc)
    passingAssist.approachSeconds = float(pa.approach_seconds)
    passingAssist.accBrakingAtDecision = pa.acc_braking_at_decision
    passingAssist.accBrakingAvailable = pa.acc_braking_available
    passingAssist.accPrechargeAtDecision = pa.acc_precharge_at_decision
    passingAssist.suspendedSeconds = float(pa.suspended_seconds)
    passingAssist.referenceSpeed = float(pa.reference_speed)
    passingAssist.referenceSource = pa.reference_source

    # The dry run. See passing_manoeuvre.py.
    passingAssist.manoeuvre = pa.manoeuvre.phase
    passingAssist.manoeuvreSeconds = float(pa.manoeuvre.phase_seconds)
    passingAssist.manoeuvreSide = pa.manoeuvre.side
    passingAssist.blinkerWouldBeOn = pa.manoeuvre.blinker_on
    passingAssist.steeringWouldBeActive = pa.manoeuvre.steering_active
    # Saturates rather than wraps: a UInt16 rolling over to 0 would read as a clean drive, which is
    # the exact opposite of what a huge abort count means.
    passingAssist.manoeuvreAborts = min(pa.manoeuvre.aborts, 65535)
    passingAssist.leadRadarConfirmed = pa.lead_radar_confirmed
    passingAssist.leadModelProb = float(pa.lead_model_prob)
    for dest, side in ((passingAssist.adjacentLeft, pa.adjacent.left),
                       (passingAssist.adjacentRight, pa.adjacent.right)):
      dest.available = side.available
      dest.occupied = side.occupied
      dest.dRel = float(side.d_rel)
      dest.yRel = float(side.y_rel)
      dest.vRel = float(side.v_rel)
      dest.vAbs = float(side.v_abs)
      dest.oncoming = side.oncoming
      dest.oncomingDRel = float(side.oncoming_d_rel)
      dest.oncomingVAbs = float(side.oncoming_v_abs)
      dest.oncomingAdjacent = side.oncoming_adjacent_seconds > 0.0
      dest.sameDirectionRecent = side.same_direction_recent
    passingAssist.undividedRoad = pa.adjacent.undivided
    passingAssist.undividedSeconds = float(pa.adjacent.undivided_seconds)
    passingAssist.oncomingSeen = pa.adjacent.oncoming_seen
