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

Side = custom.LongitudinalPlanSP.PassingAssist.Side
Blocked = custom.LongitudinalPlanSP.PassingAssist.Blocked
Reason = custom.LongitudinalPlanSP.PassingAssist.Reason

# --- lane line indices. modelV2 publishes exactly 4 lines and 2 road edges. ---
# y is negative to the left and positive to the right in this frame: ldw.py tests the left line
# against -(1.08 + CAMERA_OFFSET) and the right against +(1.08 - CAMERA_OFFSET), and
# lateral_curv_ext computes width as laneLines[2].y + (-laneLines[1].y).
LL_FAR_LEFT, LL_LEFT, LL_RIGHT, LL_FAR_RIGHT = 0, 1, 2, 3
RE_LEFT, RE_RIGHT = 0, 1

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

# --- lead / stuck gates ---
MIN_V_EGO_MS = 40 * CV.MPH_TO_MS  # below this, passing is not the manoeuvre being considered
# The lead must actually be pacing us, not merely being approached: if we are still closing, the
# set speed is not yet being held back and the stuck timer would start far too early.
MAX_CLOSING_SPEED_MS = 2.0
MAX_LEAD_D_REL_M = 60.0           # beyond this it is not holding us back yet
MAX_LEAD_D_PATH_M = 1.5           # in our lane, not an adjacent-lane return

DEFAULT_MIN_DEFICIT_MPH = 8
DEFAULT_STUCK_TIME_S = 25

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
    self.stuck_seconds = 0.0
    self.keep_right_seconds = 0.0

    self.has_lead = False
    self.lead_d_rel = 0.0
    self.lead_v_lead = 0.0
    self.speed_deficit = 0.0

    self.left_line_prob = 0.0
    self.right_line_prob = 0.0
    self.left_edge_gap = 0.0
    self.right_edge_gap = 0.0
    self.left_geometry_ok = False
    self.right_geometry_ok = False

    self.left_blindspot = False
    self.right_blindspot = False
    self.blindspot_available = False

    self.overtake_restricted = False
    self.overtake_msg = 0
    self.overtake_status = 0
    self.tsr_available = False
    self.road_name = ""

    self.params = Params()
    self.frame = 0
    self.enabled = True
    self.min_deficit_ms = DEFAULT_MIN_DEFICIT_MPH * CV.MPH_TO_MS
    self.stuck_time_s = float(DEFAULT_STUCK_TIME_S)
    self.keep_right_enabled = True
    self.keep_right_delay_s = float(DEFAULT_KEEP_RIGHT_DELAY_S)

  def update_params(self) -> None:
    if self.frame % int(PARAMS_UPDATE_PERIOD / DT_MDL) == 0:
      self.enabled = self.params.get_bool("PassingAssistLogEnabled")
      self.min_deficit_ms = self.params.get("PassingAssistMinDeficit", return_default=True) * CV.MPH_TO_MS
      self.stuck_time_s = float(self.params.get("PassingAssistStuckTime", return_default=True))
      self.keep_right_enabled = self.params.get_bool("PassingAssistKeepRight")
      self.keep_right_delay_s = float(self.params.get("PassingAssistKeepRightDelay", return_default=True))

  def _reset_outputs(self, blocked: int) -> None:
    self.suggestion = Side.none
    self.blocked_by = blocked
    self.reason = Reason.none

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
    self.left_geometry_ok = (self.left_line_prob >= MIN_ADJACENT_LINE_PROB and
                             self.left_edge_gap >= MIN_LANE_WIDTH_M and
                             left_std <= MAX_ROAD_EDGE_STD)
    self.right_geometry_ok = (self.right_line_prob >= MIN_ADJACENT_LINE_PROB and
                              self.right_edge_gap >= MIN_LANE_WIDTH_M and
                              right_std <= MAX_ROAD_EDGE_STD)

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

  def _stuck(self, CS, lead, v_cruise: float) -> bool:
    """Are we being held below the set speed by this lead, and for long enough?

    Accumulates on the frames where it holds and resets fully otherwise -- a lead that drops out
    for a second has not been holding us back continuously, and this is not the place for
    hysteresis. The timer is the whole point: a slow car you have been behind for 25 seconds is a
    different situation from one you just caught up to.
    """
    self.has_lead = bool(lead.status)
    self.lead_d_rel = float(lead.dRel)
    self.lead_v_lead = float(lead.vLead)
    self.speed_deficit = float(v_cruise - lead.vLead)

    if not lead.status:
      self.stuck_seconds = 0.0
      return False

    holding_us_back = (self.speed_deficit >= self.min_deficit_ms and
                       lead.dRel <= MAX_LEAD_D_REL_M and
                       abs(lead.dPath) <= MAX_LEAD_D_PATH_M and
                       # pacing, not still closing on
                       lead.vRel > -MAX_CLOSING_SPEED_MS)

    if not holding_us_back:
      self.stuck_seconds = 0.0
      return False

    self.stuck_seconds += DT_MDL
    return self.stuck_seconds >= self.stuck_time_s

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

    car_state_bp = sm['carStateBP'] if 'carStateBP' in sm else None
    self._blindspot(car_state_bp)
    self._traffic_signs(car_state_bp)
    self._geometry(sm['modelV2'])

    if not self.enabled:
      self.stuck_seconds = 0.0
      self.keep_right_seconds = 0.0
      self._reset_outputs(Blocked.disabled)
      return

    if not long_enabled:
      self.stuck_seconds = 0.0
      self.keep_right_seconds = 0.0
      self._reset_outputs(Blocked.notEngaged)
      return

    if CS.vEgo < MIN_V_EGO_MS:
      self.stuck_seconds = 0.0
      self.keep_right_seconds = 0.0
      self._reset_outputs(Blocked.tooSlow)
      return

    # The driver is already doing something about it. Suggesting a pass mid-manoeuvre is noise,
    # and it would corrupt the stuck timer for the far more interesting no-input case.
    if CS.leftBlinker or CS.rightBlinker or CS.brakePressed or CS.steeringPressed:
      self.stuck_seconds = 0.0
      self.keep_right_seconds = 0.0
      self._reset_outputs(Blocked.driverActive)
      return

    if not lead.status:
      self.stuck_seconds = 0.0
      self.has_lead = False
      self._reset_outputs(Blocked.noLead)
      self._keep_right()
      return

    if not self._stuck(CS, lead, v_cruise):
      self._reset_outputs(Blocked.notStuck)
      self._keep_right()
      return

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

    # Left is preferred where both are available: passing on the right is the wrong default, and
    # on a divided highway the right side being "available" usually means a slower lane or an
    # exit-only lane rather than somewhere to pass.
    if self.left_geometry_ok and not self.left_blindspot:
      self.suggestion = Side.left
      self.blocked_by = Blocked.none
      self.reason = Reason.passing
    elif self.right_geometry_ok and not self.right_blindspot:
      self.suggestion = Side.right
      self.blocked_by = Blocked.none
      self.reason = Reason.passing
    else:
      self._reset_outputs(Blocked.blindspotOccupied)

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
    if not self.keep_right_enabled or not self.right_geometry_ok:
      self.keep_right_seconds = 0.0
      return

    # Blind spot is a hard gate here, unlike geometry: moving into an occupied lane is the failure
    # mode, and returning right is never urgent enough to justify acting on stale evidence.
    if self.right_blindspot:
      self.keep_right_seconds = 0.0
      return

    self.keep_right_seconds += DT_MDL
    if self.keep_right_seconds >= self.keep_right_delay_s:
      self.suggestion = Side.right
      self.blocked_by = Blocked.none
      self.reason = Reason.keepRight
