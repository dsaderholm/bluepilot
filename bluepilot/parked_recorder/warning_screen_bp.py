"""State behind the parked warning screen: when it is on, and how far into the flash sequence it is.

Pure logic (no raylib, no openpilot imports): the UI passes in whether the car is parked and a way to read the
ParkedRecordingWarning param, and draws what flash_frame() says. The recorder owns the param; this only reads it.
"""
import time
from collections.abc import Callable

from bluepilot.parked_recorder.warning_bp import WARNING_SECONDS_RANGE

POLL_S = 0.2
# The recorder clears the param itself when the warning ends. If the recorder dies with it set, the screen must not
# strobe at full brightness until the battery is flat, so the screen gives up on its own a little after the longest
# warning the recorder can ask for, and stays off until the param has been seen false again.
MAX_ON_S = WARNING_SECONDS_RANGE[1] + 10.0


class WarningScreenState:
  def __init__(self, read_warning: Callable[[], bool], now: Callable[[], float] = time.monotonic):
    self._read = read_warning
    self._now = now
    self._last_poll = float("-inf")
    self._param = False
    self._started: float | None = None
    self._gave_up = False

  def update(self, parked: bool) -> bool:
    """Call once per frame. True while the warning screen should be shown."""
    now = self._now()
    if now - self._last_poll >= POLL_S:
      self._last_poll = now
      try:
        self._param = bool(self._read())
      except Exception:
        self._param = False
    if not self._param:
      self._gave_up = False
    if not (self._param and parked) or self._gave_up:
      self._started = None
      return False
    if self._started is None:
      self._started = now
    if now - self._started > MAX_ON_S:
      self._gave_up = True
      self._started = None
      return False
    return True

  def elapsed(self) -> float:
    """Seconds since the screen came on (0 when off)."""
    return 0.0 if self._started is None else max(0.0, self._now() - self._started)
