"""FusionPilot: tapping the HOLD badge must not also open the sidebar.

Reported 2026-08-12: "Tapping a hold does nothing. If you tap the screen it just opens the menu on
the left." The pin request WAS being raised -- but `super()._handle_mouse_release()` ran first, so
upstream's handler saw every tap and slid the sidebar out over the badge. The gesture worked and
looked dead, which is worse than not working, because it was tried repeatedly and abandoned.

So the badge tap is consumed: check our own rect first, raise the request, and return without
calling the parent. Anywhere else on screen still falls through to upstream unchanged.

Static rather than rendered, because the ordering is the whole bug and the renderer needs raylib.
"""
import ast
import pathlib


def _handler() -> ast.FunctionDef:
  src = (pathlib.Path(__file__).resolve().parents[1] / "hud_renderer_bp.py").read_text(encoding="utf-8")
  for node in ast.walk(ast.parse(src)):
    if isinstance(node, ast.FunctionDef) and node.name == "_handle_mouse_release":
      return node
  raise AssertionError("_handle_mouse_release not found")


def _super_call_index(fn: ast.FunctionDef) -> int:
  for i, stmt in enumerate(fn.body):
    for node in ast.walk(stmt):
      if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
          and node.func.attr == "_handle_mouse_release"
          and isinstance(node.func.value, ast.Call)
          and getattr(node.func.value.func, "id", None) == "super"):
        return i
  return -1


def _pin_request_index(fn: ast.FunctionDef) -> int:
  for i, stmt in enumerate(fn.body):
    for node in ast.walk(stmt):
      if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
          and node.func.attr in ("put_bool", "put") and node.args
          and isinstance(node.args[0], ast.Constant)
          and node.args[0].value == "IcbmPinHoldRequest"):
        return i
  return -1


def test_the_badge_is_checked_before_the_parent_handler_runs():
  fn = _handler()
  pin, sup = _pin_request_index(fn), _super_call_index(fn)
  assert pin >= 0, "the pin request is gone entirely"
  assert sup >= 0, "the parent handler is never called; taps elsewhere would stop working"
  assert pin < sup, (
    "super()._handle_mouse_release runs before the badge is checked, so upstream sees the tap and "
    "opens the sidebar over the badge -- THE REPORTED BUG")


def test_the_badge_branch_returns_so_the_tap_is_not_handled_twice():
  """Checking first is not enough; without the return the parent still runs afterwards."""
  fn = _handler()
  pin = _pin_request_index(fn)
  assert any(isinstance(n, ast.Return) for n in ast.walk(fn.body[pin])), (
    "the badge branch does not return, so the tap falls through to the sidebar as well")
