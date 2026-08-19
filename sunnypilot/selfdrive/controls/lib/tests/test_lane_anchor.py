"""The right-edge lane anchor.

Every test here is written against the SAFETY DIRECTION: this may only refuse a pass, never open
one, so an ambiguous input must produce None or "not leftmost". A wrong-but-confident answer in the
permissive direction is worse than no anchor, which is the whole reason the module exists in the
shape it does.
"""
from openpilot.sunnypilot.selfdrive.controls.lib.lane_anchor import (
  LANE_WIDTH_M, MAX_LATCH_S, LaneAnchor, lane_index_from_edge, lanes_to_our_left,
)

HALF = LANE_WIDTH_M / 2.0


class TestLaneIndexFromEdge:
  def test_centred_in_the_rightmost_lane_is_index_zero(self):
    assert lane_index_from_edge(HALF, 4) == 0

  def test_one_lane_in(self):
    assert lane_index_from_edge(HALF + LANE_WIDTH_M, 4) == 1

  def test_two_lanes_in(self):
    assert lane_index_from_edge(HALF + 2 * LANE_WIDTH_M, 4) == 2

  def test_the_measured_median_lands_on_lane_one(self):
    """p50 of the trusted right-edge readings on his own drive was 4.6-4.8 m."""
    assert lane_index_from_edge(4.6, 5) == 1
    assert lane_index_from_edge(4.8, 5) == 1

  def test_sign_does_not_matter(self):
    """The frame's y is negative to the left; callers should not have to remember that here."""
    assert lane_index_from_edge(-4.6, 5) == lane_index_from_edge(4.6, 5)

  def test_a_reading_past_the_lane_count_is_refused_not_clamped(self):
    """Clamping would invent a confident answer out of a contradiction. On a 2-lane road a 9 m
    edge reading means the edge is not our shoulder, so the honest output is 'unknown'."""
    assert lane_index_from_edge(HALF + 2 * LANE_WIDTH_M, 2) is None

  def test_slightly_inside_the_rightmost_centre_is_still_lane_zero(self):
    assert lane_index_from_edge(1.0, 3) == 0

  def test_absurdly_far_edge_is_unknown(self):
    assert lane_index_from_edge(40.0, 5) is None

  def test_zero_lane_count_is_unknown_never_one_lane(self):
    """mapd publishes 0 for 'no lanes tag'. Reading that as a single-lane road would refuse every
    pass on 8.5% of frames, and reading it as anything else would invent data."""
    assert lane_index_from_edge(HALF, 0) is None

  def test_none_inputs_propagate(self):
    assert lane_index_from_edge(None, 4) is None
    assert lane_index_from_edge(4.0, None) is None

  def test_garbage_is_unknown_rather_than_an_exception(self):
    assert lane_index_from_edge("wide", 4) is None
    assert lane_index_from_edge(4.0, "four") is None


class TestLanesToOurLeft:
  def test_far_right_of_four(self):
    assert lanes_to_our_left(0, 4) == 3

  def test_far_left_of_four(self):
    assert lanes_to_our_left(3, 4) == 0

  def test_out_of_range_is_unknown(self):
    assert lanes_to_our_left(4, 4) is None
    assert lanes_to_our_left(-1, 4) is None

  def test_none_propagates(self):
    assert lanes_to_our_left(None, 4) is None
    assert lanes_to_our_left(0, None) is None


class TestLatching:
  def test_a_confident_reading_latches_and_survives_the_gap(self):
    """The point of the whole class: the edge is trusted on 5-15% of frames, so the estimate has
    to persist across the 85-95% where it is not."""
    a = LaneAnchor()
    assert a.update(0.05, 4.6, 0.2, 5, True) == 1
    for _ in range(20):
      assert a.update(0.05, None, 9.9, 5, True) == 1
    assert not a.confident, "carrying a latch is not the same as measuring"

  def test_an_untrusted_edge_never_establishes_an_anchor(self):
    a = LaneAnchor()
    assert a.update(0.05, 4.6, 9.9, 5, True) is None

  def test_the_latch_expires(self):
    a = LaneAnchor()
    a.update(0.05, 4.6, 0.2, 5, True)
    assert a.update(MAX_LATCH_S + 1.0, None, 9.9, 5, True) is None

  def test_a_fresh_reading_overrides_a_disagreeing_latch(self):
    a = LaneAnchor()
    a.update(0.05, 4.6, 0.2, 5, True)
    assert a.update(0.05, HALF, 0.2, 5, True) == 0

  def test_a_lane_change_drops_the_anchor_rather_than_following_it(self):
    """Incrementing the index across a change would be dead reckoning on dead reckoning, and an
    aborted change would leave a confident wrong answer."""
    a = LaneAnchor()
    a.update(0.05, 4.6, 0.2, 5, True)
    a.note_lane_change()
    assert a.update(0.05, None, 9.9, 5, True) is None

  def test_a_changed_lane_count_drops_the_anchor(self):
    """A different cross-section means the index refers to a different road."""
    a = LaneAnchor()
    a.update(0.05, 4.6, 0.2, 5, True)
    assert a.update(0.05, None, 9.9, 3, True) is None

  def test_a_two_way_road_refuses_outright(self):
    """THE DANGEROUS CASE. On a two-way way the map's `lanes` counts BOTH directions, so counting
    leftward from the shoulder walks into the oncoming lane and calls it ours."""
    a = LaneAnchor()
    assert a.update(0.05, 4.6, 0.2, 5, False) is None

  def test_going_two_way_drops_an_existing_anchor(self):
    a = LaneAnchor()
    a.update(0.05, 4.6, 0.2, 5, True)
    assert a.update(0.05, 4.6, 0.2, 5, False) is None


class TestLeftmostQuery:
  def test_known_leftmost(self):
    a = LaneAnchor()
    a.update(0.05, HALF + 4 * LANE_WIDTH_M, 0.2, 5, True)
    assert a.in_leftmost_lane() is True

  def test_known_not_leftmost(self):
    a = LaneAnchor()
    a.update(0.05, HALF, 0.2, 5, True)
    assert a.in_leftmost_lane() is False

  def test_unknown_is_not_leftmost(self):
    """The caller is asking 'may I warn him for hogging the left lane'. On no information the
    answer must be no -- which is exactly the slow-pass complaint from 2026-08-19."""
    a = LaneAnchor()
    assert a.in_leftmost_lane() is False
    assert a.to_our_left() is None


class TestTheHogGateUsesTheAnchor:
  """The 2026-08-19 report: the slow-pass warning fired while he was NOT in the far left lane.

  Guards the SUBSTITUTION rather than the arithmetic -- that `not left_geometry_ok` is gone from
  the hog condition. It was a camera-uncertainty proxy standing in for a road fact, and on his
  freeway drives the camera was uncertain on 83-99% of frames, so the warning fired on silence.
  """

  def test_the_hog_condition_no_longer_reads_left_geometry(self):
    import inspect
    from openpilot.sunnypilot.selfdrive.controls.lib import passing_assist
    src = inspect.getsource(passing_assist.PassingAssistDetector._track_lane_hog)
    body = [ln for ln in src.splitlines() if ln.strip().startswith("hogging =")]
    assert body, "the hog condition moved; re-point this test rather than deleting it"
    assert "left_geometry_ok" not in body[0], (
      "the hog gate is back on camera geometry -- it fires on the camera being UNSURE, which is "
      "most of a freeway drive, and that is the reported false warning")
    assert "in_leftmost_lane" in body[0], "the hog gate must ask the anchor"
