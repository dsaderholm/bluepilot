"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FusionPilot: tests for adjacent-lane detection from the front radar's off-path tracks.

Four things here can be wrong in ways a drive would not reveal:

  - the SIGN. Radar yRel is left-positive and modelV2 lane geometry is left-negative. Get it
    backwards and everything still works, mirrored, which reads as a tuning problem for a long time.
  - unavailable read as clear. The same failure the blind-spot flag already had: an absent or dead
    radar must not make an occupied lane look empty.
  - the debounce counting the wrong thing. liveTracks arrives at ~8.3 Hz and the planner runs at
    20 Hz, so a per-cycle counter sees each radar message two or three times and "3 frames" of
    agreement becomes barely one real observation.
  - the comparison operand. blocks_move asks whether the lane beats a speed passed in, and the two
    callers pass different ones. Comparing against ego, or against the set speed, gives a system
    that refuses every pass in traffic.
"""

from types import SimpleNamespace as NS

from openpilot.sunnypilot.selfdrive.controls.lib.adjacent_lane import (
  ONCOMING_FRAMES, MIN_ONCOMING_MS, OVERTAKE_FRAMES, SAME_DIRECTION_FRAMES,
  SAME_DIRECTION_WINDOW_S,
  AdjacentLane, AdjacentLaneSide, ADJACENT_MIN_M, ADJACENT_MAX_M, DEBOUNCE_FRAMES, MIN_MOVING_MS,
  ONCOMING_MAX_M, SAME_DIRECTION_MIN_FRACTION, path_offset, ground_speed,
)

X_IDXS = [192.0 * (i / 32.0) ** 2 for i in range(33)]


def edge_at(y, curve_radius_m=0.0):
  """A road edge offset y meters from the path, bending with it."""
  base = path(curve_radius_m).y
  return NS(x=list(X_IDXS), y=[b + y for b in base])


def path(curve_radius_m=0.0):
  """Model predicted path, straight by default. Positive radius bends RIGHT (camera frame y is
  left-negative), by the small-angle displacement d^2 / 2R."""
  ys = [0.0] * 33 if not curve_radius_m else [(x * x) / (2.0 * curve_radius_m) for x in X_IDXS]
  return NS(x=list(X_IDXS), y=ys)

V_EGO = 30.0
MAX_D = 220.0
NO_SPEED_SENTINEL = 0.0


def track(d_rel, y_rel, v_rel=0.0):
  """One liveTracks point. yRel LEFT-POSITIVE, as the radar publishes it."""
  return NS(dRel=d_rel, yRel=y_rel, vRel=v_rel)


class FakeSM:
  """SubMaster-shaped: __getitem__ plus alive/valid/updated dicts, and no __contains__."""

  def __init__(self, tracks=(), *, alive=True, valid=True, updated=True, present=True, curve=0.0,
               left_edge=-7.0, right_edge=7.0, edge_stds=(0.1, 0.1)):
    # Road edges relative to the path, in the camera frame: negative left. Default is a wide
    # two-lane road -- the oncoming lane is INSIDE the left edge, which is the two-way case.
    self.data = {'modelV2': NS(position=path(curve),
                               roadEdges=[edge_at(left_edge, curve), edge_at(right_edge, curve)],
                               roadEdgeStds=list(edge_stds))}
    if present:
      self.data['liveTracks'] = NS(points=list(tracks))
    self.alive = {'liveTracks': alive} if present else {}
    self.valid = {'liveTracks': valid} if present else {}
    self.updated = {'liveTracks': updated} if present else {}

  def __getitem__(self, s):
    return self.data[s]


def upd(adj, sm, v_ego=V_EGO, max_d=MAX_D, **kw):
  """Apply one scene ONCOMING_FRAMES times.

  Oncoming used to latch the veto on a SINGLE radar return. It does not any more -- see
  ONCOMING_FRAMES and the I-15 report behind it -- so every case here that means "there really is
  opposing traffic" has to show it more than once. That is what a real radar does anyway: a vehicle
  in view produces a return every 120 ms, not one and then silence.

  Used only where a scene ESTABLISHES oncoming. The decay and dropout cases still step one frame at
  a time, because repeating those would run their clocks three times too fast.
  """
  for _ in range(ONCOMING_FRAMES):
    adj.update(sm, v_ego, max_d, **kw)
  return adj


def feed(adj, tracks, frames=DEBOUNCE_FRAMES, **kw):
  for _ in range(frames):
    adj.update(FakeSM(tracks, **kw), V_EGO, MAX_D)
  return adj


def went_past(adj, tracks, **kw):
  """Apply one scene OVERTAKE_FRAMES times.

  Same reason upd() exists for oncoming: an overtake is corroborated across messages now, so a
  scene meaning "a car really did go by" has to show it more than once. A real overtaking vehicle
  is inside OVERTAKE_MAX_D_REL_M for seconds, which is dozens of messages, so this is what the
  radar does anyway -- see TestOvertakeCorroboration for what it buys.
  """
  for _ in range(OVERTAKE_FRAMES):
    adj.update(FakeSM(tracks, **kw), V_EGO, MAX_D)
  return adj


class TestSideAssignment:
  def test_positive_y_is_left(self):
    # The single most consequential line in the module. A radar target 3.7 m to the LEFT publishes
    # yRel = +3.7; if this ever reads right, every suggestion comes out mirrored.
    adj = feed(AdjacentLane(), [track(50, 3.7)])
    assert adj.left.occupied
    assert not adj.right.occupied

  def test_negative_y_is_right(self):
    adj = feed(AdjacentLane(), [track(50, -3.7)])
    assert adj.right.occupied
    assert not adj.left.occupied

  def test_own_lane_lead_is_not_adjacent(self):
    # Our own lead sits near y=0. Counting it would make every slower lead block its own pass.
    adj = feed(AdjacentLane(), [track(40, 0.3)])
    assert not adj.left.occupied
    assert not adj.right.occupied

  def test_two_lanes_over_is_ignored(self):
    adj = feed(AdjacentLane(), [track(50, ADJACENT_MAX_M + 1.0)])
    assert not adj.left.occupied

  def test_band_edges_are_inclusive(self):
    assert feed(AdjacentLane(), [track(50, ADJACENT_MIN_M)]).left.occupied
    assert feed(AdjacentLane(), [track(50, ADJACENT_MAX_M)]).left.occupied

  def test_nearest_target_wins(self):
    # Two cars in the left lane: the one we would meet first is the one the decision is about.
    adj = feed(AdjacentLane(), [track(120, 3.7, v_rel=5.0), track(45, 3.6, v_rel=-2.0)])
    assert adj.left.d_rel == 45
    assert adj.left.v_rel == -2.0

  def test_beyond_look_ahead_is_ignored(self):
    adj = feed(AdjacentLane(), [track(MAX_D + 10, 3.7)])
    assert not adj.left.occupied

  def test_lateral_position_is_carried_through_unflipped(self):
    """The UI places its readout from this value and flips the sign itself at the draw site.

    Normalising to the camera frame here instead would be the subtle version of the same trap:
    the number in the log and the number on screen would silently mean different things, and only
    one of them would match the radar it came from.
    """
    adj = feed(AdjacentLane(), [track(50, 3.4), track(60, -3.9)])
    assert adj.left.y_rel == 3.4
    assert adj.right.y_rel == -3.9

  def test_lateral_position_clears_with_the_lane(self):
    adj = feed(AdjacentLane(), [track(50, 3.4)])
    feed(adj, [])
    assert adj.left.y_rel == 0.0


class TestCurves:
  """A fixed lateral band measured from the car's straight-ahead axis is only the lane on a
  straight road. These are the cases that band gets wrong, and they are not rare -- a 500 to 1000 m
  radius is an ordinary interstate bend."""

  # Right-hand bend. At 70 m the path is 70^2 / (2 * 500) = 4.9 m off axis, mid-band.
  BEND_R = 500.0

  def test_our_own_lead_on_a_curve_is_not_in_the_next_lane(self):
    # The false positive that matters most: our own lead is slower than us by definition, so
    # counting it as adjacent traffic would block the very pass it caused, on every curve.
    lead = track(70, -4.9, v_rel=-2.0)   # radar left-positive, so a right-bend lead reads negative
    adj = feed(AdjacentLane(), [lead], curve=self.BEND_R)
    assert not adj.right.occupied
    assert not adj.left.occupied

  def test_a_real_adjacent_car_on_a_curve_is_still_found(self):
    # Same bend, but this one is a lane over: path offset plus a lane width.
    car = track(70, -(4.9 + 3.7), v_rel=-2.0)
    adj = feed(AdjacentLane(), [car], curve=self.BEND_R)
    assert adj.right.occupied

  def test_left_bend_puts_the_adjacent_car_on_the_correct_side(self):
    # Sign check through the whole chain. On a LEFT bend the path swings left, so a car one lane
    # to the LEFT of the path sits further left still.
    car = track(70, 4.9 + 3.7, v_rel=-2.0)
    adj = feed(AdjacentLane(), [car], curve=-self.BEND_R)
    assert adj.left.occupied
    assert not adj.right.occupied

  def test_straight_road_is_unchanged(self):
    adj = feed(AdjacentLane(), [track(70, 3.7)], curve=0.0)
    assert adj.left.occupied


class TestOncoming:
  """The one question the geometry could never answer.

  An oncoming lane and a passing lane are identical to the camera: same paint, same drivable width,
  same road edge. The radar separates them outright, because an oncoming vehicle's absolute ground
  speed is roughly minus its own and nothing else on the road can produce that number.
  """

  # Someone doing 27 m/s the other way, one lane to the left. v_rel = -(their speed + ours).
  ONCOMING = staticmethod(lambda d=90: track(d, 3.7, v_rel=-27.0 - V_EGO))

  def test_a_single_oncoming_car_classifies_the_road(self):
    adj = AdjacentLane()
    upd(adj, FakeSM([self.ONCOMING()]), V_EGO, MAX_D)
    assert adj.oncoming_any_side
    assert adj.left.oncoming
    assert adj.oncoming_seen

  def test_no_debounce_on_oncoming(self):
    # Occupancy waits three messages; this must not. Waiting for a second sighting costs a
    # suggestion to pass into a head-on lane, and one sighting is already proof.
    adj = AdjacentLane()
    upd(adj, FakeSM([self.ONCOMING()]), V_EGO, MAX_D)
    assert adj.oncoming_any_side

  def test_oncoming_is_not_counted_as_lane_occupancy(self):
    # It must not fall through into the "is that lane faster" comparison, where a large negative
    # speed would read as very slow traffic and produce the wrong blocked reason.
    adj = feed(AdjacentLane(), [self.ONCOMING()])
    assert not adj.left.occupied

  def test_same_direction_traffic_never_classifies_the_road(self):
    adj = feed(AdjacentLane(), [track(90, 3.7, v_rel=4.0)])
    assert not adj.oncoming_any_side
    assert not adj.left.oncoming

  def test_a_barrier_is_not_oncoming(self):
    # Stationary is v_abs ~ 0, which is inside neither threshold. A guardrail must not classify
    # every divided highway as two-way.
    adj = feed(AdjacentLane(), [track(60, 3.5, v_rel=-V_EGO)])
    assert not adj.oncoming_any_side

  def test_divided_highway_opposing_carriageway_is_out_of_band(self):
    # The band does this discrimination for free: an opposing carriageway across a median sits well
    # beyond ADJACENT_MAX_M, so an interstate never trips the veto.
    adj = AdjacentLane()
    upd(adj, FakeSM([track(120, 14.0, v_rel=-27.0 - V_EGO)]), V_EGO, MAX_D)
    assert not adj.oncoming_any_side

  def test_classification_outlives_the_car_that_caused_it(self):
    # The whole point of the memory. On a quiet two-lane road the gaps between meeting cars are
    # exactly when a wrong suggestion would look most convincing.
    adj = AdjacentLane()
    upd(adj, FakeSM([self.ONCOMING()]), V_EGO, MAX_D, dt=0.05, memory_s=90)
    for _ in range(200):     # 10 s of empty road
      adj.update(FakeSM([]), V_EGO, MAX_D, dt=0.05, memory_s=90)
    assert adj.oncoming_any_side
    assert not adj.left.oncoming    # the car is gone...
    assert adj.oncoming_seconds_left > 70   # ...the road is not

  def test_the_memory_does_expire(self):
    adj = AdjacentLane()
    upd(adj, FakeSM([self.ONCOMING()]), V_EGO, MAX_D, dt=0.05, memory_s=5)
    for _ in range(120):     # 6 s
      adj.update(FakeSM([]), V_EGO, MAX_D, dt=0.05, memory_s=5)
    assert not adj.oncoming_any_side
    assert adj.oncoming_seen        # but the drive still records that it happened

  def test_a_dead_radar_does_not_clear_the_classification(self):
    # A sensor dropping out is not evidence the road became one-way.
    adj = AdjacentLane()
    upd(adj, FakeSM([self.ONCOMING()]), V_EGO, MAX_D)
    adj.update(FakeSM([], alive=False), V_EGO, MAX_D)
    assert adj.oncoming_any_side

  def test_memory_decays_on_cycles_with_no_new_radar_message(self):
    # The clock is wall time, not radar time. Decaying only on radar frames would stretch the
    # memory by whatever the message rate happened to be.
    adj = AdjacentLane()
    upd(adj, FakeSM([self.ONCOMING()]), V_EGO, MAX_D, dt=0.05, memory_s=10)
    before = adj.oncoming_seconds_left
    for _ in range(20):
      adj.update(FakeSM([], updated=False), V_EGO, MAX_D, dt=0.05, memory_s=10)
    assert adj.oncoming_seconds_left < before - 0.9

  def test_oncoming_on_a_curve_is_still_found(self):
    # Same path-relative geometry as everything else here: a fixed band from the car's axis would
    # lose the oncoming lane on exactly the bends where meeting someone matters most.
    car = track(70, 4.9 + 3.7, v_rel=-27.0 - V_EGO)
    adj = AdjacentLane()
    upd(adj, FakeSM([car], curve=-500.0), V_EGO, MAX_D)
    assert adj.oncoming_any_side
    assert adj.left.oncoming


class TestMedians:
  """The case the lateral band cannot settle by itself.

  A divided highway with a jersey barrier and no grass puts the opposing lane center around 7 m
  away. That clears ADJACENT_MAX_M, but by less than a lane width, and the margin rests on shoulder
  widths that vary road to road -- while radar lateral error grows with range. The road edge asks
  the question directly instead: beyond the edge of our own drivable surface is not our road.
  """

  @staticmethod
  def oncoming_at(y_rel, d=100):
    return track(d, y_rel, v_rel=-27.0 - V_EGO)

  def test_barrier_divided_highway_does_not_trip_the_veto(self):
    # Narrow median: our carriageway ends 5.5 m out, the opposing lane sits at 5.0 m -- INSIDE the
    # band, so the band alone would have called this a two-way road and killed passing on I-15.
    adj = AdjacentLane()
    upd(adj, FakeSM([self.oncoming_at(5.0)], left_edge=-4.0), V_EGO, MAX_D)
    assert not adj.oncoming_any_side

  def test_two_lane_road_still_trips_it(self):
    # Same lateral distance, but here the road edge is beyond the oncoming lane, because that lane
    # is part of our road. This is the inversion the band cannot see and the edge gets right.
    adj = AdjacentLane()
    upd(adj, FakeSM([self.oncoming_at(3.7)], left_edge=-7.0), V_EGO, MAX_D)
    assert adj.oncoming_any_side

  def test_an_untrusted_road_edge_still_sees_the_next_lane(self):
    # Unknown counts as ON our road. Over-detecting costs a quiet stretch; under-detecting costs a
    # suggestion to pass into a head-on lane, and those are not the same size.
    adj = AdjacentLane()
    upd(adj, FakeSM([self.oncoming_at(5.0)], left_edge=-4.0, edge_stds=(9.9, 9.9)),
               V_EGO, MAX_D)
    assert adj.oncoming_any_side

  def test_a_missing_road_edge_still_sees_the_next_lane(self):
    adj = AdjacentLane()
    sm = FakeSM([self.oncoming_at(3.7)])
    del sm.data['modelV2'].roadEdges
    upd(adj, sm, V_EGO, MAX_D)
    assert adj.oncoming_any_side

  def test_the_edge_test_follows_a_curve(self):
    # Both the track and the edge are taken path-relative, so a bend must not push a same-road
    # oncoming car outside its own road edge.
    adj = AdjacentLane()
    upd(adj, FakeSM([track(70, 4.9 + 3.7, v_rel=-27.0 - V_EGO)], curve=-500.0, left_edge=-7.0),
               V_EGO, MAX_D)
    assert adj.oncoming_any_side

  def test_the_right_side_edge_comparison_is_not_mirrored(self):
    # Camera frame: left negative, right positive, so "inside the edge" is a different comparison
    # per side. Easy to write once and have backwards on one of them.
    adj = AdjacentLane()
    upd(adj, FakeSM([self.oncoming_at(-5.0)], right_edge=4.0), V_EGO, MAX_D)
    assert not adj.oncoming_any_side
    adj = AdjacentLane()
    upd(adj, FakeSM([self.oncoming_at(-3.7)], right_edge=7.0), V_EGO, MAX_D)
    assert adj.right.oncoming


TWLTL_W = 4.27    # 14 ft, the AASHTO preferred width. Minimum 12, maximum 16.
LANE_W = 3.66     # 12 ft, the standard travel lane
SHOULDER = 1.5


def road(*, ego_offset_from_left=0.0, lanes_our_way=1, twltl=False, oncoming_lanes=1,
         divided_median=None):
  """Build one US road configuration, measured out from ego's lane center.

  Returns (left_edge, oncoming_lane_offsets, same_direction_lane_offsets), all camera-frame
  (negative = left), relative to ego.

  `divided_median` in meters puts a median between our carriageway and theirs; None means two-way
  and the opposing lanes sit inside our own road edge, which is the whole distinction.
  """
  # Lanes to our left on our own side.
  same_dir = [-(i + 1) * LANE_W for i in range(int(ego_offset_from_left))]
  edge_of_ours = -(ego_offset_from_left * LANE_W + LANE_W / 2)

  x = edge_of_ours
  if twltl:
    x -= TWLTL_W
  onc = []
  for i in range(oncoming_lanes):
    onc.append(x - LANE_W / 2 - i * LANE_W)

  if divided_median is not None:
    # Their carriageway is past a median: our road edge closes before it.
    left_edge = edge_of_ours - SHOULDER
    onc = [o - divided_median for o in onc]
  else:
    left_edge = x - oncoming_lanes * LANE_W - SHOULDER
  return left_edge, onc, same_dir


# Every configuration worth naming, traced end to end. `expect_block` is whether the LEFT side
# should be refused when the only traffic seen is the oncoming vehicle nearest us.
ROAD_CASES = [
  # name, road kwargs, strict, expect_block_left
  ("two-lane two-way (US-89 typical)",
   dict(ego_offset_from_left=0, oncoming_lanes=1), True, True),
  ("two-lane two-way, lenient mode still blocks",
   dict(ego_offset_from_left=0, oncoming_lanes=1), False, True),
  ("1 + TWLTL + 1 arterial",
   dict(ego_offset_from_left=0, twltl=True, oncoming_lanes=1), True, True),
  ("2 + TWLTL + 2 arterial, ego in the LEFT lane",
   dict(ego_offset_from_left=1, twltl=True, oncoming_lanes=2), True, True),
  ("2 + TWLTL + 2 arterial, ego in the RIGHT lane",
   dict(ego_offset_from_left=0, twltl=True, oncoming_lanes=2), True, True),
  ("four-lane two-way, ego in the RIGHT lane",
   dict(ego_offset_from_left=0, oncoming_lanes=2), True, True),
  ("divided interstate, wide median",
   dict(ego_offset_from_left=1, oncoming_lanes=2, divided_median=20.0), True, False),
  ("divided interstate, jersey barrier",
   dict(ego_offset_from_left=1, oncoming_lanes=2, divided_median=2.0), True, False),
]


class TestUSRoadConfigurations:
  """Every US road layout worth naming, traced from real lane widths.

  The two that broke the first design are the TWLTL cases. Bounding oncoming detection to the
  adjacent band assumed opposing traffic is in the next lane, which is true only on a two-lane
  road: put a 14 ft turn lane down the middle and opposing traffic moves to 7.9 m, outside a 5.5 m
  band, so no veto fired at all and the geometry test offered a pass into the turn lane.
  """

  @staticmethod
  def _run(cfg, strict):
    left_edge, onc, _same = road(**cfg)
    # The nearest oncoming vehicle, at 100 m, converted back to the radar's left-positive frame.
    tracks = [track(100, -onc[0], v_rel=-27.0 - V_EGO)]
    adj = AdjacentLane()
    upd(adj, FakeSM(tracks, left_edge=left_edge), V_EGO, MAX_D, strict=strict)
    return adj.left.blocks_oncoming

  def test_every_road_configuration(self):
    failures = []
    for name, cfg, strict, expect in ROAD_CASES:
      got = self._run(cfg, strict)
      if got != expect:
        failures.append(f"{name}: expected block={expect}, got {got}")
    assert not failures, "\n".join(failures)

  def test_twltl_oncoming_is_out_of_the_adjacent_band(self):
    """The measurement behind the bug, asserted directly so the reasoning cannot rot.

    If someone later narrows ONCOMING_MAX_M back to the adjacent band, this is the number that
    explains why every arterial went quiet.
    """
    _edge, onc, _same = road(ego_offset_from_left=0, twltl=True, oncoming_lanes=1)
    assert abs(onc[0]) > ADJACENT_MAX_M      # the whole point: outside the next-lane band
    assert abs(onc[0]) < ONCOMING_MAX_M      # but well inside what we now look at


class TestMiddleLaneAmbiguity:
  """Two roads the sensors cannot tell apart, and the setting that decides which way to be wrong.

  From the right lane of a 2+1 passing-lane section (US-6, US-89) and from the left lane of a
  2+TWLTL+2 arterial, the picture is identical: a lane at ~3.7 m, opposing traffic at ~7.9 m, our
  road edge beyond both. One is a passing lane, the other is a turn lane.
  """

  @staticmethod
  def _side(strict, same_direction_seen):
    left_edge, onc, _ = road(ego_offset_from_left=0, twltl=True, oncoming_lanes=1)
    tracks = [track(100, -onc[0], v_rel=-27.0 - V_EGO)]
    if same_direction_seen:
      # A car using the middle lane in OUR direction: the only positive evidence it is a travel lane
      tracks.append(track(60, LANE_W, v_rel=2.0))
    adj = AdjacentLane()
    for _ in range(DEBOUNCE_FRAMES):
      upd(adj, FakeSM(tracks, left_edge=left_edge), V_EGO, MAX_D, strict=strict)
    return adj.left

  def test_strict_assumes_turn_lane(self):
    assert self._side(strict=True, same_direction_seen=False).blocks_oncoming

  def test_lenient_assumes_travel_lane(self):
    assert not self._side(strict=False, same_direction_seen=False).blocks_oncoming

  def test_evidence_of_a_travel_lane_unblocks_it_even_when_strict(self):
    # One car down the passing lane in our direction is all it takes. This is what gives US-6 and
    # US-89 their passing lanes back without loosening the setting.
    assert not self._side(strict=True, same_direction_seen=True).blocks_oncoming

  def test_oncoming_in_the_next_lane_blocks_regardless_of_the_setting(self):
    # Not a judgment call, so the flag must not reach it. Two-lane road: opposing traffic IS the
    # next lane over.
    left_edge, onc, _ = road(ego_offset_from_left=0, oncoming_lanes=1)
    for strict in (True, False):
      adj = AdjacentLane()
      upd(adj, FakeSM([track(100, -onc[0], v_rel=-27.0 - V_EGO)], left_edge=left_edge),
                 V_EGO, MAX_D, strict=strict)
      assert adj.left.blocks_oncoming, f"strict={strict}"
    


class TestTurnLaneEvidenceQuality:
  """The discriminator leaks if any moving vehicle counts as proof of a travel lane.

  A car slowing into a center turn lane is still moving -- 6 or 7 m/s while it decelerates, well
  over MIN_MOVING_MS -- so without a speed floor it would vouch for the lane it is about to stop
  in, and unblock a pass into that exact lane.
  """

  @staticmethod
  def _left_after(middle_lane_v_abs):
    left_edge, onc, _ = road(ego_offset_from_left=0, twltl=True, oncoming_lanes=1)
    tracks = [track(100, -onc[0], v_rel=-27.0 - V_EGO),
              track(60, LANE_W, v_rel=middle_lane_v_abs - V_EGO)]
    adj = AdjacentLane()
    for _ in range(DEBOUNCE_FRAMES):
      upd(adj, FakeSM(tracks, left_edge=left_edge), V_EGO, MAX_D, strict=True)
    return adj.left

  def test_a_car_entering_a_turn_lane_does_not_vouch_for_it(self):
    # 7 m/s while we do 30: moving, but nothing like travelling.
    assert not self._left_after(7.0).same_direction_recent
    assert self._left_after(7.0).blocks_oncoming

  def test_a_car_actually_using_the_lane_does_vouch_for_it(self):
    assert self._left_after(V_EGO).same_direction_recent
    assert not self._left_after(V_EGO).blocks_oncoming

  def test_the_floor_scales_with_our_own_speed(self):
    # The same absolute speed means different things at different road speeds, so the threshold is
    # a fraction rather than a number.
    assert self._left_after(SAME_DIRECTION_MIN_FRACTION * V_EGO + 1.0).same_direction_recent
    assert not self._left_after(SAME_DIRECTION_MIN_FRACTION * V_EGO - 1.0).same_direction_recent


class TestGateShapes:
  """Every gate must evaluate to a bool on attribute access, not return a callable.

  `blocks_oncoming` was briefly a method taking the strict flag. A bound method is truthy, so every
  `assert not side.blocks_oncoming` in this file passed unconditionally and `assert
  side.blocks_oncoming` passed without measuring anything -- the suite went green while the gate was
  untested. The call site in passing_assist.py had the same problem in the other direction: a side
  that should not have blocked would have blocked always.

  The neighbouring RearApproachSide.blocks_lane_change is a property, so this also keeps the two
  gates the decision chain reads side by side from having different shapes.
  """

  GATES = ('blocks_oncoming', 'same_direction_recent', 'available', 'occupied', 'oncoming')

  def test_gates_are_properties_not_methods(self):
    side = AdjacentLaneSide()
    for name in self.GATES:
      value = getattr(side, name)
      assert isinstance(value, bool), f"{name} is {type(value).__name__}, expected bool"

  def test_the_aggregate_gates_are_properties_too(self):
    adj = AdjacentLane()
    for name in ('available', 'oncoming_any_side'):
      assert isinstance(getattr(adj, name), bool), name
    assert isinstance(adj.oncoming_seconds_left, float)


class TestPathOffset:
  def test_straight_path_is_zero_everywhere(self):
    assert path_offset(NS(position=path()), 80.0) == 0.0

  def test_interpolates_between_model_points(self):
    # 70 m is between X_IDXS points, so this exercises the interpolation rather than a lucky hit.
    assert abs(path_offset(NS(position=path(500.0)), 70.0) - (70.0 ** 2) / 1000.0) < 0.15

  def test_missing_path_degrades_to_straight_ahead(self):
    # No position field at all: the old behavior, not a crash and not an arbitrary number.
    assert path_offset(NS(), 80.0) == 0.0


class TestBarrierGeometry:
  """Reported from a drive: a barrier occasionally read as an adjacent car, rarely.

  Radar range-rate is the RADIAL component, so a stationary object reports -v_ego*cos(theta), not
  -v_ego. The old `v_ego + v_rel` left a residue of v_ego*(1 - cos(theta)) that grows as the object
  moves off boresight -- invisible at range, and enough to clear MIN_MOVING_MS close alongside at
  speed. That is exactly the "rare" in the report.
  """

  @staticmethod
  def barrier(d_rel, lat=4.0, v_ego=V_EGO):
    """What the radar actually reports for a stationary object at that position."""
    import math
    cos_t = d_rel / math.hypot(d_rel, lat)
    return track(d_rel, lat, v_rel=-v_ego * cos_t)

  def test_a_close_barrier_at_speed_is_still_furniture(self):
    # The failing case: 67 mph, barrier 5 m ahead and 4 m over. Read naively it is 6.6 m/s.
    adj = feed(AdjacentLane(), [self.barrier(5.0)])
    assert not adj.left.occupied

  def test_a_barrier_is_furniture_at_every_range(self):
    for d in (5.0, 8.0, 15.0, 40.0, 100.0):
      adj = feed(AdjacentLane(), [self.barrier(d)])
      assert not adj.left.occupied, f"barrier at {d} m read as a vehicle"

  def test_the_correction_is_exact_for_a_stationary_object(self):
    for d in (4.0, 10.0, 60.0):
      import math
      cos_t = d / math.hypot(d, 4.0)
      assert abs(ground_speed(V_EGO, d, 4.0, -V_EGO * cos_t)) < 0.01

  def test_a_real_car_still_reads_its_own_speed(self):
    # Same-direction traffic at 25 m/s, close alongside where the correction is largest.
    v_car = 25.0
    import math
    cos_t = 6.0 / math.hypot(6.0, 4.0)
    v_rel = (v_car - V_EGO) * cos_t
    assert abs(ground_speed(V_EGO, 6.0, 4.0, v_rel) - v_car) < 0.01

  def test_oncoming_still_reads_negative(self):
    import math
    cos_t = 60.0 / math.hypot(60.0, 3.7)
    v_rel = (-27.0 - V_EGO) * cos_t
    assert abs(ground_speed(V_EGO, 60.0, 3.7, v_rel) - (-27.0)) < 0.01


class TestStationaryRejection:
  """This radar publishes barriers and sign gantries as ordinary tracks -- no classification of any
  kind exists upstream. A guardrail sits squarely inside the adjacent-lane band, and without this
  filter it reads as a car doing 0 mph and blocks every pass for the length of the barrier."""

  def test_guardrail_is_not_a_vehicle(self):
    # 3.5 m to the left, closing at exactly ego speed: a stationary object.
    adj = feed(AdjacentLane(), [track(60, 3.5, v_rel=-V_EGO)])
    assert not adj.left.occupied

  def test_barrier_does_not_hide_a_real_vehicle_behind_it(self):
    # The barrier is nearer, so a "nearest wins" rule that ran before this filter would pick the
    # barrier and report the lane stopped. The car must still be found.
    adj = feed(AdjacentLane(), [track(30, 3.4, v_rel=-V_EGO), track(70, 3.7, v_rel=-2.0)])
    assert adj.left.occupied
    assert adj.left.d_rel == 70

  def test_traffic_moving_slowly_but_really_moving_still_counts(self):
    adj = feed(AdjacentLane(), [track(60, 3.5, v_rel=MIN_MOVING_MS + 1.0 - V_EGO)])
    assert adj.left.occupied


class TestAvailability:
  def test_empty_radar_frame_is_clear_not_unknown(self):
    adj = feed(AdjacentLane(), [])
    assert adj.left.available and adj.right.available
    assert not adj.left.occupied

  def test_dead_service_is_unavailable(self):
    # The failure this whole availability flag exists for: a radar that stopped reporting must not
    # be indistinguishable from an empty lane.
    adj = feed(AdjacentLane(), [track(50, 3.7)])
    assert adj.left.occupied
    adj.update(FakeSM([], alive=False), V_EGO, MAX_D)
    assert not adj.left.available
    assert not adj.left.occupied

  def test_invalid_service_is_unavailable(self):
    adj = feed(AdjacentLane(), [], alive=True, valid=False)
    assert not adj.available

  def test_missing_service_is_unavailable(self):
    adj = feed(AdjacentLane(), [], present=False)
    assert not adj.available

  def test_unavailable_never_blocks(self):
    adj = feed(AdjacentLane(), [], present=False)
    assert not adj.left.blocks_move(beat_speed=1e3, margin=0.0)


class TestDebounce:
  def test_appearing_needs_consecutive_messages(self):
    adj = AdjacentLane()
    for _ in range(DEBOUNCE_FRAMES - 1):
      adj.update(FakeSM([track(50, 3.7)]), V_EGO, MAX_D)
    assert not adj.left.occupied
    adj.update(FakeSM([track(50, 3.7)]), V_EGO, MAX_D)
    assert adj.left.occupied

  def test_single_dropped_frame_does_not_clear(self):
    # Track lifetimes are short and the object list flickers. Believing one empty frame would
    # produce a reading that oscillates faster than any decision built on it can settle.
    adj = feed(AdjacentLane(), [track(50, 3.7)])
    adj.update(FakeSM([]), V_EGO, MAX_D)
    assert adj.left.occupied

  def test_sustained_absence_clears(self):
    adj = feed(AdjacentLane(), [track(50, 3.7)])
    feed(adj, [])
    assert not adj.left.occupied
    assert adj.left.v_abs == 0.0

  def test_cycles_without_a_new_message_do_not_advance_the_count(self):
    # The rate trap. The planner runs at 20 Hz and liveTracks arrives at ~8.3 Hz, so if the count
    # advanced per cycle a single radar message would satisfy a 3-message debounce on its own.
    adj = AdjacentLane()
    adj.update(FakeSM([track(50, 3.7)]), V_EGO, MAX_D)
    for _ in range(10):
      adj.update(FakeSM([track(50, 3.7)], updated=False), V_EGO, MAX_D)
    assert not adj.left.occupied

  def test_state_holds_across_cycles_without_a_new_message(self):
    adj = feed(AdjacentLane(), [track(50, 3.7, v_rel=-4.0)])
    adj.update(FakeSM([], updated=False), V_EGO, MAX_D)
    assert adj.left.occupied
    assert adj.left.d_rel == 50


class TestBlocksMove:
  @staticmethod
  def occupied_at(v_abs):
    side = AdjacentLaneSide()
    for _ in range(DEBOUNCE_FRAMES):
      side.observe(True, 50.0, 3.7, v_abs - V_EGO, v_abs)
    return side

  def test_slower_than_the_lead_blocks(self):
    # The case this exists for: pull out to pass a car doing 24 and land behind one doing 23.
    assert self.occupied_at(23.0).blocks_move(beat_speed=24.0, margin=1.8)

  def test_faster_than_the_lead_by_the_margin_does_not_block(self):
    assert not self.occupied_at(28.0).blocks_move(beat_speed=24.0, margin=1.8)

  def test_faster_but_inside_the_margin_blocks(self):
    # A one-mph gain is not worth two lane changes, and it is the same threshold that decided the
    # pass was worth wanting in the first place.
    assert self.occupied_at(25.0).blocks_move(beat_speed=24.0, margin=1.8)

  def test_clear_lane_never_blocks(self):
    side = AdjacentLaneSide()
    for _ in range(DEBOUNCE_FRAMES):
      side.observe(False, 0.0, 0.0, 0.0, NO_SPEED_SENTINEL)
    assert side.available
    assert not side.blocks_move(beat_speed=1e3, margin=0.0)

  def test_absolute_speed_comes_from_the_geometry_corrected_value(self):
    # At 50 m the correction is negligible, so this still lands on v_ego + v_rel -- but it goes
    # through ground_speed() rather than assuming boresight. See TestBarrierGeometry.
    v_abs = ground_speed(V_EGO, 50.0, 3.7, -4.0)
    side = AdjacentLaneSide()
    for _ in range(DEBOUNCE_FRAMES):
      side.observe(True, 50.0, 3.7, -4.0, v_abs)
    assert abs(side.v_abs - (V_EGO - 4.0)) < 0.05


class TestOncomingNeedsCorroboration:
  """From a drive: "I was on I-15 for a while, and kept saying two-way road."

  I-15 is divided. One spurious return used to latch the full memory, so on a long interstate run a
  handful of them silenced the feature for the whole drive -- not "a few quiet minutes", which is
  what the single-frame rule was justified on, but the feature not existing on the road it is most
  used on.
  """

  ONCOMING = staticmethod(lambda: track(90, 3.7, v_rel=-27.0 - V_EGO))

  def test_one_bad_return_no_longer_silences_the_road(self):
    adj = AdjacentLane()
    adj.update(FakeSM([self.ONCOMING()]), V_EGO, MAX_D)
    assert not adj.oncoming_any_side, "a single return must not latch a 90 second veto"

  def test_two_are_still_not_enough(self):
    adj = AdjacentLane()
    for _ in range(ONCOMING_FRAMES - 1):
      adj.update(FakeSM([self.ONCOMING()]), V_EGO, MAX_D)
    assert not adj.oncoming_any_side

  def test_real_opposing_traffic_still_trips_it(self):
    """The cost has to be near zero for a genuine one. Opposing traffic closes at 120+ mph and is
    tracked for seconds -- dozens of returns -- so three is invisible."""
    adj = upd(AdjacentLane(), FakeSM([self.ONCOMING()]), V_EGO, MAX_D)
    assert adj.oncoming_any_side
    assert adj.left.oncoming

  def test_returns_scattered_over_minutes_never_add_up(self):
    """The failure mode a plain counter would have: three bad returns far apart eventually
    latching the veto anyway, just more slowly."""
    adj = AdjacentLane()
    for _ in range(ONCOMING_FRAMES + 2):
      adj.update(FakeSM([self.ONCOMING()]), V_EGO, MAX_D)
      for _ in range(int(3.0 / 0.05)):
        adj.update(FakeSM([]), V_EGO, MAX_D)
    assert not adj.oncoming_any_side

  def test_three_returns_in_ONE_message_are_not_three_corroborations(self):
    """The hole the corroboration rule had, and it is the shape of the reported fault.

    Every other test here feeds one track per message, so "three returns" and "three messages" were
    the same thing and nothing distinguished them. They are not the same thing: the count lived on
    the per-track path, so a single radar message carrying three tracks satisfied it outright and
    latched the ninety second veto in one frame -- no time passing, nothing corroborated.

    That is not a hypothetical arrangement of traffic. It is what roadside furniture looks like to
    this radar: a guardrail, a sign gantry or a barrier run publishes several returns at once, at
    slightly different ranges, and nothing upstream separates them from vehicles. Which makes it a
    live candidate for "I was on I-15 for a while, and kept saying two-way road" -- and the
    mitigation written for that report did not cover it.

    Real opposing traffic is unaffected: it is tracked for seconds across dozens of messages.
    """
    adj = AdjacentLane()
    # One message, three tracks -- a barrier run at slightly different ranges.
    adj.update(FakeSM([track(88, 3.7, v_rel=-27.0 - V_EGO),
                       track(90, 3.8, v_rel=-27.0 - V_EGO),
                       track(92, 3.9, v_rel=-27.0 - V_EGO)]), V_EGO, MAX_D)
    assert not adj.oncoming_any_side, "one message latched the veto on its own"
    assert not adj.left.blocks_oncoming

  def test_and_three_separate_messages_still_do(self):
    """The other half: fixing the above must not stop real corroboration working."""
    adj = AdjacentLane()
    for _ in range(ONCOMING_FRAMES):
      adj.update(FakeSM([self.ONCOMING()]), V_EGO, MAX_D)
    assert adj.oncoming_any_side

  def test_the_evidence_is_kept_from_the_first_sighting(self):
    """So a veto that DOES fire can say what fired it -- the only way to tell a real opposing
    carriageway from a bad return without reading a log."""
    adj = upd(AdjacentLane(), FakeSM([self.ONCOMING()]), V_EGO, MAX_D)
    assert adj.left.oncoming_d_rel == 90
    assert adj.left.oncoming_v_abs < -MIN_ONCOMING_MS


class TestUnusableRoadEdge:
  """What happens when the model cannot say where our carriageway ends.

  It used to fall back to the full 15 m band, on the reasoning that over-detecting oncoming traffic
  only costs a quiet stretch of road. The road disagreed -- "I was on I-15 for a while, and kept
  saying two-way road" -- because each firing is a ninety second silence, so on a divided highway a
  leaky fallback is the feature switched off for the drive.
  """

  FAR = 11.0     # beyond the next lane, inside the old band: across a median, most likely
  NEXT = 3.7     # the next lane along: opposing traffic on an ordinary two-lane road

  @staticmethod
  def _onc(lat):
    return track(90, lat, v_rel=-27.0 - V_EGO)

  def test_something_far_out_with_no_edge_is_not_believed(self):
    adj = upd(AdjacentLane(), FakeSM([self._onc(self.FAR)], edge_stds=(9.9, 9.9)), V_EGO, MAX_D)
    assert not adj.oncoming_any_side

  def test_but_the_next_lane_still_is(self):
    """The road this veto exists for. Opposing traffic on a two-lane highway is in the next lane,
    and that claim needs no knowledge of where the carriageway ends."""
    adj = upd(AdjacentLane(), FakeSM([self._onc(self.NEXT)], edge_stds=(9.9, 9.9)), V_EGO, MAX_D)
    assert adj.oncoming_any_side
    assert adj.left.oncoming

  def test_a_trusted_edge_restores_the_full_band(self):
    """The cost is scoped, not paid everywhere: with a usable edge, traffic two lanes out on an
    two-way road -- a center turn lane -- is still seen."""
    adj = upd(AdjacentLane(), FakeSM([self._onc(self.FAR)], left_edge=-14.0), V_EGO, MAX_D)
    assert adj.oncoming_any_side

  def test_and_a_trusted_edge_still_excludes_the_far_carriageway(self):
    adj = upd(AdjacentLane(), FakeSM([self._onc(self.FAR)], left_edge=-6.0), V_EGO, MAX_D)
    assert not adj.oncoming_any_side


class TestOncomingPosition:
  """Oncoming vehicles are now DRAWN, not just counted, so where they are has to survive.

  The reason it matters is the I-15 report: a marker over a real car on the far carriageway and a
  marker over empty tarmac are the same log line and completely different bugs. Position is what
  tells them apart.
  """

  def test_the_position_is_carried_through(self):
    adj = upd(AdjacentLane(), FakeSM([track(90, 3.7, v_rel=-27.0 - V_EGO)]), V_EGO, MAX_D)
    assert adj.left.oncoming
    assert adj.left.oncoming_d_rel == 90
    assert adj.left.oncoming_y_rel == 3.7, "drawn from this, so a wrong sign puts it in the median"

  def test_the_right_hand_side_keeps_its_own_sign(self):
    """Radar yRel is left-POSITIVE and the camera frame is not. Getting this backwards would put
    every oncoming marker on the wrong side of the car, which is the failure most likely to be
    read as 'the detection is broken' when the detection was fine."""
    adj = upd(AdjacentLane(), FakeSM([track(90, -3.7, v_rel=-27.0 - V_EGO)]), V_EGO, MAX_D)
    assert adj.right.oncoming
    assert adj.right.oncoming_y_rel == -3.7

  def test_position_is_recorded_from_the_first_sighting(self):
    """Before corroboration completes, so a veto that does fire can be drawn where it was seen
    rather than where it happened to be three frames later."""
    adj = AdjacentLane()
    adj.update(FakeSM([track(120, 3.7, v_rel=-27.0 - V_EGO)]), V_EGO, MAX_D)
    assert not adj.oncoming_any_side, "not corroborated yet"
    assert adj.left.oncoming_d_rel == 120


class TestBeingOvertaken:
  """A forward-looking radar answering a question about what is behind the car.

  The trick is that an overtake is the one event that reveals rear traffic to a front sensor: the
  vehicle was behind us a moment ago and is in front of us now, so it appears CLOSE and PULLING
  AWAY. See overtakenSeconds in custom.capnp for why a rate beats a speed here -- Ford's own
  BlueCruise refuses to merge with traffic much faster than the car, which rules out exactly the
  pass this feature exists to make.

  Measured only. Nothing gates on it yet, and these tests fix the DETECTION so the road data means
  something when it arrives.
  """

  def test_a_car_going_past_us_is_counted(self):
    adj = went_past(AdjacentLane(), [track(18, 3.7, v_rel=8.0)])
    assert adj.left.overtaken_count == 1
    assert adj.left.overtaken_seconds == 0.0
    assert adj.left.overtaken_v_abs > V_EGO

  def test_a_car_we_are_PASSING_is_not(self):
    """The mirror image, and the one that would quietly ruin the measurement. We overtake far more
    cars than overtake us, so counting these would report a permanently busy lane and the number
    would never once say the lane was clear."""
    adj = AdjacentLane()
    adj.update(FakeSM([track(18, 3.7, v_rel=-8.0)]), V_EGO, MAX_D)
    assert adj.left.overtaken_count == 0

  def test_a_car_far_ahead_pulling_away_is_not(self):
    """Distance is what separates an overtake from ordinary traffic. Something 120 m ahead moving
    faster never passed us -- it was always in front."""
    adj = AdjacentLane()
    adj.update(FakeSM([track(120, 3.7, v_rel=8.0)]), V_EGO, MAX_D)
    assert adj.left.overtaken_count == 0

  def test_one_vehicle_is_one_overtake(self):
    """Radar tracks flicker. Counting per frame would turn a single car into a convoy and make a
    quiet lane unreachable."""
    adj = AdjacentLane()
    for _ in range(int(1.5 / 0.05)):
      adj.update(FakeSM([track(18, 3.7, v_rel=8.0)]), V_EGO, MAX_D)
    assert adj.left.overtaken_count == 1

  def test_and_a_second_car_later_is_a_second_overtake(self):
    adj = went_past(AdjacentLane(), [track(18, 3.7, v_rel=8.0)])
    for _ in range(int(4.0 / 0.05)):
      adj.update(FakeSM([]), V_EGO, MAX_D)
    went_past(adj, [track(18, 3.7, v_rel=8.0)])
    assert adj.left.overtaken_count == 2

  def test_the_clock_runs_on_wall_time(self):
    """It has to count up with an EMPTY lane -- that is the whole measurement. A clock that only
    advanced while something was in view could never report a quiet lane."""
    adj = went_past(AdjacentLane(), [track(18, 3.7, v_rel=8.0)])
    for _ in range(int(10.0 / 0.05)):
      adj.update(FakeSM([]), V_EGO, MAX_D)
    assert 9.0 < adj.left.overtaken_seconds < 11.0

  def test_never_overtaken_is_not_reported_as_just_overtaken(self):
    """Zero means "nobody has ever passed us here", and the clock must not start until one has --
    otherwise a fresh drive reads as the busiest possible lane, which is the one direction this
    measurement must never fail in."""
    adj = AdjacentLane()
    for _ in range(int(30.0 / 0.05)):
      adj.update(FakeSM([]), V_EGO, MAX_D)
    assert adj.left.overtaken_count == 0
    assert adj.left.overtaken_seconds == 0.0

  def test_a_radar_dropout_does_not_reset_the_clock(self):
    """Same rule as the oncoming memory: how long since somebody passed us is a fact about the
    road, and losing the sensor is not evidence it changed."""
    adj = went_past(AdjacentLane(), [track(18, 3.7, v_rel=8.0)])
    for _ in range(int(5.0 / 0.05)):
      adj.update(FakeSM([]), V_EGO, MAX_D)
    seen = adj.left.overtaken_seconds
    adj.update(FakeSM([], alive=False), V_EGO, MAX_D)
    assert adj.left.overtaken_count == 1, "a dropout erased the count"
    assert adj.left.overtaken_seconds >= seen, "a dropout restarted the clock"

  def test_each_side_counts_its_own(self):
    adj = went_past(AdjacentLane(), [track(18, -3.7, v_rel=8.0)])
    assert adj.right.overtaken_count == 1
    assert adj.left.overtaken_count == 0

  def test_oncoming_traffic_is_never_an_overtake(self):
    """It is close and its RELATIVE speed is enormous, so a rule written only on vRel would count
    every car on a two-lane road as having just passed us."""
    adj = AdjacentLane()
    for _ in range(ONCOMING_FRAMES + 2):
      adj.update(FakeSM([track(20, 3.7, v_rel=-27.0 - V_EGO)]), V_EGO, MAX_D)
    assert adj.left.overtaken_count == 0


class TestCorroboration:
  """Every latched claim here costs more than one radar message -- INCLUDING the one that opens a
  maneuver rather than closing one.

  `same_direction_seconds` is the only thing that releases the strict turn-lane veto, and it used
  to latch its full ninety seconds from a SINGLE return while the veto it overrides required three
  corroborating messages. So on the one road where the distinction decides anything -- a
  1 + TWLTL + 1 arterial, where blocks_oncoming calls moving into the middle lane neither legal nor
  survivable -- the claim "that lane is safe" was three times cheaper to establish than the claim
  "that lane is oncoming traffic".

  Every other gate in this module is built so that missing evidence costs coverage rather than
  safety. This was the single place where a lone bad return bought a maneuver instead of blocking
  one, and it is the shape of the reported bug: three of six suggestions on the 2026-08-07 drive
  were into the center median turn lane.
  """

  @staticmethod
  def _twltl(same_dir_speed=V_EGO):
    """The ambiguous road: our lane, a two-way left-turn lane, then opposing traffic."""
    left_edge, onc, _ = road(ego_offset_from_left=0, twltl=True, oncoming_lanes=1)
    oncoming = track(100, -onc[0], v_rel=-27.0 - V_EGO)
    vouching = track(60, LANE_W, v_rel=same_dir_speed - V_EGO)
    return left_edge, oncoming, vouching

  def _blocked_after(self, extra_tracks, messages, adj=None, left_edge=None, oncoming=None):
    if adj is None:
      left_edge, oncoming, _ = self._twltl()
      adj = AdjacentLane()
      # Establish the veto first, from opposing traffic alone.
      for _ in range(ONCOMING_FRAMES):
        adj.update(FakeSM([oncoming], left_edge=left_edge), V_EGO, MAX_D, strict=True)
      assert adj.left.blocks_oncoming, "the veto under test was never established"
    for _ in range(messages):
      adj.update(FakeSM([oncoming, *extra_tracks], left_edge=left_edge), V_EGO, MAX_D, strict=True)
    return adj

  def test_one_sighting_does_not_unlock_a_turn_lane(self):
    """The bug. One return in the middle lane used to buy ninety seconds of "this is a travel
    lane", which is exactly long enough to offer the pass."""
    _, _, vouching = self._twltl()
    adj = self._blocked_after([vouching], messages=SAME_DIRECTION_FRAMES - 1)
    assert not adj.left.same_direction_recent
    assert adj.left.blocks_oncoming

  def test_a_car_really_using_the_lane_still_unlocks_it(self):
    """The other half, and the reason the threshold is low. A vehicle holding station beside us is
    the slowest-changing target this radar ever has -- §6 of BP-REAR-RADAR-PLAN.md measured real
    adjacent same-direction tracks living 4.97 to 28.73 s, so corroboration costs it nothing."""
    _, _, vouching = self._twltl()
    adj = self._blocked_after([vouching], messages=SAME_DIRECTION_FRAMES)
    assert adj.left.same_direction_recent
    assert not adj.left.blocks_oncoming

  def test_one_message_cannot_corroborate_itself(self):
    """The failure that made the oncoming rule real, applied to the other side of the decision.

    This radar publishes a guardrail or a sign gantry as several tracks at once at slightly
    different ranges. Counting per TRACK meant one such cluster satisfied three-way corroboration
    in a single frame: no time passed and nothing was corroborated by anything.
    """
    _, _, vouching = self._twltl()
    crowd = [track(50 + 10 * i, LANE_W, v_rel=0.0) for i in range(SAME_DIRECTION_FRAMES + 2)]
    adj = self._blocked_after(crowd, messages=1)
    assert not adj.left.same_direction_recent
    assert adj.left.blocks_oncoming

  def test_a_partial_count_expires_rather_than_accumulating(self):
    """Otherwise single bad returns minutes apart eventually add up to a pass offered into a turn
    lane -- the same reasoning as the oncoming window, in the direction that costs more."""
    left_edge, oncoming, vouching = self._twltl()
    adj = self._blocked_after([vouching], messages=SAME_DIRECTION_FRAMES - 1)
    for _ in range(int((SAME_DIRECTION_WINDOW_S + 0.5) / 0.05)):
      adj.update(FakeSM([oncoming], left_edge=left_edge), V_EGO, MAX_D, strict=True)
    # One more sighting must now be the FIRST of a new run, not the last of the old one.
    adj = self._blocked_after([vouching], messages=1, adj=adj, left_edge=left_edge,
                              oncoming=oncoming)
    assert not adj.left.same_direction_recent
    assert adj.left.blocks_oncoming

  def test_opening_a_maneuver_is_never_cheaper_than_refusing_one(self):
    """The invariant behind all of the above, asserted directly so it survives future tuning.

    Whatever these numbers move to, the evidence that lets the car change lanes must not be easier
    to come by than the evidence that stops it.
    """
    assert SAME_DIRECTION_FRAMES >= ONCOMING_FRAMES


