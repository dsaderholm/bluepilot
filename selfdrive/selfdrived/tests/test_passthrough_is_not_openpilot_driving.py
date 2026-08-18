"""FusionPilot: `openpilotLongitudinalControl` stopped meaning "openpilot is driving".

Under the stock-ACC passthrough that flag is True while Ford authors every command. Upstream uses it
as shorthand for "openpilot's own plan is driving the car", and every consumer that does is wrong in
this configuration. Two have bitten already, both in this file:

    FCW suppression      chimed while Ford braked normally for a lead   (found by audit)
    gap -> personality   cycled a setting that steers a discarded plan  (he reported it twice)

So this asserts on the SOURCE for every use of the flag in selfdrived, because the failure is not
that any one of them is wrong -- it is that the next one added will be wrong the same way.
"""
from __future__ import annotations

import ast
import pathlib

SRC = (pathlib.Path(__file__).resolve().parents[1] / "selfdrived.py").read_text(encoding="utf-8")


def _guards_of(target_attr):
  """Every `if` condition enclosing an assignment to `self.<target_attr>`.

  Resolved through the AST rather than by line proximity. The first version of this test measured
  distance from a COMMENT and passed against the exact mutant it was written for -- the third time
  in two days an assertion matched a label instead of the thing.
  """
  tree = ast.parse(SRC)
  for node in ast.walk(tree):
    for child in ast.iter_child_nodes(node):
      child.parent = node
  guards = []
  for node in ast.walk(tree):
    if not isinstance(node, ast.Assign):
      continue
    hit = any(isinstance(t, ast.Attribute) and t.attr == target_attr
              and isinstance(t.value, ast.Name) and t.value.id == "self" for t in node.targets)
    if not hit:
      continue
    cur = getattr(node, "parent", None)
    while cur is not None:
      if isinstance(cur, ast.If):
        guards.append(ast.unparse(cur.test))
      cur = getattr(cur, "parent", None)
  return guards


def test_the_gap_button_does_not_cycle_personality_under_the_passthrough():
  """His report, twice: "when I adjusted my gap, it said personality on the screen". With the
  passthrough on, openpilot's personality steers a plan that is thrown away, so the press should
  reach Ford's own follow distance instead of silently changing nothing."""
  guards = _guards_of("personality")
  assert guards, "nothing guards the personality write any more -- has it moved?"
  longitudinal = [g for g in guards if "openpilotLongitudinalControl" in g]
  assert longitudinal, "the personality write is no longer gated on longitudinal control at all"
  assert any("stock_acc_passthrough" in g for g in guards), (
    "the gap button still cycles openpilot's personality with the passthrough on, where that "
    f"setting drives nothing -- the press has to reach Ford's follow distance. guards: {guards}")


def test_the_fcw_suppression_still_knows_about_the_passthrough():
  """Guards the earlier fix in the same file, so a later edit cannot quietly undo it."""
  assert "stock_is_the_brake" in SRC, "the FCW suppression no longer asks who is braking"
  i = SRC.index("stock_is_the_brake =")
  assert "stock_acc_passthrough" in SRC[i:i + 200], (
    "stock_is_the_brake stopped consulting the passthrough, so the model FCW will chime while "
    "Ford brakes normally for a lead")
