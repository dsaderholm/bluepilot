"""
BluePilot: structural checks on the settings layout, parsed from source.

Both bugs these cover shipped to a car and crash-looped the UI, and neither was reachable by any
existing test -- importing this module needs pyray, ui_state and compiled Params, so nothing was
checking it at all. Parsing the source needs none of that and runs anywhere.

  1. An inserted method landed between @staticmethod and the def below it, silently stealing the
     decorator. _safe_get_bool became an instance method, so self._safe_get_bool(params, key) bound
     self to `params` and the call became self.get_bool(...) -- AttributeError on every refresh.

  2. A dual_button_item was registered in _refresh_toggles, which calls
     item.action_item.set_state() on everything in that list. Only toggles have set_state.

Both are the same shape: a change that is locally reasonable and breaks something several lines
away. That is what a structural test is for.
"""

import ast
import pathlib

import pytest

LAYOUT_SRC = pathlib.Path(__file__).parents[1] / "bluepilot.py"


@pytest.fixture(scope="module")
def cls() -> ast.ClassDef:
  tree = ast.parse(LAYOUT_SRC.read_text(encoding="utf-8"))
  return next(n for n in ast.walk(tree)
              if isinstance(n, ast.ClassDef) and n.name == "BluePilotLayout")


def decorators(fn: ast.FunctionDef) -> set[str]:
  return {d.id for d in fn.decorator_list if isinstance(d, ast.Name)}


def method(cls: ast.ClassDef, name: str) -> ast.FunctionDef:
  fn = next((n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name), None)
  assert fn is not None, f"{name} not found in BluePilotLayout"
  return fn


class TestStaticMethodsKeptTheirDecorator:
  """A method inserted above one of these steals its decorator. Silent, and fatal at runtime."""

  def test_safe_get_bool_is_static(self, cls):
    fn = method(cls, "_safe_get_bool")
    assert "staticmethod" in decorators(fn), (
      "_safe_get_bool lost @staticmethod -- self binds to its `params` argument and every "
      "self._safe_get_bool(params, key) call becomes self.get_bool(...)")

  def test_every_self_less_method_is_static(self, cls):
    """Catches the theft from the other direction: any method whose first arg is not `self` must
    be decorated, or calling it through an instance shifts every argument by one."""
    for fn in cls.body:
      if not isinstance(fn, ast.FunctionDef) or fn.name.startswith("__"):
        continue
      args = [a.arg for a in fn.args.args]
      if args and args[0] != "self":
        assert decorators(fn) & {"staticmethod", "classmethod"}, (
          f"{fn.name} takes {args[0]!r} first but is not a staticmethod")

  def test_static_methods_do_not_take_self(self, cls):
    """And the reverse: a @staticmethod that still declares self is a method that was decorated by
    accident -- calling it passes the first real argument as `self`."""
    for fn in cls.body:
      if not isinstance(fn, ast.FunctionDef):
        continue
      if "staticmethod" in decorators(fn):
        args = [a.arg for a in fn.args.args]
        assert not args or args[0] != "self", (
          f"{fn.name} is @staticmethod but declares self -- it likely stole the decorator from "
          f"the method below it")


class TestRefreshTogglesOnlyContainsToggles:
  def test_every_refresh_entry_is_a_toggle(self, cls):
    """_update_toggles calls item.action_item.set_state() on every entry. Anything built by a
    non-toggle helper raises AttributeError and takes the settings page down."""
    init = method(cls, "_initialize_items") if any(
      isinstance(n, ast.FunctionDef) and n.name == "_initialize_items" for n in cls.body
    ) else None
    src = LAYOUT_SRC.read_text(encoding="utf-8")

    tree = ast.parse(src)
    assign = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.Assign)
                  and any(getattr(t, "attr", None) == "_refresh_toggles" for t in n.targets))
    names = {n.attr for n in ast.walk(assign.value)
             if isinstance(n, ast.Attribute) and n.attr.startswith("_")}

    # How each registered item was constructed
    builders = {}
    for node in ast.walk(tree):
      if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
        for t in node.targets:
          if getattr(t, "attr", None) in names and isinstance(node.value.func, ast.Name):
            builders[t.attr] = node.value.func.id

    bad = {n: b for n, b in builders.items() if "toggle" not in b}
    assert not bad, f"non-toggle items registered in _refresh_toggles: {bad}"
    assert init is None or True
