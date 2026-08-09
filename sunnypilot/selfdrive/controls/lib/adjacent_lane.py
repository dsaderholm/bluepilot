"""
BluePilot: what is in the lane I would move into -- and is it even going my way?

Two questions off one sensor. Whether the next lane is worth moving into, and whether it is a lane
at all or the other half of a two-way road.

THE SECOND ONE IS WHY THIS MATTERS
modelV2 publishes lane geometry, not direction of travel. On a two-lane two-way road the oncoming
lane has the same paint, the same drivable width and the same road edge as a passing lane, so every
geometry test in passing_assist.py says "lane available" and means "head-on traffic". That was the
open question the whole design was built around and could not answer.

Map data cannot settle it on this build. mapd v1.12.0 is what ships here, and its documented
outputs are RoadName, MapSpeedLimit, NextMapSpeedLimit, advisory speeds, hazards and curvatures --
no oneway, no lane count. (mapd v2 publishes oneWay, lanes and highwayClass on a cereal MapdOut
message, so this becomes free the day sunnypilot moves to it. It is not this day.)

The radar can just watch. An oncoming vehicle's absolute ground speed is roughly minus its own --
about -27 m/s for someone doing 60 -- and nothing travelling our way, and no barrier or gantry, can
produce that number. One sighting classifies the road, and the classification is held (see
DEFAULT_ONCOMING_MEMORY_S) because the road does not change back when that car has gone by.

The lateral band does the rest for free: an opposing carriageway on a divided highway sits 10 m or
more away, outside the band entirely, so an interstate never trips this while a two-lane road trips
it on the first car met.

THE FIRST ONE

Answers the question the settle timer only papers over. Moving out to pass and finding the other
lane no faster is the maneuver that makes a system feel unfinished, and unlike the rear gap this
one needs no new hardware. The front radar already measures it.

WHY THIS IS FREE
`_update_delphi_mrr` reads a per-detection azimuth from each of the 64 MRR_Detection messages and
derives lateral position from it. There is no in-path filter -- the only rejections are the radar's
own validity flag, a scan-index match, and a ground-return guard below 30 m in long-range mode.
`do_clustering()` groups the detections into tracked objects, and `card` publishes ALL of them on
`liveTracks`. `radard` reduces them to two in-path leads for ACC, so everything off-path is
discarded downstream, not at the source: adjacent-lane traffic is measured, clustered and published
today, and simply thrown away.

TWO RATES, AND WHY THE DEBOUNCE COUNTS MESSAGES RATHER THAN CYCLES
`liveTracks` publishes at about 8.3 Hz on a Delphi MRR, not the 20 Hz its service entry declares --
`_update_delphi_mrr` emits once per four scan modes. The planner runs at 20 Hz, so a naive
per-cycle counter would see the same radar frame two or three times and a "3 frame" debounce would
be worth barely one real observation. Hence the `updated` check in update(): state advances on new
radar data only, and between frames the last reading stands.

Individual tracks are short-lived -- median lifetime under half a second in replayed Ford data --
so the raw occupancy signal flickers. Three consecutive messages is roughly 0.36 s of agreement,
which is enough to drop the flicker without being slow enough to miss a car we are about to pull
in front of.

THE RADAR IS NOT ON THE CENTRELINE, AND NOTHING CORRECTS FOR IT
Confirmed 2026-08-03: the front radar on this car is mounted off-center, as it is on most cars.
`_update_delphi_mrr` derives lateral position from azimuth and range alone -- there is no lateral
mounting offset anywhere in the path -- so every track carries a constant sideways bias equal to
however far the sensor sits from the centerline.

Left alone deliberately, for now. A typical offset is 0.2-0.4 m against a 3.5 m wide band, so it
shifts both edges by about a tenth of a lane; the debounce, the road-edge test and the
path-relative measurement all matter more. But it is a real bias and it is silent, so: if the
adjacent-lane band ever looks skewed to one side in the logs, this is the first thing to suspect.

MEASURED 2026-08-09, AND THE RESULT CONTRADICTS ITSELF -- so nothing has been applied.

Comparing radar-confirmed lead yRel against the model's own lead over a 7 minute drive (n=1208)
gave a median disagreement of +0.40 m, which reads as the radar sitting 0.40 m toward the
PASSENGER side. Two independent things say that is backwards:

  - the sensor is physically on the DRIVER side, behind the lower grille, in the factory pocket.
    Confirmed from the car.
  - the false positives were on the RIGHT -- "an elevated sidewalk after the right shoulder",
    "curbs as other cars". A DRIVER-side offset is what pulls the right-hand band ~0.4 m closer to
    the car and into the shoulder. A passenger-side offset would push that band further out and
    make those reports less likely, not more.

The obvious suspect was the frame flip, and it is not that: radard.py builds the vision lead as
`yRel = -lead.y[0]` (lines 127 and 149), which is exactly the conversion the measurement used. So
the error is somewhere else and is not yet found.

DO NOT APPLY A CORRECTION FROM THAT NUMBER. The sign is what matters here -- backwards turns a
0.40 m bias into 0.80 m, which is worse than leaving it alone. A tape measure from the centerline
to the sensor face settles magnitude and direction at once and depends on none of this.

Measuring it is a tape measure from the car's centerline to the sensor face, and applying it is one
constant subtracted from `lat` below. Not added speculatively -- the number has to be measured
first, and a wrong constant is worse than none.

SIGN CONVENTION -- THE TRAP
Radar `yRel` is LEFT-POSITIVE on this car. Model lane geometry is LEFT-NEGATIVE: `ldw.py` tests the
left lane line against `-(1.08 + CAMERA_OFFSET)`, and `passing_assist.py` follows that. The two
frames are opposite, so anything comparing a radar track to a lane boundary must flip one of them.
This module states which rather than inheriting it, because getting it backwards produces a system
that works perfectly and mirrors every decision.

Not a hypothetical. `radar_interface.py` contains BOTH conventions for the same signal:

  _update_delphi_mrr      yRel = -sin(azimuth) * dist    "left is positive"   <- CAR, this one
  _update_delphi_mrr_64   yRel =  sin(azimuth) * dist    "right is positive"

The two DBCs encode CAN_DET_AZIMUTH identically -- (0.0003834, -3.1416) in both FORD_CADS and
FORD_CADS_64 -- so they cannot both be right. The Fusion and Edge take RADAR.DELPHI_MRR (the CAN
path), which is what this module assumes. The CANFD path is left alone here because it is not this
car and because a sign flip there is invisible to everything currently consuming it: leads sit near
y=0, so mirroring y barely moves an in-path lead. Off-path work is the first thing that would care,
which is exactly why the discrepancy has survived.
"""

