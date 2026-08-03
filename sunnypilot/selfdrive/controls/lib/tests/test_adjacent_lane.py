"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

BluePilot: tests for adjacent-lane detection from the front radar's off-path tracks.

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
  AdjacentLane, AdjacentLaneSide, ADJACENT_MIN_M, ADJACENT_MAX_M, DEBOUNCE_FRAMES, MIN_MOVING_MS,
  ONCOMING_MAX_M, SAME_DIRECTION_MIN_FRACTION, path_offset,
)

X_IDXS = [192.0 * (i / 32.0) ** 2 for i in range(33)]


def edge_at(y, curve_radius_m=0.0):
  """A road edge offset y metres from the path, bending with it."""
  base = path(curve_radius_m).y
  return NS(x=list(X_IDXS), y=[b + y for b in base])


def path(curve_radius_m=0.0):
  """Model predicted path, straight by default. Positive radius bends RIGHT (camera frame y is
  left-negative), by the small-angle displacement d^2 / 2R."""
  ys = [0.0] * 33 if not curve_radius_m else [(x * x) / (2.0 * curve_radius_m) for x in X_IDXS]
  return NS(x=list(X_IDXS), y=ys)

V_EGO = 30.0
MAX_D = 220.0


def track(d_rel, y_rel, v_rel=0.0):
  """One liveTracks point. yRel LEFT-POSITIVE, as the radar publishes it."""
  return NS(dRel=d_rel, yRel=y_rel, vRel=v_rel)


class FakeSM:
  """SubMaster-shaped: __getitem__ plus alive/valid/updated dicts, and no __contains__."""

  def __init__(self, tracks=(), *, alive=True, valid=True, updated=True, present=True, curve=0.0,
               left_edge=-7.0, right_edge=7.0, edge_stds=(0.1, 0.1)):
    # Road edges relative to the path, in the camera frame: negative left. Default is a wide
    # two-lane road -- the oncoming lane is INSIDE the left edge, which is the undivided case.
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


def feed(adj, tracks, frames=DEBOUNCE_FRAMES, **kw):
  for _ in range(frames):
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
    adj.update(FakeSM([self.ONCOMING()]), V_EGO, MAX_D)
    assert adj.undivided
    assert adj.left.oncoming
    assert adj.oncoming_seen

  def test_no_debounce_on_oncoming(self):
    # Occupancy waits three messages; this must not. Waiting for a second sighting costs a
    # suggestion to pass into a head-on lane, and one sighting is already proof.
    adj = AdjacentLane()
    adj.update(FakeSM([self.ONCOMING()]), V_EGO, MAX_D)
    assert adj.undivided

  def test_oncoming_is_not_counted_as_lane_occupancy(self):
    # It must not fall through into the "is that lane faster" comparison, where a large negative
    # speed would read as very slow traffic and produce the wrong blocked reason.
    adj = feed(AdjacentLane(), [self.ONCOMING()])
    assert not adj.left.occupied

  def test_same_direction_traffic_never_classifies_the_road(self):
    adj = feed(AdjacentLane(), [track(90, 3.7, v_rel=4.0)])
    assert not adj.undivided
    assert not adj.left.oncoming

  def test_a_barrier_is_not_oncoming(self):
    # Stationary is v_abs ~ 0, which is inside neither threshold. A guardrail must not classify
    # every divided highway as two-way.
    adj = feed(AdjacentLane(), [track(60, 3.5, v_rel=-V_EGO)])
    assert not adj.undivided

  def test_divided_highway_opposing_carriageway_is_out_of_band(self):
    # The band does this discrimination for free: an opposing carriageway across a median sits well
    # beyond ADJACENT_MAX_M, so an interstate never trips the veto.
    adj = AdjacentLane()
    adj.update(FakeSM([track(120, 14.0, v_rel=-27.0 - V_EGO)]), V_EGO, MAX_D)
    assert not adj.undivided

  def test_classification_outlives_the_car_that_caused_it(self):
    # The whole point of the memory. On a quiet two-lane road the gaps between meeting cars are
    # exactly when a wrong suggestion would look most convincing.
    adj = AdjacentLane()
    adj.update(FakeSM([self.ONCOMING()]), V_EGO, MAX_D, dt=0.05, memory_s=90)
    for _ in range(200):     # 10 s of empty road
      adj.update(FakeSM([]), V_EGO, MAX_D, dt=0.05, memory_s=90)
    assert adj.undivided
    assert not adj.left.oncoming    # the car is gone...
    assert adj.undivided_seconds > 70   # ...the road is not

  def test_the_memory_does_expire(self):
    adj = AdjacentLane()
    adj.update(FakeSM([self.ONCOMING()]), V_EGO, MAX_D, dt=0.05, memory_s=5)
    for _ in range(120):     # 6 s
      adj.update(FakeSM([]), V_EGO, MAX_D, dt=0.05, memory_s=5)
    assert not adj.undivided
    assert adj.oncoming_seen        # but the drive still records that it happened

  def test_a_dead_radar_does_not_clear_the_classification(self):
    # A sensor dropping out is not evidence the road became one-way.
    adj = AdjacentLane()
    adj.update(FakeSM([self.ONCOMING()]), V_EGO, MAX_D)
    adj.update(FakeSM([], alive=False), V_EGO, MAX_D)
    assert adj.undivided

  def test_memory_decays_on_cycles_with_no_new_radar_message(self):
    # The clock is wall time, not radar time. Decaying only on radar frames would stretch the
    # memory by whatever the message rate happened to be.
    adj = AdjacentLane()
    adj.update(FakeSM([self.ONCOMING()]), V_EGO, MAX_D, dt=0.05, memory_s=10)
    before = adj.undivided_seconds
    for _ in range(20):
      adj.update(FakeSM([], updated=False), V_EGO, MAX_D, dt=0.05, memory_s=10)
    assert adj.undivided_seconds < before - 0.9

  def test_oncoming_on_a_curve_is_still_found(self):
    # Same path-relative geometry as everything else here: a fixed band from the car's axis would
    # lose the oncoming lane on exactly the bends where meeting someone matters most.
    car = track(70, 4.9 + 3.7, v_rel=-27.0 - V_EGO)
    adj = AdjacentLane()
    adj.update(FakeSM([car], curve=-500.0), V_EGO, MAX_D)
    assert adj.undivided
    assert adj.left.oncoming


