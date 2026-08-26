"""FusionPilot: the SCC-Map vetoes must publish their own INPUTS, not just their verdict.

His report, 2026-08-25: *"It also decided to drop down to 20 for no reason with no warning."*
`bp_icbm_steps.py` attributed it to SCC-Map -- five of seven floor episodes across routes
000003c0/c2 demanded a corner 8-23x tighter than the road the car then drove -- and then could go no
further, because the two numbers the camera vetoes actually compare, `target_distance` and
`model_lat_acc`, had never been on the wire. Four drives, an attributable cause, and no way to say
which gate declined.

That is the third instance of one mistake in this repo, and it already has a written rule:

    PUBLISHING A DIAGNOSTIC IS NOT A ONE-TIME ACT. A diagnostic is a property of the RULE, not of
    the module. When a comparison is rewritten, re-check that its new terms reach the wire.

and, from the cancel-recovery episode:

    When a rule cannot be explained from a drive, add the log line rather than a third inference.

So these tests protect the WIRING, and one thing more: that the two vetoes stay TELLABLE APART. They
were merged with `or`, which short-circuits, so `_camera_has_not_seen_it` was not even evaluated
whenever `_model_disagrees` was true -- a drive could not have distinguished them at any cost.
"""
import ast
import inspect
import math

import pytest

from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control import map_controller as mc


def _update_src():
  return inspect.getsource(mc.SmartCruiseControlMap.update)


def _repo_root():
  import pathlib
  here = pathlib.Path(mc.__file__).resolve()
  return next(p for p in here.parents if (p / "cereal/custom.capnp").exists())


def _planner_src():
  """READ AS TEXT, NOT IMPORTED. `longitudinal_planner` pulls in `system.hardware.hw` through
  sunnypilot's model helpers, which does not exist offline -- the same reason CLAUDE.md records that
  nothing offline imports plannerd, and the reason a green suite once sat on a file full of conflict
  markers. Reading the file is what makes this checkable at all."""
  p = _repo_root() / "sunnypilot/selfdrive/controls/lib/longitudinal_planner.py"
  return p.read_text(encoding="utf-8")


class TestTheVetoesAreTellableApart:
  def test_they_are_evaluated_separately_not_short_circuited(self):
    """`a or b` never evaluates b when a is true. Two vetoes behind one `or` cannot be told apart
    from a log at any cost, which is exactly the state that made the report undiagnosable."""
    src = _update_src()
    assert "self.camera_not_seen = bool(" in src, \
      "camera_not_seen is not evaluated on its own -- the two vetoes are merged again"
    tree = ast.parse(src.lstrip())
    for node in ast.walk(tree):
      if isinstance(node, ast.Assign) and any(
          isinstance(t, ast.Attribute) and t.attr == "model_vetoed" for t in node.targets):
        expr = ast.unparse(node.value)
        assert "_camera_has_not_seen_it" not in expr, (
          f"model_vetoed = {expr} -- calling the second veto inside the OR short-circuits it")
        return
    raise AssertionError("no assignment to self.model_vetoed found in update()")

  def test_both_flags_exist_before_any_frame_runs(self):
    """A missing attribute in this layer does not disable a diagnostic, it raises out of the
    planner. Same category as the 2026-08-15 carcontroller crash."""
    m = mc.SmartCruiseControlMap.__new__(mc.SmartCruiseControlMap)
    src = inspect.getsource(mc.SmartCruiseControlMap.__init__)
    for name in ("model_vetoed", "camera_not_seen", "model_lat_acc", "target_distance"):
      assert f"self.{name}" in src, f"{name} is not initialised in __init__"
    del m


class TestTheyReachTheWire:
  def test_the_planner_publishes_all_four(self):
    src = _planner_src()
    for field, attr in (("targetDistance", "target_distance"),
                        ("modelLatAcc", "model_lat_acc"),
                        ("modelVetoed", "model_vetoed"),
                        ("cameraNotSeen", "camera_not_seen")):
      assert f"sccMap.{field}" in src, f"{field} is never published"
      assert attr in src, f"{field} is published but never fed from {attr}"

  def test_the_capnp_struct_carries_them(self):
    import pathlib
    here = pathlib.Path(mc.__file__).resolve()
    root = next(p for p in here.parents if (p / "cereal/custom.capnp").exists())
    text = (root / "cereal/custom.capnp").read_text(encoding="utf-8")
    block = text[text.index("struct Map {"):]
    block = block[:block.index("\n    }")]
    for f in ("targetDistance", "modelLatAcc", "modelVetoed", "cameraNotSeen"):
      assert f in block, f"{f} is not declared on SmartCruiseControl.Map"

  def test_the_ordinals_are_contiguous_and_unique(self):
    """capnp does not raise on a bad numbering space -- it calls abort(), killing the interpreter
    with no Python-level exception. This repo has already lost a suite to that."""
    import pathlib
    import re
    here = pathlib.Path(mc.__file__).resolve()
    root = next(p for p in here.parents if (p / "cereal/custom.capnp").exists())
    text = (root / "cereal/custom.capnp").read_text(encoding="utf-8")
    block = text[text.index("struct Map {"):]
    block = block[:block.index("\n    }")]
    nums = [int(n) for n in re.findall(r"@(\d+)\s*:", block)]
    assert nums == list(range(len(nums))), f"Map ordinals are {nums}, must be contiguous from 0"


class TestInfinityCannotReadAsZeroDistance:
  def test_no_corner_publishes_zero_not_infinity(self):
    """`target_distance` initialises to inf, which capnp cannot carry meaningfully. It goes out as
    0.0 -- and **0 must read as NO CORNER, never as "the corner is right here"**, which is the one
    interpretation that would make this field dangerous rather than merely absent. `slowDownEndpoint`
    needed the identical guard for the identical reason."""
    src = _planner_src()
    assert "math.isfinite(td)" in src, \
      "target_distance goes to capnp without an isfinite guard; inf will not survive the wire"

  def test_the_controller_really_does_start_at_infinity(self):
    """The guard above is only load-bearing because of this. If the sentinel ever became a large
    finite number the guard would silently stop mattering and a stale distance would publish."""
    src = inspect.getsource(mc.SmartCruiseControlMap.__init__)
    assert "self.target_distance = float('inf')" in src or \
           'self.target_distance = float("inf")' in src


class TestTheGatesThemselvesAreUnchanged:
  """The vetoes' BEHAVIOUR is deliberately not touched by this commit. Each was bought with a
  measured event on his roads, and changing a gate and its instrumentation together produces a
  drive that cannot say which half moved."""

  def test_the_horizon_split_still_keys_on_the_one_definition(self):
    src = inspect.getsource(mc.SmartCruiseControlMap._model_disagrees)
    assert "_MAP_FACTOR_V_BP[1]" in src, \
      "the ramp/highway line was duplicated instead of referenced; the four defenses can now drift"

  def test_camera_has_not_seen_it_is_still_highway_only(self):
    """Not an endorsement -- this is the gate the 2026-08-25 measurement suggests is the gap. It is
    pinned so that changing it is a deliberate act with its own drive, not a side effect."""
    src = inspect.getsource(mc.SmartCruiseControlMap._camera_has_not_seen_it)
    assert "self.v_target >= _MAP_FACTOR_V_BP[1]" in src

  @pytest.mark.parametrize("attr,val", [("MODEL_DISAGREE_LAT_ACC", 0.4)])
  def test_the_veto_threshold_is_where_the_measurement_left_it(self, attr, val):
    assert math.isclose(getattr(mc, attr), val)
