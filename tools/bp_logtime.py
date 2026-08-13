"""FusionPilot: turn a route's `logMonoTime` into a drive-relative timestamp, correctly.

Every diagnostic here reads several services out of one LogReader and needs "seconds into the drive"
to label events with. Two different attempts at that were wrong in the same direction, and both
inflated every timestamp this fork has ever printed by roughly a factor of four.

WHAT IS ACTUALLY GOING ON, measured on route 00000365 (13 segments) rather than reasoned about:

    seg        first mono      last mono
    0                70.0          131.3
    1                70.0          191.3
    2                70.0          251.3
    ...
    12               70.0          824.4

**Every segment file begins at the same early monotime**, because each rlog replays the boot-time
header messages -- initData, carParams and friends -- before its own data. So walking the segments in
order steps BACKWARD at every boundary: -61 s, -121 s, -181 s, ... -721 s, growing as the drive goes
on.

Both previous versions saw those steps and "corrected" them:

    if t_prev is not None and t_raw < t_prev - 1.0:      # v1: any backward step
      t_shift += t_prev - t_raw
                                                        # v2: only reboot-sized steps (60 s)
Each boundary jump is far larger than either threshold, so both accumulated a shift at every one of
them. Route 00000365 is **754 seconds** long and was being reported past t+3300.

THE FIX IS TO DO NOTHING. **openpilot starts a new route per ignition cycle**, so `logMonoTime` is
already monotonic across a route's segments -- there is no reset to compensate for, and the header
messages are not out-of-order data, they are just early. Subtracting the smallest monotime seen gives
the true drive time, and 754 s agrees with reading `carState` alone, which is the one stream with no
header replay to confuse it.

A genuine mid-route reboot would show up as time running backwards. That is reported rather than
smoothed over, because silently papering over it is what produced this bug twice.

THE LESSON: a number only one tool can produce has never been checked. Four tools shared this helper,
so they always agreed with each other, and the disagreement that finally exposed it came from a
one-off script that happened to read a single service.
"""
from __future__ import annotations

# A monotime this far BELOW the earliest one already seen is a new minimum, not header replay
# (which lands on the earliest value exactly).
NEW_MINIMUM_EPS_S = 5.0

# ...and only once the route has clearly started, so ordinary ordering among the first few header
# messages cannot look like a reset.
SETTLED_SPAN_S = 60.0


class DriveClock:
  """Drive-relative seconds from `logMonoTime`.

      clock = DriveClock()
      for msg in LogReader(path):
        ts = clock.seconds(msg.logMonoTime)
      ...
      if clock.went_backwards:
        print("warning: monotime ran backwards; this route may span a reboot")
  """

  def __init__(self) -> None:
    self._t0: float | None = None
    self._max: float | None = None
    self.went_backwards = False
    self.max_fall_below_start = 0.0

  def seconds(self, log_mono_time: int | float) -> float:
    raw = log_mono_time / 1e9

    # A reset is told apart from header replay by the VALUE it lands on, never by the size of the
    # step. The step backward at a segment boundary equals the elapsed drive time, so it grows
    # without bound and no threshold on it is safe -- a 10-minute drive already clears 600 s.
    # Header replay returns to exactly the earliest monotime; a reset goes BELOW it.
    if self._t0 is not None and raw < self._t0 - NEW_MINIMUM_EPS_S and self._max is not None \
       and self._max - self._t0 > SETTLED_SPAN_S:
      self.went_backwards = True
      self.max_fall_below_start = max(self.max_fall_below_start, self._t0 - raw)

    # The FIRST monotime is not the smallest: segment 0's header messages are, and every later
    # segment repeats them. Track the minimum so the drive starts at zero wherever it is seen.
    if self._t0 is None or raw < self._t0:
      self._t0 = raw
    if self._max is None or raw > self._max:
      self._max = raw
    return raw - self._t0
