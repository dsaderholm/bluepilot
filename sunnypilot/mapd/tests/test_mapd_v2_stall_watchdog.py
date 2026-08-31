"""FusionPilot: mapd v2 can be ALIVE and silent, and nothing used to notice.

2026-08-30, routes 000003f5 through 000003f9. Five consecutive drives, 45 segments, ~185,000 moving
frames, and ZERO mapdOut:

    route   mapdOut   liveMapDataSP   speedLimit>0   managerState mapd_v2
    3f2       6,810        644          180/644      running 100%
    3f3      13,326        873          424/873      running 100%
    3f4      15,647        865          755/865      running 100%
    3f5           0        892            0/892      running 100%   <- died here
    3f6           0        506            0/506      running 100%
    3f7           0        312            0/312      running 100%
    3f8           0        336            0/336      running 100%
    3f9           0        491            0/491      running 100%

Speed Limit Assist had no limit for five drives. `managerState` read `running=True` with NO exit
code on every sample, memory 64-67% and temperature normal, so the process never died.

WHY `restart_if_crash=True` DOES NOT COVER THIS, which is the whole reason this file exists: that
flag watches for the process DYING (the 2026-08-24 failure, route 000003b4, 441 "not running:
mapd_v2" events). Here the process is alive. And 3f5, 3f7 and 3f8 each begin ~62 s after boot --
THREE FRESH BOOTS, all publishing nothing -- so it also survives a reboot, which was the documented
recovery and is wrong.

Ruled out by measurement before this was written: the build (same commit on all 8 routes), his
settings (the params snapshot diff shows only lateral gains moved), GPS (gpsLocation present
throughout), network (`none` on 100% of frames on the WORKING routes too), the tile store (US tiles
intact, and the dead routes never crossed a tile boundary), geography, and the clock reset (it
happens on every boot, working ones included).

The cause is still unknown. This does not diagnose it -- it recovers from it.
"""
import ast
import importlib
import os
import sys
import time
import types

import pytest

from cereal import log

import sunnypilot.mapd.live_map_data.mapd_v2_map_data as mv2
from sunnypilot.mapd.live_map_data.mapd_v2_map_data import MapdV2MapData, MAPD_V2_STALL_S

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


class FakeLocation:
  def __init__(self, valid: bool):
    self.valid = valid
    # The REAL enum. A guessed 1 made every stalled fixture read as an invalid localizer, so the
    # watchdog tests passed while never once exercising a stall.
    self.status = log.LiveLocationKalman.Status.valid if valid else 0
    self.positionGeodetic = self
    self.value = [40.7, -111.8, 1300.0]
    self.calibratedOrientationNED = self
    self.gpsOK = valid


class FakeMapdOut:
  tileLoaded = True
  waySelectionType = "current"


class FakeSubMaster:
  def __init__(self, mapd_alive: bool, localizer_valid: bool):
    self.alive = {"mapdOut": mapd_alive, "liveLocationKalman": True}
    self.valid = {"mapdOut": mapd_alive, "liveLocationKalman": True}
    self._data = {"liveLocationKalman": FakeLocation(localizer_valid), "mapdOut": FakeMapdOut()}

  def __getitem__(self, name):
    return self._data[name]


class QuietParams:
  def put(self, *a, **k):
    pass


def make(mapd_alive: bool, localizer_valid: bool) -> MapdV2MapData:
  """__new__ rather than __init__: the real one builds a SubMaster, which needs compiled msgq."""
  obj = MapdV2MapData.__new__(MapdV2MapData)
  obj.sm = FakeSubMaster(mapd_alive, localizer_valid)
  obj.mem_params = QuietParams()
  obj.last_bearing = None
  obj.last_position = None
  obj.localizer_valid = False
  obj._last_alive = mapd_alive          # suppress the transition cloudlog
  obj._last_tile_loaded = None
  obj._last_way_selection = None
  obj._stall_since = None
  return obj


