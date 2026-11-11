import csv
import importlib
import sys
import types
from types import SimpleNamespace

import pytest

from bluepilot.parked_recorder import battery_log_bp as bl


def row(t, filtered, power, label="stock"):
  return {"monoTime": t, "filteredMv": filtered, "powerDrawW": power, "label": label, "wallTime": 0, "voltageMv": filtered}


class TestLogFile:
  def test_header_once_and_rows_appended(self, tmp_path):
    p = str(tmp_path / "log.csv")
    bl.append_row(p, row(0, 12600, 4.0))
    bl.append_row(p, row(30, 12590, 4.1))
    rows = list(csv.DictReader(open(p)))
    assert [r["monoTime"] for r in rows] == ["0", "30"] and list(rows[0]) == bl.COLUMNS

  def test_rotates_instead_of_growing(self, tmp_path):
    p = str(tmp_path / "log.csv")
    for i in range(50):
      bl.append_row(p, row(i, 12600, 4.0), max_bytes=500)
    assert (tmp_path / "log.csv.1").exists()
    assert (tmp_path / "log.csv").stat().st_size < 1500

  def test_creates_missing_directory(self, tmp_path):
    bl.append_row(str(tmp_path / "a" / "b" / "log.csv"), row(0, 12600, 4.0))
    assert (tmp_path / "a" / "b" / "log.csv").exists()


class TestSummary:
  def test_slope_and_power_per_label(self, tmp_path):
    p = str(tmp_path / "log.csv")
    for i in range(0, 7200, 30):                      # 2 h, dropping 50 mV per hour, 4 W
      bl.append_row(p, row(i, 12600 - 50 * i / 3600, 4.0, "stock"))
    for i in range(0, 3600, 30):                      # 1 h, dropping 120 mV per hour, 6 W
      bl.append_row(p, row(10_000 + i, 12600 - 120 * i / 3600, 6.0, "recorder"))
    stock, rec = bl.summarize(p)
    assert stock.label == "stock" and rec.label == "recorder"
    assert stock.slope_v_per_h == pytest.approx(-0.05, abs=1e-3) and stock.mean_power_w == pytest.approx(4.0)
    assert rec.slope_v_per_h == pytest.approx(-0.12, abs=1e-3) and rec.max_power_w == 6.0
    assert stock.hours == pytest.approx(2.0, abs=0.01)

  def test_missing_file_and_bad_rows(self, tmp_path):
    assert bl.summarize(str(tmp_path / "nope.csv")) == []
    p = str(tmp_path / "log.csv")
    bl.append_row(p, row(0, "", 4.0))                 # no voltage: skipped
    bl.append_row(p, row(30, 12600, 4.0))
    assert bl.summarize(p) == []                      # only one usable sample: nothing to fit


UNKNOWN, GOOD = 0, 1


class FakeParams:
  def __init__(self, **v):
    self.v = v

  def get(self, key):
    return self.v[key]

  def get_bool(self, key):
    return bool(self.v.get(key, False))


class FakeSM:
  def __init__(self):
    self.t = 0.0
    self.updated = {"deviceState": False, "peripheralState": False}
    self.data = {
      "peripheralState": SimpleNamespace(pandaType=GOOD, voltage=12600),
      "deviceState": SimpleNamespace(powerDrawW=4.123, somPowerDrawW=3.0, offroadPowerUsageUwh=100, maxTempC=31.04,
                                     cpuUsagePercent=[10, 20], networkType="wifi", screenBrightnessPercent=0),
    }

  def __getitem__(self, k):
    return self.data[k]

  def update(self, timeout=0):
    pass


@pytest.fixture
def mod(monkeypatch):
  cereal = types.ModuleType("cereal")
  cereal.log = SimpleNamespace(PandaState=SimpleNamespace(PandaType=SimpleNamespace(unknown=UNKNOWN)))
  messaging = types.ModuleType("cereal.messaging")
  messaging.SubMaster = lambda s: FakeSM()
  cereal.messaging = messaging
  params_mod = types.ModuleType("openpilot.common.params")
  params_mod.Params = FakeParams
  for name, m in (("cereal", cereal), ("cereal.messaging", messaging), ("openpilot.common.params", params_mod)):
    monkeypatch.setitem(sys.modules, name, m)
  sys.modules.pop("bluepilot.parked_recorder.battery_logger_bp", None)
  return importlib.import_module("bluepilot.parked_recorder.battery_logger_bp")


def make(mod, tmp_path, **params):
  sm = FakeSM()
  p = FakeParams(ParkedBatteryLogLabel="stock", ParkedRecordingStatus={"state": "armed"}, **params)
  lg = mod.BatteryLogger(params=p, sm=sm, clock=lambda: sm.t, wall=lambda: 1.0e9, path=str(tmp_path / "log.csv"))
  return lg, sm


def tick(lg, sm, dt, both=True):
  sm.t += dt
  sm.updated = {"deviceState": both, "peripheralState": both}
  lg.update()


class TestLogger:
  def rows(self, tmp_path):
    return list(csv.DictReader(open(tmp_path / "log.csv")))

  def test_writes_one_row_per_interval(self, mod, tmp_path):
    lg, sm = make(mod, tmp_path)
    for _ in range(100):                              # 100 s at 1 Hz
      tick(lg, sm, 1.0)
    rows = self.rows(tmp_path)
    assert len(rows) == 4                             # at t=1, 31, 61, 91
    r = rows[0]
    assert r["label"] == "stock" and r["voltageMv"] == "12600" and r["powerDrawW"] == "4.12"
    assert r["recorderState"] == "armed" and r["cpuPercent"] == "15.0" and r["capture"] == "0"

  def test_no_row_without_a_valid_voltage(self, mod, tmp_path):
    lg, sm = make(mod, tmp_path)
    sm.data["peripheralState"].pandaType = UNKNOWN
    for _ in range(40):
      tick(lg, sm, 1.0)
    assert not (tmp_path / "log.csv").exists()

  def test_stale_messages_do_not_write(self, mod, tmp_path):
    lg, sm = make(mod, tmp_path)
    tick(lg, sm, 1.0)
    for _ in range(100):
      tick(lg, sm, 1.0, both=False)                   # nothing new from deviceState
    assert len(self.rows(tmp_path)) == 1
