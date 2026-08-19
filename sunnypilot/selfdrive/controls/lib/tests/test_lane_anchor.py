"""The right-edge lane anchor.

Every test here is written against the SAFETY DIRECTION: this may only refuse a pass, never open
one, so an ambiguous input must produce None or "not leftmost". A wrong-but-confident answer in the
permissive direction is worse than no anchor, which is the whole reason the module exists in the
shape it does.
"""
from openpilot.sunnypilot.selfdrive.controls.lib.lane_anchor import (
  LANE_WIDTH_M, MAX_LATCH_S, LaneAnchor, lane_bounds_from_lines, lane_index_from_edge,
  lanes_to_our_left,
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
    """The point of the whole class: a reading is intermittent, so the estimate has to persist
    across the frames with none. The edge needs the lines to agree before it counts at all now,
    so the reading here is corroborated -- 4.6 m is lane 1, and 1..3 is what the lines allow."""
    a = LaneAnchor()
    assert a.update(0.05, 4.6, 0.2, 5, True, 0.9, 0.9) == 1
    for _ in range(20):
      assert a.update(0.05, None, 9.9, 5, True, 0.9, 0.9) == 1
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
    a.update(0.05, 4.6, 0.2, 5, True, 0.9, 0.9)          # lane 1, lines allow 1..3
    assert a.update(0.05, HALF, 0.2, 5, True, 0.9, 0.02) == 0    # no line right -> lane 0

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
    a.update(0.05, HALF + 4 * LANE_WIDTH_M, 0.2, 5, True, 0.02, 0.9)
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


class TestFourLineBound:
  """The middle-lane fix. He watched the strip go blank whenever he moved to a middle lane, and it
  was honest: the right edge is out of reach there and the far-left line is present because a lane
  really is to the left, so both prior witnesses fall silent at once.

  Reading all four lines answers it -- and on a three-lane road it pins the middle exactly.
  """
  def test_three_lanes_both_outer_lines_pins_the_middle(self):
    """THE CASE HE REPORTED. Lines each side of us, three lanes total: only lane 1 fits."""
    assert lane_bounds_from_lines(0.9, 0.9, 3) == (1, 1)

  def test_no_line_left_is_the_leftmost_lane(self):
    assert lane_bounds_from_lines(0.02, 0.9, 4) == (3, 3)

  def test_no_line_right_is_the_rightmost_lane(self):
    assert lane_bounds_from_lines(0.9, 0.02, 4) == (0, 0)

  def test_four_lanes_both_present_narrows_without_pinning(self):
    """A range is still worth having: lanes_to_our_left only needs 'not at either end'."""
    assert lane_bounds_from_lines(0.9, 0.9, 4) == (1, 2)

  def test_five_lanes_both_present(self):
    assert lane_bounds_from_lines(0.9, 0.9, 5) == (1, 3)

  def test_both_outer_lines_absent_is_a_contradiction_not_a_guess(self):
    """Cannot be leftmost AND rightmost on a multi-lane road. Claim nothing rather than pick."""
    assert lane_bounds_from_lines(0.02, 0.02, 4) is None

  def test_two_lanes_with_a_line_each_side_is_inconsistent(self):
    """On two lanes there is no 'strictly between', so both-present cannot be true."""
    assert lane_bounds_from_lines(0.9, 0.9, 2) is None

  def test_single_lane_road_claims_nothing(self):
    assert lane_bounds_from_lines(0.02, 0.02, 1) is None

  def test_missing_probability_propagates(self):
    assert lane_bounds_from_lines(None, 0.9, 3) is None
    assert lane_bounds_from_lines(0.9, None, 3) is None

  def test_no_lane_count_claims_nothing(self):
    assert lane_bounds_from_lines(0.9, 0.9, None) is None
    assert lane_bounds_from_lines(0.9, 0.9, 0) is None

  def test_garbage_is_none_rather_than_an_exception(self):
    assert lane_bounds_from_lines("high", 0.9, 3) is None


class TestTheBoundReachesTheAnchor:
  def test_a_pinned_middle_lane_becomes_an_index_with_no_edge_at_all(self):
    """The whole point: no usable right edge, and it still knows the lane."""
    a = LaneAnchor()
    assert a.update(0.05, None, 9.9, 3, True, 0.9, 0.9) == 1
    assert a.confident, "a pin from the lines is a measurement, not a latch"

  def test_the_lines_win_when_they_contradict_the_edge(self):
    """REVERSED 2026-08-19 on measured evidence. The edge reads to the outer edge of the SHOULDER,
    so it lands about one lane left of the truth wherever a shoulder exists. Here it claims the
    rightmost lane while the lines report paint on both sides of us, which cannot both be true."""
    a = LaneAnchor()
    assert a.update(0.05, HALF, 0.2, 3, True, 0.9, 0.9) == 1

  def test_the_edge_narrows_a_range_it_agrees_with(self):
    """It is still worth having. On five lanes the lines only bound us to 1..3; a corroborating
    edge reading picks which one, and that is the one job it can still do honestly."""
    a = LaneAnchor()
    assert a.update(0.05, HALF + 2 * LANE_WIDTH_M, 0.2, 5, True, 0.9, 0.9) == 2

  def test_an_uncorroborated_edge_reading_is_refused(self):
    """With no lane-line bound there is nothing to check the edge against, and an unchecked edge
    was wrong on 231 of the 289 frames where it could be checked. Unknown beats biased."""
    a = LaneAnchor()
    assert a.update(0.05, HALF, 0.2, 3, True, None, None) is None

  def test_a_range_does_not_become_an_index(self):
    a = LaneAnchor()
    assert a.update(0.05, None, 9.9, 5, True, 0.9, 0.9) is None

  def test_leftmost_from_the_bound_on_a_wide_road(self):
    a = LaneAnchor()
    a.update(0.05, None, 9.9, 5, True, 0.02, 0.9)
    assert a.in_leftmost_lane() is True


class TestTheTwoWitnessesCrossCheck:
  """The edge and the lines measure the same quantity by completely different means, so they can
  be compared. This is MEASUREMENT ONLY today -- the edge still wins -- and these tests exist so
  the flag is trustworthy when the replay reports a number for it.
  """

  def test_edge_says_rightmost_while_the_lines_say_leftmost(self):
    """The sharpest disagreement there is: 1.85 m from the right edge, and no line to our left on
    a three-lane road. Both cannot be true, and this is the shape a bad edge reading takes."""
    a = LaneAnchor()
    a.update(0.05, HALF, 0.2, 3, True, 0.02, 0.9)
    assert a.edge_index == 0
    assert a.line_bounds == (2, 2)
    assert a.contradiction is True

  def test_the_edge_inside_the_range_agrees(self):
    a = LaneAnchor()
    a.update(0.05, HALF + LANE_WIDTH_M, 0.2, 3, True, 0.9, 0.9)   # edge 1, lines pin 1
    assert a.edge_index == 1
    assert a.contradiction is False

  def test_the_edge_outside_the_range_is_flagged(self):
    """Edge says the rightmost lane while the lines report a line beyond our right. One is wrong."""
    a = LaneAnchor()
    a.update(0.05, HALF, 0.2, 3, True, 0.9, 0.9)    # edge 0, lines say strictly between -> (1, 1)
    assert a.edge_index == 0
    assert a.contradiction is True

  def test_a_contradicted_edge_reading_is_discarded(self):
    """The flag is still set for the replay to count, and the reading no longer reaches the index."""
    a = LaneAnchor()
    assert a.update(0.05, HALF, 0.2, 3, True, 0.9, 0.9) == 1
    assert a.contradiction is True
    assert a.edge_index == 0, "what the edge said is still recorded, it just does not win"

  def test_no_bound_is_not_a_contradiction(self):
    a = LaneAnchor()
    a.update(0.05, HALF, 0.2, 3, True, None, None)
    assert a.contradiction is False

  def test_no_edge_reading_is_not_a_contradiction(self):
    """A lane pinned by the lines alone has nothing to disagree with."""
    a = LaneAnchor()
    a.update(0.05, None, 9.9, 3, True, 0.9, 0.9)
    assert a.edge_index is None
    assert a.contradiction is False
