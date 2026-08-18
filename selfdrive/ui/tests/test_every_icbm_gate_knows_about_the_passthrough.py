"""FusionPilot: no ICBM gate may key on openpilot longitudinal ALONE.

He reported it on 2026-08-18: "when I turn ICBM on, all its settings are grayed out" -- with the
passthrough on, which is the configuration where ICBM is supposed to work.

There were FOUR places deciding whether ICBM is available, and they were fixed one at a time as each
was found, which is exactly how the fourth survived:

    interfaces.py  _initialize_intelligent_cruise_button_management   fixed 2026-08-17
    interfaces.py  _cleanup_unsupported_params                        fixed 2026-08-17
    cruise.py      the settings page's own render gate                fixed 2026-08-18
    ui_state.py    _enforce_constraints                               MISSED until he reported it

The question every one of them has to ask is not "is openpilot longitudinal on" but "is openpilot
longitudinal DRIVING" -- under the stock-ACC passthrough openpilot carries Ford's command rather
than authoring one, the set speed still governs, and ICBM is still the thing that moves it.

And these do not merely disable: they `params.remove(...)`, so the settings page would light the
toggle and the next render would delete it. That is why re-enabling never stuck.

So this test DISCOVERS the gates rather than listing them. A fifth one added in a new file is
covered without anyone remembering this file exists -- which is the specific failure it exists to
prevent.
"""
from __future__ import annotations

import ast
import pathlib

PARAM = "IntelligentCruiseButtonManagement"
REPO = pathlib.Path(__file__).resolve().parents[3]
# Where a gate could plausibly live. Not the whole repo: opendbc's vendored tree and third_party are
# not ours, and scanning them would make this slow and noisy.
ROOTS = ("selfdrive", "sunnypilot", "bluepilot")
# Terms that mean the passthrough was actually CONSULTED. Deliberately only two, and the local
# variable name `op_long_drives` is deliberately NOT among them: a name is not a value, and the
# first version of this list accepted it -- so rebinding it to bare `has_long` left the name intact
# and the test passed against the exact bug it was written for. That is three times in one night
# that an assertion matched a label instead of the thing, which is now its own rule.
#
#   StockAccPassthrough  the param read directly, after `_effective_condition` inlines the binding
#   _op_long_drives      the shared helper in interfaces.py, which has its own tests
PASSTHROUGH_TERMS = ("StockAccPassthrough", "_op_long_drives")
LONG_TERMS = ("has_long", "has_longitudinal_control", "openpilotLongitudinalControl",
              "AlphaLongitudinalEnabled")


def _sources():
  for root in ROOTS:
    for path in (REPO / root).rglob("*.py"):
      if "tests" in path.parts or "third_party" in path.parts:
        continue
      yield path


def _enclosing_function(node):
  cur = getattr(node, "parent", None)
  while cur is not None:
    if isinstance(cur, ast.FunctionDef | ast.AsyncFunctionDef | ast.Module):
      return cur
    cur = getattr(cur, "parent", None)
  return None


def _effective_condition(guard) -> str:
  """The guard's test with any local names it depends on inlined.

  Gates are written as `op_long_drives = has_long and not params.get_bool("StockAccPassthrough")`
  followed by `if ... or op_long_drives:`, so the unparsed test alone says only `op_long_drives` and
  proves nothing. This resolves one level of that binding within the enclosing function.

  Deliberately NOT a whole-file text scan. The first version of this test fell back to "does the
  word appear anywhere in the file", which passed against the exact bug it was written for -- the
  comment explaining the fix contained the word. Same fault as the 400-character window in
  test_icbm_toggle_survives_passthrough, which is twice now.
  """
  cond = ast.unparse(guard.test)
  fn = _enclosing_function(guard)
  if fn is None:
    return cond
  names = {n.id for n in ast.walk(guard.test) if isinstance(n, ast.Name)}
  for node in ast.walk(fn):
    if isinstance(node, ast.Assign):
      for t in node.targets:
        if isinstance(t, ast.Name) and t.id in names:
          cond += " ;; " + ast.unparse(node.value)
  return cond


def _removal_sites():
  """Every `params.remove("IntelligentCruiseButtonManagement")`, with its enclosing `if` test."""
  sites = []
  for path in _sources():
    try:
      text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
      continue
    if PARAM not in text:
      continue
    tree = ast.parse(text)
    # Parent links, so a removal can be walked back up to the condition that guards it.
    for node in ast.walk(tree):
      for child in ast.iter_child_nodes(node):
        child.parent = node
    for node in ast.walk(tree):
      if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr == "remove"):
        continue
      if not any(isinstance(a, ast.Constant) and a.value == PARAM for a in node.args):
        continue
      guard, cur = None, getattr(node, "parent", None)
      while cur is not None:
        if isinstance(cur, ast.If):
          guard = cur
          break
        cur = getattr(cur, "parent", None)
      sites.append((path, node.lineno, guard))
  return sites


def test_the_gates_exist_at_all():
  """If this goes to zero the test below is vacuous and would pass in silence."""
  assert len(_removal_sites()) >= 3, (
    "fewer ICBM removal sites than expected -- if they were consolidated, good, but check this "
    "test still points at something")


