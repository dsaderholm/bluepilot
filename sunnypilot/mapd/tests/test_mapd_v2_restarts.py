"""FusionPilot: mapd_v2 must be restartable. Route 000003b4, 2026-08-24: 441 "not running:
mapd_v2" events in one drive, and only a reboot brought it back -- which the notes had recorded as
a property of the daemon. It was a missing keyword argument.

Speed Limit Assist reads `mapdOut` in state 2, so a dead mapd_v2 is SLA silently losing its speed
limit source mid-drive. On a 2,000 mile trip that is not an annoyance.
"""
import ast
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _process_config_call(name):
  src = open(os.path.join(ROOT, "system/manager/process_config.py"), encoding="utf-8").read()
  for node in ast.walk(ast.parse(src)):
    if not isinstance(node, ast.Call):
      continue
    if not node.args or not isinstance(node.args[0], ast.Constant) or node.args[0].value != name:
      continue
    return node
  return None


def test_mapd_v2_restarts_if_it_crashes():
  """Parsed, not grepped: the whole point is the KEYWORD reaching the constructor."""
  call = _process_config_call("mapd_v2")
  assert call is not None, "mapd_v2 is no longer registered in process_config"
  kw = {k.arg: k.value for k in call.keywords}
  assert "restart_if_crash" in kw, (
    "mapd_v2 is registered without restart_if_crash -- if it dies, SLA loses its speed limit "
    "source for the rest of the drive and only a reboot recovers it")
  assert isinstance(kw["restart_if_crash"], ast.Constant) and kw["restart_if_crash"].value is True


def test_native_process_accepts_restart_if_crash():
  """ensure_running's restart branch is unreachable for a class that does not take the argument,
  and NativeProcess.start() returns early whenever self.proc is not None -- dead or alive."""
  src = open(os.path.join(ROOT, "system/manager/process.py"), encoding="utf-8").read()
  tree = ast.parse(src)
  for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef) and node.name == "NativeProcess":
      for fn in node.body:
        if isinstance(fn, ast.FunctionDef) and fn.name == "__init__":
          names = [a.arg for a in fn.args.args] + [a.arg for a in fn.args.kwonlyargs]
          assert "restart_if_crash" in names, (
            "NativeProcess.__init__ does not accept restart_if_crash, so no native process can "
            "ever be restarted after a crash")
          assigns = [n for n in ast.walk(fn) if isinstance(n, ast.Attribute)
                     and n.attr == "restart_if_crash"]
          assert assigns, "restart_if_crash is accepted but never assigned to self"
          return
  raise AssertionError("NativeProcess.__init__ not found")