import math

# Lateral band counted as "the next lane over". A US lane is 3.7 m, so the neighbouring lane center
# sits near 3.7 m; the band is wider than that because the radar's lateral estimate degrades with
# range and no one drives on the lane center. The lower bound is above our own lane's half-width so
# our own lead never reads as adjacent, and the upper bound stops two-lanes-over traffic counting.
ADJACENT_MIN_M = 2.0
ADJACENT_MAX_M = 5.5

# Absolute ground speed below which a track is roadside furniture, not traffic.
#
# NOT optional, and the reason is the whole character of this radar: liveTracks carries every
# clustered return with no classification of any kind. do_clustering() groups on geometry and
# velocity alone -- there is no object type, no moving/stationary flag, and nothing rejects ground
# objects except a minimum-range guard. Guardrails, jersey barriers, sign gantries and parked cars
# all arrive as ordinary tracks.
#
# A concrete barrier sits 3-5 m off the lane center, which is the middle of the band above. Without
# this test it reads as an adjacent vehicle doing 0 mph -- slower than any lead -- and blocks every
# pass on that side for the length of the barrier. That is the failure mode, and it would look
# exactly like the feature being too conservative rather than like a bug.
#
# The cost is stated plainly: genuinely stopped traffic in the next lane is indistinguishable from
# a barrier to this sensor, so it is not counted either. That is the right way to be wrong here --
# this gate only ever SUPPRESSES a suggestion, so failing to suppress leaves the driver exactly
# where they were, with the blind spot, the mirrors and their own eyes still in the loop. Blocking
# every pass along a barrier would instead make the whole feature look broken.
#
# 5 m/s (11 mph) clears the noise floor: v_abs is v_ego plus a filtered range rate, so a stationary
# return lands within a m/s or two of zero.
MIN_MOVING_MS = 5.0

# Absolute ground speed below which a track is coming TOWARDS us. Same magnitude as MIN_MOVING_MS
# and the same reasoning: this is a noise floor around zero, and anything outside it in the negative
# direction is moving the other way down the road.
#
# This is the answer to the question the whole design has been unable to settle. modelV2 publishes
# lane geometry, not direction of travel, so on a two-lane two-way road the oncoming lane looks
# exactly like a passing lane -- same paint, same drivable width, same everything. Map data cannot
# help on this build: mapd v1.12.0 is what ships here and it writes only RoadName, MapSpeedLimit
# and friends to /dev/shm/params. No oneway, no lane count.
#
# The radar can just watch. An oncoming car's absolute ground speed is roughly minus its own speed
# -- around -27 m/s for someone doing 60 -- which is not a value any same-direction vehicle,
# barrier or sign can produce. It is the one unambiguous measurement available, and the sensor for
# it is already fitted.
MIN_ONCOMING_MS = 5.0

# How far out to LOOK for oncoming traffic. Deliberately much wider than the adjacent-lane band,
# and the reason is the center turn lane.
#
# Bounding oncoming detection to the adjacent band assumed the opposing lane is the one next to us.
# It is on a two-lane road. It is not on any road with a two-way left-turn lane down the middle,
# which is most American arterials: from the single through lane of a 1 + TWLTL + 1 road the turn
# lane sits at 3.7 m and opposing traffic at 7.4 m -- outside a 5.5 m band, so no oncoming was ever
# detected, no veto ever fired, and the geometry test happily offered a pass INTO THE TURN LANE.
# Add a lane each way and it is 11 m. The band was measuring the wrong thing entirely.
#
# The road edge is what bounds this properly (see _on_our_carriageway); this is only a sanity limit
# for radar lateral error at range. 15 m reaches across four lanes, which covers any two-way road
# worth passing on.
ONCOMING_MAX_M = 15.0

# Fraction of our own speed a vehicle in the next lane must be doing before it counts as proof that
# lane is a TRAVEL lane rather than a turn lane.
#
# Without this the discriminator leaks in the one direction that matters. A car slowing into a
# center turn lane to wait for a gap is still moving -- 6 or 7 m/s as it decelerates, comfortably
# over MIN_MOVING_MS -- so it would register as "somebody drove down that lane in our direction"
# and unblock a pass into the very turn lane it was entering.
#
# Traffic actually using a travel lane is doing roughly what we are doing. Traffic entering a turn
# lane is shedding speed hard. 0.6 sits between them and scales with the road instead of assuming
# one: at 45 mph it asks for 27 mph, which a through lane clears easily and a turning car does not.
SAME_DIRECTION_MIN_FRACTION = 0.6

# How long a single sighting keeps the road classified as two-way.
#
# Long, and deliberately so. Meeting a car is EVIDENCE about the road, not an event to react to:
# one oncoming vehicle proves the lane to the left carries opposing traffic, and that stays true
# for the rest of the road whether or not anyone else comes along. On a quiet two-lane road you can
# drive a minute between meetings, and a short memory would spend those gaps offering to pass into
# the oncoming lane -- which is the exact failure this exists to prevent, arriving in the exact
# gaps where it is most plausible.
DEFAULT_ONCOMING_MEMORY_S = 90

