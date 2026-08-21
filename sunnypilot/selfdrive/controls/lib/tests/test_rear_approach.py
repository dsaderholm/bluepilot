"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FusionPilot: tests for the rear-approach interface.

Neither source is live on his car, but BOTH PRODUCERS ARE WIRED, so TestTheProducer at the bottom
drives RearApproach.update itself rather than assigning fields. That distinction found a real bug:
the empty-lane radar branch set `available` without setting `source`, so a working radar reporting
a CLEAR lane failed the authorization gate while the same radar reporting a car passed it. Every
hand-built fixture in the tree assigns the two together and none of them could have seen it.

The decisions these pin, all expensive to get wrong once a sensor arrives:

  - unavailable must never read as clear, at any layer
  - a BLIS source must not be able to masquerade as a radar source in the log
  - a source must be named wherever availability is claimed
  - a dying radar must fall back to BLIS rather than going blind
"""

from types import SimpleNamespace as NS

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


class _FakeSM:
  """Enough SubMaster to drive RearApproach.update for real.

  THE POINT IS THAT IT IS THE REAL PRODUCER. The actuation-gate tests in test_passing_assist.py
  set `available` and `source` together by hand, which guarantees an invariant the producer did
  not hold: the empty-lane radar branch left `source` at none, so a working radar reporting a
  CLEAR lane failed `_rear_can_authorize` while the same radar reporting a car passed it. A
  fixture that assigns both fields can never see that. Drive the class.
  """

  def __init__(self, data, valid=None, updated=None):
    self.data = data
    self.valid = {k: True for k in data} if valid is None else valid
    self.updated = {k: True for k in data} if updated is None else updated

  def __getitem__(self, k):
    return self.data[k]


def _radar(dataAvailable=True, left=None, right=None):
  empty = NS(detected=False, dRel=0.0, vRel=0.0)
  return NS(dataAvailable=dataAvailable, left=left or empty, right=right or empty)


class TestTheProducer:
  def test_a_clear_lane_under_radar_is_still_SOURCED_BY_THE_RADAR(self):
    """The bug this class exists for. Availability without a source reads as 'nobody is watching'
    to may_actuate, so a radar that saw an empty lane could not open the pass it was fitted for."""
    rear = RearApproach()
    rear.update(_FakeSM({'rearRadarBP': _radar()}))
    for side in (rear.left, rear.right):
      assert side.available
      assert not side.blocks_lane_change
      assert side.source == Source.radar, "a live radar reporting a clear lane named no source"

  def test_a_target_behind_is_sourced_by_the_radar_too(self):
    rear = RearApproach()
    rear.update(_FakeSM({'rearRadarBP': _radar(left=NS(detected=True, dRel=40.0, vRel=12.0))}))
    assert rear.left.source == Source.radar and rear.left.blocks_lane_change
    assert rear.right.source == Source.radar and not rear.right.blocks_lane_change

  def test_no_message_at_all_leaves_both_sides_unavailable(self):
    rear = RearApproach()
    rear.update(_FakeSM({}))
    assert not rear.available
    assert rear.left.source == Source.none

  def test_blis_fills_when_no_digest_is_arriving(self):
    rear = RearApproach()
    rear.update(_FakeSM({'carStateBP': NS(blisLeft=NS(dataAvailable=True, sodDetect=1),
                                          blisRight=NS(dataAvailable=True, sodDetect=0))}))
    assert rear.left.source == Source.blis and rear.left.blocks_lane_change
    assert rear.right.available and not rear.right.blocks_lane_change

  def test_a_DEAD_RADAR_falls_back_to_blis_rather_than_going_blind(self):
    """dataAvailable false is the feeder saying its own sensor died. Returning bare there would
    drop both sides to unavailable and take every BLIS refusal with them -- so a dying radar would
    make the car LESS careful than one that never had it."""
    rear = RearApproach()
    rear.update(_FakeSM({'rearRadarBP': _radar(dataAvailable=False),
                         'carStateBP': NS(blisLeft=NS(dataAvailable=True, sodDetect=1),
                                          blisRight=NS(dataAvailable=True, sodDetect=0))}))
    assert rear.left.source == Source.blis
    assert rear.left.blocks_lane_change, "a dead radar silently removed the blind-spot veto"

  def test_a_live_radar_wins_over_blis_on_the_same_frame(self):
    rear = RearApproach()
    rear.update(_FakeSM({'rearRadarBP': _radar(),
                         'carStateBP': NS(blisLeft=NS(dataAvailable=True, sodDetect=1),
                                          blisRight=NS(dataAvailable=True, sodDetect=1))}))
    assert rear.left.source == Source.radar and not rear.left.blocks_lane_change

  def test_a_MALFORMED_blis_message_leaves_both_sides_unavailable_not_half_filled(self):
    """A capnp read that raises must not leave a side carrying a reading nothing checked -- and
    must not escape into plannerd, which is how a drive was lost on 2026-08-18."""
    rear = RearApproach()
    rear.update(_FakeSM({'carStateBP': NS(blisLeft=NS(dataAvailable=True, sodDetect=1),
                                          blisRight=NS(dataAvailable=True))}))   # no sodDetect
    assert not rear.left.available and not rear.right.available

  def test_stale_state_does_not_survive_a_frame_with_no_source(self):
    rear = RearApproach()
    rear.update(_FakeSM({'carStateBP': NS(blisLeft=NS(dataAvailable=True, sodDetect=1),
                                          blisRight=NS(dataAvailable=True, sodDetect=1))}))
    assert rear.left.blocks_lane_change
    rear.update(_FakeSM({}))
    assert not rear.available and not rear.left.blocks_lane_change
