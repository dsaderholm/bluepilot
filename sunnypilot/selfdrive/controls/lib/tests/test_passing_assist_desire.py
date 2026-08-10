"""
FusionPilot: passing assist's lane-change request as desire_helper reads it.

This is the piece that makes the car actually move, so the tests are about the two ways it could
move WRONGLY rather than about whether it moves at all.
"""

from types import SimpleNamespace

from openpilot.sunnypilot.selfdrive.controls.lib.passing_assist_desire import (
  request_side, NONE, LEFT, RIGHT)


def plan(actuating=True, blinker=True, side=LEFT):
  pa = SimpleNamespace(actuating=actuating, blinkerWouldBeOn=blinker, maneuverSide=side)
  return SimpleNamespace(passingAssist=pa)


class FakeEnum:
  """What a capnp enum read off a LIVE message behaves like: int() raises, .raw is the integer."""

  def __init__(self, raw):
    self.raw = raw

  def __int__(self):
    raise TypeError("int() argument must be a string or a number, not '_DynamicEnum'")


class TestItCannotMoveWhenItShouldNotBeMoving:
  """The dry run has published blinkerWouldBeOn and maneuverSide on every drive since the feature
  was written. Reading those without `actuating` would have driven lane changes on his commute."""

  def test_the_dry_run_cannot_ask_for_a_lane_change(self):
    assert request_side(plan(actuating=False)) == NONE

  def test_no_request_between_maneuvers(self):
    """`actuating` is a permission, not a request -- true whenever the hardware allows it."""
    assert request_side(plan(blinker=False)) == NONE

  def test_a_missing_planner_asks_for_nothing(self):
    """This runs in modeld's hot loop. An absent or malformed planner must not reach it."""
    assert request_side(None) == NONE
    assert request_side(SimpleNamespace()) == NONE

  def test_an_unknown_side_is_not_a_request(self):
    """Side.none is 0, but a value outside left/right must not be read as permission either."""
    for bad in (0, 3, 7, -1):
      assert request_side(plan(side=bad)) == NONE


class TestTheSideItAsksFor:
  """The one that would put the car on the shoulder.

  DesireHelper.get_lane_change_direction is "left if CS.leftBlinker else RIGHT" -- right by
  default, because a stalk press is normally the only way in and one of the two is always set. Our
  commanded signal is invisible to carState, so a LEFT request that did not also fix the direction
  would arm the machine and steer RIGHT, into whatever is there.
  """

  def test_left_is_left(self):
    assert request_side(plan(side=LEFT)) == LEFT

  def test_right_is_right(self):
    assert request_side(plan(side=RIGHT)) == RIGHT


class TestItSurvivesALiveCapnpMessage:
  """int() on a capnp enum raises TypeError on the device and CANNOT fail offline, because every
  fixture here builds messages from plain ints. The broad except would have turned that into a
  request that silently never arrived -- which is exactly what happened to hud_ext.py's cluster
  display, unnoticed for its whole life."""

  def test_a_dynamic_enum_side_is_read_rather_than_swallowed(self):
    assert request_side(plan(side=FakeEnum(LEFT))) == LEFT
    assert request_side(plan(side=FakeEnum(RIGHT))) == RIGHT

  def test_and_a_dynamic_enum_none_is_still_none(self):
    assert request_side(plan(side=FakeEnum(0))) == NONE
