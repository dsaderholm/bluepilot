"""
BluePilot: places worth remembering, learned from the radar detector.

Two jobs, one store, and that is the point rather than a compromise. A place you repeatedly alert
at is either a false alarm or somewhere police actually work, and the same records answer both:

  FALSE ALARM   -- alerts on essentially every pass. A supermarket door, a sign, an overpass. These
                   get muted so the car stops reacting to them.
  ENFORCEMENT   -- alerts on some passes and not others. Nobody sits at a speed trap all day, so a
                   real position shows up sometimes and not others.

The discriminator is the HIT RATIO, which is why this counts the times you drove past and NOTHING
happened. Without those, every location looks like a 100% hit rate and every real speed trap gets
written off as a door opener. Counting the quiet passes is not bookkeeping, it is the measurement.

Laser sits outside that logic entirely -- see LASER_ALWAYS_ENFORCEMENT.

PERFORMANCE: A GRID, NOT A SCAN
-------------------------------
This is checked against the car's position continuously, and it can hold thousands of places. A
haversine against every one of them, every frame, is precisely the mistake pinned_holds.py already
records having made -- its own comment puts a linear scan at control rate at "tens of thousands of
trig calls a second" for a couple of hundred pins.

So locations are bucketed into a dictionary keyed by rounded coordinates. A lookup touches the nine
cells around you and nothing else, which makes it O(1) in the size of the store rather than O(n).
The expensive distance maths then runs on the handful of candidates that survive, and only at the
cadence the CALLER chooses -- _RADAR_MATCH_FRAMES in speed_limit_resolver.py, currently 1 Hz. This
module used to carry its own MATCH_HZ constant that nothing read, which is worse than nothing:
changing it would have looked like changing the match rate and done exactly zero.

At 80 mph the car covers 36 m in the time between matches at 1 Hz, so even the tightest useful
radius cannot be stepped over.
"""

import json
import math
import os
import threading

from openpilot.sunnypilot.selfdrive.car.intelligent_cruise_button_management.pinned_holds import distance_m

# Grid cell size in degrees. 0.01 degrees of latitude is about 1.1 km, and less in longitude the
# further north you are -- at 40 degrees (Utah) about 850 m. Both comfortably exceed any radius
# worth using, which is what makes checking the nine surrounding cells sufficient.
CELL_DEG = 0.01

# How close counts as "the same place". Larger than a speed-hold pin's radius on purpose: a pin
# needs to name one spot on one carriageway, while a patrol car works a stretch and parks somewhere
# slightly different each time.
DEFAULT_RADIUS_M = 150

MAX_LOCATIONS = 2000   # the same order as the R4's own memory, and nothing here is bigger

# A place must be seen this many times before it is treated as anything at all. One alert is a
# coincidence; it gets recorded, but it does not mute anything and it does not warn.
MIN_OBSERVATIONS = 3

# Muting needs MORE evidence than warning does, and the reason is that the two mistakes do not cost
# the same. Warning about a place that turns out to be a supermarket door is an annoyance. Muting a
# place that turns out to be a patrol car is a ticket, and worse, it is SILENT -- the driver never
# finds out the system decided not to tell them.
#
# The asymmetry is not theoretical. Quiet passes only start counting once a place exists, and a
# place is created by its first ALERT, so nothing records the dozens of uneventful times you drove
# past before that. A spot that happens to alert twice running therefore reads two-for-two and would
# be muted on the strength of two observations. It self-corrects as quiet passes accumulate, but the
# window is exactly when a genuinely new enforcement spot is least understood.
MIN_OBSERVATIONS_TO_MUTE = 10

# Above this hit ratio a place is a fixed source -- it is there every single time, which no patrol
# car is. Below it, something intermittent is happening.
FALSE_ALARM_RATIO = 0.85
# Below this it is too rare to warn about without crying wolf. Between the two it is recorded and
# shown but stays quiet.
ENFORCEMENT_MIN_RATIO = 0.15

# Laser is never treated as a false alarm however often it repeats.
#
# Two reasons, and both matter. Lidar is a narrow aimed beam, so a detection is nearly always a real
# gun rather than a stray reflection -- the usual false-alarm sources simply do not produce it. And
# because it is aimed, most passes through a laser position produce NO detection at all, so the hit
# ratio is meaningless here: a spot an officer works every day might read 1 in 20 and would be
# discarded by the rule above.
LASER_ALWAYS_ENFORCEMENT = True

# Passes with nothing seen, after which a place that has never re-confirmed is forgotten.
#
# Marks going stale is the failure mode that kills this kind of feature: a trap that was real last
# year becomes a warning you learn to ignore, and one you ignore is worse than none. Decay is built
# in from the start rather than added after it has annoyed someone.
DECAY_AFTER_QUIET_PASSES = 40


