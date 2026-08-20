"""FusionPilot: mapd v2's path ahead, in the shape SCC-Map already walks.

The whole point of this file is that it is boring. v1 hands SCC-Map a list of
`{latitude, longitude, velocity}` through /dev/shm/params; v2 publishes `mapdExtendedOut.path`, a
list of points carrying `latitude`, `longitude`, `curvature` and `targetVelocity`. Same shape, one
extra field. So the migration for the curve path is a translation, and the walk, the trigger
arithmetic, the corner-speed factor pair and all four camera defenses stay exactly as they are.

THAT RESTRAINT IS DELIBERATE. The four defenses were each built from a measured event on his roads --
a bend where the map asked for 43 mph and 51 was comfortable, a 50 mph corner acted on at 467 m
against a 353 m camera horizon, and so on. They were designed to interrogate a SINGLE corner speed
arriving with no context, and against a full profile some of them are answering a question that is no
longer being asked. Re-deriving them is real work and it needs drive data, not an afternoon's
reasoning. Swapping the source without touching them means one drive can say whether v2's numbers
match v1's, with everything downstream held still.

WHAT IS NOT USED YET, and is the reason to come back here:

  `curvature` per point. v1 never had it -- the corner speed arrived already computed, which is why
  "SmartCruiseControlMapDecel is a TRIGGER DISTANCE, not a rate" had to be learned the hard way. With
  curvature along the path we could plan a descent against the 3.3 mph/s the buttons actually deliver
  instead of reacting to a step. That is the exit-ramp problem, and it is the next piece.
"""
import math

from openpilot.sunnypilot.navd.helpers import Coordinate

# Below this the way is straight enough that mapd publishing no target velocity means "no corner"
# rather than "could not compute". 1e-4 1/m is a 10 km radius -- nothing any controller would act on.
_STRAIGHT_CURVATURE = 1e-4

# FusionPilot: the lateral acceleration a mapped corner is planned against, m/s^2.
#
# A CAR FACT, measured on 2026-08-19 rather than borrowed: this is where his retrofit Edge PSCM
# stops holding the line in angle mode. NO PARAM, deliberately -- category 3 of "the model gets what
# he has no preference about". It is a property of one car with no fleet to learn it from, and
# `SmartCruiseControlMapFactor` already exists for the preference part.
#
# Do not move it toward mapd's 2.2 (someone else's comfort constant) or toward the 3.2 that a first
# pass reported -- that figure was his own hands on the wheel leaking into the measurement, and the
# 64 mph it produced "agreeing" with the 64 mph he drives was circular, not corroboration.
_CORNER_LAT_ACC = 2.5


