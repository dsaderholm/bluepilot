"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import time

import cereal.messaging as messaging
from cereal import custom
from openpilot.common.constants import CV
from openpilot.common.gps import get_gps_location_service
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot import PARAMS_UPDATE_PERIOD, get_sanitize_int_param
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit import LIMIT_MAX_MAP_DATA_AGE, LIMIT_ADAPT_ACC, MAX_FIX_AGE_S
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.common import Policy, OffsetType, Fallback

SpeedLimitSource = custom.LongitudinalPlanSP.SpeedLimit.Source

ALL_SOURCES = tuple(SpeedLimitSource.schema.enumerants.values())


class SpeedLimitResolver:
  limit_solutions: dict[custom.LongitudinalPlanSP.SpeedLimit.Source, float]
  distance_solutions: dict[custom.LongitudinalPlanSP.SpeedLimit.Source, float]
  v_ego: float
  speed_limit: float
  speed_limit_last: float
  speed_limit_final: float
  speed_limit_final_last: float
  distance: float
  source: custom.LongitudinalPlanSP.SpeedLimit.Source
  speed_limit_offset: float

  def __init__(self):
    self.params = Params()
    self.frame = -1

    self._gps_location_service = get_gps_location_service(self.params)
    self.limit_solutions = {}  # Store for speed limit solutions from different sources
    self.distance_solutions = {}  # Store for distance to current speed limit start for different sources

    self.policy = self.params.get("SpeedLimitPolicy", return_default=True)
    self.policy = get_sanitize_int_param(
      "SpeedLimitPolicy",
      Policy.min().value,
      Policy.max().value,
      self.params
    )
    self._policy_to_sources_map = {
      Policy.car_state_only: [SpeedLimitSource.car],
      Policy.map_data_only: [SpeedLimitSource.map],
      Policy.car_state_priority: [SpeedLimitSource.car, SpeedLimitSource.map],
      Policy.map_data_priority: [SpeedLimitSource.map, SpeedLimitSource.car],
      Policy.combined: [SpeedLimitSource.car, SpeedLimitSource.map],
    }
    self.source = SpeedLimitSource.none
    for source in ALL_SOURCES:
      self._reset_limit_sources(source)

    self.is_metric = self.params.get_bool("IsMetric")
    self.offset_type = get_sanitize_int_param(
      "SpeedLimitOffsetType",
      OffsetType.min().value,
      OffsetType.max().value,
      self.params
    )
    self.offset_value = self.params.get("SpeedLimitValueOffset", return_default=True)
    self.offset_low = self.params.get("SpeedLimitOffsetLow", return_default=True)
    self.offset_mid = self.params.get("SpeedLimitOffsetMid", return_default=True)
    self.offset_high = self.params.get("SpeedLimitOffsetHigh", return_default=True)
    self.offset_mid_threshold = self.params.get("SpeedLimitOffsetMidThreshold", return_default=True)
    self.offset_high_threshold = self.params.get("SpeedLimitOffsetHighThreshold", return_default=True)
    self.fallback = self.params.get("SpeedLimitFallback", return_default=True)
    self.lookahead_higher = self.params.get("SpeedLimitLookaheadHigher", return_default=True)

    self.speed_limit = 0.
    self.speed_limit_last = 0.
    self.speed_limit_final = 0.
    self.speed_limit_final_last = 0.
    self.speed_limit_offset = 0.

  def update_speed_limit_states(self) -> None:
    self.speed_limit_final = self.speed_limit + self.speed_limit_offset

    if self.speed_limit > 0.:
      self.speed_limit_last = self.speed_limit
      self.speed_limit_final_last = self.speed_limit_final

  @property
  def speed_limit_valid(self) -> bool:
    return self.speed_limit > 0.

  @property
  def speed_limit_last_valid(self) -> bool:
    """Is the REMEMBERED limit still something to act on?

    Under Fallback.setSpeed it is not, the moment the live limit goes away. That is the whole fix:
    a limit is a fact about the road you are on, and once no source can say what road that is,
    continuing to assert the last one is a guess dressed as data. Everything downstream reads this
    -- Speed Limit Assist stands down and the sign shows "---" -- which is the honest display.
    """
    if self.fallback == int(Fallback.setSpeed) and not self.speed_limit_valid:
      return False
    return self.speed_limit_last > 0.

  def update_params(self):
    if self.frame % int(PARAMS_UPDATE_PERIOD / DT_MDL) == 0:
      self.policy = self.params.get("SpeedLimitPolicy", return_default=True)
      self.is_metric = self.params.get_bool("IsMetric")
      self.offset_type = self.params.get("SpeedLimitOffsetType", return_default=True)
      self.offset_value = self.params.get("SpeedLimitValueOffset", return_default=True)
      self.offset_low = self.params.get("SpeedLimitOffsetLow", return_default=True)
      self.offset_mid = self.params.get("SpeedLimitOffsetMid", return_default=True)
      self.offset_high = self.params.get("SpeedLimitOffsetHigh", return_default=True)
      self.offset_mid_threshold = self.params.get("SpeedLimitOffsetMidThreshold", return_default=True)
      self.offset_high_threshold = self.params.get("SpeedLimitOffsetHighThreshold", return_default=True)
      self.fallback = self.params.get("SpeedLimitFallback", return_default=True)
      self.lookahead_higher = self.params.get("SpeedLimitLookaheadHigher", return_default=True)

  def _get_speed_limit_offset(self) -> float:
    if self.offset_type == OffsetType.off:
      return 0
    elif self.offset_type == OffsetType.fixed:
      return float(self.offset_value * (CV.KPH_TO_MS if self.is_metric else CV.MPH_TO_MS))
    elif self.offset_type == OffsetType.percentage:
      return float(self.offset_value * 0.01 * self.speed_limit)
    elif self.offset_type == OffsetType.bySpeed:
      # Banded by the POSTED limit, not by current speed: the offset is a statement about the road,
      # and keying it on v_ego would make it drift as the car slowed for traffic.
      # ROUNDED, and this is the whole bug that shipped: reported from a drive that a 30 mph zone
      # was getting the under-30 offset. The limit reaches here as a float in m/s and does not land
      # on a whole display unit -- 48 km/h converts to 29.825817 mph, which is < 30 and drops into
      # the slow band. Even a clean mph source only round-trips to 30.000000000000004, so the
      # comparison was riding on floating-point noise in whichever direction it happened to fall.
      #
      # Posted limits are whole numbers. Round to one before deciding which band it is in, and the
      # question stops being about representation at all.
      #
      # The test that should have caught it built its input as mph * MPH_TO_MS -- the same
      # conversion the code undoes -- so it could only ever prove the round trip was self-consistent.
      # Realistic map-derived values are now in the parametrisation.
      to_display = CV.MS_TO_KPH if self.is_metric else CV.MS_TO_MPH
      limit = round(self.speed_limit * to_display)
      if limit < self.offset_mid_threshold:
        offset = self.offset_low
      elif limit < self.offset_high_threshold:
        offset = self.offset_mid
      else:
        offset = self.offset_high
      return float(offset * (CV.KPH_TO_MS if self.is_metric else CV.MPH_TO_MS))
    else:
      raise NotImplementedError("Offset not supported")

  def _reset_limit_sources(self, source: custom.LongitudinalPlanSP.SpeedLimit.Source) -> None:
    self.limit_solutions[source] = 0.
    self.distance_solutions[source] = 0.

  def _get_from_car_state(self, sm: messaging.SubMaster) -> None:
    self._reset_limit_sources(SpeedLimitSource.car)
    self.limit_solutions[SpeedLimitSource.car] = sm['carStateSP'].speedLimit
    self.distance_solutions[SpeedLimitSource.car] = 0.


  def _refuse_a_car_limit_above_the_map(self) -> None:
    """FusionPilot: A CAR-SOURCED LIMIT MAY LOWER THE MAP'S, NEVER RAISE IT.

    Route 0000038e, 2026-08-18: `carStateSP.speedLimit` sat at a CONSTANT 80 mph for 16,991 frames
    -- through 70 mph freeway and 20 and 30 mph surface streets alike. That is not a sign being
    read, it is a stuck value. With `SpeedLimitPolicy` on `combined` the car source is consulted
    before the map, so it won 700 frames at 80 where the map correctly said 70, and his By-Limit
    offset turned that into the **90 mph** he reported -- a number on no road in Utah.

    TSR on this car has a KNOWN communication fault (U0253, the camera cannot reach the APIM; see
    CLAUDE.md). Its output is exactly what the fork's own rule is about: a limit is an instruction
    to change speed, so refusing one costs coverage while honoring a wrong one costs safety.
    Refusing a HIGHER car reading costs a slower car; honoring it asks for 90 on a residential
    street.

    Deliberately asymmetric rather than dropping the source. A car reading LOWER than the map is
    the case TSR exists for -- a work zone, a school zone, a limit the tiles do not carry -- and
    that still gets through untouched. Only the direction that can SPEED HIM UP is refused.

    Called after BOTH sources are resolved. `_get_from_car_state` runs first, so doing this inside
    it compared against the PREVIOUS frame's map limit -- right almost always and wrong at exactly
    the transitions where the two disagree, which is the only time it matters.
    """
    # NOT when he has explicitly chosen to trust the car source. `car_state_only` and
    # `car_state_priority` are a deliberate statement that TSR is the authority on this
    # vehicle, and silently overriding that would be the same class of mistake as the stuck
    # reading itself. His policy is `combined`, where the car merely happens to be consulted
    # first -- an ordering, not a preference.
    if self.policy in (int(Policy.car_state_only), int(Policy.car_state_priority)):
      return

    map_limit = self.limit_solutions.get(SpeedLimitSource.map, 0.)
    car_limit = self.limit_solutions.get(SpeedLimitSource.car, 0.)
    if map_limit > 0. and car_limit > map_limit:
      self.limit_solutions[SpeedLimitSource.car] = 0.
      self.distance_solutions[SpeedLimitSource.car] = 0.


  def _get_from_map_data(self, sm: messaging.SubMaster) -> None:
    self._reset_limit_sources(SpeedLimitSource.map)
    self._process_map_data(sm)

  def _process_map_data(self, sm: messaging.SubMaster) -> None:
    gps_data = sm[self._gps_location_service]
    map_data = sm['liveMapDataSP']

    # BluePilot: the SAME epoch mix-up fixed in _calculate_map_data_limits below, and it was still
    # here. time.monotonic() counts from boot (~1e4); unixTimestampMillis * 1e-3 counts from 1970
    # (~1.8e9), so this evaluated to about -1.8 billion and the guard could never be true. Map data
    # was therefore accepted at any age, including a fix minutes old with no satellites -- which is
    # exactly the state you are in under an overpass or in an interchange.
    gps_fix_age = time.time() - gps_data.unixTimestampMillis * 1e-3
    if gps_fix_age > LIMIT_MAX_MAP_DATA_AGE:
      return

    speed_limit = map_data.speedLimit if map_data.speedLimitValid else 0.
    next_speed_limit = map_data.speedLimitAhead if map_data.speedLimitAheadValid else 0.

    self._calculate_map_data_limits(sm, speed_limit, next_speed_limit)

  def _calculate_map_data_limits(self, sm: messaging.SubMaster, speed_limit: float, next_speed_limit: float) -> None:
    gps_data = sm[self._gps_location_service]
    map_data = sm['liveMapDataSP']

    # BluePilot: this is the FIXME below, and it is a clock-epoch mix-up. time.monotonic() counts
    # seconds since BOOT (~1e4); unixTimestampMillis * 1e-3 counts seconds since 1970 (~1.8e9).
    # Subtracting them gave a "fix age" of about -1.8 billion seconds, so distance_since_fix came
    # out around -5e10 m, distance_to_speed_limit_ahead became astronomically large, and the
    # early-adoption test below could never be true. The feature has never once run.
    #
    # Clamped as well as corrected: the device clock is GPS-disciplined (system/timed.py), so it
    # can be wrong early in a boot, and a bad clock must degrade to "no correction" rather than to
    # a wrong one. Beyond a couple of seconds the fix is stale and extrapolating from it is guesswork.
    fix_age = time.time() - gps_data.unixTimestampMillis * 1e-3
    fix_age = min(max(fix_age, 0.0), MAX_FIX_AGE_S)
    distance_since_fix = self.v_ego * fix_age
    distance_to_speed_limit_ahead = max(0., map_data.speedLimitAheadDistance - distance_since_fix)

    self.limit_solutions[SpeedLimitSource.map] = speed_limit
    self.distance_solutions[SpeedLimitSource.map] = 0.

    # Start easing down BEFORE the sign, so the new limit is met at the sign rather than a
    # hundred meters past it -- and at LIMIT_ADAPT_ACC = -1.0 m/s^2, which is deliberately under
    # the 1.3 m/s^2 that lights the stop lamps. Coast in, do not brake at the boundary.
    if 0. < next_speed_limit < self.v_ego:
      adapt_time = (next_speed_limit - self.v_ego) / LIMIT_ADAPT_ACC
      adapt_distance = self.v_ego * adapt_time + 0.5 * LIMIT_ADAPT_ACC * adapt_time ** 2

      if distance_to_speed_limit_ahead <= adapt_distance:
        self.limit_solutions[SpeedLimitSource.map] = next_speed_limit
        self.distance_solutions[SpeedLimitSource.map] = distance_to_speed_limit_ahead

    # BluePilot: the mirror case, which upstream does not have -- an upcoming limit that is HIGHER.
    #
    # Leaving a slow zone, the set speed only starts climbing once the car is past the sign, and
    # ICBM's rise limiter then walks it up in steps. The result is a long crawl out of a 35 zone
    # onto a 65 road. Adopting the higher limit a little early means the car is already at speed
    # where the faster road begins, which is where it needs to be.
    #
    # Time-based rather than the deceleration geometry used above, and deliberately so: slowing has
    # a correct answer set by physics -- meet the new limit at the sign -- while speeding up has no
    # such constraint. It is purely a question of how soon you want it, so it is a plain lead time.
    #
    # Bounded by the sign, never past it: adopting a 65 while still in the 35 is a ticket. The lead
    # time buys ICBM room to walk the set speed up, not permission to arrive early.
    elif next_speed_limit > speed_limit > 0. and self.lookahead_higher > 0:
      if distance_to_speed_limit_ahead <= self.v_ego * self.lookahead_higher:
        self.limit_solutions[SpeedLimitSource.map] = next_speed_limit
        self.distance_solutions[SpeedLimitSource.map] = distance_to_speed_limit_ahead

  def _get_source_solution_according_to_policy(self) -> custom.LongitudinalPlanSP.SpeedLimit.Source:
    sources_for_policy = self._policy_to_sources_map[self.policy]

    if self.policy != Policy.combined:
      # They are ordered in the order of preference, so we pick the first that's non-zero
      for source in sources_for_policy:
        if self.limit_solutions[source] > 0.:
          return source
      return SpeedLimitSource.none

    sources_with_limits = [(s, limit) for s, limit in [(s, self.limit_solutions[s]) for s in sources_for_policy] if limit > 0.]
    if sources_with_limits:
      return min(sources_with_limits, key=lambda x: x[1])[0]

    return SpeedLimitSource.none

  def _resolve_limit_sources(self, sm: messaging.SubMaster) -> tuple[float, float, custom.LongitudinalPlanSP.SpeedLimit.Source]:
    """Get limit solutions from each data source"""
    self._get_from_car_state(sm)
    self._get_from_map_data(sm)
    self._refuse_a_car_limit_above_the_map()

    source = self._get_source_solution_according_to_policy()
    speed_limit = self.limit_solutions[source] if source else 0.
    distance = self.distance_solutions[source] if source else 0.

    return speed_limit, distance, source

  def update(self, v_ego: float, sm: messaging.SubMaster) -> None:
    self.v_ego = v_ego
    self.update_params()

    self.speed_limit, self.distance, self.source = self._resolve_limit_sources(sm)
    self.speed_limit_offset = self._get_speed_limit_offset()

    self.update_speed_limit_states()

    self.frame += 1
