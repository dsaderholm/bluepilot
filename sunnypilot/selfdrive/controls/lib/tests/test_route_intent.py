"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FusionPilot: route intent -- the consumer, the gate, and the structural guarantee that it can only
ever refuse.

THREE KINDS OF TEST HERE AND THEY ARE NOT INTERCHANGEABLE:

  policy       what RouteIntent believes and when. Pure, and fed exact clocks.
  gate         the same thing through the real PassingAssistDetector, because a policy that is
               right in isolation and wired to nothing is this fork's oldest bug.
  structural   parsed with `ast`, because the property that matters -- "this may refuse and may
               never open" -- is not a behaviour any fixture can exercise. It is the ABSENCE of
               code, and only a parser can assert an absence.

The structural ones follow test_it_never_reads_fords_command and test_mapd_schema.py, and for the
identical reason: every explanation of why we refuse a field contains the field's own name, so
grepping is useless and the module has to be parsed.
"""

import ast
import pathlib
import time
from types import SimpleNamespace as NS

import pytest

from cereal import custom
from openpilot.sunnypilot.selfdrive.controls.lib.passing_assist import PassingAssistDetector
from openpilot.sunnypilot.selfdrive.controls.lib.route_intent import (
  RouteIntent, LOOKAHEAD_S, LOOKAHEAD_MIN_M, MAX_INSTRUCTION_AGE_S, NO_CLAIM_MANEUVERS,
)
from openpilot.sunnypilot.routeintent.source import Instruction, StubSource, fill_message
from openpilot.sunnypilot.selfdrive.controls.lib.tests.test_passing_assist import (
  make_sm, CRUISE_MS, STUCK_FRAMES,
)

Side = custom.LongitudinalPlanSP.PassingAssist.Side
Blocked = custom.LongitudinalPlanSP.PassingAssist.Blocked
Phase = custom.LongitudinalPlanSP.PassingAssist.Maneuver
MANEUVERS = [str(e) for e in custom.RouteIntentBP.Maneuver.schema.enumerants]

REPO = next(d for d in pathlib.Path(__file__).resolve().parents
            if (d / "common" / "params_keys.h").exists())

NOW = 1_000_000_000_000  # an arbitrary monotonic nanosecond, so ages are exact rather than racy


def intent_sm(maneuver="exitRight", distance=100.0, distance_known=True, source="stub",
              observed=NOW, present=True, alive=True, valid=True):
  """A SubMaster carrying just routeIntentBP, for the policy tests.

  Deliberately NOT a dict, for the reason recorded on FakeSubMaster: SubMaster has __getitem__ and
  no __contains__, so a dict fixture makes `in` work in tests and crash plannerd on the car.
  """
  data = {}
  sm = NS(data=data, alive={}, valid={}, __getitem__=None)

  class _SM:
    def __init__(self):
      self.alive = {}
      self.valid = {}
      self.data = {}

    def __getitem__(self, s):
      return self.data[s]

  out = _SM()
  if present:
    out.data['routeIntentBP'] = NS(maneuver=maneuver, distance=distance,
                                   distanceKnown=distance_known, source=source,
                                   observedMonoTime=observed)
    out.alive['routeIntentBP'] = alive
    out.valid['routeIntentBP'] = valid
  del sm
  return out


def read(**kw):
  """A RouteIntent that has consumed one frame at exactly NOW."""
  ri = RouteIntent()
  ri.update(intent_sm(**kw), now_ns=NOW)
  return ri


class TestItBelievesNothingWithoutAFreshStamp:
  """Every one of these is a way a source can look alive while saying nothing, and each has to
  reach `unavailable` rather than a plausible-looking instruction."""

  def test_no_transport_fitted_at_all(self):
    # The state of every car today, this one included. It must read as no claim, not as a default.
    ri = read(present=False)
    assert not ri.available
    assert not ri.refuses_pass(30.0)

  def test_an_invalid_message_is_not_an_instruction(self):
    """`valid` is the real check. `alive` is NOT, and this test used to assert it was.

    Read out of SubMaster's source 2026-08-23: a service declared at frequency 0 is ON DEMAND, so
    `alive` is initialised True and then recomputed only for `static_freq_services`, which excludes
    it. `sm.alive['routeIntentBP']` is therefore ALWAYS True on the car, and the consumer's old
    check on it could never fail -- a guard that reads like liveness and provides none, which this
    fork removes rather than keeps.

    `valid` does work: SubMaster assigns it from the message's own field on arrival, so a producer
    that forgets `msg.valid = True` is correctly ignored. That is the likeliest way a new transport
    fails on its first run, which is why source.py's fill_message now says so in capitals.
    """
    assert not read(valid=False).available
    # And the honest statement about alive: a False here changes NOTHING, because the consumer no
    # longer consults it. Asserting the real behaviour rather than the one that reads better.
    assert read(alive=False).available

  def test_an_unstamped_message_is_refused(self):
    """Zero is capnp's default for observedMonoTime, so an unset field is what a source that forgot
    to stamp publishes.

    TESTED JUST AFTER BOOT ON PURPOSE, and the first version of this test was VACUOUS without that.
    `time.monotonic_ns()` counts from boot, so at NOW = 1e12 an unstamped message ages out at 1000
    seconds and the freshness check catches it for the wrong reason -- deleting the guard entirely
    left the whole suite green. One second into the clock, age is 1.0 s, comfortably inside
    MAX_INSTRUCTION_AGE_S, and an unstamped message would be BELIEVED. That window is real: plannerd
    starts seconds after boot, which is exactly when a new transport is coming up too.
    """
    early = int(1.0e9)
    ri = RouteIntent()
    ri.update(intent_sm(observed=0), now_ns=early)
    assert not ri.available
    assert not ri.refuses_pass(30.0)
    # ...and the same instant with a real stamp IS believed, or the assertion above would pass for
    # any reason at all.
    ri.update(intent_sm(observed=early - int(0.2e9)), now_ns=early)
    assert ri.available and ri.refuses_pass(30.0)

  def test_a_stale_instruction_is_refused(self):
    fresh = read(observed=NOW - int(0.5e9))
    assert fresh.available and fresh.refuses_pass(30.0)
    stale = read(observed=NOW - int((MAX_INSTRUCTION_AGE_S + 0.5) * 1e9))
    assert not stale.available
    assert not stale.refuses_pass(30.0)

  def test_a_stamp_from_the_future_is_refused(self):
    # A clock disagreement, not a fresh instruction -- and believing it makes an arbitrarily old
    # instruction look arbitrarily fresh, which is the failure the stamp exists to prevent.
    assert not read(observed=NOW + int(5e9)).available

  def test_the_age_is_recorded_not_just_tested(self):
    # It has to be readable, or "the source is 2.9 s behind" is invisible on the drive that would
    # settle whether MAX_INSTRUCTION_AGE_S is the right number.
    assert read(observed=NOW - int(1.5e9)).age_s == pytest.approx(1.5, abs=1e-6)

  def test_a_garbled_message_does_not_take_the_planner_with_it(self):
    class Exploding:
      alive = {'routeIntentBP': True}
      valid = {'routeIntentBP': True}

      def __getitem__(self, s):
        return NS()  # every field missing

    ri = RouteIntent()
    ri.update(Exploding(), now_ns=NOW)
    assert not ri.available


class TestWhatRefuses:
  def test_a_close_exit_refuses(self):
    assert read(maneuver="exitRight", distance=100.0).refuses_pass(30.0)

  def test_a_distant_exit_does_not(self):
    # 3000 m against a 600 m bound at 30 m/s.
    assert not read(maneuver="exitRight", distance=3000.0).refuses_pass(30.0)

  @pytest.mark.parametrize("maneuver", sorted(NO_CLAIM_MANEUVERS))
  def test_the_two_non_commitments_never_refuse(self, maneuver):
    assert not read(maneuver=maneuver, distance=1.0).refuses_pass(30.0)

  @pytest.mark.parametrize("maneuver", [m for m in MANEUVERS if m not in NO_CLAIM_MANEUVERS])
  def test_every_other_maneuver_refuses_including_ones_nobody_has_added_yet(self, maneuver):
    """Parametrised over the SCHEMA, not a list written here.

    The default direction is the point. A maneuver type added by a later transport author and
    silently NOT refusing is invisible in a log; one that refuses costs a pass and shows up as
    routeManeuver in blockedBy. This test fails the day someone adds an enumerant and wires it
    into NO_CLAIM_MANEUVERS without arguing for it.
    """
    assert read(maneuver=maneuver, distance=100.0).refuses_pass(30.0)

  def test_unknown_refuses(self):
    # Named separately from the sweep above because it is a DESIGN choice rather than a fallout:
    # a transport may ship before its classifier is complete, and an unrecognised glyph costs a
    # pass instead of passing as `continueAhead`.
    assert read(maneuver="unknown", distance=100.0).refuses_pass(30.0)

  def test_a_maneuver_with_no_distance_does_not_refuse(self):
    """The one place this gate is deliberately permissive.

    A maneuver with no distance carries no bound, so refusing on it goes quiet for the whole route
    rather than for the approach. That is not a conservative version of this gate, it is a
    different and much worse feature -- and the schema lets a source say "a turn is coming and I do
    not know how far" precisely so it need not invent a number.
    """
    ri = read(maneuver="exitRight", distance=0.0, distance_known=False)
    assert ri.available
    assert not ri.refuses_pass(30.0)

  def test_a_negative_distance_does_not_refuse(self):
    assert not read(maneuver="exitRight", distance=-50.0).refuses_pass(30.0)

  def test_zero_distance_is_a_real_reading(self):
    # NOT the same as unknown. The maneuver is upon us, which is the strongest possible reason to
    # refuse -- and reading 0 as "no data" is how `_limit_drop_ahead` nearly got its bound wrong.
    assert read(maneuver="turnLeft", distance=0.0).refuses_pass(30.0)

  def test_the_bound_scales_with_speed(self):
    # 500 m is inside 20 s at 30 m/s and outside it at 20 m/s. A fixed distance would mean
    # twenty-seven seconds of silence at 25 mph and nine at 75.
    ri = read(maneuver="exitRight", distance=500.0)
    assert ri.refuses_pass(30.0)
    assert not ri.refuses_pass(20.0)
    assert 20.0 * LOOKAHEAD_S < 500.0 < 30.0 * LOOKAHEAD_S

  def test_the_floor_keeps_the_bound_from_collapsing_at_low_speed(self):
    ri = read(maneuver="turnRight", distance=LOOKAHEAD_MIN_M - 1.0)
    assert ri.refuses_pass(0.0)
    assert not read(maneuver="turnRight", distance=LOOKAHEAD_MIN_M + 1.0).refuses_pass(0.0)


def with_route(**kw):
  """make_sm(), plus a routeIntentBP the real detector will read.

  Stamped with the REAL clock, so these run the detector's own `time.monotonic_ns()` path rather
  than an injected one -- the freshness test is part of what is being exercised here.
  """
  present = kw.pop('route_present', True)
  maneuver = kw.pop('route_maneuver', "exitRight")
  distance = kw.pop('route_distance', 100.0)
  known = kw.pop('route_distance_known', True)
  age_s = kw.pop('route_age_s', 0.0)
  sm = make_sm(**kw)
  if present:
    sm.data['routeIntentBP'] = NS(maneuver=maneuver, distance=distance, distanceKnown=known,
                                  source="stub",
                                  observedMonoTime=time.monotonic_ns() - int(age_s * 1e9))
    sm.alive['routeIntentBP'] = True
    sm.valid['routeIntentBP'] = True
    sm.updated['routeIntentBP'] = True
  return sm


def run_route(det, frames, **kw):
  for _ in range(frames):
    det.update(with_route(**kw), CRUISE_MS, True)
  return det


class TestTheGate:
  """The policy above, reached through the real detector. A gate that is never called is the
  failure this fork keeps recording, and no amount of unit-testing the predicate would show it."""

  def test_a_clear_left_lane_is_still_suggested_with_no_route(self):
    # The baseline the whole design rests on: no transport fitted leaves the feature exactly as it
    # is today. If this ever fails, the gate has stopped being inert.
    det = run_route(PassingAssistDetector(), STUCK_FRAMES, route_present=False)
    assert det.suggestion == Side.left
    assert det.blocked_by == Blocked.none

  def test_a_route_saying_nothing_changes_nothing(self):
    det = run_route(PassingAssistDetector(), STUCK_FRAMES, route_maneuver="continueAhead")
    assert det.suggestion == Side.left
    assert det.blocked_by == Blocked.none

  def test_his_exit_coming_up_stops_the_suggestion(self):
    det = run_route(PassingAssistDetector(), STUCK_FRAMES, route_distance=100.0)
    assert det.suggestion == Side.none
    assert det.blocked_by == Blocked.routeManeuver

  def test_an_exit_far_enough_off_does_not(self):
    det = run_route(PassingAssistDetector(), STUCK_FRAMES, route_distance=3000.0)
    assert det.suggestion == Side.left
    assert det.blocked_by == Blocked.none

  def test_a_stale_instruction_leaves_the_feature_alone(self):
    det = run_route(PassingAssistDetector(), STUCK_FRAMES,
                    route_distance=100.0, route_age_s=MAX_INSTRUCTION_AGE_S + 1.0)
    assert det.suggestion == Side.left

  def test_the_refusal_does_not_destroy_the_debounced_blinker_intent(self):
    """keep_wanted, which its neighbours all carry and which nothing was checking.

    This gate is reached PAST "a pass is warranted", so it is the flicker case. A transport that
    drops a frame goes stale, the gate releases, the instruction returns -- and hard-clearing
    wanted_side each time would make the debounce pay WANTED_RISE_S to get it back, over and over.
    That is precisely the wobble that 126 aborts in 37 minutes bought _debounce_wanted for, and it
    was reintroduced once already on a path the original fix did not cover.
    """
    det = run_route(PassingAssistDetector(), STUCK_FRAMES, route_present=False)
    assert det.wanted_side == Side.left
    run_route(det, 2, route_distance=100.0)
    assert det.blocked_by == Blocked.routeManeuver
    assert det.suggestion == Side.none
    assert det.wanted_side == Side.left, "the gate hard-cleared the debounced intent"

  def test_the_route_reason_beats_the_map_reasons(self):
    """Ordering, and it decides which words he reads.

    Near an exit the map gates are true as well -- the limit drops on the ramp -- so whichever runs
    first owns blockedBy. "Your turn is coming up" explains the silence; "Slower zone ahead" is
    true and explains the wrong thing.
    """
    det = run_route(PassingAssistDetector(), STUCK_FRAMES, route_distance=100.0,
                    mapd_present=True, mapd_sl=29.0, mapd_next_sl=13.0, mapd_next_d=100.0)
    assert det.blocked_by == Blocked.routeManeuver

  def test_it_does_not_pull_the_car_out_of_a_crossing_already_underway(self):
    """A refusal may not reach a committed crossing.

    A car cannot un-change lanes because an exit arrived, and backing out between two lanes is a
    maneuver in its own right rather than the absence of one -- the same reasoning that keeps a
    BLIS presence veto out of `demands_abort`.
    """
    det = PassingAssistDetector()
    # Until the dry run is actually ACROSS, not merely signalling -- and stop there, because the
    # crossing is over in a few seconds and a fixed frame count sails past it into `finishing`.
    for _ in range(400):
      det.update(with_route(route_present=False), CRUISE_MS, True)
      if det.maneuver.phase == Phase.changing:
        break
    assert det.maneuver.phase == Phase.changing, "fixture never reached the committed phase"
    run_route(det, 10, route_distance=50.0)
    assert det.blocked_by == Blocked.routeManeuver
    assert det.maneuver.phase == Phase.changing


class TestItCanOnlyRefuse:
  """Structural. The property is an ABSENCE, so it is parsed rather than exercised."""

  CONSUMER = REPO / "sunnypilot" / "selfdrive" / "controls" / "lib" / "route_intent.py"
  DETECTOR = REPO / "sunnypilot" / "selfdrive" / "controls" / "lib" / "passing_assist.py"

  # Everything RouteIntent is allowed to offer the outside world. `refuses_pass` is the only
  # predicate; the rest is state a log reads. Widening this list is the review conversation.
  PUBLIC_API = {"update", "reset", "refuses_pass"}

  def _class(self, path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == name)

  def test_the_consumer_offers_no_way_to_say_yes(self):
    cls = self._class(self.CONSUMER, "RouteIntent")
    public = {n.name for n in cls.body
              if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")}
    assert public == self.PUBLIC_API, (
      f"RouteIntent's public surface changed to {sorted(public)}. A method that answers 'yes' -- "
      "allows, ok, clear, permits -- turns route intent from evidence into permission, which a "
      "stale instruction then uses to move the car.")

  def test_the_detector_only_ever_asks_it_to_refuse(self):
    tree = ast.parse(self.DETECTOR.read_text(encoding="utf-8"))
    used = {n.attr for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Attribute)
            and n.value.attr == "route_intent"}
    assert used == {"update", "refuses_pass"}, (
      f"passing_assist reads {sorted(used)} off route_intent. Only update and refuses_pass may be "
      "called: a diagnostic read is harmless until somebody branches on it.")

  def test_the_refusal_leads_nowhere_but_a_refusal(self):
    """The call site itself, not just the API.

    `if refuses_pass(...)` is only safe while its body is a refusal and it has no else. An `else`
    branch is where "and therefore this pass is fine" gets written, and it would look entirely
    reasonable in a diff.
    """
    tree = ast.parse(self.DETECTOR.read_text(encoding="utf-8"))
    sites = [n for n in ast.walk(tree) if isinstance(n, ast.If)
             and isinstance(n.test, ast.Call)
             and isinstance(n.test.func, ast.Attribute)
             and n.test.func.attr == "refuses_pass"]
    assert len(sites) == 1, f"expected exactly one route-intent gate, found {len(sites)}"
    site = sites[0]
    assert not site.orelse, "the route-intent gate grew an else branch"
    calls = [n for n in ast.walk(ast.Module(body=site.body, type_ignores=[]))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    assert [c.func.attr for c in calls] == ["_reset_outputs"], (
      "the route-intent gate's body does something other than refuse")
    arg = calls[0].args[0]
    assert isinstance(arg, ast.Attribute) and arg.attr == "routeManeuver"

  def test_it_is_out_of_reach_of_anything_that_actuates(self):
    """The gate review, made executable.

    CLAUDE.md's rule for wiring a new source into a shared struct: enumerate every consumer and
    label it REFUSES / AUTHORIZES / DISPLAYS. For route intent exactly one consumer is permitted
    and it refuses. `may_actuate` AUTHORIZES a commanded move and `_must_abort` performs one --
    the BLIS bug was precisely a refusal-shaped input reaching the second of those.
    """
    tree = ast.parse(self.DETECTOR.read_text(encoding="utf-8"))
    reached = set()
    for fn in ast.walk(tree):
      if not isinstance(fn, ast.FunctionDef):
        continue
      for n in ast.walk(fn):
        if isinstance(n, ast.Attribute) and n.attr == "route_intent":
          reached.add(fn.name)
    assert reached == {"__init__", "_decide"}, (
      f"route_intent is now read in {sorted(reached)}. It may be constructed and it may be asked "
      "to refuse a suggestion; it may not reach may_actuate, _must_abort or _run_maneuver.")


class TestTheServiceDeclaration:
  """The consumer's freshness design rests on how routeIntentBP is DECLARED, not just on its code.

  services.py gives it frequency 0, which makes SubMaster treat it as on-demand: `alive` is pinned
  True and never recomputed. That is why route_intent.py consults `valid` alone and says so. If
  someone later "tidies" the declaration by giving it a real frequency, `alive` starts being
  computed from a rate that belongs to whichever transport happens to be fitted -- and the reasoning
  in that file silently stops matching the system. Pin it here with the reason attached.
  """

  def test_it_is_declared_at_frequency_zero_and_logged(self):
    from cereal.services import SERVICE_LIST
    svc = SERVICE_LIST['routeIntentBP']
    assert svc.frequency == 0.0, (
      "routeIntentBP must stay at frequency 0. The publish rate is a property of whichever "
      "transport is fitted, so a declared rate makes sm.alive report on the TRANSPORT and be "
      "mistaken for a statement about the INSTRUCTION. Freshness comes from observedMonoTime.")
    assert svc.should_log, "an unlogged transport cannot be scored on its first drive"

  def test_the_consumer_does_not_consult_alive(self):
    """Structural, because the reason is subtle and a future reader would 'restore' the check.

    At frequency 0, SubMaster pins `alive` True for the life of the process, so testing it is a
    guard that cannot fail. This fork removes those rather than keeping them -- see the reverted
    pinned-holds guard that sat nested where it could never fire.
    """
    # PARSED, not grepped. The first version of this test searched the text and matched the
    # docstring, which EXPLAINS why alive is not consulted -- so it failed on prose describing the
    # very property it was checking. Same lesson as test_mapd_schema.py: every explanation of why
    # we refuse a thing contains the thing's name.
    tree = ast.parse((REPO / "sunnypilot" / "selfdrive" / "controls" / "lib"
                      / "route_intent.py").read_text(encoding="utf-8"))
    upd = next(n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "update")
    reads = {n.attr for n in ast.walk(upd) if isinstance(n, ast.Attribute)}
    assert "alive" not in reads, (
      "route_intent consults sm.alive again. At frequency 0 that is always True -- see "
      "TestTheServiceDeclaration.")
    assert "valid" in reads, "the valid check disappeared; that one does work"


class TestTheBenchScriptParser:
  """`bp_route_intent_stub.py` is how the gate gets exercised with no transport. A parsing bug there
  produces a confusing bench session rather than an error, which is the worst kind."""

  @staticmethod
  def _parse(text):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
      "bp_route_intent_stub", REPO / "tools" / "bp_route_intent_stub.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.parse_script(text)

  def test_three_fields_is_a_known_distance(self):
    assert self._parse("8,exitRight,700") == [(8.0, "exitRight", 700.0)]

  def test_two_fields_means_the_distance_is_UNKNOWN_not_zero(self):
    # The distinction the whole schema turns on. A parser that read this as 0.0 would make the
    # bench exercise "the maneuver is upon us" while the operator thought they were testing
    # "distance unknown" -- opposite branches of the consumer.
    assert self._parse("0,continueAhead") == [(0.0, "continueAhead", None)]

  def test_entries_are_sorted_by_time_not_by_input_order(self):
    out = self._parse("20,turnLeft,50 5,exitRight,900")
    assert [t for t, _, _ in out] == [5.0, 20.0]

  def test_a_malformed_entry_is_an_error_rather_than_a_guess(self):
    for bad in ("8", "8,exitRight,700,extra"):
      try:
        self._parse(bad)
      except SystemExit:
        continue
      raise AssertionError(f"{bad!r} parsed instead of failing")


class TestTheStubSource:
  """The bench source. It exists so the whole chain is exercisable before any transport lands --
  which is the answer to `IcbmModelStopEnabled`, a feature that shipped unreachable and was
  reported broken twice when it was merely unenableable."""

  def test_the_script_advances_with_the_clock(self):
    now = [0.0]
    src = StubSource([(0.0, "continueAhead", None),
                      (10.0, "exitRight", 600.0),
                      (25.0, "exitRight", 100.0)], clock=lambda: now[0])
    assert src.poll().maneuver == "continueAhead"
    now[0] = 12.0
    assert src.poll().distance_m == 600.0
    now[0] = 99.0
    assert src.poll().distance_m == 100.0

  def test_a_script_that_has_not_started_says_nothing_yet(self):
    now = [0.0]
    src = StubSource([(5.0, "turnLeft", 50.0)], clock=lambda: now[0])
    assert src.poll() is None
    now[0] = 5.0
    assert src.poll().maneuver == "turnLeft"

  def test_a_missing_distance_is_reported_as_missing_not_as_zero(self):
    # The rule that RearApproachSide.from_blis broke: a source that cannot measure something must
    # not be given a number for it.
    src = StubSource([(0.0, "exitRight", None)])
    inst = src.poll()
    assert not inst.distance_known
    assert inst.distance_m == 0.0
    ri = RouteIntent()
    ri.update(intent_sm(maneuver=inst.maneuver, distance=inst.distance_m,
                        distance_known=inst.distance_known), now_ns=NOW)
    assert not ri.refuses_pass(30.0)

  def test_a_scripted_route_can_never_be_read_as_a_real_one(self):
    # Two populations read as one is the shape of every denominator error in this fork's history.
    assert StubSource([(0.0, "none", None)]).source == "stub"

  def test_it_round_trips_through_the_real_schema(self):
    """Built against real capnp, because the fixtures above are namespaces.

    A capnp enum rejects a name that is not in the schema and a numpy scalar blows up at the
    boundary -- neither of which a SimpleNamespace has any opinion about. This is the same gap
    test_capnp_accepts_published_types.py exists for.
    """
    msg = custom.RouteIntentBP.new_message()
    fill_message(msg, Instruction(maneuver="forkLeft", distance_m=421.5, distance_known=True,
                                  observed_mono_ns=NOW), "phoneBridge")
    ri = RouteIntent()
    ri.update(intent_sm(maneuver=str(msg.maneuver), distance=float(msg.distance),
                        distance_known=bool(msg.distanceKnown), source=str(msg.source),
                        observed=int(msg.observedMonoTime)), now_ns=NOW)
    assert ri.available and ri.source == "phoneBridge"
    assert ri.refuses_pass(30.0)

  def test_nothing_is_a_stamped_silence_not_an_absence(self):
    # "No route active" and "the transport is broken" must not read the same. The first carries a
    # fresh stamp and says the road ahead is clear of maneuvers; the second ages out.
    inst = Instruction.nothing()
    assert inst.maneuver == "none" and inst.observed_mono_ns > 0
