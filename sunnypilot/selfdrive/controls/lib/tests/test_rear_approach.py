"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FusionPilot: tests for the rear-approach interface.

No source is fitted, so these are interface tests, not behavior tests. They pin the two decisions
that would be expensive to get wrong once a sensor arrives:

  - unavailable must never read as clear, at any layer
  - a BLIS source must not be able to masquerade as a radar source in the log
"""

from openpilot.sunnypilot.selfdrive.controls.lib.rear_approach import (
  RearApproach, RearApproachSide, Source, UNSAFE_TTC_S, MIN_CLOSING_MS, NO_THREAT_TTC_S,
)


class TestUnavailableIsNotClear:
  def test_defaults_to_unavailable(self):
    side = RearApproachSide()
    assert not side.available
    assert side.source == Source.none

  def test_unavailable_does_not_block(self):
    """It must not block -- with no sensor fitted, blocking would disable passing entirely and
    bury the reason. The caller checks `available` and decides; that policy is deliberately not
    hidden in here."""
    side = RearApproachSide()
    assert not side.blocks_lane_change

  def test_unavailable_is_distinguishable_from_clear(self):
    """The whole point. A clear lane and a missing sensor must not look the same."""
    missing = RearApproachSide()
    clear = RearApproachSide()
    clear.from_radar(d_rel=90.0, v_rel=0.0)
    assert not missing.blocks_lane_change
    assert not clear.blocks_lane_change
    assert missing.available != clear.available

  def test_no_source_fitted_today(self):
    rear = RearApproach()
    rear.update(sm=None)
    assert not rear.available
    assert not rear.left.blocks_lane_change
    assert not rear.right.blocks_lane_change


class TestRadarSource:
  def test_fast_close_vehicle_blocks(self):
    side = RearApproachSide()
    side.from_radar(d_rel=60.0, v_rel=11.0)   # ~5.5 s
    assert side.closing and side.blocks_lane_change
    assert side.ttc < UNSAFE_TTC_S

  def test_distant_closer_does_not_block(self):
    side = RearApproachSide()
    side.from_radar(d_rel=200.0, v_rel=11.0)  # ~18 s
    assert side.closing and not side.blocks_lane_change

  def test_matching_speed_does_not_block(self):
    # Someone sitting in the next lane at our speed is not an approach.
    side = RearApproachSide()
    side.from_radar(d_rel=40.0, v_rel=0.2)
    assert not side.closing
    assert side.ttc == NO_THREAT_TTC_S
    assert not side.blocks_lane_change

  def test_falling_behind_does_not_block(self):
    side = RearApproachSide()
    side.from_radar(d_rel=40.0, v_rel=-5.0)
    assert not side.closing and not side.blocks_lane_change

  def test_noise_below_threshold_is_not_closing(self):
    side = RearApproachSide()
    side.from_radar(d_rel=50.0, v_rel=MIN_CLOSING_MS - 0.1)
    assert not side.closing

  def test_source_recorded(self):
    side = RearApproachSide()
    side.from_radar(d_rel=50.0, v_rel=5.0)
    assert side.source == Source.radar


class TestBlisSource:
  def test_detection_blocks_when_approach_is_unknown(self):
    # BLIS cannot tell approach from presence, so presence is treated as blocking.
    side = RearApproachSide()
    side.from_blis(detected=True)
    assert side.blocks_lane_change
    assert side.source == Source.blis

  def test_clear_does_not_block(self):
    side = RearApproachSide()
    side.from_blis(detected=False)
    assert side.available and not side.blocks_lane_change

  def test_carries_no_range_or_rate(self):
    """A BLIS-sourced decision must not look range-checked in the log."""
    side = RearApproachSide()
    side.from_blis(detected=True)
    assert side.d_rel == 0.0
    assert side.v_rel == 0.0
    assert side.source != Source.radar

  def test_explicit_not_closing_does_not_block(self):
    # If sodStat/sodAlert turn out to distinguish, presence alone stops being a veto.
    side = RearApproachSide()
    side.from_blis(detected=True, closing=False)
    assert not side.blocks_lane_change


class TestSidesAreIndependent:
  def test_left_threat_does_not_affect_right(self):
    rear = RearApproach()
    rear.left.from_radar(d_rel=40.0, v_rel=12.0)
    rear.right.from_radar(d_rel=250.0, v_rel=0.0)
    assert rear.left.blocks_lane_change
    assert not rear.right.blocks_lane_change

  def test_available_is_true_if_either_side_is(self):
    rear = RearApproach()
    assert not rear.available
    rear.right.from_blis(detected=False)
    assert rear.available
