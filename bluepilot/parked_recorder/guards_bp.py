"""Battery-drain safeguards for the parked recorder.

Pure logic: no openpilot imports, no I/O, time is passed in. The recorder stops itself with ParkedGuard (cameras and
accelerometer off). The shutdown itself is hardwared's job: power_monitoring consults sentry_voltage_shutdown, which
powers the device off under the voltage floor even if the recorder has hung. The window does not power the device off
(the stock offroad timer is 30 hours and effective_max_time_offroad_s only ever raises it).
"""
import math

VOLTAGE_LPF_TAU_S = 45.0          # same time constant as PowerMonitoring's car voltage filter
LOW_VOLTAGE_HOLD_S = 30.0
NO_VOLTAGE_GRACE_S = 15.0        # pandad's first voltage message arrives about 0.25 s after start; do not trip before it

MAX_HOURS_RANGE = (0.25, 12.0)
MIN_VOLTAGE_RANGE = (12.0, 13.0)


def clamp(value: float, lo: float, hi: float, default: float) -> float:
  try:
    value = float(value)
  except (TypeError, ValueError):
    return default
  if not math.isfinite(value):
    return default
  return min(hi, max(lo, value))


def clamp_max_hours(value) -> float:
  return clamp(value, *MAX_HOURS_RANGE, default=4.0)


def clamp_min_voltage(value) -> float:
  return clamp(value, *MIN_VOLTAGE_RANGE, default=12.3)


class VoltageLPF:
  """Low-passed car voltage in mV. Starts at the first reading (not at a made-up 12 V)."""

  def __init__(self, tau_s: float = VOLTAGE_LPF_TAU_S):
    self.tau_s = tau_s
    self.value_mV: float | None = None
    self._last_t: float | None = None

  def update(self, t: float, voltage_mV: float | None) -> float | None:
    if voltage_mV is None or not math.isfinite(voltage_mV) or voltage_mV <= 0:
      return self.value_mV
    if self.value_mV is None or self._last_t is None:
      self.value_mV = float(voltage_mV)
    else:
      dt = max(0.0, t - self._last_t)
      alpha = dt / (self.tau_s + dt)
      self.value_mV += alpha * (voltage_mV - self.value_mV)
    self._last_t = t
    return self.value_mV


class ParkedGuard:
  """Latched stop conditions. Once one trips it stays tripped until the next ignition cycle (new instance).

  Reasons: "time" (window used up), "voltage" (filtered voltage under the floor for LOW_VOLTAGE_HOLD_S),
  "no_voltage" (no valid reading for NO_VOLTAGE_GRACE_S after start, fail closed), "power_down_disabled"
  (DisablePowerDown is set, so hardwared would never shut the device down at the end of the window: the safety
  promise cannot be kept, so the recorder does not run).
  """

  def __init__(self, t_start: float, max_hours: float = 4.0, min_voltage_v: float = 12.3,
               low_voltage_hold_s: float = LOW_VOLTAGE_HOLD_S, no_voltage_grace_s: float = NO_VOLTAGE_GRACE_S):
    self.t_start = t_start
    self.max_hours = clamp_max_hours(max_hours)
    self.min_voltage_v = clamp_min_voltage(min_voltage_v)
    self.low_voltage_hold_s = low_voltage_hold_s
    self.no_voltage_grace_s = no_voltage_grace_s
    self.tripped: str | None = None
    self._low_since: float | None = None

  def configure(self, max_hours, min_voltage_v) -> None:
    self.max_hours = clamp_max_hours(max_hours)
    self.min_voltage_v = clamp_min_voltage(min_voltage_v)

  def update(self, t: float, filtered_voltage_mV: float | None, power_down_disabled: bool = False) -> str | None:
    """Returns the stop reason once tripped (and on every later call), else None."""
    if self.tripped is not None:
      return self.tripped

    if power_down_disabled:
      self.tripped = "power_down_disabled"
    elif t - self.t_start >= self.max_hours * 3600:
      self.tripped = "time"
    elif filtered_voltage_mV is None or not math.isfinite(filtered_voltage_mV) or filtered_voltage_mV <= 0:
      # fail closed, but only once the grace period has passed: the first reading is not instant
      if t - self.t_start >= self.no_voltage_grace_s:
        self.tripped = "no_voltage"
    elif filtered_voltage_mV < self.min_voltage_v * 1000:
      if self._low_since is None:
        self._low_since = t
      elif t - self._low_since >= self.low_voltage_hold_s:
        self.tripped = "voltage"
    else:
      self._low_since = None
    return self.tripped


def effective_max_time_offroad_s(stock_s: float, enabled: bool, window_hours, voltage_mV: float | None, min_voltage_v) -> float:
  """Offroad time (seconds) after which the device shuts down; 0 means no limit, as for MaxTimeOffroad.

  Stock behaviour unless the parked recorder is enabled and the (filtered) car voltage is healthy, in which
  case the limit is raised to the recorder window. If the voltage is unknown or below the floor the stock
  limit applies, which after a few minutes offroad means the device shuts down: that is the point.
  A stock limit of 0 (the owner chose no limit) is left alone.
  """
  if not enabled or stock_s == 0:
    return stock_s
  if voltage_mV is None or not math.isfinite(voltage_mV) or voltage_mV < clamp_min_voltage(min_voltage_v) * 1000:
    return stock_s
  return max(stock_s, clamp_max_hours(window_hours) * 3600)


def sentry_voltage_shutdown(enabled: bool, voltage_mV: float | None, min_voltage_v, offroad_time_s: float,
                            min_offroad_s: float = 60.0) -> bool:
  """True when hardwared should shut the device down because the (filtered) car voltage is under the parked recorder's
  floor. Only while the recorder is enabled, so stock behaviour is untouched otherwise. It is stricter than the stock
  11.8 V rule, which exists to keep the car startable, and is the real battery protection: it is enforced by hardwared,
  not by the recorder process. An unknown voltage returns False and leaves the decision to the stock rules."""
  if not enabled or voltage_mV is None or not math.isfinite(voltage_mV) or voltage_mV <= 0:
    return False
  return voltage_mV < clamp_min_voltage(min_voltage_v) * 1000 and offroad_time_s > min_offroad_s
