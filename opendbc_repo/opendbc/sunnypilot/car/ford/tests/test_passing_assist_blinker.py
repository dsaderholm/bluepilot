"""
FusionPilot: the passing-assist turn signal, which is wiring around a proven mechanism.

The blink pattern itself is tested in test_blinker_phase_lock.py and was settled on the car over
many attempts. Nothing here re-tests it. What is tested is the part that is new and the part that
would be catastrophic to get wrong: that the DRY RUN cannot command the signal.

blinkerWouldBeOn and maneuverSide have been published on every drive since the feature existed. A
consumer that acted on those alone would have been signalling lane changes on his commute for
weeks, so the `actuating` bit carries the whole weight and every path through it is asserted.
"""

from types import SimpleNamespace

from opendbc.sunnypilot.car.ford.passing_assist_blinker import PassingAssistBlinker
from opendbc.sunnypilot.car.ford.blinker_test_ext import SIGNAL_NONE, SIGNAL_LEFT, SIGNAL_RIGHT

PERIOD_S = 0.76


class FakeSM:
  """SubMaster-shaped, carrying only what _request reads."""

  def __init__(self, actuating=True, blinker=True, side=1, broken=False):
    self._broken = broken
    pa = SimpleNamespace(actuating=actuating, blinkerWouldBeOn=blinker, maneuverSide=side)
    self.data = {'longitudinalPlanSP': SimpleNamespace(passingAssist=pa)}

  def __getitem__(self, k):
    if self._broken:
      raise KeyError(k)
    return self.data[k]


def drive(b, sm, frames=200, ts_start=1):
  """Run frames with a FRESH gateway timestamp each one, collecting what would be transmitted."""
  out = []
  for i in range(frames):
    out.append(b.update(sm, ts_start + i))
  return out


def blinks(out):
  return [v for v in out if v != SIGNAL_NONE]


def blink_groups(out):
  """ON slots, which is what a driver counts as a flash.

  NOT the number of sends. should_send answers true for every gateway frame inside an ON slot --
  about four at a 0.76 s period against a 10 Hz gateway -- because our frames are what hold the lamp
  on while the gateway's frames between slots turn it off. Counting sends counts frames, which is
  the same mistake blinker_test_ext records making with rising edges.
  """
  groups, prev = 0, SIGNAL_NONE
  for v in out:
    if v != SIGNAL_NONE and prev == SIGNAL_NONE:
      groups += 1
    prev = v
  return groups


class TestTheDryRunCannotCommandTheSignal:
  """The single most important property in this file."""

  def test_not_actuating_sends_nothing_even_with_the_blinker_requested(self):
    """EXACTLY THE STATE EVERY DRIVE HAS BEEN IN. blinkerWouldBeOn true, a side committed, and
    actuating false because no rear sensor exists. Must be silent."""
    out = drive(PassingAssistBlinker(PERIOD_S), FakeSM(actuating=False, blinker=True, side=1))
    assert blinks(out) == []

  def test_actuating_without_the_blinker_phase_sends_nothing(self):
    """`actuating` is a permission, not a request. Between maneuvers it is true and the signal
    must still be off."""
    out = drive(PassingAssistBlinker(PERIOD_S), FakeSM(actuating=True, blinker=False, side=1))
    assert blinks(out) == []

  def test_no_side_sends_nothing(self):
    out = drive(PassingAssistBlinker(PERIOD_S), FakeSM(actuating=True, blinker=True, side=0))
    assert blinks(out) == []

  def test_a_missing_planner_is_silent_rather_than_an_exception(self):
    """The car controller must not be stoppable by a planner that is absent or malformed."""
    out = drive(PassingAssistBlinker(PERIOD_S), FakeSM(broken=True))
    assert blinks(out) == []


class TestWhenItIsAllowedToSignal:

  def test_it_commands_the_committed_side(self):
    out = drive(PassingAssistBlinker(PERIOD_S), FakeSM(side=1))
    assert blinks(out), "never signalled"
    assert set(blinks(out)) == {SIGNAL_LEFT}

  def test_right_is_right(self):
    """A mapping that is backwards signals the opposite of the lane being entered, which is worse
    than not signalling at all."""
    out = drive(PassingAssistBlinker(PERIOD_S), FakeSM(side=2))
    assert set(blinks(out)) == {SIGNAL_RIGHT}

  def test_it_keeps_signalling_for_as_long_as_the_request_stands(self):
    """NOT a fixed count. BLINK_COUNT is 7 because that is his one-touch, which is the right
    emulation of a stalk TAP and the wrong bound for a crossing -- 5.3 s against a sequence that is
    the signal lead plus CHANGE_DURATION_S. A fixed seven runs out part-way with the car moving."""
    out = drive(PassingAssistBlinker(PERIOD_S), FakeSM(side=1), frames=1500)
    assert blink_groups(out) > 7, "stopped at a fixed count instead of tracking the request"


class TestCancelling:
  """"What if you cancel a lane change? Then it blinks 7 times for no reason."

  It does not. The seven blinks are seven commanded frames rather than a one-touch latch, so the
  pattern stops when the request does.
  """

  def test_dropping_the_request_stops_the_blinking(self):
    b = PassingAssistBlinker(PERIOD_S)
    sm = FakeSM(side=1)
    first = drive(b, sm, frames=40)
    assert blinks(first), "the fixture never got it signalling"
    sm.data['longitudinalPlanSP'].passingAssist.blinkerWouldBeOn = False
    assert blinks(drive(b, sm, frames=200, ts_start=100)) == []

  def test_it_stops_partway_rather_than_finishing_the_pattern(self):
    """The rule this keeps: a signal must never describe a maneuver that is not happening."""
    b = PassingAssistBlinker(PERIOD_S)
    sm = FakeSM(side=1)
    out = drive(b, sm, frames=40)
    sent = blink_groups(out)
    assert sent > 0, "need it signalling for this to mean anything"
    sm.data['longitudinalPlanSP'].passingAssist.actuating = False
    assert blinks(drive(b, sm, frames=200, ts_start=100)) == []

  def test_a_new_maneuver_on_the_other_side_signals_the_other_side(self):
    """Re-arming on a side change resets the phase, so the second maneuver gets a clean pattern
    rather than inheriting whatever the first left behind."""
    b = PassingAssistBlinker(PERIOD_S)
    sm = FakeSM(side=1)
    drive(b, sm, frames=400)
    sm.data['longitudinalPlanSP'].passingAssist.maneuverSide = 2
    out = drive(b, sm, frames=400, ts_start=1000)
    assert blink_groups(out) > 0
    assert set(blinks(out)) == {SIGNAL_RIGHT}

  def test_a_wedged_request_cannot_signal_forever(self):
    """See MAX_SIGNAL_S. A backstop, not the normal bound -- a turn signal that never goes off is
    the one failure here worth spending a constant on."""
    out = drive(PassingAssistBlinker(PERIOD_S), FakeSM(side=1), frames=6000)
    tail = out[-500:]
    assert blinks(tail) == [], "still signalling well past the runaway backstop"
