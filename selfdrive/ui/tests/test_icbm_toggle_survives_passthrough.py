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


def test_who_is_driving_is_published_not_inferred():
  """Two attempts at inferring this from the numbers were wrong, and a third could not have worked.

  The pill compared `carOutput.actuatorsOutput.accel` against the camera's `accAccelRequest`. That
  is an honest test of WHETHER they diverge and carries nothing about WHY -- and the two reasons are
  opposites. `opStop` is the override doing its job for a few seconds. `inert` is the camera having
  latched cancel, with openpilot longitudinal driving for the rest of the drive; drive A sat in that
  state for 262 s. Identical on the wire. No gate on the comparison could separate them, because the
  information is not in the numbers -- it is in the carcontroller, which decided it.

  So this asserts the CHAIN exists end to end: the controller sets it, the publisher carries it, the
  renderer reads it. A field set and never published is the failure this fork has already had three
  times over."""
  import pathlib
  root = pathlib.Path(__file__).resolve().parents[3]
  cc = (root / "opendbc_repo" / "opendbc" / "car" / "ford" / "carcontroller.py").read_text(encoding="utf-8")
  pub = (root / "bluepilot" / "selfdrive" / "car" / "bp_card_publisher.py").read_text(encoding="utf-8")
  hud = (root / "selfdrive" / "ui" / "bp" / "onroad" / "hud_renderer_bp.py").read_text(encoding="utf-8")

  # PARSED, NOT GREPPED, since 2026-08-22. These were three substring checks for
  # `self.acc_authority = _AA.<name>`, and adding a ternary -- `_AA.recovery if clear_cancel else
  # _AA.ford` -- broke the `ford` one while the controller went on reporting `ford` perfectly well.
  # A test that fails on the SHAPE of an assignment rather than on what is assigned is a test that
  # has to be edited every time the code is, which is how a real check gets weakened into a
  # rubber stamp. This walks every assignment to `acc_authority` and collects the enumerants,
  # however they are spelled.
  import ast
  reported = set()
  for node in ast.walk(ast.parse(cc)):
    if not isinstance(node, ast.Assign):
      continue
    if not any(isinstance(t, ast.Attribute) and t.attr == "acc_authority" for t in node.targets):
      continue
    for sub in ast.walk(node.value):
      if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) and sub.value.id == "_AA":
        reported.add(sub.attr)
  assert "opStop" in reported, "the controller never reports the override"
  assert "inert" in reported, "the controller never reports a latched cancel"
  assert "ford" in reported, "the controller never reports the normal state"
  assert "recovery" in reported, (
    "a masked cancel recovery is reported as plain `ford`, so every offline tool counts those "
    "frames as clean Ford authorship -- the denominator mistake this fork has made three times")
  assert "cs_bp.accAuthority" in pub, (
    "the controller sets acc_authority and the publisher does not carry it -- the field is dead")
  assert "AccAuthority.inert" in hud and '"ACC LOST"' in hud, (
    "nothing on screen says the passthrough went inert, which is the state that does not recover")
  assert "AccAuthority.opStop" in hud and '"OP STOP"' in hud
  assert "AccAuthority.fallback" in hud and '"OP LONG"' in hud, (
    "openpilot longitudinal driving unasked is not shown, so he cannot tell it from Ford driving")

  # The inference must be GONE, not merely unused. Leaving it next to the published value is how a
  # later edit reaches for the wrong one.
  assert "abs(ours - bls.accAccelRequest)" not in hud, (
    "the old accel comparison is still in the renderer alongside the published authority")


def test_the_inert_state_is_not_debounced_away():
  """`inert` is already five seconds of latched cancel by the time the controller publishes it, and
  it never clears. Running it through the fallback debounce would delay the one readout that has to
  interrupt him, and would do it for no reason at all."""
  import pathlib
  hud = (pathlib.Path(__file__).resolve().parents[3] / "selfdrive" / "ui" / "bp" / "onroad" /
         "hud_renderer_bp.py").read_text(encoding="utf-8")
  i = hud.index("AccAuthority.inert")
  branch = hud[i:hud.index("elif", i + 10)] if "elif" in hud[i:] else hud[i:]
  assert '"ACC LOST"' in branch, "the inert branch does not set the readout"
  assert ">= OP_AUTHORING_FRAMES" not in branch, (
    "the inert readout is debounced, delaying the one state that cannot recover")
