"""FusionPilot: liveMapDataSP, filled from mapd v2's `mapdOut` instead of /dev/shm/params.

This is the whole reason the v1 -> v2 move is small. Speed Limit Assist, the resolver, the four
SCC-Map defenses, ICBM, holds and the HUD all consume `liveMapDataSP` -- none of them knows where
the numbers came from. `BaseMapData` is abstract with exactly four methods behind it, so a second
source is a sibling of OsmMapData rather than a rewrite of anything downstream. sunnypilot's
abandoned attempt was +3/-2567 because it was consolidating features at the same time; the swap
itself is this file.

WHAT IT DELIBERATELY DOES NOT READ: `mapdOut.suggestedSpeed`. That is mapd's own arbitration -- the
minimum of its speed-limit and curve numbers -- and its integration guide has you clamp v_cruise to
it directly. It cannot know this car is driven by BUTTON PRESSES at about 3.3 mph/s, that a HOLD
exists, or that SCC-Map carries four defenses each built from a measured event on these roads.
Applied as a clamp it also moves the MAX number, which is his. test_mapd_schema.py guards it.

The curve and vision numbers (`mapCurveSpeed`, `visionCurveSpeed`) and the path ahead are NOT
consumed here either, but for a different reason: they belong to SCC, not to Speed Limit Assist,
and they arrive through `mapdOut`/`mapdExtendedOut` at their own call sites.

NO QUIET FALLBACK. If mapd v2 is selected and is not publishing, this reports no speed limit rather
than reaching back to v1's params. A stub laxer than the thing it stands in for hides exactly the
bug it was built to catch -- and SLA already treats "no limit" as a first-class state, so the honest
answer is the safe one. `mapdAlive` is cloudlogged on every transition so a route says which it was.
"""
import json
import math
import platform
import time

from cereal import log
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.mapd.live_map_data.base_map_data import BaseMapData
from openpilot.sunnypilot.navd.helpers import Coordinate


# FusionPilot: way-match states whose speed limit is NOT trusted. See `way_match_untrusted`.
# A tuple rather than two comparisons so adding a state is one edit and the test can enumerate it.
_UNTRUSTED_WAY_MATCH = ("fail", "possible")

# FusionPilot: how long mapdOut may be silent, WHILE THE LOCALIZER HAS A GOOD POSITION, before the
# process is treated as stalled. See `stalled_s` and mapd_manager's watchdog.
#
# 2026-08-30: routes 3f5-3f9 published ZERO mapdOut across 45 segments and ~185,000 moving frames
# while `managerState` read `mapd_v2 running=True` on every sample, with no exit code, normal memory
# and normal temperature. Three of those five routes were FRESH BOOTS, so this survives a reboot and
# survives `restart_if_crash=True` -- that flag watches for the process DYING, and this process does
# not die. Speed Limit Assist had no limit for all five drives.
#
# 60 s is deliberately slow. mapd v2 loads tiles at startup and is legitimately quiet for some
# seconds; the failure being caught lasted for entire drives, so detection speed is worth nothing
# and a restart loop would cost a core.
MAPD_V2_STALL_S = 60.0


def _now() -> float:
  """Indirection so tests can drive the stall clock deterministically. The monotonic clock has
  ~15 ms granularity on Windows, so several calls inside one test return the SAME value -- which
  let a mutant that re-anchors the clock on every tick pass the entire suite. That mutant makes
  MAPD_V2_STALL_S unreachable, i.e. the watchdog never fires at all, so it is the one that most
  needed catching. A seam is cheaper than an assertion that depends on timer resolution.
  """
  return time.monotonic()


