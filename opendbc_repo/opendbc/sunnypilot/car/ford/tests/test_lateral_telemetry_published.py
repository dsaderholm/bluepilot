"""FusionPilot: the angle-mode gain must REACH A ROUTE, not just exist in memory.

2026-09-01. He reported that a setting which was perfect for 600 miles could not take curves the
next morning. Answering it required decoding `LateralMotionControl` (0x3D3) off `sendcan` and
RE-IMPLEMENTING the gain schedule in a tool, because `curvature_factor` -- the number that actually
multiplies the command -- was computed every frame and published nowhere. `bp_path_angle_final`
had a comment saying as much and a tool built around the gap.

That is this fork's oldest recurring bug: a value computed correctly and never rendered. It has now
happened with the SCC veto's suppressed target, the three ICBM readouts, the DEC slow-down fields,
and here. The pattern each time is that nothing failed -- the code was right, the number was right,
and the question was simply unanswerable afterwards.

So this file asserts the whole chain, because any single link silently breaks it:

    lateral_angle_ext  sets self.bp_*
    carcontroller      copies bp_* -> self.*        (angle mode only, zeroed otherwise)
    bp_card_publisher  reads CI.CC.* -> ControllerStateBP
    capnp              accepts the type

Two of those links are checked by EXECUTION rather than by reading source, because the two failures
that actually reached the car were a wiring mistake between objects (2026-08-15, AttributeError in
CarController.update) and a type refused at the capnp boundary (2026-08-18, plannerd dead on frame
one). Structural tests cannot see either.
"""
from __future__ import annotations

import ast
import os

import capnp
import numpy as np
import pytest

from cereal import custom

# The fixture and the strict CarState stand-in are the smoke test's; importing them keeps ONE
# harness that matches card's call convention rather than a second that merely resembles it.
from opendbc.sunnypilot.car.ford.tests.test_carcontroller_smoke import (  # noqa: F401
  carcontroller_parts, FakeCarState, _car_control,
)

def _repo_root():
  """Walk up to the marker rather than counting `..` levels.

  A fixed count broke: the runner can import this module as `opendbc.sunnypilot...` (through the
  opendbc_repo package alias) or by file path, and those differ by one directory -- so the same
  expression resolved to the repo on one invocation and its PARENT on another, producing
  FileNotFoundError for every source-reading test. Counting levels encodes an assumption about how
  pytest happened to collect the file.
  """
  d = os.path.dirname(os.path.abspath(__file__))
  while d != os.path.dirname(d):
    if os.path.exists(os.path.join(d, "cereal", "custom.capnp")):
      return d
    d = os.path.dirname(d)
  raise AssertionError("repo root not found: no ancestor contains cereal/custom.capnp")


ROOT = _repo_root()

# Every field this change added, and what it is for. The list is the test's subject: adding a field
# to custom.capnp without adding it here leaves it unchecked, which is the hole being closed.
FIELDS = [
  "pathAngleFinal",
  "kappaCmd",
  "curvatureFactor",
  "laneCenterCorrection",
  "gainLowCurv",
  "gainHighCurv",
  "blendWeight",
]


def _src(rel):
  return open(os.path.join(ROOT, rel), encoding="utf-8").read()


def _assigned_attrs(rel, obj):
  """Names assigned as `obj.<name> = ...` anywhere in the file."""
  out = set()
  for node in ast.walk(ast.parse(_src(rel))):
    if isinstance(node, ast.Assign):
      for t in node.targets:
        if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == obj:
          out.add(t.attr)
  return out


