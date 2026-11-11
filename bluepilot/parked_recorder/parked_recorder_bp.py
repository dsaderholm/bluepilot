#!/usr/bin/env python3
"""Parked recorder process.

Runs only while the car is off and ParkedRecordingEnabled is set (see process_config.py). It reads the
accelerometer, applies the battery guards, and on a bump writes an event directory and then records a short
clip: it raises ParkedRecordingCapture, which makes the manager start camerad and the stream encoder, and
writes the encoder's H.264 frames to disk. The decisions live in detector_bp / guards_bp / storage_bp /
capture_bp so they can be tested without hardware.

Battery safety: the guard here stops the recorder early (cameras and accelerometer off). Powering the device off is hardwared's
job: power_monitoring shuts the device down under the recorder's voltage floor (guards_bp.sentry_voltage_shutdown) even if this
process has hung. The 4 h window does not power the device off; the comma then idles under the stock offroad rules.

The cameras only start after the trigger, so a clip shows the seconds after the bump (a few seconds late),
not the bump itself. Keeping cameras on while armed would catch it but costs far more power.
"""
import time

import cereal.messaging as messaging
from cereal import log
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

from bluepilot.parked_recorder import storage_bp
from bluepilot.parked_recorder.capture_bp import ClipWriter, EventRateLimiter, clamp_clip_seconds
from bluepilot.parked_recorder.detector_bp import ParkedTrigger
from bluepilot.parked_recorder.guards_bp import ParkedGuard, VoltageLPF
from bluepilot.parked_recorder.warning_bp import WarningTracker, clamp_warning_seconds

ARM_DELAY_S = 120.0
COOLDOWN_S = 30.0
CONFIG_REFRESH_S = 5.0
VOLTAGE_STALE_S = 10.0           # no fresh panda voltage for this long counts as "no voltage" (fails closed)
MIN_FREE_BYTES = 2 * 1024 ** 3   # do not write events when the disk is this close to full
MAX_CAPTURES_PER_HOUR = 12

# camera name -> livestream encode service. The wide road camera shows the front; the driver camera the cabin.
CAPTURE_CAMERAS = {
  "wideRoad": "livestreamWideRoadEncodeData",
  "driver": "livestreamDriverEncodeData",
}


