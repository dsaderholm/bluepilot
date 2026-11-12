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
      "ParkedRecordingMaxStorageMB": 2048, "ParkedRecordingActive": False, "ParkedRecordingClipSeconds": 20,
      "ParkedRecordingCapture": False, "ParkedRecordingWarningEnabled": False, "ParkedRecordingWarningSeconds": 30,
      "ParkedRecordingWarning": False, "ParkedRecordingWebhookEnabled": False, "ParkedRecordingWarningSound": False, **values,
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


class FakeAudio:
  instances = []

  def __init__(self):
    self.started = 0
    self.stopped = 0
    FakeAudio.instances.append(self)

  def start(self):
    self.started += 1

  def stop(self):
    self.stopped += 1


class FakeSock:
  def __init__(self):
    self.queue = []


def frame(header=b"\x00\x00\x00\x01H", data=b"frame"):
  enc = SimpleNamespace(header=header, data=data)
  return SimpleNamespace(which=lambda: "enc", enc=enc)


def make(mod, tmp_path, **param_values):
  sm = FakeSM()
  params = FakeParams(**param_values)

  def drain(sock):
    out, sock.queue = sock.queue, []
    return out

  rec = mod.ParkedRecorder(params=params, sm=sm, clock=Clock(sm), events_root=str(tmp_path),
                           make_sock=lambda service: FakeSock(), drain=drain, audio_factory=FakeAudio)
  return rec, sm, params


def bump(rec, sm, **kw):
  """Arms the recorder and bumps it; returns with a capture running."""
  advance(rec, sm, 125, **kw)
  sm.step(accel=(0.0, 0.0, 11.0), **kw)
  rec.update()


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
  bump(rec, sm)
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


def test_no_panda_voltage_fails_closed_after_grace(mod, tmp_path):
  rec, sm, params = make(mod, tmp_path)
  advance(rec, sm, 10, panda=UNKNOWN)
  assert status(params)["state"] == "arming"   # grace period: not stopped yet
  advance(rec, sm, 6, panda=UNKNOWN)
  assert status(params) == {"state": "stopped", "reason": "no_voltage"}


def test_voltage_arriving_late_at_startup_does_not_stop_it(mod, tmp_path):
  # the real device: the first loop runs before pandad's first voltage message
  rec, sm, params = make(mod, tmp_path)
  advance(rec, sm, 0.5, with_voltage=False)
  advance(rec, sm, 5)
  assert status(params)["state"] == "arming"


def test_stale_voltage_fails_closed(mod, tmp_path):
  rec, sm, params = make(mod, tmp_path)
  advance(rec, sm, 1)                          # one good reading
  advance(rec, sm, 30, with_voltage=False)     # then nothing for longer than the stale limit and the grace period
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


def feed_frames(rec, sm, cams=("wideRoad", "driver"), n=1, data=b"frame"):
  for cam in cams:
    rec.socks[cam].queue.extend(frame(data=data) for _ in range(n))


def test_bump_raises_capture_and_clip_is_written(mod, tmp_path):
  rec, sm, params = make(mod, tmp_path)
  bump(rec, sm)
  assert params.values["ParkedRecordingCapture"] is True       # manager would now start camerad + encoder
  assert status(params)["state"] == "recording"

  feed_frames(rec, sm, n=3)
  sm.step()
  rec.update()
  assert params.values["ParkedRecordingCapture"] is True       # clip still running
  sm.t += 21                                                    # past the 20 s clip
  feed_frames(rec, sm, n=1)
  sm.step()
  rec.update()

  assert params.values["ParkedRecordingCapture"] is False
  (event,) = list(tmp_path.iterdir())
  entry = json.loads((event / "event.json").read_text())
  assert entry["captured"] is True and entry["outcome"] == "ok" and entry["clipSeconds"] == 20
  assert {f["camera"] for f in entry["files"]} == {"wideRoad", "driver"}
  data = (event / "wideRoad.h264").read_bytes()
  assert data == (b"\x00\x00\x00\x01Hframe") * 4              # header + data per frame, in order


def test_no_frames_gives_up_and_stops_cameras(mod, tmp_path):
  rec, sm, params = make(mod, tmp_path)
  bump(rec, sm)
  sm.t += 25                                                    # past the 20 s startup timeout, nothing arrived
  sm.step()
  rec.update()
  assert params.values["ParkedRecordingCapture"] is False
  (event,) = list(tmp_path.iterdir())
  entry = json.loads((event / "event.json").read_text())
  assert entry["captured"] is False and entry["outcome"] == "no_frames" and entry["files"] == []
  assert not list(event.glob("*.h264"))                         # empty files are removed


def test_second_bump_during_capture_is_ignored(mod, tmp_path):
  rec, sm, params = make(mod, tmp_path)
  bump(rec, sm)
  sm.t += 40                                                    # cooldown over, clip still waiting for frames
  sm.step(accel=(0.0, 0.0, 12.0))
  rec.update()
  assert len(list(tmp_path.iterdir())) == 1