# Consecutive liveTracks messages a side must agree on before the reading is believed. Symmetric on
# purpose: the flicker drops tracks as often as it invents them, so debouncing only the appearing
# edge would still produce a jittery clear.
DEBOUNCE_FRAMES = 3

# --- there has to be ROOM, not just a speed advantage ---
#
# From the road: "it tried to pass when there was obviously a car in the lane over from me... I
# could see the car in the other lane just a little bit in front of me."
#
# blocks_move only ever asked whether that lane was FASTER than the vehicle we are stuck behind. A
# car a few meters ahead in the target lane, travelling normally, is faster than a slow lead by a
# wide margin -- so it did not block, and the pass was offered into the space that car occupies.
#
# On a car with blind-spot monitoring that gate would have caught it. This car has none, which is
# exactly why a geometric check cannot be left out: every other gate here is about SPEED, and none
# of them asks whether the space is free.
#
# Expressed as time rather than distance so it scales with the road. One second of travel is the
# gap this refuses to squeeze into -- at 70 mph that is about 31 m, at 40 mph about 18 m. The floor
# stops it collapsing to nothing in traffic.
MIN_ADJACENT_GAP_S = 1.0
MIN_ADJACENT_GAP_M = 18.0

# --- being overtaken, seen by a forward-looking radar. See overtakenSeconds in custom.capnp. ---
#
# A vehicle that passes us was behind us a moment ago. It appears CLOSE and travelling AWAY, and no
# other event in the adjacent lane looks like that -- a car we are gaining on appears far off and
# closes; a car we are passing falls back rather than pulls ahead.
#
# Close, because the further out it appears the more likely it was simply revealed by traffic moving
# rather than by passing us. One and a half car lengths either side of alongside.
OVERTAKE_MAX_D_REL_M = 25.0
# ...and genuinely pulling away, not merely drifting. 2 m/s (4.5 mph) is faster than lane-keeping
# noise and slower than any real overtake, which is the whole gap this has to sit in.
OVERTAKE_MIN_V_REL_MS = 2.0
# One vehicle is one overtake. A track that flickers must not count twice, and radar tracks flicker
# constantly -- so a side that has just counted one holds off until the lane is quiet again.
OVERTAKE_REARM_S = 2.0

# Oncoming used to need no patience at all: one return latched the full memory. The argument was
# that believing a false one costs "a few quiet minutes" and waiting for a second could cost a
# suggestion to pass into a head-on lane.
#
# THAT COST WAS WRONG, and the road said so. Reported after a drive: "I was on I-15 for a while,
# and kept saying two-way road." I-15 is divided. One spurious return silences the feature for the
# full memory, and on a long interstate run a handful of them silence it for the whole drive --
# which is not a few quiet minutes, it is the feature not existing on the road it is most used on.
#
# So: corroboration, and it is nearly free. Genuine opposing traffic closes at 120+ mph and is
# still tracked for seconds -- upwards of a dozen returns at 8.3 Hz -- so three is invisible for a
# real one and fatal to a lone bad return. NOT required to be consecutive, because radar tracks
# flicker; they must fall within ONCOMING_WINDOW_S of each other.
ONCOMING_FRAMES = 3
# How long a partial count survives without corroboration. Longer than one dropped message, far
# shorter than an oncoming vehicle's time in view.
ONCOMING_WINDOW_S = 1.5

# --- and the same rule for the evidence that CANCELS that veto ---
#
# same_direction_seconds is the only thing that releases the strict turn-lane veto (see
# blocks_oncoming case 2). It used to latch the full ninety seconds from ONE return, while the
# veto it overrides required three corroborating messages -- so the claim "that lane is safe to
# move into" was three times cheaper to establish than the claim "that lane is oncoming traffic".
#
# The asymmetry ran the wrong way on the one road where it decides anything. Every gate here is
# built so that missing evidence costs coverage rather than safety, and this was the single place
# where a lone bad return bought a maneuver instead of blocking one -- into a lane blocks_oncoming
# itself calls neither legal nor survivable.
#
# Counted per MESSAGE for the reason in observe_oncoming: this radar publishes several returns at
# once for one piece of scenery, so a per-track count corroborates nothing. Costs a genuine
# sighting nothing either -- §6 of BP-REAR-RADAR-PLAN.md measured real adjacent same-direction
# tracks living 4.97 to 28.73 s, against a 0.12-0.48 s median for the clutter, and a vehicle
# holding station beside us is the slowest-changing target the radar ever has.
SAME_DIRECTION_FRAMES = 3
SAME_DIRECTION_WINDOW_S = 1.5

# An overtake is one vehicle passing us, and OVERTAKE_REARM_S already collapses a whole message to
# a single count. What it cannot do is tell one flicker from one car: a single spurious return at
# close range with a positive range rate reads as somebody going by. Corroboration separates them,
# and it is nearly free here too -- a real overtaking vehicle inside OVERTAKE_MAX_D_REL_M pulling
# away at OVERTAKE_MIN_V_REL_MS stays in that window for seconds, which is dozens of messages.
#
# Two rather than three: this one only feeds a readout, so it is tuned to keep the count honest
# without discarding a brief genuine pass.
OVERTAKE_FRAMES = 2

# Below this the line-of-sight geometry stops being trustworthy and the correction below would
# amplify range-rate noise rather than remove a bias. cos 72 degrees; nothing in the adjacent-lane
# band reaches it except very close alongside, where the radar is least reliable anyway.
MIN_COS_THETA = 0.3

NO_SPEED = 0.0


