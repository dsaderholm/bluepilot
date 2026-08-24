"""FusionPilot: every way the override can refuse must say which gate refused.

Measured from his own swaglogs, 2026-08-24:

    6  stop override ON:  stopping for something the radar cannot see
    3  stop override off: stopping for something the radar cannot see   <- the ARM reason, on exit
    2  stop override off: model stopped asking
    1  stop override off: stopped

Three of six episodes could not say why they ended, because eight arming gates returned a bare
False and left `last_result` holding whatever the last arm had written.

That matters more than tidiness. Every override measured so far handed the car back while it was
still moving -- between 8 and 37 mph -- and "why does it release mid-approach" is the question
standing between this feature and stopping at a red light. The instrument could not answer it.
"""
import ast
from pathlib import Path

SRC = Path(__file__).parents[1] / "stop_override.py"


def _update_fn():
  tree = ast.parse(SRC.read_text(encoding="utf-8"))
  for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "update":
      return node
  raise AssertionError("update() not found in stop_override.py")


def _sets_reason(stmt) -> bool:
  """Does this statement record a reason -- either directly or via _end/_end_slowdown?"""
  for n in ast.walk(stmt):
    if isinstance(n, ast.Assign):
      for t in n.targets:
        if isinstance(t, ast.Attribute) and t.attr == "last_result":
          return True
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
      if n.func.attr in ("_end", "_end_slowdown"):
        return True
  return False


def test_every_refusal_records_which_gate_refused():
  """A bare `return False` leaves the previous arm's reason standing, and the log then reports the
  reason the override STARTED as the reason it ended."""
  fn = _update_fn()
  silent = []
  for node in ast.walk(fn):
    if not isinstance(node, (ast.If, ast.FunctionDef)):
      continue
    body = node.body if isinstance(node, ast.If) else node.body
    for i, stmt in enumerate(body):
      if not (isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Constant)
              and stmt.value.value is False):
        continue
      # `if self.spent: return False` is EXEMPT and must stay exempt. `spent` is the state after an
      # override ended, so `_end`'s message is the answer to "why did it end" -- labelling this gate
      # overwrites it one frame later. Adding a reason here broke two existing tests that read
      # last_result after the end, which is the behaviour worth keeping.
      if isinstance(node, ast.If) and isinstance(node.test, ast.Attribute) and node.test.attr == "spent":
        continue
      # a reason may be set by any earlier statement in this same block
      if not any(_sets_reason(s) for s in body[:i + 1]):
        silent.append(stmt.lineno)
  assert not silent, (
    "return False with no reason recorded at stop_override.py lines "
    + ", ".join(str(x) for x in sorted(silent))
    + " -- the log will report the previous ARM reason as the exit reason")


def test_the_reasons_are_distinct():
  """Two gates sharing a string cannot be told apart in a log, which is the whole point."""
  tree = ast.parse(SRC.read_text(encoding="utf-8"))
  reasons = []
  for n in ast.walk(tree):
    if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
      for t in n.targets:
        if isinstance(t, ast.Attribute) and t.attr == "last_result":
          reasons.append(n.value.value)
  assert len(reasons) >= 8, f"expected the arming gates to be labelled, found {len(reasons)}"
  dupes = {r for r in reasons if reasons.count(r) > 1}
  assert not dupes, f"gates share a reason string and cannot be distinguished: {dupes}"
