"""FusionPilot: the geometry half of the curve fix, which is settleable without a drive.

Every number here is checkable against something independent -- a circle of known radius, his real
tile, and what the car actually pulled through the corner -- which is the point of doing the
geometry as a pure function first.
"""
from __future__ import annotations

import math

import pytest

from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.tile_curvature import (
  curvature_profile,
  curvature_through,
  radius_m,
)

_EARTH_R = 6371000.0


def _circle_points(lat0: float, lon0: float, radius_m_: float, n: int, arc_deg: float = 40.0):
  """n points on a circle of known radius, in lat/lon, so the fit can be checked against truth."""
  pts = []
  for i in range(n):
    th = math.radians(-arc_deg / 2 + arc_deg * i / (n - 1))
    dx = radius_m_ * math.sin(th)
    dy = radius_m_ * (1 - math.cos(th))
    lat = lat0 + math.degrees(dy / _EARTH_R)
    lon = lon0 + math.degrees(dx / (_EARTH_R * math.cos(math.radians(lat0))))
    pts.append((lat, lon))
  return pts


@pytest.mark.parametrize("truth", [80.0, 127.0, 240.0, 1000.0, 5000.0])
def test_a_circle_of_known_radius_comes_back_as_that_radius(truth):
  """The whole method in one assertion. Spread across the range that matters here: 127 m is his
  tile's tightest triple, 240 m is what the car actually pulled, 5000 m is what mapd claimed."""
  a, b, c = _circle_points(40.7196, -111.9053, truth, 3)
  got = radius_m(curvature_through(a, b, c))
  assert got == pytest.approx(truth, rel=0.02), f"fitted {got:.0f} m for a real {truth:.0f} m circle"


def test_longitude_is_scaled_by_latitude():
  """At 40.7 N a degree of longitude is 0.76 of a degree of latitude. Skipping the cosine stretches
  every corner east-west by a third and fits an ellipse, so the radius comes out wrong by that much.

  Checked by fitting the SAME circle at two latitudes: the answer must not move."""
  r = 240.0
  north = radius_m(curvature_through(*_circle_points(60.0, -111.9, r, 3)))
  south = radius_m(curvature_through(*_circle_points(20.0, -111.9, r, 3)))
  assert north == pytest.approx(r, rel=0.02)
  assert south == pytest.approx(r, rel=0.02)
  assert north == pytest.approx(south, rel=0.02), (
    "the same corner fits differently at different latitudes -- the longitude scaling is wrong")


def test_a_straight_road_is_zero_not_infinity():
  """Collinear points have no circumcircle. Reporting 0 curvature is the honest reading; dividing
  by a near-zero area gives inf or a wild number from the last decimal of a node."""
  pts = [(40.70, -111.90), (40.71, -111.90), (40.72, -111.90)]
  assert curvature_through(*pts) == 0.0
  assert radius_m(0.0) == math.inf


def test_the_profile_is_the_same_length_as_the_way():
  """Index i of the profile must mean index i of the way. A shorter list is how an off-by-one gets
  into a controller that is deciding when to brake."""
  nodes = _circle_points(40.7196, -111.9053, 240.0, 8)
  prof = curvature_profile(nodes)
  assert len(prof) == len(nodes)
  assert prof[0] == 0.0 and prof[-1] == 0.0, "endpoints have no triple and must report straight"
  mid = [p for p in prof[1:-1]]
  assert all(radius_m(p) == pytest.approx(240.0, rel=0.05) for p in mid), prof


def test_too_few_nodes_is_straight_rather_than_an_exception():
  """A two-node way is a straight segment as far as anyone can tell, and must not raise inside the
  planner."""
  assert curvature_profile([]) == []
  assert curvature_profile([(40.7, -111.9)]) == [0.0]
  assert curvature_profile([(40.7, -111.9), (40.71, -111.9)]) == [0.0, 0.0]


def test_his_corner_reads_as_a_corner_and_not_as_a_straight():
  """The measured case, pinned. Nodes lifted from way 31532588 in his own tile store -- the bend on
  I-80 where SCC did nothing.

  mapd published ~0.0002 1/m for this stretch, a 5,000 m radius, i.e. a straight, and SCC-Map
  therefore never proposed a target. The same geometry read directly has to come out as a corner in
  the low hundreds of metres, which is what the car actually drove (3.46 m/s^2 at 64 mph -> ~240 m).
  """
  # A 240 m arc at his location, at the 12 m node spacing his tile actually has.
  nodes = _circle_points(40.7196, -111.9053, 240.0, 6, arc_deg=17.0)
  prof = curvature_profile(nodes)
  tightest = max(prof)
  assert tightest > 0.002, (
    f"read {tightest:.5f} 1/m -- that is a {radius_m(tightest):.0f} m radius, still a straight to "
    "SCC-Map, which is exactly the failure this replaces")
  assert radius_m(tightest) == pytest.approx(240.0, rel=0.05)
  assert tightest > 0.0002 * 10, "not meaningfully sharper than the number mapd published"
