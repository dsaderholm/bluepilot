"""FusionPilot: Ford's own propulsion floor reaches the car under the passthrough.

Upstream's `min_gas` of -0.5 m/s^2 is a generic openpilot constant, not a Ford number. Measured
across 7 drives / 99,520 frames of the camera's own `AccPrpl_A_Rq`:

    Ford's range   -2.710 .. 2.300      p01 -1.030   p50 0.280   p99 1.850
    below -0.5     2,851 frames (2.86%)

Every one of those was being softened to -0.495 on its way out, which the owner felt as missing
powertrain braking: 756 frames on one drive, median 0.415 and worst 1.095 m/s^2 given up, in
episodes up to 17.5 s. His call on widening it: "if we are abiding by Ford's safety, I don't care."

THE RISK THIS FILE GUARDS IS NOT THE WIDER BAND. It is the two halves disagreeing. Panda's floor
and this clamp are derived from ONE flag bit for that reason -- if Python were the more permissive
of the two, panda would drop the WHOLE frame and a 50 Hz message would vanish and reappear, which
is the exact failure `passthrough_admissible` was built to prevent.
"""
from __future__ import annotations

import pathlib
import re

from opendbc.sunnypilot.car.ford.fordcan_ext import (
  _PANDA_GAS_INACTIVE, _PANDA_GAS_MAX, _PANDA_MARGIN, _PANDA_GAS_MIN, _PANDA_GAS_MIN_PASSTHROUGH,
  create_acc_msg, create_acc_msg_passthrough, passthrough_gas_floor,
)
from opendbc.sunnypilot.car.ford.values_ext import FordSafetyFlagsSP
from opendbc.sunnypilot.car.ford.tests.test_acc_passthrough import _CAN, _Packer, _stock

_FORD_H = pathlib.Path(__file__).resolve().parents[4] / "safety" / "modes" / "ford.h"
_WIDE = _PANDA_GAS_MIN_PASSTHROUGH


class _CP_SP:
  def __init__(self, safety_param):
    self.safetyParam = safety_param


def _sent(gas, floor=None):
  packer = _Packer()
  create_acc_msg_passthrough(packer, _CAN, _stock(AccPrpl_A_Rq=gas), gas_floor=floor)
  return packer.calls[0][2]["AccPrpl_A_Rq"]


# --- the two halves must be the same number ----------------------------------------------------

def test_the_c_constant_and_the_python_constant_are_the_same_number():
  """Raw units in the DBC are (m/s^2 + 5.0) * 100. If these ever drift apart in the permissive
  direction the passthrough starts losing whole frames at 50 Hz."""
  src = _FORD_H.read_text(encoding="utf-8")
  wide = int(re.search(r"#define FORD_MIN_GAS_PASSTHROUGH (\d+)", src).group(1))
  stock = int(re.search(r"#define FORD_MIN_GAS_STOCK (\d+)", src).group(1))
  assert wide / 100.0 - 5.0 == _WIDE
  assert stock / 100.0 - 5.0 == _PANDA_GAS_MIN


def test_the_c_flag_bit_and_the_python_flag_bit_are_the_same_bit():
  src = _FORD_H.read_text(encoding="utf-8")
  bit = int(re.search(r"FORD_PARAM_SP_PASSTHROUGH_LONG = (\d+)", src).group(1))
  assert bit == FordSafetyFlagsSP.PASSTHROUGH_LONG


def test_the_widened_floor_is_actually_WIRED_INTO_the_band():
  """A #define nothing references is not a change. This asserts the ternary reaches .min_gas, and
  -- the half that matters -- that .min_accel does NOT, so the friction brake keeps its own cap."""
  src = _FORD_H.read_text(encoding="utf-8")
  min_gas = re.search(r"^\s*\.min_gas\s*=\s*(.+?),", src, re.M).group(1)
  assert "ford_bp_passthrough_long" in min_gas, f".min_gas is {min_gas!r}; the flag is not wired in"
  assert "FORD_MIN_GAS_PASSTHROUGH" in min_gas and "FORD_MIN_GAS_STOCK" in min_gas

  min_accel = re.search(r"^\s*\.min_accel\s*=\s*(.+?),", src, re.M).group(1)
  assert "ford_bp_passthrough_long" not in min_accel, "the FRICTION BRAKE cap was widened too"
  assert "FORD_MIN_GAS" not in min_accel


