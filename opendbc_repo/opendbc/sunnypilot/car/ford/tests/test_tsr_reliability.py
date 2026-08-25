"""FusionPilot: a speed limit the camera has marked OUTDATED must not reach the car.

Twelve TSR signals are subscribed in carstate_ext and, until 2026-08-24, exactly two were read --
the number and the unit. `TsrVl1StatMsgTxt_D_Rq` carries the camera's own grade of the value it is
sending, and it was parsed and discarded.

Decoded off his own routes (0x3CD, camera bus):

    000003b7   LimitOutdated on 100% of 721 frames
    000003b6   LimitOutdated 83%, and of the 165 frames carrying the value 80:
               96 LimitReliable, 62 LimitOutdated, 7 LimitChanged

WHAT THIS DOES NOT FIX, recorded so nobody re-derives it: the phantom 80 on 000003b6 was an I-80
route shield, and the camera graded it LimitReliable on 58% of its frames. This gate shortens that
event; it does not prevent it. A confident wrong read needs corroboration against another source.
"""
from types import SimpleNamespace as NS

import pytest

from opendbc.car import structs
from opendbc.car.ford.values import FordFlags
from opendbc.sunnypilot.car.ford.carstate_ext import CarStateExt

MPH_TO_MS = 0.44704
ADDR = "Traffic_RecognitnData"
NULL, CHANGED, RELIABLE, OUTDATED = 0, 1, 2, 3


class FakeCam:
  def __init__(self, limit, status, unit=2):
    self.vl = {ADDR: {"TsrVLim1MsgTxt_D_Rq": limit,
                      "TsrVlUnitMsgTxt_D_Rq": unit,
                      "TsrVl1StatMsgTxt_D_Rq": status}}


def _ext():
  ext = CarStateExt(NS(flags=FordFlags.TSR, carFingerprint="FORD_FUSION_MK5"), NS())
  return ext


def _read(limit, status, unit=2):
  return CarStateExt.update_traffic_signals(_ext(), FakeCam(limit, status, unit))


def test_a_reliable_limit_still_gets_through():
  """The gate must not break the working case."""
  assert _read(35, RELIABLE) == pytest.approx(35 * MPH_TO_MS)


def test_a_changed_limit_gets_through():
  """LimitChanged is a fresh read of a new sign, not a stale one."""
  assert _read(45, CHANGED) == pytest.approx(45 * MPH_TO_MS)


def test_an_outdated_limit_is_refused():
  """THE POINT. 100% of route 000003b7's frames were graded this way."""
  assert _read(80, OUTDATED) == 0


def test_a_null_grade_is_refused():
  """Null is the power-on state, not a reading."""
  assert _read(30, NULL) == 0


def test_the_grade_is_checked_before_the_value():
  """An outdated read must be refused whatever the number is, including plausible ones."""
  for limit in (25, 30, 45, 65, 80):
    assert _read(limit, OUTDATED) == 0, f"outdated {limit} was accepted"


def test_no_limit_is_still_no_limit_when_reliable():
  """255 is the camera's 'nothing to report'; a reliable grade must not turn it into a speed."""
  assert _read(255, RELIABLE) == 0
  assert _read(0, RELIABLE) == 0


def test_kph_still_converts():
  """The unit path is untouched by the gate."""
  assert _read(50, RELIABLE, unit=1) == pytest.approx(50 * 0.277778, rel=1e-3)


def test_a_car_without_tsr_reads_nothing():
  """The flag gate above the whole function must still short-circuit."""
  ext = CarStateExt(NS(flags=0, carFingerprint="FORD_FUSION_MK5"), NS())
  assert CarStateExt.update_traffic_signals(ext, FakeCam(35, RELIABLE)) == 0


def test_the_reported_80_would_have_been_shortened_not_stopped():
  """Honest about the limit of this fix, as an executable statement rather than a comment.

  96 of the 80-frames were graded LimitReliable, so they still pass. 69 were not, and are now
  refused. If a future change makes this test fail by rejecting the reliable ones too, that is a
  different (and stronger) fix -- update this test deliberately rather than deleting it.
  """
  assert _read(80, RELIABLE) == pytest.approx(80 * MPH_TO_MS), "still accepted, as measured"
  assert _read(80, OUTDATED) == 0
  assert _read(80, CHANGED) == pytest.approx(80 * MPH_TO_MS)
