"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

BluePilot: tests for the learned-location store.

Two of these are load-bearing in a way the rest are not.

test_lookup_cost_does_not_grow_with_the_store is the answer to "will this overload the device". The
owner is running ICBM and Passing Assist on the same hardware, and pinned_holds.py already records
what a linear scan at control rate costs -- "tens of thousands of trig calls a second" for a couple
of hundred pins. This store can hold ten times that. So the test counts distance calculations
rather than timing anything: a wall-clock assertion is flaky on a loaded machine and proves nothing
about the algorithm, while the call count is exact and is the thing that actually scales.

test_a_speed_trap_is_not_mistaken_for_a_door_opener is the feature working at all. If the hit ratio
does not separate those two, every real enforcement spot gets muted as a false alarm and the whole
store is worse than nothing.
"""

import json
import pathlib

import pytest

from openpilot.sunnypilot.selfdrive.controls.lib.radar_detector import locations as loc_mod
from openpilot.sunnypilot.selfdrive.controls.lib.radar_detector.esp_protocol import BAND_KA
from openpilot.sunnypilot.selfdrive.controls.lib.radar_detector.locations import (
  DECAY_AFTER_QUIET_PASSES, MAX_LOCATIONS, MIN_OBSERVATIONS_TO_MUTE, RadarLocations,
)

# Somewhere in Salt Lake City, and a point far enough away to be a different place.
LAT, LON = 40.7608, -111.8910
FAR_LAT, FAR_LON = 40.9000, -111.7000


def drive_past(store, lat, lon, times, alerted, bands=BAND_KA, laser=False):
  for _ in range(times):
    store.observe(lat, lon, alerted, bands=bands, laser=laser)


class TestItScales:
  def test_lookup_cost_does_not_grow_with_the_store(self, monkeypatch):
    """The performance guarantee, asserted as an algorithm property rather than a stopwatch."""
    calls = {"n": 0}
    real = loc_mod.distance_m

    def counting(*a):
      calls["n"] += 1
      return real(*a)

    store = RadarLocations()
    # Fill to capacity, spread over roughly a degree in each direction -- a few hundred kilometres,
    # which is far more spread out than a lifetime of one person's driving.
    for i in range(MAX_LOCATIONS):
      store.observe(40.0 + (i % 200) * 0.005, -112.0 + (i // 200) * 0.005, True)
    assert len(store.places) == MAX_LOCATIONS

    monkeypatch.setattr(loc_mod, "distance_m", counting)
    store.near(40.5, -111.75)
    # Nine cells' worth of candidates, not two thousand. The exact number depends on how the fill
    # above lands in the grid; the point is that it is bounded by cell occupancy and not by size.
    assert calls["n"] < 100, f"{calls['n']} distance calls -- the grid index is not working"

  def test_memory_is_bounded(self):
    store = RadarLocations()
    for i in range(MAX_LOCATIONS + 500):
      store.observe(40.0 + i * 0.002, -112.0, True)
    assert len(store.places) <= MAX_LOCATIONS

  def test_eviction_never_drops_a_deliberate_mark(self):
    """Manual marks and laser spots cannot be re-learned by driving past, so they outrank anything
    the store picked up on its own."""
    store = RadarLocations()
    store.observe(LAT, LON, True, laser=True)
    store.observe(LAT + 0.05, LON, True, manual=True)
    for i in range(MAX_LOCATIONS + 50):
      store.observe(41.0 + i * 0.002, -112.0, True)
    kept = [(p["laser"], p["manual"]) for p in store.places if p["laser"] or p["manual"]]
    assert (True, False) in kept
    assert (False, True) in kept


class TestTellingThemApart:
  def test_a_speed_trap_is_not_mistaken_for_a_door_opener(self):
    """The whole feature in one test.

    A supermarket door fires every single time. A patrol car does not -- nobody works one spot all
    day. Get this wrong and every real enforcement position gets muted as a false alarm.

    Ordered the way it happens on the road: a place does not exist until something alerts there, so
    the quiet passes that define it can only accumulate afterwards. Writing this test with the quiet
    passes first is what surfaced that -- and with it, the fact that a brand-new spot is briefly
    indistinguishable from a door until those passes arrive. See MIN_OBSERVATIONS_TO_MUTE.
    """
    store = RadarLocations()
    drive_past(store, LAT, LON, 20, alerted=True)              # door: 20 for 20

    store.observe(FAR_LAT, FAR_LON, True)                      # trap: first sighting creates it
    for _ in range(16):
      store.observe(FAR_LAT, FAR_LON, False)                   # quiet most days...
    for _ in range(3):
      store.observe(FAR_LAT, FAR_LON, True)                    # ...and there sometimes

    door = store.near(LAT, LON)[0]
    trap = store.near(FAR_LAT, FAR_LON)[0]
    assert store.classify(door) == "false_alarm"
    assert store.classify(trap) == "enforcement"
    assert store.should_mute(LAT, LON)
    assert not store.should_mute(FAR_LAT, FAR_LON)

  def test_quiet_passes_are_what_make_the_difference(self):
    """Without counting the times nothing happened, both look identical -- 4 alerts each -- and the
    trap would be indistinguishable from a door seen four times."""
    store = RadarLocations()
    drive_past(store, LAT, LON, 4, alerted=True)
    assert store.classify(store.near(LAT, LON)[0]) == "false_alarm"
    for _ in range(16):
      store.observe(LAT, LON, False)
    assert store.classify(store.near(LAT, LON)[0]) == "enforcement"

  def test_muting_needs_more_evidence_than_warning(self):
    """The two mistakes do not cost the same.

    Warning about a supermarket door is an annoyance. Muting a patrol car is a ticket, and it is
    SILENT -- the driver never learns the system decided not to tell them. So a place can read as a
    false alarm, and stop the car reacting to it, well before it has earned the right to take the
    driver's own warning away.
    """
    store = RadarLocations()
    drive_past(store, LAT, LON, MIN_OBSERVATIONS_TO_MUTE - 1, alerted=True)
    place = store.near(LAT, LON)[0]
    assert store.classify(place) == "false_alarm"
    assert not store.should_mute(LAT, LON)

    drive_past(store, LAT, LON, 1, alerted=True)
    assert store.should_mute(LAT, LON)

  def test_one_sighting_is_a_coincidence(self):
    store = RadarLocations()
    store.observe(LAT, LON, True)
    assert store.classify(store.near(LAT, LON)[0]) == "unknown"
    assert store.warnings(LAT, LON) == []

  def test_too_rare_to_warn_about(self):
    """Seen once in fifty passes is noise, and warning about it teaches the driver to ignore the
    warnings that matter."""
    store = RadarLocations()
    store.observe(LAT, LON, True)
    for _ in range(49):
      store.observe(LAT, LON, False)
    assert store.classify(store.near(LAT, LON)[0]) == "unknown"

  def test_laser_is_enforcement_however_rare(self):
    """A lidar gun is aimed, so most passes produce no detection at all -- the hit ratio is
    meaningless here and would throw away a position an officer works daily."""
    store = RadarLocations()
    store.observe(LAT, LON, True, laser=True)
    for _ in range(200):
      store.observe(LAT, LON, False)
    place = store.near(LAT, LON)[0]
    assert store.classify(place) == "enforcement"
    assert store.warnings(LAT, LON)

  def test_a_manual_mark_is_trusted_immediately(self):
    """The driver saw a cruiser. That outranks any amount of statistics."""
    store = RadarLocations()
    store.observe(LAT, LON, False, manual=True)
    assert store.classify(store.near(LAT, LON)[0]) == "enforcement"


class TestPassCounting:
  def test_a_drive_through_counts_once_not_once_per_frame(self):
    """Called at match rate, so without the inside/outside edge a single pass would count dozens of
    times and the ratio would be meaningless."""
    store = RadarLocations()
    store.observe(LAT, LON, True)
    before = store.near(LAT, LON)[0]["passes"]
    for _ in range(30):                       # sitting at a light inside the radius
      store.update_pass(LAT, LON, alerted=False)
    assert store.near(LAT, LON)[0]["passes"] == before
    store.update_pass(FAR_LAT, FAR_LON, alerted=False)   # left the area
    assert store.near(LAT, LON)[0]["passes"] == before + 1

  def test_a_quiet_place_is_not_created_out_of_nothing(self):
    """Driving somewhere with no alert must not manufacture a record, or the store would fill with
    every road ever driven."""
    store = RadarLocations()
    store.observe(LAT, LON, False)
    assert store.places == []

  def test_no_fix_is_never_a_place(self):
    """0,0 is a fix that never arrived. Without this guard every drive piles observations onto one
    imaginary island until it outvotes everything real."""
    store = RadarLocations()
    store.observe(0.0, 0.0, True)
    assert store.places == []
    assert store.near(0.0, 0.0) == []


class TestItDoesNotManufactureItsOwnEvidence:
  """The feedback loop that would have eaten the store from the inside.

  Learned as a false alarm -> we mute the detector -> the detector is quiet -> we count a quiet pass
  -> the ratio falls -> it stops looking like a false alarm -> we stop muting -> it alerts again.
  A weeks-long oscillation caused entirely by our own action, visible only on the road.

  Same shape as the "is Ford braking" trap in unconfirmed_lead.py, and found by reading it.
  """

  def _muted_place(self):
    store = RadarLocations()
    drive_past(store, LAT, LON, MIN_OBSERVATIONS_TO_MUTE, alerted=True)
    assert store.should_mute(LAT, LON)
    return store

  def test_a_pass_we_silenced_is_not_evidence_of_quiet(self):
    store = self._muted_place()
    before = dict(store.near(LAT, LON)[0])
    for _ in range(50):
      store.observe(LAT, LON, alerted=False, suppressed=True)
    after = store.near(LAT, LON)[0]
    assert after["passes"] == before["passes"]
    assert after["quiet"] == before["quiet"]

  def test_the_mute_does_not_oscillate(self):
    """The symptom, asserted directly: a muted place stays muted instead of flipping back."""
    store = self._muted_place()
    for _ in range(200):
      store.update_pass(LAT, LON, alerted=False, suppressed=True)
      store.update_pass(FAR_LAT, FAR_LON, alerted=False, suppressed=True)   # leave the area
      assert store.should_mute(LAT, LON)

  def test_a_suppressed_place_does_not_decay_away(self):
    """Decay counts quiet passes, so without the guard our own mute would eventually delete the
    very record that caused it."""
    store = self._muted_place()
    for _ in range(DECAY_AFTER_QUIET_PASSES * 3):
      store.observe(LAT, LON, alerted=False, suppressed=True)
    assert store.decay() == 0

  def test_an_alert_that_survives_a_mute_still_counts(self):
    """reqMuteOn only lasts until the V1 stops tracking, so a NEW signal at a muted place can still
    alert -- and that is real evidence, not something we manufactured."""
    store = self._muted_place()
    before = store.near(LAT, LON)[0]["alerts"]
    store.observe(LAT, LON, alerted=True, suppressed=True)
    assert store.near(LAT, LON)[0]["alerts"] == before + 1


class TestDecay:
  def test_a_stale_spot_is_forgotten(self):
    """A trap that was real last year becomes a warning you learn to ignore, and one you ignore is
    worse than none."""
    store = RadarLocations()
    store.observe(LAT, LON, True)
    for _ in range(DECAY_AFTER_QUIET_PASSES + 1):
      store.observe(LAT, LON, False)
    assert store.decay() == 1
    assert store.places == []

  def test_laser_and_manual_marks_do_not_decay(self):
    store = RadarLocations()
    store.observe(LAT, LON, True, laser=True)
    store.observe(FAR_LAT, FAR_LON, False, manual=True)
    for _ in range(DECAY_AFTER_QUIET_PASSES * 3):
      store.observe(LAT, LON, False)
      store.observe(FAR_LAT, FAR_LON, False)
    assert store.decay() == 0
    assert len(store.places) == 2

  def test_a_reconfirmed_spot_survives(self):
    store = RadarLocations()
    store.observe(LAT, LON, True)
    for _ in range(DECAY_AFTER_QUIET_PASSES - 1):
      store.observe(LAT, LON, False)
    store.observe(LAT, LON, True)        # seen again; the quiet run resets
    for _ in range(DECAY_AFTER_QUIET_PASSES - 1):
      store.observe(LAT, LON, False)
    assert store.decay() == 0


class TestStorage:
  def test_round_trips_through_a_file(self, tmp_path):
    path = str(tmp_path / "places.json")
    store = RadarLocations(path)
    drive_past(store, LAT, LON, 5, alerted=True)
    store.save()

    reloaded = RadarLocations(path)
    assert len(reloaded.places) == 1
    assert reloaded.near(LAT, LON)

  def test_a_corrupt_store_means_no_places_not_a_crash(self, tmp_path):
    path = tmp_path / "places.json"
    path.write_text("{ this is not json")
    store = RadarLocations(str(path))
    assert store.places == []

  def test_garbage_entries_are_skipped_individually(self, tmp_path):
    path = tmp_path / "places.json"
    path.write_text(json.dumps([{"lat": "nonsense"}, {"lat": LAT, "lon": LON, "alerts": 3}]))
    store = RadarLocations(str(path))
    assert len(store.places) == 1


class TestExport:
  def test_geojson_is_viewable_without_writing_a_map(self):
    """The point of choosing GeoJSON: the answer to "can I see this on a map" is yes today, in
    geojson.io or Google My Maps, with no on-device map screen written."""
    store = RadarLocations()
    drive_past(store, LAT, LON, 20, alerted=True)
    gj = store.to_geojson()
    assert gj["type"] == "FeatureCollection"
    feat = gj["features"][0]
    # GeoJSON is longitude first. Getting this backwards puts Utah in Kazakhstan and looks fine
    # until someone opens the file.
    assert feat["geometry"]["coordinates"] == [pytest.approx(LON), pytest.approx(LAT)]
    assert feat["properties"]["kind"] == "false_alarm"
    assert 0.0 <= feat["properties"]["hit_ratio"] <= 1.0
    json.dumps(gj)   # must be serialisable as-is


class TestSavingOffTheControlPath:
  def test_async_save_lands_on_disk(self, tmp_path):
    import time as _t
    path = str(tmp_path / "places.json")
    store = RadarLocations(path)
    drive_past(store, LAT, LON, 4, alerted=True)
    store.save_async()
    for _ in range(200):                       # the writer is a separate thread
      if RadarLocations(path).places:
        break
      _t.sleep(0.01)
    assert RadarLocations(path).places

  def test_the_snapshot_isolates_the_writer_from_live_edits(self, tmp_path):
    """A pass recorded while a save is in flight must not produce a half-mutated file. The snapshot
    is taken on the caller's thread, so the writer never sees the live list at all."""
    path = str(tmp_path / "places.json")
    store = RadarLocations(path)
    drive_past(store, LAT, LON, 4, alerted=True)
    snapshot = [dict(p) for p in store.places]
    store.places[0]["alerts"] = 999            # mutate after snapshotting
    store.save(snapshot)
    assert RadarLocations(path).places[0]["alerts"] == 4


