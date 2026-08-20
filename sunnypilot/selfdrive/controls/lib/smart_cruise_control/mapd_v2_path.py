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

from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.tile_curvature import (
  curvature_profile_multiscale,
)

from openpilot.sunnypilot.navd.helpers import Coordinate

# Below this the way is straight enough that mapd publishing no target velocity means "no corner"
# rather than "could not compute". 1e-4 1/m is a 10 km radius -- nothing any controller would act on.
_STRAIGHT_CURVATURE = 1e-4

# The tightest curvature worth believing from mapd, 1/m. 0.05 is a 20 m radius -- tighter than any
# road this car is driven on at a speed SCC-Map acts at, and the same scale the ladder's shortest
# rung resolves. Past it is a bad tile or a bad way match, not a corner.
_MAX_TRUSTED_CURVATURE = 0.05

# FusionPilot: the lateral acceleration a mapped corner is planned against, m/s^2.
#
# A CAR FACT, measured on 2026-08-19 rather than borrowed: this is where his retrofit Edge PSCM
# stops holding the line in angle mode. NO PARAM, deliberately -- category 3 of "the model gets what
# he has no preference about". It is a property of one car with no fleet to learn it from, and
# `SmartCruiseControlMapFactor` already exists for the preference part.
#
# 2.5 -> 2.0 -> 2.4, AND THE THIRD NUMBER IS THE FIRST ONE MEASURED AGAINST THE RIGHT FAILURE.
#
# He reported curves too fast, then made it precise: *"I got steering exhausted warnings from the
# PSCM."* I lowered to 2.0 against `steerSaturated`. Then he said the thing that mattered: *"I just
# ignore most steering saturated errors until it starts to stray enough from my lane."*
#
# SATURATION IS NOT THE FAILURE. RUNNING WIDE IS. Measured as the lateral-acceleration SHORTFALL --
# how much less cornering the car delivered than was commanded, `(desiredCurvature - curvature) *
# v^2`, hands off the wheel, above 25 mph:
#
#     lat_acc bin   frames   mean |shortfall|   >0.5 short   saturated
#      0.5 - 1.0      4022       0.165            3.2%         0.0%
#      1.0 - 1.5      1443       0.245           10.0%         0.6%
#      1.5 - 2.0      1580       0.214            8.7%         3.2%
#      2.0 - 2.5       281       0.288           11.4%         2.8%
#      2.5 - 3.0        20       0.909           85.0%         0.0%
#
# Tracking is FLAT to 2.5 and collapses above it. And saturation does not predict it at all -- 3.2%
# saturated where tracking is clean, 0% in the bin that actually runs wide. He was right that the
# warnings are noise; I had tuned against them.
#
# 2.4 keeps a margin under the collapse without giving away speed for warnings he ignores -- and he
# has been explicit that he would take curves faster, not slower. The 2.5-3.0 bin is only 20 frames,
# so the collapse point is established in KIND rather than to a decimal; that is the reason for a
# margin rather than sitting exactly on 2.5.
#
# The lesson is the signal, not the number: three values came out of three different failure
# definitions, and only the last one was the failure HE cares about. Ask which failure before
# measuring a limit.
#
# Do not move it back toward the 3.2 an earlier pass reported: that figure was his own hands on the
# wheel leaking into the measurement, and the 64 mph it produced "agreeing" with the 64 mph he
# drives was circular, not corroboration.
_CORNER_LAT_ACC = 2.4


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
  # THE VALUE ITSELF, and why it moved, is at `_CORNER_LAT_ACC` -- kept in one place so a tuning
  # change cannot leave a stale argument for the old number sitting next to the code that uses it.
  # `SmartCruiseControlMapFactor` still trims on top and is still his: at his current 90 the
  # effective figure is 2.4 * 0.81 = 1.94 m/s^2, under the 2.5 collapse point.
  # AND THE CURVATURE IS OURS TOO NOW, computed from the path's own COORDINATES.
  #
  # mapd smooths curvature over a window long enough to average a bend away -- on his I-80 corner it
  # published 5,000 m where the tile geometry and the car both say ~250 m. But the COORDINATES it
  # publishes are not smoothed: measured 2026-08-19, a path whose curvature mapd gave as 21 m
  # recomputes to the same order from its own lat/lon. So the real shape is already in this message,
  # and there is no need to open the tile store -- which matters, because reading tiles here would
  # put blocking file I/O in the planner, the exact design flaw v2 exists to escape.
  #
  # TAKE WHICHEVER IS TIGHTER. Ours resolves bends mapd flattens; mapd may still have a corner at a
  # scale no rung of the ladder clears. The larger |curvature| is never worse than today and better
  # wherever mapd smoothed -- and a spurious tight reading is exactly what the ladder's noise floor
  # refuses, so this does not import jitter.
  coords = [(float(p.latitude), float(p.longitude)) for p in points]
  ours = curvature_profile_multiscale(coords)

  targets = []
  for p, k_ours in zip(points, ours, strict=True):
    k_mapd = float(p.curvature)
    # NaN and straight both mean "no corner speed here", for different reasons, and both must fail
    # closed. A NaN reaching the walk poisons min() over the corner speeds and a NaN v_target is a
    # set-speed request nobody can act on -- the same trap this file already documents twice. NaN is
    # dropped from the COMPARISON rather than propagated: our own reading may be a real corner there.
    # BOUNDED ABOVE, and only mapd's number needs it. Ours is geometrically limited -- a 20 m
    # baseline cannot resolve below roughly a 5 m radius -- while mapd's arrives unchecked, and the
    # `max()` routes it straight past the ladder's entire jitter-refusing purpose.
    #
    # Two measured outcomes without the bound. k = 100 prices the corner at 0.155 m/s, which the
    # walk floors to MIN_V and publishes as a live interstate slowdown to 12.4 mph -- and since that
    # is under the 45 mph ramp threshold, camera defenses 2 and 3 skip it, so NOTHING questions it.
    # k = inf gives a velocity of exactly 0.0, which passes the straight test, wins `min()` in the
    # walk, and then reads as "no corner" downstream: one bad point masking every real corner ahead.
    k = abs(k_ours)
    if math.isfinite(k_mapd) and abs(k_mapd) <= _MAX_TRUSTED_CURVATURE:
      k = max(k, abs(k_mapd))
    if k <= _STRAIGHT_CURVATURE:
      continue
    targets.append({"latitude": p.latitude, "longitude": p.longitude,
                    "velocity": math.sqrt(_CORNER_LAT_ACC / k)})
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
