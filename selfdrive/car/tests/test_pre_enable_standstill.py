"""BluePilot: don't ask for the brake to be released on a car that resumes from a stop by itself.

preEnableStandstill holds openpilot in State.preEnabled showing "Release Brake to Engage". That is
right for a car openpilot cannot engage from a standstill, and wrong for Ford, whose stock ACC
resumes with the brake still held.

It also caused an IMMEDIATE_DISABLE, confirmed from a route on 2026-08-06 rather than reasoned:

  t+738.74  sdEn=True sdActive=False  ev=preEnableStandstill  cA=False
  ...       (held for the full window while the brake stayed down)
  t+748.89  controlsMismatch

preEnabled counts as ENABLED but panda has no reason to allow controls there, and selfdrived raises
controlsMismatch after 200 frames of enabled-without-allowed. Most cars leave preEnabled in well
under two seconds; a driver stopped at a red light does not.

This asserts the gate directly rather than through CarSpecificEvents, which needs the platform
registry and a full CarParams. The condition is three terms and the point is that the third one is
present at all.
"""
import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[3]
SRC = REPO / "selfdrive" / "car" / "car_specific.py"


def _guard() -> ast.If:
  """The `if ...: events.add(EventName.preEnableStandstill)` statement."""
  tree = ast.parse(SRC.read_text(encoding="utf-8"), filename=str(SRC))
  for node in ast.walk(tree):
    if not isinstance(node, ast.If):
      continue
    for stmt in node.body:
      if (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
          and isinstance(stmt.value.func, ast.Attribute) and stmt.value.func.attr == "add"
          and any(isinstance(a, ast.Attribute) and a.attr == "preEnableStandstill"
                  for a in stmt.value.args)):
        return node
  raise AssertionError("preEnableStandstill is no longer raised from car_specific.py")


def test_it_is_gated_on_auto_resume_sng():
  """Without this the alert returns and takes controlsMismatch with it."""
  src = ast.unparse(_guard().test)
  assert "autoResumeSng" in src, (
    f"preEnableStandstill is raised on `{src}` with no autoResumeSng gate. On a car that resumes "
    "from a stop by itself this holds openpilot in preEnabled for as long as the brake is held, "
    "which is both the wrong prompt and an IMMEDIATE_DISABLE two seconds later.")


def test_the_gate_is_negated():
  """`autoResumeSng` rather than `not autoResumeSng` would invert it -- firing on exactly the cars
  that should never see it, which reads as correct at a glance."""
  test = _guard().test
  negated = any(isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not)
                and "autoResumeSng" in ast.unparse(n)
                for n in ast.walk(test))
  assert negated, f"gate is `{ast.unparse(test)}` -- autoResumeSng must be NEGATED"


def test_the_original_conditions_survive():
  """The gate narrows the event; it must not replace what it was keyed on."""
  src = ast.unparse(_guard().test)
  for term in ("brakePressed", "standstill"):
    assert term in src, f"{term} was dropped from the preEnableStandstill condition"