# How far ahead to announce a known place. Time, not distance -- a quarter mile at 55 mph and
# nearly half at 75. A fixed distance is too early in town and far too late on the freeway.
WARN_LEAD_S = 20.0

# Half-angle of the cone ahead that counts as "we are heading into it".
#
# Wide enough to survive a bend in the road between here and the place, narrow enough to exclude the
# opposite carriageway and the surface street below the freeway. GPS heading is also noisy at low
# speed, which is one more reason the warning is gated on moving at all.
WARN_CONE_DEG = 55.0


def _cell(lat: float, lon: float) -> tuple[int, int]:
  return (int(math.floor(lat / CELL_DEG)), int(math.floor(lon / CELL_DEG)))


def _neighbors(lat: float, lon: float):
  """The nine cells around a position. A place near a cell edge is in a neighbor, not in ours."""
  cy, cx = _cell(lat, lon)
  for dy in (-1, 0, 1):
    for dx in (-1, 0, 1):
      yield (cy + dy, cx + dx)


def _neighbors_within(lat: float, lon: float, reach_m: float):
  """Grid cells that could hold anything within reach_m.

  NOT LOAD-BEARING AT PRESENT CONSTANTS, and worth saying so rather than letting someone assume it
  is. A cell is about 1.1 km and a 20-second lead at 80 mph is 715 m, so this resolves to the same
  nine cells _neighbors() returns; forcing span to 1 breaks no test, which is how that was found.

  It stays general anyway because the two numbers it depends on are tunable and unrelated -- raising
  WARN_LEAD_S or shrinking CELL_DEG would silently start missing places, and the symptom would be
  "the warning does not seem to fire much", which is close to indistinguishable from working. A
  correct calculation costs nothing here; discovering that one months later costs a lot.
  """
  # Sized against the NARROWER dimension of a cell. A degree of latitude is about 111 km everywhere,
  # but a degree of longitude shrinks with the cosine of latitude -- at 40 degrees it is about 85 km,
  # so a cell is 1.1 km tall and 850 m wide here. Measuring against the tall side would undercount
  # the span needed east-west and miss places on exactly one axis, which is the kind of bug that
  # looks like flaky GPS.
  #
  # No safety margin beyond the ceiling: standing anywhere in our own cell, a point reach_m away is
  # at most reach_m past our cell's edge, so ceil(reach / cell) further cells always covers it. An
  # earlier +1 here made even a 100 m reach search 25 cells instead of 9 for nothing.
  cell_m = CELL_DEG * 111_000 * max(math.cos(math.radians(lat)), 0.1)
  span = max(1, int(math.ceil(reach_m / cell_m)))
  cy, cx = _cell(lat, lon)
  for dy in range(-span, span + 1):
    for dx in range(-span, span + 1):
      yield (cy + dy, cx + dx)


def _relative_bearing(lat1: float, lon1: float, lat2: float, lon2: float, heading_deg: float) -> float:
  """Degrees between where we are pointing and where the place is, in -180..180."""
  d_lon = math.radians(lon2 - lon1)
  a1, a2 = math.radians(lat1), math.radians(lat2)
  y = math.sin(d_lon) * math.cos(a2)
  x = math.cos(a1) * math.sin(a2) - math.sin(a1) * math.cos(a2) * math.cos(d_lon)
  to_place = math.degrees(math.atan2(y, x))
  return (to_place - heading_deg + 180.0) % 360.0 - 180.0


def _has_fix(lat: float, lon: float) -> bool:
  """A position at exactly 0,0 is a fix that never arrived, not a place in the Gulf of Guinea.

  Same guard and same reasoning as pinned_holds; without it every drive would pile observations
  onto one imaginary island until it outvoted everything real.
  """
  return abs(lat) > 1e-6 or abs(lon) > 1e-6


