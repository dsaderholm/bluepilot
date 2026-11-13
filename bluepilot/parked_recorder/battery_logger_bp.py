#!/usr/bin/env python3
"""Parked battery logger process.

Runs only while the car is off and ParkedBatteryLog is set (see process_config.py). Every 30 s it appends a row
to the CSV with the car voltage (raw and filtered), the comma's power draw, temperature, network, and what the
parked recorder is doing. Reads only: it changes nothing and its own cost is a few wakeups a minute.
"""
import time

import cereal.messaging as messaging
from cereal import log
from openpilot.common.params import Params

from bluepilot.parked_recorder import battery_log_bp
from bluepilot.parked_recorder.guards_bp import VoltageLPF


class BatteryLogger:
  def __init__(self, params=None, sm=None, clock=time.monotonic, wall=time.time, path: str = battery_log_bp.LOG_PATH):  # noqa: TID251
    self.params = params or Params()
    self.sm = sm if sm is not None else messaging.SubMaster(["deviceState", "peripheralState"])
    self.clock = clock
    self.wall = wall
    self.path = path
    self.lpf = VoltageLPF()
    self.raw_mV: float | None = None
    self._last_row_t: float | None = None

  def _status_text(self, key: str) -> str:
    try:
      value = self.params.get(key)
    except Exception:
      return ""
    return value.get("state", "") if isinstance(value, dict) else ""

  def update(self) -> None:
    self.sm.update(1000)
    now = self.clock()

    if self.sm.updated["peripheralState"]:
      ps = self.sm["peripheralState"]
      if ps.pandaType != log.PandaState.PandaType.unknown:
        self.raw_mV = float(ps.voltage)
        self.lpf.update(now, self.raw_mV)

    if self.raw_mV is None or not self.sm.updated["deviceState"]:
      return
    if self._last_row_t is not None and now - self._last_row_t < battery_log_bp.SAMPLE_INTERVAL_S:
      return
    self._last_row_t = now

    ds = self.sm["deviceState"]
    try:
      label = self.params.get("ParkedBatteryLogLabel") or ""
    except Exception:
      label = ""
    battery_log_bp.append_row(self.path, {
      "wallTime": round(self.wall(), 1), "monoTime": round(now, 1), "label": label,
      "voltageMv": round(self.raw_mV), "filteredMv": round(self.lpf.value_mV or 0),
      "powerDrawW": round(ds.powerDrawW, 2), "somPowerDrawW": round(ds.somPowerDrawW, 2),
      "offroadUwh": ds.offroadPowerUsageUwh, "maxTempC": round(ds.maxTempC, 1),
      "cpuPercent": round(sum(ds.cpuUsagePercent) / max(1, len(ds.cpuUsagePercent)), 1),
      "network": str(ds.networkType), "screenPercent": ds.screenBrightnessPercent,
      "recorderState": self._status_text("ParkedRecordingStatus"),
      "capture": int(bool(self.params.get_bool("ParkedRecordingCapture"))),
    })

  def run(self) -> None:
    while True:
      self.update()


def main() -> None:
  BatteryLogger().run()


if __name__ == "__main__":
  main()
