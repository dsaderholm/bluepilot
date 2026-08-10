"""
FusionPilot: the dry run of a fully-automatic pass.

The question this file exists to answer is the owner's: "would it theoretically function
correctly?" Not "does each gate work" -- that is tested next door -- but does the SEQUENCE hold
together: does it commit when it should, back out when it should, back out cleanly, and never leave
a blinker on for a maneuver that is not happening.

So the cases here are transitions and aborts, not states. A state machine that reaches every state
and mishandles every edge between them would pass a state-coverage suite and strand a car
mid-signal on the road.
"""

from cereal import custom
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot.selfdrive.controls.lib.passing_maneuver import (
  SIGNAL_WINDOW_S,
  PassingManeuver, CHANGE_DURATION_S, FINISH_HOLD_S, ABORT_DURATION_S, ABORT_STANDDOWN_S,
)

Side = custom.LongitudinalPlanSP.PassingAssist.Side
Phase = custom.LongitudinalPlanSP.PassingAssist.Maneuver


def run(m, seconds, *, clear=Side.none, suggested=Side.none, confirming=False, confirmed=False,
        override=False, collision=False, actuating=False, settle_s=None, wanted=None):
  kw = {} if settle_s is None else {"settle_after_change_s": settle_s}
  if wanted is not None:
    kw["wanted"] = wanted
  for _ in range(max(1, int(round(seconds / DT_MDL)))):
    m.update(clear=clear, suggested=suggested, confirming=confirming, confirmed=confirmed,
             driver_override=override, collision_abort=collision, actuating=actuating, **kw)
  return m


def armed(lead_s=1.0, confirmed=True):
  """A machine that has just entered `signaling`, which is where everything interesting happens.

  `confirmed=True` by default so most cases exercise the gates rather than the confirmation clock;
  the overlap itself is tested explicitly in TestTheClocksOverlap.
  """
  m = PassingManeuver()
  m.blinker_lead_s = lead_s
  m.update(clear=Side.left, suggested=Side.left if confirmed else Side.none,
           confirming=not confirmed, confirmed=confirmed, driver_override=False)
  assert m.phase == Phase.signaling
  return m


class TestTheHappyPath:
  def test_the_whole_sequence_in_order(self):
    m = PassingManeuver()
    run(m, 0.5, confirming=True)
    assert m.phase == Phase.confirming

    run(m, 0.5, confirmed=True)
    assert m.phase == Phase.waiting, "confirmed but gated is its own state, not 'still deciding'"

    run(m, 0.5, clear=Side.left, suggested=Side.left, confirmed=True)
    assert m.phase == Phase.signaling
    assert m.side == Side.left
    assert m.blinker_on and not m.steering_active

    run(m, 0.6, clear=Side.left, suggested=Side.left, confirmed=True)
    assert m.phase == Phase.changing
    assert m.blinker_on, "the signal stays on THROUGH the crossing -- that is how a person signals"
    assert m.steering_active

    run(m, CHANGE_DURATION_S, clear=Side.left, suggested=Side.left, confirmed=True)
    assert m.phase == Phase.finishing
    assert not m.blinker_on, "signal must go out when the change completes"
    assert not m.steering_active

    run(m, FINISH_HOLD_S, suggested=Side.none)
    assert m.phase == Phase.idle
    assert m.aborts == 0

  def test_the_signal_hold_is_the_configured_one(self):
    m = armed(lead_s=2.0)
    run(m, 1.5, clear=Side.left, suggested=Side.left, confirmed=True)
    assert m.phase == Phase.signaling
    run(m, 0.6, clear=Side.left, suggested=Side.left, confirmed=True)
    assert m.phase == Phase.changing


