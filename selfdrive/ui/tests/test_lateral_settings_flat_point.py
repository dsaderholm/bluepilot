"""FusionPilot: the settings screen tells him where the gain ramp goes flat. Keep that number true.

`curvature_factor` interpolates from `FordHighSpeedDampening_ang` at low curvature to
`anchor * FordHighSpeedFactor_ang` at high curvature, so the ramp is flat when those are equal --
i.e. at High = Dampening / anchor. That anchor is the high-curvature gain for the platform, in
opendbc/sunnypilot/car/ford/angle_gains.py.

AND THAT ANCHOR IS PLATFORM-SPECIFIC -- 1.15 on CAN, 0.95 on a CAN-FD truck, 1.05 on a CAN-FD
unibody SUV. The settings screen shipped a hardcoded 1.15 for exactly one commit, which would have
told an F-150 owner a flat point that is nothing of the sort. It now imports
`angle_gains.flat_high_speed_factor` and passes its own `carFingerprint`, so there is no duplicated
constant left to drift -- `angle_gains.py` deliberately imports only `CAR` so the UI process is not
dragging the car layer in to render a description.

Parsed rather than imported where it must be: `bluepilot.py` needs pyray and the whole UI stack.
"""
from __future__ import annotations

import os
import re

import pytest


def _repo_root() -> str:
  d = os.path.dirname(os.path.abspath(__file__))
  while not os.path.exists(os.path.join(d, "common", "params_keys.h")):
    parent = os.path.dirname(d)
    assert parent != d, "repo root not found: no ancestor contains common/params_keys.h"
    d = parent
  return d


ROOT = _repo_root()
UI = os.path.join(ROOT, "selfdrive/ui/bp/layouts/settings/bluepilot.py")


def _gain_can_high() -> float:
  from opendbc.sunnypilot.car.ford.angle_gains import GAIN_CAN
  return GAIN_CAN[1]


def test_the_screen_uses_the_SHARED_helper_not_its_own_constant():
  """There must be no second copy of the anchor to drift. A hardcoded number here is how the screen
  told a CAN-FD owner the wrong flat point for one commit."""
  src = open(UI, encoding="utf-8").read()
  assert "flat_high_speed_factor" in src, (
    "the settings screen no longer uses the shared flat-point helper")
  assert "_HIGH_CURV_GAIN_ANCHOR" not in src, (
    "the settings screen has re-introduced its own copy of the gain anchor")


def test_the_flat_point_is_PLATFORM_SPECIFIC():
  """The bug this file exists for. One High value cannot be flat on every Ford, so the screen must
  ask the car rather than assume his."""
  from opendbc.car.ford.values import CAR
  from opendbc.sunnypilot.car.ford.angle_gains import flat_high_speed_factor
  can = flat_high_speed_factor(0.78, CAR.FORD_FUSION_MK5)
  truck = flat_high_speed_factor(0.78, CAR.FORD_F_150_MK14)
  suv = flat_high_speed_factor(0.78, CAR.FORD_MUSTANG_MACH_E_MK1)
  assert can == pytest.approx(0.78 / 1.15, abs=1e-4)
  assert truck == pytest.approx(0.78 / 0.95, abs=1e-4)
  assert suv == pytest.approx(0.78 / 1.05, abs=1e-4)
  assert len({round(can, 3), round(truck, 3), round(suv, 3)}) == 3, (
    "the flat point is the same on all three platforms -- the anchor is being ignored")


def test_the_screen_passes_the_cars_own_fingerprint():
  src = open(UI, encoding="utf-8").read()
  assert "carFingerprint" in src, (
    "the screen computes a flat point without asking which car it is on")


def test_an_unknown_fingerprint_falls_back_to_CAN():
  """`update_angle_params` has always defaulted anything outside the two CAN-FD sets to the CAN
  pair. The helper must agree, or the screen and the controller disagree on an unrecognised car."""
  from opendbc.sunnypilot.car.ford.angle_gains import flat_high_speed_factor
  assert flat_high_speed_factor(0.78, None) == pytest.approx(0.78 / 1.15, abs=1e-4)


def test_the_screen_actually_shows_it():
  """Guards the other half: the constant can agree perfectly and still reach no description."""
  src = open(UI, encoding="utf-8").read()
  assert "def _flat_high_speed_factor" in src
  assert "self._high_speed_factor_description," in src, (
    "the High Speed Adjustment Factor is back on a static description -- the flat point is "
    "computed and rendered nowhere, which is this fork's oldest bug")
  assert "self._dampening_description," in src, (
    "the Dampening control no longer warns that it moves the other knob's flat point")


def test_the_dampening_default_and_the_factor_default_ARE_the_flat_point():
  """The shipped pair must be self-consistent: High = Dampening / anchor. If a later edit moves one
  without the other, the fork ships a tilted ramp while CLAUDE.md says it ships flat."""
  keys = open(os.path.join(ROOT, "common", "params_keys.h"), encoding="utf-8").read()

  def default(name):
    line = next(x for x in keys.splitlines() if f'"{name}"' in x and "{" in x)
    return float(re.search(r'"([0-9.]+)"\s*\}\}', line).group(1))

  damp = default("FordHighSpeedDampening_ang")
  high = default("FordHighSpeedFactor_ang")
  assert high == round(damp / _gain_can_high(), 2), (
    f"shipped defaults are not the flat pair FOR A CAN FORD: Dampening {damp} implies High "
    f"{damp / _gain_can_high():.3f}, but High ships at {high}. A single default cannot be flat on "
    "every platform -- see the params_keys.h comment; CAN-FD owners set theirs from the screen.")
