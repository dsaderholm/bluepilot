"""
FusionPilot: command the turn signal for a passing-assist lane change.

Everything here is wiring. The mechanism was settled and proven on the car on 2026-08-06 and lives
in blinker_test_ext.py and blinker_phase_lock.py; this asks it the same question from a different
caller, which is what BlinkerPhaseLock's docstring says it was factored out for:

    "so that both the bench test and any real feature ask the same question the same way"

WHAT IS NOT REPEATED HERE, deliberately:

  * the phase lock itself -- BlinkerPhaseLock owns it
  * the blink itself -- one commanded frame per ON slot, the gateway's frames between them
  * the blink period -- FordBlinkerBlinkPeriod, the one number the car has to agree with

WHY THE TEST PATH COULD NOT SIMPLY BE CALLED. update_blinker_test aborts on `vEgo > 0.3` and on
`cruiseState.enabled`, and both are correct FOR A TEST: "a stationary car signals nothing about a
maneuver, which makes an erroneous [one] harmless." A lane change is the opposite case in both
respects. The gates are the only thing that differs, so only the gates are absent.

CANCELLING COSTS NOTHING, which is the question that prompted this being written down. The blinks
are commanded frames, not a one-touch latch -- "our frame turns the lamp on, the gateway's next
frame turns it off". Stop asking and it stops blinking, mid-pattern, on the next frame. A sequence
that backs out after two blinks blinked twice, and the rule that a signal must never describe a
maneuver that is not happening is kept by construction rather than by a timer.

AND IT DOES NOT USE BLINK_COUNT, which is the other half of the same idea. Asked directly:

    "Am I going to have to test how many blinks covers one of your lane changes?"

No, and a fixed count is what would have forced that. BLINK_COUNT is 7 because that is what his
one-touch does, and a one-touch is the right emulation of a stalk TAP. A lane change is not a tap.
Seven blinks is 5.3 s at the 0.76 s period; the sequence is the signal lead plus CHANGE_DURATION_S,
and his own measured changes ran 5.88 s before the lead is added -- so a fixed seven runs out
part-way across, with the car still moving and the signal already dark.

So the signal lasts as long as the REQUEST does. blinkerWouldBeOn is published "on through the
crossing, out when it completes", which is exactly the right bound and is already measured rather
than assumed. Nothing to tune, and nothing for him to count.

His own cross-check, and it is reassuring rather than contradictory: *"7 blinks covers a sunnypilot
nudgeless lane change with a 1 second delay currently, which is why I use it."* So seven is about
right for the maneuver as he drives it today, and a request-bounded signal should land near seven
by itself. The difference is that it cannot run SHORT if a crossing takes longer than nominal, and
cannot run LONG if one is cut off early -- neither of which a fixed count can promise, and both of
which would otherwise be his to discover on the road.
"""

from opendbc.car.interfaces import DT_CTRL
from openpilot.common.params import Params
from opendbc.sunnypilot.car.ford.blinker_phase_lock import BlinkerPhaseLock
from opendbc.sunnypilot.car.ford.blinker_test_ext import (
  DEFAULT_BLINK_PERIOD_S, SIGNAL_NONE, SIGNAL_LEFT, SIGNAL_RIGHT)

SIDE_LEFT, SIDE_RIGHT = 1, 2

# RUNAWAY BACKSTOP, not the normal bound. The request's own lifetime is what ends the signal; this
# only catches a planner that wedges with blinkerWouldBeOn stuck true. A turn signal that never goes
# off is the failure mode worth spending a constant on -- it is the software half of the hardware
# self-clear that stalk injection would have needed.
#
# Generous on purpose: the longest legitimate sequence is the signal lead plus a crossing plus an
# abort, and 30 s is several times that. It should never be reached, and reaching it means something
# upstream is broken rather than slow.
MAX_SIGNAL_S = 30.0


class PassingAssistBlinker:
  """Turns the planner's published request into the value carcontroller transmits."""

  def __init__(self, period_s: float | None = None):
    # THE SAME PARAM THE PROVEN PATH USES, read the same way. Passing in the test's
    # bt_blink_period_s instead looked tidier and was wrong: that attribute holds the DEFAULT until
    # a bench test arms, so a car with FordBlinkerBlinkPeriod changed would signal at 0.76 s
    # regardless -- correct today only because his setting happens to be 760.
    #
    # Read once at construction rather than per frame. The period is a property of the car's blink
    # rate, and blinker_test_ext's own note applies: the count and the send condition must come from
    # the SAME number, or they round differently and open a truncated extra blink at the end.
    if period_s is None:
      try:
        period_s = float(Params().get("FordBlinkerBlinkPeriod", return_default=True)) / 1000.0
      except Exception:  # noqa: BLE001 - a bad param must not stop the car controller starting
        period_s = float(DEFAULT_BLINK_PERIOD_S)
    self._lock = BlinkerPhaseLock(float(period_s))
    self._side = 0
    self._frame = 0
    self._start_frame = 0

  @staticmethod
  def _request(sm) -> int:
    """The side to signal, or 0. Reads what the dry run already publishes.

    THREE FIELDS, AND `actuating` IS THE ONE THAT MATTERS. blinkerWouldBeOn and maneuverSide are
    published on every drive since the feature was written -- they are the dry run's whole output --
    so acting on them alone would have commanded the signal for weeks. `actuating` is false whenever
    the rear sensor on the side being moved into is unavailable, which today is always.

    Shaped after hud_ext.py, which already reads this message on the car side. Same broad except:
    a planner that is missing or malformed must never be able to stop the controller.
    """
    try:
      pa = sm['longitudinalPlanSP'].passingAssist
      if not (bool(pa.actuating) and bool(pa.blinkerWouldBeOn)):
        return 0
      return int(pa.maneuverSide)
    except Exception:  # noqa: BLE001 - no planner is not a reason to break the car controller
      return 0

  def update(self, sm, gateway_ts: int) -> int:
    """One control frame. Returns the TurnLghtSwtch_D_Stat value, or SIGNAL_NONE.

    Called only when the bench test returned nothing, so the two can never fight over the switch.
    """
    self._frame += 1
    side = self._request(sm)
    if side not in (SIDE_LEFT, SIDE_RIGHT):
      self._side = 0
      return SIGNAL_NONE

    # A fresh pattern on a new request, and on a side that changed under us. Re-arming resets the
    # phase so the first gateway frame after it begins an ON slot rather than landing wherever the
    # previous run left off -- and resets blinks_sent, so a genuinely new maneuver gets its own
    # seven rather than inheriting a spent counter.
    if side != self._side:
      self._side = side
      self._start_frame = self._frame
      self._lock.arm(self._frame)

    # See MAX_SIGNAL_S. Not the normal end -- the request going away is.
    if (self._frame - self._start_frame) * DT_CTRL > MAX_SIGNAL_S:
      return SIGNAL_NONE
    if not self._lock.should_send(self._frame, gateway_ts):
      return SIGNAL_NONE
    return SIGNAL_LEFT if side == SIDE_LEFT else SIGNAL_RIGHT