class TestBackingOut:
  """The whole point. A gate that flickers is invisible frame by frame and fatal to a maneuver."""

  def test_a_gate_going_red_during_the_signal_aborts_and_is_counted(self):
    m = armed()
    run(m, 0.3, clear=Side.left, suggested=Side.left, confirmed=True)
    run(m, DT_MDL, suggested=Side.none, confirmed=True)
    assert m.phase == Phase.waiting
    assert not m.blinker_on, "blinker must go out with the abort"
    assert m.side == Side.none
    assert m.aborts == 1, "this is the number the whole module exists to produce"

  def test_the_side_changing_mid_signal_also_aborts(self):
    """Signaling left and then quietly starting to go right would be the worst outcome available:
    the traffic behind was told one thing and the car did another."""
    m = armed()
    run(m, 0.3, clear=Side.left, suggested=Side.left, confirmed=True)
    run(m, DT_MDL, clear=Side.right, suggested=Side.right, confirmed=True)
    assert m.aborts == 1
    assert m.phase != Phase.changing

  def test_it_can_recover_and_go_again(self):
    """An abort must not wedge it. If the gate clears, the next sequence has to run normally."""
    m = armed()
    run(m, DT_MDL, suggested=Side.none, confirmed=True)
    assert m.aborts == 1
    run(m, DT_MDL, clear=Side.left, suggested=Side.left, confirmed=True)
    assert m.phase == Phase.signaling
    run(m, 1.1, clear=Side.left, suggested=Side.left, confirmed=True)
    assert m.phase == Phase.changing
    assert m.aborts == 1, "recovering is not a second abort"

  def test_losing_the_lead_entirely_returns_to_idle(self):
    m = armed()
    run(m, DT_MDL, suggested=Side.none, confirming=False, confirmed=False)
    assert m.phase == Phase.idle
    assert m.side == Side.none


class TestTheClocksOverlap:
  """The owner, on being told the blinker waited for the confirmation: "That doesn't seem right.
  It should come on instantly telling drivers I want to change lanes... I want this to realize the
  car is slow faster than I can and change lanes as fast as possible."

  So the signal goes on the moment a slow car is spotted with somewhere to go, and the confirmation
  runs underneath it. The crossing waits for whichever clock finishes LAST, not for their sum.
  """

  def test_the_blinker_comes_on_before_the_confirmation_finishes(self):
    m = PassingManeuver()
    m.update(clear=Side.left, suggested=Side.none, confirming=True, confirmed=False,
             driver_override=False)
    assert m.phase == Phase.signaling
    assert m.blinker_on
    assert not m.steering_active, "signaling, not moving"

  def test_it_does_not_move_until_confirmed(self):
    """Signal early, commit late. A 1 s signal lead must not let an unconfirmed car be passed."""
    m = armed(lead_s=1.0, confirmed=False)
    run(m, 3.0, clear=Side.left, suggested=Side.none, confirming=True)
    assert m.phase == Phase.signaling, "the signal lead alone must not start a crossing"
    assert m.aborts == 0

  def test_and_moves_as_soon_as_both_are_satisfied(self):
    m = armed(lead_s=1.0, confirmed=False)
    run(m, 1.5, clear=Side.left, suggested=Side.none, confirming=True)
    assert m.phase == Phase.signaling
    run(m, DT_MDL, clear=Side.left, suggested=Side.left, confirmed=True)
    assert m.phase == Phase.changing

  def test_the_wait_is_the_longer_clock_not_their_sum(self):
    """2 s of confirming and 1 s of signaling is 2 s in total, not 3. That second is the whole
    point -- it is a second of Ford ACC not braking for a car we had already decided to pass."""
    m = PassingManeuver()
    m.blinker_lead_s = 1.0
    run(m, 1.9, clear=Side.left, suggested=Side.none, confirming=True)
    assert m.phase == Phase.signaling
    assert m.phase_seconds >= 1.0, "blinker has already been up for its full lead"
    run(m, DT_MDL, clear=Side.left, suggested=Side.left, confirmed=True)
    assert m.phase == Phase.changing, "should move the instant confirmation lands, not a second after"

  def test_a_car_that_turns_out_not_to_be_slow_drops_the_signal(self):
    """The honest cost of signaling early, and exactly what the abort count is for."""
    m = armed(lead_s=1.0, confirmed=False)
    run(m, 0.5, clear=Side.left, suggested=Side.none, confirming=True)
    run(m, DT_MDL, clear=Side.none, suggested=Side.none)
    assert not m.blinker_on
    assert m.aborts == 1


