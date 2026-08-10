"""
FusionPilot: command the turn signal for a passing-assist lane change.

Everything here is wiring. The mechanism was settled and proven on the car on 2026-08-06 and lives
in blinker_test_ext.py and blinker_phase_lock.py; this asks it the same question from a different
caller, which is what BlinkerPhaseLock's docstring says it was factored out for:

    "so that both the bench test and any real feature ask the same question the same way"

WHAT IS NOT REPEATED HERE, deliberately:

  * the phase lock itself -- BlinkerPhaseLock owns it
  * how many blinks -- BLINK_COUNT, the same 7 the "Blink Left"/"Blink Right" buttons send
  * the blink period -- FordBlinkerBlinkPeriod, the one number the car has to agree with

WHY THE TEST PATH COULD NOT SIMPLY BE CALLED. update_blinker_test aborts on `vEgo > 0.3` and on
`cruiseState.enabled`, and both are correct FOR A TEST: "a stationary car signals nothing about a
maneuver, which makes an erroneous [one] harmless." A lane change is the opposite case in both
respects. The gates are the only thing that differs, so only the gates are absent.

CANCELLING COSTS NOTHING, which is the question that prompted this being written down. The seven
blinks are seven commanded frames, not a one-touch latch -- "our frame turns the lamp on, the
gateway's next frame turns it off". Stop asking and it stops blinking, mid-pattern, on the next
frame. A sequence that backs out after two blinks blinked twice, and the rule that a signal must
never describe a maneuver that is not happening is kept by construction rather than by a timer.
"""

from openpilot.common.params import Params
from opendbc.sunnypilot.car.ford.blinker_phase_lock import BlinkerPhaseLock
from opendbc.sunnypilot.car.ford.blinker_test_ext import (
  BLINK_COUNT, DEFAULT_BLINK_PERIOD_S, SIGNAL_NONE, SIGNAL_LEFT, SIGNAL_RIGHT)

SIDE_LEFT, SIDE_RIGHT = 1, 2


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
      self._lock.arm(self._frame)

    if self._lock.blinks_sent >= BLINK_COUNT:
      return SIGNAL_NONE
    if not self._lock.should_send(self._frame, gateway_ts):
      return SIGNAL_NONE
    return SIGNAL_LEFT if side == SIDE_LEFT else SIGNAL_RIGHT
