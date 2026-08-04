"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import random
import time

import pytest
from pytest_mock import MockerFixture

from cereal import custom
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit import LIMIT_MAX_MAP_DATA_AGE

from openpilot.common.constants import CV
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.speed_limit_resolver import SpeedLimitResolver, ALL_SOURCES
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.common import Policy, OffsetType, Fallback

SpeedLimitSource = custom.LongitudinalPlanSP.SpeedLimit.Source


def create_mock(properties, mocker: MockerFixture):
  mock = mocker.MagicMock()
  for _property, value in properties.items():
    setattr(mock, _property, value)
  return mock


def setup_sm_mock(mocker: MockerFixture):
  cruise_speed_limit = random.uniform(0, 120)
  live_map_data_limit = random.uniform(0, 120)

  car_state = create_mock({
    'gasPressed': False,
    'brakePressed': False,
    'standstill': False,
  }, mocker)
  car_state_sp = create_mock({
    'speedLimit': cruise_speed_limit,
  }, mocker)
  live_map_data = create_mock({
    'speedLimit': live_map_data_limit,
    'speedLimitValid': True,
    'speedLimitAhead': 0.,
    'speedLimitAheadValid': 0.,
    'speedLimitAheadDistance': 0.,
  }, mocker)
  # BluePilot: time.time(), not time.monotonic(). The field is a UNIX timestamp -- gpsLocation
  # publishes GPS time -- and feeding it a boot-relative clock is the same epoch confusion the
  # resolver itself had. It canceled out while the code was also using monotonic, so these tests
  # passed against broken code and the stale-data test below passed for the wrong reason.
  gps_data = create_mock({
    'unixTimestampMillis': time.time() * 1e3,
  }, mocker)
  sm_mock = mocker.MagicMock()
  sm_mock.__getitem__.side_effect = lambda key: {
    'carState': car_state,
    'liveMapDataSP': live_map_data,
    'carStateSP': car_state_sp,
    'gpsLocation': gps_data,
  }[key]
  return sm_mock


parametrized_policies = pytest.mark.parametrize(
  "policy, sm_key, function_key", [
    (Policy.car_state_only, 'carStateSP', SpeedLimitSource.car),
    (Policy.car_state_priority, 'carStateSP', SpeedLimitSource.car),
    (Policy.map_data_only, 'liveMapDataSP', SpeedLimitSource.map),
    (Policy.map_data_priority, 'liveMapDataSP', SpeedLimitSource.map),
  ],
  ids=lambda val: val.name if hasattr(val, 'name') else str(val)
)


@pytest.mark.parametrize("resolver_class", [SpeedLimitResolver])
class TestSpeedLimitResolverValidation:

  @pytest.mark.parametrize("policy", list(Policy), ids=lambda policy: policy.name)
  def test_initial_state(self, resolver_class, policy):
    resolver = resolver_class()
    resolver.policy = policy
    for source in ALL_SOURCES:
      if source in resolver.limit_solutions:
        assert resolver.limit_solutions[source] == 0.
        assert resolver.distance_solutions[source] == 0.

  @parametrized_policies
  def test_resolver(self, resolver_class, policy, sm_key, function_key, mocker: MockerFixture):
    resolver = resolver_class()
    resolver.policy = policy
    sm_mock = setup_sm_mock(mocker)
    source_speed_limit = sm_mock[sm_key].speedLimit

    # Assert the resolver
    resolver.update(source_speed_limit, sm_mock)
    assert resolver.speed_limit == source_speed_limit
    assert resolver.source == ALL_SOURCES[function_key]

  def test_resolver_combined(self, resolver_class, mocker: MockerFixture):
    resolver = resolver_class()
    resolver.policy = Policy.combined
    sm_mock = setup_sm_mock(mocker)
    socket_to_source = {'carStateSP': SpeedLimitSource.car, 'liveMapDataSP': SpeedLimitSource.map}
    minimum_key, minimum_speed_limit = min(
      ((key, sm_mock[key].speedLimit) for key in
       socket_to_source.keys()), key=lambda x: x[1])

    # Assert the resolver
    resolver.update(minimum_speed_limit, sm_mock)
    assert resolver.speed_limit == minimum_speed_limit
    assert resolver.source == socket_to_source[minimum_key]

  @parametrized_policies
  def test_parser(self, resolver_class, policy, sm_key, function_key, mocker: MockerFixture):
    resolver = resolver_class()
    resolver.policy = policy
    sm_mock = setup_sm_mock(mocker)
    source_speed_limit = sm_mock[sm_key].speedLimit

    # Assert the parsing
    resolver.update(source_speed_limit, sm_mock)
    assert resolver.limit_solutions[ALL_SOURCES[function_key]] == source_speed_limit
    assert resolver.distance_solutions[ALL_SOURCES[function_key]] == 0.

  @pytest.mark.parametrize("policy", list(Policy), ids=lambda policy: policy.name)
  def test_resolve_interaction_in_update(self, resolver_class, policy, mocker: MockerFixture):
    v_ego = 50
    resolver = resolver_class()
    resolver.policy = policy

    sm_mock = setup_sm_mock(mocker)
    resolver.update(v_ego, sm_mock)

    # After resolution
    assert resolver.speed_limit is not None
    assert resolver.distance is not None
    assert resolver.source is not None

  @pytest.mark.parametrize("policy", list(Policy), ids=lambda policy: policy.name)
  def test_old_map_data_ignored(self, resolver_class, policy, mocker: MockerFixture):
    resolver = resolver_class()
    resolver.policy = policy
    sm_mock = mocker.MagicMock()
    sm_mock['gpsLocation'].unixTimestampMillis = (time.time() - 2 * LIMIT_MAX_MAP_DATA_AGE) * 1e3
    resolver._get_from_map_data(sm_mock)
    assert resolver.limit_solutions[SpeedLimitSource.map] == 0.
    assert resolver.distance_solutions[SpeedLimitSource.map] == 0.