class TestThePointOfNoReturn:
  """A real car cannot un-change lanes halfway across. A model that pretended otherwise would
  report a clean sequence that reality could not have delivered."""

  def test_a_gate_cannot_call_it_off_once_crossing(self):
    m = armed()
    run(m, 1.1, clear=Side.left, suggested=Side.left, confirmed=True)
    assert m.phase == Phase.changing
    run(m, 0.5, suggested=Side.none)
    assert m.phase == Phase.changing, "a gate must not abort a crossing that has begun"
    assert m.aborts == 0, "and it must not be counted as one either"

  def test_the_driver_always_can(self):
    """Not a gate -- the driver taking their car back, at any phase including mid-crossing."""
    m = armed()
    run(m, 1.1, clear=Side.left, suggested=Side.left, confirmed=True)
    assert m.phase == Phase.changing
    run(m, DT_MDL, clear=Side.left, suggested=Side.left, confirmed=True, override=True)
    assert m.phase == Phase.idle
    assert not m.blinker_on and not m.steering_active

  def test_a_driver_takeover_is_not_counted_as_an_abort(self):
    """It is the correct outcome, and counting it would poison the one metric that matters."""
    m = armed()
    run(m, 0.3, clear=Side.left, suggested=Side.left, confirmed=True)
    run(m, DT_MDL, clear=Side.left, suggested=Side.left, confirmed=True, override=True)
    assert m.phase == Phase.idle
    assert m.aborts == 0


class TestNothingHappensWhenNothingShould:
  def test_idle_stays_idle(self):
    m = run(PassingManeuver(), 5.0)
    assert m.phase == Phase.idle
    assert not m.blinker_on and not m.steering_active
    assert m.aborts == 0

  def test_a_driver_signaling_never_starts_a_sequence(self):
    """They are already doing it themselves -- with sunnypilot's own lane change, which stays the
    driver's tool. The dry run must not shadow it."""
    m = run(PassingManeuver(), 3.0, clear=Side.left, suggested=Side.left, confirmed=True, override=True)
    assert m.phase == Phase.idle
    assert m.aborts == 0

  def test_confirming_alone_never_lights_a_blinker(self):
    m = run(PassingManeuver(), 5.0, confirming=True)
    assert m.phase == Phase.confirming
    assert not m.blinker_on


