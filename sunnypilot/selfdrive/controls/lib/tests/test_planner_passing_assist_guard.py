"""FusionPilot: passing assist may never take plannerd down with it.

2026-10-05 review: `passing_assist.update()` and `.publish()` ran bare in LongitudinalPlannerSP, so any
exception in an observe-only feature killed plannerd and disengaged the car. Read with `ast` because
nothing offline constructs the planner (it needs CarParams, a live MPC and the model bundle).
"""
import ast
from pathlib import Path

PLANNER = Path(__file__).resolve().parents[1] / "longitudinal_planner.py"


def _calls_and_parents():
  tree = ast.parse(PLANNER.read_text(encoding="utf-8"))
  parents = {}
  for node in ast.walk(tree):
    for child in ast.iter_child_nodes(node):
      parents[child] = node
  calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
           and n.func.attr in ("update", "publish") and isinstance(n.func.value, ast.Attribute)
           and n.func.value.attr == "passing_assist"]
  return calls, parents


def _inside_try_that_latches(node, parents):
  while node in parents:
    node = parents[node]
    if isinstance(node, ast.Try):
      handler_src = "".join(ast.unparse(h) for h in node.handlers)
      return "passing_assist_failed = True" in handler_src
  return False


def test_every_detector_call_in_the_planner_is_guarded_and_latches():
  calls, parents = _calls_and_parents()
  assert {c.func.attr for c in calls} == {"update", "publish"}, "the planner's detector calls moved"
  for c in calls:
    assert _inside_try_that_latches(c, parents), (
      f"passing_assist.{c.func.attr}() at line {c.lineno} can raise straight out of plannerd")


def test_a_latched_failure_requests_no_gap():
  src = PLANNER.read_text(encoding="utf-8")
  assert "accGapRequest = 0 if self.passing_assist_failed" in src, \
    "a dead detector must not keep asking ICBM for gap 1"