class TestApproachWarning:
  """When to actually make a sound.

  Every filter here exists to stop the warning becoming noise. A warning that cries wolf is worse
  than none, because the one that matters arrives after the driver has learned to ignore it -- the
  same conclusion the ICBM alerts reached from the other direction.
  """

  V_EGO = 31.0        # ~70 mph
  NORTH, EAST = 0.0, 90.0

  def _trap(self, dlat=0.0, dlon=0.0):
    store = RadarLocations()
    store.observe(LAT + dlat, LON + dlon, True)
    for _ in range(16):
      store.observe(LAT + dlat, LON + dlon, False)
    for _ in range(3):
      store.observe(LAT + dlat, LON + dlon, True)
    assert store.classify(store.near(LAT + dlat, LON + dlon)[0]) == "enforcement"
    return store

  def test_warns_about_a_place_ahead(self):
    store = self._trap(dlat=0.004)                    # ~440 m north
    assert store.approaching(LAT, LON, self.NORTH, self.V_EGO)

  def test_says_nothing_about_a_place_behind(self):
    """Otherwise every mark fires again on the way home."""
    store = self._trap(dlat=-0.004)                   # ~440 m south
    assert store.approaching(LAT, LON, self.NORTH, self.V_EGO) is None

  def test_says_nothing_about_a_place_off_to_the_side(self):
    """The surface street below the freeway, and the opposite carriageway."""
    store = self._trap(dlon=0.006)
    assert store.approaching(LAT, LON, self.NORTH, self.V_EGO) is None

  def test_reaches_far_enough_at_speed(self):
    """20 s at 70 mph is over 600 m, which is more than one grid cell -- searching only the
    immediate ring would silently miss most places and look like "it never fires"."""
    store = self._trap(dlat=0.005)                    # ~555 m north
    assert store.approaching(LAT, LON, self.NORTH, self.V_EGO)

  def test_does_not_reach_that_far_in_town(self):
    """Lead time, not distance: the same place is not announced at 25 mph until much closer."""
    store = self._trap(dlat=0.005)
    assert store.approaching(LAT, LON, self.NORTH, 11.0) is None

  def test_nothing_once_you_are_on_top_of_it(self):
    store = self._trap()
    assert store.approaching(LAT, LON, self.NORTH, self.V_EGO) is None

  def test_stationary_says_nothing(self):
    """Heading is meaningless at a standstill, and there is nothing to warn about anyway."""
    store = self._trap(dlat=0.004)
    assert store.approaching(LAT, LON, self.NORTH, 0.0) is None

  def test_a_false_alarm_is_never_announced(self):
    """The store's other half. Warning about a supermarket door is exactly how this feature would
    teach its owner to stop listening."""
    store = RadarLocations()
    drive_past(store, LAT + 0.004, LON, 20, alerted=True)
    assert store.approaching(LAT, LON, self.NORTH, self.V_EGO) is None

  def test_the_nearest_of_several_wins(self):
    store = self._trap(dlat=0.004)
    for _ in range(20):
      store.observe(LAT + 0.002, LON, True)
    for _ in range(80):
      store.observe(LAT + 0.002, LON, False)
    got = store.approaching(LAT, LON, self.NORTH, self.V_EGO)
    assert abs(got["lat"] - (LAT + 0.002)) < 1e-6

  def test_a_laser_spot_is_announced_even_though_it_rarely_alerts(self):
    """The case the whole warning exists for: you cannot be told about lidar in the moment."""
    store = RadarLocations()
    store.observe(LAT + 0.004, LON, True, laser=True)
    for _ in range(200):
      store.observe(LAT + 0.004, LON, False)
    assert store.approaching(LAT, LON, self.NORTH, self.V_EGO)


