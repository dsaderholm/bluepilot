"""
BluePilot: measuring a pass that is taking too long.

This feeds the one thing passing assist may ever do to the set speed, so the cases that matter are
the ones where it would fire when it should not -- a nudge handed out for an ordinary car in the
next lane would be a car quietly speeding up for no reason the driver can see.
"""

from types import SimpleNamespace as NS

from cereal import custom
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot.selfdrive.controls.lib.overtake_progress import (
  OvertakeProgress, CLOSE_M, SLOW_GAIN_MPH, AFTER_SUGGESTION_S,
)

Side = custom.LongitudinalPlanSP.PassingAssist.Side

CRUISE_MS = 31.0


def lane(occupied=True, d_rel=25.0, v_rel=-0.5, available=True):
  """One adjacent side. v_rel is THEIRS relative to ours, so gaining on them is negative."""
  return NS(available=available, occupied=occupied, d_rel=d_rel, v_rel=v_rel)


EMPTY = lane(occupied=False)


def run(op, seconds, left=EMPTY, right=EMPTY, v_ego=CRUISE_MS, settle_s=0.0,
        since_lane_change_s=0.0):
  for _ in range(max(1, int(round(seconds / DT_MDL)))):
    op.update(v_ego, left, right, settle_s, since_lane_change_s)
  return op


class TestItFiresOnARealGrind:
  def test_barely_gaining_on_a_close_car_counts(self):
    op = run(OvertakeProgress(), 10.0, left=lane(d_rel=20.0, v_rel=-0.4))
    assert op.crawling
    assert op.crawl_side == Side.left
    assert op.crawl_events == 1

  def test_matching_their_speed_exactly_is_the_worst_case_not_an_exempt_one(self):
    """Sitting in the passing lane gaining nothing at all is the situation this exists for."""
    op = run(OvertakeProgress(), 10.0, left=lane(v_rel=0.0))
    assert op.crawling

  def test_losing_ground_counts_too(self):
    op = run(OvertakeProgress(), 10.0, left=lane(v_rel=0.6))
    assert op.crawling

  def test_the_longest_crawl_survives_the_crawl_ending(self):
    """The number a drive is judged on -- it must outlive the event that produced it."""
    op = run(OvertakeProgress(), 12.0, left=lane())
    run(op, 5.0)
    assert op.crawl_seconds == 0.0
    assert op.crawl_longest >= 12.0 - DT_MDL

  def test_one_crawl_is_one_event(self):
    """Counted once per crawl, not once per frame past the threshold."""
    op = run(OvertakeProgress(), 25.0, left=lane())
    assert op.crawl_events == 1


class TestItStaysQuietOtherwise:
  def test_an_empty_lane_is_not_a_crawl(self):
    op = run(OvertakeProgress(), 20.0)
    assert not op.crawling and op.crawl_events == 0

  def test_passing_a_car_properly_is_not_a_crawl(self):
    """The whole point of the threshold: gaining at a decent clip is an overtake working."""
    op = run(OvertakeProgress(), 20.0, left=lane(v_rel=-(SLOW_GAIN_MPH + 5) * CV.MPH_TO_MS))
    assert not op.crawling
    assert op.crawl_seconds == 0.0

  def test_a_car_far_ahead_in_the_next_lane_is_not_a_crawl(self):
    """Beyond the close range this is just traffic somewhere ahead, not a stuck overtake."""
    op = run(OvertakeProgress(), 20.0, left=lane(d_rel=CLOSE_M + 30))
    assert not op.crawling

  def test_below_passing_speed_nothing_is_measured(self):
    """In town, sitting beside someone at a light is not a pass taking too long."""
    op = run(OvertakeProgress(), 20.0, left=lane(), v_ego=10.0)
    assert not op.crawling and op.crawl_events == 0

  def test_an_unavailable_side_is_not_a_crawl(self):
    """Same rule as everywhere else here: no data must never read as data."""
    op = run(OvertakeProgress(), 20.0, left=lane(available=False))
    assert not op.crawling

  def test_a_break_in_the_grind_restarts_the_clock(self):
    op = run(OvertakeProgress(), 7.0, left=lane())
    run(op, 1.0)
    run(op, 7.0, left=lane())
    assert not op.crawling, "two short grinds are not one long one"


class TestProvenance:
  """crawlAfterSuggestion labels the data; it must never behave like a gate."""

  def test_a_car_beside_you_is_not_a_pass(self):
    """Reversed on road evidence: "it's been saying slow pass even though I'm in the far right
    lane."

    This used to assert that a crawl with no suggestion behind it was still measured, on the
    reasoning that those were the most interesting ones. They were not interesting, they were the
    right lane of a highway with traffic alongside -- _grinding asks "is a car close and am I not
    gaining on it", which is true almost continuously there. It measured being ALONGSIDE someone
    and called it overtaking them.
    """
    op = run(OvertakeProgress(), 10.0, left=lane(),
             settle_s=AFTER_SUGGESTION_S + 100, since_lane_change_s=AFTER_SUGGESTION_S + 100)
    assert not op.crawling, "a car beside us with no pass underway counted as a slow pass"
    assert op.crawl_events == 0

  def test_but_a_pass_the_driver_made_himself_still_counts(self):
    """The over-correction to avoid. Gating on the suggestion alone would stop measuring the passes
    he makes on his own -- which are most of them, and the ones this number was wanted for."""
    op = run(OvertakeProgress(), 10.0, left=lane(),
             settle_s=AFTER_SUGGESTION_S + 100, since_lane_change_s=2.0)
    assert op.crawling

  def test_a_crawl_soon_after_a_suggestion_is_labeled(self):
    op = run(OvertakeProgress(), 10.0, left=lane(), settle_s=1.0)
    assert op.crawl_after_suggestion

  def test_the_label_is_latched_at_the_start(self):
    """Deciding it per frame would let a long crawl rewrite its own provenance as the settle timer
    ran out underneath it -- so a 40 s grind that began right after a suggestion would end up
    filed as unrelated."""
    op = OvertakeProgress()
    run(op, 1.0, left=lane(), settle_s=1.0)
    run(op, 20.0, left=lane(), settle_s=AFTER_SUGGESTION_S + 100)
    assert op.crawl_after_suggestion
