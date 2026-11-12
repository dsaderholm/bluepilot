"""Runs the real system/hardware/power_monitoring.py (with the openpilot pieces it imports replaced by stand-ins) to check the
parked recorder's hooks in it: the voltage floor, and that stock behaviour is untouched when the recorder is off."""
import importlib.util
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


class FakeParams:
  def __init__(self, **values):
    self.values = {"CarBatteryCapacity": 30_000_000, "MaxTimeOffroad": 1800, "ParkedRecordingEnabled": False,
                   "ParkedRecordingMaxHours": 4.0, "ParkedRecordingMinVoltage": 12.3, "DisablePowerDown": False,
                   "ForcePowerDown": False, **values}
    self.puts = {}

  def get(self, key):
    return self.values[key]

  def get_bool(self, key):
    return bool(self.values.get(key, False))

  def put(self, key, value):
    self.puts[key] = value


@pytest.fixture
def pm_module(monkeypatch):
  stubs = {
    "openpilot.common.params": SimpleNamespace(Params=FakeParams),
    "openpilot.system.hardware": SimpleNamespace(HARDWARE=SimpleNamespace(get_current_power_draw=lambda: 2.0)),
    "openpilot.common.swaglog": SimpleNamespace(cloudlog=SimpleNamespace(exception=lambda *a, **k: None)),
    "openpilot.system.statsd": SimpleNamespace(statlog=SimpleNamespace(gauge=lambda *a, **k: None)),
  }
  for name, attrs in stubs.items():
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs.__dict__)
    monkeypatch.setitem(sys.modules, name, mod)
  spec = importlib.util.spec_from_file_location("bp_test_power_monitoring", ROOT / "system" / "hardware" / "power_monitoring.py")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def make(pm_module, volts_mV, **params):
  pm = pm_module.PowerMonitoring()
  pm.params = FakeParams(**params)
  pm.car_voltage_mV = volts_mV
  return pm


def shutdown(pm, offroad_s=7200, ignition=False, in_car=True):
  return pm.should_shutdown(ignition, in_car, time.monotonic() - offroad_s, True)


def test_recorder_off_leaves_stock_behaviour_alone(pm_module):
  pm = make(pm_module, 12200)                      # under 12.3 V but above the stock 11.8 V floor
  assert shutdown(pm) is False
  assert pm.params.puts == {}


def test_stock_floor_still_applies_with_the_recorder_off(pm_module):
  pm = make(pm_module, 11700)
  assert shutdown(pm) is True


def test_recorder_on_shuts_down_under_its_floor_and_records_why(pm_module):
  pm = make(pm_module, 12200, ParkedRecordingEnabled=True)
  assert shutdown(pm) is True
  reason = pm.params.puts["ParkedRecordingShutdownReason"]
  assert reason["reason"] == "low_voltage" and reason["voltageV"] == 12.2 and reason["floorV"] == 12.3


def test_recorder_on_but_voltage_healthy_stays_up(pm_module):
  pm = make(pm_module, 12500, ParkedRecordingEnabled=True)
  assert shutdown(pm) is False
  assert pm.params.puts == {}


def test_reason_is_written_once(pm_module):
  pm = make(pm_module, 12200, ParkedRecordingEnabled=True)
  shutdown(pm)
  pm.params.puts.clear()
  shutdown(pm)
  assert pm.params.puts == {}


def test_disable_power_down_still_wins(pm_module):
  pm = make(pm_module, 12000, ParkedRecordingEnabled=True, DisablePowerDown=True)
  assert shutdown(pm) is False


def test_ignition_on_never_shuts_down(pm_module):
  pm = make(pm_module, 12000, ParkedRecordingEnabled=True)
  assert shutdown(pm, ignition=True) is False


def test_not_in_a_car_never_shuts_down(pm_module):
  pm = make(pm_module, 5000, ParkedRecordingEnabled=True)      # the bench comma: about 5 V, no car
  assert shutdown(pm, in_car=False) is False


def test_a_hook_failure_falls_back_to_the_stock_rules(pm_module):
  pm = make(pm_module, 12200, ParkedRecordingEnabled=True)
  pm.params.values.pop("ParkedRecordingMinVoltage")            # reading it raises KeyError
  assert shutdown(pm) is False                                 # stock rules only: 12.2 V is above 11.8 V


def test_the_window_hook_only_raises_the_limit(pm_module):
  assert pm_module.effective_max_time_offroad_s(1800 * 60, True, 4.0, 12600, 12.3) == 1800 * 60   # stock 30 h stays
  pm = make(pm_module, 12600, ParkedRecordingEnabled=True, MaxTimeOffroad=30)                    # 30 min stock
  assert pm.max_time_offroad_exceeded(3600) is False                                             # raised to the 4 h window
  assert pm.max_time_offroad_exceeded(4 * 3600) is True
