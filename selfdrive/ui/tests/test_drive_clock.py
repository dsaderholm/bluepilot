"""The drive clock must report real drive time from a route's segment files.

Two earlier versions inflated a 754-second drive past t+3300, both by "correcting" a backward step
that needed no correction. The shape that fooled them is the one asserted here: every segment file
replays the boot-time header messages, so walking segments in order steps backward at every boundary
by an amount that GROWS as the drive goes on.

Measured on route 00000365 -- each segment starts at monotime 70.0 and ends 60 s later than the last:

    seg 0    70.0 -> 131.3
    seg 1    70.0 -> 191.3
    ...
    seg 12   70.0 -> 824.4      real drive length 754 s

So the fixture below is that pattern, and the assertion is the number a human can check: 754.
"""
from __future__ import annotations

import importlib.util
import pathlib

SPEC = importlib.util.spec_from_file_location(
  "bp_logtime", pathlib.Path(__file__).resolve().parents[3] / "tools/bp_logtime.py")
bp_logtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bp_logtime)
DriveClock = bp_logtime.DriveClock

NS = 1_000_000_000
HEADER_MONO = 70.0          # where every segment's replayed header messages sit
SEG_S = 60.0


def _route(segments: int, last_seg_s: float = SEG_S):
  """(monotime seconds) for a route, in the order LogReader yields them segment by segment.

  The final segment is PARTIAL, as a real one is -- 00000365 ends 34 s into its thirteenth.
  """
  for seg in range(segments):
    yield HEADER_MONO                                  # header replay, same value every segment
    start = HEADER_MONO + seg * SEG_S
    span = last_seg_s if seg == segments - 1 else SEG_S
    for i in range(int(span)):
      yield start + i + 1.0                            # that segment's own data


def test_a_754_second_drive_reports_as_754_seconds():
  clock = DriveClock()
  last = 0.0
  for mono in _route(13, last_seg_s=34.0):
    ts = clock.seconds(int(mono * NS))
    last = max(last, ts)
  assert 750 <= last <= 760, f"a 754 s drive reported as {last:.0f} s"


def test_segment_header_replay_is_not_treated_as_a_reboot():
  clock = DriveClock()
  for mono in _route(13, last_seg_s=34.0):
    clock.seconds(int(mono * NS))
  assert not clock.went_backwards, "the per-segment header replay was mistaken for a clock reset"


def test_the_clock_starts_at_zero_whatever_the_uptime():
  clock = DriveClock()
  assert clock.seconds(int(86_400 * NS)) == 0.0        # a device up for a day
  assert clock.seconds(int(86_410 * NS)) == 10.0


def test_a_header_message_arriving_late_still_anchors_the_start():
  """Segment 0's header is the smallest monotime, but it is not always the first message seen."""
  clock = DriveClock()
  assert clock.seconds(int(200 * NS)) == 0.0           # data first
  assert clock.seconds(int(70 * NS)) == 0.0            # header, older -- becomes the new anchor
  assert clock.seconds(int(210 * NS)) == 140.0         # measured from the header, not the data


def test_a_real_clock_reset_is_reported_not_smoothed():
  clock = DriveClock()
  for i in range(2000):
    clock.seconds(int((100 + i) * NS))
  clock.seconds(int(5 * NS))
  assert clock.went_backwards, "a genuine monotime reset was hidden"
  # How far below the route's START it fell -- 100 s in, back to 5 s. Not the drop from the peak,
  # which is just elapsed drive time and says nothing about the reset.
  assert clock.max_fall_below_start == 95.0