class TestBandedOffset:
  """BluePilot: one offset per speed band, the owner's own habit as the default -- 2 over in a
  20-25, 5 over from 30-60, 10 over at 65+."""

  @staticmethod
  def _resolver(**overrides):
    r = SpeedLimitResolver()
    r.offset_type = int(OffsetType.bySpeed)
    r.is_metric = False
    r.offset_low, r.offset_mid, r.offset_high = 2, 5, 10
    r.offset_mid_threshold, r.offset_high_threshold = 30, 65
    for k, v in overrides.items():
      setattr(r, k, v)
    return r

  @pytest.mark.parametrize("limit_mph,expected_mph", [
    (20, 2), (25, 2),          # slow band
    (29, 2),                   # just under the first breakpoint
    (30, 5), (45, 5), (60, 5),  # medium band
    (64, 5),                   # just under the second
    (65, 10), (80, 10),        # fast band
  ])
  def test_band_boundaries(self, limit_mph, expected_mph):
    r = self._resolver(speed_limit=limit_mph * CV.MPH_TO_MS)
    assert round(r._get_speed_limit_offset() * CV.MS_TO_MPH) == expected_mph

  def test_offset_keys_off_the_posted_limit_not_the_car(self):
    """Keying on v_ego would make the offset drift as the car slowed for traffic."""
    fast = self._resolver(speed_limit=70 * CV.MPH_TO_MS, v_ego=5.0)
    assert round(fast._get_speed_limit_offset() * CV.MS_TO_MPH) == 10

  def test_metric_units_are_honoured_end_to_end(self):
    r = self._resolver(is_metric=True, speed_limit=50 * CV.KPH_TO_MS)
    # 50 km/h sits above the 30 breakpoint and below 65, so the medium band applies.
    assert round(r._get_speed_limit_offset() * CV.MS_TO_KPH) == 5

  def test_other_offset_types_are_untouched(self):
    r = self._resolver(offset_type=int(OffsetType.off))
    assert r._get_speed_limit_offset() == 0


class TestNoLimitFallback:
  """BluePilot: leaving I-215, the set speed stayed at 70 down the ramp and along a residential
  street until OSM had data again. The last known limit was driving a road it knew nothing about."""

  @staticmethod
  def _resolver(fallback, limit, last):
    r = SpeedLimitResolver()
    r.fallback = int(fallback)
    r.speed_limit = limit
    r.speed_limit_last = last
    return r

  def test_set_speed_drops_the_stale_limit_immediately(self):
    r = self._resolver(Fallback.setSpeed, 0.0, 70 * CV.MPH_TO_MS)
    assert not r.speed_limit_valid
    assert not r.speed_limit_last_valid, "the freeway's limit must not survive leaving the freeway"

  def test_last_known_keeps_it(self):
    r = self._resolver(Fallback.lastKnown, 0.0, 70 * CV.MPH_TO_MS)
    assert r.speed_limit_last_valid, "upstream behaviour must still be selectable"

  def test_a_live_limit_is_valid_under_either_fallback(self):
    for fb in (Fallback.setSpeed, Fallback.lastKnown):
      r = self._resolver(fb, 35 * CV.MPH_TO_MS, 70 * CV.MPH_TO_MS)
      assert r.speed_limit_valid and r.speed_limit_last_valid

  def test_no_limit_ever_seen_is_invalid_either_way(self):
    for fb in (Fallback.setSpeed, Fallback.lastKnown):
      assert not self._resolver(fb, 0.0, 0.0).speed_limit_last_valid