def test_the_flag_is_set_from_the_passthrough_param_at_car_init():
  """Panda cannot widen anything if nobody sets the bit. Parsed rather than grepped: every comment
  explaining this feature contains the flag name."""
  import ast, inspect
  from opendbc.sunnypilot.car import interfaces as sp_interfaces

  src = inspect.getsource(sp_interfaces._initialize_ford)
  tree = ast.parse(src.lstrip())
  names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
  assert "PASSTHROUGH_LONG" in names, "_initialize_ford never sets the passthrough flag"
  consts = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)}
  assert "StockAccPassthrough" in consts, "the flag is not keyed on the passthrough param"

  # ...and the param has to be handed to setup_interfaces at all
  from openpilot.sunnypilot.selfdrive.car.interfaces import initialize_params

  class _P:
    @staticmethod
    def get(k, return_default=True):
      return 0

  keys = {k for d in initialize_params(_P()) for k in d}
  assert "StockAccPassthrough" in keys, "the param never reaches _initialize_ford's params_dict"


def test_the_flag_bit_does_not_collide_with_the_pinion_geometry_index():
  """Bits 1-4 carry the geometry index. A collision would silently select the wrong steering
  geometry, which is a far worse bug than anything this feature could cause."""
  assert FordSafetyFlagsSP.PASSTHROUGH_LONG & (0xF << 1) == 0
  assert FordSafetyFlagsSP.PASSTHROUGH_LONG & FordSafetyFlagsSP.STEER_ANGLE_CURVATURE == 0


def test_the_geometry_table_cannot_GROW_into_the_passthrough_bit():
  """Bit 5 is the first bit above the 4-bit pinion geometry field, and nothing else guards it.

  `_initialize_ford` packs the index as `geometry_index << 1`, so a table that ever reaches 16
  entries writes 32 -- which IS `PASSTHROUGH_LONG`. That would widen panda's gas band on a car
  that is not running the passthrough at all, from an edit that looks like adding a Ford platform
  and has nothing to do with this feature. There are 12 entries today, so 3 platforms of headroom.
  """
  from opendbc.sunnypilot.car.ford.values_ext import (
    FORD_PINION_GEOMETRY_INDEX, FORD_PINION_GEOMETRY_SHIFT,
  )

  worst = max(FORD_PINION_GEOMETRY_INDEX.values()) << FORD_PINION_GEOMETRY_SHIFT
  assert worst & FordSafetyFlagsSP.PASSTHROUGH_LONG == 0, (
    f"the geometry index now reaches {worst:#x}, which overlaps PASSTHROUGH_LONG "
    f"({FordSafetyFlagsSP.PASSTHROUGH_LONG:#x}) -- move the flag to a higher bit in BOTH "
    f"values_ext.py and safety/modes/ford.h")


def test_the_floor_is_read_from_the_flag_panda_was_actually_given():
  assert passthrough_gas_floor(_CP_SP(FordSafetyFlagsSP.PASSTHROUGH_LONG)) == _WIDE
  assert passthrough_gas_floor(_CP_SP(0)) == _PANDA_GAS_MIN
  # the pinion flag and a geometry index must not widen anything by themselves
  assert passthrough_gas_floor(_CP_SP(FordSafetyFlagsSP.STEER_ANGLE_CURVATURE | (5 << 1))) == _PANDA_GAS_MIN


def test_an_unreadable_safety_param_falls_back_to_the_NARROW_floor():
  """The safe direction: clamping more than panda requires costs a little braking, clamping less
  costs the entire frame."""
  class _Broken:
    safetyParam = "not an int"

  assert passthrough_gas_floor(_Broken()) == _PANDA_GAS_MIN
  assert passthrough_gas_floor(object()) == _PANDA_GAS_MIN


# --- what actually goes on the wire ------------------------------------------------------------

def test_fords_real_braking_reaches_the_car_with_the_flag_set():
  """Every one of these is a value Ford actually commanded on a recorded drive."""
  for gas in (-0.51, -1.03, -2.30, -2.710):
    assert _sent(gas, _WIDE) == gas, f"{gas} was softened; that is the powertrain braking he misses"


