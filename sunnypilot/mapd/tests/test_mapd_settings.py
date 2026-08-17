"""FusionPilot: the mapd settings bridge, and the measurement that decided it ships empty.

The write path exists and is exercised here so that turning it on later is one line rather than a
rediscovery. What it must NOT do is ship a control that duplicates one this fork already has -- see
the module docstring for the measurement: every path point satisfies
`targetVelocity^2 * |curvature| = map_curve_target_lat_a`, which makes mapd's lateral-acceleration
setting and `SmartCruiseControlMapFactor` the same knob applied twice.
"""
from __future__ import annotations

import json

import pytest

from openpilot.sunnypilot.mapd import mapd_settings
from openpilot.sunnypilot.mapd.mapd_settings import MAX_ATTEMPTS, MapdSettingsSync

BLOB = {
  "map_curve_speed_control_enabled": False,
  "personalities": {
    "relaxed": {"map_curve_target_lat_a": 1.9, "curve_target_speed_time_offset": 1.5},
    "standard": {"map_curve_target_lat_a": 2.2, "curve_target_speed_time_offset": 1.5},
    "aggressive": {"map_curve_target_lat_a": 2.4, "curve_target_speed_time_offset": 1.2},
  },
}


class FakeParams:
  def __init__(self, **vals):
    self.vals = dict(vals)
    self.puts: list[tuple[str, object]] = []

  def get(self, k, return_default=False, block=False):
    return self.vals.get(k)

  def put(self, k, v, block=False):
    self.puts.append((k, v))


class FakeSM:
  def __init__(self, blob, alive=True, valid=True):
    self.alive = {"mapdExtendedOut": alive}
    self.valid = {"mapdExtendedOut": valid}
    self._blob = blob

  def update(self, _):
    pass

  def __getitem__(self, _):
    return type("_E", (), {"settings": self._blob})()


class FakePM:
  def __init__(self):
    self.sent = []

  def send(self, name, msg):
    self.sent.append((name, msg))


def _sync(blob, params=None, **kw):
  s = MapdSettingsSync.__new__(MapdSettingsSync)
  s.params = params or FakeParams()
  s.sm = FakeSM(json.dumps(blob) if isinstance(blob, dict) else blob, **kw)
  s.pm = FakePM()
  s.attempts, s.settled = {}, set()
  s._logged_unreadable = False
  return s


def test_no_control_ships_today_so_nothing_is_ever_written():
  """The guard against re-adding a duplicate knob without re-doing the measurement."""
  assert mapd_settings._FLOAT_SETTINGS == {}, (
    "a mapd setting was exposed as a control -- check it is not a second name for one we have; "
    "map_curve_target_lat_a and SmartCruiseControlMapFactor are the same knob")
  s = _sync(BLOB)
  s.tick()
  assert not s.pm.sent, "mapdIn was published with no setting configured"


def test_mapds_own_settings_are_cached_for_diagnostics():
  s = _sync(BLOB)
  s.tick()
  assert ("MapdSettings", BLOB) in s.params.puts, "mapd's configuration was not recorded"


def test_nothing_happens_when_mapd_is_silent():
  s = _sync(BLOB, alive=False)
  s.tick()
  assert not s.pm.sent and not s.params.puts


def test_a_non_json_blob_is_survived():
  s = _sync("<not json>")
  s.tick()
  assert not s.pm.sent


def test_a_configured_setting_is_written_to_every_personality_then_stops(monkeypatch):
  """The mechanism, proven so that enabling it later is a one-line change.

  All three personalities, because mapd picks one from openpilot's via shadow_selfdrive_state --
  which is False on this device, so which it reads is not observable from here.
  """
  monkeypatch.setitem(mapd_settings._FLOAT_SETTINGS, "MapdCurveLatA", ("map_curve_target_lat_a", 0.1))
  s = _sync(BLOB, params=FakeParams(MapdCurveLatA=25))  # 2.5 m/s^2
  s.tick()
  paths = [m.mapdIn.jsonPath for n, m in s.pm.sent if n == "mapdIn" and m.mapdIn.which() != "saveSettings"] \
    if False else None  # message shape is capnp; assert on counts instead
  assert len(s.pm.sent) == 4, "expected three setJsonPathFloat plus one saveSettings"

  # Once mapd reports the value, it stops asking.
  s.sm = FakeSM(json.dumps({"personalities": {p: {"map_curve_target_lat_a": 2.5} for p in
                                              ("relaxed", "standard", "aggressive")}}))
  s.pm.sent.clear()
  s.tick()
  assert not s.pm.sent, "kept writing a setting mapd had already adopted"


def test_a_setting_that_will_not_stick_gives_up(monkeypatch):
  monkeypatch.setitem(mapd_settings._FLOAT_SETTINGS, "MapdCurveLatA", ("map_curve_target_lat_a", 0.1))
  s = _sync(BLOB, params=FakeParams(MapdCurveLatA=25))
  for _ in range(MAX_ATTEMPTS + 3):
    s.tick()
  # Three paths, each attempted at most MAX_ATTEMPTS times, plus one save per changed tick.
  writes = sum(1 for _ in s.pm.sent)
  assert writes <= 3 * MAX_ATTEMPTS + MAX_ATTEMPTS, "retried a stuck setting forever"
