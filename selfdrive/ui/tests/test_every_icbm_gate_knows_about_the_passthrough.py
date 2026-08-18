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
