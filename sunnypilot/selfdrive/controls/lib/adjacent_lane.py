"""
BluePilot: is the lane I would move into already occupied by something slow?

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

# Consecutive liveTracks messages a side must agree on before the reading is believed. Symmetric on
# purpose: the flicker drops tracks as often as it invents them, so debouncing only the appearing
# edge would still produce a jittery clear.
DEBOUNCE_FRAMES = 3

NO_SPEED = 0.0


class AdjacentLaneSide:
  """One side. Defaults to unavailable, which is not the same as clear."""

  def __init__(self):
    self.available = False
    self.occupied = False
    self.d_rel = 0.0
    self.y_rel = 0.0             # radar frame, left-positive
    self.v_rel = 0.0
    self.v_abs = NO_SPEED        # absolute speed of the nearest vehicle in that lane
    self._raw_occupied = False
    self._streak = 0

  def reset(self) -> None:
    self.__init__()

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

  @property
  def available(self) -> bool:
    return self.left.available or self.right.available

  def reset(self) -> None:
    self.left.reset()
    self.right.reset()

  def update(self, sm, v_ego: float, max_distance_m: float) -> None:
    """Scan liveTracks for the nearest vehicle in each adjacent lane.

    Nearest by dRel, because that is the one we would meet first. Anything beyond the passing
    look-ahead is ignored for the same reason the passing trigger ignores it: too far to be a
    decision yet.

    Holds the last reading on cycles with no new radar message; resets only when the service is
    dead or invalid. Between those two cases lies the whole distinction this module maintains --
    stale-but-real data is not the same as no sensor.
    """
    try:
      if not (sm.alive['liveTracks'] and sm.valid['liveTracks']):
        self.reset()
        return
      if not sm.updated['liveTracks']:
        return
      tracks = sm['liveTracks'].points
    except (KeyError, AttributeError):
      self.reset()
      return

    best: dict[str, object] = {'left': None, 'right': None}
    for p in tracks:
      if p.dRel <= 0 or p.dRel > max_distance_m:
        continue
      if not (ADJACENT_MIN_M <= abs(p.yRel) <= ADJACENT_MAX_M):
        continue
      # Roadside furniture, not traffic. See MIN_MOVING_MS -- this radar publishes barriers and
      # sign gantries as ordinary tracks and nothing upstream tells them apart from cars.
      if v_ego + p.vRel < MIN_MOVING_MS:
        continue
      # Radar yRel is LEFT-POSITIVE -- the opposite of the model's lane geometry. See the module
      # docstring; this one line is the easiest thing here to get backwards.
      side = 'left' if p.yRel > 0 else 'right'
      if best[side] is None or p.dRel < best[side].dRel:
        best[side] = p

    for name, obj in (('left', self.left), ('right', self.right)):
      p = best[name]
      obj.observe(p is not None,
                  p.dRel if p is not None else 0.0,
                  p.yRel if p is not None else 0.0,
                  p.vRel if p is not None else 0.0, v_ego)
