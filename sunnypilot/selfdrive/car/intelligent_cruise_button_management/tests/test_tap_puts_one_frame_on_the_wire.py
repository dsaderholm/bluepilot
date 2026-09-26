"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

# FusionPilot: a tap must put EXACTLY ONE button frame on the wire.
#
# Measured on route 0000049d t+33085: one `increase` moved the dash 75 -> 77, and ICBM then had to
# press `decrease` to come back. Two mph from one tap, inside the band whose whole purpose is to
# ask for one. The cause is arithmetic between two files that never agreed:
#
#   controller.py   holds the request for TAP_ON_FRAMES
#   ford/icbm.py    emits when (frame - last_button_frame) * DT_CTRL > 0.05
#
# so the request window has to be short enough to contain a single emission opportunity. This file
# REPLAYS the emitter's own rule rather than restating the conclusion -- a test that asserted
# `TAP_ON_FRAMES == 6` would pass against an emitter that had changed its cadence underneath it,
# which is the "assert on the expression, not a window" lesson from 2026-09-17.

import unittest

from openpilot.common.realtime import DT_CTRL
from openpilot.sunnypilot.selfdrive.car.intelligent_cruise_button_management.controller import (
  TAP_BAND, TAP_ON_FRAMES, TAP_CYCLE_FRAMES,
)

# ford/icbm.py: `if (self.frame - self.last_button_frame) * DT_CTRL > 0.05`
WIRE_PERIOD_S = 0.05


def _wire_frames(on_frames: int, cycle_frames: int, cycles: int = 3) -> list[int]:
  """Run the real duty cycle through the real emitter rule and return the emitting frame numbers."""
  last = -10_000
  out = []
  tap_phase = 0
  for frame in range(cycle_frames * cycles):
    tap_phase += 1
    requesting = (tap_phase % cycle_frames) < on_frames
    if requesting and (frame - last) * DT_CTRL > WIRE_PERIOD_S:
      out.append(frame)
      last = frame
  return out


class TestATapIsOnePress(unittest.TestCase):
  def test_no_two_presses_land_inside_one_tap(self):
    """The property, stated as the defect: two frames on the wire from a SINGLE request window.

    Asserting a count per cycle instead is what the first version of this test did, and it failed
    on its own harness -- the loop starts mid-phase, so three cycles legitimately contain a fourth
    boundary press. The gap between consecutive presses is the thing that cannot be argued with.
    """
    fired = _wire_frames(TAP_ON_FRAMES, TAP_CYCLE_FRAMES, cycles=4)
    gaps = [b - a for a, b in zip(fired, fired[1:])]
    self.assertTrue(gaps, "no presses reached the wire at all")
    self.assertGreater(min(gaps), TAP_ON_FRAMES,
                       f"two presses {min(gaps)} frames apart fit inside one {TAP_ON_FRAMES}-frame "
                       f"request: that is the 75 -> 77 double step from route 0000049d. {fired}")

  def test_the_shipped_window_cannot_span_two_slots(self):
    # The whole defect in one line: a window longer than the emitter's period catches two.
    self.assertLessEqual(TAP_ON_FRAMES * DT_CTRL, WIRE_PERIOD_S + DT_CTRL,
                         "the request window is longer than the wire period, so one tap emits twice")

  def test_the_old_value_really_did_double_step(self):
    """Guards the diagnosis, not the fix. If this ever stops failing, the emitter changed and the
    reasoning behind TAP_ON_FRAMES has to be redone rather than trusted."""
    fired = _wire_frames(8, TAP_CYCLE_FRAMES, cycles=4)
    gaps = [b - a for a, b in zip(fired, fired[1:])]
    self.assertLessEqual(min(gaps), 8,
                         "8 frames no longer double-steps -- re-derive TAP_ON_FRAMES from the emitter")

  def test_a_tap_still_reaches_the_wire_at_all(self):
    """The other way to get this wrong: shorten it so far that taps are silently dropped and the
    set speed never converges."""
    self.assertGreaterEqual(len(_wire_frames(TAP_ON_FRAMES, TAP_CYCLE_FRAMES, cycles=5)), 5)

  def test_the_gap_between_cycles_is_long_enough_to_be_a_release(self):
    off = (TAP_CYCLE_FRAMES - TAP_ON_FRAMES) * DT_CTRL
    self.assertGreater(off, WIRE_PERIOD_S * 2,
                       "the car has to read a release rather than a repeat between taps")

  def test_the_band_itself_is_unchanged(self):
    # Not part of this fix, and moving it is how three earlier attempts broke the drop limiter.
    self.assertEqual(TAP_BAND, 2)


if __name__ == "__main__":
  unittest.main()
