"""The route-intent scorer must count episodes correctly, because it only ever runs once.

`tools/bp_route_intent_score.py` executes on data that does not exist until he drives, and its
verdict decides whether route prediction gets built at all. A miscount is not a wrong number on a
screen -- it is a wasted drive and a decision taken on it. Nothing else in this repo has that shape:
every other tool can be re-run against the same route until it is right.

So the episode walk is exercised here against synthetic frames, where the right answer is arithmetic
a human can check rather than something read off a route.
"""
from __future__ import annotations

import importlib.util
import pathlib
from types import SimpleNamespace as NS

SPEC = importlib.util.spec_from_file_location(
  "bp_route_intent_score", pathlib.Path(__file__).resolve().parents[3] / "tools/bp_route_intent_score.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)
Score = mod.Score

NS_PER_S = 1_000_000_000
FAST = 30.0        # m/s, well over MIN_SPEED_MS
CRAWL = 1.0        # under it


class _Msg:
  """A capnp-shaped message. `which()` is a METHOD, as it is on the real reader."""

  def __init__(self, kind, t=0.0, **fields):
    self._kind = kind
    self.logMonoTime = int(t * NS_PER_S)
    setattr(self, kind, NS(**fields))

  def which(self):
    return self._kind


def car(t, v=FAST, left=False, right=False):
  return _Msg("carState", t, vEgo=v, leftBlinker=left, rightBlinker=right)


def mapd(t, sel, way=1, hwy="motorway"):
  return _Msg("mapdOut", t, waySelectionType=sel, wayId=way, highwayClass=hwy)


def test_a_guess_that_comes_true_is_a_hit_and_its_lead_time_is_the_gap():
  """The core case, and the lead time is what the verdict turns on: mapd guessed way 42 at t=1 and
  was proved right at t=10, so it had NINE SECONDS of warning -- comfortably inside the 8 s the
  set-speed budget needs."""
  sc = Score()
  sc.feed_segment([car(0.0), mapd(1.0, "predicted", way=42), mapd(5.0, "predicted", way=42),
                   mapd(10.0, "current", way=42)])
  assert (sc.hits, sc.misses) == (1, 0)
  assert sc.lead_times == [9.0], "lead time must run from the FIRST frame of the guess, not the last"


def test_a_guess_that_is_wrong_is_a_miss():
  sc = Score()
  sc.feed_segment([car(0.0), mapd(1.0, "predicted", way=42), mapd(4.0, "current", way=99)])
  assert (sc.hits, sc.misses) == (0, 1)


def test_changing_its_mind_starts_a_new_guess_rather_than_extending_the_old_one():
  """THE ONE THAT WOULD INFLATE THE HEADLINE NUMBER. mapd guesses way 42, then switches to 99, then
  99 proves right. Timing that from the first guess would credit it with 9 s of lead time it never
  had -- it only settled on 99 at t=8, so the honest figure is 2 s."""
  sc = Score()
  sc.feed_segment([car(0.0), mapd(1.0, "predicted", way=42), mapd(8.0, "predicted", way=99),
                   mapd(10.0, "current", way=99)])
  assert (sc.hits, sc.misses) == (1, 0)
  assert sc.lead_times == [2.0]


def test_giving_up_is_counted_apart_from_being_wrong():
  """`fail` is mapd saying it does not know, which is not the same claim as guessing wrong. Folding
  them together would make the accuracy number describe two different things."""
  sc = Score()
  sc.feed_segment([car(0.0), mapd(1.0, "predicted", way=42), mapd(4.0, "fail", way=0)])
  assert (sc.hits, sc.misses, sc.unresolved) == (0, 0, 1)


def test_a_guess_still_open_at_the_end_is_dropped_and_counted():
  """Sampled segments are not contiguous, so an episode running past the boundary cannot be timed.
  Counted so the reader knows how much was discarded rather than silently losing it."""
  sc = Score()
  sc.feed_segment([car(0.0), mapd(1.0, "predicted", way=42)])
  assert (sc.hits, sc.misses, sc.spanning) == (0, 0, 1)


def test_crawling_is_not_a_fork():
  """Below the speed floor, "which way is he going" is not a question anyone is asking, and a car
  creeping in a parking lot changes ways constantly."""
  sc = Score()
  sc.feed_segment([car(0.0, v=CRAWL), mapd(1.0, "predicted", way=42), mapd(4.0, "current", way=42)])
  assert (sc.hits, sc.misses, sc.spanning) == (0, 0, 0)
  assert sc.mapd_frames == 2, "the frames are still seen, they just do not open an episode"


def test_slowing_below_the_floor_abandons_an_open_guess():
  """He came off the freeway and stopped. Whatever mapd was guessing about the fork is now being
  resolved by a car that is barely moving, and scoring that as a fork prediction is noise."""
  sc = Score()
  sc.feed_segment([car(0.0), mapd(1.0, "predicted", way=42), car(2.0, v=CRAWL),
                   mapd(3.0, "current", way=42)])
  assert (sc.hits, sc.misses, sc.spanning) == (0, 0, 0)


def test_ramps_are_scored_on_their_own_because_they_are_the_population_that_matters():
  """For passing assist the question is never "which way", it is "is he leaving the freeway". A
  mainline hit and a ramp hit both count overall; only the ramp one belongs to the subset a
  "do not offer a pass approaching an exit" gate would run on."""
  sc = Score()
  sc.feed_segment([car(0.0),
                   mapd(1.0, "predicted", way=7), mapd(9.0, "current", way=7, hwy="motorwayLink"),
                   mapd(20.0, "predicted", way=8), mapd(24.0, "current", way=8, hwy="motorway")])
  assert sc.hits == 2
  assert (sc.ramp_hits, sc.ramp_misses) == (1, 0)
  assert sc.ramp_leads == [8.0], "only the ramp resolution belongs in the ramp lead times"


def test_the_blinker_is_recorded_at_the_ramp_resolution_only():
  """Corroboration, never the label -- it arrives at the gore point, after the decision had to be
  made. Recorded so the ramp rows can be read, and only there."""
  sc = Score()
  sc.feed_segment([car(0.0, right=True),
                   mapd(1.0, "predicted", way=7), mapd(5.0, "current", way=7, hwy="motorwayLink")])
  assert dict(sc.blinker_at_resolve) == {"right": 1}


def test_state_carries_across_segments_but_episodes_do_not():
  """Two segments fed to one Score: the tallies accumulate, and neither segment's open episode leaks
  into the other."""
  sc = Score()
  sc.feed_segment([car(0.0), mapd(1.0, "predicted", way=1), mapd(3.0, "current", way=1)])
  sc.feed_segment([car(0.0), mapd(1.0, "predicted", way=2), mapd(3.0, "current", way=2)])
  assert sc.hits == 2
  assert sc.lead_times == [2.0, 2.0]
  assert sc.spanning == 0


def test_a_resolution_with_no_guess_before_it_is_not_scored():
  """mapd being certain the whole time is the ordinary case and says nothing about prediction."""
  sc = Score()
  sc.feed_segment([car(0.0), mapd(1.0, "current", way=1), mapd(2.0, "current", way=1)])
  assert (sc.hits, sc.misses, sc.unresolved, sc.spanning) == (0, 0, 0, 0)
  assert sc.mapd_frames == 2