@pytest.fixture
def clock(monkeypatch):
  """Drives `_now` in one-second steps. Real time cannot be used here: the monotonic clock's ~15 ms
  granularity on Windows makes several calls in one test return an identical value, which hid the
  re-anchoring mutant that disables the watchdog entirely."""
  class Clock:
    def __init__(self):
      self.t = 1000.0

    def __call__(self):
      return self.t

    def advance(self, dt):
      self.t += dt

  c = Clock()
  monkeypatch.setattr(mv2, "_now", c)
  return c


class TestTheStallClock:
  """Driven through the REAL update_location, not a mirror of its arithmetic."""

  def test_a_healthy_mapd_never_stalls(self):
    m = make(mapd_alive=True, localizer_valid=True)
    for _ in range(5):
      m.update_location()
    assert m._stall_since is None
    assert m.stalled_s == 0.0

  def test_silence_with_a_good_position_starts_the_clock(self):
    """The measured failure: the localizer knows exactly where the car is and mapd says nothing."""
    m = make(mapd_alive=False, localizer_valid=True)
    m.update_location()
    assert m._stall_since is not None, "mapdOut silent with a valid position is not being counted"

  def test_OFFROAD_SILENCE_IS_NOT_A_STALL(self):
    """The one that would bounce the process in his driveway. Parked, the localizer publishes
    nothing, so mapd v2 has no position to resolve and CORRECTLY emits zero frames -- CLAUDE.md
    records this as the reason observe mode cannot be verified from a parked car. A watchdog that
    counted it would restart mapd every 60 s forever while the car sat still."""
    m = make(mapd_alive=False, localizer_valid=False)
    for _ in range(10):
      m.update_location()
    assert m.stalled_s == 0.0
    assert m._stall_since is None

  def test_mapd_coming_back_clears_the_clock(self):
    """ASSERT ON `_stall_since`, NOT ON `stalled_s`. Deleting the reset survived mutation testing
    when this checked `stalled_s == 0.0`: Windows' time.monotonic() has ~15 ms granularity, so the
    set and the read land on the SAME tick and a clock that was never reset still reads exactly
    0.0. A float derived from a clock cannot witness the state that produced it."""
    m = make(mapd_alive=False, localizer_valid=True)
    m.update_location()
    assert m._stall_since is not None
    m.sm.alive["mapdOut"] = True
    m.sm.valid["mapdOut"] = True
    m.update_location()
    assert m._stall_since is None, "the stall clock survived mapd recovering"
    assert m.stalled_s == 0.0

  def test_THE_CLOCK_ANCHOR_IS_NOT_RESTARTED_WHILE_THE_STALL_CONTINUES(self, clock):
    """The mutant that kills the whole feature silently. Re-stamping `_stall_since` on every stalled
    tick keeps `stalled_s` near zero forever, so it never reaches MAPD_V2_STALL_S and the watchdog
    never fires -- while every other test here still passes."""
    m = make(mapd_alive=False, localizer_valid=True)
    m.update_location()
    anchor = m._stall_since
    assert anchor is not None
    for _ in range(5):
      clock.advance(1.0)
      m.update_location()
    assert m._stall_since == anchor, (
      "the stall clock is re-anchored on every tick, so elapsed silence never accumulates and "
      "MAPD_V2_STALL_S is unreachable -- the watchdog would never fire")
    assert m.stalled_s == 5.0, "elapsed silence is not being accumulated"

  def test_the_stall_grows_past_the_threshold_on_a_real_outage(self, clock):
    """End to end on the clock: silence with a good position eventually crosses MAPD_V2_STALL_S,
    which is what makes _watch_for_stall reachable at all."""
    m = make(mapd_alive=False, localizer_valid=True)
    m.update_location()
    assert m.stalled_s < MAPD_V2_STALL_S
    clock.advance(MAPD_V2_STALL_S + 1.0)
    m.update_location()
    assert m.stalled_s > MAPD_V2_STALL_S

  def test_the_clock_measures_real_elapsed_time(self):
    m = make(mapd_alive=False, localizer_valid=True)
    m._stall_since = time.monotonic() - (MAPD_V2_STALL_S + 5.0)
    assert m.stalled_s > MAPD_V2_STALL_S

  def test_reset_stall_zeroes_it(self):
    m = make(mapd_alive=False, localizer_valid=True)
    m._stall_since = time.monotonic() - 999.0
    m.reset_stall()
    assert m.stalled_s == 0.0

  def test_the_threshold_is_slower_than_startup_but_far_faster_than_the_failure(self):
    """60 s is not arbitrary. mapd v2 loads tiles at startup and is legitimately quiet for some
    seconds, while the failure this answers lasted for five entire drives -- so detection speed buys
    nothing and a short threshold buys a restart loop."""
    assert 30.0 <= MAPD_V2_STALL_S <= 180.0


