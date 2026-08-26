"""FusionPilot: openpilot may ask the powertrain for Ford's own range of deceleration.

Upstream's `min_gas` of -0.5 m/s^2 is a generic openpilot constant, not a Ford number. Measured
across 7 drives / 99,520 frames of the camera's own `AccPrpl_A_Rq`:

    Ford's range   -2.710 .. 2.300      p01 -1.030   p50 0.280   p99 1.850
    below -0.5     2,851 frames (2.86%)

IT WAS AN ENVELOPE CHANGE ONLY UNTIL 2026-08-25, and that sentence is how it went wrong: the
Python clamp in `create_acc_msg` still held -0.5 when the propulsion blend started asking for
Ford's -0.66, so **-0.490 went out on the wire** -- silently, in the conservative direction. What
these tests protect is that panda and the Python side cannot drift apart, in EITHER value, and
that the FRICTION BRAKE band is not touched.
"""
import pathlib
import re

import pytest

from opendbc.sunnypilot.car.ford.fordcan_ext import _PANDA_GAS_MIN_STOCK, _PANDA_GAS_MIN_WIDE
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
  assert stock / 100.0 - 5.0 == _PANDA_GAS_MIN_STOCK
  # BOTH sides of the pair, since 2026-08-25. This used to pin only the stock number against the
  # single `_PANDA_GAS_MIN` the Python side had -- and when panda was widened and that constant was
  # not, the test still passed while -0.490 went out on the wire in place of Ford's -0.66. A test
  # that pins one end of a pair cannot notice the two ends parting.
  assert wide / 100.0 - 5.0 == _PANDA_GAS_MIN_WIDE


def test_the_c_flag_bit_and_the_python_flag_bit_are_the_same_bit():
  bit = int(re.search(r"FORD_PARAM_SP_FORD_ENVELOPE = (\d+)", _src()).group(1))
  assert bit == FordSafetyFlagsSP.FORD_MEASURED_ENVELOPE


def test_the_widened_floor_is_actually_wired_into_the_band():
  """A #define nothing references is not a change. This asserts the ternary reaches .min_gas and --
  the half that matters -- that .min_accel does NOT, so the friction brake keeps its own cap."""
  src = _src()
  min_gas = re.search(r"^\s*\.min_gas\s*=\s*(.+?),", src, re.M).group(1)
  assert "ford_bp_ford_envelope" in min_gas, f".min_gas is {min_gas!r}; the flag is not wired in"
  assert "FORD_MIN_GAS_WIDE" in min_gas and "FORD_MIN_GAS_STOCK" in min_gas

  min_accel = re.search(r"^\s*\.min_accel\s*=\s*(.+?),", src, re.M).group(1)
  assert "ford_bp_ford_envelope" not in min_accel, "the FRICTION BRAKE cap was widened too"
  assert "FORD_MIN_GAS" not in min_accel


def test_the_geometry_table_cannot_grow_into_the_flag_bit():
  """Bit 5 is the first bit above the 4-bit pinion geometry field, and nothing else guards it.

  `_initialize_ford` packs the index as `geometry_index << 1`, so a table reaching 16 entries writes
  32 -- which IS FORD_MEASURED_ENVELOPE. That would widen panda's gas band from an edit that looks
  like adding a Ford platform. Twelve entries today.
  """
  worst = max(FORD_PINION_GEOMETRY_INDEX.values()) << FORD_PINION_GEOMETRY_SHIFT
  assert worst & FordSafetyFlagsSP.FORD_MEASURED_ENVELOPE == 0, (
    f"the geometry index now reaches {worst:#x}, which overlaps FORD_MEASURED_ENVELOPE "
    f"({FordSafetyFlagsSP.FORD_MEASURED_ENVELOPE:#x}) -- move the flag in BOTH values_ext.py and ford.h")


