"""
FusionPilot: a settings widget must match the type its param is registered as, and must be reachable.

Two checks, both for mistakes made while moving the passing-assist controls into their own panel,
and both of which produce a crash or a dead control on the device while every other test stays
green.

WIDGET TYPE vs PARAM TYPE
Only the widgets that write through `Params.put()` are checked, and the distinction is not cosmetic:

  option_item_sp    OptionControlSP calls `params.put(key, value)` with an int. put() runs
                    _put_cast, which looks the pair up in PYTHON_2_CPP -- (int, BOOL) is not in it,
                    so pointing an option item at a BOOL key raises TypeError the first time it is
                    touched. Source looks perfectly correct; reads even work, because reading is
                    not what fails.
  toggle_item_sp    ToggleSP calls `params.put_bool()`, which goes straight to putBool and never
                    runs _put_cast at all. A toggle on an INT key is therefore a registration
                    inconsistency, NOT a runtime error -- it writes "1"/"0" and get_bool reads it
                    back correctly. sunnypilot has one (BlinkerPauseLateralControl, registered
                    INT), and it works. So this test deliberately does not flag toggles.

That asymmetry is worth stating because the obvious symmetric rule is wrong in one direction, and a
test asserting it would fail on working upstream code.

This is the widget-level cousin of the bug that already cost a drive: the blinker test wrote a str
to an INT key, the TypeError was swallowed, and the button silently did nothing.

DANGLING ITEM REFERENCES
These layouts build controls as `self.x = ...` and then list them in an `items` array. Deleting a
control without deleting its list entry leaves `self.x` unresolved, which is an AttributeError
inside __init__ -- so the whole settings screen crash-loops rather than the one row misbehaving.
That happened here: a passing-assist entry survived a move because a blank line kept it out of the
block being removed.

Static, so this needs no device and no compiled Params.
"""

import ast
import pathlib
import re

import pytest


def _repo_root() -> pathlib.Path:
  for d in pathlib.Path(__file__).resolve().parents:
    if (d / "common" / "params_keys.h").exists():
      return d
  raise RuntimeError("repo root not found")


ROOT = _repo_root()

# Every settings layout that declares param-backed widgets.
LAYOUTS = [
  "selfdrive/ui/sunnypilot/layouts/settings/cruise.py",
  "selfdrive/ui/sunnypilot/layouts/settings/steering.py",
  "selfdrive/ui/sunnypilot/layouts/settings/steering_sub_layouts/passing_assist_settings.py",
  "selfdrive/ui/sunnypilot/layouts/settings/steering_sub_layouts/lane_change_settings.py",
  "selfdrive/ui/bp/layouts/settings/bluepilot.py",
  "selfdrive/ui/sunnypilot/layouts/settings/steering_sub_layouts/blinker_settings.py",
]

# Widgets that write via Params.put(), which type-checks. Toggles are absent on purpose -- see the
# module docstring: put_bool bypasses the cast, so a toggle on an INT key is legal at runtime.
WIDGET_TYPES = {
  "option_item_sp": {"INT", "FLOAT"},
  "int_control_item": {"INT"},
  "float_control_item": {"FLOAT"},
}


@pytest.fixture(scope="module")
def key_types() -> dict[str, str]:
  src = (ROOT / "common" / "params_keys.h").read_text(encoding="utf-8", errors="replace")
  return {m.group(1): m.group(2)
          for m in re.finditer(r'\{"(\w+)",\s*\{[^}]*?,\s*(STRING|BOOL|INT|FLOAT|BYTES|TIME|JSON)\b', src)}


@pytest.mark.parametrize("path", LAYOUTS)
def test_widget_kind_matches_param_type(path, key_types):
  tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
  bad = []

  for node in ast.walk(tree):
    if not isinstance(node, ast.Call):
      continue
    name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
    allowed = WIDGET_TYPES.get(name)
    if allowed is None:
      continue
    param = next((kw.value for kw in node.keywords if kw.arg == "param"), None)
    if not (isinstance(param, ast.Constant) and isinstance(param.value, str)):
      continue
    ktype = key_types.get(param.value)
    if ktype is None:
      bad.append(f"line {node.lineno}: {name} drives {param.value}, which is not in params_keys.h")
    elif ktype not in allowed:
      bad.append(f"line {node.lineno}: {name} drives {param.value}, registered {ktype} "
                 f"(needs one of {sorted(allowed)})")

  assert not bad, f"{path}\n" + "\n".join(bad)


@pytest.mark.parametrize("path", LAYOUTS)
def test_no_dangling_item_references(path):
  """Every `self.x` used in a list literal must be assigned somewhere in the same class.

  Deliberately narrow: only list elements, which is where the items arrays live. A broader scan
  would trip over legitimate attributes inherited from Widget.
  """
  src = (ROOT / path).read_text(encoding="utf-8")
  tree = ast.parse(src)

  assigned = set()
  for node in ast.walk(tree):
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store) \
       and isinstance(node.value, ast.Name) and node.value.id == "self":
      assigned.add(node.attr)

  referenced = []
  for node in ast.walk(tree):
    if not isinstance(node, (ast.List, ast.Tuple)):
      continue
    for el in node.elts:
      if isinstance(el, ast.Attribute) and isinstance(el.value, ast.Name) and el.value.id == "self":
        referenced.append((el.lineno, el.attr))
      # ("ParamName", self.widget) pairs, as used by the BluePilot refresh list
      elif isinstance(el, ast.Tuple):
        for sub in el.elts:
          if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) and sub.value.id == "self":
            referenced.append((sub.lineno, sub.attr))

  missing = [f"line {ln}: self.{attr} is listed but never assigned" for ln, attr in referenced
             if attr not in assigned]
  assert not missing, f"{path}\n" + "\n".join(missing)
