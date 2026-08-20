"""FusionPilot: the v2 reader answers from mapdOut, and answers NOTHING when mapd is not there.

The four methods below are the entire interface between the map program and Speed Limit Assist --
BaseMapData turns them into liveMapDataSP and SLA, the resolver, ICBM and the HUD consume that
without knowing the source. So these four are the swap, and the one behavior that matters most is
the one that is easiest to get wrong under pressure: what happens when mapd v2 is selected and is
not publishing.

The answer is "no speed limit", not "v1's speed limit". A fallback would be silent, would make a
broken v2 install look like a working one, and is the same shape as the stub-laxer-than-the-device
failure recorded in CLAUDE.md. SLA already treats no-limit as a first-class state.
"""
import pytest

from sunnypilot.mapd.live_map_data.mapd_v2_map_data import MapdV2MapData


class FakeMapdOut:
  def __init__(self, **kw):
    self.speedLimit = kw.get("speedLimit", 0.0)
    self.roadName = kw.get("roadName", "")
    self.nextSpeedLimit = kw.get("nextSpeedLimit", 0.0)
    self.nextSpeedLimitDistance = kw.get("nextSpeedLimitDistance", 0.0)
    self.tileLoaded = kw.get("tileLoaded", True)
    self.waySelectionType = kw.get("waySelectionType", "current")
    # Defaults to a NON-motorway class deliberately. The motorway rule is a REFUSAL, so defaulting
    # to "motorway" would switch it on for every fixture in this file and hide the ordinary
    # ease-down it is meant to leave alone. `primary` is the road most of these describe (US 40).
    self.highwayClass = kw.get("highwayClass", "primary")


class FakeSubMaster:
  def __init__(self, alive: bool, valid: bool = True, **kw):
    self.alive = {"mapdOut": alive}
    self.valid = {"mapdOut": valid}
    self._data = {"mapdOut": FakeMapdOut(**kw)}

  def __getitem__(self, name):
    return self._data[name]


class ExplodingParams:
  """Any read of v1's shared memory is a bug, so make it loud instead of plausible."""

  def get(self, *a, **k):
    raise AssertionError("the v2 reader fell back to v1's /dev/shm/params")

  def put(self, *a, **k):
    pass


def make(alive: bool, valid: bool = True, **kw) -> MapdV2MapData:
  # __new__ rather than __init__: the real one builds a SubMaster, which needs compiled msgq. The
  # four getters are the logic under test and they only touch self.sm.
  obj = MapdV2MapData.__new__(MapdV2MapData)
  obj.sm = FakeSubMaster(alive, valid, **kw)
  obj.mem_params = ExplodingParams()
  obj._last_alive = None
  obj._last_tile_loaded = None
  obj._last_way_selection = None
  return obj


def test_values_pass_through_when_mapd_is_publishing():
  m = make(True, speedLimit=29.058, roadName="US 40;US 189",
           nextSpeedLimit=24.587, nextSpeedLimitDistance=412.0)
  assert m.get_current_speed_limit() == pytest.approx(29.058)
  assert m.get_current_road_name() == "US 40;US 189"
  assert m.get_next_speed_limit_and_distance() == (pytest.approx(24.587), pytest.approx(412.0))


def test_the_next_limit_distance_is_mapds_not_recomputed():
  """v1 published the next limit's lat/lon and every consumer derived the distance itself.

  mapd knows where it is along the way; we would be deriving it from a position a frame or two old.
  Guarded because "compute it like v1 did" is the obvious-looking change.
  """
  m = make(True, nextSpeedLimit=20.0, nextSpeedLimitDistance=758.0)
  _, distance = m.get_next_speed_limit_and_distance()
  assert distance == pytest.approx(758.0)


def test_no_limit_when_mapd_is_not_alive():
  m = make(False, speedLimit=29.058, roadName="US 40", nextSpeedLimit=24.6, nextSpeedLimitDistance=412.0)
  assert m.get_current_speed_limit() == 0.0
  assert m.get_current_road_name() == ""
  assert m.get_next_speed_limit_and_distance() == (0.0, 0.0)


def test_no_limit_when_mapd_is_alive_but_invalid():
  m = make(True, valid=False, speedLimit=29.058)
  assert m.get_current_speed_limit() == 0.0


def test_it_never_reads_v1s_shared_memory_for_a_limit():
  """ExplodingParams raises on any get. Covers both states, because a fallback would most likely be
  written into the not-alive branch where it looks helpful."""
  for alive in (True, False):
    m = make(alive, speedLimit=29.058)
    m.get_current_speed_limit()
    m.get_current_road_name()
    m.get_next_speed_limit_and_distance()


def test_transitions_are_logged_once_not_per_frame(monkeypatch):
  """Every drive has thousands of frames; a per-frame warning is a log nobody can read."""
  warnings: list[str] = []
  monkeypatch.setattr("sunnypilot.mapd.live_map_data.mapd_v2_map_data.cloudlog",
                      type("L", (), {"warning": staticmethod(lambda msg: warnings.append(msg))})())

  m = make(True, tileLoaded=True, waySelectionType="current")
  for _ in range(5):
    m._log_transitions()
  assert len(warnings) == 3, f"expected alive/tileLoaded/waySelection once each, got {warnings}"

  # The state that has never been visible in a route: the matcher giving up.
  m.sm._data["mapdOut"].waySelectionType = "fail"
  m._log_transitions()
  assert any("waySelectionType=fail" in w for w in warnings)