def ground_speed(v_ego: float, d_rel: float, y_rel: float, v_rel: float) -> float:
  """The object's own speed over the ground, corrected for where it sits in the beam.

  Radar range-rate is the RADIAL component -- the part along the line of sight -- not the full
  relative speed. A stationary object therefore reports `-v_ego * cos(theta)`, not `-v_ego`, and
  `v_ego + v_rel` leaves a residue of `v_ego * (1 - cos(theta))` that grows as the object moves off
  boresight.

  Reported from a drive: a barrier occasionally read as an adjacent vehicle, rarely. The geometry
  says exactly how rarely. At 67 mph a barrier 4 m to the side reads as 0.15 m/s at 40 m, 1.0 at
  15 m, 3.2 at 8 m -- all comfortably below MIN_MOVING_MS -- and then **6.6 m/s at 5 m**, which
  clears the threshold and becomes a car. At 45 mph the same barrier at 5 m reads 4.4 and stays
  furniture. Close range plus high speed only, which is why it was occasional rather than constant.

  Correcting it is exact rather than a fudge:

      stationary        v_rel = -v_ego * cos(t)   ->  0
      same direction    v_rel = (u - v_ego) cos(t) -> u
      oncoming at w     v_rel = (-w - v_ego) cos(t) -> -w

  so all three classifications get the right answer at any angle, not just near boresight.
  """
  cos_theta = max(d_rel / math.hypot(d_rel, y_rel), MIN_COS_THETA) if d_rel > 0 else 1.0
  return v_ego + v_rel / cos_theta


def path_offset(model, d_rel: float) -> float:
  """Where the road goes at d_rel: the model path's lateral position there, camera frame.

  Without this the lane band is measured from the car's straight-ahead axis, which is only the
  lane center on a straight road. A curve of radius R displaces the path by roughly d^2 / 2R, so
  on an ordinary interstate bend -- 500 to 1000 m radius -- our OWN lead at 60 to 100 m sits 2 to
  5 m off axis and lands squarely inside the adjacent-lane band. It is slower than us by
  definition, since that is why a pass is wanted, so it would block the very pass it caused and
  report the next lane as no faster while pointing at the car directly ahead.

  Measuring against the path cancels that: both the lead and the adjacent car swing with the road,
  and only their separation from it survives.

  Returns 0.0 when the model has no usable path, which degrades exactly to the old straight-ahead
  assumption rather than to something arbitrary.
  """
  try:
    xs, ys = model.position.x, model.position.y
  except AttributeError:
    return 0.0
  n = min(len(xs), len(ys))
  if n == 0:
    return 0.0
  if d_rel <= xs[0]:
    return float(ys[0])
  for i in range(1, n):
    if xs[i] >= d_rel:
      x0, x1 = float(xs[i - 1]), float(xs[i])
      if x1 <= x0:
        return float(ys[i])
      f = (d_rel - x0) / (x1 - x0)
      return float(ys[i - 1]) + f * (float(ys[i]) - float(ys[i - 1]))
  # Past the end of the path. The last point is the best estimate available; the distance bound in
  # update() keeps this from being reached with anything the decision depends on.
  return float(ys[n - 1])


# Matches passing_assist's threshold of the same name. Duplicated rather than imported because
# importing it the other way round would be circular, and a road-edge reading this module cannot
# trust is a different decision from one that module cannot trust.
#
# DELIBERATELY STILL 0.5 while passing_assist's went to 1.2 on 2026-08-06, and the divergence is the
# point rather than drift. The two consumers want opposite things from the same number:
#
#   passing_assist   an untrusted edge REFUSES the pass, so a tight threshold is the permissive-to-
#                    nothing direction -- it blocked every suggestion of a 34 minute drive.
#   here             an untrusted edge NARROWS _on_our_carriageway to the adjacent band, which is
#                    the CONSERVATIVE direction. Loosening it widens where opposing traffic is
#                    looked for, and that is what produced "I was on I-15 for a while, and kept
#                    saying two-way road" -- ninety seconds of veto per firing.
#
# So raising this one buys a scenery filter that trusts the edge more often, at the cost of the
# false two-way vetoes that the narrow fallback was introduced to stop. Not worth trading blind;
# it needs its own measurement, and the report's oncoming counters are where that comes from.
MAX_ROAD_EDGE_STD = 0.5
RE_LEFT, RE_RIGHT = 0, 1


def road_edge_offset(model, side: str, d_rel: float):
  """Where the drivable surface ends on that side at d_rel, relative to the path. None if untrusted.

  This is the honest answer to "is that oncoming car on MY road or across a median", and it beats
  the lateral band at it. The band asks how far away something is; a barrier-only divided highway
  puts the opposing lane center around 7 m off, which clears ADJACENT_MAX_M by less than a lane
  width and depends on shoulder widths nobody measured. The road edge asks the question directly:
  the median edge IS where our carriageway stops, so anything beyond it is not on our road.

  It also inverts correctly on the case that matters. On a two-lane two-way road the left road
  edge sits BEYOND the oncoming lane, so an oncoming car is inside it and counts. On a divided road
  the median edge sits between us and them, so it does not. No width assumptions either way.

  Returns None when the model's own std says the edge is not worth trusting -- the caller then
  falls back to the band alone, which is the conservative direction: it can only over-detect
  oncoming traffic, and over-detecting costs a quiet stretch of road rather than a bad suggestion.
  """
  idx = RE_LEFT if side == 'left' else RE_RIGHT
  try:
    if float(model.roadEdgeStds[idx]) > MAX_ROAD_EDGE_STD:
      return None
    edge = model.roadEdges[idx]
  except (IndexError, AttributeError, TypeError):
    return None
  # Same frame and same path-relative convention as the tracks it will be compared against.
  return path_offset(NS_EDGE(edge), d_rel) - path_offset(model, d_rel)


