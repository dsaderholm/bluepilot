"""FusionPilot: holds pinned to a place.

The risky part of this feature is not the geometry, it is that a corrupt or hostile param must never
reach the control loop as anything but "no pins". It is read at control rate and its output edits
the set speed, so every parse failure has to degrade to doing nothing.
"""
import json

from openpilot.sunnypilot.selfdrive.car.intelligent_cruise_button_management.pinned_holds import (
  PinnedHolds, distance_m, DEFAULT_RADIUS_M, MIN_RADIUS_M, MAX_RADIUS_M, MAX_PINS,
  SUGGEST_AFTER,
)

# Two points on I-15 through Salt Lake, ~1 km apart.
LAT, LON = 40.7608, -111.8910
FAR_LAT, FAR_LON = 40.7698, -111.8910

# Declared JSON in common/params_keys.h, which decides what params_pyx will accept and return.
JSON_KEYS = ("IcbmPinnedHolds", "IcbmHoldObservations")


class FakeParams:
  def __init__(self, **kv):
    self.store = {"IcbmPinnedHoldsEnabled": True, "IcbmPinnedHoldRadius": DEFAULT_RADIUS_M}
    self.store.update(kv)

  def get(self, key, *a, **k):
    return self.store.get(key)

  def get_bool(self, key, *a, **k):
    return bool(self.store.get(key, False))

  # block=, because the real signature has it: params_pyx is `put(self, key, dat, bool block =
  # False)`. A stub without it turns any caller that passes block=True into a TypeError that the
  # caller's own except-clause swallows -- so the param silently never gets written and the test
  # reports a bug that exists only in the harness. That cost a drive once already.
  #
  # The value TYPE is checked for the same reason, and it is not hypothetical: both pin params are
  # declared JSON, params_pyx's PYTHON_2_CPP has (dict, JSON) and (list, JSON) and no (str, JSON),
  # and this stub used to accept the json.dumps string the writer was handing it. So every test
  # passed while every write on the car raised a TypeError into selfdrived's catch-all and no pin was
  # ever stored. A stub that is more permissive than the device proves nothing.
  def put(self, key, value, block=False):
    if key in JSON_KEYS and not isinstance(value, (list, dict)):
      raise TypeError(f"Type mismatch while writing param {key}: proposed_type={type(value)} "
                      f"expected_type=JSON")
    self.store[key] = value

  def put_bool(self, key, value, block=False):
    self.store[key] = bool(value)


class DeviceParams(FakeParams):
  """params_pyx end to end: a JSON key is TEXT in the store, encoded on write, decoded on read.

  FakeParams hands back whatever it was given, which is what makes the parser tests readable -- but
  it means nothing in those tests ever crosses the encode/decode boundary the device puts between a
  write and the next boot's read. This one does, so a round trip here is the real one.
  """

  def put(self, key, value, block=False):
    super().put(key, value, block)
    if key in JSON_KEYS:
      self.store[key] = json.dumps(value)

  def get(self, key, *a, **k):
    v = self.store.get(key)
    return json.loads(v) if key in JSON_KEYS and isinstance(v, str) and v else v


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


class TestItSurvivesTheDeviceStore:
  """The round trip the car actually performs, across the JSON encode/decode Params does for us.

  Everything else here reads and writes the same Python objects, so it cannot see the boundary where
  this feature was entirely broken: the writer handed Params a json.dumps STRING for a key declared
  JSON, which params_pyx rejects outright, and selfdrived's catch-all swallowed it. Enabled by
  default, documented on the settings screen, and it had never stored a single pin.
  """

  def test_a_pin_survives_a_reboot(self):
    p = DeviceParams()
    ph = PinnedHolds(p)
    ph.update_params()
    assert ph.toggle(LAT, LON, 45) == "added"
    assert isinstance(p.store["IcbmPinnedHolds"], str), "the store holds text, as the device does"

    reloaded = PinnedHolds(p)
    reloaded.update_params()
    assert reloaded.match(LAT, LON) == 45, "the pin did not survive the param store"

  def test_observations_survive_a_reboot(self):
    p = DeviceParams()
    ph = PinnedHolds(p)
    ph.update_params()
    for _ in range(SUGGEST_AFTER):
      ph.observe_hold(LAT, LON, 45)

    reloaded = PinnedHolds(p)
    reloaded.update_params()
    assert reloaded.suggestion(LAT, LON) == 45, "the evidence for a suggestion was lost on reboot"

  def test_clear_all_does_not_raise(self):
    """The settings-screen button, which has no try/except around it -- an exception here reaches
    the UI rather than being quietly absorbed like the control-loop path."""
    p = DeviceParams()
    ph = PinnedHolds(p)
    ph.update_params()
    ph.toggle(LAT, LON, 45)
    ph.clear()

    reloaded = PinnedHolds(p)
    reloaded.update_params()
    assert reloaded.pins == []