class TestOvertakeCorroboration:
  """OVERTAKE_REARM_S collapses a whole message to one count, so a real car is never a convoy. What
  it cannot do is tell one flicker from one car -- and §6 measured a 0.12-0.48 s median lifetime for
  adjacent-band tracks, which at 8.3 Hz is one or two messages.

  This only feeds a readout today, but it is the readout the rear-radar decision gets made on, and
  he has already reported it reading wrong once: fifty vehicles having overtaken him in a few
  minutes, on a drive where the real number was nearer one.
  """

  def test_a_single_flicker_is_not_a_car(self):
    adj = AdjacentLane()
    adj.update(FakeSM([track(18, 3.7, v_rel=8.0)]), V_EGO, MAX_D)
    adj.update(FakeSM([]), V_EGO, MAX_D)
    assert adj.left.overtaken_count == 0

  def test_sightings_have_to_be_consecutive(self):
    """Two flickers a while apart are two flickers. A run broken by a message showing nothing
    starts over rather than resuming."""
    adj = AdjacentLane()
    for _ in range(OVERTAKE_FRAMES + 2):
      adj.update(FakeSM([track(18, 3.7, v_rel=8.0)]), V_EGO, MAX_D)
      adj.update(FakeSM([]), V_EGO, MAX_D)
    assert adj.left.overtaken_count == 0

  def test_a_real_pass_is_still_counted_exactly_once(self):
    """The cost side. A car going by is inside OVERTAKE_MAX_D_REL_M for seconds, which is dozens of
    messages, so corroboration must not turn one pass into none -- or into two."""
    adj = AdjacentLane()
    for _ in range(int(1.5 / 0.05)):
      adj.update(FakeSM([track(18, 3.7, v_rel=8.0)]), V_EGO, MAX_D)
    assert adj.left.overtaken_count == 1


