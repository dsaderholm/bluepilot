"""The map path must be rebuilt only when its message changes.

This is a fix for a SOFT DISABLE, not an optimisation. `path_from_mapd` walks every point of
mapdExtendedOut and allocates a dict per point. It ran on every plannerd frame (20 Hz) against a
message that publishes at ~1 Hz, so it redid identical work nineteen times per message.

Measured against real path sizes, on a desktop CPU faster than the device's:

    0 points (straight)     0.001 ms
    285 points              7.6   ms
    652 points (mountain)  17.6   ms      <-- against a 50 ms frame budget

plannerd polls carState at 100 Hz, so it must service that socket every 10 ms. Stalling inside this
call starves it; carState drops under the 80 Hz floor of its frequency band as plannerd sees it; and
every all_checks in that process fails at once. That invalidates longitudinalPlan, longitudinalPlanSP
and driverAssistance together -- carState is the only service common to all three check lists -- and
selfdrived turns that into commIssue, which is ET.SOFT_DISABLE.

Measured on his device across a 2,000 mile trip: 96 of 103 commIssue events had exactly those three
invalid simultaneously, with alive and freq passing on everything else.
"""
import ast

SRC = "sunnypilot/selfdrive/controls/lib/smart_cruise_control/smart_cruise_control.py"


def _update_fn():
  tree = ast.parse(open(SRC, encoding="utf-8").read())
  for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "update":
      return node
  raise AssertionError("SmartCruiseControl.update not found")


def _calls_to(node, name):
  return [n for n in ast.walk(node)
          if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", None)) == name]


def test_path_from_mapd_is_not_called_unconditionally():
  """The bug: one bare call per frame. It must sit behind an `updated` guard."""
  upd = _update_fn()
  calls = _calls_to(upd, "path_from_mapd")
  assert calls, "path_from_mapd is no longer called at all -- v2 curve source is gone"
  # Every call must be nested inside an If, not a direct statement of the function body.
  for st in upd.body:
    if isinstance(st, (ast.Assign, ast.Expr)) and _calls_to(st, "path_from_mapd"):
      raise AssertionError("path_from_mapd is called unconditionally every frame -- this is the "
                           "17.6 ms stall that soft-disabled him")


def test_the_rebuild_is_gated_on_the_message_updating():
  src = open(SRC, encoding="utf-8").read()
  tree = ast.parse(src)
  found = False
  for node in ast.walk(tree):
    if isinstance(node, ast.If) and _calls_to(node, "path_from_mapd"):
      test_src = ast.unparse(node.test)
      if "updated" in test_src and "mapdExtendedOut" in test_src:
        found = True
  assert found, "the rebuild must be guarded by sm.updated['mapdExtendedOut']"


def test_a_dead_publisher_clears_the_cache():
  """Without this, `updated` stays False forever and the last path is served indefinitely --
  turning a fallback-to-v1 into a stale answer, which is worse than the bug being fixed."""
  src = open(SRC, encoding="utf-8").read()
  tree = ast.parse(src)
  ok = False
  for node in ast.walk(tree):
    if not isinstance(node, ast.If):
      continue
    t = ast.unparse(node.test)
    if "alive" in t and "mapdExtendedOut" in t:
      # its body must assign None to the cache
      for n in ast.walk(node):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and n.value.value is None:
          ok = True
  assert ok, "a not-alive mapdExtendedOut must clear the cached path, not keep serving it"