def test_no_icbm_gate_keys_on_openpilot_longitudinal_alone():
  """The bug he reported, generalised: any gate that clears ICBM because openpilot longitudinal is
  on must also ask whether the passthrough is on, because that is the case where openpilot is NOT
  the one driving."""
  offenders = []
  for path, lineno, guard in _removal_sites():
    if guard is None:
      continue                       # unconditional cleanup; not an op-long decision
    cond = _effective_condition(guard)
    if not any(t in cond for t in LONG_TERMS):
      continue                       # not gated on longitudinal at all -- nothing to check
    if not any(t in cond for t in PASSTHROUGH_TERMS):
      offenders.append(f"{path.relative_to(REPO)}:{lineno} -- if {cond}")
  assert not offenders, (
    "ICBM is cleared on openpilot longitudinal alone, so turning ICBM on under the stock-ACC "
    "passthrough deletes the param and greys out every setting under it:\n  " +
    "\n  ".join(offenders))


def test_a_missing_carparams_does_not_destroy_the_setting():
  """"Not known yet" is not "not supported". THE FIFTH GATE.

  `ui_state.CP_SP` is None until `CarParamsSPPersistent` has been read -- every UI start, before any
  car has been seen, and any frame that read is briefly unavailable. The `else` branch there deleted
  `IntelligentCruiseButtonManagement` outright, so the UI destroyed the setting on essentially every
  boot. `card` then read it as False at car init and never cleared `pcmCruiseSpeed`.

  Both of his 2026-08-18 complaints came out of that one flag:
    - `v_cruise` mirrors the dash instead of being openpilot's, so MAX and the ICBM number are the
      same number and there is no separate max speed to move
    - `pcm_op_long` goes True, so SLA runs the PCM machine that demands the set speed sit at
      `PCM_LONG_REQUIRED_MAX_SET_SPEED` -- the "set your speed to 70 for it to work"

  Verified on the device: the param file read `1` earlier in the session and was GONE afterwards.

  Removing a PERSISTENT param is not a way to express "I have no evidence". Report unavailable for
  display; leave the stored value alone."""
  src = (REPO / "selfdrive" / "ui" / "sunnypilot" / "ui_state.py").read_text(encoding="utf-8")
  tree = ast.parse(src)
  for node in ast.walk(tree):
    for child in ast.iter_child_nodes(node):
      child.parent = node

  for node in ast.walk(tree):
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "remove"):
      continue
    if not any(isinstance(a, ast.Constant) and a.value == PARAM for a in node.args):
      continue
    # Walk up: any enclosing `if` whose test is a CP_SP presence check means this removal fires on
    # missing evidence rather than on a real answer.
    cur = getattr(node, "parent", None)
    while cur is not None:
      if isinstance(cur, ast.If):
        cond = ast.unparse(cur.test)
        assert not ("CP_SP is None" in cond or "CP_SP is not None" in cond and _in_else(cur, node)), (
          "ICBM is deleted when CarParamsSP has not been read yet -- that is every UI start, so the "
          "setting is destroyed on boot and card never clears pcmCruiseSpeed")
      cur = getattr(cur, "parent", None)


def _in_else(if_node, target):
  """True when `target` sits in the `else` of `if_node`."""
  for n in if_node.orelse:
    for sub in ast.walk(n):
      if sub is target:
        return True
  return False


def test_no_persistent_setting_is_deleted_on_missing_carparams():
  """Generalizes the fifth gate: NOTHING in `_enforce_constraints` may delete a stored setting just
  because CarParams has not been read yet.

  The ICBM gate was one instance. Four lines below it, `if not (has_long or self.has_icbm)` removes
  three more PERSISTENT params -- `CustomAccIncrementsEnabled`, `SmartCruiseControlVision` and
  `SmartCruiseControlMap`, two of them his curve controllers -- and both terms go False when
  CarParams is unread.

  That one was MASKED on his car by load order (`CP` populates before `CP_SP`, so `has_long` is
  already True), which is a coincidence rather than a guarantee -- and the same ordering is exactly
  why the ICBM param DID die. So this asserts on the shape, for every removal in the function, not
  on the two known instances."""
  src = (REPO / "selfdrive" / "ui" / "sunnypilot" / "ui_state.py").read_text(encoding="utf-8")
  tree = ast.parse(src)
  for node in ast.walk(tree):
    for child in ast.iter_child_nodes(node):
      child.parent = node

  fn = next(n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_enforce_constraints")

  offenders = []
  for node in ast.walk(fn):
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "remove"):
      continue
    # Collect every enclosing condition, plus any local binding they depend on.
    conds, cur = [], getattr(node, "parent", None)
    while cur is not None and cur is not fn:
      if isinstance(cur, ast.If):
        conds.append(ast.unparse(cur.test))
      cur = getattr(cur, "parent", None)
    names = set()
    for c in conds:
      names |= {n.id for n in ast.walk(ast.parse(c, mode="eval")) if isinstance(n, ast.Name)}
    for n2 in ast.walk(fn):
      if isinstance(n2, ast.Assign):
        for t in n2.targets:
          if isinstance(t, ast.Name) and t.id in names:
            conds.append(ast.unparse(n2.value))
    joined = " ;; ".join(conds)
    # A removal is safe when SOMETHING in its guard chain establishes that CarParams was read.
    if not ("CP is not None" in joined or "CP_SP is not None" in joined):
      arg = next((a.value for a in node.args if isinstance(a, ast.Constant)), "?")
      offenders.append(f"line {node.lineno}: removes {arg!r} guarded only by [{joined}]")

  assert not offenders, (
    "a stored setting is deleted without establishing that CarParams was ever read, so it dies on "
    "any boot where the params are not loaded yet:\n  " + "\n  ".join(offenders))
