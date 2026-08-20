"""FusionPilot: mapd v2's path arrives in the shape SCC-Map already walks, or not at all.

The curve half of the migration is a translation, and its whole risk is in the boundary: when does
SCC-Map read v2, and when does it fall back to v1? Getting that wrong silently is easy -- returning
an empty list instead of None would leave the controller correctly idle in a way that is
indistinguishable from v2 being absent, and nobody would notice until a corner was not taken.
"""
import math

import pytest

from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.mapd_v2_path import (
  _CORNER_LAT_ACC,
)
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


def test_the_corner_speed_is_ours_and_comes_from_curvature():
  """THE SOURCE SWAP, 2026-08-19. mapd's targetVelocity is exactly sqrt(2.2 / k) -- 2.2 being
  `map_curve_target_lat_a`, a constant belonging to somebody else's car. We derive it ourselves at
  the lateral acceleration his PSCM was MEASURED to hold, and mapd's own velocity is now ignored
  entirely (note the deliberately absurd values in the fixture).

  2.5 comes from tools/bp_pscm_lateral_limit.py: openpilot alone, hands off, p99 2.73 and max 3.19,
  with the deviation limiter quiet below 2.5 and biting 27.4% of frames by 3.0-3.5.
  """
  sm = FakeSM(points=[(40.76, -111.90, 999.0, 0.01), (40.77, -111.91, 999.0, 0.0025)])
  position, targets = path_from_mapd(sm)

  assert position.latitude == pytest.approx(40.75)
  assert targets == [
    {"latitude": pytest.approx(40.76), "longitude": pytest.approx(-111.90),
     "velocity": pytest.approx(math.sqrt(_CORNER_LAT_ACC / 0.01))},      # 100 m radius -> 15.8 m/s
    {"latitude": pytest.approx(40.77), "longitude": pytest.approx(-111.91),
     "velocity": pytest.approx(math.sqrt(_CORNER_LAT_ACC / 0.0025))},    # 400 m radius -> 31.6 m/s
  ]


def test_it_plans_corners_faster_than_mapd_did_and_by_the_expected_ratio():
  """Wiring 2.5 where mapd used 2.2 RAISES corner speeds by sqrt(_CORNER_LAT_ACC/2.2) = 6.6%. Pinned because it
  is the opposite direction from "low speed curves don't slow enough" and must stay a deliberate,
  visible consequence rather than a surprise -- that complaint was measured to be map COVERAGE
  (SCC-Map active 146 frames in 26 minutes), not this constant."""
  k = 0.004
  _, targets = path_from_mapd(FakeSM(points=[(40.76, -111.90, 0.0, k)]))
  mapd_would_have = math.sqrt(2.2 / k)
  assert targets[0]["velocity"] == pytest.approx(mapd_would_have * math.sqrt(_CORNER_LAT_ACC / 2.2))


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


def test_a_point_with_no_curvature_yields_no_target():
  """REPLACES "points with no targetVelocity are dropped" -- mapd's velocity is no longer read, so
  a zero there means nothing. What must still be dropped is a STRAIGHT point: the walk compares
  `tv > self.v_ego` to skip points faster than the car, and a zero velocity would pass that at any
  speed and be treated as a corner requiring a stop."""
  sm = FakeSM(points=[(40.76, -111.90, 0.0, 0.0), (40.77, -111.91, 0.0, 0.01)])
  _, targets = path_from_mapd(sm)
  assert len(targets) == 1
  assert targets[0]["velocity"] == pytest.approx(math.sqrt(_CORNER_LAT_ACC / 0.01))
  assert all(t["velocity"] > 0 for t in targets), "a zero corner speed would be walked as a stop"


def test_a_straight_road_with_no_velocities_is_an_ANSWER_not_a_fallback():
  """Measured on route 00000383: of 46 frames where no point carried a velocity, all 46 had no
  curvature either. That is "no corners ahead", and returning None sent SCC-Map to v1 for a question
  v2 had already answered -- 8 of the 9 percentage points of fallback on that drive."""
  position, targets = path_from_mapd(FakeSM(points=[(40.76, -111.90, 0.0), (40.77, -111.91, 0.0)]))
  assert targets == []
  assert position.latitude == pytest.approx(40.75)


def test_curvature_with_no_velocity_is_now_a_CORNER_not_a_fallback():
  """THIS FALLBACK IS GONE ON PURPOSE, and it is the one behavioural removal in the source swap.

  It existed because mapd sometimes published a bend it could not price -- curvature present,
  velocity absent -- which meant "mapd failed here", and v1 was worth asking. We compute the price
  ourselves now, so that state is no longer a failure: it is simply a corner, and it gets a speed.

  Measured as happening zero times on route 00000383, so this removes a path that never ran; it is
  recorded because a fallback disappearing is exactly the kind of change that is invisible until the
  day it would have fired."""
  sm = FakeSM(points=[(40.76, -111.90, 0.0, 0.01)])   # 100 m radius, mapd priced nothing
  result = path_from_mapd(sm)
  assert result is not None, "a bend mapd could not price must now be priced by us, not sent to v1"
  _, targets = result
  assert targets[0]["velocity"] == pytest.approx(math.sqrt(_CORNER_LAT_ACC / 0.01))


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
  sm = FakeSM(points=[(40.76, -111.90, 20.1, 0.01)])
  _, targets = path_from_mapd(sm)
  assert all(isinstance(v, float) for v in targets[0].values())


