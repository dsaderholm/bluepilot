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
# name -> [(style, seconds), ...], repeated for as long as the warning runs. "police_white" is the default: red and blue
# to catch the eye, then white strobes, which are the brightest the screen can be.
SEQUENCES: dict[str, list[tuple[str, float]]] = {
  "police_white": [("police", 6.0), ("white", 4.0)],
}
DEFAULT_STYLE = "police_white"
DEFAULT_HZ = 5.0
# Flashes per second. D.J. chose a fast, attention-grabbing strobe for his own device. Very fast saturated red
# flashing is the worst case for people with photosensitive epilepsy, so there is still a ceiling.
MAX_HZ = 8.0
MIN_HZ = 0.5
WARNING_SECONDS_RANGE = (10, 120)


def clamp_warning_seconds(value) -> int:
  try:
    seconds = int(value)
  except (TypeError, ValueError):
    return 30
  return min(WARNING_SECONDS_RANGE[1], max(WARNING_SECONDS_RANGE[0], seconds))


def _resolve_style(t: float, style: str) -> str:
  """For a sequence, the style in force at time t; otherwise the style itself."""
  seq = SEQUENCES.get(style)
  if not seq:
    return style if style in STYLES else _resolve_style(t, DEFAULT_STYLE)
  period = sum(seconds for _, seconds in seq)
  phase = t % period
  for name, seconds in seq:
    if phase < seconds:
      return name
    phase -= seconds
  return seq[-1][0]


def flash_frame(t: float, style: str = DEFAULT_STYLE, hz: float = DEFAULT_HZ) -> tuple[tuple[int, int, int], bool]:
  """Returns (background colour, show_text) at time t seconds into the warning. The text stays on the whole time;
  the flag is there so a style can blink it later. hz is the number of colour A flashes per second (capped)."""
  t = t if math.isfinite(t) and t >= 0 else 0.0
  a, b = STYLES[_resolve_style(t, style)]
  hz = min(MAX_HZ, max(MIN_HZ, hz)) if math.isfinite(hz) else DEFAULT_HZ
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