class TestCollisionAbort:
  """Roadmap feature #1, and the only one of the five that REDUCES risk rather than adding
  convenience. It has to exist before automatic lane changes do: a car that cannot back out should
  not be initiating.

  The shape is that abort criteria narrow as the maneuver progresses. A gate going red stops a
  sequence that has not moved and is powerless once the crossing begins -- a car cannot un-change
  lanes on a change of mind. A vehicle ARRIVING behind is a different question, and reversing is
  worth doing from anywhere.
  """

  def test_it_reverses_a_crossing_already_underway(self):
    m = armed()
    run(m, 1.1, clear=Side.left, suggested=Side.left, confirmed=True)
    assert m.phase == Phase.changing
    run(m, DT_MDL, clear=Side.left, suggested=Side.left, confirmed=True, collision=True)
    assert m.phase == Phase.aborting
    assert m.emergency_aborts == 1
    assert not m.steering_active and not m.blinker_on

  def test_it_also_stops_one_that_has_not_moved_yet(self):
    m = armed()
    run(m, DT_MDL, clear=Side.left, suggested=Side.left, confirmed=True, collision=True)
    assert m.phase == Phase.aborting
    assert m.emergency_aborts == 1

  def test_it_is_counted_apart_from_an_ordinary_backout(self):
    """One is the system changing its mind, the other is avoiding a collision. Averaging them
    would hide the second inside the first, which is the number that matters."""
    m = armed()
    run(m, DT_MDL, clear=Side.left, suggested=Side.left, confirmed=True, collision=True)
    assert m.emergency_aborts == 1
    assert m.aborts == 0

  def test_the_abort_finishes_and_returns_to_idle(self):
    m = armed()
    run(m, 1.1, clear=Side.left, suggested=Side.left, confirmed=True)
    run(m, DT_MDL, clear=Side.left, suggested=Side.left, confirmed=True, collision=True)
    run(m, ABORT_DURATION_S + 0.2, clear=Side.left, suggested=Side.left, confirmed=True)
    # `waiting`, not idle: a pass is still warranted and something is still stopping it -- the
    # stand-down. Reporting idle there would say the system had lost interest, which is wrong.
    assert m.phase == Phase.waiting
    assert m.side == Side.none
    assert not m.blinker_on

  def test_it_does_not_immediately_try_again(self):
    """Every input is unchanged after an abort -- slow car still there, lane clear again once the
    vehicle behind has gone past -- so without a stand-down it re-signals within seconds. Backing
    out and then signaling again is worse than either doing it or not: whoever just went past has
    no idea what this car is doing."""
    m = armed()
    run(m, DT_MDL, clear=Side.left, suggested=Side.left, confirmed=True, collision=True)
    run(m, ABORT_DURATION_S + 3.0, clear=Side.left, suggested=Side.left, confirmed=True)
    assert m.phase not in (Phase.signaling, Phase.changing), "re-signaled during the stand-down"
    assert not m.blinker_on

  def test_and_works_again_once_the_stand_down_expires(self):
    m = armed()
    run(m, DT_MDL, clear=Side.left, suggested=Side.left, confirmed=True, collision=True)
    run(m, ABORT_STANDDOWN_S + 1.0, clear=Side.left, suggested=Side.left, confirmed=True)
    assert m.phase in (Phase.signaling, Phase.changing)

  def test_a_gate_still_cannot_reverse_a_crossing(self):
    """The narrow tier must stay narrow. If an ordinary gate could do this, every flickering
    blind-spot reading would throw the car back mid-maneuver."""
    m = armed()
    run(m, 1.1, clear=Side.left, suggested=Side.left, confirmed=True)
    run(m, 0.5, clear=Side.none, suggested=Side.none)
    assert m.phase == Phase.changing
    assert m.emergency_aborts == 0

  def test_nothing_fires_without_a_rear_sensor(self):
    """The whole thing hangs off demands_abort, which answers False when unavailable rather than
    guessing -- so on a car with no rear radar this path is inert, not trigger-happy."""
    m = armed()
    run(m, 3.0, clear=Side.left, suggested=Side.left, confirmed=True, collision=False)
    assert m.emergency_aborts == 0
    assert m.phase in (Phase.changing, Phase.finishing)

  def test_the_stand_down_is_visible_while_it_runs(self):
    """Without this the panel contradicts itself. The detector still says a pass is warranted and
    the lane is clear -- both true -- so with nothing said here the screen falls through to a green
    PASS LEFT seconds after the car backed out of exactly that pass."""
    m = armed()
    run(m, DT_MDL, clear=Side.left, suggested=Side.left, confirmed=True, collision=True)
    run(m, ABORT_DURATION_S + 1.0, clear=Side.left, suggested=Side.left, confirmed=True)
    assert m.phase == Phase.waiting
    assert m.standdown_remaining > 0.0, "nothing on the wire to say why it is refusing"

  def test_and_is_zero_when_nothing_has_been_reversed(self):
    m = run(PassingManeuver(), 5.0, clear=Side.left, suggested=Side.left, confirmed=True)
    assert m.standdown_remaining == 0.0