class NS_EDGE:
  """Adapts a roadEdge (which has .x/.y directly) to the .position shape path_offset expects."""

  def __init__(self, edge):
    self.position = edge


class AdjacentLaneSide:
  """One side. Defaults to unavailable, which is not the same as clear."""

  def __init__(self):
    self.available = False
    self.occupied = False
    self.d_rel = 0.0
    self.y_rel = 0.0             # radar frame, left-positive
    self.v_rel = 0.0
    self.v_abs = NO_SPEED        # absolute speed of the nearest vehicle in that lane
    self.oncoming = False        # something on this side is travelling the other way, right now
    self.oncoming_d_rel = 0.0
    self.oncoming_y_rel = 0.0
    self.oncoming_v_abs = NO_SPEED
    # Corroboration for the veto. See ONCOMING_FRAMES.
    self._oncoming_hits = 0
    self._oncoming_gap_s = 0.0
    # ...and for the evidence that cancels it. See SAME_DIRECTION_FRAMES.
    self._same_dir_hits = 0
    self._same_dir_gap_s = 0.0
    # ONE COUNT PER MESSAGE EACH, however many tracks that message carries. See observe_oncoming.
    self._counted_this_message = False
    self._same_dir_counted_this_message = False
    self._overtake_counted_this_message = False
    # Three latches, because three different facts decide whether this side is usable and they
    # expire independently.
    #
    #   oncoming_seconds           opposing traffic somewhere on our road, this side. Says the road
    #                              is two-way. Does NOT by itself say the next lane is theirs.
    #   oncoming_adjacent_seconds  opposing traffic in the lane RIGHT NEXT to us. That lane is
    #                              theirs, full stop -- the two-lane-road case.
    #   same_direction_seconds     a vehicle in the next lane over travelling OUR way. Positive
    #                              proof that lane is a travel lane and not a turn lane.
    #
    # The third exists because two very common roads are geometrically identical and mean opposite
    # things. From the right lane of a four-lane two-way road, and from the left lane of a
    # 2 + TWLTL + 2 arterial, the picture is the same: a lane at 3.7 m and opposing traffic at
    # 7.4 m. In the first the next lane is an ordinary passing lane. In the second it is a two-way
    # left-turn lane and moving into it is neither legal nor survivable as a passing maneuver.
    # Nothing in the geometry separates them. What separates them is whether anyone has ever driven
    # down that lane in our direction.
    self.oncoming_seconds = 0.0
    self.oncoming_adjacent_seconds = 0.0
    self.same_direction_seconds = 0.0
    # Set from the param each cycle by AdjacentLane.update. Held as state rather than passed to
    # blocks_oncoming, so that stays a PROPERTY like RearApproachSide.blocks_lane_change next to it.
    # As a method it silently passed every `assert not side.blocks_oncoming` in the suite -- a bound
    # method is truthy, so the negation is always False and the assertion never fired.
    self.strict = True
    self._raw_occupied = False
    self._streak = 0
    # See overtakenSeconds in custom.capnp -- the only evidence about rear traffic a forward-looking
    # radar can produce. Measured, gating nothing yet.
    self.overtaken_seconds = 0.0     # since the last one; 0 until there has been one
    self.overtaken_count = 0
    self.overtaken_v_abs = 0.0
    self._overtake_rearm_s = 1e3
    # See OVERTAKE_FRAMES. Consecutive messages showing a vehicle going by, so one flicker cannot
    # report a car.
    self._overtake_hits = 0

  def reset(self) -> None:
    """Radar gone. Everything measured goes with it -- EXCEPT the oncoming memory.

    A sensor dropping out is not evidence that a two-way road became one-way. Rebuilding the whole
    object here would clear the one piece of state whose entire purpose is to outlive the
    observation that created it, and it would do so silently, at exactly the moment the display is
    already reporting no data.
    """
    held = (self.oncoming_seconds, self.oncoming_adjacent_seconds, self.same_direction_seconds,
            self.oncoming, self.strict,
            # Same reasoning: how long since somebody last passed us is a fact about the road, and
            # a sensor dropout is not evidence that it changed. Losing it would silently reset the
            # clock to "nobody has ever overtaken", which reads as a quiet lane -- the one direction
            # this measurement must never fail in.
            self.overtaken_seconds, self.overtaken_count, self.overtaken_v_abs)
    self.__init__()
    (self.oncoming_seconds, self.oncoming_adjacent_seconds, self.same_direction_seconds,
     self.oncoming, self.strict,
     self.overtaken_seconds, self.overtaken_count, self.overtaken_v_abs) = held

  def observe(self, occupied: bool, d_rel: float, y_rel: float, v_rel: float, v_abs: float) -> None:
    """Feed one radar message's raw finding through the debounce."""
    self.available = True

    if occupied == self._raw_occupied:
      self._streak += 1
    else:
      self._raw_occupied = occupied
      self._streak = 1

    if self._streak >= DEBOUNCE_FRAMES:
      self.occupied = occupied

    if self.occupied and occupied:
      self.d_rel, self.y_rel = float(d_rel), float(y_rel)
      self.v_rel, self.v_abs = float(v_rel), float(v_abs)
    elif not self.occupied:
      self.d_rel, self.y_rel, self.v_rel, self.v_abs = 0.0, 0.0, 0.0, NO_SPEED

  def observe_oncoming(self, d_rel: float, y_rel: float, v_abs: float, memory_s: float,
                       adjacent: bool) -> None:
    """Record a vehicle travelling the other way on this side.

    Latches the memory only once ONCOMING_FRAMES MESSAGES have corroborated each other. The
    evidence is kept from the first sighting either way, so a veto that does fire can say what
    fired it -- which is the only way to tell a real opposing carriageway from a bad return
    without reading a log.

    MESSAGES, NOT TRACKS, and the distinction is the whole point of the rule. The count used to
    increment per track, so one radar message carrying three returns satisfied three-way
    corroboration in a single frame -- no time passed and nothing was corroborated by anything.
    That is not an odd arrangement of traffic; it is what roadside furniture looks like to this
    radar, which publishes a guardrail or a sign gantry as several tracks at once at slightly
    different ranges, with nothing upstream to separate them from vehicles. So the mitigation
    written for "I was on I-15 for a while, and kept saying two-way road" did not cover the most
    likely way that report happens.

    Costs a genuine sighting nothing: opposing traffic closes at 120+ mph and is tracked across
    dozens of messages, so it still corroborates in well under half a second.
    """
    self.available = True
    self.oncoming = True
    self.oncoming_d_rel = float(d_rel)
    self.oncoming_y_rel = float(y_rel)
    self.oncoming_v_abs = float(v_abs)
    self._oncoming_gap_s = 0.0
    if not self._counted_this_message:
      self._counted_this_message = True
      self._oncoming_hits += 1
    if self._oncoming_hits < ONCOMING_FRAMES:
      return
    self.oncoming_seconds = float(memory_s)
    if adjacent:
      self.oncoming_adjacent_seconds = float(memory_s)

  def tick_overtaken(self, dt: float) -> None:
    """Wall time, advanced every cycle whatever the radar is doing.

    Only counts up once there has been one: zero means "nobody has passed us in this lane", which is
    not the same claim as "nobody has passed us for zero seconds" and must not read as a busy lane.
    """
    if self.overtaken_count:
      self.overtaken_seconds = min(self.overtaken_seconds + dt, 1e4)
    self._overtake_rearm_s = min(self._overtake_rearm_s + dt, 1e4)

  def observe_overtaken(self, v_abs: float) -> None:
    """Somebody just went past us in this lane. See OVERTAKE_MAX_D_REL_M.

    Re-armed rather than counted per frame -- one vehicle is one overtake, and a track that flickers
    at the threshold would otherwise report a convoy.

    The re-arm collapses a message to one count but cannot tell one flicker from one car, so the
    count also has to survive OVERTAKE_FRAMES consecutive messages before it is believed.
    """
    if self._overtake_counted_this_message:
      return
    self._overtake_counted_this_message = True
    self._overtake_hits += 1
    if self._overtake_hits < OVERTAKE_FRAMES:
      return
    if self._overtake_rearm_s < OVERTAKE_REARM_S:
      return
    self._overtake_rearm_s = 0.0
    self.overtaken_count += 1
    self.overtaken_seconds = 0.0
    self.overtaken_v_abs = float(v_abs)

  def observe_same_direction(self, memory_s: float) -> None:
    """A vehicle in the next lane going our way. The only positive evidence that lane is drivable.

    Corroborated across MESSAGES before it latches, for the reasons at SAME_DIRECTION_FRAMES: this
    is the one claim in the module that opens a maneuver rather than closing one, and it was the
    only latch here a single return could set.
    """
    if self._same_dir_counted_this_message:
      return
    self._same_dir_counted_this_message = True
    self._same_dir_hits += 1
    self._same_dir_gap_s = 0.0
    if self._same_dir_hits < SAME_DIRECTION_FRAMES:
      return
    self.same_direction_seconds = float(memory_s)

  def begin_message(self) -> None:
    """Start of a new liveTracks message. Called once per message, which is what makes it the right
    place to re-arm every per-message corroboration gate -- see observe_oncoming.

    Named for the message rather than for oncoming because three separate counts re-arm here now.
    While it was called clear_oncoming this looked like the oncoming path's private bookkeeping,
    which is part of how the same-direction latch came to be the one uncorroborated claim in the
    module: it was added beside corroborated ones without anything pointing at where corroboration
    is actually wired up.
    """
    self.oncoming = False
    self._counted_this_message = False
    self._same_dir_counted_this_message = False
    # Overtakes must be seen on CONSECUTIVE messages, so a message that showed nothing breaks the
    # run. Read before the flag is cleared, because it still describes the message just finished.
    if not self._overtake_counted_this_message:
      self._overtake_hits = 0
    self._overtake_counted_this_message = False

  def decay(self, dt: float) -> None:
    """Wall time. Every held claim ages here, whatever the radar is doing."""
    # A partial count that stops being corroborated is discarded rather than carried forward, or
    # single bad returns minutes apart would eventually add up to a veto -- and, for the
    # same-direction count, to a pass offered into a turn lane.
    self._oncoming_gap_s += dt
    if self._oncoming_gap_s > ONCOMING_WINDOW_S:
      self._oncoming_hits = 0
    self._same_dir_gap_s += dt
    if self._same_dir_gap_s > SAME_DIRECTION_WINDOW_S:
      self._same_dir_hits = 0
    self.oncoming_seconds = max(0.0, self.oncoming_seconds - dt)
    self.oncoming_adjacent_seconds = max(0.0, self.oncoming_adjacent_seconds - dt)
    self.same_direction_seconds = max(0.0, self.same_direction_seconds - dt)

  @property
  def same_direction_recent(self) -> bool:
    return self.same_direction_seconds > 0.0

  @property
  def blocks_oncoming(self) -> bool:
    """Is the lane next to us one we must not move into?

    Two ways to be true, and they are not the same claim:

    1. Opposing traffic has been seen IN that lane. It is theirs. Nothing overrides this -- not even
       having also seen a car going our way there, which on a two-lane road just means somebody was
       overtaking us.

    2. The road is two-way, and we have no evidence the next lane is a travel lane. This is the
       center-turn-lane case. From the left lane of a 2 + TWLTL + 2 arterial the turn lane is at
       3.7 m and opposing traffic at 7.4 m; from the right lane of a plain four-lane two-way road
       an ordinary passing lane is at 3.7 m and opposing traffic at 7.4 m. Identical geometry,
       opposite meanings. The only thing that tells them apart is whether anyone has driven down
       that lane in our direction, so absence of that evidence is treated as "assume turn lane".

    Held rather than instantaneous throughout: meeting a car is evidence about the road, not an
    event. The seconds after it has gone by are exactly when the lane looks most invitingly empty
    and is most certainly still not ours.

    `self.strict` decides case 2 only, and it is a genuine trade rather than a safety dial, which
    is why it is the driver's to make:

      strict   Utah's 2+1 highways lose their passing lanes until someone drives down one. US-6 and
               US-89 have long alternating passing-lane sections and they are often empty, which is
               exactly when a pass is wanted.
      lenient  a 1 + TWLTL + 1 arterial can offer a pass into the turn lane, until an oncoming car
               happens to use that lane to turn -- which does eventually happen, and then case 1
               latches and it stops.

    Case 1 is unaffected by the flag. Opposing traffic seen IN the next lane is not a judgment
    call.
    """
    if self.oncoming_adjacent_seconds > 0.0:
      return True
    if not self.strict:
      return False
    return self.oncoming_seconds > 0.0 and not self.same_direction_recent

  def blocks_move(self, beat_speed: float, margin: float, v_ego: float = 0.0) -> bool:
    """Would moving into this lane actually gain anything?

    Not "is it occupied" -- a car in the target lane is fine if it is moving well. The test is
    whether the move is worth making, and both callers express that as the same comparison against
    a speed they need to beat:

      passing     beat the lead we are trying to get past, by the deficit margin. A target lane
                  whose nearest car is no faster swaps one obstruction for another and then wants
                  to be undone, which is the weave the settle timer suppresses rather than prevents.
      keep right  beat "the set speed minus the deficit", with no extra margin. Moving over behind
                  a car slow enough that we would immediately want to pass it is a wasted pair of
                  lane changes, and the same threshold decides both halves.

    Returns False when unavailable -- unknown is not blocked, and the caller reports availability
    separately so a decision made without this data stays legible as such.
    """
    if not self.available or not self.occupied:
      return False
    # NO ROOM beats any speed argument. See MIN_ADJACENT_GAP_S -- a vehicle this close in the lane
    # we would move into is occupying the space, and how fast it is going does not change that.
    if 0.0 < self.d_rel < max(MIN_ADJACENT_GAP_M, v_ego * MIN_ADJACENT_GAP_S):
      return True
    return (self.v_abs - beat_speed) < margin