class MapdV2MapData(BaseMapData):
  def __init__(self):
    super().__init__(extra_services=['mapdOut'])
    self.mem_params = Params("/dev/shm/params") if platform.system() != "Darwin" else self.params

    # Logged on change only. Three questions a route has never been able to answer: is mapd there,
    # is there a tile here at all, and is the matcher confident or lost. The last one is the
    # measured US 40/189 case -- tile holds 65 mph, SLA showed nothing, and nothing said why.
    self._last_alive: bool | None = None
    self._last_tile_loaded: bool | None = None
    self._last_way_selection: str | None = None

    # FusionPilot: monotonic clock rather than a tick count, so this does not silently depend on
    # mapd_manager's Ratekeeper rate. None means "not stalled".
    self._stall_since: float | None = None

  @property
  def mapd_alive(self) -> bool:
    return bool(self.sm.alive['mapdOut'] and self.sm.valid['mapdOut'])

  def update_location(self) -> None:
    """Identical to v1's, INCLUDING the LastGPSPosition write, and that is on purpose.

    mapd v2 does not need it -- it subscribes to gpsLocation itself. Two other readers still do:
    map_controller.py takes its own position from that key, and v1 mapd is still installed during
    the overlap. Dropping the write here would take SCC-Map's position away as a side effect of
    changing the speed-limit source, which is exactly the kind of coupling that makes a migration
    look like it broke something unrelated. It goes when SCC-Map moves to mapdExtendedOut.position.
    """
    # BEFORE the early return below, and that placement is the whole point. This reads self.sm and
    # nothing else, so coupling it to having a position made it unreachable exactly when it matters:
    # last_position is seeded from the LastGPSPositionLLK param, which is absent on a fresh device,
    # so "mapdOut alive=False" -- the one line that explains a silent v2 at startup -- was the line
    # that could not be printed at startup.
    self._log_transitions()

    location = self.sm['liveLocationKalman']
    self.localizer_valid = (location.status == log.LiveLocationKalman.Status.valid) and location.positionGeodetic.valid

    # FusionPilot: only count a stall while we HAVE a position. Offroad the localizer is silent and
    # mapd v2 correctly publishes nothing -- counting that would bounce the process in the driveway.
    if self.localizer_valid and not self.mapd_alive:
      if self._stall_since is None:
        self._stall_since = _now()
    else:
      self._stall_since = None

    if self.localizer_valid:
      self.last_bearing = math.degrees(location.calibratedOrientationNED.value[2])
      self.last_position = Coordinate(location.positionGeodetic.value[0], location.positionGeodetic.value[1])

    if self.last_position is None:
      return

    params = {
      "latitude": self.last_position.latitude,
      "longitude": self.last_position.longitude,
    }

    if self.last_bearing is not None:
      params['bearing'] = self.last_bearing

    self.mem_params.put("LastGPSPosition", json.dumps(params), block=True)

  @property
  def stalled_s(self) -> float:
    """Seconds mapdOut has been silent while the localizer had a valid position. 0.0 if healthy."""
    return 0.0 if self._stall_since is None else _now() - self._stall_since

  def reset_stall(self) -> None:
    """Restart the stall clock. Called when a restart has been REQUESTED, so the watchdog gives the
    new process a full MAPD_V2_STALL_S to come up instead of asking again on the next tick."""
    self._stall_since = None

  def _log_transitions(self) -> None:
    alive = self.mapd_alive
    if alive != self._last_alive:
      cloudlog.warning(f"mapd v2: mapdOut alive={alive}")
      self._last_alive = alive
    if not alive:
      return

    mapd = self.sm['mapdOut']
    if bool(mapd.tileLoaded) != self._last_tile_loaded:
      self._last_tile_loaded = bool(mapd.tileLoaded)
      cloudlog.warning(f"mapd v2: tileLoaded={self._last_tile_loaded}")
    way_selection = str(mapd.waySelectionType)
    if way_selection != self._last_way_selection:
      self._last_way_selection = way_selection
      cloudlog.warning(f"mapd v2: waySelectionType={way_selection}")

  @property
  def way_match_untrusted(self) -> bool:
    """mapd is not confident which way the car is on, so its limit is not "the limit here".

    A limit published on such a frame is a limit for a road mapd is not sure we are on, arriving
    labelled exactly like a matched one. v1 has no way to express this state at all, which is part
    of why it is worth migrating.

    `possible` WAS ADDED 2026-08-26, FROM A ROAD REPORT: *"at one point at the beginning my speed
    went to 80 while it was at a stop and then went back down to 35."*

    Route 000003c4, t+74, stopped at 1 mph on 1300 East -- a secondary road with a 30 mph limit:

        t+72-73   waySelectionType fail       unknown      no limit
        t+74      waySelectionType POSSIBLE   MOTORWAY     "Dwight D. Eisenhower H"   70 mph
        t+75      waySelectionType fail       unknown      gone
        t+78      waySelectionType possible   secondary    "1300 East"                30 mph
        t+79+     waySelectionType current    secondary    "1300 East"                30 mph

    ONE SAMPLE matching I-80 was enough. The resolver took the 70, it cleared his 35 hold, and ICBM
    pressed the set speed from 35 toward 80 over three seconds before the map corrected itself.
    **A bad limit is converted into BUTTON PRESSES, and those do not come back when the limit does**
    -- the same thing the TSR phantom 80 did on route 000003b6.

    WHAT IT COSTS, measured rather than assumed, across routes c4 and c5 (25,556 mapdOut frames):

        current    21,660 frames   81.7% carry a limit    <- the normal case, untouched
        fail        2,311           0.0%                  <- already refused
        predicted   1,080          79.2%                  <- untouched
        possible      309          46.3%                  <- REFUSED NOW
        extended      196          58.2%                  <- untouched

    143 limit-carrying frames out of 17,952. **0.8% of coverage**, for a state that supplies a limit
    less than half the time it occurs and that the real road never settles in -- 1300 East resolved
    to `current` four seconds later and stayed there.

    `predicted` and `extended` are deliberately NOT refused. They are mapd projecting along a way it
    HAS matched, they carry a limit ~80% and ~58% of the time, and nothing has measured them doing
    harm. Refusing on suspicion is how a confidence policy starts costing coverage for nothing.

    This is the map-is-evidence rule as written: **a limit is an instruction to change speed, so
    refusing one costs coverage while honoring a wrong one costs safety.**
    """
    return str(self.sm['mapdOut'].waySelectionType) in _UNTRUSTED_WAY_MATCH

  def get_current_speed_limit(self) -> float:
    if not self.mapd_alive or self.way_match_untrusted:
      return 0.0
    return float(self.sm['mapdOut'].speedLimit)

  def get_current_road_name(self) -> str:
    if not self.mapd_alive:
      return ""
    return str(self.sm['mapdOut'].roadName)

  def get_next_speed_limit_and_distance(self) -> tuple[float, float]:
    """v2 publishes the distance directly.

    v1 published the next limit's LAT/LON and every consumer computed the distance itself, which is
    why OsmMapData carries that arithmetic. mapd knows where it is along the way and we do not, so
    its number is better than one we derive from a position that is a frame or two stale.
    """
    if not self.mapd_alive or self.way_match_untrusted:
      return 0.0, 0.0
    mapd = self.sm['mapdOut']
    nxt = float(mapd.nextSpeedLimit)
    dist = float(mapd.nextSpeedLimitDistance)

    # BluePilot: A LOWER NEXT LIMIT ON A MOTORWAY IS AN EXIT RAMP, NOT THIS ROAD SLOWING DOWN.
    #
    # Measured 2026-08-19 on route 00000393, and it is exactly the "why did it think 45" report.
    # On I-215 -- way 31535502, highwayClass motorway, and the tile on his own device carries
    # maxSpeed 31.2928 m/s = 70.0 mph -- mapd published:
    #
    #     speedLimit 70.0    nextSpeedLimit 45.0    waySelectionType current
    #
    # so mapd was right about both. The resolver's ease-down then adopted the 45: at 70 mph with
    # LIMIT_ADAPT_ACC -1.0 m/s^2 the adopt window is ~288 m, and inside it the map solution becomes
    # `next_speed_limit`. His set speed dropped to 45 on a freeway.
    #
    # THE ROOT CAUSE IS THAT MAPD PUBLISHES NO `nextHighwayClass` AND NO `nextWayId`. There is
    # nothing in the message that separates "the limit drops ahead ON THIS ROAD" from "there is a
    # 45 mph ramp ahead that mapd predicts we take". Upstream's ease-down assumes the former, which
    # is right on a surface street and wrong on a motorway.
    #
    # Motorway limits do not step down mid-way; a lower next limit there means a link. So refuse it
    # -- and note the refusal is narrow on purpose:
    #
    #   - `motorwayLink` is NOT included. Once the car is actually ON the ramp the drop is real and
    #     adopting it early is the whole point of the ease-down.
    #   - A HIGHER next limit is untouched, so leaving a zone onto a faster road still works.
    #   - If he does exit, the ramp becomes the CURRENT way and its limit applies immediately, and
    #     SCC-Map already owns the corner itself.
    #
    # What it costs: no pre-emptive set-speed drop for a ramp's posted limit while still on the
    # freeway. That was never reachable anyway -- the set speed falls at ~3.3 mph/s, which is the
    # whole of "the exit that never slows enough is not a tuning problem".
    if nxt > 0.0 and 0.0 < nxt < float(mapd.speedLimit) and str(mapd.highwayClass) == "motorway":
      return 0.0, 0.0

    return nxt, dist
