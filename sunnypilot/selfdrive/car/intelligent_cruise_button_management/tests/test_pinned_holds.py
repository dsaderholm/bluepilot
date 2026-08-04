"""BluePilot: holds pinned to a place.

The risky part of this feature is not the geometry, it is that a corrupt or hostile param must never
reach the control loop as anything but "no pins". It is read at control rate and its output edits
the set speed, so every parse failure has to degrade to doing nothing.
"""
import json

from openpilot.sunnypilot.selfdrive.car.intelligent_cruise_button_management.pinned_holds import (
  PinnedHolds, distance_m, DEFAULT_RADIUS_M, MIN_RADIUS_M, MAX_RADIUS_M, MAX_PINS,
)

# Two points on I-15 through Salt Lake, ~1 km apart.
LAT, LON = 40.7608, -111.8910
FAR_LAT, FAR_LON = 40.7698, -111.8910


class FakeParams:
  def __init__(self, **kv):
    self.store = {"IcbmPinnedHoldsEnabled": True, "IcbmPinnedHoldRadius": DEFAULT_RADIUS_M}
    self.store.update(kv)

  def get(self, key, *a, **k):
    return self.store.get(key)

  def get_bool(self, key, *a, **k):
    return bool(self.store.get(key, False))

  def put(self, key, value):
    self.store[key] = value

  def put_bool(self, key, value):
    self.store[key] = bool(value)


def fresh(**kv) -> PinnedHolds:
  ph = PinnedHolds(FakeParams(**kv))
  ph.update_params()
  return ph


class TestDistance:
  def test_haversine_is_in_metres(self):
    d = distance_m(LAT, LON, FAR_LAT, FAR_LON)
    assert 950 < d < 1050, f"expected ~1 km, got {d:.0f}"

  def test_zero_distance_to_self(self):
    assert distance_m(LAT, LON, LAT, LON) < 0.001


class TestParsingNeverCrashes:
  """Every one of these has to mean "no pins", not an exception in the control loop."""

  def test_garbage_shapes(self):
    for raw in (None, "", "not json", "[", "{}", "[1,2,3]", '[{"lat":"x"}]', "null", "[[]]",
                '[{"lon":1,"speed":50}]', '[{"lat":1,"lon":1}]', b"\xff\xfe bad bytes"):
      ph = fresh(IcbmPinnedHolds=raw)
      assert ph.pins == [], f"{raw!r} should have parsed to no pins"
      assert ph.match(LAT, LON) == 0

  def test_null_island_is_rejected(self):
    """0,0 is what a fix that never arrived looks like, not a place in the Gulf of Guinea."""
    ph = fresh(IcbmPinnedHolds=json.dumps([{"lat": 0.0, "lon": 0.0, "speed": 50}]))
    assert ph.pins == []

  def test_nonpositive_speed_rejected(self):
    ph = fresh(IcbmPinnedHolds=json.dumps([{"lat": LAT, "lon": LON, "speed": 0},
                                           {"lat": LAT, "lon": LON, "speed": -5}]))
    assert ph.pins == []

  def test_good_pins_among_bad_ones_survive(self):
    ph = fresh(IcbmPinnedHolds=json.dumps([{"bad": 1},
                                           {"lat": LAT, "lon": LON, "speed": 45},
                                           {"lat": "x", "lon": "y", "speed": 1}]))
    assert len(ph.pins) == 1 and ph.pins[0]["speed"] == 45

  def test_pin_count_is_bounded(self):
    many = [{"lat": LAT + i * 1e-3, "lon": LON, "speed": 40} for i in range(MAX_PINS + 50)]
    ph = fresh(IcbmPinnedHolds=json.dumps(many))
    assert len(ph.pins) == MAX_PINS


class TestMatching:
  def test_inside_the_radius_returns_the_speed(self):
    ph = fresh(IcbmPinnedHolds=json.dumps([{"lat": LAT, "lon": LON, "speed": 45}]))
    assert ph.match(LAT, LON) == 45

  def test_outside_the_radius_returns_nothing(self):
    ph = fresh(IcbmPinnedHolds=json.dumps([{"lat": LAT, "lon": LON, "speed": 45}]))
    assert ph.match(FAR_LAT, FAR_LON) == 0

  def test_disabled_matches_nothing(self):
    ph = fresh(IcbmPinnedHoldsEnabled=False,
               IcbmPinnedHolds=json.dumps([{"lat": LAT, "lon": LON, "speed": 45}]))
    assert ph.match(LAT, LON) == 0

  def test_no_fix_matches_nothing(self):
    """A dropped fix reads as 0,0. Matching there would fire a pin made at 0,0 -- and, worse,
    fire the NEAREST pin the moment the radius was wide enough to reach the origin."""
    ph = fresh(IcbmPinnedHolds=json.dumps([{"lat": LAT, "lon": LON, "speed": 45}]))
    assert ph.match(0.0, 0.0) == 0

  def test_nearest_pin_wins_when_two_overlap(self):
    ph = fresh(IcbmPinnedHolds=json.dumps([
      {"lat": LAT, "lon": LON, "speed": 45},
      {"lat": LAT + 2e-4, "lon": LON, "speed": 30},   # ~22 m away
    ]))
    assert ph.match(LAT, LON) == 45

  def test_radius_is_clamped_both_ways(self):
    assert fresh(IcbmPinnedHoldRadius=1).radius == MIN_RADIUS_M
    assert fresh(IcbmPinnedHoldRadius=99999).radius == MAX_RADIUS_M


class TestToggle:
  def test_adds_then_removes_at_the_same_place(self):
    ph = fresh()
    assert ph.toggle(LAT, LON, 45) == "added"
    assert ph.match(LAT, LON) == 45
    assert ph.toggle(LAT, LON, 45) == "removed"
    assert ph.match(LAT, LON) == 0

  def test_removing_does_not_need_a_hold(self):
    """Unpinning has to work from the badge whatever the current speed is."""
    ph = fresh()
    ph.toggle(LAT, LON, 45)
    assert ph.toggle(LAT, LON, 0) == "removed"

  def test_will_not_pin_without_a_hold(self):
    assert fresh().toggle(LAT, LON, 0) == "no_hold"

  def test_will_not_pin_without_a_fix(self):
    assert fresh().toggle(0.0, 0.0, 45) == "no_fix"

  def test_a_second_pin_far_away_is_added_not_swapped(self):
    ph = fresh()
    ph.toggle(LAT, LON, 45)
    assert ph.toggle(FAR_LAT, FAR_LON, 30) == "added"
    assert len(ph.pins) == 2
    assert ph.match(LAT, LON) == 45 and ph.match(FAR_LAT, FAR_LON) == 30

  def test_survives_a_round_trip_through_the_param(self):
    """The param is the only storage; a pin that cannot be re-read is not a pin."""
    ph = fresh()
    ph.toggle(LAT, LON, 45)
    reloaded = PinnedHolds(ph.params)
    reloaded.update_params()
    assert reloaded.match(LAT, LON) == 45

  def test_clear_removes_everything(self):
    ph = fresh()
    ph.toggle(LAT, LON, 45)
    ph.toggle(FAR_LAT, FAR_LON, 30)
    ph.clear()
    assert ph.pins == [] and ph.match(LAT, LON) == 0