class AdjacentLane:
  """Both sides, fed from the front radar's off-path tracks."""

  def __init__(self):
    self.left = AdjacentLaneSide()
    self.right = AdjacentLaneSide()
    self.oncoming_seen = False   # ever, this drive -- logged so the veto can be audited

  @property
  def available(self) -> bool:
    return self.left.available or self.right.available

  @property
  def oncoming_any_side(self) -> bool:
    """The road has opposing traffic on it. For the log and the display; the GATE is per side and
    goes through blocks_oncoming(), because a road being two-way does not make both sides of it
    unusable and the middle-lane case is a judgment call the driver owns."""
    return self.left.oncoming_seconds > 0.0 or self.right.oncoming_seconds > 0.0

  @property
  def oncoming_seconds_left(self) -> float:
    return max(self.left.oncoming_seconds, self.right.oncoming_seconds)

  def reset(self) -> None:
    self.left.reset()
    self.right.reset()

  @staticmethod
  def _on_our_carriageway(model, side: str, lat: float, d_rel: float) -> bool:
    """Is that oncoming vehicle on our road, or across a median on the other carriageway?

    The one case the lateral band cannot settle on its own. A divided highway with a jersey barrier
    and no grass puts the opposing lane center around 7 m away -- outside ADJACENT_MAX_M, but by
    less than a lane width, and the margin rests on shoulder widths that vary by road. Radar
    lateral error grows with range, so at 120 m that margin is not something to bet a veto on.

    The road edge answers it without any width assumptions at all. Beyond the edge of our own
    drivable surface is, by definition, not our road.

    UNKNOWN NARROWS THE BAND RATHER THAN OPENING IT. An untrusted or missing edge used to fall
    back to the full band, on the reasoning that over-detecting only costs a quiet stretch of road.
    The road disagreed: "I was on I-15 for a while, and kept saying two-way road." Each firing is a
    ninety second silence, so on a divided highway a leaky fallback is not a quiet stretch, it is
    the feature switched off for the drive.

    So with no usable edge, only the ADJACENT lane counts. That is where opposing traffic sits on
    the road this veto exists for -- an ordinary two-lane highway -- and it is the one claim that
    can be made without knowing where our carriageway ends. Something ten meters out with no edge
    to place it against is more likely across a median than in the next lane.

    The cost is real and worth naming: on a two-way road with a center turn lane the opposing
    traffic is two lanes out, and that case now needs a trusted road edge to be seen at all. With
    one, the full band still applies.
    """
    edge_lat = road_edge_offset(model, side, d_rel)
    if edge_lat is None:
      return abs(lat) <= ADJACENT_MAX_M
    # Camera frame: left is negative. "Inside the edge" is therefore a different comparison per
    # side, which is exactly the sort of thing that reads fine and is backwards.
    return lat > edge_lat if side == 'left' else lat < edge_lat

  def update(self, sm, v_ego: float, max_distance_m: float, dt: float = 0.05,
             memory_s: float = DEFAULT_ONCOMING_MEMORY_S, strict: bool = True) -> None:
    """Scan liveTracks for the nearest vehicle in each adjacent lane.

    Nearest by dRel, because that is the one we would meet first. Anything beyond the passing
    look-ahead is ignored for the same reason the passing trigger ignores it: too far to be a
    decision yet.

    Holds the last reading on cycles with no new radar message; resets only when the service is
    dead or invalid. Between those two cases lies the whole distinction this module maintains --
    stale-but-real data is not the same as no sensor.
    """
    # Runs before every early return below. A dead radar does not make a two-way road one-way, and
    # a cycle with no new message is not evidence of anything -- so the clock is wall time, not
    # radar time, and nothing except its own expiry clears it.
    self.left.strict = self.right.strict = strict
    self.left.decay(dt)
    self.right.decay(dt)
    self.left.tick_overtaken(dt)
    self.right.tick_overtaken(dt)

    try:
      if not (sm.alive['liveTracks'] and sm.valid['liveTracks']):
        self.reset()
        return
      if not sm.updated['liveTracks']:
        return
      tracks = sm['liveTracks'].points
      model = sm['modelV2']
    except (KeyError, AttributeError):
      self.reset()
      return

    self.left.begin_message()
    self.right.begin_message()

    best: dict[str, object] = {'left': None, 'right': None}
    for p in tracks:
      if p.dRel <= 0 or p.dRel > max_distance_m:
        continue
      # Radar yRel is LEFT-POSITIVE -- the opposite of the camera frame the model works in. Flip
      # first, THEN subtract where the road actually goes at that distance, so the result is
      # offset-from-the-lane rather than offset-from-straight-ahead. See path_offset: skipping
      # this puts our own lead in the next lane on every curve.
      lat = -p.yRel - path_offset(model, p.dRel)
      abs_lat = abs(lat)
      # Our own lane. Nothing here is about a lane change.
      if abs_lat < ADJACENT_MIN_M:
        continue
      # Beyond any road we would consider passing on, and beyond what the radar's lateral estimate
      # is worth at range.
      if abs_lat > ONCOMING_MAX_M:
        continue
      # Camera frame now: negative is left.
      side = 'left' if lat < 0 else 'right'
      obj = self.left if side == 'left' else self.right
      adjacent = abs_lat <= ADJACENT_MAX_M

      # NOTHING BEYOND THE ROAD EDGE IS TRAFFIC. His question, and it is the right one:
      # *"It shouldn't pick up anything beyond the red line, right?"*
      #
      # This test already existed and was applied to ONCOMING ONLY, which left the same-direction
      # and overtake paths with nothing but a speed threshold between them and the scenery. From a
      # drive on 2026-08-06: *"it picked up an elevated sidewalk after the right shoulder"*, *"it
      # kept seeing curbs as other cars, even though I could see a red line on the curb too"*, and
      # a count of fifty vehicles having overtaken him in a few minutes.
      #
      # ground_speed() corrects the geometry that makes a close barrier read as moving, and it is
      # exact -- but it is a fix for the SYMPTOM. The road edge is the actual boundary: beyond the
      # edge of our own drivable surface is, by definition, not our road, whatever its range rate
      # happens to compute to. Applied once here, before anything is classified.
      #
      # Fails open in the sense that matters: with no trusted edge, _on_our_carriageway narrows to
      # the adjacent band rather than opening up, so an unseen edge costs coverage rather than
      # letting the scenery back in.
      if not self._on_our_carriageway(model, side, lat, p.dRel):
        continue

      # The sign of absolute ground speed sorts the rest out. Oncoming is checked FIRST, because it
      # is the only one of the three that is a safety fact rather than a convenience one.
      # NOT v_ego + p.vRel -- see ground_speed(). That form reads a close barrier as a moving car.
      v_abs = ground_speed(v_ego, p.dRel, p.yRel, p.vRel)

      if v_abs < -MIN_ONCOMING_MS:
        # Travelling the other way. Seen across the FULL width of our road, not just the next lane
        # -- on anything with a center turn lane the opposing traffic is two lanes out, and bounding
        # this to the adjacent band meant those roads produced no veto at all. That width is what
        # the carriageway test above allows; it is the same test, hoisted.
        obj.observe_oncoming(p.dRel, p.yRel, v_abs, memory_s, adjacent)
        self.oncoming_seen = True
        continue

      # Roadside furniture, not traffic. See MIN_MOVING_MS -- this radar publishes barriers and
      # sign gantries as ordinary tracks and nothing upstream tells them apart from cars.
      if v_abs < MIN_MOVING_MS:
        continue

      # Same-direction traffic. Beyond the adjacent lane it tells us nothing we act on, but WITHIN
      # it, it is the only positive evidence that the next lane is a travel lane rather than a
      # two-way turn lane. See blocks_oncoming: those two are otherwise indistinguishable.
      if not adjacent:
        continue

      # SOMEBODY JUST WENT PAST US. Close, and pulling away -- which is what an overtake looks like
      # from in front, and nothing else in this lane does. A car we are gaining on appears far off
      # and closes; a car we are passing falls back. See overtakenSeconds in custom.capnp: this is
      # the only thing a forward-looking radar can say about the traffic behind us.
      if p.dRel <= OVERTAKE_MAX_D_REL_M and p.vRel >= OVERTAKE_MIN_V_REL_MS:
        obj.observe_overtaken(v_abs)
      # ...but only at a speed that means "travelling", not "turning". See
      # SAME_DIRECTION_MIN_FRACTION: a car decelerating into a turn lane would otherwise vouch for
      # the lane it is about to stop in.
      if v_abs >= SAME_DIRECTION_MIN_FRACTION * v_ego:
        obj.observe_same_direction(memory_s)

      if best[side] is None or p.dRel < best[side].dRel:
        best[side] = p

    for name, obj in (('left', self.left), ('right', self.right)):
      p = best[name]
      obj.observe(p is not None,
                  p.dRel if p is not None else 0.0,
                  p.yRel if p is not None else 0.0,
                  p.vRel if p is not None else 0.0,
                  ground_speed(v_ego, p.dRel, p.yRel, p.vRel) if p is not None else NO_SPEED)
