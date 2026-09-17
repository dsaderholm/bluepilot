"""Check 3 must score a driver press against the number the car is DRIVEN to, not against MAX.

The complaint it was written for -- *"it changes the ICBM little speed number, not the max"* -- was
made while a hold was a SECOND number in its own badge. On 2026-08-22 the badge was deleted and the
set-speed box started showing the hold (`max_box_state.aim`), so a press that creates or moves a
hold correctly leaves `vCruiseCluster` where it is. The check kept reading `vCruiseCluster` alone
and therefore reported that as the complaint.

Measured on routes 0000046b..0000046f, 2026-09-15: 24 driver presses, of which the old rule called
12 `dash only`. Every one of those 12 moved the hold or walked the dash onto the MAX -- the rows
below are eight of them, verbatim from the wire, plus the two shapes that must still read BAD.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import pathlib

SPEC = importlib.util.spec_from_file_location(
  "bp_drive_checkup", pathlib.Path(__file__).resolve().parents[3] / "tools/bp_drive_checkup.py")
bp_drive_checkup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bp_drive_checkup)
Checkup = bp_drive_checkup.Checkup

MPH_TO_MS = 1.0 / bp_drive_checkup.MS_TO_MPH
MPH_TO_KPH = 1.0 / bp_drive_checkup.KPH_TO_MPH
WINDOW = bp_drive_checkup.PRESS_WINDOW_S


class _Cruise:
  def __init__(self, dash_mph: float) -> None:
    self.speedCluster = dash_mph * MPH_TO_MS
    self.enabled = True
    self.standstill = False


class _Button:
  def __init__(self, kind: str) -> None:
    self.type = kind
    self.pressed = True


class _CS:
  """Only the fields check 3 reads. A Mock would return a Mock for every one of them."""

  def __init__(self, dash_mph: float, max_mph: float, buttons: list[str]) -> None:
    self.vEgo = 30.0 * MPH_TO_MS
    self.cruiseState = _Cruise(dash_mph)
    self.vCruiseCluster = max_mph * MPH_TO_KPH
    self.buttonEvents = [_Button(b) for b in buttons]


class _Icbm:
  def __init__(self, baseline_mph: float) -> None:
    self.vBaseline = baseline_mph
    self.vTargetRaw = 0.0


def _press(dash0, max0, hold0, dash1, max1, hold1, kind="accelCruise"):
  """One press, resolved a full window later, scored by RUNNING check 3 and reading what it said.

  Deliberately not a re-implementation of the rule: a test that mirrors the arithmetic passes
  whether or not the shipped code matches it, which this fork has already paid for twice.
  """
  c = Checkup("test")
  c.icbm_hold(_Icbm(hold0), 0.0)                       # ignored: no carState seen yet
  c.car_state(_CS(dash0, max0, []), 0.0)
  c.buttons(_CS(dash0, max0, []), 0.0)
  c.icbm_hold(_Icbm(hold0), 0.0)                       # the hold as it stood at the press
  c.car_state(_CS(dash0, max0, [kind]), 0.1)
  c.icbm_hold(_Icbm(hold1), 0.1 + WINDOW)
  c.car_state(_CS(dash1, max1, []), 0.1 + WINDOW)
  assert len(c.presses) == 1, "the press never resolved -- the window or the fixture is wrong"

  out = io.StringIO()
  with contextlib.redirect_stdout(out):
    bp_drive_checkup.render(c, False)
  lines = [ln for ln in out.getvalue().splitlines() if "presses:" in ln]
  assert len(lines) == 1, "check 3 printed {} count lines, expected 1".format(len(lines))
  counts = lines[0].split("presses:")[1].split()
  scored = dict(zip(counts[0::2], (int(n) for n in counts[1::2])))
  assert sum(scored.values()) == 1, "one press in, {} scored out: {}".format(sum(scored.values()), scored)
  verdict_line = [ln for ln in out.getvalue().splitlines() if ln.startswith("3. +/- routing")][0]
  hit = [k for k, v in scored.items() if v == 1][0]
  headline = "BAD" if "BAD" in verdict_line else ("OK" if "OK" in verdict_line else "?")
  expected = {"dash-only": "BAD", "neither": "?"}.get(hit, "OK")
  assert headline == expected, (
    "the headline verdict disagrees with its own count line: {} / {}".format(verdict_line, scored))
  return hit


def test_creating_a_hold_is_not_the_complaint():
  # 0000046b t+..., decelCruise: dash 76 -> 75, MAX parked at 80, a hold BORN at 75.
  assert _press(76, 80, 0, 75, 80, 75, "decelCruise") == "aim"


def test_lowering_a_hold_is_not_the_complaint():
  # 0000046f, decelCruise: dash 50 -> 40 with the hold following it down.
  assert _press(50, 50, 50, 40, 45, 40, "decelCruise") == "aim"


def test_clearing_a_hold_onto_the_max_is_not_the_complaint():
  # 0000046f, accelCruise: the hold at 79 is cleared and the dash arrives on the MAX.
  assert _press(79, 80, 79, 80, 80, 0) == "aim"


def test_the_aim_can_move_while_the_dash_stands_still():
  # 0000046c, accelCruise: dash already at 75, the hold raised 70 -> 75 underneath it.
  # The old rule read this as `neither`, which is the same verdict it gives a press that did
  # nothing at all.
  assert _press(75, 80, 70, 75, 80, 75) == "aim"


def test_a_press_that_lands_inside_icbms_own_walk_is_not_the_complaint():
  # 0000046d, accelCruise: no hold either side, MAX 35, and the dash walks 30 -> 35 onto it.
  assert _press(30, 35, 0, 35, 35, 0) == "converged"


def test_a_dash_that_moves_away_from_the_aim_IS_still_the_complaint():
  # The shape the check exists for: the aim holds at 80 and the dash is dragged to 70 anyway.
  assert _press(80, 80, 0, 70, 80, 0, "decelCruise") == "dash-only"


def test_a_press_nothing_answered_still_reads_as_neither():
  assert _press(75, 80, 75, 75, 80, 75) == "neither"


def test_the_aim_is_the_hold_whenever_one_is_up():
  c = Checkup("test")
  # `buttons` is what sets `_cs_seen`, and `icbm_hold` reads nothing until it does -- a hold in the
  # opening frames must not be attributed before a carState has been seen. main() calls the two
  # together, so the fixture does too.
  c.car_state(_CS(70, 80, []), 0.0)
  c.buttons(_CS(70, 80, []), 0.0)
  assert c._aim(80.0) == 80.0, "with no hold the aim is the MAX"
  c.icbm_hold(_Icbm(62.0), 0.0)
  assert c._aim(80.0) == 62.0, "a live hold IS the aim -- that is what the box shows"
  c.icbm_hold(_Icbm(0.0), 1.0)
  assert c._aim(80.0) == 80.0, "a cleared hold hands the aim back to the MAX"


def test_a_segment_gap_does_not_carry_a_hold_across_it():
  # `segment_gap` zeroes `_prev_baseline` so a hold that ended inside a missing segment cannot be
  # attributed to the frames after it. The aim has to follow that reset, or a press on the far side
  # is scored against a hold that no longer exists.
  c = Checkup("test")
  c.car_state(_CS(70, 80, []), 0.0)
  c.buttons(_CS(70, 80, []), 0.0)
  c.icbm_hold(_Icbm(62.0), 0.0)
  c.segment_gap()
  assert c._aim(80.0) == 80.0
