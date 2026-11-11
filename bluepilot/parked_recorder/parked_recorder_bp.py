#!/usr/bin/env python3
"""Parked recorder process (phase 1: detect bumps and log events; no camera capture yet).

Runs only while the car is off and ParkedRecordingEnabled is set (see process_config.py). It reads the
accelerometer, applies the battery guards, and writes one event directory per bump. The decisions live in
detector_bp / guards_bp / storage_bp so they can be tested without hardware.

Battery safety: the guard here stops the recorder early, but the real backstop is hardwared, which shuts the
device down at the end of the window (power_monitoring.effective_max_time_offroad_s) and at the stock
low-voltage floor, whether or not this process is alive.
"""
import time

import cereal.messaging as messaging
from cereal import log
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

from bluepilot.parked_recorder import storage_bp
from bluepilot.parked_recorder.detector_bp import ParkedTrigger
from bluepilot.parked_recorder.guards_bp import ParkedGuard, VoltageLPF

ARM_DELAY_S = 120.0
COOLDOWN_S = 30.0
CONFIG_REFRESH_S = 5.0
VOLTAGE_STALE_S = 10.0           # no fresh panda voltage for this long counts as "no voltage" (fails closed)
MIN_FREE_BYTES = 2 * 1024 ** 3   # do not write events when the disk is this close to full


class ParkedRecorder:
  def __init__(self, params=None, sm=None, clock=time.monotonic, events_root: str = storage_bp.EVENTS_ROOT):
    self.params = params or Params()
    self.sm = sm if sm is not None else messaging.SubMaster(["accelerometer", "peripheralState"])
    self.clock = clock
    self.events_root = events_root
    t0 = clock()
    self.trigger = ParkedTrigger(t0, arm_delay_s=ARM_DELAY_S, cooldown_s=COOLDOWN_S)
    self.guard = ParkedGuard(t0)
    self.lpf = VoltageLPF()
    self.voltage_t: float | None = None
    self.max_storage_mb = 2048
    self._last_config_t = -CONFIG_REFRESH_S
    self._last_status: dict | None = None

  def _status(self, state: str, **extra) -> None:
    status = {"state": state, **extra}
    if status == self._last_status:
      return
    self._last_status = status
    try:
      self.params.put("ParkedRecordingStatus", status)
    except Exception:
      cloudlog.exception("parked_recorder: failed to write status")

  def _refresh_config(self, now: float) -> None:
    if now - self._last_config_t < CONFIG_REFRESH_S:
      return
    self._last_config_t = now
    try:
      self.trigger.detector.set_sensitivity(self.params.get("ParkedRecordingSensitivity"))
      self.guard.configure(self.params.get("ParkedRecordingMaxHours"), self.params.get("ParkedRecordingMinVoltage"))
      self.max_storage_mb = storage_bp.clamp_storage_mb(self.params.get("ParkedRecordingMaxStorageMB"))
    except Exception:
      # unreadable settings: keep what we had (the guard always has clamped values)
      cloudlog.exception("parked_recorder: config read failed")

  def _filtered_voltage_mV(self, now: float) -> float | None:
    if self.voltage_t is None or now - self.voltage_t > VOLTAGE_STALE_S:
      return None
    return self.lpf.value_mV

  def _record_event(self, motion, now: float) -> None:
    free = storage_bp.free_bytes(self.events_root)
    if free is not None and free < MIN_FREE_BYTES:
      self._status("storage_full", freeBytes=free)
      return
    event_dir = storage_bp.new_event_dir(self.events_root)
    entry = storage_bp.write_event(event_dir, {
      "kind": "bump",
      "detectedAt": storage_bp.now_iso(),
      "score": round(motion.score, 2),
      "hits": motion.hits,
      "voltageV": round((self.lpf.value_mV or 0) / 1000.0, 2),
      "files": [],   # phase 2 adds the captured media here
    })
    removed = storage_bp.enforce_quota(self.events_root, self.max_storage_mb, keep=event_dir)
    self.params.put("ParkedRecordingLastEvent", entry)
    cloudlog.event("parked_recorder.event", event_id=entry["eventId"], score=entry["score"], removed=removed)
    self._status("armed", lastEvent=entry["eventId"])

  def update(self) -> None:
    self.sm.update(100)
    now = self.clock()

    if self.sm.updated["peripheralState"]:
      ps = self.sm["peripheralState"]
      if ps.pandaType != log.PandaState.PandaType.unknown:
        self.lpf.update(now, ps.voltage)
        self.voltage_t = now

    self._refresh_config(now)

    if self.guard.update(now, self._filtered_voltage_mV(now)) is not None:
      if self.params.get_bool("ParkedRecordingActive"):
        # window over: release sensord so it stops too; the device itself is shut down by hardwared
        self.params.put_bool("ParkedRecordingActive", False)
        cloudlog.warning(f"parked_recorder: stopped, reason={self.guard.tripped}")
      self._status("stopped", reason=self.guard.tripped)
      return

    self._status("armed" if self.trigger.armed(now) else "arming")
    if self.sm.updated["accelerometer"]:
      t = self.sm.logMonoTime["accelerometer"] * 1e-9
      motion = self.trigger.update(t, list(self.sm["accelerometer"].acceleration.v))
      if motion is not None:
        self._record_event(motion, now)

  def run(self) -> None:
    self.params.put_bool("ParkedRecordingActive", True)
    try:
      while True:
        self.update()
    finally:
      self.params.put_bool("ParkedRecordingActive", False)
      self._status("disabled")


def main() -> None:
  ParkedRecorder().run()


if __name__ == "__main__":
  main()