def test_the_flag_is_set_from_op_long_at_car_init():
  """Parsed rather than grepped: every comment explaining this feature contains the flag name."""
  import ast
  import inspect

  from opendbc.sunnypilot.car import interfaces as sp_interfaces

  tree = ast.parse(inspect.getsource(sp_interfaces._initialize_ford).lstrip())
  names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
  assert "FORD_MEASURED_ENVELOPE" in names, "_initialize_ford never sets the flag"
  assert "openpilotLongitudinalControl" in names, "the flag is not gated on op long"
  consts = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)}
  assert "StockAccPassthrough" not in consts, "still gated on the deleted passthrough param"


# ---------------------------------------------------------------------------------------------
# FusionPilot 2026-08-26: THE ACCELERATION END. Added after he asked "how many drives have you
# measured?" and the answer turned out to invalidate the change that was about to ship.
#
# Ford, measured across 114,079 frames where its ACC was genuinely driving (engaged, moving, foot
# off the brake, cancel clear) -- `tools/bp_ford_brake_extremes.py`, routes c0-c3, no segment cap:
#
#                        min    p00.1     p99   p99.9     max
#     AccBrkTot_A_Rq   -3.34    -3.01    1.70    2.06    3.24
#     AccPrpl_A_Rq     -3.50    (p01 -0.66)      2.16    2.25
# ---------------------------------------------------------------------------------------------
from opendbc.car.ford.values import CarControllerParams  # noqa: E402
from opendbc.sunnypilot.car.ford.fordcan_ext import (  # noqa: E402
  _PANDA_ACCEL_MIN, _PANDA_ACCEL_MAX_STOCK, _PANDA_ACCEL_MAX_WIDE, _PANDA_GAS_MAX_STOCK, _PANDA_GAS_MAX_WIDE,
)


def _raw(name: str) -> int:
  return int(re.search(rf"#define {name}\s+(\d+)", _src()).group(1))


class TestTheAccelerationCeilingIsWidenedAtBothLayers:
  """A pair, pinned at BOTH ends. Pinning one end is how -0.490 went out on the wire where Ford's
  -0.66 belonged: panda moved, the Python clamp did not, and the test watching only the stock
  number stayed green throughout."""

  def test_accbrktot_ceiling_matches_between_c_and_python(self):
    assert _raw("FORD_MAX_ACCEL_STOCK") * 0.0039 - 20.0 == pytest.approx(_PANDA_ACCEL_MAX_STOCK, abs=5e-5)
    assert _raw("FORD_MAX_ACCEL_WIDE") * 0.0039 - 20.0 == pytest.approx(_PANDA_ACCEL_MAX_WIDE, abs=5e-5)

  def test_accprpl_ceiling_matches_between_c_and_python(self):
    assert _raw("FORD_MAX_GAS_STOCK") / 100.0 - 5.0 == pytest.approx(_PANDA_GAS_MAX_STOCK, abs=1e-9)
    assert _raw("FORD_MAX_GAS_WIDE") / 100.0 - 5.0 == pytest.approx(_PANDA_GAS_MAX_WIDE, abs=1e-9)

  def test_both_ceilings_are_wired_into_the_band(self):
    src = _src()
    for field, wide in ((r"\.max_accel", "FORD_MAX_ACCEL_WIDE"), (r"\.max_gas", "FORD_MAX_GAS_WIDE")):
      expr = re.search(rf"^\s*{field}\s*=\s*(.+?),", src, re.M).group(1)
      assert "ford_bp_ford_envelope" in expr, f"{field} is not gated on op long: {expr!r}"
      assert wide in expr, f"{field} does not reference {wide}: {expr!r}"

  def test_the_wide_ceilings_cover_what_ford_was_measured_asking_for(self):
    """2.06 is AccBrkTot's p99.9 and 2.16 is AccPrpl's. Covering the DISTRIBUTION is the claim;
    covering a lone max is how an envelope gets built on noise."""
    assert _PANDA_ACCEL_MAX_WIDE >= 3.24, "does not cover Ford's measured AccBrkTot max"
    assert _PANDA_GAS_MAX_WIDE >= 2.25, "does not cover Ford's measured AccPrpl max"