# --- NaN, which mapd really does emit ------------------------------------------------------------

NAN = float("nan")


def test_a_nan_curvature_falls_back_to_v1_rather_than_reading_as_straight():
  """THE BUG. `NaN > _STRAIGHT_CURVATURE` is False, so a path mapd could not compute fell through to
  `return ..., []` -- "straight road, no corners ahead", the most confident answer this function can
  give, produced by the one input that means the opposite.

  SCC-Map then idles and never consults v1, which is precisely the fallback that branch exists to
  reach. Confirmed that mapd emits NaN here on route 0000038e, 2026-08-18: reading the path crashed
  a diagnostic with `cannot convert float NaN to integer`.

  Same shape as several bugs found today: a comparison that answers False on a value meaning
  "unknown" reads as a clean negative."""
  sm = FakeSM(points=[(40.76, -111.9, 0.0, NAN), (40.77, -111.9, 0.0, NAN)])
  assert path_from_mapd(sm) is None, (
    "a path whose curvature mapd could not compute reported STRAIGHT ROAD -- SCC-Map idles and "
    "never falls back to v1, so the corner is not taken and nothing says why")


def test_a_nan_curvature_among_good_points_is_dropped_rather_than_priced():
  """REPLACES "a NaN velocity is dropped" -- mapd's velocity is no longer read at all, so a NaN
  there cannot reach anything. The equivalent hazard is now a NaN CURVATURE on one point of an
  otherwise good path: `sqrt(2.5 / nan)` is NaN, and a NaN velocity in the walk poisons `min()`
  over the corner speeds and produces a set-speed request nobody can act on.

  The all-NaN case still returns None and falls back to v1 -- that is the test above. This is the
  MIXED case, which must neither fall back nor emit a NaN."""
  sm = FakeSM(points=[(40.76, -111.9, 0.0, NAN), (40.77, -111.9, 0.0, 0.01)])
  result = path_from_mapd(sm)
  assert result is not None, "one bad point must not cost the whole path"
  _, targets = result
  assert len(targets) == 1, f"the NaN-curvature point was priced: {targets}"
  assert targets[0]["velocity"] == pytest.approx(math.sqrt(_CORNER_LAT_ACC / 0.01))
  assert not any(math.isnan(t["velocity"]) for t in targets)


def test_a_real_straight_road_still_answers_straight():
  """The other direction, so the NaN guard cannot be satisfied by refusing everything: genuine zero
  curvature with no velocities is a real answer meaning no corners ahead, and must NOT fall back."""
  sm = FakeSM(points=[(40.76, -111.9, 0.0, 0.0), (40.77, -111.9, 0.0, 0.0)])
  result = path_from_mapd(sm)
  assert result is not None, "a genuinely straight road fell back to v1"
  assert result[1] == []


# --- our own curvature, from the path's coordinates ----------------------------------------------

def _arc(lat0, lon0, radius_m, n, spacing_m):
  """Points on a real circle of known radius, as (lat, lon)."""
  out, step = [], spacing_m / radius_m
  mlat, mlon = 111320.0, 111320.0 * math.cos(math.radians(lat0))
  for i in range(n):
    t = i * step
    out.append((lat0 + radius_m * math.sin(t) / mlat,
                lon0 + radius_m * (1 - math.cos(t)) / mlon))
  return out


def test_a_bend_mapd_flattened_is_still_priced_as_a_bend():
  """THE WHOLE POINT OF THE CURVATURE SWAP. On his I-80 corner mapd published ~5,000 m where the
  tile geometry and the car both say ~250 m. Here mapd claims a near-straight on coordinates that
  describe a real 240 m bend, and the corner speed must come from the GEOMETRY, not the claim."""
  pts = [(lat, lon, 0.0, 2e-5) for lat, lon in _arc(40.76, -111.9, 240.0, 40, 12.0)]
  _, targets = path_from_mapd(FakeSM(points=pts))
  assert targets, "a real 240 m bend produced no corner at all"
  slowest = min(t["velocity"] for t in targets)
  # sqrt(2.5/240) = 0.102 rad/s -> 15.6 m/s. mapd's 2e-5 would have priced it at 354 m/s.
  assert slowest == pytest.approx(math.sqrt(_CORNER_LAT_ACC / (1 / 240.0)), rel=0.15), \
    f"priced at {slowest:.1f} m/s -- mapd's flattened curvature won"


def test_mapd_still_wins_where_it_sees_a_tighter_corner_than_we_resolve():
  """Not a replacement -- whichever is TIGHTER. A corner at a scale no rung of the ladder clears
  must not be lost just because we could not measure it ourselves."""
  pts = [(40.76 + i * 1e-5, -111.9, 0.0, 0.02) for i in range(6)]   # straight coords, mapd says 50 m
  _, targets = path_from_mapd(FakeSM(points=pts))
  assert targets, "mapd's own corner was discarded"
  assert min(t["velocity"] for t in targets) == pytest.approx(math.sqrt(_CORNER_LAT_ACC / 0.02))


def test_a_genuinely_straight_road_is_still_straight_with_both_sources():
  """Neither source may invent a corner on a straight road -- the failure that would make every
  highway mile slow down."""
  pts = [(40.76 + i * 1e-4, -111.9, 0.0, 0.0) for i in range(40)]
  position, targets = path_from_mapd(FakeSM(points=pts))
  assert targets == [], f"a straight road produced {len(targets)} corners"
  assert position is not None
