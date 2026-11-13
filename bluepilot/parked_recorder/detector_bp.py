"""Bump detector for the parked recorder.

Pure logic: no openpilot imports, no I/O, time is passed in. That keeps it unit-testable without hardware.

The accelerometer reads gravity plus whatever the car does. A slow baseline per axis removes gravity and
slow drift (wind, temperature, the car settling), so what is left is the part that changed quickly: a bump,
a door slam next to the car, someone leaning on it.
"""
import math
from collections import deque
from dataclasses import dataclass

# Residual magnitude (m/s^2) that counts as a hit at sensitivity 1.0. The real sensor noise floor is not
# measured yet, so this is a starting point to tune on the car, not a verified number.
BASE_THRESHOLD_MS2 = 0.3
MIN_SENSITIVITY = 0.5
MAX_SENSITIVITY = 10.0


@dataclass(frozen=True)
class MotionEvent:
  t: float        # time of the sample that triggered
  score: float    # peak residual divided by the hit threshold (>= 1.0)
  hits: int       # hits in the window when it triggered


def clamp_sensitivity(value: float) -> float:
  if not math.isfinite(value):
    return 3.0
  return min(MAX_SENSITIVITY, max(MIN_SENSITIVITY, value))


class MotionDetectorBP:
  """Triggers on several hits in a short window, or on one strong hit."""

  def __init__(self, sensitivity: float = 3.0, baseline_tau_s: float = 2.0, hit_window_s: float = 2.0,
               min_hits: int = 3, strong_factor: float = 4.0, max_gap_s: float = 1.0):
    self.baseline_tau_s = baseline_tau_s
    self.hit_window_s = hit_window_s
    self.min_hits = min_hits
    self.strong_factor = strong_factor
    self.max_gap_s = max_gap_s
    self.set_sensitivity(sensitivity)
    self.reset()

  def set_sensitivity(self, sensitivity: float) -> None:
    self.sensitivity = clamp_sensitivity(sensitivity)
    self.threshold = BASE_THRESHOLD_MS2 / self.sensitivity

  def reset(self) -> None:
    self._baseline: tuple[float, float, float] | None = None
    self._last_t: float | None = None
    self._hits: deque[float] = deque()
    self._peak = 0.0

  def update(self, t: float, accel) -> MotionEvent | None:
    try:
      x, y, z = (float(accel[0]), float(accel[1]), float(accel[2]))
    except (TypeError, ValueError, IndexError):
      return None
    if not all(math.isfinite(v) for v in (t, x, y, z)):
      return None

    # first sample, a time step backwards, or a long gap (process stalled): relearn gravity instead of
    # reading the jump as a bump
    if self._baseline is None or self._last_t is None or t <= self._last_t or t - self._last_t > self.max_gap_s:
      self._baseline = (x, y, z)
      self._last_t = t
      self._hits.clear()
      return None

    dt = t - self._last_t
    self._last_t = t
    bx, by, bz = self._baseline
    magnitude = math.sqrt((x - bx) ** 2 + (y - by) ** 2 + (z - bz) ** 2)

    # baseline moves after the residual is taken, so a sudden change stands out against it
    alpha = dt / (self.baseline_tau_s + dt)
    self._baseline = (bx + alpha * (x - bx), by + alpha * (y - by), bz + alpha * (z - bz))

    while self._hits and t - self._hits[0] > self.hit_window_s:
      self._hits.popleft()
    if len(self._hits) == 0:
      self._peak = 0.0

    if magnitude <= self.threshold:
      return None

    self._hits.append(t)
    self._peak = max(self._peak, magnitude)
    if len(self._hits) >= self.min_hits or magnitude >= self.threshold * self.strong_factor:
      event = MotionEvent(t=t, score=self._peak / self.threshold, hits=len(self._hits))
      self._hits.clear()
      self._peak = 0.0
      return event
    return None


class ParkedTrigger:
  """Detector plus arm delay and cooldown.

  Before `arm_delay_s` has passed since `t_start` the detector still learns the baseline but cannot trigger,
  so the owner walking away and shutting the door does not count. After a trigger, further ones are ignored
  for `cooldown_s` so one incident makes one event.
  """

  def __init__(self, t_start: float, sensitivity: float = 3.0, arm_delay_s: float = 120.0, cooldown_s: float = 30.0):
    self.t_start = t_start
    self.arm_delay_s = arm_delay_s
    self.cooldown_s = cooldown_s
    self.detector = MotionDetectorBP(sensitivity)
    self._last_trigger_t: float | None = None

  def armed(self, t: float) -> bool:
    return t - self.t_start >= self.arm_delay_s

  def update(self, t: float, accel) -> MotionEvent | None:
    event = self.detector.update(t, accel)
    if event is None or not self.armed(t):
      return None
    if self._last_trigger_t is not None and t - self._last_trigger_t < self.cooldown_s:
      return None
    self._last_trigger_t = t
    return event