def test_guard_trip_mid_capture_stops_cameras_first(mod, tmp_path):
  rec, sm, params = make(mod, tmp_path, ParkedRecordingMaxHours=0.25)   # 15 minute window
  params.values["ParkedRecordingActive"] = True
  bump(rec, sm)
  assert params.values["ParkedRecordingCapture"] is True
  feed_frames(rec, sm, n=2)
  sm.step()
  rec.update()                                                           # frames are read and written
  sm.t += 15 * 60                                                        # window runs out while the clip is recording
  sm.step()
  rec.update()
  assert status(params) == {"state": "stopped", "reason": "time"}
  assert params.values["ParkedRecordingCapture"] is False
  assert params.values["ParkedRecordingActive"] is False
  (event,) = list(tmp_path.iterdir())
  entry = json.loads((event / "event.json").read_text())
  assert entry["outcome"] == "aborted" and entry["captured"] is True     # what was recorded so far is kept


def test_rate_limit_stops_capture_not_the_log(mod, tmp_path):
  rec, sm, params = make(mod, tmp_path)
  for _ in range(mod.MAX_CAPTURES_PER_HOUR):
    rec.limiter.record(sm.t)
  bump(rec, sm)
  assert params.values["ParkedRecordingCapture"] is False
  (event,) = list(tmp_path.iterdir())
  entry = json.loads((event / "event.json").read_text())
  assert entry["captured"] is False and entry["reason"] == "rate_limited"


def test_exit_mid_capture_turns_cameras_off(mod, tmp_path):
  rec, sm, params = make(mod, tmp_path)
  bump(rec, sm)
  assert params.values["ParkedRecordingCapture"] is True
  rec.update = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
  with pytest.raises(KeyboardInterrupt):
    rec.run()
  assert params.values["ParkedRecordingCapture"] is False


def test_disable_power_down_stops_it_before_anything_arms(mod, tmp_path):
  rec, sm, params = make(mod, tmp_path, DisablePowerDown=True)
  params.values["ParkedRecordingActive"] = True
  advance(rec, sm, 2)
  assert status(params) == {"state": "stopped", "reason": "power_down_disabled"}
  assert params.values["ParkedRecordingActive"] is False
  advance(rec, sm, 130)
  sm.step(accel=(0.0, 0.0, 11.0))
  rec.update()
  assert list(tmp_path.iterdir()) == []             # and it never records, even once armed time has passed


def finish_capture(rec, sm):
  """Lets a running capture time out so the next bump can register."""
  sm.t += 25
  sm.step()
  rec.update()


def test_warning_off_by_default_even_on_a_hard_hit(mod, tmp_path):
  rec, sm, params = make(mod, tmp_path)
  bump(rec, sm)                                                # a hard hit (score about 12)
  assert params.values["ParkedRecordingWarning"] is False


def test_hard_hit_raises_warning_then_it_expires(mod, tmp_path):
  rec, sm, params = make(mod, tmp_path, ParkedRecordingWarningEnabled=True, ParkedRecordingWarningSeconds=30)
  bump(rec, sm)
  assert params.values["ParkedRecordingWarning"] is True
  sm.t += 29
  sm.step()
  rec.update()
  assert params.values["ParkedRecordingWarning"] is True       # still within the 30 s
  sm.t += 2
  sm.step()
  rec.update()
  assert params.values["ParkedRecordingWarning"] is False


def test_single_light_bump_records_but_does_not_warn(mod, tmp_path):
  rec, sm, params = make(mod, tmp_path, ParkedRecordingWarningEnabled=True)
  advance(rec, sm, 125)
  sm.step(accel=(0.0, 0.0, 9.81 + 0.5))                        # score about 5: an event, but not a hard hit
  rec.update()
  assert len(list(tmp_path.iterdir())) == 1
  assert params.values["ParkedRecordingWarning"] is False


def test_repeated_light_bumps_escalate(mod, tmp_path):
  rec, sm, params = make(mod, tmp_path, ParkedRecordingWarningEnabled=True)
  advance(rec, sm, 125)
  for i in range(3):                                           # three light bumps inside two minutes
    sm.step(accel=(0.0, 0.0, 9.81 + 0.5))
    rec.update()
    assert params.values["ParkedRecordingWarning"] is (i == 2)
    finish_capture(rec, sm)                                    # clip times out, cooldown passes
    advance(rec, sm, 6)


def test_guard_trip_switches_the_warning_off(mod, tmp_path):
  rec, sm, params = make(mod, tmp_path, ParkedRecordingWarningEnabled=True, ParkedRecordingMaxHours=0.25)
  params.values["ParkedRecordingActive"] = True
  bump(rec, sm)
  assert params.values["ParkedRecordingWarning"] is True
  sm.t += 15 * 60
  sm.step()
  rec.update()
  assert status(params)["state"] == "stopped"
  assert params.values["ParkedRecordingWarning"] is False


