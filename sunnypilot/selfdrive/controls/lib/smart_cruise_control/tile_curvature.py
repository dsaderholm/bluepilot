"""FusionPilot: the corner's real shape, computed from the OSM nodes rather than taken from mapd.

WHY THIS EXISTS, measured on 2026-08-18 and reversing a conclusion stated twice that evening. He
reported a curve on I-80 (wayId 31532588, "Dwight D. Eisenhower Highway", motorway, 3 lanes) where
Smart Cruise Control did nothing at all. SCC-Map was active for 5 frames out of 25,986.

It was not SCC-Map's logic and not the four camera defenses -- SCC-Map never proposed a target, so
there was nothing to veto. It was the input:

    mapd published curvature       ~0.0002 1/m   ->  5,000 m radius, i.e. a straight
    THE TILE ON HIS OWN DEVICE      0.00790 1/m   ->    127 m at the tightest triple
    node spacing on that way        min 6 m, MEDIAN 12 m, max 112 m, 56 nodes
    what the car actually pulled    3.46 m/s^2 at 64 mph  ->  ~240 m radius

**The tile is right.** 240 m sits between the tile's tightest (127 m) and its median (364 m), which
is what a real driving line through a varying bend looks like. Twelve-metre node spacing resolves a
240 m corner easily. mapd read those same nodes and published a number FORTY TIMES too small -- it
smooths over a window long enough to average the bend away.

The first investigation checked mapd's SETTINGS, correctly found no curvature or smoothing knob
(`map_curve_target_lat_a` is `SmartCruiseControlMapFactor` in other units), and concluded the map
was blind. Wrong layer. The data was on the device the whole time and
`tools/bp_offline_map.py` could already read it.

WHAT THIS MODULE IS, AND IS NOT
-------------------------------
It is the geometry only: three consecutive nodes define exactly one circle, and that circle's radius
is the corner's radius there. Pure arithmetic, no I/O, no params, no policy -- so it is testable
offline in full, which is the half of this problem that can be settled without a drive.

**It deliberately does NOT choose a speed.** Turning a radius into a corner speed is the other half
and it is HIS correction, which is the sharper one:

    "remember that my PSCM requires slower speeds for curves, so how I take the curve won't be
     accurate. I want to take the curve as fast as the PSCM can handle with angle steering."

So the target is neither how he drives the corner (that encodes the PSCM's limits, not the car's
capability -- which is also why learning it from his braking is the wrong shape, and pinned holds
already do that anyway) nor openpilot's generic `_A_LAT_REG_MAX` comfort constants. It is the
fastest the retrofit Edge PSCM can hold the lane at in angle mode: a CAR FACT with no fleet to learn
it from, written code with no param. That belongs in the controller, against the same authority
limit the passing-assist curve gate rests on.
"""
from __future__ import annotations

import math

# Earth radius used for the local flat-earth projection below. Over the tens of metres between two
# OSM nodes the curvature of the planet is irrelevant; what matters is that latitude and longitude
# are converted to metres CONSISTENTLY, or the circle through three points is fitted to a stretched
# picture of the road and comes out elliptical.
_EARTH_R = 6371000.0

# Below this the three points are effectively collinear and the circumradius explodes toward
# infinity. Reported as 0 curvature -- straight -- which is the honest reading and avoids a divide
# that would otherwise produce inf or a wild number from rounding in the last decimal of a node.
_MIN_TRIANGLE_AREA_M2 = 1e-6


def _to_local_m(lat: float, lon: float, lat0: float) -> tuple[float, float]:
  """Latitude/longitude to metres on a plane centred at lat0.

  Longitude degrees shrink with latitude, and at 40.7 N -- his roads -- a degree of longitude is
  only 0.76 of a degree of latitude. Skipping that cosine stretches every corner east-west by a
  third and the fitted circle is wrong by the same amount.
  """
  return (math.radians(lon) * _EARTH_R * math.cos(math.radians(lat0)),
          math.radians(lat) * _EARTH_R)


def curvature_through(a: tuple[float, float], b: tuple[float, float],
                      c: tuple[float, float]) -> float:
  """Curvature (1/m) of the unique circle through three (lat, lon) points. 0.0 when collinear.

  Circumradius R = |ab||bc||ca| / (4 * area). Curvature is 1/R, which is used instead of the radius
  precisely so a straight road is 0 rather than infinity.
  """
  lat0 = b[0]
  ax, ay = _to_local_m(a[0], a[1], lat0)
  bx, by = _to_local_m(b[0], b[1], lat0)
  cx, cy = _to_local_m(c[0], c[1], lat0)

  # Twice the signed triangle area, via the cross product.
  area2 = abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay))
  if area2 / 2.0 < _MIN_TRIANGLE_AREA_M2:
    return 0.0

  ab = math.hypot(bx - ax, by - ay)
  bc = math.hypot(cx - bx, cy - by)
  ca = math.hypot(ax - cx, ay - cy)
  if ab == 0.0 or bc == 0.0 or ca == 0.0:
    return 0.0

  radius = (ab * bc * ca) / (2.0 * area2)
  return 1.0 / radius if radius > 0.0 else 0.0


def curvature_profile(nodes: list[tuple[float, float]]) -> list[float]:
  """Curvature at each node of a way, from its neighbours. Endpoints are 0 -- no triple exists.

  Same length as `nodes` on purpose: the caller walks a path and needs index i of the profile to
  mean index i of the way. Returning a shorter list is how an off-by-one enters a controller that
  is deciding when to brake.
  """
  n = len(nodes)
  if n < 3:
    return [0.0] * n
  out = [0.0] * n
  for i in range(1, n - 1):
    out[i] = curvature_through(nodes[i - 1], nodes[i], nodes[i + 1])
  return out


def radius_m(curvature: float) -> float:
  """Curvature back to a radius, with straight reported as infinity rather than a divide error."""
  return math.inf if curvature <= 0.0 else 1.0 / curvature