class TestSuggestions:
  """FusionPilot: noticing you keep correcting the same place.

  The safety property is that it only ever SUGGESTS -- nothing changes how the car drives until the
  driver taps. So the tests that matter most are the ones proving it does NOT suggest: too few
  observations, a different speed, or somewhere already pinned."""

  def test_suggests_only_after_enough_repeats(self):
    ph = fresh()
    for i in range(1, SUGGEST_AFTER):
      assert ph.observe_hold(LAT, LON, 45) == i
      assert ph.suggestion(LAT, LON) == 0, f"suggested after only {i} observations"
    assert ph.observe_hold(LAT, LON, 45) == SUGGEST_AFTER
    assert ph.suggestion(LAT, LON) == 45

  def test_different_speeds_do_not_accumulate(self):
    """Three different intentions near one place are not one intention."""
    ph = fresh()
    for speed in (30, 45, 70):
      ph.observe_hold(LAT, LON, speed)
    assert ph.suggestion(LAT, LON) == 0

  def test_close_speeds_do_accumulate(self):
    """Within tolerance is the same intention -- a driver does not hit the same number every time."""
    ph = fresh()
    for speed in (45, 46, 44):
      ph.observe_hold(LAT, LON, speed)
    assert ph.suggestion(LAT, LON) == 44, "the most recent intent should win"

  def test_observations_elsewhere_do_not_suggest_here(self):
    ph = fresh()
    for _ in range(SUGGEST_AFTER + 2):
      ph.observe_hold(FAR_LAT, FAR_LON, 45)
    assert ph.suggestion(LAT, LON) == 0

  def test_an_existing_pin_is_not_re_suggested(self):
    ph = fresh()
    ph.toggle(LAT, LON, 45)
    for _ in range(SUGGEST_AFTER + 2):
      assert ph.observe_hold(LAT, LON, 45) == 0, "a pinned place has nothing left to learn"
    assert ph.suggestion(LAT, LON) == 0

  def test_accepting_stops_it_asking_again(self):
    ph = fresh()
    for _ in range(SUGGEST_AFTER):
      ph.observe_hold(LAT, LON, 45)
    assert ph.suggestion(LAT, LON) == 45
    ph.toggle(LAT, LON, 45)                      # the tap
    assert ph.match(LAT, LON) == 45              # now a real pin
    assert ph.suggestion(LAT, LON) == 0          # and no longer offered

  def test_unpinning_also_forgets(self):
    """Otherwise removing a pin would immediately re-suggest the thing just rejected."""
    ph = fresh()
    for _ in range(SUGGEST_AFTER):
      ph.observe_hold(LAT, LON, 45)
    ph.toggle(LAT, LON, 45)
    ph.toggle(LAT, LON, 45)                      # tap again to remove
    assert ph.match(LAT, LON) == 0
    assert ph.suggestion(LAT, LON) == 0

  def test_no_fix_records_nothing(self):
    assert fresh().observe_hold(0.0, 0.0, 45) == 0

  def test_disabled_suggests_nothing(self):
    ph = fresh(IcbmPinnedHoldsEnabled=False)
    for _ in range(SUGGEST_AFTER + 2):
      ph.observe_hold(LAT, LON, 45)
    assert ph.suggestion(LAT, LON) == 0

  def test_corrupt_observations_mean_no_suggestions(self):
    ph = fresh(IcbmHoldObservations="not json at all")
    assert ph.observations == []
    assert ph.suggestion(LAT, LON) == 0
