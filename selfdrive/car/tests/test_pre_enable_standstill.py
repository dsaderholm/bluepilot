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


def test_the_gate_is_actually_OPEN_on_his_car():
  """The gate existing is not the same as the gate being open, and only one of those was checked.

  The fix above only helps if `autoResumeSng` is TRUE on a 2020 Fusion. Ford derives it rather than
  setting it outright -- `ret.autoResumeSng = ret.minEnableSpeed == -1.` -- so it depends on two
  facts in two different files, neither of which the AST test above can see:

    interfaces.py   minEnableSpeed defaults to -1 ("enable is done by stock ACC, so ignore this")
    ford/interface.py  overrides it to 20 mph ONLY on the manual-transmission branch

  His car is an automatic, so the default survives and autoResumeSng is True. If upstream ever
  changes either link, preEnableStandstill starts firing at every red light again and the shape
  test still passes -- it checks the condition's form, not whether it can ever be false here.
  """
  import pathlib
  root = pathlib.Path(__file__).resolve()
  while not (root / "common" / "params_keys.h").exists():
    root = root.parent

  base = (root / "opendbc_repo" / "opendbc" / "car" / "interfaces.py").read_text(encoding="utf-8")
  assert "ret.minEnableSpeed = -1." in base, (
    "minEnableSpeed no longer defaults to -1, so autoResumeSng may be False on Ford and "
    "'Release Brake to Engage' would come back at every stop")

  ford = (root / "opendbc_repo" / "opendbc" / "car" / "ford" / "interface.py").read_text(encoding="utf-8")
  assert "ret.autoResumeSng = ret.minEnableSpeed == -1." in ford, (
    "Ford no longer derives autoResumeSng from minEnableSpeed -- re-check that it is True for an "
    "automatic before trusting the preEnableStandstill gate")

  # The 20 mph override must stay on the MANUAL branch. If it ever moves out from under
  # `transmissionType = manual`, an automatic gets a non-negative minEnableSpeed and the gate shuts.
  manual_block = ford.split("TransmissionType.manual", 1)[1].split("\n\n", 1)[0]
  assert "minEnableSpeed" in manual_block, (
    "the 20 mph minEnableSpeed override is no longer inside the manual-transmission branch")