@pytest.fixture
def mgr(monkeypatch):
  # `openpilot.system.micd` arrives through alertmanager and wants a sound device. Stub the MODULE,
  # not its chain, which is what interfaces.py needed for the same reason.
  #
  # NOT importorskip. The first version of this file used one, mapd_manager failed to import, and
  # all seven watchdog tests SKIPPED while the run reported 8 passed -- a green suite testing none
  # of the code this file exists for. A skip that hides a whole class is worse than a failure.
  def stub(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
      setattr(m, k, v)
    monkeypatch.setitem(sys.modules, name, m)

  class Paths:
    @staticmethod
    def mapd_root():
      return "/data/media/0/osm"

  stub("openpilot.system.micd", SAMPLE_RATE=44100, SAMPLE_BUFFER=4096)  # events reads both at import
  # `openpilot.system.hardware` is itself a stub in the offline runner, so it has no __path__ and
  # the .hw leaf cannot be imported through it. Seeding the leaf in sys.modules short-circuits that
  # -- the parent-binding trap CLAUDE.md records for interfaces.py.
  stub("openpilot.system.hardware.hw", Paths=Paths)
  stub("openpilot.system.version", is_prebuilt=lambda *a, **k: False)   # mapd_installer
  stub("openpilot.system.sentry", capture_exception=lambda *a, **k: None)
  stub("openpilot.common.spinner", Spinner=object)
  mod = importlib.import_module("sunnypilot.mapd.mapd_manager")

  class RecordingParams:
    def __init__(self):
      self.writes = []

    def put_bool(self, k, v):
      self.writes.append((k, v))

  fake = RecordingParams()
  monkeypatch.setattr(mod, "params", fake)
  return mod, fake


class StubSource:
  def __init__(self, stalled_s):
    self._s = stalled_s
    self.reset_calls = 0

  @property
  def stalled_s(self):
    return self._s

  def reset_stall(self):
    self.reset_calls += 1
    self._s = 0.0


class TestTheWatchdog:
  """Driven through the REAL _watch_for_stall."""

  def test_a_healthy_source_asks_for_nothing(self, mgr):
    mod, fake = mgr
    assert mod._watch_for_stall(StubSource(0.0), False, 0) == (False, 0)
    assert fake.writes == []

  def test_just_under_the_threshold_asks_for_nothing(self, mgr):
    mod, fake = mgr
    assert mod._watch_for_stall(StubSource(MAPD_V2_STALL_S - 0.1), False, 0) == (False, 0)
    assert fake.writes == []

  def test_a_real_stall_requests_a_restart(self, mgr):
    mod, fake = mgr
    pending, restarts = mod._watch_for_stall(StubSource(MAPD_V2_STALL_S + 1.0), False, 0)
    assert pending is True and restarts == 1
    assert ("MapdV2RestartRequest", True) in fake.writes

  def test_THE_REQUEST_IS_RELEASED_ON_THE_NEXT_TICK(self, mgr):
    """Held forever, this DISABLES mapd v2 -- `mapd_v2_ready` returns False while it stands, so the
    watchdog would become the thing that stopped the daemon it exists to protect."""
    mod, fake = mgr
    pending, _ = mod._watch_for_stall(StubSource(0.0), True, 1)
    assert pending is False
    assert fake.writes == [("MapdV2RestartRequest", False)]

  def test_THE_STALL_CLOCK_IS_RESET_WHEN_THE_RESTART_IS_REQUESTED(self, mgr):
    """Without this the clock keeps running while the new process loads tiles, so every remaining
    restart in the budget is spent within a few ticks and the watchdog is finished before mapd has
    had one chance to come up."""
    mod, _ = mgr
    src = StubSource(MAPD_V2_STALL_S + 1.0)
    mod._watch_for_stall(src, False, 0)
    assert src.reset_calls == 1
    assert src.stalled_s == 0.0

  def test_the_restart_budget_is_finite(self, mgr):
    mod, fake = mgr
    pending, restarts = mod._watch_for_stall(StubSource(MAPD_V2_STALL_S * 10), False,
                                             mod.MAPD_V2_MAX_RESTARTS)
    assert pending is False and restarts == mod.MAPD_V2_MAX_RESTARTS
    assert fake.writes == [], "restarting forever is a core spent on a cause we have not found"

  def test_observe_and_off_states_are_untouched(self, mgr):
    """mapd_manager passes None unless the state is 2. In state 1 SLA reads v1, so a quiet v2 costs
    the comparison and nothing on the road; in state 0 v2 is not running at all."""
    mod, fake = mgr
    assert mod._watch_for_stall(None, False, 0) == (False, 0)
    assert fake.writes == []


def _fn(name):
  src = open(os.path.join(ROOT, "system/manager/process_config.py"), encoding="utf-8").read()
  for node in ast.walk(ast.parse(src)):
    if isinstance(node, ast.FunctionDef) and node.name == name:
      return ast.unparse(node)
  raise AssertionError(f"{name} is gone from process_config")


class TestTheManagerGate:
  """process_config cannot be imported offline -- it pulls in the whole manager chain -- so this
  reads it, which is what the rest of the fork does for surfaces the suite cannot execute."""

  def test_mapd_v2_ready_honors_the_restart_request(self):
    assert "MapdV2RestartRequest" in _fn("mapd_v2_ready"), (
      "mapd_v2_ready ignores the stall watchdog's restart request -- mapd_manager sets it and "
      "manager never stops the process, so a stalled mapd v2 stays stalled for the whole drive")

  def test_the_opt_in_check_still_comes_first(self):
    """State 0 must still run nothing -- somebody tracking this branch for ICBM alone should not pay
    a fifth of a core for a migration that is ours."""
    src = _fn("mapd_v2_ready")
    assert "MapdV2" in src and "> 0" in src
    assert src.index("return_default=True) > 0") < src.index("MapdV2RestartRequest"), (
      "the restart request is consulted before the opt-in check, so state 0 reads a param that "
      "only means anything when v2 is enabled")

  def test_the_param_is_declared(self):
    keys = open(os.path.join(ROOT, "common/params_keys.h"), encoding="utf-8").read()
    assert "MapdV2RestartRequest" in keys, "the stubbed Params raises on unknown keys, as the device does"

  def test_THE_REQUEST_CANNOT_SURVIVE_A_BOOT(self):
    """A request left set by a mapd_manager that died would keep mapd_v2 stopped indefinitely. Two
    independent clears: the param flag, and mapd_manager writing False at startup."""
    keys = open(os.path.join(ROOT, "common/params_keys.h"), encoding="utf-8").read()
    line = next(x for x in keys.splitlines() if "MapdV2RestartRequest" in x)
    assert "CLEAR_ON_MANAGER_START" in line, f"not cleared on manager start: {line.strip()}"
    assert "PERSISTENT" not in line, f"a PERSISTENT request can disable mapd v2 forever: {line.strip()}"
    mgr_src = open(os.path.join(ROOT, "sunnypilot/mapd/mapd_manager.py"), encoding="utf-8").read()
    assert 'put_bool("MapdV2RestartRequest", False)' in mgr_src
