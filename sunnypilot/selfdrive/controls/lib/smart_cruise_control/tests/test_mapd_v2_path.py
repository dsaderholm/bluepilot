"""FusionPilot: mapd v2's path arrives in the shape SCC-Map already walks, or not at all.

The curve half of the migration is a translation, and its whole risk is in the boundary: when does
SCC-Map read v2, and when does it fall back to v1? Getting that wrong silently is easy -- returning
an empty list instead of None would leave the controller correctly idle in a way that is
indistinguishable from v2 being absent, and nobody would notice until a corner was not taken.
"""
import pytest

from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.mapd_v2_path import path_from_mapd


class FakePoint:
  def __init__(self, lat, lon, target_velocity, curvature=0.0):
    self.latitude = lat
    self.longitude = lon
    self.targetVelocity = target_velocity
    self.curvature = curvature


class FakePosition:
  def __init__(self, lat, lon):
    self.latitude = lat
    self.longitude = lon


class FakeExtended:
  def __init__(self, points, position):
    self.path = points
    self.position = position


class FakeSM:
  def __init__(self, alive=True, valid=True, points=None, position=(40.75, -111.9)):
    self.alive = {"mapdExtendedOut": alive}
    self.valid = {"mapdExtendedOut": valid}
    pts = [FakePoint(*p) for p in (points if points is not None else [(40.76, -111.9, 20.1)])]
    self._data = {"mapdExtendedOut": FakeExtended(pts, FakePosition(*position))}

  def __getitem__(self, name):
    return self._data[name]


def test_points_become_the_dicts_the_walk_indexes_by_name():
  """SCC-Map indexes latitude/longitude/velocity by NAME. mapd calls the third one targetVelocity."""
  sm = FakeSM(points=[(40.76, -111.90, 20.1), (40.77, -111.91, 17.9)])
  position, targets = path_from_mapd(sm)

  assert position.latitude == pytest.approx(40.75)
  assert position.longitude == pytest.approx(-111.9)
  assert targets == [
    {"latitude": pytest.approx(40.76), "longitude": pytest.approx(-111.90), "velocity": pytest.approx(20.1)},
    {"latitude": pytest.approx(40.77), "longitude": pytest.approx(-111.91), "velocity": pytest.approx(17.9)},
  ]


def test_none_when_mapd_is_not_publishing():
  """None, not an empty list. The caller uses None to decide to read v1 instead."""
  assert path_from_mapd(FakeSM(alive=False)) is None
  assert path_from_mapd(FakeSM(valid=False)) is None


def test_none_when_the_path_is_empty():
  assert path_from_mapd(FakeSM(points=[])) is None


def test_none_when_mapd_has_no_position():
  """Null island. Walking a path from 0,0 puts every corner thousands of km away, so SCC-Map would
  quietly find nothing to slow for rather than falling back to a source that works."""
  assert path_from_mapd(FakeSM(position=(0.0, 0.0))) is None


def test_points_with_no_target_velocity_are_dropped():
  """A point mapd publishes with targetVelocity 0 is not a corner to slow to 0 mph for.

  The walk compares `tv > self.v_ego` to skip points faster than the car; a zero would pass that
  test at any speed and then be treated as a corner requiring a stop.
  """
  sm = FakeSM(points=[(40.76, -111.90, 0.0), (40.77, -111.91, 17.9)])
  _, targets = path_from_mapd(sm)
  assert len(targets) == 1
  assert targets[0]["velocity"] == pytest.approx(17.9)


def test_a_straight_road_with_no_velocities_is_an_ANSWER_not_a_fallback():
  """Measured on route 00000383: of 46 frames where no point carried a velocity, all 46 had no
  curvature either. That is "no corners ahead", and returning None sent SCC-Map to v1 for a question
  v2 had already answered -- 8 of the 9 percentage points of fallback on that drive."""
  position, targets = path_from_mapd(FakeSM(points=[(40.76, -111.90, 0.0), (40.77, -111.91, 0.0)]))
  assert targets == []
  assert position.latitude == pytest.approx(40.75)


def test_but_curvature_with_no_velocity_DOES_fall_back():
  """mapd derives velocity from curvature alone, so a bend with no velocity means it could not
  compute rather than that there was nothing to compute. That is worth v1. It happened zero times
  on the measured drive, and this stays because zero is a measurement, not a guarantee."""
  sm = FakeSM(points=[(40.76, -111.90, 0.0, 0.01)])   # 100 m radius, no velocity
  assert path_from_mapd(sm) is None


def test_the_controller_actually_walks_the_v2_path():
  """The WIRING, not the translation. Pure-logic tests cannot catch a value that is computed
  correctly and then not used -- that is the category that took the car off the road on 2026-08-15,
  where every test passed against a CarController attribute nothing ever set.

  Drives the real SmartCruiseControlMap and asserts the list it ends up walking is the one handed
  in, not the one it would have read from /dev/shm/params.
  """
  from openpilot.sunnypilot.navd.helpers import Coordinate
  from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.map_controller import SmartCruiseControlMap

  controller = SmartCruiseControlMap()
  handed_in = [{"latitude": 40.76, "longitude": -111.90, "velocity": 17.9}]
  controller.update(long_enabled=True, long_override=False, v_ego=30.0, a_ego=0.0, v_cruise=31.0,
                    mapd_v2_path=(Coordinate(40.75, -111.90), handed_in))

  assert controller.target_velocities == handed_in, (
    "SCC-Map did not walk the path it was given -- it fell back to v1's params. The translation "
    "being right is worth nothing if the controller never reads it.")
  assert controller.last_position.latitude == pytest.approx(40.75)


def test_the_controller_still_reads_v1_when_handed_nothing():
  """The other half: passing None must leave the v1 path exactly as it was."""
  from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.map_controller import SmartCruiseControlMap

  controller = SmartCruiseControlMap()
  controller.update(long_enabled=True, long_override=False, v_ego=30.0, a_ego=0.0, v_cruise=31.0,
                    mapd_v2_path=None)
  # The stubbed params hold no MapTargetVelocities, so v1's answer here is the empty list -- the
  # point is that it went and asked, rather than carrying a stale v2 path forward.
  assert controller.target_velocities == []


def test_the_dicts_do_not_alias_capnp_readers():
  """Held across frames, so they must be plain values.

  A capnp reader is a view onto the message buffer; SubMaster replaces that buffer on the next
  frame, and SCC-Map keeps self.target_velocities between updates. Storing readers would mean the
  path silently becoming whatever arrived later, or a segfault-class error in pycapnp.
  """
  sm = FakeSM(points=[(40.76, -111.90, 20.1)])
  _, targets = path_from_mapd(sm)
  assert all(isinstance(v, float) for v in targets[0].values())
