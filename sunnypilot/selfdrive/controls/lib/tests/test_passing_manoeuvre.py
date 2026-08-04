"""
BluePilot: the dry run of a fully-automatic pass.

The question this file exists to answer is the owner's: "would it theoretically function
correctly?" Not "does each gate work" -- that is tested next door -- but does the SEQUENCE hold
together: does it commit when it should, back out when it should, back out cleanly, and never leave
a blinker on for a manoeuvre that is not happening.

So the cases here are transitions and aborts, not states. A state machine that reaches every state
and mishandles every edge between them would pass a state-coverage suite and strand a car
mid-signal on the road.
"""

from cereal import custom
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot.selfdrive.controls.lib.passing_manoeuvre import (
  PassingManoeuvre, CHANGE_DURATION_S, FINISH_HOLD_S,
)

Side = custom.LongitudinalPlanSP.PassingAssist.Side
Phase = custom.LongitudinalPlanSP.PassingAssist.Manoeuvre


def run(m, seconds, *, suggested=Side.none, confirming=False, confirmed=False, override=False):
  for _ in range(max(1, int(round(seconds / DT_MDL)))):
    m.update(suggested=suggested, confirming=confirming, confirmed=confirmed, driver_override=override)
  return m


def armed(lead_s=1.0):
  """A machine that has just entered `signalling`, which is where everything interesting happens."""
  m = PassingManoeuvre()
  m.blinker_lead_s = lead_s
  m.update(suggested=Side.left, confirming=False, confirmed=True, driver_override=False)
  assert m.phase == Phase.signalling
  return m


class TestTheHappyPath:
  def test_the_whole_sequence_in_order(self):
    m = PassingManoeuvre()
    run(m, 0.5, confirming=True)
    assert m.phase == Phase.confirming

    run(m, 0.5, confirmed=True)
    assert m.phase == Phase.waiting, "confirmed but gated is its own state, not 'still deciding'"

    run(m, 0.5, suggested=Side.left, confirmed=True)
    assert m.phase == Phase.signalling
    assert m.side == Side.left
    assert m.blinker_on and not m.steering_active

    run(m, 0.6, suggested=Side.left, confirmed=True)
    assert m.phase == Phase.changing
    assert m.blinker_on, "the signal stays on THROUGH the crossing -- that is how a person signals"
    assert m.steering_active

    run(m, CHANGE_DURATION_S, suggested=Side.left, confirmed=True)
    assert m.phase == Phase.finishing
    assert not m.blinker_on, "signal must go out when the change completes"
    assert not m.steering_active

    run(m, FINISH_HOLD_S, suggested=Side.none)
    assert m.phase == Phase.idle
    assert m.aborts == 0

  def test_the_signal_hold_is_the_configured_one(self):
    m = armed(lead_s=2.0)
    run(m, 1.5, suggested=Side.left, confirmed=True)
    assert m.phase == Phase.signalling
    run(m, 0.6, suggested=Side.left, confirmed=True)
    assert m.phase == Phase.changing


class TestBackingOut:
  """The whole point. A gate that flickers is invisible frame by frame and fatal to a manoeuvre."""

  def test_a_gate_going_red_during_the_signal_aborts_and_is_counted(self):
    m = armed()
    run(m, 0.3, suggested=Side.left, confirmed=True)
    run(m, DT_MDL, suggested=Side.none, confirmed=True)
    assert m.phase == Phase.waiting
    assert not m.blinker_on, "blinker must go out with the abort"
    assert m.side == Side.none
    assert m.aborts == 1, "this is the number the whole module exists to produce"

  def test_the_side_changing_mid_signal_also_aborts(self):
    """Signalling left and then quietly starting to go right would be the worst outcome available:
    the traffic behind was told one thing and the car did another."""
    m = armed()
    run(m, 0.3, suggested=Side.left, confirmed=True)
    run(m, DT_MDL, suggested=Side.right, confirmed=True)
    assert m.aborts == 1
    assert m.phase != Phase.changing

  def test_it_can_recover_and_go_again(self):
    """An abort must not wedge it. If the gate clears, the next sequence has to run normally."""
    m = armed()
    run(m, DT_MDL, suggested=Side.none, confirmed=True)
    assert m.aborts == 1
    run(m, DT_MDL, suggested=Side.left, confirmed=True)
    assert m.phase == Phase.signalling
    run(m, 1.1, suggested=Side.left, confirmed=True)
    assert m.phase == Phase.changing
    assert m.aborts == 1, "recovering is not a second abort"

  def test_losing_the_lead_entirely_returns_to_idle(self):
    m = armed()
    run(m, DT_MDL, suggested=Side.none, confirming=False, confirmed=False)
    assert m.phase == Phase.idle
    assert m.side == Side.none


class TestThePointOfNoReturn:
  """A real car cannot un-change lanes halfway across. A model that pretended otherwise would
  report a clean sequence that reality could not have delivered."""

  def test_a_gate_cannot_call_it_off_once_crossing(self):
    m = armed()
    run(m, 1.1, suggested=Side.left, confirmed=True)
    assert m.phase == Phase.changing
    run(m, 0.5, suggested=Side.none)
    assert m.phase == Phase.changing, "a gate must not abort a crossing that has begun"
    assert m.aborts == 0, "and it must not be counted as one either"

  def test_the_driver_always_can(self):
    """Not a gate -- the driver taking their car back, at any phase including mid-crossing."""
    m = armed()
    run(m, 1.1, suggested=Side.left, confirmed=True)
    assert m.phase == Phase.changing
    run(m, DT_MDL, suggested=Side.left, confirmed=True, override=True)
    assert m.phase == Phase.idle
    assert not m.blinker_on and not m.steering_active

  def test_a_driver_takeover_is_not_counted_as_an_abort(self):
    """It is the correct outcome, and counting it would poison the one metric that matters."""
    m = armed()
    run(m, 0.3, suggested=Side.left, confirmed=True)
    run(m, DT_MDL, suggested=Side.left, confirmed=True, override=True)
    assert m.phase == Phase.idle
    assert m.aborts == 0


class TestNothingHappensWhenNothingShould:
  def test_idle_stays_idle(self):
    m = run(PassingManoeuvre(), 5.0)
    assert m.phase == Phase.idle
    assert not m.blinker_on and not m.steering_active
    assert m.aborts == 0

  def test_a_driver_signalling_never_starts_a_sequence(self):
    """They are already doing it themselves -- with sunnypilot's own lane change, which stays the
    driver's tool. The dry run must not shadow it."""
    m = run(PassingManoeuvre(), 3.0, suggested=Side.left, confirmed=True, override=True)
    assert m.phase == Phase.idle
    assert m.aborts == 0

  def test_confirming_alone_never_lights_a_blinker(self):
    m = run(PassingManoeuvre(), 5.0, confirming=True)
    assert m.phase == Phase.confirming
    assert not m.blinker_on
