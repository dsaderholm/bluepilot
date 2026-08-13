"""The drive clock must survive out-of-order services and still catch a real reboot.

The bug this replaces inflated a 753-second drive into timestamps past t+3300 -- a factor of four --
because it treated ordinary inter-service interleaving as a clock reset and accumulated the
correction forever. Nothing said so; the numbers just looked like a longer drive.

So both directions are asserted here: interleaving must NOT move the clock, and a reboot must.
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


def test_interleaved_services_do_not_inflate_the_clock():
  """carState, radarState and modelV2 arrive out of order by seconds. That is not a reboot."""
  clock = DriveClock()
  # 600 s of drive, sampled every second, with each sample jittered backwards by up to 3 s the way
  # a slow service's messages land after a fast one's.
  jitter = [0.0, -2.5, -1.0, -3.0, -0.5]
  last = 0.0
  for i in range(600):
    for j in jitter:
      last = clock.seconds(int((i + j) * NS))
  assert clock.reboots == 0, "ordinary interleaving was mistaken for a reboot"
  # 600 samples, last one jittered -0.5 s, so just under 600.
  assert 595 <= last <= 600, f"a 600 s drive reported as {last:.0f} s"


def test_a_real_reboot_keeps_the_timeline_moving_forward():
  clock = DriveClock()
  for i in range(100):
    clock.seconds(int(i * NS))
  # Reboot: monotime falls back to near zero after 100 s of uptime.
  after = clock.seconds(int(0.5 * NS))
  assert clock.reboots == 1, "a monotime reset was not recognised"
  assert after >= 99, f"the timeline went backwards across a reboot: {after:.1f}"


def test_the_clock_starts_at_zero_whatever_the_uptime():
  clock = DriveClock()
  first = clock.seconds(int(86_400 * NS))       # a device up for a day
  assert first == 0.0
  assert clock.seconds(int(86_410 * NS)) == 10.0


def test_a_short_backward_step_is_never_a_reboot():
  """The old code's threshold was 1 s, which ordinary logging crosses constantly."""
  clock = DriveClock()
  clock.seconds(int(50 * NS))
  clock.seconds(int(45 * NS))     # 5 s late -- a slow service, not a reboot
  assert clock.reboots == 0
  assert clock.seconds(int(51 * NS)) == 1.0, "a late message shifted the whole timeline"