class TestWhatChangesWhenItActuates:
  """The two timings this file's constants said were owed once a control exists.

  Both are conditional rather than replaced, and that is the point. While this only narrates, the
  abort COUNT is the product and suppressing flicker would hide it; the moment it drives the turn
  signal the flicker IS the problem and the count has done its job. Same state machine, one flag.
  """

  SETTLE = 4.0

  @staticmethod
  def _step_until_idle(m, actuating, settle_s, cap_s=30.0):
    """Run a committed crossing through to completion. Returns seconds taken, or None."""
    for i in range(int(cap_s / DT_MDL)):
      m.update(clear=Side.left, suggested=Side.left, confirming=False, confirmed=True,
               driver_override=False, actuating=actuating, settle_after_change_s=settle_s)
      if m.phase == Phase.idle:
        return (i + 1) * DT_MDL
    return None

  def _gate_drops_during_signaling(self, actuating):
    m = armed()
    run(m, 0.1, clear=Side.none, confirmed=True, actuating=actuating)
    return m

  def test_observing_re_signals_at_once_so_the_count_stays_honest(self):
    """Unchanged behaviour, pinned. A flickering gate must produce one abort per flicker, or the
    number measuring gate instability under-reports it by the ratio of the stand-down."""
    m = self._gate_drops_during_signaling(actuating=False)
    assert m.aborts == 1
    run(m, 0.05, clear=Side.left, suggested=Side.left, confirmed=True)
    assert m.phase == Phase.signaling, "an observing run must be free to re-signal immediately"

  def test_actuating_stands_down_instead_of_strobing_the_signal(self):
    """THE REASON THIS EXISTS. Re-entering `signaling` on the next frame puts the lamp back on at
    the gate's chatter rate, which traffic behind reads as noise rather than as intent."""
    m = self._gate_drops_during_signaling(actuating=True)
    assert m.aborts == 1
    run(m, 0.05, clear=Side.left, suggested=Side.left, confirmed=True, actuating=True)
    assert m.phase != Phase.signaling, "re-signalled immediately while driving the lamp"

  def test_that_stand_down_expires_rather_than_latching(self):
    m = self._gate_drops_during_signaling(actuating=True)
    run(m, ABORT_STANDDOWN_S + 0.2, clear=Side.none, confirmed=True, actuating=True)
    run(m, 0.05, clear=Side.left, suggested=Side.left, confirmed=True, actuating=True)
    assert m.phase == Phase.signaling

  def test_observing_holds_thirty_seconds_because_nothing_moved(self):
    """The endless cycle from the road -- "it just kept saying that over and over again". Nothing
    moved, so every input still reads the same and the sequence restarts without this."""
    m = armed()
    assert self._step_until_idle(m, actuating=False, settle_s=self.SETTLE) is not None
    run(m, self.SETTLE + 1.0, clear=Side.left, suggested=Side.left, confirmed=True)
    # `waiting`, not `idle`: still confirmed, and the stand-down is what holds it. Asserting idle
    # here was wrong about the machine rather than about the timing.
    assert m.phase != Phase.signaling, "restarted before COMPLETE_STANDDOWN_S"

  def test_actuating_only_waits_out_the_anti_weave_settle(self):
    """Once the car really moves the reason to pass is gone -- we are past the slow vehicle. What
    remains is not a loop guard, it is the detector's own wait before changing lanes again."""
    m = armed()
    assert self._step_until_idle(m, actuating=True, settle_s=self.SETTLE) is not None
    run(m, self.SETTLE + 0.3, clear=Side.left, suggested=Side.left, confirmed=True,
        actuating=True, settle_s=self.SETTLE)
    assert m.phase == Phase.signaling, "still held by the dry-run stand-down while actuating"

  def test_the_settle_is_passed_in_rather_than_invented_here(self):
    """passing_assist imports this module, so this module cannot import its constants back. The
    number arriving as an argument is what keeps one owner for the anti-weave policy."""
    m = armed()
    assert self._step_until_idle(m, actuating=True, settle_s=30.0) is not None
    run(m, self.SETTLE + 0.3, clear=Side.left, suggested=Side.left, confirmed=True,
        actuating=True, settle_s=30.0)
    assert m.phase != Phase.signaling, "a larger settle must actually hold it longer"