class TestTheControlSideSitsInsideTheEnvelope:
  """panda DROPS a frame outside its band -- it does not soften it -- so a controller clamp that
  lands OUTSIDE is not conservative, it deletes a 50 Hz message."""

  def test_accel_min_is_inside_pandas_floor_and_this_is_the_bug_it_fixes(self):
    """-3.5 against a -3.4991 floor put every hardest-braking frame 0.0009 OUTSIDE. Route 000003b8:
    15 rejected frames, ACC faulted 2.8 s later, next ignition came up "Cruise Fault"."""
    assert CarControllerParams.ACCEL_MIN > _PANDA_ACCEL_MIN, (
      f"ACCEL_MIN {CarControllerParams.ACCEL_MIN} is outside panda's {_PANDA_ACCEL_MIN}; "
      "every frame reaching this clip is DROPPED, not softened")

  def test_accel_max_is_inside_the_widened_ceilings(self):
    assert CarControllerParams.ACCEL_MAX <= _PANDA_GAS_MAX_WIDE
    assert CarControllerParams.ACCEL_MAX <= _PANDA_ACCEL_MAX_WIDE

  def test_accel_max_actually_moved_above_upstreams_ceiling(self):
    """Ford's ordinary pull-away sits ON 2.0. Leaving ACCEL_MAX there is the whole "it went
    ridiculously slow" complaint, and widening panda alone would not have moved it."""
    assert CarControllerParams.ACCEL_MAX > _PANDA_GAS_MAX_STOCK

  def test_the_friction_brake_floor_was_NOT_widened(self):
    """Ford's worst while actually driving is -3.34, INSIDE panda's -3.4991. The -4.63 that nearly
    justified widening this came from frames where ACC was not running."""
    assert _PANDA_ACCEL_MIN == -3.4991
    min_accel = re.search(r"^\s*\.min_accel\s*=\s*(.+?),", _src(), re.M).group(1)
    assert "ford_bp_ford_envelope" not in min_accel


class TestTheACTIVEClampIsTheWideOne:
  """MUTATION TESTING PUT THIS HERE, and it is the third instance of one mistake.

  The tests above pin `_PANDA_*_WIDE` against ford.h, which proves the two DEFINITIONS agree and
  proves nothing about which one the clamp reaches for. Setting `_PANDA_ACCEL_MAX =
  _PANDA_ACCEL_MAX_STOCK` survived the whole suite -- panda widened, openpilot's own request
  clamped straight back down, exactly the shape that put -0.490 on the wire where Ford's -0.66
  belonged on 2026-08-25.

  A pair needs three assertions, not two: each end defined right, AND the live one bound to the
  end you meant.
  """

  def test_the_accel_clamp_in_use_is_the_wide_one(self):
    from opendbc.sunnypilot.car.ford import fordcan_ext as fx
    assert fx._PANDA_ACCEL_MAX == fx._PANDA_ACCEL_MAX_WIDE, (
      f"the clamp actually applied is {fx._PANDA_ACCEL_MAX}, not the widened "
      f"{fx._PANDA_ACCEL_MAX_WIDE}; panda's room is unreachable")

  def test_the_gas_clamp_in_use_is_the_wide_one(self):
    from opendbc.sunnypilot.car.ford import fordcan_ext as fx
    assert fx._PANDA_GAS_MAX == fx._PANDA_GAS_MAX_WIDE

  def test_the_gas_floor_in_use_is_the_wide_one(self):
    """The one that already bit. Kept beside its siblings so the trio is obviously a trio."""
    from opendbc.sunnypilot.car.ford import fordcan_ext as fx
    assert fx._PANDA_GAS_MIN == fx._PANDA_GAS_MIN_WIDE