class TestOncomingEdgeTrust:
  """Which of the two I-15 bugs fired -- recorded, because the drive summary could not say.

  "I was on I-15 for a while, and kept saying two-way road." Diagnosing that from a completed
  drive failed on 2026-08-09 for a specific reason: the stored record held dRel and vAbs and
  nothing else, and those two numbers are produced by both candidate causes. See
  oncomingEdgeTrusted in custom.capnp.
  """

  def test_a_trusted_edge_is_recorded_as_trusted(self):
    """The model placed our carriageway's edge beyond the opposing lane, so a real car over there
    counted as being on our road. Default edge_stds are 0.1 -- well inside MAX_ROAD_EDGE_STD."""
    adj = upd(AdjacentLane(), FakeSM([track(90, 3.7, v_rel=-27.0 - V_EGO)]), V_EGO, MAX_D)
    assert adj.left.oncoming
    assert adj.left.oncoming_edge_trusted is True

  def test_an_untrusted_edge_is_recorded_as_untrusted(self):
    """The fallback band was in force, so whatever fired was within ADJACENT_MAX_M of the car.
    On a divided highway that is not opposing traffic at all -- it is close-range scenery."""
    adj = upd(AdjacentLane(), FakeSM([track(90, 3.7, v_rel=-27.0 - V_EGO)],
                                     edge_stds=(0.9, 0.1)), V_EGO, MAX_D)
    assert adj.left.oncoming, "still inside the narrowed band, so it still fires"
    assert adj.left.oncoming_edge_trusted is False

  def test_it_defaults_to_untrusted(self):
    """An absent measurement reports the CONSERVATIVE answer, never the permissive one. False
    here means "the band was in force", which is the claim that does not require a good edge."""
    assert AdjacentLane().left.oncoming_edge_trusted is False

  def test_it_survives_the_radar_dropping_out(self):
    """It is part of the oncoming record, and that record deliberately outlives the observation.
    Losing it on a dropout would leave a live veto whose provenance silently read as untrusted."""
    adj = upd(AdjacentLane(), FakeSM([track(90, 3.7, v_rel=-27.0 - V_EGO)]), V_EGO, MAX_D)
    assert adj.left.oncoming_edge_trusted is True
    adj.update(FakeSM(present=False), V_EGO, MAX_D)
    assert adj.left.oncoming_seconds > 0.0, "the memory itself must still be there"
    assert adj.left.oncoming_edge_trusted is True
