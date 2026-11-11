"""Tests the process glue with a fake SubMaster and Params.

The compiled Params and cereal are not needed: the modules the process imports from openpilot are replaced
with stand-ins before it is imported.
"""
import importlib
import json
import sys
import types
from types import SimpleNamespace

import pytest

UNKNOWN, GOOD = 0, 1
DT = 1 / 104


class FakeParams:
  def __init__(self, **values):
    self.values = {
      "ParkedRecordingSensitivity": 3.0, "ParkedRecordingMaxHours": 4.0, "ParkedRecordingMinVoltage": 12.3,
      "ParkedRecordingMaxStorageMB": 2048, "ParkedRecordingActive": False, **values,
    }

  def get(self, key):
    return self.values[key]

  def get_bool(self, key):
    return bool(self.values.get(key, False))

  def put(self, key, value):
    self.values[key] = value

  def put_bool(self, key, value):
    self.values[key] = value


class FakeSM:
  """One accelerometer sample (and optionally a voltage) per update()."""

  def __init__(self):
    self.updated = {"accelerometer": False, "peripheralState": False}
    self.logMonoTime = {"accelerometer": 0}
    self.data = {
      "accelerometer": SimpleNamespace(acceleration=SimpleNamespace(v=[0.0, 0.0, 9.81])),
      "peripheralState": SimpleNamespace(pandaType=GOOD, voltage=12600),
    }
    self.t = 1000.0

  def __getitem__(self, key):
    return self.data[key]

  def update(self, timeout=0):
    pass

  def step(self, accel=(0.0, 0.0, 9.81), voltage=12600, panda=GOOD, with_voltage=True):
    self.t += DT
    self.logMonoTime["accelerometer"] = int(self.t * 1e9)
    self.data["accelerometer"].acceleration.v = list(accel)
    self.data["peripheralState"].voltage = voltage
    self.data["peripheralState"].pandaType = panda
    self.updated = {"accelerometer": True, "peripheralState": with_voltage}


@pytest.fixture
def mod(monkeypatch):
  cereal = types.ModuleType("cereal")
  cereal.log = SimpleNamespace(PandaState=SimpleNamespace(PandaType=SimpleNamespace(unknown=UNKNOWN)))
  messaging = types.ModuleType("cereal.messaging")
  messaging.SubMaster = lambda services: FakeSM()
  cereal.messaging = messaging
  params_mod = types.ModuleType("openpilot.common.params")
  params_mod.Params = FakeParams
  swaglog = types.ModuleType("openpilot.common.swaglog")
  swaglog.cloudlog = SimpleNamespace(event=lambda *a, **k: None, warning=lambda *a, **k: None,
                                     exception=lambda *a, **k: None)
  for name, m in (("cereal", cereal), ("cereal.messaging", messaging), ("openpilot.common.params", params_mod),
                  ("openpilot.common.swaglog", swaglog)):
    monkeypatch.setitem(sys.modules, name, m)
  sys.modules.pop("bluepilot.parked_recorder.parked_recorder_bp", None)
  return importlib.import_module("bluepilot.parked_recorder.parked_recorder_bp")


class Clock:
  def __init__(self, sm):
    self.sm = sm

  def __call__(self):
    return self.sm.t


def make(mod, tmp_path, **param_values):
  sm = FakeSM()
  params = FakeParams(**param_values)
  rec = mod.ParkedRecorder(params=params, sm=sm, clock=Clock(sm), events_root=str(tmp_path))
  return rec, sm, params


def advance(rec, sm, seconds, **kw):
  for _ in range(int(seconds / DT)):
    sm.step(**kw)
    rec.update()


def status(params):
  return params.values.get("ParkedRecordingStatus", {})


def test_arming_then_armed(mod, tmp_path):
  rec, sm, params = make(mod, tmp_path)
  advance(rec, sm, 5)
  assert status(params)["state"] == "arming"
  advance(rec, sm, 125)
  assert status(params)["state"] == "armed"


def test_bump_before_armed_makes_no_event(mod, tmp_path):
  rec, sm, params = make(mod, tmp_path)
  advance(rec, sm, 5)
  sm.step(accel=(0.0, 0.0, 11.0))
  rec.update()
  assert list(tmp_path.iterdir()) == []


def test_bump_after_armed_writes_event(mod, tmp_path):
  rec, sm, params = make(mod, tmp_path)
  advance(rec, sm, 125)
  sm.step(accel=(0.0, 0.0, 11.0))
  rec.update()
  events = list(tmp_path.iterdir())
  assert len(events) == 1
  entry = json.loads((events[0] / "event.json").read_text())
  assert entry["kind"] == "bump" and entry["eventId"] == events[0].name and entry["voltageV"] == 12.6
  assert params.values["ParkedRecordingLastEvent"]["eventId"] == events[0].name


def test_low_voltage_stops_and_releases_sensord(mod, tmp_path):
  rec, sm, params = make(mod, tmp_path)
  params.values["ParkedRecordingActive"] = True
  advance(rec, sm, 10, voltage=12000)         # well under the 12.3 V floor
  advance(rec, sm, 40, voltage=12000)         # held past the 30 s hold
  assert status(params) == {"state": "stopped", "reason": "voltage"}
  assert params.values["ParkedRecordingActive"] is False
  # stopped means stopped: a bump now is ignored
  advance(rec, sm, 125, voltage=12000)
  sm.step(accel=(0.0, 0.0, 11.0), voltage=12000)
  rec.update()
  assert list(tmp_path.iterdir()) == []


def test_window_expiry_stops(mod, tmp_path):
  rec, sm, params = make(mod, tmp_path, ParkedRecordingMaxHours=0.25)  # 15 minutes
  params.values["ParkedRecordingActive"] = True
  sm.t += 15 * 60                              # jump the clock past the window
  sm.step()
  rec.update()
  assert status(params) == {"state": "stopped", "reason": "time"}
  assert params.values["ParkedRecordingActive"] is False


def test_no_panda_voltage_fails_closed(mod, tmp_path):
  rec, sm, params = make(mod, tmp_path)
  advance(rec, sm, 1, panda=UNKNOWN)
  assert status(params) == {"state": "stopped", "reason": "no_voltage"}


def test_stale_voltage_fails_closed(mod, tmp_path):
  rec, sm, params = make(mod, tmp_path)
  advance(rec, sm, 1)                          # one good reading
  advance(rec, sm, 12, with_voltage=False)     # then nothing for longer than the stale limit
  assert status(params) == {"state": "stopped", "reason": "no_voltage"}


def test_full_disk_skips_event(mod, tmp_path, monkeypatch):
  monkeypatch.setattr(mod.storage_bp, "free_bytes", lambda root: 100)
  rec, sm, params = make(mod, tmp_path)
  advance(rec, sm, 125)
  sm.step(accel=(0.0, 0.0, 11.0))
  rec.update()
  assert list(tmp_path.iterdir()) == []
  assert status(params)["state"] == "storage_full"


def test_run_clears_active_on_exit(mod, tmp_path):
  rec, sm, params = make(mod, tmp_path)

  def boom():
    raise KeyboardInterrupt
  rec.update = boom
  with pytest.raises(KeyboardInterrupt):
    rec.run()
  assert params.values["ParkedRecordingActive"] is False
  assert status(params)["state"] == "disabled"