class TestMedians:
  """The case the lateral band cannot settle by itself.

  A divided highway with a jersey barrier and no grass puts the opposing lane centre around 7 m
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
    adj.update(FakeSM([self.oncoming_at(5.0)], left_edge=-4.0), V_EGO, MAX_D)
    assert not adj.undivided

  def test_two_lane_road_still_trips_it(self):
    # Same lateral distance, but here the road edge is beyond the oncoming lane, because that lane
    # is part of our road. This is the inversion the band cannot see and the edge gets right.
    adj = AdjacentLane()
    adj.update(FakeSM([self.oncoming_at(3.7)], left_edge=-7.0), V_EGO, MAX_D)
    assert adj.undivided

  def test_an_untrusted_road_edge_falls_back_to_the_band(self):
    # Unknown counts as ON our road. Over-detecting costs a quiet stretch; under-detecting costs a
    # suggestion to pass into a head-on lane, and those are not the same size.
    adj = AdjacentLane()
    adj.update(FakeSM([self.oncoming_at(5.0)], left_edge=-4.0, edge_stds=(9.9, 9.9)),
               V_EGO, MAX_D)
    assert adj.undivided

  def test_a_missing_road_edge_falls_back_to_the_band(self):
    adj = AdjacentLane()
    sm = FakeSM([self.oncoming_at(3.7)])
    del sm.data['modelV2'].roadEdges
    adj.update(sm, V_EGO, MAX_D)
    assert adj.undivided

  def test_the_edge_test_follows_a_curve(self):
    # Both the track and the edge are taken path-relative, so a bend must not push a same-road
    # oncoming car outside its own road edge.
    adj = AdjacentLane()
    adj.update(FakeSM([track(70, 4.9 + 3.7, v_rel=-27.0 - V_EGO)], curve=-500.0, left_edge=-7.0),
               V_EGO, MAX_D)
    assert adj.undivided

  def test_the_right_side_edge_comparison_is_not_mirrored(self):
    # Camera frame: left negative, right positive, so "inside the edge" is a different comparison
    # per side. Easy to write once and have backwards on one of them.
    adj = AdjacentLane()
    adj.update(FakeSM([self.oncoming_at(-5.0)], right_edge=4.0), V_EGO, MAX_D)
    assert not adj.undivided
    adj = AdjacentLane()
    adj.update(FakeSM([self.oncoming_at(-3.7)], right_edge=7.0), V_EGO, MAX_D)
    assert adj.right.oncoming


TWLTL_W = 4.27    # 14 ft, the AASHTO preferred width. Minimum 12, maximum 16.
LANE_W = 3.66     # 12 ft, the standard travel lane
SHOULDER = 1.5


def road(*, ego_offset_from_left=0.0, lanes_our_way=1, twltl=False, oncoming_lanes=1,
         divided_median=None):
  """Build one US road configuration, measured out from ego's lane centre.

  Returns (left_edge, oncoming_lane_offsets, same_direction_lane_offsets), all camera-frame
  (negative = left), relative to ego.

  `divided_median` in metres puts a median between our carriageway and theirs; None means undivided
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
  ("two-lane undivided (US-89 typical)",
   dict(ego_offset_from_left=0, oncoming_lanes=1), True, True),
  ("two-lane undivided, lenient mode still blocks",
   dict(ego_offset_from_left=0, oncoming_lanes=1), False, True),
  ("1 + TWLTL + 1 arterial",
   dict(ego_offset_from_left=0, twltl=True, oncoming_lanes=1), True, True),
  ("2 + TWLTL + 2 arterial, ego in the LEFT lane",
   dict(ego_offset_from_left=1, twltl=True, oncoming_lanes=2), True, True),
  ("2 + TWLTL + 2 arterial, ego in the RIGHT lane",
   dict(ego_offset_from_left=0, twltl=True, oncoming_lanes=2), True, True),
  ("four-lane undivided, ego in the RIGHT lane",
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
    adj.update(FakeSM(tracks, left_edge=left_edge), V_EGO, MAX_D, strict=strict)
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
      adj.update(FakeSM(tracks, left_edge=left_edge), V_EGO, MAX_D, strict=strict)
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
    # Not a judgement call, so the flag must not reach it. Two-lane road: opposing traffic IS the
    # next lane over.
    left_edge, onc, _ = road(ego_offset_from_left=0, oncoming_lanes=1)
    for strict in (True, False):
      adj = AdjacentLane()
      adj.update(FakeSM([track(100, -onc[0], v_rel=-27.0 - V_EGO)], left_edge=left_edge),
                 V_EGO, MAX_D, strict=strict)
      assert adj.left.blocks_oncoming, f"strict={strict}"
    


class TestTurnLaneEvidenceQuality:
  """The discriminator leaks if any moving vehicle counts as proof of a travel lane.

  A car slowing into a centre turn lane is still moving -- 6 or 7 m/s while it decelerates, well
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
      adj.update(FakeSM(tracks, left_edge=left_edge), V_EGO, MAX_D, strict=True)
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
    for name in ('available', 'undivided'):
      assert isinstance(getattr(adj, name), bool), name
    assert isinstance(adj.undivided_seconds, float)


class TestPathOffset:
  def test_straight_path_is_zero_everywhere(self):
    assert path_offset(NS(position=path()), 80.0) == 0.0

  def test_interpolates_between_model_points(self):
    # 70 m is between X_IDXS points, so this exercises the interpolation rather than a lucky hit.
    assert abs(path_offset(NS(position=path(500.0)), 70.0) - (70.0 ** 2) / 1000.0) < 0.15

  def test_missing_path_degrades_to_straight_ahead(self):
    # No position field at all: the old behaviour, not a crash and not an arbitrary number.
    assert path_offset(NS(), 80.0) == 0.0


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
      side.observe(True, 50.0, 3.7, v_abs - V_EGO, V_EGO)
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
      side.observe(False, 0.0, 0.0, 0.0, V_EGO)
    assert side.available
    assert not side.blocks_move(beat_speed=1e3, margin=0.0)

  def test_absolute_speed_is_derived_from_ego(self):
    side = AdjacentLaneSide()
    for _ in range(DEBOUNCE_FRAMES):
      side.observe(True, 50.0, 3.7, -4.0, V_EGO)
    assert side.v_abs == V_EGO - 4.0
