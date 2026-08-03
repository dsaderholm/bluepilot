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
  path_offset,
)

X_IDXS = [192.0 * (i / 32.0) ** 2 for i in range(33)]


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

  def __init__(self, tracks=(), *, alive=True, valid=True, updated=True, present=True, curve=0.0):
    self.data = {'modelV2': NS(position=path(curve))}
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