class TestSearchReach:
  """Direct cover for _neighbors_within, because the approach tests cannot reach it.

  At the shipped constants a 20-second lead never leaves the nine-cell ring, so forcing the span to
  1 breaks nothing -- which is exactly why this is tested here instead. The calculation guards a
  future change to WARN_LEAD_S or CELL_DEG, and a guard nothing exercises is a guard that rots.
  """

  def test_a_short_reach_is_the_nine_cell_ring(self):
    assert len(list(loc_mod._neighbors_within(LAT, LON, 100.0))) == 9

  def test_a_long_reach_widens(self):
    near = set(loc_mod._neighbors_within(LAT, LON, 100.0))
    far = set(loc_mod._neighbors_within(LAT, LON, 5000.0))
    assert far > near

  def test_it_covers_what_it_claims_to(self):
    """The property that matters: anything within the reach is in some cell we will look at."""
    reach = 4000.0
    cells = set(loc_mod._neighbors_within(LAT, LON, reach))
    for dlat, dlon in ((0.03, 0), (-0.03, 0), (0, 0.03), (0, -0.03), (0.02, 0.02)):
      target = (LAT + dlat, LON + dlon)
      if loc_mod.distance_m(LAT, LON, *target) <= reach:
        assert loc_mod._cell(*target) in cells


