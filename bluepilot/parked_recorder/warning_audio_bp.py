"""Spoken warning and siren for the parked warning.

While the warning is active this plays the voice clip, then a siren, and repeats until stopped. The comma's speaker amplifier is shut down
while parked, so it is woken first and put back the way it was found when this stops (including on an error). The audio hardware is injected,
so the sequencing is tested without a device; make_default() builds the real one on the comma.
"""
import threading
import wave
from pathlib import Path

import numpy as np

SR = 48000
CLIP = Path(__file__).parent / "sounds" / "intruder_ryan.wav"
SIREN_SECONDS = 5.0
AMP_SHUTDOWN_REGISTER = 0x51     # bit 7 set means the amplifier is on (see system/hardware/tici/amplifier.py)


def load_clip(path: Path = CLIP) -> np.ndarray:
  with wave.open(str(path)) as w:
    if w.getframerate() != SR or w.getnchannels() != 1 or w.getsampwidth() != 2:
      raise ValueError("warning clip must be mono 48 kHz 16-bit")
    return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def siren(seconds: float = SIREN_SECONDS, level: float = 1.0, lo: float = 600.0, hi: float = 1500.0, rate: float = 2.0) -> np.ndarray:
  t = np.arange(int(SR * seconds)) / SR
  f = lo + (hi - lo) * (0.5 + 0.5 * np.sin(2 * np.pi * rate * t))
  return (level * np.sin(2 * np.pi * np.cumsum(f) / SR)).astype(np.float32)


class WarningAudio:
  """sd: a sounddevice-like module (play, stop). amp_is_on() -> bool; amp_shutdown(bool) sets the shutdown state; amp_wake() initialises it."""

  def __init__(self, sd, amp_is_on, amp_shutdown, amp_wake, clip: np.ndarray | None = None, wait=None):
    self.sd = sd
    self.amp_is_on = amp_is_on
    self.amp_shutdown = amp_shutdown
    self.amp_wake = amp_wake
    self.clip = clip
    self._wait = wait or (lambda ev, secs: ev.wait(secs))
    self._stop = threading.Event()
    self._thread: threading.Thread | None = None
    self.error: Exception | None = None
    self.plays: list[str] = []          # what was played, in order (for tests and logs)

  @property
  def running(self) -> bool:
    return self._thread is not None and self._thread.is_alive()

  def start(self) -> None:
    if self.running:
      return
    self._stop.clear()
    self.error = None
    self._thread = threading.Thread(target=self._run, name="parked-warning-audio", daemon=True)
    self._thread.start()

  def stop(self, timeout: float = 3.0) -> None:
    self._stop.set()
    if self._thread is not None:
      self._thread.join(timeout)

  def _play(self, name: str, data: np.ndarray) -> None:
    self.plays.append(name)
    self.sd.play(data, SR)
    self._wait(self._stop, len(data) / SR + 0.2)

  def _run(self) -> None:
    was_on = False
    woke = False
    try:
      was_on = self.amp_is_on()
      self.amp_wake()
      woke = True
      clip = self.clip if self.clip is not None else load_clip()
      sirene = siren()
      while not self._stop.is_set():
        for name, data in (("voice", clip), ("siren", sirene)):
          if self._stop.is_set():
            break
          self._play(name, data)
    except Exception as e:
      self.error = e
    finally:
      try:
        self.sd.stop()
      except Exception:
        pass
      if woke and not was_on:
        try:
          self.amp_shutdown(True)       # back to the parked state; if it was already on, leave it alone
        except Exception as e:
          self.error = self.error or e


def make_default() -> WarningAudio:
  """The real thing, on the comma. Imports are here so the rest of the module (and its tests) need none of them."""
  import sounddevice as sd
  from smbus2 import SMBus

  from openpilot.system.hardware.tici.amplifier import Amplifier

  amp = Amplifier()

  def is_on() -> bool:
    with SMBus(Amplifier.AMP_I2C_BUS) as bus:
      return bool(bus.read_byte_data(Amplifier.AMP_ADDRESS, AMP_SHUTDOWN_REGISTER, force=True) & 0x80)

  return WarningAudio(sd, is_on, amp.set_global_shutdown, amp.initialize_configuration)
