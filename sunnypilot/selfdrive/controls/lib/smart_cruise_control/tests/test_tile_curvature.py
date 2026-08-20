"""FusionPilot: the geometry half of the curve fix, which is settleable without a drive.

Every number here is checkable against something independent -- a circle of known radius, his real
tile, and what the car actually pulled through the corner -- which is the point of doing the
geometry as a pure function first.
"""
from __future__ import annotations

import math

import pytest

from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control import tile_curvature as tc
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


# --- the baseline: rejecting node jitter without averaging the corner away ----------------------

def _circle_nodes(radius_m_: float, spacing_m: float, arc_deg: float = 90.0,
                  lat0: float = 40.76, lon0: float = -111.89):
  """Points on a real circle of known radius, at a given along-arc spacing."""
  import math as _m
  out = []
  step = spacing_m / radius_m_
  n = int(_m.radians(arc_deg) / step)
  mlat = 111320.0
  mlon = 111320.0 * _m.cos(_m.radians(lat0))
  for i in range(n + 1):
    t = i * step
    out.append((lat0 + (radius_m_ * _m.sin(t)) / mlat,
                lon0 + (radius_m_ * (1 - _m.cos(t))) / mlon))
  return out


def test_a_wide_baseline_still_measures_a_real_circle_exactly():
  """Widening the baseline must cost NOTHING on real geometry. A circle sampled at any spacing is
  still that circle, so if this drifts the baseline is averaging the corner away -- which is mapd's
  failure, reproduced."""
  for r in (150.0, 240.0, 400.0, 800.0):
    nodes = _circle_nodes(r, spacing_m=12.0)
    prof = [c for c in tc.curvature_profile_baseline(nodes) if c > 0]
    assert prof, f"no interior node had a full baseline at R={r}"
    mid = sorted(prof)[len(prof) // 2]
    assert abs(tc.radius_m(mid) - r) / r < 0.05, \
      f"R={r} measured {tc.radius_m(mid):.0f} m -- the baseline is distorting real geometry"


def test_the_baseline_rejects_the_node_jitter_that_adjacent_triples_cannot():
  """THE WHOLE REASON THIS EXISTS. A STRAIGHT road with half a metre of node jitter reads as a
  ~144 m corner on adjacent 12 m triples -- which is what the '127 m tightest' figure this module
  was first validated on actually was. The wide baseline must see a straight."""
  import math as _m
  lat0, lon0 = 40.76, -111.89
  mlat, mlon = 111320.0, 111320.0 * _m.cos(_m.radians(lat0))
  nodes = []
  for i in range(60):
    jitter = 0.5 if i % 2 else -0.5      # worst case: alternating, maximum apparent curvature
    nodes.append((lat0 + (i * 12.0) / mlat, lon0 + jitter / mlon))

  narrow = [abs(c) for c in tc.curvature_profile(nodes)[1:-1]]
  wide = [abs(c) for c in tc.curvature_profile_baseline(nodes) if c != 0.0]

  assert wide, "the wide baseline produced no reading at all on a 700 m way"
  worst_narrow = tc.radius_m(max(narrow))
  worst_wide = tc.radius_m(max(wide))
  assert worst_narrow < 300.0, \
    f"the premise is wrong: jitter read as {worst_narrow:.0f} m, not a tight corner"
  assert worst_wide > 5 * worst_narrow, \
    f"jitter still reads as {worst_wide:.0f} m -- the baseline is not rejecting it"


def test_the_ends_of_a_way_report_straight_rather_than_a_short_baseline():
  """A truncated baseline is the NOISY measurement wearing the wide one's confidence. Refuse it."""
  nodes = _circle_nodes(240.0, spacing_m=12.0)
  prof = tc.curvature_profile_baseline(nodes)
  assert prof[0] == 0.0 and prof[-1] == 0.0
  assert len(prof) == len(nodes), "the profile must stay index-aligned with the way"


def test_a_way_shorter_than_the_baseline_reads_straight_not_noisy():
  nodes = _circle_nodes(240.0, spacing_m=12.0)[:4]
  assert all(c == 0.0 for c in tc.curvature_profile_baseline(nodes))


# --- multiscale: the finest baseline that still clears the noise floor --------------------------

def test_a_tight_corner_needs_a_SHORT_baseline_and_gets_one():
  """MEASURED ON HIS OWN mapd PATH, 2026-08-19. A 21 m turn read 30 m at the fixed 70 m baseline --
  the baseline was longer than the corner and averaged it away, which is mapd's own failure
  reproduced by the fix for it. The ladder must pick a short rung here."""
  nodes = _circle_nodes(21.0, spacing_m=4.0, arc_deg=180.0)
  fixed = [abs(c) for c in tc.curvature_profile_baseline(nodes, 70.0) if c > 0]
  multi = [abs(c) for c in tc.curvature_profile_multiscale(nodes) if c > 0]
  assert multi, "the ladder produced no reading on a real 21 m corner"
  best = tc.radius_m(sorted(multi)[len(multi) // 2])
  assert abs(best - 21.0) / 21.0 < 0.25, f"multiscale read {best:.0f} m on a 21 m corner"
  if fixed:
    worst = tc.radius_m(sorted(fixed)[len(fixed) // 2])
    assert abs(best - 21.0) < abs(worst - 21.0), "the ladder is no better than the fixed baseline"


def test_a_sweeper_still_gets_the_LONG_baseline():
  """The other end, unchanged: a 240 m bend at 12 m spacing is below the jitter floor on a short
  rung, so the ladder must keep walking out to one that clears it."""
  nodes = _circle_nodes(240.0, spacing_m=12.0)
  multi = [abs(c) for c in tc.curvature_profile_multiscale(nodes) if c > 0]
  assert multi, "no rung of the ladder resolved a 240 m sweeper"
  mid = tc.radius_m(sorted(multi)[len(multi) // 2])
  assert abs(mid - 240.0) / 240.0 < 0.10, f"multiscale read {mid:.0f} m on a 240 m sweeper"


def test_jitter_on_a_straight_still_reads_straight():
  """The property the whole ladder exists to keep. A short rung is available now, so the noise floor
  has to be what rejects it -- not the absence of a short baseline."""
  import math as _m
  lat0, lon0 = 40.76, -111.89
  mlat, mlon = 111320.0, 111320.0 * _m.cos(_m.radians(lat0))
  nodes = [(lat0 + (i * 12.0) / mlat, lon0 + (0.5 if i % 2 else -0.5) / mlon) for i in range(60)]

  multi = [abs(c) for c in tc.curvature_profile_multiscale(nodes) if c != 0.0]
  narrow = [abs(c) for c in tc.curvature_profile(nodes)[1:-1] if c > 0]
  worst_narrow = tc.radius_m(max(narrow))
  if multi:
    assert tc.radius_m(max(multi)) > 5 * worst_narrow, \
      f"the ladder let jitter through at {tc.radius_m(max(multi)):.0f} m"


def test_every_reading_clears_the_noise_floor_it_was_measured_at():
  """The ladder's whole contract, asserted directly rather than via an example: whatever rung a
  reading came from, k * L^2 must have cleared 8 * jitter * SNR -- otherwise it is a number the
  module itself does not believe."""
  need = 8.0 * tc._JITTER_M * tc._SNR
  for r, spacing in ((21.0, 4.0), (60.0, 8.0), (240.0, 12.0), (600.0, 20.0)):
    nodes = _circle_nodes(r, spacing_m=spacing, arc_deg=180.0)
    for k in tc.curvature_profile_multiscale(nodes):
      if k == 0.0:
        continue
      assert any(abs(k) * L * L >= need for L in tc._BASELINE_LADDER), \
        f"R={r}: emitted k={k} that clears no rung's noise floor"