class TestMapExport:
  def test_the_map_file_is_written_alongside_the_store(self, tmp_path):
    """Automatic, not a command to remember -- a map you have to generate is a map nobody opens."""
    import time as _t
    store = RadarLocations(str(tmp_path / "places.json"))
    drive_past(store, LAT, LON, 20, alerted=True)
    gj = tmp_path / "places.geojson"
    store.save_async(str(gj))
    for _ in range(200):
      if gj.exists():
        break
      _t.sleep(0.01)
    data = json.loads(gj.read_text())
    assert data["features"][0]["geometry"]["coordinates"][0] == pytest.approx(LON)

  def test_the_export_uses_the_same_snapshot_as_the_store(self, tmp_path):
    """Otherwise the map and the store could disagree about a place edited between the two writes."""
    store = RadarLocations(str(tmp_path / "places.json"))
    drive_past(store, LAT, LON, 4, alerted=True)
    snapshot = [dict(p) for p in store.places]
    store.places[0]["alerts"] = 999
    gj = str(tmp_path / "places.geojson")
    store.export_geojson(gj, snapshot)
    assert json.loads(pathlib.Path(gj).read_text())["features"][0]["properties"]["alerts"] == 4


class TestOnePassIsOneObservation:
  """The bug a code review caught before any hardware existed.

  update_pass ran at 1 Hz and recorded an observation on every cycle it was alerting, so a single
  drive-through of a 150 m radius at 70 mph logged five alerts and five passes. Alerts and passes
  inflated TOGETHER, which pins the hit ratio at 1.0 -- and a hit ratio of 1.0 is the definition of
  a false alarm. So every genuine speed trap that alerted on approach would classify as a
  supermarket door and get muted. The feature would have destroyed its own core discrimination.

  The existing drive-through test missed it because it passed alerted=False.
  """

  def _leave(self, store):
    store.update_pass(FAR_LAT, FAR_LON, alerted=False)

  def test_a_five_second_alerting_pass_counts_once(self):
    store = RadarLocations()
    store.observe(LAT, LON, True)                       # place exists, 1 alert / 1 pass
    for _ in range(5):
      store.update_pass(LAT, LON, alerted=True)         # five seconds inside, alerting
    self._leave(store)
    place = store.near(LAT, LON)[0]
    assert place["alerts"] == 2
    assert place["passes"] == 2

  def test_the_hit_ratio_survives_a_long_alerting_pass(self):
    """The symptom, asserted directly: a trap that alerts every time you approach must still read
    as intermittent, because most passes have no alert at all."""
    store = RadarLocations()
    store.observe(LAT, LON, True)
    for _ in range(16):                                 # sixteen quiet trips
      store.update_pass(LAT, LON, alerted=False)
      self._leave(store)
    for _ in range(3):                                  # three trips where it fired for ages
      for _ in range(6):
        store.update_pass(LAT, LON, alerted=True)
      self._leave(store)
    assert store.classify(store.near(LAT, LON)[0]) == "enforcement"

  def test_an_alert_early_in_a_pass_still_counts_at_the_end(self):
    """It must not matter whether something happened to be firing on the exact cycle we crossed the
    boundary -- that was the old behaviour and it turned real alerts into quiet passes."""
    store = RadarLocations()
    store.observe(LAT, LON, True)
    before = store.near(LAT, LON)[0]["alerts"]
    store.update_pass(LAT, LON, alerted=True)
    for _ in range(4):
      store.update_pass(LAT, LON, alerted=False)        # alert cleared before we left
    self._leave(store)
    assert store.near(LAT, LON)[0]["alerts"] == before + 1
    assert store.near(LAT, LON)[0]["quiet"] == 0

  def test_an_alert_on_open_road_creates_exactly_one_place(self):
    store = RadarLocations()
    for _ in range(5):
      store.update_pass(LAT, LON, alerted=True)
    self._leave(store)
    assert len(store.places) == 1
    assert store.places[0]["alerts"] == 1
    assert store.places[0]["passes"] == 1
