import wave

import numpy as np
import pytest

from bluepilot.parked_recorder.warning_audio_bp import CLIP, SR, WarningAudio, load_clip, siren


class FakeSD:
  def __init__(self, fail_on=None):
    self.played, self.stops, self.fail_on = [], 0, fail_on

  def play(self, data, sr):
    assert sr == SR and data.dtype == np.float32 and np.max(np.abs(data)) <= 1.0
    if self.fail_on is not None and len(self.played) >= self.fail_on:
      raise RuntimeError("no audio device")
    self.played.append(len(data))

  def stop(self):
    self.stops += 1


class FakeAmp:
  def __init__(self, on):
    self.on, self.log = on, []

  def is_on(self):
    return self.on

  def wake(self):
    self.on = True
    self.log.append("wake")

  def shutdown(self, flag):
    self.on = not flag
    self.log.append(f"shutdown={flag}")


def make(amp_on=False, fail_on=None, clip_s=0.02):
  sd, amp = FakeSD(fail_on), FakeAmp(amp_on)
  # a tiny clip and an instant wait keep the loop fast; the real clip and timings are checked separately
  audio = WarningAudio(sd, amp.is_on, amp.shutdown, amp.wake, clip=np.zeros(int(SR * clip_s), np.float32), wait=lambda ev, secs: ev.wait(0.005))
  return audio, sd, amp


def run_for(audio, plays=4):
  audio.start()
  for _ in range(400):
    if len(audio.plays) >= plays:
      break
    audio._stop.wait(0.005)
  audio.stop()


def test_voice_then_siren_then_repeats():
  audio, sd, amp = make()
  run_for(audio, plays=5)
  assert audio.plays[:5] == ["voice", "siren", "voice", "siren", "voice"]


def test_amp_is_woken_first_and_restored_to_shutdown_when_it_was_off():
  audio, sd, amp = make(amp_on=False)
  run_for(audio)
  assert amp.log == ["wake", "shutdown=True"] and amp.on is False and sd.stops >= 1


def test_amp_already_on_is_left_on():
  audio, sd, amp = make(amp_on=True)
  run_for(audio)
  assert amp.log == ["wake"] and amp.on is True


def test_stop_ends_the_thread_and_cuts_the_sound():
  audio, sd, amp = make()
  audio.start()
  audio.stop()
  assert not audio.running and sd.stops >= 1


def test_start_twice_does_not_start_two_players():
  audio, sd, amp = make()
  audio.start()
  audio.start()
  audio.stop()
  assert amp.log.count("wake") == 1


def test_it_can_be_started_again_after_a_stop():
  audio, sd, amp = make()
  run_for(audio, plays=2)
  audio.plays.clear()
  run_for(audio, plays=2)
  assert audio.plays[:2] == ["voice", "siren"] and amp.log.count("wake") == 2


def test_an_audio_error_still_restores_the_amp():
  audio, sd, amp = make(amp_on=False, fail_on=0)
  audio.start()
  audio._thread.join(2)
  assert isinstance(audio.error, RuntimeError) and amp.log == ["wake", "shutdown=True"]


def test_a_failing_amp_wake_does_not_raise_or_leave_it_on():
  sd = FakeSD()
  amp = FakeAmp(False)
  def broken_wake():
    raise OSError("i2c")

  audio = WarningAudio(sd, amp.is_on, amp.shutdown, broken_wake, clip=np.zeros(10, np.float32), wait=lambda ev, s: ev.wait(0.005))
  audio.start()
  audio._thread.join(2)
  assert isinstance(audio.error, OSError) and amp.log == [] and sd.played == []   # never woken, so nothing to restore, nothing played


def test_the_shipped_clip_is_what_was_approved():
  assert CLIP.exists()
  x = load_clip()
  assert 11.0 < len(x) / SR < 12.0 and np.max(np.abs(x)) <= 0.99 and np.max(np.abs(x)) > 0.9
  rms = 20 * np.log10(np.sqrt(np.mean(x[np.abs(x) > 0.02] ** 2)))
  assert -4.0 < rms < -2.0                                                  # the loud mix D.J. chose (about -2.7 dBFS)


def test_a_clip_in_the_wrong_format_is_refused(tmp_path):
  p = tmp_path / "bad.wav"
  with wave.open(str(p), "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(44100)
    w.writeframes(b"\0\0" * 100)
  with pytest.raises(ValueError):
    load_clip(p)


def test_siren_is_full_scale_and_the_right_length():
  s = siren(2.0)
  assert len(s) == 2 * SR and 0.99 <= np.max(np.abs(s)) <= 1.0