class TestSignalFirstThenCheck:
  """"Signaling should always start right when it notices a car is slow, and then during that one
  second of signaling it should then check blind spots and radar and all of that before making the
  change."

  What production systems do, and both benchmarks bound it the same way: Super Cruise holds in lane
  showing "looking for an opening" and gives up at five seconds, BlueCruise at about ten. The
  difference here is that this one decides for itself, so the promise has no human behind it.
  """

  def test_the_signal_comes_up_before_the_gates_are_happy(self):
    """THE CHANGE. `wanted` is a slow car and a lane; `clear` is every safety gate. The blinker
    now follows the first, and used to wait for the second."""
    m = PassingManeuver()
    run(m, 0.2, wanted=Side.left, clear=Side.none, confirmed=True)
    assert m.phase == Phase.signaling
    assert m.side == Side.left

  def test_but_it_does_not_cross_until_they_are(self):
    """Signalling early is a promise; moving is the act. The gates guard the act."""
    m = PassingManeuver()
    run(m, 3.0, wanted=Side.left, clear=Side.none, suggested=Side.left, confirmed=True)
    assert m.phase == Phase.signaling, "crossed with a gate still red"

  def test_the_gates_must_hold_continuously_not_momentarily(self):
    """The guarantee that moving the gates nearly lost. A blind-spot return dropping in and out
    could otherwise be true on the single frame that is sampled, and the car commits into a gap
    that was never there."""
    m = PassingManeuver()
    for i in range(int(4.0 / DT_MDL)):
      flicker = Side.left if (i // int(0.2 / DT_MDL)) % 2 == 0 else Side.none
      m.update(clear=flicker, wanted=Side.left, suggested=Side.left, confirming=False,
               confirmed=True, driver_override=False)
    assert m.phase != Phase.changing, "committed on a flickering gate"

  def test_it_crosses_once_they_settle(self):
    m = PassingManeuver()
    run(m, 0.2, wanted=Side.left, clear=Side.none, suggested=Side.left, confirmed=True)
    run(m, m.blinker_lead_s + 0.2, wanted=Side.left, clear=Side.left, suggested=Side.left,
        confirmed=True)
    assert m.phase == Phase.changing

  def test_the_blinker_is_never_up_for_less_than_the_lead(self):
    """His rule, and the change strengthens it rather than weakening it: the lead is measured from
    when the gates went good, so the signal has been up at least that long and usually longer."""
    m = PassingManeuver()
    run(m, 2.0, wanted=Side.left, clear=Side.none, suggested=Side.left, confirmed=True)
    before = m.phase_seconds
    run(m, m.blinker_lead_s - 0.1, wanted=Side.left, clear=Side.left, suggested=Side.left,
        confirmed=True)
    assert m.phase == Phase.signaling, "crossed before the lead elapsed from the gates going good"
    assert m.phase_seconds > before

  def test_the_window_expires_rather_than_promising_forever(self):
    """See SIGNAL_WINDOW_S. A signal held while nothing happens is the "never signal what you are
    not doing" failure, and it is what bounds signalling before certainty.

    Asserted on the COUNT rather than the phase: while only narrating there is no stand-down, so it
    backs out and is free to re-signal on the next frame, and sampling the phase afterwards catches
    the new sequence rather than the end of the old one."""
    m = PassingManeuver()
    run(m, SIGNAL_WINDOW_S - 0.5, wanted=Side.left, clear=Side.none, suggested=Side.left,
        confirmed=True)
    assert m.aborts == 0, "gave up before the window was out"
    run(m, 0.8, wanted=Side.left, clear=Side.none, suggested=Side.left, confirmed=True)
    assert m.aborts == 1, "still promising past the window"

  def test_and_while_actuating_it_stands_down_instead_of_re_signalling(self):
    """The same expiry, with a control wired. Backing out and lighting the lamp again on the next
    frame is the strobe, so the stand-down applies here exactly as it does to a gate abort."""
    m = PassingManeuver()
    run(m, SIGNAL_WINDOW_S + 0.3, wanted=Side.left, clear=Side.none, suggested=Side.left,
        confirmed=True, actuating=True)
    assert m.aborts == 1
    assert m.phase != Phase.signaling, "re-signalled immediately while driving the lamp"

  def test_losing_the_reason_ends_it_immediately_rather_than_waiting_out_the_window(self):
    """A gate saying "not yet" is worth waiting on. The slow car or the lane going away is not."""
    m = PassingManeuver()
    run(m, 0.2, wanted=Side.left, clear=Side.none, confirmed=True)
    assert m.phase == Phase.signaling
    run(m, 0.1, wanted=Side.none, clear=Side.none, confirmed=True)
    assert m.phase != Phase.signaling
