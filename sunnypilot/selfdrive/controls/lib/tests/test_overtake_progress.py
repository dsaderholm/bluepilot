"""
FusionPilot: measuring a pass that is taking too long.

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
        since_lane_change_s=0.0, in_leftmost=True):
  for _ in range(max(1, int(round(seconds / DT_MDL)))):
    op.update(v_ego, left, right, settle_s, since_lane_change_s, in_leftmost)
  return op


class TestItFiresOnARealGrind:
  def test_barely_gaining_on_a_close_car_counts(self):
    op = run(OvertakeProgress(), 10.0, right=lane(d_rel=20.0, v_rel=-0.4))
    assert op.crawling
    assert op.crawl_side == Side.right
    assert op.crawl_events == 1

  def test_matching_their_speed_exactly_is_the_worst_case_not_an_exempt_one(self):
    """Sitting in the passing lane gaining nothing at all is the situation this exists for."""
    op = run(OvertakeProgress(), 10.0, right=lane(v_rel=0.0))
    assert op.crawling

  def test_losing_ground_counts_too(self):
    op = run(OvertakeProgress(), 10.0, right=lane(v_rel=0.6))
    assert op.crawling

  def test_the_longest_crawl_survives_the_crawl_ending(self):
    """The number a drive is judged on -- it must outlive the event that produced it."""
    op = run(OvertakeProgress(), 12.0, right=lane())
    run(op, 5.0)
    assert op.crawl_seconds == 0.0
    assert op.crawl_longest >= 12.0 - DT_MDL

  def test_one_crawl_is_one_event(self):
    """Counted once per crawl, not once per frame past the threshold."""
    op = run(OvertakeProgress(), 25.0, right=lane())
    assert op.crawl_events == 1


class TestItStaysQuietOtherwise:
  def test_an_empty_lane_is_not_a_crawl(self):
    op = run(OvertakeProgress(), 20.0)
    assert not op.crawling and op.crawl_events == 0

  def test_passing_a_car_properly_is_not_a_crawl(self):
    """The whole point of the threshold: gaining at a decent clip is an overtake working."""
    op = run(OvertakeProgress(), 20.0, right=lane(v_rel=-(SLOW_GAIN_MPH + 5) * CV.MPH_TO_MS))
    assert not op.crawling
    assert op.crawl_seconds == 0.0

  def test_a_car_far_ahead_in_the_next_lane_is_not_a_crawl(self):
    """Beyond the close range this is just traffic somewhere ahead, not a stuck overtake."""
    op = run(OvertakeProgress(), 20.0, right=lane(d_rel=CLOSE_M + 30))
    assert not op.crawling

  def test_below_passing_speed_nothing_is_measured(self):
    """In town, sitting beside someone at a light is not a pass taking too long."""
    op = run(OvertakeProgress(), 20.0, right=lane(), v_ego=10.0)
    assert not op.crawling and op.crawl_events == 0

  def test_an_unavailable_side_is_not_a_crawl(self):
    """Same rule as everywhere else here: no data must never read as data."""
    op = run(OvertakeProgress(), 20.0, right=lane(available=False))
    assert not op.crawling

  def test_a_break_in_the_grind_restarts_the_clock(self):
    op = run(OvertakeProgress(), 7.0, right=lane())
    run(op, 1.0)
    run(op, 7.0, right=lane())
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
    op = run(OvertakeProgress(), 10.0, right=lane(),
             settle_s=AFTER_SUGGESTION_S + 100, since_lane_change_s=AFTER_SUGGESTION_S + 100)
    assert not op.crawling, "a car beside us with no pass underway counted as a slow pass"
    assert op.crawl_events == 0

  def test_but_a_pass_the_driver_made_himself_still_counts(self):
    """The over-correction to avoid. Gating on the suggestion alone would stop measuring the passes
    he makes on his own -- which are most of them, and the ones this number was wanted for."""
    op = run(OvertakeProgress(), 10.0, right=lane(),
             settle_s=AFTER_SUGGESTION_S + 100, since_lane_change_s=2.0)
    assert op.crawling

  def test_a_crawl_soon_after_a_suggestion_is_labeled(self):
    op = run(OvertakeProgress(), 10.0, right=lane(), settle_s=1.0)
    assert op.crawl_after_suggestion

  def test_the_label_is_latched_at_the_start(self):
    """Deciding it per frame would let a long crawl rewrite its own provenance as the settle timer
    ran out underneath it -- so a 40 s grind that began right after a suggestion would end up
    filed as unrelated."""
    op = OvertakeProgress()
    run(op, 1.0, right=lane(), settle_s=1.0)
    run(op, 20.0, right=lane(), settle_s=AFTER_SUGGESTION_S + 100)
    assert op.crawl_after_suggestion


class TestWhatIsActuallyASlowPass:
  """His correction, and it reframed the whole thing rather than adjusting it:

    "A slow pass would only matter if I'm passing on the left. If I'm passing on the right, I should
    just stay in the right lane. There's no eagerness to get out."
    "I kept getting slow pass warnings saying barely gaining on the car on the left, but obviously I
    wouldn't be gaining on the car on the left because the cars on the left are going faster."

  Every fixture in this file used to put the vehicle on the LEFT, which is the geometry of being
  overtaken rather than of overtaking. The tests encoded the bug and passed.
  """

  def test_a_car_on_the_left_is_never_a_slow_pass(self):
    """Left-lane traffic doing about your speed, which on a highway is continuous."""
    op = run(OvertakeProgress(), 25.0, left=lane(v_rel=0.0))
    assert not op.crawling
    assert op.crawl_events == 0

  def test_a_car_pulling_away_is_not_a_pass_at_all(self):
    """They are 10 mph faster. -v_rel is -10, comfortably under the 5 mph bar, so the old test
    counted every vehicle overtaking us as one we were failing to overtake."""
    op = run(OvertakeProgress(), 25.0, right=lane(v_rel=10 * CV.MPH_TO_MS))
    assert not op.crawling

  def test_but_drifting_back_slightly_mid_pass_still_counts(self):
    """Alongside a lorry that creeps ahead by a mile an hour is the case worth naming, and a hard
    floor at zero would drop it."""
    op = run(OvertakeProgress(), 25.0, right=lane(v_rel=1 * CV.MPH_TO_MS))
    assert op.crawling

  def test_passing_on_the_right_is_deliberately_silent(self):
    """Undertaking has no lane to hurry back to. The car being passed would be on the LEFT, and
    that side is not watched."""
    op = run(OvertakeProgress(), 25.0, left=lane(v_rel=-0.4))
    assert not op.crawling

  def test_the_real_case_still_fires(self):
    """In the left lane, barely gaining on the car you are passing, which is on your right."""
    op = run(OvertakeProgress(), 25.0, right=lane(d_rel=20.0, v_rel=-0.4))
    assert op.crawling
    assert op.crawl_side == Side.right


class TestOnlyFromTheFarLeftLane:
  """"The slow pass thing should only apply if I'm in the far left lane."

  The harm in a slow pass is WHERE it happens, not that it is slow. Grinding past someone from the
  middle lane of a four-lane road blocks nobody -- the passing lane is still free and anyone in a
  hurry goes around it. Doing it from the far left is the thing he does not want to be: "no one
  should ever have to be stuck behind me."

  This is the second correction on the same idea. The first was "a slow pass would only matter if
  I'm passing on the left", which made it watch the right side only.
  """

  GRIND = dict(right=lane(d_rel=20.0, v_rel=-0.4))

  def test_a_grind_from_the_leftmost_lane_counts(self):
    assert run(OvertakeProgress(), 10.0, in_leftmost=True, **self.GRIND).crawling

  def test_the_same_grind_from_a_middle_lane_does_not(self):
    """Identical arithmetic, different lane. Nothing is being held up."""
    assert not run(OvertakeProgress(), 10.0, in_leftmost=False, **self.GRIND).crawling

  def test_moving_out_of_the_left_lane_ends_a_crawl_in_progress(self):
    """If he pulls right mid-grind the complaint is over, and the event should stop rather than
    keep accruing against a lane he is no longer blocking."""
    op = run(OvertakeProgress(), 10.0, in_leftmost=True, **self.GRIND)
    assert op.crawling
    run(op, 1.0, in_leftmost=False, **self.GRIND)
    assert not op.crawling

  def test_it_does_not_count_events_from_a_middle_lane(self):
    """The drive summary number, not just the live flag -- crawlEvents is what he reads."""
    op = run(OvertakeProgress(), 30.0, in_leftmost=False, **self.GRIND)
    assert op.crawl_events == 0
    assert op.crawl_longest == 0.0