def test_exit_switches_the_warning_off(mod, tmp_path):
  rec, sm, params = make(mod, tmp_path, ParkedRecordingWarningEnabled=True)
  bump(rec, sm)
  assert params.values["ParkedRecordingWarning"] is True
  rec.update = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
  with pytest.raises(KeyboardInterrupt):
    rec.run()
  assert params.values["ParkedRecordingWarning"] is False


def queued(tmp_path):
  from bluepilot.parked_recorder.webhook_bp import Outbox
  ob = Outbox(tmp_path / ".outbox")
  return [ob.read(p) for p in ob.pending()]


def test_webhook_off_queues_nothing(mod, tmp_path):
  rec, sm, params = make(mod, tmp_path)
  bump(rec, sm)
  assert queued(tmp_path) == [] and not (tmp_path / ".outbox").exists()


def test_bump_and_clip_are_queued_when_enabled(mod, tmp_path):
  rec, sm, params = make(mod, tmp_path, ParkedRecordingWebhookEnabled=True)
  bump(rec, sm)
  assert [q["kind"] for q in queued(tmp_path)] == ["bump"]
  bump_msg = queued(tmp_path)[0]
  assert bump_msg["score"] > 8 and bump_msg["hits"] >= 1 and bump_msg["voltageV"] == 12.6 and bump_msg["eventId"]
  feed_frames(rec, sm, n=2)
  sm.step()
  rec.update()
  sm.t += 21
  feed_frames(rec, sm, n=1)
  sm.step()
  rec.update()                                                  # the clip finishes
  kinds = [q["kind"] for q in queued(tmp_path)]
  assert kinds == ["bump", "clip"]
  clip = queued(tmp_path)[1]
  assert clip["outcome"] == "ok" and {f["name"] for f in clip["files"]} == {"wideRoad.h264", "driver.h264"}
  assert all(set(f) == {"name", "bytes"} for f in clip["files"])   # no paths, no media


def test_warning_is_queued_when_it_fires(mod, tmp_path):
  rec, sm, params = make(mod, tmp_path, ParkedRecordingWebhookEnabled=True, ParkedRecordingWarningEnabled=True)
  bump(rec, sm)
  assert [q["kind"] for q in queued(tmp_path)] == ["warning", "bump"]


def test_a_full_disk_does_not_stop_the_recorder(mod, tmp_path, monkeypatch):
  rec, sm, params = make(mod, tmp_path, ParkedRecordingWebhookEnabled=True)
  monkeypatch.setattr(rec.outbox, "enqueue", lambda payload: (_ for _ in ()).throw(OSError("disk full")))
  bump(rec, sm)
  assert params.values["ParkedRecordingCapture"] is True        # the event and the clip carry on regardless
  assert [e for e in tmp_path.iterdir() if not e.name.startswith(".")]


def test_sound_plays_with_the_warning_when_enabled_and_stops_when_it_ends(mod, tmp_path):
  FakeAudio.instances.clear()
  rec, sm, params = make(mod, tmp_path, ParkedRecordingWarningEnabled=True, ParkedRecordingWarningSound=True,
                         ParkedRecordingWarningSeconds=30)
  bump(rec, sm)
  (audio,) = FakeAudio.instances
  assert audio.started == 1 and audio.stopped == 0
  sm.t += 31
  sm.step()
  rec.update()
  assert audio.stopped == 1 and params.values["ParkedRecordingWarning"] is False


def test_no_sound_when_the_sound_setting_is_off(mod, tmp_path):
  FakeAudio.instances.clear()
  rec, sm, params = make(mod, tmp_path, ParkedRecordingWarningEnabled=True)
  bump(rec, sm)
  assert params.values["ParkedRecordingWarning"] is True and FakeAudio.instances == []     # the screen warning still raises; no player is even built


def test_sound_stops_when_the_battery_guard_trips(mod, tmp_path):
  FakeAudio.instances.clear()
  rec, sm, params = make(mod, tmp_path, ParkedRecordingWarningEnabled=True, ParkedRecordingWarningSound=True,
                         ParkedRecordingMaxHours=0.25)
  params.values["ParkedRecordingActive"] = True
  bump(rec, sm)
  sm.t += 15 * 60
  sm.step()
  rec.update()
  assert FakeAudio.instances[0].stopped >= 1


def test_sound_stops_when_the_recorder_exits(mod, tmp_path):
  FakeAudio.instances.clear()
  rec, sm, params = make(mod, tmp_path, ParkedRecordingWarningEnabled=True, ParkedRecordingWarningSound=True)
  bump(rec, sm)
  rec.update = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
  with pytest.raises(KeyboardInterrupt):
    rec.run()
  assert FakeAudio.instances[0].stopped >= 1


def test_a_broken_sound_stack_does_not_stop_the_recorder(mod, tmp_path):
  rec, sm, params = make(mod, tmp_path, ParkedRecordingWarningEnabled=True, ParkedRecordingWarningSound=True)
  rec.audio_factory = lambda: (_ for _ in ()).throw(ImportError("no sounddevice"))
  bump(rec, sm)
  assert params.values["ParkedRecordingWarning"] is True and params.values["ParkedRecordingCapture"] is True