class TestTheCapnpBoundary:
  """Executed, not read. The boundary is where the process dies."""

  def test_every_field_exists_on_the_message(self):
    msg = custom.ControllerStateBP.new_message()
    for f in FIELDS:
      setattr(msg, f, 1.0)
      assert getattr(msg, f) == pytest.approx(1.0)

  def test_a_numpy_float_is_accepted(self):
    """`curvature_factor` comes out of `numpy.interp`, so it is a numpy scalar unless cast. The
    2026-08-18 plannerd death was exactly this, one struct over."""
    msg = custom.ControllerStateBP.new_message()
    for f in FIELDS:
      msg.__setattr__(f, float(np.float64(0.78)))
      assert getattr(msg, f) == pytest.approx(0.78)

  @pytest.mark.parametrize("value", [np.float64(0.78), np.float32(0.78), np.array([0.78, 0.79])])
  def test_an_UNCAST_numpy_value_IS_REFUSED(self, value):
    """The float() casts are load-bearing, VERIFIED rather than assumed: capnp refuses np.float64
    and np.float32 outright. `curvature_factor` is the output of `numpy.interp`, so it IS an
    np.float64 -- assigning it raw is the 2026-08-18 plannerd death exactly, one struct over.

    An earlier version of this test used only np.array, which any sane binding refuses; it would
    have passed while the realistic failure went unchecked."""
    msg = custom.ControllerStateBP.new_message()
    with pytest.raises((capnp.KjException, TypeError, ValueError)):
      msg.curvatureFactor = value

  def test_the_dataclass_mirrors_every_field(self):
    """A capnp field with no dataclass field crashed card at startup once; test_structs_capnp_parity
    guards the general case, this one names these fields so a partial revert is loud."""
    from opendbc.car import structs
    cs = structs.ControllerStateBP()
    for f in FIELDS:
      assert hasattr(cs, f), f"{f} is in custom.capnp but not in structs.ControllerStateBP"


class TestTheWiring:
  """Source-level, because these two hops have no offline execution path."""

  def test_the_publisher_sets_every_field(self):
    assigned = _assigned_attrs("bluepilot/selfdrive/car/bp_card_publisher.py", "cs_bp")
    missing = [f for f in FIELDS if f not in assigned]
    assert not missing, (
      f"computed and never published: {missing}. This is the exact bug the whole file exists for -- "
      "the value reaches the dataclass and stops there, so a route still cannot answer the question.")

  def test_the_carcontroller_copies_every_field(self):
    assigned = _assigned_attrs("opendbc_repo/opendbc/car/ford/carcontroller.py", "self")
    missing = [f for f in FIELDS if f not in assigned]
    assert not missing, (
      f"lateral_angle_ext computes these but CarController never exposes them: {missing}. The "
      "publisher reads CI.CC.<field>, so without this hop it publishes the getattr default forever.")

  def test_EVERY_PUBLISHED_FIELD_GOES_THROUGH_float(self):
    """VERIFIED, not assumed: capnp refuses np.float64 AND np.float32 outright, and
    `curvature_factor` is the return of `numpy.interp`. Dropping one cast is the 2026-08-18
    plannerd death -- the process dies on the first frame of the drive."""
    src = _src("bluepilot/selfdrive/car/bp_card_publisher.py")
    tree = ast.parse(src)
    bad = []
    for node in ast.walk(tree):
      if not isinstance(node, ast.Assign):
        continue
      for t in node.targets:
        if (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
            and t.value.id == "cs_bp" and t.attr in FIELDS):
          call = node.value
          if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                  and call.func.id == "float"):
            bad.append(t.attr)
    assert not bad, (
      f"published without a float() cast: {bad}. capnp REFUSES numpy scalars, and these values "
      "come out of numpy.interp -- an uncast assignment kills the publishing process.")

  def test_EVERY_CARCONTROLLER_COPY_IS_GATED_ON_ANGLE_MODE(self):
    """Outside angle mode the gain schedule never runs, so an ungated copy publishes whatever the
    last angle-mode frame left behind. A stale value reads as a live one -- the same failure as a
    settings snapshot describing a car that never existed. Checked structurally because the offline
    harness only ever runs angle mode, so no fixture can distinguish stale from fresh."""
    src = _src("opendbc_repo/opendbc/car/ford/carcontroller.py")
    for line in src.splitlines():
      stripped = line.strip()
      for f in FIELDS:
        if stripped.startswith(f"self.{f} = "):
          assert "_angle_mode" in stripped, (
            f"self.{f} is copied without the _angle_mode gate, so it keeps its last angle-mode "
            f"value in curvature mode: {stripped}")

  def test_the_lane_trim_contribution_is_measured_as_a_DELTA(self):
    """It is 0.0 on the fixture road (no model, so the trim resets), so no offline drive can catch
    its capture being deleted. Reading the trim's own `correction` property instead would go stale
    the moment that class gains an early return -- the delta cannot."""
    src = _src("opendbc_repo/opendbc/sunnypilot/car/ford/lateral_angle_ext.py")
    assert "self.bp_lane_center_correction = float(kappa_cmd - _kappa_before_trim)" in src, (
      "the lane-centering contribution is no longer captured as the delta across the trim call -- "
      "it is the only measurement of the one closed position loop in the lateral stack")

  def test_the_publisher_reads_them_off_the_car_controller(self):
    """Guards the source, not just the assignment: `cs_bp.curvatureFactor = 0.0` would satisfy the
    test above while publishing a constant."""
    src = _src("bluepilot/selfdrive/car/bp_card_publisher.py")
    for f in FIELDS:
      assert f'"{f}"' in src or f"'{f}'" in src, (
        f"{f} is assigned but not read from CI.CC -- it would publish a constant")


