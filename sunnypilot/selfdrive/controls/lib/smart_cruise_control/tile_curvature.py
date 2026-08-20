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


# THE BASELINE. Why adjacent nodes CANNOT be used, worked out 2026-08-19 before wiring any of this
# into a controller -- and it explains the number that looked like a win.
#
# For three points on a chord of length L with the middle one offset by a sagitta d,
#
#     d = L^2 / 8R        so      R = L^2 / 8d
#
# At the 12 m median node spacing on his way, L = 24 m. A REAL 240 m corner then bulges the middle
# node by only d = 24^2 / (8 * 240) = 0.30 m -- which is BELOW the position noise of an OSM node.
# Turn it around: half a metre of jitter, on its own, reads as
#
#     R = 24^2 / (8 * 0.5) = 144 m
#
# and the "tightest triple = 127 m" figure this module was first validated on is indistinguishable
# from exactly that. It was measuring node jitter, not the bend. Wiring it in would have asked for
# a 127 m corner on a road that has none -- the "gentle sweepers brake hard" regression that got
# SmartCruiseControlVisionEarliness deleted, arriving through a different door.
#
# Noise falls as L^2 while the real signal grows as L^2 / R, so a longer baseline buys signal
# quadratically. At L = 70 m the same 240 m corner gives d = 2.55 m against the same 0.5 m of
# jitter -- five to one instead of worse-than-one.
#
# It is NOT free: a baseline longer than the corner averages the corner away, which is precisely
# what mapd does at 40x. 70 m is chosen to sit above the jitter floor and well below the length of
# the bends that matter here; anything much longer starts reproducing mapd's own failure.
_BASELINE_M = 70.0


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
  lat0 = 0.5 * (a[0] + b[0])
  ax, ay = _to_local_m(a[0], a[1], lat0)
  bx, by = _to_local_m(b[0], b[1], lat0)
  return math.hypot(bx - ax, by - ay)


# ONE BASELINE CANNOT SERVE BOTH ENDS, measured 2026-08-19 on his own mapd path.
#
# A fixed 70 m was derived for a 240 m sweeper and is right there. On a TIGHT corner it is longer
# than the corner itself and averages it away -- mapd's exact failure, reproduced by the fix for it:
#
#     a real 21 m turn (mapd's own number)      70 m baseline read 30 m       too loose
#     the same path, adjacent triples            read 8 m                     noise
#
# The two constraints pull opposite ways and both are quantitative, so the baseline is DERIVED per
# node rather than chosen:
#
#   NOISE FLOOR   jitter d over baseline L fakes a curvature of 8d/L^2, so a reading is only
#                 trustworthy when k >> 8d/L^2. That wants L LARGE.
#   AVERAGING     L longer than the corner's own arc flattens it. That wants L SMALL.
#
# So: walk a ladder of baselines from short to long and take the FIRST -- the finest scale -- whose
# reading clears the noise floor by `_SNR`. Finest-that-clears is the least-averaged measurement
# that is still real, which is exactly the trade above with no room left for taste.
#
#     k * L^2 >= 8 * _JITTER_M * _SNR
#
# Checks out at both ends: the 240 m sweeper clears at 70 m (0.00417 * 4900 = 20.4 >= 20) and the
# 21 m turn clears at 30 m (0.0476 * 900 = 42.8), where 70 m would have flattened it.
_JITTER_M = 0.5          # OSM node position noise; the 0.5 that made 127 m out of a straight road
_SNR = 5.0               # signal-to-noise a reading must clear before it is believed
_BASELINE_LADDER = (20.0, 30.0, 45.0, 70.0)


def curvature_profile_multiscale(nodes: list[tuple[float, float]]) -> list[float]:
  """Curvature at each node, each measured at the finest baseline that clears the noise floor.

  Same length and index meaning as the other two profiles, and 0.0 wherever no baseline on the
  ladder produced a trustworthy reading -- which is a straight road, a way too short to measure, or
  the ends of one. All three mean "no corner here", and reporting a noisy number instead is what
  this whole family of functions exists to avoid.
  """
  n = len(nodes)
  if n < 3:
    return [0.0] * n

  per_scale = [curvature_profile_baseline(nodes, L) for L in _BASELINE_LADDER]
  need = 8.0 * _JITTER_M * _SNR

  out = [0.0] * n
  for i in range(n):
    for L, prof in zip(_BASELINE_LADDER, per_scale, strict=True):
      k = abs(prof[i])
      if k > 0.0 and k * L * L >= need:
        out[i] = prof[i]
        break
  return out


def curvature_profile_baseline(nodes: list[tuple[float, float]],
                               baseline_m: float = _BASELINE_M) -> list[float]:
  """Curvature at each node, measured across ~`baseline_m` of road instead of to its neighbours.

  Same length and same index meaning as `curvature_profile`, and the same 0.0 where no triple
  exists -- here that is any node without `baseline_m / 2` of way on both sides, so the ends of a
  short way read straight rather than reading noise.

  A circle sampled at ANY spacing still gives its own radius, so widening the baseline costs nothing
  on real geometry; what it drops is the jitter that scales as 1 / L^2. See `_BASELINE_M`.
  """
  n = len(nodes)
  if n < 3:
    return [0.0] * n

  # Cumulative distance along the way, so the reach is in METRES of road rather than in nodes --
  # spacing on this way runs 6 m to 112 m, so a fixed node offset would be a 6 m baseline in one
  # place and a 224 m one in another.
  cum = [0.0] * n
  for i in range(1, n):
    cum[i] = cum[i - 1] + _haversine_m(nodes[i - 1], nodes[i])

  half = 0.5 * baseline_m
  out = [0.0] * n
  for i in range(n):
    j = i
    while j > 0 and cum[i] - cum[j] < half:
      j -= 1
    k = i
    while k < n - 1 and cum[k] - cum[i] < half:
      k += 1
    # Refuse rather than shrink. A truncated baseline at the ends would silently be the noisy
    # short-baseline measurement again, reported as though it carried the same confidence.
    if cum[i] - cum[j] < half or cum[k] - cum[i] < half:
      continue
    out[i] = curvature_through(nodes[j], nodes[i], nodes[k])
  return out