class TestALostRoadPublishesNoLimit:
  """A limit from a FAILED way match is a guess about which road we are on.

  `waySelectionType == fail` means mapd's matcher could not decide. Whether it also zeroes its
  `speedLimit` on those frames is unknown and cannot be settled without a drive -- so this gate is
  OUR confidence policy, not an assumption about mapd's behavior. If mapd zeroes it anyway the gate
  never fires and costs nothing; if it does not, this is the only thing standing between Speed Limit
  Assist and a limit for a road the car may not be on.

  Which way it actually is gets answered by the first observe route:
  `tools/bp_mapd_compare.py` now cross-tabs fail frames against non-zero speedLimit and says so.

  This matters ONLY at MapdV2 = 2, which has not happened yet -- which is exactly why the gate goes
  in now rather than after. The rule it comes from is in CLAUDE.md: the map may refuse freely, but
  must never be the sole thing that opens. A limit is an instruction to slow down or speed up.
  """

  def test_a_failed_match_yields_no_current_limit(self):
    m = make(True, speedLimit=29.058, waySelectionType="fail")
    assert m.get_current_speed_limit() == 0.0

  def test_a_failed_match_yields_no_next_limit_either(self):
    """The next limit is matched against the same way, so it is exactly as suspect."""
    m = make(True, nextSpeedLimit=24.587, nextSpeedLimitDistance=412.0, waySelectionType="fail")
    assert m.get_next_speed_limit_and_distance() == (0.0, 0.0)

  def test_every_confident_selection_still_passes_through(self):
    """The gate must catch `fail` and nothing else -- `extended` and `possible` are still matches."""
    for kind in ("current", "predicted", "possible", "extended"):
      m = make(True, speedLimit=29.058, waySelectionType=kind)
      assert m.get_current_speed_limit() == 29.058, f"{kind} was gated as though it had failed"


class TestALowerNextLimitOnAMotorwayIsARamp:
  """HIS 45 ON I-215, 2026-08-19, and mapd was RIGHT about every number it published.

  Route 00000393, way 31535502. The tile on his own device carries maxSpeed 31.2928 m/s = 70.0 mph,
  and mapd published exactly that, with `waySelectionType current`:

      speedLimit 70.0    nextSpeedLimit 45.0    sel current

  Upstream's ease-down then adopted the 45 -- at 70 mph with LIMIT_ADAPT_ACC -1.0 the adopt window
  is ~288 m -- and his set speed dropped to 45 on a freeway. The 45 belongs to an exit ramp.

  HE SETTLED IT FROM THE SEAT: *"I was in the left lane, which can't exit."* So the ramp was not a
  road he could physically have taken, and no amount of route prediction would have been right.

  mapd publishes no `nextHighwayClass` and no `nextWayId`, so nothing in the message separates
  "this road slows ahead" from "there is a ramp ahead we are predicted onto". On a motorway it is
  always the second: motorway limits do not step down mid-way.
  """

  def test_a_lower_next_limit_on_a_motorway_is_refused(self):
    m = make(True, speedLimit=31.2928, nextSpeedLimit=20.1168,
             nextSpeedLimitDistance=280.0, highwayClass="motorway")
    assert m.get_next_speed_limit_and_distance() == (0.0, 0.0), \
      "a 45 mph ramp was adopted while still on a 70 mph motorway"

  def test_the_current_limit_survives_the_refusal(self):
    """Refusing the ramp must not cost him the road he is actually on."""
    m = make(True, speedLimit=31.2928, nextSpeedLimit=20.1168,
             nextSpeedLimitDistance=280.0, highwayClass="motorway")
    assert m.get_current_speed_limit() == pytest.approx(31.2928)

  def test_a_lower_next_limit_off_a_motorway_still_eases_down(self):
    """The ordinary case the ease-down exists for. The refusal must not reach it."""
    m = make(True, speedLimit=29.058, nextSpeedLimit=24.587,
             nextSpeedLimitDistance=412.0, highwayClass="primary")
    assert m.get_next_speed_limit_and_distance() == (pytest.approx(24.587), pytest.approx(412.0))

  def test_a_motorway_LINK_keeps_its_lower_next_limit(self):
    """Once actually ON the ramp the drop is real and adopting it early is the point.

    This is why the rule compares `motorway` EXACTLY rather than by prefix -- `motorwayLink` shares
    its first eight characters and is the opposite case.
    """
    m = make(True, speedLimit=29.058, nextSpeedLimit=20.1168,
             nextSpeedLimitDistance=200.0, highwayClass="motorwayLink")
    assert m.get_next_speed_limit_and_distance() == (pytest.approx(20.1168), pytest.approx(200.0))

  def test_a_higher_next_limit_on_a_motorway_is_kept(self):
    """Only the LOWER direction is a ramp signature; leaving a zone onto a faster road is untouched."""
    m = make(True, speedLimit=20.1168, nextSpeedLimit=31.2928,
             nextSpeedLimitDistance=300.0, highwayClass="motorway")
    assert m.get_next_speed_limit_and_distance() == (pytest.approx(31.2928), pytest.approx(300.0))
