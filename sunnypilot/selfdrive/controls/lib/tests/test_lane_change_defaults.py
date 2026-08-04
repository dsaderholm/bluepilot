"""
BluePilot: the shipped defaults for auto lane change.

A default is not a preference here, it is behaviour. A param that has never been written reads its
default, so on a device where the driver has not opened this control, upstream's 0 (Nudge) means no
timed lane change happens at all -- the car waits for a steering nudge that the owner is not going
to give.

This is an upstream param in an upstream file, which is exactly why it needs a test: a merge that
takes upstream's line wins silently, the setting looks fine on screen, and the only symptom is lane
changes quietly not happening on a fresh device.
"""

import re
from pathlib import Path

from openpilot.sunnypilot.selfdrive.controls.lib.auto_lane_change import (
  AutoLaneChangeMode, AUTO_LANE_CHANGE_TIMER,
)

ROOT = next(d for d in Path(__file__).resolve().parents if (d / "common" / "params_keys.h").exists())


def _default(key: str) -> str:
  src = (ROOT / "common" / "params_keys.h").read_text(encoding="utf-8", errors="replace")
  m = re.search(r'\{"%s",\s*\{[^}]*?,\s*\w+,\s*"([^"]*)"\}' % key, src)
  assert m, f"{key} is not declared in params_keys.h"
  return m.group(1)


def test_auto_lane_change_defaults_to_one_second():
  """The owner's own habit: blinker on, wait a second, change lanes."""
  assert int(_default("AutoLaneChangeTimer")) == AutoLaneChangeMode.ONE_SECOND


def test_one_second_really_means_one_second():
  """Guards the other half -- the enum value could survive a merge while the table behind it does
  not, and nothing on screen would look different."""
  assert AUTO_LANE_CHANGE_TIMER[AutoLaneChangeMode.ONE_SECOND] == 1.0


def test_the_default_is_a_nudgeless_mode():
  """Anything above NUDGE requires no steering input. If a merge left the default at or below it,
  the car would sit waiting for a nudge instead of changing lanes."""
  assert int(_default("AutoLaneChangeTimer")) > AutoLaneChangeMode.NUDGE
