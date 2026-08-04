"""
BluePilot: holds that stay pinned to a place.

A normal ICBM hold is temporary by design -- it survives curves and leads, but a big enough change
in the posted limit discards it, and so does handing the speed back to Speed Limit Assist. That is
right for "I want 72 on this freeway" and wrong for the handful of places where the same correction
is needed on every single drive.

The analogy the owner reached for is exactly right: a radar detector's mute memory. Mute memory is
not for a broken detector. It is for a working one that is reliably wrong in one specific spot --
the supermarket's automatic door, every time, forever. This is that, for speed.

WHAT THIS IS FOR, INCLUDING AFTER TSR WORKS
-------------------------------------------
The obvious use is patching bad OSM data, and that one really does evaporate once the camera reads
signs. These do not:

  - TSR's own repeatable misreads. It will pick up the frontage road's sign, or read an exit ramp's
    yellow advisory as regulatory, at the same place every time. A correct reading of the wrong sign
    is still the wrong number.
  - Signs you disagree with. A correctly-read 25 on an empty stretch is still a 25 nobody drives.
  - Signs that are right at 8am and wrong at 22:00 -- an empty construction zone, a school zone out
    of hours.

So this is deliberately NOT a workaround for a missing sensor. It encodes local knowledge, which is
the one input no sensor and no map database has.

NOT FOR RAMPS. Freeway ramps are curve geometry and belong to SCC-Map, which already knows the
curve is coming from OSM way shape and needs no speed limit to do it. Pinning ramps by hand would
be re-solving a solved problem one interchange at a time.

HOW A PIN IS MADE
-----------------
Tapping the on-screen HOLD badge while a hold is active pins it at the current position; tapping a
pinned hold removes the pin. There is no new button gesture, because the cruise buttons already
carry settled meanings the owner has learned once and should not have to relearn -- see the ICBM
button contract in CLAUDE.md.

WHAT A PIN DOES
---------------
Entering the radius sets the hold to the pinned speed, exactly as though the driver had pressed for
it. It is a normal hold from that moment: curves still slow the car, hazards still override, and the
usual clearing rules apply. That is the whole point of reusing the baseline rather than inventing a
second kind of hold -- the pin decides the NUMBER and WHERE, never the behaviour.
"""
import json
import math

from openpilot.common.params import Params
from openpilot.common.realtime import DT_CTRL
from openpilot.sunnypilot import PARAMS_UPDATE_PERIOD

# Earth radius, matching smart_cruise_control/map_controller.py so the two agree on what a metre is.
R_EARTH = 6373000.0
TO_RADIANS = math.pi / 180

DEFAULT_RADIUS_M = 60
# A pin is a point, but the thing it corrects is usually a stretch of road. That is fine and is why
# the pin only has to fire ONCE: it sets a normal hold, which then persists on its own until
# something legitimately clears it. So the radius wants to be big enough that GPS scatter cannot
# miss it at speed, not big enough to cover the zone.
#
# At 80 mph a 20 Hz position update moves ~1.8 m, so even a tight radius cannot be stepped over.
# The real lower bound is fix accuracy, which is a few metres on a good day and worse in a canyon.
MIN_RADIUS_M = 15
# The upper bound is what stops a pin on a surface street from firing on the freeway above it, or
# on the opposite carriageway. Beyond a couple of hundred metres a point stops meaning a place.
MAX_RADIUS_M = 250

MAX_PINS = 200  # sanity bound on a JSON param, not an expected count

# BluePilot: noticing that you keep correcting the same place, and offering to remember it.
#
# Every hold you set by hand is a small statement that something here is wrong -- a limit nobody
# drives, a sign the camera misreads, a stretch you take differently. Set the same hold in the same
# place enough times and that stops being a one-off. No shipping system does this: an OEM cannot,
# because it has no way to store a correction that applies to one driver on one road.
#
# It only ever SUGGESTS. Crossing the threshold draws a hollow dot on the badge; tapping accepts it
# and turns it into an ordinary pin. Nothing changes how the car drives until you agree, which is
# what makes it safe to be wrong -- the worst case is a dot you ignore.
SUGGEST_AFTER = 3
# The speeds have to agree too, or "I adjusted the speed near here" collapses three different
# intentions into one meaningless average.
SUGGEST_SPEED_TOLERANCE = 3   # display units
MAX_OBSERVATIONS = 400        # twice MAX_PINS; observations churn faster than pins

