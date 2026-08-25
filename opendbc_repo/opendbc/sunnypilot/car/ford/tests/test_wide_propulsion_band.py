"""FusionPilot: openpilot may ask the powertrain for Ford's own range of deceleration.

Upstream's `min_gas` of -0.5 m/s^2 is a generic openpilot constant, not a Ford number. Measured
across 7 drives / 99,520 frames of the camera's own `AccPrpl_A_Rq`:

    Ford's range   -2.710 .. 2.300      p01 -1.030   p50 0.280   p99 1.850
    below -0.5     2,851 frames (2.86%)

THIS IS AN ENVELOPE CHANGE ONLY. `create_acc_msg` still clamps its own request to -0.495, so
nothing openpilot transmits moves today. Widening the control side belongs with removing the
brake/gas mutual exclusion -- see bluepilot/FORD-ACC-PARITY.md. What these tests protect is that
panda and the Python side cannot drift apart, and that the FRICTION BRAKE band is not touched.
"""
import pathlib
import re

from opendbc.sunnypilot.car.ford.fordcan_ext import _PANDA_GAS_MIN
from opendbc.sunnypilot.car.ford.values_ext import (
  FORD_PINION_GEOMETRY_INDEX, FORD_PINION_GEOMETRY_SHIFT, FordSafetyFlagsSP,
)

_FORD_H = pathlib.Path(__file__).resolve().parents[4] / "safety" / "modes" / "ford.h"
_WIDE_MS2 = -2.8


def _src():
  return _FORD_H.read_text(encoding="utf-8")


def test_the_c_constants_match_their_documented_m_s2():
  """Raw units are (m/s^2 + 5.0) * 100. A drift here is silent and changes what the car will do."""
  src = _src()
  wide = int(re.search(r"#define FORD_MIN_GAS_WIDE (\d+)", src).group(1))
  stock = int(re.search(r"#define FORD_MIN_GAS_STOCK (\d+)", src).group(1))
  assert wide / 100.0 - 5.0 == _WIDE_MS2
  assert stock / 100.0 - 5.0 == _PANDA_GAS_MIN


def test_the_c_flag_bit_and_the_python_flag_bit_are_the_same_bit():
  bit = int(re.search(r"FORD_PARAM_SP_WIDE_PROPULSION = (\d+)", _src()).group(1))
  assert bit == FordSafetyFlagsSP.WIDE_PROPULSION_BAND


def test_the_widened_floor_is_actually_wired_into_the_band():
  """A #define nothing references is not a change. This asserts the ternary reaches .min_gas and --
  the half that matters -- that .min_accel does NOT, so the friction brake keeps its own cap."""
  src = _src()
  min_gas = re.search(r"^\s*\.min_gas\s*=\s*(.+?),", src, re.M).group(1)
  assert "ford_bp_wide_propulsion" in min_gas, f".min_gas is {min_gas!r}; the flag is not wired in"
  assert "FORD_MIN_GAS_WIDE" in min_gas and "FORD_MIN_GAS_STOCK" in min_gas

  min_accel = re.search(r"^\s*\.min_accel\s*=\s*(.+?),", src, re.M).group(1)
  assert "ford_bp_wide_propulsion" not in min_accel, "the FRICTION BRAKE cap was widened too"
  assert "FORD_MIN_GAS" not in min_accel


def test_the_geometry_table_cannot_grow_into_the_flag_bit():
  """Bit 5 is the first bit above the 4-bit pinion geometry field, and nothing else guards it.

  `_initialize_ford` packs the index as `geometry_index << 1`, so a table reaching 16 entries writes
  32 -- which IS WIDE_PROPULSION_BAND. That would widen panda's gas band from an edit that looks
  like adding a Ford platform. Twelve entries today.
  """
  worst = max(FORD_PINION_GEOMETRY_INDEX.values()) << FORD_PINION_GEOMETRY_SHIFT
  assert worst & FordSafetyFlagsSP.WIDE_PROPULSION_BAND == 0, (
    f"the geometry index now reaches {worst:#x}, which overlaps WIDE_PROPULSION_BAND "
    f"({FordSafetyFlagsSP.WIDE_PROPULSION_BAND:#x}) -- move the flag in BOTH values_ext.py and ford.h")


def test_the_flag_is_set_from_op_long_at_car_init():
  """Parsed rather than grepped: every comment explaining this feature contains the flag name."""
  import ast
  import inspect

  from opendbc.sunnypilot.car import interfaces as sp_interfaces

  tree = ast.parse(inspect.getsource(sp_interfaces._initialize_ford).lstrip())
  names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
  assert "WIDE_PROPULSION_BAND" in names, "_initialize_ford never sets the flag"
  assert "openpilotLongitudinalControl" in names, "the flag is not gated on op long"
  consts = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)}
  assert "StockAccPassthrough" not in consts, "still gated on the deleted passthrough param"