def path_from_mapd(sm) -> tuple[Coordinate, list[dict]] | None:
  """(position, target velocities) from mapdExtendedOut, or None to fall back to v1.

  None is returned for every condition that means "v2 has nothing to say": not alive, not valid, or
  an empty path. NOT an empty list -- an empty list is a real answer meaning "no corners ahead" and
  would leave SCC-Map correctly idle, which is indistinguishable from v2 being absent. The caller
  needs to tell those apart to decide whether to read v1 instead.
  """
  if not (sm.alive['mapdExtendedOut'] and sm.valid['mapdExtendedOut']):
    return None

  extended = sm['mapdExtendedOut']
  points = extended.path
  if len(points) == 0:
    return None

  position = extended.position
  # mapd resolves where it thinks the car is along the way it matched, which is a better anchor for
  # walking its own path than a position we read separately -- they cannot disagree by a frame.
  if not (position.latitude or position.longitude):
    return None

  # Dicts rather than the capnp readers on purpose: the walk indexes by name, capnp readers are
  # invalidated when the underlying message is replaced, and this list is held across frames.
  # NaN IS "COULD NOT COMPUTE", AND IT MUST NOT LOOK LIKE ANYTHING ELSE.
  #
  # mapd puts NaN in this path -- confirmed on route 0000038e, 2026-08-18, where reading it crashed a
  # diagnostic with `cannot convert float NaN to integer`. Every comparison against NaN returns
  # False, so it slips through any range check silently and arrives downstream as a number.
  #
  # `p.targetVelocity > 0` already drops a NaN velocity, which is right but accidental: it is False
  # because NaN comparisons are False, not because anyone decided. Made explicit here so a later
  # refactor to `>= 0` or `!= 0` cannot quietly let it through -- a NaN velocity reaching the walk
  # poisons `min()` over the corner speeds, and a NaN v_target is a set-speed request nobody can act
  # on.
  # THE CORNER SPEED IS OURS NOW, DERIVED FROM CURVATURE AT A MEASURED LATERAL ACCELERATION.
  #
  # mapd's `targetVelocity` is exactly `sqrt(2.2 / k)` -- verified across 6,725 points, where 2.2 is
  # `/personalities/standard/map_curve_target_lat_a`, a constant belonging to somebody else's car.
  # It carries no information `curvature` does not, so replacing it costs nothing and gains the one
  # number this car actually has evidence for.
  #
  # 2.5 IS MEASURED, NOT CHOSEN. `tools/bp_pscm_lateral_limit.py` over three routes, splitting on
  # `steeringPressed` because `latActive` only means openpilot was PERMITTED to steer:
  #
  #     openpilot alone (no hands)   n=5251   p50 1.09  p90 1.93  p99 2.73  max 3.19
  #     HIS hands on the wheel       n= 892   p50 1.95  p90 3.09  p99 4.14  max 4.20
  #
  # and the deviation limiter, binned by lateral acceleration, is quiet to 2.5 (<= 3.7% of frames),
  # then 9.1% at 2.5-3.0 and 27.4% at 3.0-3.5. `hands-on%` climbs the same curve -- 6% low, 90%+
  # above 3.0 -- so he takes the wheel exactly where the PSCM starts losing the line. Two
  # independent signatures of one ceiling.
  #
  # THIS RAISES CORNER SPEEDS BY sqrt(2.5/2.2) = 6.6%, which is the opposite direction from "low
  # speed curves don't slow enough" -- and deliberately so. That complaint was measured to be a
  # COVERAGE problem: SCC-Map was active for 146 frames of a 26-minute drive, and SCC-Vision cannot
  # help below ~40 mph because its target is proportional to current speed. Slowing harder on the
  # few corners the map DOES see would not have addressed it, and would have made every one of them
  # wrong in a way he would feel.
  #
  # `SmartCruiseControlMapFactor` still trims on top and is still his: at his current 90 the
  # effective figure is 2.5 * 0.81 = 2.03 m/s^2, comfortably under the measured ceiling.
  targets = []
  for p in points:
    k = float(p.curvature)
    # NaN and straight both mean "no corner speed here", for different reasons, and both must fail
    # closed. A NaN reaching the walk poisons min() over the corner speeds and a NaN v_target is a
    # set-speed request nobody can act on -- the same trap this file already documents twice.
    if math.isnan(k) or abs(k) <= _STRAIGHT_CURVATURE:
      continue
    targets.append({"latitude": p.latitude, "longitude": p.longitude,
                    "velocity": math.sqrt(_CORNER_LAT_ACC / abs(k))})
  if not targets:
    # NO CORNERS AHEAD IS A REAL ANSWER, and returning None for it was a bug against this file's own
    # docstring. Measured on route 00000383: of 46 frames where no point carried a velocity, **all
    # 46 had no curvature either** -- straight road, nothing to slow for. Falling back to v1 there
    # made SCC-Map consult a second, older map for a question v2 had already answered, and it was
    # 8 of the 9 percentage points of fallback on that drive.
    #
    # The discrimination is curvature, not the velocity list. mapd derives velocity from curvature
    # alone (`v = sqrt(a_lat / k)`, verified across 6,725 points), so curvature present with no
    # velocity would mean mapd could not compute rather than that there was nothing to compute --
    # and THAT is worth v1. It happened zero times, and this stays because zero is a measurement
    # rather than a guarantee.
    # AND A NaN CURVATURE IS THE SAME STATEMENT, which this branch got exactly backwards.
    #
    # `NaN > _STRAIGHT_CURVATURE` is False, so a path whose curvature mapd could not compute fell
    # through to `return ... , []` -- "straight road, no corners ahead", the most confident answer
    # this function can give, from the one input that means the opposite. SCC-Map then idles and
    # never consults v1, which is the fallback this branch exists to reach.
    #
    # Same shape as the velocity check above and as several bugs today: a comparison that answers
    # False on a value meaning "unknown" reads as a clean negative.
    if any(math.isnan(p.curvature) for p in points):
      return None
    if any(abs(float(p.curvature)) > _STRAIGHT_CURVATURE for p in points):
      return None
    return Coordinate(position.latitude, position.longitude), []

  return Coordinate(position.latitude, position.longitude), targets
