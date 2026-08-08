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
from openpilot.sunnypilot.selfdrive.controls.lib.radar_detector.alert_log import RadarAlertLog, file_writer
from openpilot.sunnypilot.selfdrive.controls.lib.radar_detector.esp_protocol import BAND_LASER, DisplayData, mute_off, mute_on
from openpilot.sunnypilot.selfdrive.controls.lib.radar_detector.locations import RadarLocations
from openpilot.sunnypilot.selfdrive.controls.lib.radar_detector.radar_alert import RadarAlertDetector
from openpilot.sunnypilot.selfdrive.controls.lib.radar_detector.transport import EspSerialReader

SpeedLimitSource = custom.LongitudinalPlanSP.SpeedLimit.Source

ALL_SOURCES = tuple(SpeedLimitSource.schema.enumerants.values())

# BluePilot: how often to reconsider whether the radar detector's serial link should be up. Two
# seconds at the 20 Hz model rate -- slow because the answer only changes when a setting is toggled
# or something is plugged in, and opening a port is not a per-frame decision.
_RADAR_LINK_CHECK_FRAMES = int(2.0 / DT_MDL)

# Where encounters are written for later review. On /data because that is the writable partition
# that survives a reboot, and it is meant to be pulled over SSH at home and then deleted -- nothing
# uploads it. file_writer swallows every error, so a dev machine with no /data simply logs nothing.
RADAR_LOG_PATH = "/data/radar_alerts.jsonl"
# Learned places. Separate file from the raw encounter log: the log is a firehose meant to be read
# once and deleted, while this is small, durable, and the thing the feature actually runs on.
RADAR_PLACES_PATH = "/data/radar_places.json"
# The same places as a map file, rewritten whenever the store is. Automatic rather than a command
# to remember, because a map you have to generate is a map nobody looks at. Opens directly in
# geojson.io, Google My Maps or QGIS.
RADAR_PLACES_GEOJSON = "/data/radar_places.geojson"
# Position matching cadence. 1 Hz -- see locations.py; 36 m of travel at 80 mph, far inside any
# useful radius, and it keeps the haversines off the 20 Hz loop.
_RADAR_MATCH_FRAMES = max(int(1.0 / DT_MDL), 1)
# How often the learned store is decayed and written back. Two minutes: rare enough that the disk
# write is irrelevant, often enough that a drive cut short by pulling the fuse loses almost nothing.
_RADAR_SAVE_FRAMES = max(int(120.0 / DT_MDL), 1)


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

    # BluePilot: radar detector. Lives here rather than in Speed Limit Assist because what it
    # produces is an OFFSET -- the same kind of thing SpeedLimitOffsetType produces -- and the
    # resolver is what owns turning a posted limit into a number to aim at.
    #
    # radar_display is written by the ESP transport and is None whenever the detector is not
    # connected, not powered, or not yet built. None means "cannot tell", which the detector treats
    # as a release rather than as a quiet road.
    self.radar_alert = RadarAlertDetector(self.params)
    self.radar_display: DisplayData | None = None
    self._radar_reader: EspSerialReader | None = None
    # Encounter log. Written from day one with the set-speed override switched off, because
    # RadarDetectorMinBars cannot be chosen from first principles -- see alert_log.py.
    self._radar_log = RadarAlertLog(file_writer(RADAR_LOG_PATH))
    self._radar_places = RadarLocations(RADAR_PLACES_PATH)
    # Whether WE are the reason the detector is quiet right now. Feeds the store so our own mute
    # is never mistaken for evidence that a place has gone quiet on its own.
    self._radar_muting = False
    # Set for one match cycle when a learned place comes into range ahead; the planner turns it
    # into the alert. Latched on the place so an approach announces once, not once a second.
    self.radar_place_ahead: dict | None = None
    self._radar_warned_key: tuple | None = None

  def _update_radar_places(self, sm: messaging.SubMaster) -> None:
    """Learn where the detector is right and where it cries wolf, and act on it.

    Runs at 1 Hz, not the model rate. At 80 mph that is 36 m between checks, far inside any radius
    worth using, and it keeps the position matching off the control loop -- see locations.py for
    why that matters on hardware already carrying ICBM and Passing Assist.

    The suppressed flag is the important one. While WE are muting the detector, its silence is not
    evidence that the place is quiet -- it is evidence of nothing at all, and counting it would set
    up a loop where our own mute erodes the record that caused it. See RadarLocations.observe.
    """
    # Cleared FIRST, every frame. This flag is read by the planner at 20 Hz but only written on the
    # 1 Hz match cycle, so leaving it set meant one approach raised the alert on twenty consecutive
    # frames -- the "once per approach" latch was working and the flag underneath it was not.
    self.radar_place_ahead = None

    if self.frame % _RADAR_MATCH_FRAMES != 0:
      return
    try:
      gps = sm[self._gps_location_service]
      lat, lon = gps.latitude, gps.longitude
    except Exception:  # noqa: BLE001 - no fix, no message, no learning. Never an exception here.
      return

    d = self.radar_display
    alerting = d is not None and d.searching and bool(d.bands)
    self._radar_places.update_pass(lat, lon, alerting,
                                   bands=d.bands if d else 0,
                                   laser=bool(d.bands & BAND_LASER) if d else False,
                                   suppressed=self._radar_muting)

    # Laser marks itself, and it does so THROUGH update_pass above rather than with a second
    # observe() call. That is what the laser flag on update_pass is for -- an extra observe here
    # recorded the same drive-through twice, on top of the per-cycle inflation update_pass used to
    # have. You cannot react to laser anyway (by the time it fires you have been measured), which is
    # exactly why it is recorded automatically instead of prompting for a tap.

    self._update_radar_mute(lat, lon)
    self._announce_radar_place(lat, lon, getattr(gps, "bearingDeg", 0.0))

    # Decay and persist on a slow cadence. Without this the whole store lives only in RAM and every
    # reboot throws away everything learned, which would have been a quiet and thoroughly
    # demoralising way for the feature to do nothing.
    #
    # save_async, not save: a full store is a couple of hundred kilobytes of JSON and encoding it
    # inline would drop a frame on a 20 Hz loop. Only the snapshot happens here.
    if self.frame % _RADAR_SAVE_FRAMES == 0:
      self._radar_places.decay()
      self._radar_places.save_async(RADAR_PLACES_GEOJSON)

  def _announce_radar_place(self, lat: float, lon: float, bearing_deg: float) -> None:
    """Flag a learned place we are heading into. Once per approach, not once a second.

    The latch is keyed on the PLACE, not on a timer, so driving the same road twice announces twice
    while sitting in traffic short of it announces once. It clears only when nothing is being
    approached, which is also what makes two marks close together announce separately.

    The event itself is raised by the planner, which owns the alert sink -- this only decides that
    there is something to say. Same split as everything else here.
    """
    place = self._radar_places.approaching(lat, lon, bearing_deg, self.v_ego)
    if place is None:
      self._radar_warned_key = None
      self.radar_place_ahead = None
      return

    key = (round(place["lat"], 5), round(place["lon"], 5))
    self.radar_place_ahead = place if key != self._radar_warned_key else None
    self._radar_warned_key = key

  def _update_radar_mute(self, lat: float, lon: float) -> None:
    """Tell the detector to go quiet at a learned false alarm, and to stop when we leave.

    Edge-triggered: one packet on the way in and one on the way out, rather than a stream. The V1
    treats reqMuteOn as a press of its own mute button, which lasts only until it stops tracking the
    alerts it currently has, so this cannot get stuck on even if the unmute is lost.
    """
    if self._radar_reader is None:
      self._radar_muting = False
      return

    want = (self.params.get_bool("RadarDetectorMuteFalseAlarms")
            and self._radar_places.should_mute(lat, lon))
    if want == self._radar_muting:
      return
    if self._radar_reader.send(mute_on() if want else mute_off()):
      self._radar_muting = want

  def _log_radar_encounter(self, sm: messaging.SubMaster, v_ego: float) -> None:
    """Feed the encounter log. Never allowed to matter.

    Wrapped whole rather than defensively field by field: this is a diagnostic, and there is no
    failure in it worth taking down the process that is driving the car. A missing GPS service, a
    message that has not arrived yet, a full filesystem -- all of them mean "no log entry", never
    "no speed limit".
    """
    try:
      gps = sm[self._gps_location_service]
      self._radar_log.update(self.radar_display, gps.latitude, gps.longitude,
                             v_ego, self.radar_alert.active, time.monotonic())
    except Exception:  # noqa: BLE001 - see docstring
      pass

  def _update_radar_link(self) -> None:
    """Bring the detector's serial link up or down, and refresh the latest front-panel state.

    Three things are deliberately separated here, because they run at different rates and for
    different reasons:

    START/STOP is checked on a slow cadence. Opening a port and spawning a thread is not something
    to reconsider at 20 Hz, and the answer only changes when someone toggles a setting or plugs
    something in.

    THE PORT MUST EXIST BEFORE A THREAD IS SPAWNED. Without that gate a resolver built anywhere --
    including every offline test, on a machine with no /dev/serial at all -- would start a thread
    that does nothing but fail to open and sleep. The gate also means plugging the adapter in later
    is picked up on the next check rather than needing a reboot.

    THE DISPLAY IS REFRESHED EVERY FRAME, because staleness is measured in tenths of a second and
    reading it on the slow cadence would make a live link look intermittent.
    """
    if self.frame % _RADAR_LINK_CHECK_FRAMES == 0:
      # Read the param here rather than borrowing radar_alert.enabled. That attribute is only
      # populated once the detector's own update() has run, which happens AFTER this -- so the link
      # would sit down for one check period at every boot, on an ordering dependency nothing states
      # and no test would notice. Reading it directly costs one param read every two seconds.
      want = self.params.get_bool("RadarDetectorEnabled")
      if want and self._radar_reader is None and EspSerialReader.find_port() is not None:
        self._radar_reader = EspSerialReader()
        self._radar_reader.start()
      elif not want and self._radar_reader is not None:
        self._radar_reader.stop()
        self._radar_reader = None

    self.radar_display = self._radar_reader.display() if self._radar_reader is not None else None

  @staticmethod
  def _long_enabled(sm: messaging.SubMaster) -> bool:
    """Is longitudinal engaged and ours right now?

    Defensive in the same way as the rest of this fork's cross-message reads: a missing or invalid
    carControl must mean "not engaged", so a radar alert can never override the offset on the
    strength of data that is not actually there.
    """
    try:
      return bool(sm.valid['carControl'] and sm['carControl'].enabled)
    except (KeyError, AttributeError):
      return False

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
    # BluePilot: a radar detector alert REPLACES the driver's offset for as long as it holds.
    #
    # Replaces rather than adds, and that is the point: someone running +5 over gets a 6 mph change
    # out of a 1 mph margin, because the whole reason to slow down is that the number they normally
    # choose is not the number they want to be doing right now. Adding the margin to their offset
    # would leave them over the limit on exactly the roads where they run over it most.
    #
    # Deliberately ahead of the OffsetType.off branch: "no offset" is a statement about how they
    # normally drive, not a refusal to respond to a radar alert.
    radar_override = self.radar_alert.offset_override(self.is_metric)
    if radar_override is not None:
      return radar_override

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

    source = self._get_source_solution_according_to_policy()
    speed_limit = self.limit_solutions[source] if source else 0.
    distance = self.distance_solutions[source] if source else 0.

    return speed_limit, distance, source

  def update(self, v_ego: float, sm: messaging.SubMaster) -> None:
    self.v_ego = v_ego
    self.update_params()

    self.speed_limit, self.distance, self.source = self._resolve_limit_sources(sm)

    # BluePilot: must run BEFORE the offset is read, or the override is always one frame stale --
    # which at a trigger boundary means the first frame of an alert still uses the driver's own
    # offset. Cheap to get right here, invisible and annoying to debug later.
    self._update_radar_link()
    self.radar_alert.update(self.radar_display, v_ego, self._long_enabled(sm))
    self._update_radar_places(sm)
    self._log_radar_encounter(sm, v_ego)
    self.speed_limit_offset = self._get_speed_limit_offset()

    self.update_speed_limit_states()

    self.frame += 1