# BluePilot: this is read from selfdrived, whose step() runs at 100 Hz. Reading three params --
# one of them a JSON blob that grows with the pin count -- on every one of those is 300 reads a
# second for settings that change when someone opens a menu. Every other param reader in this
# fork gates on PARAMS_UPDATE_PERIOD and this one was the outlier; the device is already thermally
# tight enough that BluePilot 7.0 shipped with UI concessions for it.
_SETTINGS_PERIOD_FRAMES = max(int(PARAMS_UPDATE_PERIOD / DT_CTRL), 1)
# The pin request is a button press and cannot wait three seconds, but it does not need 100 Hz
# either. 10 Hz is below the threshold where a tap feels delayed and is a tenth of the cost.
_REQUEST_PERIOD_FRAMES = max(int(0.1 / DT_CTRL), 1)
# Matching is CHEAP per entry and expensive in aggregate: a haversine against every pin and every
# observation, at 100 Hz, is tens of thousands of trig calls a second for an answer that cannot
# change meaningfully between frames. At 80 mph the car moves 0.36 m per frame and the radius is
# tens of metres, so re-testing at 10 Hz is 3.6 m of travel -- far inside any radius that makes
# sense. This is the same mistake as the param reads above, in compute rather than I/O, and it was
# introduced by the fix for that one.
_MATCH_PERIOD_FRAMES = max(int(0.1 / DT_CTRL), 1)


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
  """Haversine great-circle distance in metres."""
  a1, o1, a2, o2 = lat1 * TO_RADIANS, lon1 * TO_RADIANS, lat2 * TO_RADIANS, lon2 * TO_RADIANS
  a = math.sin((a2 - a1) / 2) ** 2 + math.cos(a1) * math.cos(a2) * math.sin((o2 - o1) / 2) ** 2
  return R_EARTH * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class PinnedHolds:
  """Load, match and edit the pinned-hold list.

  Speeds are stored in DISPLAY units (mph or km/h), because that is what a hold is -- the number on
  the cluster. Storing metres per second here would mean a pin made in mph reading back slightly
  different after a units change, for no benefit: nothing downstream wants SI.
  """

  def __init__(self, params: Params | None = None):
    self.params = params or Params()
    self.pins: list[dict] = []
    self.enabled = False
    self.radius = DEFAULT_RADIUS_M
    self._raw = None
    self._obs_raw = None
    self.observations: list[dict] = []
    self.frame = -1
    self._match_cache = (0, 0)   # (matched speed, suggested speed) from the last evaluation

  def update_params(self) -> None:
    """Re-read on the standard settings cadence, then only re-parse if the JSON actually changed.

    Two gates on purpose: the frame counter keeps us off the param store at control rate, and the
    raw comparison keeps us out of json.loads when someone has merely opened the settings screen.
    """
    self.frame += 1
    if self.frame % _SETTINGS_PERIOD_FRAMES != 0:
      return
    self.enabled = self.params.get_bool("IcbmPinnedHoldsEnabled")
    self.radius = min(max(int(self.params.get("IcbmPinnedHoldRadius", return_default=True)),
                          MIN_RADIUS_M), MAX_RADIUS_M)
    raw = self.params.get("IcbmPinnedHolds")
    if raw == self._raw:
      return
    self._raw = raw
    self.pins = self._parse(raw)
    obs_raw = self.params.get("IcbmHoldObservations")
    if obs_raw != self._obs_raw:
      self._obs_raw = obs_raw
      self.observations = self._parse(obs_raw, limit=MAX_OBSERVATIONS, keep_count=True)

  @staticmethod
  def _parse(raw, limit: int = MAX_PINS, keep_count: bool = False) -> list[dict]:
    """Tolerate anything. A corrupt param must mean "no pins", never a crash in the control loop."""
    if not raw:
      return []
    try:
      if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
      data = json.loads(raw)
      if not isinstance(data, list):
        return []
      out = []
      for p in data[:limit]:
        try:
          lat, lon, speed = float(p["lat"]), float(p["lon"]), int(p["speed"])
        except (TypeError, KeyError, ValueError):
          continue
        # A pin at 0,0 is the signature of a fix that never came, not a place in the Gulf of Guinea.
        if speed > 0 and (abs(lat) > 1e-6 or abs(lon) > 1e-6):
          entry = {"lat": lat, "lon": lon, "speed": speed}
          if keep_count:
            try:
              entry["count"] = max(1, int(p.get("count", 1)))
            except (TypeError, ValueError):
              entry["count"] = 1
          out.append(entry)
      return out
    except (ValueError, TypeError):
      return []

  def nearest(self, lat: float, lon: float) -> tuple[dict | None, float]:
    """Closest pin and its distance, whatever the radius. Used by both matching and un-pinning."""
    best, best_d = None, float("inf")
    for p in self.pins:
      d = distance_m(lat, lon, p["lat"], p["lon"])
      if d < best_d:
        best, best_d = p, d
    return best, best_d

  def evaluate(self, lat: float, lon: float) -> tuple[int, int]:
    """(pinned speed here, speed worth suggesting here). Re-tested at 10 Hz, cached between.

    One entry point for both because they walk the same lists and are wanted on the same frame;
    computing them separately doubled the work for no extra information.
    """
    if self.frame % _MATCH_PERIOD_FRAMES == 0:
      self._match_cache = (self.match(lat, lon), self.suggestion(lat, lon))
    return self._match_cache

  def match(self, lat: float, lon: float) -> int:
    """Pinned speed for this position in display units, or 0 for none.

    Returns 0 when disabled or without a fix, so the caller needs no separate availability test.
    """
    if not self.enabled or not self.pins:
      return 0
    if abs(lat) < 1e-6 and abs(lon) < 1e-6:
      return 0
    pin, d = self.nearest(lat, lon)
    return int(pin["speed"]) if pin is not None and d <= self.radius else 0

  def toggle(self, lat: float, lon: float, speed: int) -> str:
    """Pin the current hold here, or remove the pin already here. Returns what it did.

    One control for both directions, because the badge that triggers it shows which state you are
    in -- there is nothing to disambiguate and a second control would be a second thing to learn.
    """
    if abs(lat) < 1e-6 and abs(lon) < 1e-6:
      return "no_fix"

    pin, d = self.nearest(lat, lon)
    if pin is not None and d <= self.radius:
      self.pins = [p for p in self.pins if p is not pin]
      self._save()
      self.forget(lat, lon)   # removing a pin here also drops the evidence that suggested it
      return "removed"

    if speed <= 0:
      return "no_hold"
    if len(self.pins) >= MAX_PINS:
      return "full"

    self.pins.append({"lat": round(lat, 6), "lon": round(lon, 6), "speed": int(speed)})
    self._save()
    self.forget(lat, lon)   # the suggestion has been answered; stop counting
    return "added"

  def request_pending(self) -> bool:
    """Has the HOLD badge been tapped? Polled at 10 Hz, not at control rate -- see the constants."""
    if self.frame % _REQUEST_PERIOD_FRAMES != 0:
      return False
    if not self.params.get_bool("IcbmPinHoldRequest"):
      return False
    self.params.put_bool("IcbmPinHoldRequest", False)
    return True

  def observe_hold(self, lat: float, lon: float, speed: int) -> int:
    """Record that the driver set this hold here. Returns how many times that has now happened.

    Called once per hold, on creation -- not per frame, and never for a hold a pin created, which
    would count the suggestion as evidence for itself.
    """
    if speed <= 0 or (abs(lat) < 1e-6 and abs(lon) < 1e-6):
      return 0
    if self.match(lat, lon):
      return 0   # already pinned here; nothing left to learn

    for o in self.observations:
      if (distance_m(lat, lon, o["lat"], o["lon"]) <= self.radius
          and abs(o["speed"] - speed) <= SUGGEST_SPEED_TOLERANCE):
        o["count"] += 1
        o["speed"] = speed          # the latest intent wins; it is the one they just expressed
        self._save_observations()
        return o["count"]

    if len(self.observations) < MAX_OBSERVATIONS:
      self.observations.append({"lat": round(lat, 6), "lon": round(lon, 6),
                                "speed": int(speed), "count": 1})
      self._save_observations()
    return 1

  def suggestion(self, lat: float, lon: float) -> int:
    """Speed worth offering to pin here, or 0. Never acts on its own -- see SUGGEST_AFTER."""
    if not self.enabled or self.match(lat, lon):
      return 0
    if abs(lat) < 1e-6 and abs(lon) < 1e-6:
      return 0
    for o in self.observations:
      if o["count"] >= SUGGEST_AFTER and distance_m(lat, lon, o["lat"], o["lon"]) <= self.radius:
        return int(o["speed"])
    return 0

  def forget(self, lat: float, lon: float) -> None:
    """Drop observations here, so accepting or declining a suggestion stops it re-offering."""
    self.observations = [o for o in self.observations
                         if distance_m(lat, lon, o["lat"], o["lon"]) > self.radius]
    self._save_observations()

  def _save_observations(self) -> None:
    self._obs_raw = json.dumps(self.observations)
    self.params.put("IcbmHoldObservations", self._obs_raw)

  def clear(self) -> None:
    self.pins = []
    self.observations = []
    self._save()
    self._save_observations()

  def _save(self) -> None:
    self._raw = json.dumps(self.pins)
    self.params.put("IcbmPinnedHolds", self._raw)