class RadarLocations:
  """Learned places, indexed spatially. Nothing in here does I/O on the control path."""

  def __init__(self, path: str | None = None, radius_m: int = DEFAULT_RADIUS_M):
    self.path = path
    self.radius_m = radius_m
    self.places: list[dict] = []
    self._grid: dict[tuple[int, int], list[dict]] = {}
    # State for the pass currently in progress. A drive-through is ONE observation however long it
    # takes, so what happened during it is accumulated here and settled up on the way out.
    self._inside: set[int] = set()
    self._alerted_here: set[int] = set()
    self._laser_here: set[int] = set()
    self._suppressed_here: set[int] = set()
    # Places created DURING this pass. observe() already counted their first alert and first pass,
    # so settling up on the way out would count the same drive-through twice.
    self._settled_here: set[int] = set()
    self._bands_here = 0
    self.load()

  # ---- storage -------------------------------------------------------------------------------

  def load(self) -> None:
    if not self.path or not os.path.exists(self.path):
      return
    try:
      with open(self.path) as f:
        data = json.load(f)
      if isinstance(data, list):
        self.places = [p for p in (self._clean(e) for e in data[:MAX_LOCATIONS]) if p]
    except Exception:  # noqa: BLE001 - a corrupt store means "no places", never a crash
      self.places = []
    self._reindex()

  def save(self, places=None) -> None:
    if not self.path:
      return
    try:
      tmp = self.path + ".tmp"
      with open(tmp, "w") as f:
        json.dump(self.places if places is None else places, f)
      os.replace(tmp, self.path)   # atomic: a power cut cannot leave a half-written store
    except Exception:  # noqa: BLE001 - a diagnostic store must never take down the process
      pass

  def export_geojson(self, path: str, places=None) -> None:
    """Write the map file next to the store, every time the store is written.

    Deliberately automatic rather than a command to run. The point of choosing GeoJSON was that the
    answer to "can I see this on a map" is yes today -- geojson.io, Google My Maps, QGIS all read it
    -- and that is only true if the file is already sitting there when he goes looking. A feature
    that needs a remembered incantation before it produces anything is a feature that produces
    nothing.
    """
    try:
      tmp = path + ".tmp"
      with open(tmp, "w") as f:
        json.dump(self.to_geojson(places), f)
      os.replace(tmp, path)
    except Exception:  # noqa: BLE001 - a map export must never matter to a driving process
      pass

  def save_async(self, geojson_path: str | None = None) -> None:
    """Snapshot on the caller's thread, write on another one.

    A full store is a couple of hundred kilobytes of JSON, and json.dump of that on the device is
    tens of milliseconds. This is called from a 20 Hz control loop, where tens of milliseconds is a
    dropped frame -- so the only part that runs here is copying the dicts, which is a millisecond or
    two, and the encode and the disk write happen off to the side.

    The snapshot is what makes that safe: the writer thread never touches the live list, so a pass
    being recorded while a save is in flight cannot produce a half-mutated file.
    """
    if not self.path:
      return
    snapshot = [dict(p) for p in self.places]

    def write():
      self.save(snapshot)
      if geojson_path:
        self.export_geojson(geojson_path, snapshot)

    threading.Thread(target=write, daemon=True).start()

  @staticmethod
  def _clean(entry) -> dict | None:
    try:
      lat, lon = float(entry["lat"]), float(entry["lon"])
    except (TypeError, KeyError, ValueError):
      return None
    if not _has_fix(lat, lon):
      return None
    return {
      "lat": lat, "lon": lon,
      "alerts": max(1, int(entry.get("alerts", 1))),
      "passes": max(1, int(entry.get("passes", 1))),
      "quiet": max(0, int(entry.get("quiet", 0))),
      "bands": int(entry.get("bands", 0)),
      "laser": bool(entry.get("laser", False)),
      "manual": bool(entry.get("manual", False)),
    }

  def _reindex(self) -> None:
    self._grid = {}
    for p in self.places:
      self._grid.setdefault(_cell(p["lat"], p["lon"]), []).append(p)

    # Drop any pass in progress. The pass state tracks places by id(), and reindexing follows
    # eviction, decay or a reload -- all of which drop dict objects whose ids CPython is free to
    # hand straight back to the next dict allocated. A recycled id would silently credit one
    # place's drive-through to another. Losing one in-progress observation is the cheap side of
    # that trade, and it happens at most every couple of minutes.
    self._inside = set()
    self._alerted_here = set()
    self._laser_here = set()
    self._suppressed_here = set()
    self._settled_here = set()
    self._bands_here = 0

  # ---- lookup --------------------------------------------------------------------------------

  def near(self, lat: float, lon: float) -> list[dict]:
    """Places within the radius. O(1) in the size of the store -- see the module docstring."""
    if not _has_fix(lat, lon):
      return []
    out = []
    for cell in _neighbors(lat, lon):
      for p in self._grid.get(cell, ()):
        if distance_m(lat, lon, p["lat"], p["lon"]) <= self.radius_m:
          out.append(p)
    return out

  # ---- learning ------------------------------------------------------------------------------

  def observe(self, lat: float, lon: float, alerted: bool, bands: int = 0,
              laser: bool = False, manual: bool = False, suppressed: bool = False) -> dict | None:
    """Record one PASS through this position.

    Called once per approach, not per frame -- update_pass below handles that. `alerted` is the
    half that makes the hit ratio mean anything: a pass with nothing seen is evidence too, and the
    only evidence that can ever tell a speed trap from a supermarket door.

    `suppressed` says WE are the reason it was quiet, and it discards the observation entirely.
    That is not fastidiousness, it is the fix for a feedback loop that would have destroyed the
    store from the inside:

        a place is learned as a false alarm  ->  we mute the detector there  ->  the detector is
        quiet  ->  we record a quiet pass  ->  the hit ratio falls  ->  the place stops looking
        like a false alarm  ->  we stop muting  ->  it alerts again  ->  the ratio climbs  ->  we
        mute again

    An oscillation with a period of weeks, driven entirely by our own action, and visible only on
    the road. It is the same shape as the "is Ford braking" trap in unconfirmed_lead.py -- evidence
    the detector manufactures for itself -- and it is worth stating in both places because the
    pattern is easy to reintroduce anywhere a system observes something it also influences.

    A muted pass is not evidence of quiet. It is no evidence at all.
    """
    if not _has_fix(lat, lon):
      return None
    if suppressed and not (alerted or manual):
      return None

    here = self.near(lat, lon)
    if here:
      place = min(here, key=lambda p: distance_m(lat, lon, p["lat"], p["lon"]))
      place["passes"] += 1
      if alerted:
        place["alerts"] += 1
        place["quiet"] = 0
        place["bands"] |= bands
        place["laser"] = place["laser"] or laser
      else:
        place["quiet"] += 1
      place["manual"] = place["manual"] or manual
      return place

    if not (alerted or manual):
      return None   # nothing happened somewhere we know nothing about; not a place
    if len(self.places) >= MAX_LOCATIONS:
      self._forget_weakest()

    place = {"lat": round(lat, 6), "lon": round(lon, 6), "alerts": 1, "passes": 1, "quiet": 0,
             "bands": bands, "laser": laser, "manual": manual}
    self.places.append(place)
    self._grid.setdefault(_cell(lat, lon), []).append(place)
    return place

  def update_pass(self, lat: float, lon: float, alerted: bool, bands: int = 0,
                  laser: bool = False, suppressed: bool = False) -> None:
    """Count a pass ONCE per approach, on the way out.

    Called at match rate. Entering a place records nothing; leaving it does. That way a single
    drive-through is one observation whether it took two seconds or twenty, and stopping at a light
    inside the radius does not inflate the count.

    `suppressed` -- we muted the detector during this pass -- drops the observation rather than
    counting it as quiet. See observe() for why that loop matters.
    """
    if not _has_fix(lat, lon):
      return

    near = self.near(lat, lon)
    now_inside = {id(p) for p in near}

    # Settle up for everything we have just left. ONE pass each, and it counts as an alert if
    # anything fired at any point while we were inside -- not merely if something is firing on the
    # frame we happened to cross the boundary.
    for p in self.places:
      pid = id(p)
      if pid not in self._inside or pid in now_inside:
        continue
      if pid in self._suppressed_here:
        continue        # we were the reason it was quiet; that is no evidence at all
      if pid in self._settled_here:
        continue        # created during this pass and already counted for it
      p["passes"] += 1
      if pid in self._alerted_here:
        p["alerts"] += 1
        p["quiet"] = 0
        p["bands"] |= self._bands_here
        p["laser"] = p["laser"] or (pid in self._laser_here)
      else:
        p["quiet"] += 1

    gone = self._inside - now_inside
    self._alerted_here -= gone
    self._laser_here -= gone
    self._suppressed_here -= gone
    self._settled_here -= gone
    if not now_inside:
      self._bands_here = 0
    self._inside = now_inside

    if suppressed:
      self._suppressed_here |= now_inside

    if not alerted:
      return

    # Alerting. Credit it to the containment rather than recording it now: an approach that alerts
    # for five seconds is ONE observation of one place, and counting per cycle was inflating alerts
    # and passes together, pinning the hit ratio at 1.0 and making every real enforcement spot look
    # like a supermarket door.
    self._bands_here |= bands
    if near:
      self._alerted_here |= now_inside
      if laser:
        self._laser_here |= now_inside
      return

    # Nothing known here. Create the place, already credited for this pass, so leaving it does not
    # count a second time.
    created = self.observe(lat, lon, True, bands, laser, suppressed=suppressed)
    if created is not None:
      self._inside.add(id(created))
      self._settled_here.add(id(created))

  def _forget_weakest(self) -> None:
    """Drop the least informative place when full. Never a manual mark and never a laser spot --
    those were put there deliberately or cannot be re-learned by driving past."""
    candidates = [p for p in self.places if not p["manual"] and not p["laser"]]
    if not candidates:
      candidates = self.places
    worst = min(candidates, key=lambda p: p["alerts"])
    self.places.remove(worst)
    self._reindex()

  def decay(self) -> int:
    """Forget places that have gone quiet for a long time. Returns how many were dropped.

    Manual marks and laser spots are exempt: a laser position produces no detection on most passes
    by its nature, so quiet passes are not evidence against it.
    """
    before = len(self.places)
    self.places = [p for p in self.places
                   if p["manual"] or p["laser"] or p["quiet"] < DECAY_AFTER_QUIET_PASSES]
    if len(self.places) != before:
      self._reindex()
    return before - len(self.places)

  # ---- classification ------------------------------------------------------------------------

  @staticmethod
  def ratio(place: dict) -> float:
    return place["alerts"] / max(place["passes"], 1)

  def classify(self, place: dict) -> str:
    """"false_alarm", "enforcement", or "unknown"."""
    if place["laser"] and LASER_ALWAYS_ENFORCEMENT:
      return "enforcement"
    if place["manual"]:
      return "enforcement"
    if place["passes"] < MIN_OBSERVATIONS:
      return "unknown"
    r = self.ratio(place)
    if r >= FALSE_ALARM_RATIO:
      return "false_alarm"
    if r >= ENFORCEMENT_MIN_RATIO:
      return "enforcement"
    return "unknown"

  def should_mute(self, lat: float, lon: float) -> bool:
    """Is this a place the detector should be told to shut up about?

    Deliberately stricter than classify() alone -- see MIN_OBSERVATIONS_TO_MUTE. A place can read
    as a false alarm on three observations, which is enough to stop the car reacting to it, and not
    nearly enough to take the driver's warning away.
    """
    return any(self.classify(p) == "false_alarm" and p["passes"] >= MIN_OBSERVATIONS_TO_MUTE
               for p in self.near(lat, lon))

  def warnings(self, lat: float, lon: float) -> list[dict]:
    """Places near this position worth announcing."""
    return [p for p in self.near(lat, lon) if self.classify(p) == "enforcement"]

  def approaching(self, lat: float, lon: float, bearing_deg: float, v_ego: float) -> dict | None:
    """The place we are about to reach, if any, or None. Announce this and nothing else.

    Three filters, and every one of them exists to stop the warning becoming noise. A warning that
    cries wolf is worse than no warning, because the one that matters arrives after the driver has
    already learned to ignore it -- the same conclusion the ICBM alerts reached from the other
    direction, dialling an emergency tone back to a prompt after two false positives in a drive.

    LEAD TIME, NOT DISTANCE. Warn WARN_LEAD_S ahead, which is a quarter mile at 55 and nearly half
    at 75. A fixed distance is too early in town and too late on the freeway.

    HEADING. Only places actually in front of us. Without this a spot marked on a surface street
    fires on the freeway above it, and every mark fires again on the way home -- pinned_holds
    records the same problem for speed pins, and it bites harder here because the cost is a sound
    rather than a number changing.

    NOT ALREADY INSIDE IT. Past the point, there is nothing to warn about.
    """
    if not _has_fix(lat, lon) or v_ego <= 0:
      return None

    reach = max(v_ego * WARN_LEAD_S, self.radius_m)
    best = None
    best_d = float("inf")
    for cell in _neighbors_within(lat, lon, reach):
      for p in self._grid.get(cell, ()):
        if self.classify(p) != "enforcement":
          continue
        d = distance_m(lat, lon, p["lat"], p["lon"])
        if d <= self.radius_m or d > reach or d >= best_d:
          continue
        if abs(_relative_bearing(lat, lon, p["lat"], p["lon"], bearing_deg)) > WARN_CONE_DEG:
          continue
        best, best_d = p, d
    return best

  # ---- export --------------------------------------------------------------------------------

  def to_geojson(self, places=None) -> dict:
    """Everything learned, as GeoJSON.

    Chosen so the answer to "can I see this on a map" is yes TODAY, with no map screen written:
    geojson.io, Google My Maps and QGIS all read it directly. A custom on-device map could come
    later, but it should not be what stands between the owner and his own data.
    """
    return {
      "type": "FeatureCollection",
      "features": [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
        "properties": {
          "kind": self.classify(p),
          "alerts": p["alerts"],
          "passes": p["passes"],
          "hit_ratio": round(self.ratio(p), 3),
          "laser": p["laser"],
          "manual": p["manual"],
        },
      } for p in (self.places if places is None else places)],
    }
