"""FusionPilot: the settings screen tells him where the gain ramp goes flat. Keep that number true.

`curvature_factor` interpolates from `FordHighSpeedDampening_ang` at low curvature to
`1.15 * FordHighSpeedFactor_ang` at high curvature, so the ramp is flat when those are equal --
i.e. at High = Dampening / 1.15. That 1.15 is `_GAIN_CAN[1]` in
opendbc/sunnypilot/car/ford/lateral_angle_ext.py, the high-curvature gain anchor for a non-CAN-FD
Ford, which is his car.

The settings screen DUPLICATES it rather than importing it, deliberately: the UI process has no
business pulling in the car layer to render a description. This file is the price of that choice --
it is the "make the note executable" rule, because a duplicated constant with a comment saying
"keep these in sync" is exactly the kind of prose that stops being true the moment the code moves.

Parsed rather than imported: `bluepilot.py` needs pyray and the whole UI stack, and
`lateral_angle_ext.py` reaches the opendbc car layer, so neither imports in the offline suite.
"""
from __future__ import annotations

import ast
import os
import re


def _repo_root() -> str:
  d = os.path.dirname(os.path.abspath(__file__))
  while not os.path.exists(os.path.join(d, "common", "params_keys.h")):
    parent = os.path.dirname(d)
    assert parent != d, "repo root not found: no ancestor contains common/params_keys.h"
    d = parent
  return d


ROOT = _repo_root()
UI = os.path.join(ROOT, "selfdrive/ui/bp/layouts/settings/bluepilot.py")
LAT = os.path.join(ROOT, "opendbc_repo/opendbc/sunnypilot/car/ford/lateral_angle_ext.py")


def _ui_anchor() -> float:
  src = open(UI, encoding="utf-8").read()
  for node in ast.walk(ast.parse(src)):
    if isinstance(node, ast.Assign):
      for t in node.targets:
        if isinstance(t, ast.Name) and t.id == "_HIGH_CURV_GAIN_ANCHOR":
          return float(ast.literal_eval(node.value))
  raise AssertionError("_HIGH_CURV_GAIN_ANCHOR is gone from the settings screen")


def _gain_can_high() -> float:
  src = open(LAT, encoding="utf-8").read()
  m = re.search(r"^_GAIN_CAN\s*=\s*\(([^)]+)\)", src, re.MULTILINE)
  assert m, "_GAIN_CAN is gone from lateral_angle_ext"
  low, high = (float(x) for x in m.group(1).split(","))
  return high


def test_flat_point_matches_the_gain_schedule():
  """If these drift, the settings screen confidently prints a flat point that is not flat -- which
  is worse than printing nothing, because he would tune to it."""
  assert _ui_anchor() == _gain_can_high(), (
    f"the settings screen divides Dampening by {_ui_anchor()} but the gain schedule uses "
    f"{_gain_can_high()} -- the flat point shown on the car is wrong")


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
    f"shipped defaults are not the flat pair: Dampening {damp} implies High "
    f"{damp / _gain_can_high():.3f}, but High ships at {high}")
