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
from openpilot.sunnypilot.navd.helpers import Coordinate

# Below this the way is straight enough that mapd publishing no target velocity means "no corner"
# rather than "could not compute". 1e-4 1/m is a 10 km radius -- nothing any controller would act on.
_STRAIGHT_CURVATURE = 1e-4


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
  targets = [{"latitude": p.latitude, "longitude": p.longitude, "velocity": float(p.targetVelocity)}
             for p in points if p.targetVelocity > 0]
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
    if any(abs(float(p.curvature)) > _STRAIGHT_CURVATURE for p in points):
      return None
    return Coordinate(position.latitude, position.longitude), []

  return Coordinate(position.latitude, position.longitude), targets