def test_the_same_values_are_still_softened_without_the_flag():
  for gas in (-0.51, -1.03, -2.710):
    assert _sent(gas) == _PANDA_GAS_MIN + _PANDA_MARGIN, f"{gas} went out below panda's stock floor"


def test_past_the_new_floor_is_still_clamped_not_forwarded():
  """The band has to end somewhere, and -2.8 is past Ford's measured -2.710 on purpose."""
  assert _sent(-3.4, _WIDE) == _WIDE + _PANDA_MARGIN


def test_the_inactive_escape_is_never_clamped_by_the_wider_floor():
  """-5.0 sits BELOW the band and means 'not requesting'. Clamping it would invert it into a
  propulsion request -- the one way this change could be dangerous rather than merely wrong."""
  assert _sent(_PANDA_GAS_INACTIVE, _WIDE) == _PANDA_GAS_INACTIVE


def test_ordinary_propulsion_is_untouched_by_either_floor():
  for gas in (-0.4, 0.0, 1.0, 1.9):
    assert _sent(gas, _WIDE) == gas
    assert _sent(gas) == gas


def test_the_FRICTION_BRAKE_band_is_untouched_by_all_of_this():
  """`AccBrkTot_A_Rq` IS the brake and its floor is deliberately NOT widened, so the two calls
  have to agree exactly. Widening the gas band is a statement about the powertrain only."""
  for accel in (-2.70, -3.16, -3.60):
    wide, narrow = _Packer(), _Packer()
    create_acc_msg_passthrough(wide, _CAN, _stock(AccBrkTot_A_Rq=accel), gas_floor=_WIDE)
    create_acc_msg_passthrough(narrow, _CAN, _stock(AccBrkTot_A_Rq=accel))
    assert wide.calls[0][2]["AccBrkTot_A_Rq"] == narrow.calls[0][2]["AccBrkTot_A_Rq"]


def test_OPENPILOTS_OWN_command_stays_conservative_whatever_panda_now_allows():
  """The load-bearing one, and the reason a wider band is not a wider blast radius.

  Panda is the OUTER envelope; this Python clamp is what keeps openpilot itself conservative.
  `create_acc_msg` authors the fallback frame AND the stop override's frame, and it must go on
  clamping at -0.495 regardless of what the firmware would now accept. The wider band exists to
  carry FORD's numbers through untouched, not to hand openpilot a bigger lever.
  """
  for gas in (-0.51, -1.03, -2.710):
    packer = _Packer()
    create_acc_msg(packer, _CAN, True, gas=gas, accel=-0.5, accel_pred=-5.0, stopping=False,
                   brake_actuate=False, precharge_actuate=False, v_ego_kph=50.0)
    sent = packer.calls[0][2]["AccPrpl_A_Rq"]
    assert sent == _PANDA_GAS_MIN + _PANDA_MARGIN, f"openpilot authored {sent}; it must stay at its own floor"


def test_OPENPILOTS_OWN_PREDICTED_accel_also_stays_inside_the_stock_band():
  """The sibling field of the one above, and the one the review caught going out raw.

  Panda checks `min_gas` against BOTH `AccPrpl_A_Rq` and `AccPrpl_A_Pred`, so widening the floor
  enlarged what this field could carry. It is the -5.0 sentinel in practice, which is precisely
  why it needs a test rather than a reader's trust in a constant three files away.
  """
  for pred in (-0.51, -1.03, -2.710, 2.5):
    packer = _Packer()
    create_acc_msg(packer, _CAN, True, gas=0.0, accel=-0.5, accel_pred=pred, stopping=False,
                   brake_actuate=False, precharge_actuate=False, v_ego_kph=50.0)
    sent = packer.calls[0][2]["AccPrpl_A_Pred"]
    assert _PANDA_GAS_MIN <= sent <= _PANDA_GAS_MAX, f"authored {sent}, outside the stock band"


def test_the_inactive_sentinel_survives_the_pred_clamp():
  """-5.0 means "no prediction". Dragging it into the band would invent a propulsion hint."""
  packer = _Packer()
  create_acc_msg(packer, _CAN, True, gas=0.0, accel=-0.5, accel_pred=_PANDA_GAS_INACTIVE,
                 stopping=False, brake_actuate=False, precharge_actuate=False, v_ego_kph=50.0)
  assert packer.calls[0][2]["AccPrpl_A_Pred"] == _PANDA_GAS_INACTIVE
