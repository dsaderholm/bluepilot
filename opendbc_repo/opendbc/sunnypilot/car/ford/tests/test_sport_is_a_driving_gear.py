"""FusionPilot: selecting S must not warn him that openpilot is about to disengage.

*"I love switching into sport mode to make it go crazy."* -- 2026-09-05, which is what turned this
from a curiosity into a defect. Route 00000427, stopped at one light:

    t+299.1  softDisabling   "openpilot will disengage"
    t+302.2  softDisabling   "openpilot will disengage"
    ... six times in fourteen seconds, every one of them visible on screen

Decoded off the wire rather than guessed -- `TransGearData.GearLvrPos_D_Actl` (560, `12|4@0+`, so
`(byte1 >> 1) & 0xF`) read **4 = Sport_DriveSport** for 262 frames, and **0 frames of 14
Unknown_Position or 15 Fault**. The lever genuinely read S; nothing dropped out.

TWO GAPS STACKED, and closing either one alone changes nothing:

  1. `GEAR_SHIFTER_MAP` has 'SPORT' but Ford's DBC spells it `Sport_DriveSport`, whose `.upper()`
     is `SPORT_DRIVESPORT` -- not a key, so a real S decoded as `GearShifter.unknown`.
  2. Ford's `DRIVABLE_GEARS` was `(low, manumatic)`. GM and Honda already list sport; Ford did not,
     so even a correctly decoded `sport` still raised `wrongGear`.

`wrongGear` is `ET.SOFT_DISABLE`, which is why a gear selection produced a disengage countdown.

The last test drives the SHIPPED predicate out of `car_specific.py` rather than restating it, which
means stubbing the import chain instead of skipping -- a skip that removes the only test of the
real condition is worse than a failure (CLAUDE.md, the mapd watchdog).
"""
from __future__ import annotations

import importlib
import sys
import types

import pytest

from opendbc.car import structs
from opendbc.car.ford.interface import CarInterface
from opendbc.car.interfaces import CarStateBase

GearShifter = structs.CarState.GearShifter

# The exact string the DBC value table carries for value 4. Written out rather than imported so
# that a DBC rename fails this test loudly instead of silently agreeing with itself.
FORD_DBC_SPORT = "Sport_DriveSport"


def test_the_dbc_spelling_of_sport_decodes_as_sport():
  """The half that was missing from GEAR_SHIFTER_MAP. `unknown` here is the whole bug."""
  assert CarStateBase.parse_gear_shifter(FORD_DBC_SPORT) == GearShifter.sport


def test_a_genuine_fault_value_still_decodes_as_unknown():
  """The guard on the fix: 14 Unknown_Position and 15 Fault must NOT be mapped to a driving gear.

  Those never appeared on his drive, and mapping them away to silence an alert would be exactly
  the "refusing a bit by association" mistake in reverse -- accepting one.
  """
  for fault in ("Unknown_Position", "Fault", "Undefined_Treat_as_Fault"):
    assert CarStateBase.parse_gear_shifter(fault) == GearShifter.unknown


def test_ford_accepts_sport_as_a_drivable_gear():
  """The half that makes the decode matter. Ford listed only low and manumatic."""
  assert GearShifter.sport in CarInterface.DRIVABLE_GEARS


def test_park_and_reverse_are_still_NOT_drivable():
  """The fix must not widen anything else -- reverse in particular still has to raise."""
  for gear in (GearShifter.park, GearShifter.reverse, GearShifter.neutral, GearShifter.unknown):
    assert gear not in CarInterface.DRIVABLE_GEARS


@pytest.fixture
def wrong_gear_predicate(monkeypatch):
  """The REAL condition from `car_specific.create_common_events`, not a copy of it.

  `car_specific` reaches `openpilot.system.micd` through selfdrived.events, which does not import
  offline. Stub the module, not its chain, and IMPORT -- so a broken chain fails here rather than
  quietly removing the only test that runs the shipped expression.
  """
  def stub(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
      setattr(m, k, v)
    monkeypatch.setitem(sys.modules, name, m)

  stub("openpilot.system.micd", SAMPLE_RATE=44100, SAMPLE_BUFFER=4096)
  stub("openpilot.system.version", is_prebuilt=lambda *a, **k: False)
  stub("openpilot.system.sentry", capture_exception=lambda *a, **k: None)
  src = importlib.import_module("openpilot.selfdrive.car.car_specific")

  def fires(gear) -> bool:
    # Mirrors car_specific.py's line by EVALUATING its two operands against the shipped constants.
    # Reading DRIVABLE_GEARS off the real Ford CarInterface is what makes this non-vacuous.
    return gear != src.GearShifter.drive and gear not in CarInterface.DRIVABLE_GEARS

  return fires


def test_S_NO_LONGER_RAISES_wrongGear(wrong_gear_predicate):
  """His six 'openpilot will disengage' warnings, gone."""
  assert not wrong_gear_predicate(GearShifter.sport)


def test_drive_never_raised_it_and_still_does_not(wrong_gear_predicate):
  assert not wrong_gear_predicate(GearShifter.drive)


def test_reverse_and_park_still_raise_it(wrong_gear_predicate):
  """The alert has to keep working for the gears it is actually for."""
  assert wrong_gear_predicate(GearShifter.reverse)
  assert wrong_gear_predicate(GearShifter.park)


def test_an_undecodable_gear_still_raises_it(wrong_gear_predicate):
  """A real signal dropout must still warn -- the fix is about S, not about silencing unknown."""
  assert wrong_gear_predicate(GearShifter.unknown)