class TestItSurvivesARealDrive:
  """The 2026-08-15 category: a wiring mistake between two objects that no pure-logic test sees."""

  @staticmethod
  def _drive(parts, *, lat_active=True, frames=60):
    CarController, dbc_names, CP, CP_SP, structs = parts
    from cereal import car as capnp_car
    cc = CarController(dbc_names, CP, CP_SP)
    CC, CC_SP = _car_control(structs, enabled=lat_active, send_button="none", gap_target=3)
    out = capnp_car.CarState.new_message()
    out.vEgo = 30.0
    out.steeringAngleDeg = 2.0
    CS = FakeCarState(out.as_reader())
    for i in range(frames):
      cc.update(CC, CC_SP, CS, i * 10_000_000)
    return cc

  def test_the_telemetry_attributes_exist_after_update(self, carcontroller_parts):  # noqa: F811
    """If CarController.update never sets them, the publisher's getattr default hides it forever --
    it would publish 0.0 on every frame of every drive and look like a working feature."""
    cc = self._drive(carcontroller_parts)
    for f in FIELDS:
      assert hasattr(cc, f), f"CarController.update never set {f}"
      assert isinstance(getattr(cc, f), float), f"{f} is not a plain float; capnp will refuse it"

  def test_THE_GAIN_FIELDS_ARE_ACTUALLY_POPULATED_NOT_JUST_PRESENT(self, carcontroller_parts):  # noqa: F811
    """`hasattr` and a 0.0 look identical, which is the ENTIRE failure mode this file exists for --
    a value that reaches the wire as a constant zero on every frame of every drive reads exactly
    like a working feature. Deleting the capture in lateral_angle_ext survived mutation testing
    until this test existed.

    The harness runs ANGLE mode (`primary_lateral_control == 1`, `disable_BP_lat_UI` False), so the
    gain schedule genuinely evaluates; measured here it produces 1.0 / 1.0 / 1.1856 / 0.175.
    kappaCmd, pathAngleFinal and laneCenterCorrection are legitimately 0 on a straight fixture road
    and are checked structurally instead."""
    cc = self._drive(carcontroller_parts)
    for f in ("curvatureFactor", "gainLowCurv", "gainHighCurv", "blendWeight"):
      assert getattr(cc, f) != 0.0, (
        f"{f} published as a constant zero -- the gain schedule ran but its value never reached "
        "the CarController, so a route still cannot say what authority the car had")
    assert cc.gainHighCurv != cc.gainLowCurv, (
      "both ramp anchors are the same value -- one of them is not being captured from its own "
      "variable, and the ramp cannot be reconstructed from the wire")

  def test_EACH_PUBLISHED_FIELD_EQUALS_THE_CONTROLLERS_OWN_LIVE_VARIABLE(self, carcontroller_parts):  # noqa: F811
    """The whole chain, by VALUE, in one assertion per field.

    A non-zero check is not enough on its own: deleting the blend-weight capture leaves it at the
    __init__ seed of 0.5, which is non-zero and passed. Comparing against the live variable catches
    a capture that never runs, a capture reading the wrong variable, and a carcontroller hop that
    drops one -- because every one of those makes the two disagree.

    `LateralAngleExt` is called CLASS-STYLE with the CarController as `self` (see CLAUDE.md), so
    its working state is on `cc` and is directly comparable."""
    cc = self._drive(carcontroller_parts)
    for published, source in (
      ("pathAngleFinal", "bp_path_angle_final"),
      ("kappaCmd", "bp_kappa_cmd"),
      ("curvatureFactor", "bp_curvature_factor"),
      ("laneCenterCorrection", "bp_lane_center_correction"),
      ("gainLowCurv", "bp_gain_low_curv"),
      ("gainHighCurv", "bp_gain_high_curv"),
      ("blendWeight", "bp_blend_weight"),
    ):
      assert getattr(cc, published) == pytest.approx(float(getattr(cc, source))), (
        f"{published} does not match {source} -- the carcontroller hop dropped it")

    # and the captures themselves read the variable they claim to
    assert cc.bp_curvature_factor == pytest.approx(float(cc.curvature_factor))
    assert cc.bp_gain_low_curv == pytest.approx(float(cc.low_gain_calc))
    assert cc.bp_gain_high_curv == pytest.approx(float(cc.high_gain_calc))
    assert cc.bp_blend_weight == pytest.approx(float(cc.b_blend)), (
      "blendWeight is not tracking the live ramp -- deleting its capture leaves the __init__ seed, "
      "which is non-zero and therefore invisible to a plausibility check")

  def test_they_are_reset_rather_than_left_STALE_when_lateral_is_inactive(self, carcontroller_parts):  # noqa: F811
    """A held-over value reads as a live one. That is how a settings snapshot once described a car
    that never existed, and it is why b_blend is reset at the same bail-out sites.

    blendWeight is the ONE that must not go to zero: `b_blend` is ramped, persistent state, and its
    documented requirement is that a bail-out reseeds it to the DEFAULT so a re-engage cannot
    inherit the last drive's weight. Zero would be a weight the controller never uses. Asserting 0.0
    here failed against correct code -- the fixture was wrong, not the car."""
    from opendbc.sunnypilot.car.ford.lateral_angle_ext import _FORD_PATH_ANGLE_BLEND_RATIO_DEFAULT
    cc = self._drive(carcontroller_parts, lat_active=False)
    for f in FIELDS:
      if f == "blendWeight":
        assert getattr(cc, f) == pytest.approx(_FORD_PATH_ANGLE_BLEND_RATIO_DEFAULT), (
          "blendWeight must reseed to the blend default on bail-out, not to zero")
      else:
        assert getattr(cc, f) == 0.0, f"{f} kept a stale value while lateral was inactive"


