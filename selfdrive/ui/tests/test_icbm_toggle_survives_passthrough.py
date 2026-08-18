"""FusionPilot: the settings screen is a THIRD gate on ICBM, and it deletes the param.

`interfaces.py` has two gates that decide whether ICBM runs. `cruise.py` has one that decides
whether he can see the toggle -- and it does not merely disable it, it calls
`params.remove("IntelligentCruiseButtonManagement")` on every render of the page.

So with openpilot longitudinal on, opening settings deleted the setting. That is why re-enabling
ICBM mid-drive on 2026-08-18 did not stick, why the device read `unset` afterwards, and why he saw
"ICBM was grayed out". Fixing the two gates in interfaces.py was not enough, and only the screen
said so.

Static, because the page needs raylib and cannot be rendered offline -- but it asserts the SHAPE
that was wrong: the branch condition must consult the passthrough, not `has_long` alone.
"""
from __future__ import annotations

import ast
import pathlib

PAGE = pathlib.Path(__file__).resolve().parents[3] / "selfdrive" / "ui" / "sunnypilot" / "layouts" / "settings" / "cruise.py"


def _source() -> str:
  return PAGE.read_text(encoding="utf-8")


def test_the_toggle_gate_consults_the_passthrough_and_not_op_long_alone():
  src = _source()
  assert "StockAccPassthrough" in src, "the ICBM toggle gate does not know the passthrough exists"
  assert "op_long_drives" in src, (
    "the gate still keys on has_long alone -- under the passthrough Ford authors the command, "
    "the set speed still governs, and ICBM is meaningful")


def test_the_param_removal_is_reachable_only_when_op_long_actually_drives():
  """The removal is the damaging half. It must sit under the passthrough-aware condition."""
  tree = ast.parse(_source())
  removals = []
  for node in ast.walk(tree):
    if not isinstance(node, ast.Call):
      continue
    f = node.func
    if isinstance(f, ast.Attribute) and f.attr == "remove" and node.args:
      arg = node.args[0]
      if isinstance(arg, ast.Constant) and arg.value == "IntelligentCruiseButtonManagement":
        removals.append(node.lineno)
  assert removals, "the param removal vanished -- if that is deliberate, delete this test with it"

  # The nearest enclosing `if` must test op_long_drives, not has_long.
  src_lines = _source().split("\n")
  for lineno in removals:
    guard = None
    for i in range(lineno - 1, max(0, lineno - 30), -1):
      line = src_lines[i - 1].strip()
      if line.startswith("if ") and "intelligentCruiseButtonManagementAvailable" in line:
        guard = line
        break
    assert guard is not None, f"could not find the guard above the removal at line {lineno}"
    assert "op_long_drives" in guard, (
      f"the removal at line {lineno} is guarded by `{guard}` -- it deletes his ICBM setting "
      "whenever op long is on, including under the passthrough where ICBM still works")


def test_the_stop_override_toggle_needs_the_passthrough():
  """The passthrough toggle was gated on op long so a useless state would be unreachable; its
  dependent was left ungated, which is half a fix.

  With the passthrough OFF, openpilot already authors every ACCDATA frame, so the override selects a
  command that was going out anyway -- the toggle changes nothing at all, and there is no way to
  tell that from the override failing to arm. Caught in review, 2026-08-18."""
  src = _source()
  assert "stock_acc_stop_override.action_item.set_enabled" in src, (
    "the stop override toggle is never enabled or disabled, so it is always reachable")
  # The passthrough must be in THIS call's own argument, not merely somewhere nearby. The first
  # version of this assertion took a 400-character window and passed against a mutant that dropped
  # the term entirely -- the description branch below it mentions `passthrough_on` too, so the
  # window found the word without the gate existing. Read to the closing paren instead.
  i = src.index("stock_acc_stop_override.action_item.set_enabled")
  expr = src[i:src.index("))", i) + 2]
  assert "passthrough_on" in expr or "StockAccPassthrough" in expr, (
    "the stop override toggle does not check the passthrough, without which it does nothing")


def test_the_op_stop_readout_is_gated_on_openpilot_longitudinal():
  """`self.accel` on the carcontroller is assigned ONLY inside the op-long ACCDATA block, so with op
  long off it is 0.0 for the whole drive while accAccelRequest carries Ford's real brake total.
  Ungated, every ordinary ACC brake application past the deadband read as OP STOP -- on the mode he
  drives nearly all the time."""
  import pathlib
  hud = (pathlib.Path(__file__).resolve().parents[3] / "selfdrive" / "ui" / "bp" / "onroad" /
         "hud_renderer_bp.py").read_text(encoding="utf-8")
  i = hud.index("OP_AUTHORING_DELTA")
  # the comparison itself, not the constant definition
  cmp_site = hud[hud.index("abs(ours - bls.accAccelRequest)") - 200:][:300]
  assert "has_longitudinal_control" in cmp_site, (
    "the OP STOP comparison does not check that openpilot longitudinal is on, so it fires whenever "
    "Ford brakes with op long off")
  # AND the passthrough, which is a SEPARATE precondition rather than a stronger version of the
  # same one. With op long on and the passthrough off, `self.accel` is openpilot's own number while
  # the camera computes its own independently -- they disagree past the deadband through every
  # brake application, so plain alpha long painted OP STOP constantly. That is not noise, it is the
  # pill claiming openpilot took the command away from Ford in the one configuration where Ford
  # never had it. Neither condition implies the other: the param outlives op long being switched
  # off, because the settings toggle greys out without clearing.
  assert "_stock_acc_passthrough" in cmp_site or "StockAccPassthrough" in cmp_site, (
    "the OP STOP comparison does not check the passthrough, so it fires through every brake "
    "application under plain alpha long")
  assert i > 0
