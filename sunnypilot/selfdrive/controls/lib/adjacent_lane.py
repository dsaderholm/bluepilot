"""
BluePilot: what is in the lane I would move into -- and is it even going my way?

Two questions off one sensor. Whether the next lane is worth moving into, and whether it is a lane
at all or the other half of a two-way road.

THE SECOND ONE IS WHY THIS MATTERS
modelV2 publishes lane geometry, not direction of travel. On a two-lane undivided road the oncoming
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
lane no faster is the manoeuvre that makes a system feel unfinished, and unlike the rear gap this
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

# Lateral band counted as "the next lane over". A US lane is 3.7 m, so the neighbouring lane centre
# sits near 3.7 m; the band is wider than that because the radar's lateral estimate degrades with
# range and no one drives on the lane centre. The lower bound is above our own lane's half-width so
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
# A concrete barrier sits 3-5 m off the lane centre, which is the middle of the band above. Without
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
# lane geometry, not direction of travel, so on a two-lane undivided road the oncoming lane looks
# exactly like a passing lane -- same paint, same drivable width, same everything. Map data cannot
# help on this build: mapd v1.12.0 is what ships here and it writes only RoadName, MapSpeedLimit
# and friends to /dev/shm/params. No oneway, no lane count.
#
# The radar can just watch. An oncoming car's absolute ground speed is roughly minus its own speed
# -- around -27 m/s for someone doing 60 -- which is not a value any same-direction vehicle,
# barrier or sign can produce. It is the one unambiguous measurement available, and the sensor for
# it is already fitted.
MIN_ONCOMING_MS = 5.0

# How long a single sighting keeps the road classified as undivided.
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

# Oncoming needs no such patience, and giving it any would be backwards. Occupancy debounces
# because a flickering track produces a flickering suggestion; a single oncoming return is already
# proof of a two-way road, and the cost of believing a false one is a few quiet minutes while the
# cost of waiting for a second is a suggestion to pass into a head-on lane. One is enough.
ONCOMING_FRAMES = 1

NO_SPEED = 0.0


def path_offset(model, d_rel: float) -> float:
  """Where the road goes at d_rel: the model path's lateral position there, camera frame.

  Without this the lane band is measured from the car's straight-ahead axis, which is only the
  lane centre on a straight road. A curve of radius R displaces the path by roughly d^2 / 2R, so
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
    self.oncoming_v_abs = NO_SPEED
    # Seconds of "this SIDE carries opposing traffic" left on the clock. Per side, not per road,
    # and that is what lets the feature keep working on a four-lane undivided arterial: sitting in
    # the left lane there, the oncoming lane is one over on the LEFT and the through lane is one
    # over on the RIGHT. A whole-road veto would give up on both; this gives up only on the side
    # the traffic is actually on.
    self.oncoming_seconds = 0.0
    self._raw_occupied = False
    self._streak = 0

  def reset(self) -> None:
    """Radar gone. Everything measured goes with it -- EXCEPT the oncoming memory.

    A sensor dropping out is not evidence that a two-way road became one-way. Rebuilding the whole
    object here would clear the one piece of state whose entire purpose is to outlive the
    observation that created it, and it would do so silently, at exactly the moment the display is
    already reporting no data.
    """
    held_s, held_seen = self.oncoming_seconds, self.oncoming
    self.__init__()
    self.oncoming_seconds, self.oncoming = held_s, held_seen

  def observe(self, occupied: bool, d_rel: float, y_rel: float, v_rel: float, v_ego: float) -> None:
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
      self.v_rel, self.v_abs = float(v_rel), float(v_ego + v_rel)
    elif not self.occupied:
      self.d_rel, self.y_rel, self.v_rel, self.v_abs = 0.0, 0.0, 0.0, NO_SPEED

  def observe_oncoming(self, d_rel: float, v_abs: float, memory_s: float) -> None:
    """Record a vehicle travelling the other way on this side. See ONCOMING_FRAMES: no debounce."""
    self.available = True
    self.oncoming = True
    self.oncoming_d_rel = float(d_rel)
    self.oncoming_v_abs = float(v_abs)
    self.oncoming_seconds = float(memory_s)

  def clear_oncoming(self) -> None:
    self.oncoming = False

  def decay_oncoming(self, dt: float) -> None:
    self.oncoming_seconds = max(0.0, self.oncoming_seconds - dt)

  @property
  def blocks_oncoming(self) -> bool:
    """Is this side the one they are driving on?

    Held rather than instantaneous: meeting a car is evidence about the road, not an event. The
    seconds after it has gone by are exactly when the lane looks most invitingly empty and is most
    certainly still theirs.
    """
    return self.oncoming_seconds > 0.0

  def blocks_move(self, beat_speed: float, margin: float) -> bool:
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
  def undivided(self) -> bool:
    """Either side is carrying opposing traffic. For the log and the display; the GATE is per
    side, because a road being two-way does not make both sides of it unusable."""
    return self.left.blocks_oncoming or self.right.blocks_oncoming

  @property
  def undivided_seconds(self) -> float:
    return max(self.left.oncoming_seconds, self.right.oncoming_seconds)

  def reset(self) -> None:
    self.left.reset()
    self.right.reset()

  def update(self, sm, v_ego: float, max_distance_m: float, dt: float = 0.05,
             memory_s: float = DEFAULT_ONCOMING_MEMORY_S) -> None:
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
    self.left.decay_oncoming(dt)
    self.right.decay_oncoming(dt)

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

    self.left.clear_oncoming()
    self.right.clear_oncoming()

    best: dict[str, object] = {'left': None, 'right': None}
    for p in tracks:
      if p.dRel <= 0 or p.dRel > max_distance_m:
        continue
      # Radar yRel is LEFT-POSITIVE -- the opposite of the camera frame the model works in. Flip
      # first, THEN subtract where the road actually goes at that distance, so the result is
      # offset-from-the-lane rather than offset-from-straight-ahead. See path_offset: skipping
      # this puts our own lead in the next lane on every curve.
      lat = -p.yRel - path_offset(model, p.dRel)
      if not (ADJACENT_MIN_M <= abs(lat) <= ADJACENT_MAX_M):
        continue
      # Camera frame now: negative is left.
      side = 'left' if lat < 0 else 'right'

      # Three kinds of thing live in that band, and the sign of the absolute ground speed is what
      # separates them. Order matters: oncoming is checked FIRST, because it is the only one of the
      # three that is a safety fact rather than a convenience one.
      v_abs = v_ego + p.vRel
      if v_abs < -MIN_ONCOMING_MS:
        # Travelling the other way. The lane to our left is not a passing lane -- it is the one
        # they are using. Note the band does the discriminating for free on a divided road: an
        # opposing carriageway sits 10 m or more away, well outside it, so a highway with a real
        # median never trips this while a two-lane road trips it on the first car we meet.
        (self.left if side == 'left' else self.right).observe_oncoming(p.dRel, v_abs, memory_s)
        self.oncoming_seen = True
        continue
      # Roadside furniture, not traffic. See MIN_MOVING_MS -- this radar publishes barriers and
      # sign gantries as ordinary tracks and nothing upstream tells them apart from cars.
      if v_abs < MIN_MOVING_MS:
        continue

      if best[side] is None or p.dRel < best[side].dRel:
        best[side] = p

    for name, obj in (('left', self.left), ('right', self.right)):
      p = best[name]
      obj.observe(p is not None,
                  p.dRel if p is not None else 0.0,
                  p.yRel if p is not None else 0.0,
                  p.vRel if p is not None else 0.0, v_ego)