class TestTheOrdinals:
  """capnp reads by POSITION and does not raise on a bad numbering space -- it calls abort(), which
  kills the interpreter with no Python-level traceback. Cheap to check, fatal to get wrong."""

  @staticmethod
  def _controller_state_bp_ordinals():
    import re
    src = _src("cereal/custom.capnp")
    i = src.index("struct ControllerStateBP")
    depth, lines = 0, []
    for line in src[i:].splitlines():
      lines.append(line)
      depth += line.count("{") - line.count("}")
      if depth == 0 and len(lines) > 1:
        break
    depth, tops = 0, []
    for line in lines:
      before = depth
      depth += line.count("{") - line.count("}")
      m = re.search(r"^\s*(\w+)\s*@(\d+)\s*:", line)
      if m and before == 1:
        tops.append((int(m.group(2)), m.group(1)))
    return sorted(tops)

  def test_they_are_contiguous_from_zero(self):
    tops = self._controller_state_bp_ordinals()
    nums = [n for n, _ in tops]
    assert nums == list(range(len(nums))), (
      f"ControllerStateBP ordinals are not contiguous: {nums}. capnp abort()s on a gap, taking the "
      "whole process with it and naming pytest in the traceback rather than the schema.")

  def test_no_ordinal_is_used_twice(self):
    tops = self._controller_state_bp_ordinals()
    nums = [n for n, _ in tops]
    assert len(nums) == len(set(nums)), f"duplicate ordinal in ControllerStateBP: {tops}"

  def test_the_new_fields_sit_above_everything_that_has_wire_history(self):
    """They were appended at @55+, which is free on passing-assist-phase1 and radar-detector (both
    end at @54). `route-intent` has its own @55 and must renumber at rebase -- ITS field is the one
    with no recorded history on the device, and that branch is not what the car runs."""
    tops = dict((f, n) for n, f in self._controller_state_bp_ordinals())
    for f in FIELDS:
      assert tops.get(f, -1) >= 55, (
        f"{f} took ordinal {tops.get(f)} -- anything below @55 collides with a field that is "
        "already recorded in routes on the device, which decodes every past drive as garbage")