class ParkedRecorder:
  def __init__(self, params=None, sm=None, clock=time.monotonic, events_root: str = storage_bp.EVENTS_ROOT,
               make_sock=None, drain=None):
    self.params = params or Params()
    self.sm = sm if sm is not None else messaging.SubMaster(["accelerometer", "peripheralState"])
    self.clock = clock
    self.events_root = events_root
    # not conflated: dropping a frame would corrupt the H.264 clip
    self.make_sock = make_sock or (lambda service: messaging.sub_sock(service, conflate=False))
    self.drain = drain or messaging.drain_sock
    t0 = clock()
    self.trigger = ParkedTrigger(t0, arm_delay_s=ARM_DELAY_S, cooldown_s=COOLDOWN_S)
    self.guard = ParkedGuard(t0)
    self.lpf = VoltageLPF()
    self.voltage_t: float | None = None
    self.max_storage_mb = 2048
    self.clip_seconds = 20
    self.power_down_disabled = False
    self.warning_tracker = WarningTracker()
    self.warning_enabled = False
    self.warning_seconds = 30
    self.warning_until: float | None = None     # monotonic time the warning screen should be switched off
    self.limiter = EventRateLimiter(MAX_CAPTURES_PER_HOUR)
    self.socks: dict | None = None
    self.capture: dict | None = None   # {"writer", "dir", "entry"} while a clip is being recorded
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
      self.clip_seconds = clamp_clip_seconds(self.params.get("ParkedRecordingClipSeconds"))
      self.power_down_disabled = bool(self.params.get_bool("DisablePowerDown"))
      self.warning_enabled = bool(self.params.get_bool("ParkedRecordingWarningEnabled"))
      self.warning_seconds = clamp_warning_seconds(self.params.get("ParkedRecordingWarningSeconds"))
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
      "files": [],
    })
    removed = storage_bp.enforce_quota(self.events_root, self.max_storage_mb, keep=event_dir)
    self.params.put("ParkedRecordingLastEvent", entry)
    cloudlog.event("parked_recorder.event", event_id=entry["eventId"], score=entry["score"], removed=removed)

    if not self.limiter.allow(now):
      entry = storage_bp.write_event(event_dir, {**{k: v for k, v in entry.items() if k != "eventId"},
                                                 "captured": False, "reason": "rate_limited"})
      self._status("armed", lastEvent=entry["eventId"], captured=False)
      return
    self._start_capture(event_dir, entry, now)

  def _set_warning(self, on: bool) -> None:
    self.warning_until = None if not on else self.warning_until
    try:
      self.params.put_bool("ParkedRecordingWarning", on)
    except Exception:
      cloudlog.exception("parked_recorder: failed to write warning flag")

  def _maybe_warn(self, motion, now: float) -> None:
    """A hard hit or repeated hits raise ParkedRecordingWarning for warning_seconds (the UI shows the screen)."""
    escalate = self.warning_tracker.add_event(now, motion.score)
    if escalate and self.warning_enabled:
      self.warning_until = now + self.warning_seconds
      self._set_warning(True)
      cloudlog.event("parked_recorder.warning", seconds=self.warning_seconds, score=round(motion.score, 2))

  def _start_capture(self, event_dir, entry: dict, now: float) -> None:
    if self.socks is None:
      self.socks = {cam: self.make_sock(service) for cam, service in CAPTURE_CAMERAS.items()}
    for sock in self.socks.values():
      self.drain(sock)   # discard anything queued from before this capture
    self.limiter.record(now)
    self.capture = {"writer": ClipWriter(event_dir, CAPTURE_CAMERAS, self.clip_seconds, now), "dir": event_dir, "entry": entry}
    self.params.put_bool("ParkedRecordingCapture", True)   # manager starts camerad + stream encoder
    self._status("recording", lastEvent=entry["eventId"])

  def _pump_capture(self, now: float) -> None:
    cap = self.capture
    writer = cap["writer"]
    for cam, sock in self.socks.items():
      for msg in self.drain(sock):
        enc = getattr(msg, msg.which())
        writer.add_frame(cam, enc.header, enc.data, now)
    state = writer.state(now)
    if state in ("done", "failed"):
      self._finish_capture("ok" if state == "done" else "no_frames")

  def _finish_capture(self, outcome: str) -> None:
    """Closes the clip, stops the cameras, and records what was kept. Safe to call at any time."""
    cap, self.capture = self.capture, None
    if cap is None:
      return
    self.params.put_bool("ParkedRecordingCapture", False)
    files = cap["writer"].close()
    base = {k: v for k, v in cap["entry"].items() if k != "eventId"}
    entry = storage_bp.write_event(cap["dir"], {**base, "files": files, "captured": bool(files),
                                                "clipSeconds": cap["writer"].clip_seconds, "outcome": outcome})
    removed = storage_bp.enforce_quota(self.events_root, self.max_storage_mb, keep=cap["dir"])
    self.params.put("ParkedRecordingLastEvent", entry)
    cloudlog.event("parked_recorder.clip", event_id=entry["eventId"], outcome=outcome, files=len(files), removed=removed)
    self._status("armed", lastEvent=entry["eventId"], captured=bool(files))

  def update(self) -> None:
    self.sm.update(100)
    now = self.clock()

    if self.sm.updated["peripheralState"]:
      ps = self.sm["peripheralState"]
      if ps.pandaType != log.PandaState.PandaType.unknown:
        self.lpf.update(now, ps.voltage)
        self.voltage_t = now

    self._refresh_config(now)

    if self.guard.update(now, self._filtered_voltage_mV(now), self.power_down_disabled) is not None:
      self._finish_capture("aborted")   # cameras off first: they are the biggest drain
      if self.warning_until is not None:
        self._set_warning(False)
      if self.params.get_bool("ParkedRecordingActive"):
        # window over: release sensord so it stops too; the device itself is shut down by hardwared
        self.params.put_bool("ParkedRecordingActive", False)
        cloudlog.warning(f"parked_recorder: stopped, reason={self.guard.tripped}")
      self._status("stopped", reason=self.guard.tripped)
      return

    if self.warning_until is not None and now >= self.warning_until:
      self._set_warning(False)

    if self.capture is not None:
      self._pump_capture(now)
      return   # no new triggers while a clip is being recorded

    self._status("armed" if self.trigger.armed(now) else "arming")
    if self.sm.updated["accelerometer"]:
      t = self.sm.logMonoTime["accelerometer"] * 1e-9
      motion = self.trigger.update(t, list(self.sm["accelerometer"].acceleration.v))
      if motion is not None:
        self._maybe_warn(motion, now)
        self._record_event(motion, now)

  def run(self) -> None:
    self.params.put_bool("ParkedRecordingActive", True)
    try:
      while True:
        self.update()
    finally:
      self._finish_capture("aborted")
      self.params.put_bool("ParkedRecordingActive", False)
      self.params.put_bool("ParkedRecordingCapture", False)
      self.params.put_bool("ParkedRecordingWarning", False)
      self._status("disabled")


def main() -> None:
  ParkedRecorder().run()


if __name__ == "__main__":
  main()
