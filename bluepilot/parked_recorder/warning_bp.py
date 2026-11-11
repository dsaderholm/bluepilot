"""Escalation and flash pattern for the parked warning screen.

Pure logic: no openpilot imports, no I/O, time is passed in. The recorder decides *when* to warn (a strong hit, or
several hits close together); the UI draws what flash_frame() says. The UI side is separate and not built yet.
"""
import math
from collections import deque

# name -> (colour A, colour B), RGB. The screen flashes between the two.
STYLES: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
  "police": ((220, 30, 30), (30, 60, 230)),
  "amber": ((255, 170, 0), (0, 0, 0)),
  "white": ((255, 255, 255), (0, 0, 0)),
}
DEFAULT_STYLE = "police"
MAX_HZ = 3.0                      # flashes per second, kept under the usual photosensitivity guideline
WARNING_SECONDS_RANGE = (10, 120)


def clamp_warning_seconds(value) -> int:
  try:
    seconds = int(value)
  except (TypeError, ValueError):
    return 30
  return min(WARNING_SECONDS_RANGE[1], max(WARNING_SECONDS_RANGE[0], seconds))


def flash_frame(t: float, style: str = DEFAULT_STYLE, hz: float = 2.0) -> tuple[tuple[int, int, int], bool]:
  """Returns (background colour, show_text) at time t seconds into the warning. The text stays on the whole time;
  the flag is there so a style can blink it later. hz is the number of colour A flashes per second (capped)."""
  a, b = STYLES.get(style, STYLES[DEFAULT_STYLE])
  hz = min(MAX_HZ, max(0.5, hz)) if math.isfinite(hz) else 2.0
  return (a if int(t * hz * 2) % 2 == 0 else b), True


class WarningTracker:
  """Decides when a bump is serious enough to turn on the warning screen: one hard hit (score >= strong_score, the
  peak divided by the trigger threshold), or `repeat_count` triggers inside `window_s` seconds (someone leaning
  on or rocking the car). An ordinary single bump only records."""

  def __init__(self, strong_score: float = 8.0, repeat_count: int = 3, window_s: float = 120.0):
    self.strong_score = strong_score
    self.repeat_count = repeat_count
    self.window_s = window_s
    self._times: deque[float] = deque()

  def add_event(self, now: float, score: float) -> bool:
    """Records a trigger and returns True if the warning should be shown now."""
    self._times.append(now)
    while self._times and now - self._times[0] > self.window_s:
      self._times.popleft()
    return score >= self.strong_score or len(self._times) >= self.repeat_count
