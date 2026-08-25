"""FusionPilot: the route-intent transport report must not flatter a transport that is too late.

`tools/bp_route_intent_report.py` is the instrument the FIRST transport drive gets read with, and
its job is to answer one question honestly: did the source give enough WARNING. This fork has
already scored the wrong thing once here -- mapd's fork prediction came back 96-100% accurate and
was closed anyway, because 1.0 s of lead against an 8 s budget is useless however correct it is.

So the tests are about the two ways the report can lie:

  it credits LEAD TIME that was not there          -> a bad transport looks adequate
  it reports a plausible table when nothing spoke  -> "inert" and "broken" look identical

And it drives the tool's own `Replay`, not a copy of it. Reimplementing the logic in the test is
exactly how `test_can_nav_diff.py` stayed green while a new threshold went uncovered, earlier the
same day this file was written.
"""
from __future__ import annotations

import importlib.util
import pathlib
from types import SimpleNamespace as NS

SPEC = importlib.util.spec_from_file_location(
  "bp_route_intent_report",
  pathlib.Path(__file__).resolve().parents[3] / "tools/bp_route_intent_report.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

SEC = 1_000_000_000


def intent(maneuver="exitRight", distance=100.0, known=True, observed_ns=0, source="carCan"):
  return NS(maneuver=maneuver, distance=distance, distanceKnown=known,
            source=source, observedMonoTime=observed_ns)


def drive(rep, start_ns, seconds, maneuver, distance=100.0, hz=20, v_ego=30.0, stale_by=0.0):
  """Feed `seconds` of frames at `hz`, with the source stamping each one `stale_by` behind."""
  step = SEC // hz
  t = start_ns
  for _ in range(int(seconds * hz)):
    rep.on_intent(t, intent(maneuver=maneuver, distance=distance,
                            observed_ns=t - int(stale_by * SEC)))
    rep.on_frame(t, v_ego)
    t += step
  return t


class TestLeadTime:
  """The number that decides whether a transport is worth having."""

  def test_a_maneuver_announced_for_ten_seconds_scores_ten_seconds(self):
    rep = mod.Replay()
    t = drive(rep, SEC * 100, 10.0, "exitRight")
    # A run is closed when the maneuver stops being committing.
    drive(rep, t, 1.0, "continueAhead")
    assert len(rep.leads) == 1
    name, secs = rep.leads[0]
    assert name == "exitRight"
    assert 9.0 < secs < 11.0, f"scored {secs:.1f}s of lead for a 10s announcement"

  def test_a_late_transport_cannot_score_as_an_early_one(self):
    """THE FAILURE THIS TOOL EXISTS TO CATCH.

    mapd's prediction is 96-100% accurate with 1.0 s of lead and was closed on the lead. A report
    that credited a one-second announcement with more than a second would have kept that source
    alive on a number that was never true.
    """
    rep = mod.Replay()
    t = drive(rep, SEC * 100, 1.0, "exitRight")
    drive(rep, t, 1.0, "continueAhead")
    assert len(rep.leads) == 1
    _, secs = rep.leads[0]
    assert secs < 1.5, f"a 1s announcement scored {secs:.1f}s"

  def test_two_separate_maneuvers_are_two_runs(self):
    rep = mod.Replay()
    t = drive(rep, SEC * 100, 4.0, "exitRight")
    t = drive(rep, t, 2.0, "continueAhead")
    t = drive(rep, t, 6.0, "turnLeft")
    drive(rep, t, 1.0, "continueAhead")
    assert [m for m, _ in rep.leads] == ["exitRight", "turnLeft"]

  def test_one_maneuver_straight_into_another_is_still_two_runs(self):
    """NO `continueAhead` BETWEEN THEM, and that is the whole point of this test.

    `test_two_separate_maneuvers_are_two_runs` puts a continue in the middle, so the run is closed
    by the NOT-committing branch and the maneuver-changed branch is never exercised. Mutation
    testing found it: deleting the close on a maneuver CHANGE left the whole suite green.

    The case is real -- a navigator goes straight from "exit right" to "turn left" at the end of a
    ramp with nothing in between -- and without this the two would merge into one run and report
    double the lead time for the first maneuver, which is the exact number the tool exists to get
    right.
    """
    rep = mod.Replay()
    t = drive(rep, SEC * 100, 4.0, "exitRight")
    t = drive(rep, t, 6.0, "turnLeft")
    drive(rep, t, 1.0, "continueAhead")
    assert [m for m, _ in rep.leads] == ["exitRight", "turnLeft"]
    first, second = (s for _, s in rep.leads)
    assert 3.0 < first < 5.0, f"exitRight scored {first:.1f}s, expected ~4"
    assert 5.0 < second < 7.0, f"turnLeft scored {second:.1f}s, expected ~6"

  def test_continueAhead_is_never_scored_as_a_maneuver(self):
    # It is not a commitment, so crediting it with lead time would inflate every drive on a
    # motorway -- where "carry on" is what the source says for miles at a time.
    rep = mod.Replay()
    drive(rep, SEC * 100, 30.0, "continueAhead")
    assert rep.leads == []


class TestItDoesNotInventCoverage:
  def test_a_stale_source_is_not_counted_as_available(self):
    # Stamped further behind than the consumer will believe. The frames exist; the instruction does
    # not. Counting them would report a dead link as full coverage.
    rep = mod.Replay()
    drive(rep, SEC * 100, 5.0, "exitRight", stale_by=30.0)
    assert rep.frames > 0
    assert rep.available == 0
    assert rep.refused == 0

  def test_freshness_is_measured_against_the_frame_clock_not_the_publish_clock(self):
    # A transport that republishes a cached instruction looks fresh by publish time and is stale by
    # observation time. The report has to use the second, which is what the consumer uses.
    rep = mod.Replay()
    drive(rep, SEC * 100, 3.0, "exitRight", stale_by=1.5)
    assert rep.available > 0
    assert all(1.4 < a < 1.6 for a in rep.ages), "ages did not reflect the observation stamp"

  def test_the_gate_verdict_comes_from_the_real_consumer(self):
    """Not recomputed here. The bound, the no-claim cases and the freshness rule are all argued in
    route_intent.py, and a report with its own copy would drift from the car."""
    # Read from the file rather than via inspect: the module is loaded by path, so inspect cannot
    # resolve its source and reports it as a built-in class.
    src = (pathlib.Path(__file__).resolve().parents[3]
           / "tools/bp_route_intent_report.py").read_text(encoding="utf-8")
    body = src[src.index("class Replay"):src.index("def main(")]
    assert "refuses_pass" in body, "the report stopped asking the real consumer"
    assert "RouteIntent" in body
    # ...and it must not have grown its own threshold arithmetic.
    assert "LOOKAHEAD" not in body, "the report reimplemented the consumer's bound"

  def test_a_far_maneuver_is_available_but_not_refused(self):
    rep = mod.Replay()
    drive(rep, SEC * 100, 3.0, "exitRight", distance=5000.0, v_ego=30.0)
    assert rep.available > 0
    assert rep.refused == 0, "refused a maneuver 5 km away"

  def test_it_counts_what_would_have_MOVED_the_car_and_not_only_what_was_refused(self):
    """Reporting only refusals scores the safe half of a transport.

    A refusal costs a pass. An open costs a lane change. The first version of this tool counted
    only the first, which would have let a source that constantly asked the car to move look
    identical to one that never did.
    """
    rep = mod.Replay()
    drive(rep, SEC * 100, 3.0, "exitRight", distance=50.0)
    assert rep.opened > 0
    assert rep.opened_sides == {"right": rep.opened}

  def test_a_maneuver_that_refuses_but_may_not_open_is_counted_only_as_a_refusal(self):
    # `unknown` is the case the two counters exist to separate: cautious enough to hold a pass,
    # nowhere near enough to steer. If these ever move together, this is what breaks first.
    rep = mod.Replay()
    drive(rep, SEC * 100, 3.0, "unknown", distance=50.0)
    assert rep.refused > 0
    assert rep.opened == 0

  def test_a_close_maneuver_is_refused(self):
    rep = mod.Replay()
    drive(rep, SEC * 100, 3.0, "exitRight", distance=50.0, v_ego=30.0)
    assert rep.refused == rep.available > 0
