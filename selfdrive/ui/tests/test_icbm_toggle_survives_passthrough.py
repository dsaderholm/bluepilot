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
