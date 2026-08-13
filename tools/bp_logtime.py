"""FusionPilot: turn a route's `logMonoTime` into a drive-relative timestamp, correctly.

Every diagnostic here reads several services out of one LogReader and needs "seconds into the drive"
to label events with. The obvious way to get it is wrong in a way that is invisible until you check a
number against something else.

WHAT WENT WRONG, found 2026-08-12. All four tools carried this:

    if t_prev is not None and t_raw < t_prev - 1.0:
      t_shift += t_prev - t_raw          # "segments restart the clock"

Two false premises. `logMonoTime` is boot-relative and **already monotonic across the segments of one
route**, so there is normally no clock to restart. And messages from DIFFERENT SERVICES interleave out
of order by more than a second routinely, because each service is buffered on its own -- so the guard
fires constantly on ordinary logging and adds to `t_shift` every time. The shift is never removed, so
the reported time inflates all drive.

On route 00000365 that turned a 753-second drive into timestamps past t+3300 -- a factor of four.
Reading only `carState`, a single ordered stream, the same code gave the honest 753 s, which is how
the discrepancy finally surfaced.

**The event data was never wrong** -- speeds, targets, radii and their order within a printed table
are all fine, because consecutive rows carry nearly the same shift. What was wrong is every absolute
`t+NNNN` label, and any duration measured across a stretch long enough to accumulate more shift.

THE FIX. A route crossing a reboot is real but rare, and it looks nothing like interleaving: monotime
drops by the whole uptime, not by a couple of seconds. So compare against the running MAXIMUM rather
than the previous message, and only treat a reboot-sized fall as a reset.
"""
from __future__ import annotations

# A backward step of at least this much is a reboot. Ordinary inter-service interleaving is well
# under a second; a few seconds happens. An hour of uptime disappearing does not.
REBOOT_GAP_S = 60.0


class DriveClock:
  """Drive-relative seconds from `logMonoTime`, tolerant of out-of-order services.

      clock = DriveClock()
      for msg in LogReader(path):
        ts = clock.seconds(msg.logMonoTime)
  """

  def __init__(self, reboot_gap_s: float = REBOOT_GAP_S) -> None:
    self.reboot_gap_s = reboot_gap_s
    self._t0: float | None = None
    self._max_raw: float | None = None
    self._shift = 0.0
    self.reboots = 0

  def seconds(self, log_mono_time: int | float) -> float:
    raw = log_mono_time / 1e9
    if self._max_raw is not None and raw < self._max_raw - self.reboot_gap_s:
      # The clock genuinely restarted. Continue the timeline from where it left off.
      self._shift += self._max_raw - raw
      self._max_raw = raw
      self.reboots += 1
    elif self._max_raw is None or raw > self._max_raw:
      self._max_raw = raw
    t = raw + self._shift
    if self._t0 is None:
      self._t0 = t
    return t - self._t0
