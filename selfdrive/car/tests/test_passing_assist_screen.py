"""
BluePilot: the passing-assist settings screen, read as a whole.

Every control here was added in its own commit and described sensibly in isolation. The screen as a
whole was never read end to end until it had about twenty of them, at which point the first section
had drifted to nine controls -- three of which were not about that section's subject at all.

Nothing renders offline, so this is structural: every control reachable, every one under a heading,
no heading left empty, and no section grown past what someone can scan while parked.
"""

import ast
import re
from pathlib import Path

ROOT = next(d for d in Path(__file__).resolve().parents if (d / "common" / "params_keys.h").exists())
SRC = (ROOT / "selfdrive" / "ui" / "sunnypilot" / "layouts" / "settings" /
       "steering_sub_layouts" / "passing_assist_settings.py").read_text(encoding="utf-8")

# Longest a section can get before it stops being a group and becomes a list. Six is what fits on
# screen without scrolling past the heading that gives the controls their meaning.
MAX_PER_SECTION = 6


def _returned_items() -> list[str]:
  """The literal order of the returned list, headings included."""
  tree = ast.parse(SRC)
  fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_initialize_items")
  ret = next(n for n in ast.walk(fn) if isinstance(n, ast.Return))
  out = []
  for el in ret.value.elts:
    if isinstance(el, ast.Attribute):
      out.append(el.attr)
    else:
      # ast.unparse emits SINGLE quotes, so a pattern written for the source text matches nothing
      # and every heading collapses to one key -- which silently made the section-size check
      # meaningless. Accept either.
      m = re.search(r"""tr\(['"](.+?)['"]\)""", ast.unparse(el))
      assert m, f"unrecognized non-control entry: {ast.unparse(el)[:60]}"
      out.append("#" + m.group(1))
  return out


def _sections() -> dict[str, list[str]]:
  out, cur = {}, "(top)"
  out[cur] = []
  for item in _returned_items():
    if item.startswith("#"):
      cur = item[1:]
      out[cur] = []
    else:
      out[cur].append(item)
  return out


def test_every_control_built_is_on_the_screen():
  """The failure this catches is silent: a control constructed, given a param and a description,
  and never added to the list. It works, it is tested, and no driver can ever reach it."""
  built = set(re.findall(r"self\.(_\w+) = (?:toggle_item_sp|option_item_sp|button_item)\(", SRC))
  shown = set(_returned_items())
  assert not (built - shown), f"built but unreachable: {sorted(built - shown)}"


def test_nothing_is_shown_that_was_never_built():
  built = set(re.findall(r"self\.(_\w+) = (?:toggle_item_sp|option_item_sp|button_item)\(", SRC))
  shown = {i for i in _returned_items() if not i.startswith("#")}
  assert not (shown - built), f"listed but never built: {sorted(shown - built)}"


def test_no_section_is_empty():
  """An empty heading is a control that was moved or deleted and left its label behind."""
  empty = [name for name, items in _sections().items() if not items and name != "(top)"]
  assert not empty, f"headings with nothing under them: {empty}"


def test_no_section_has_grown_into_a_list():
  """The one that actually happened. Sections grow one commit at a time and nobody notices until
  the heading no longer describes what is under it."""
  fat = {n: len(i) for n, i in _sections().items() if len(i) > MAX_PER_SECTION}
  assert not fat, f"sections past {MAX_PER_SECTION} controls: {fat}"


def test_the_master_switch_comes_first():
  """Everything below it is inert when it is off, so it cannot sit under a heading among equals."""
  assert _returned_items()[0] == "_enabled"


def test_every_name_it_imports_actually_lives_where_it_says():
  """A settings screen is not importable offline -- pyray needs a display -- so a wrong import path
  raises nothing here and takes the whole panel out on the car instead.

  This caught `button_item` being imported from `system.ui.sunnypilot.widgets.list_view`, where it
  does not exist; it lives in `system.ui.widgets.list_view`. Nothing in 1226 tests noticed, because
  the failure is at import time on a device with a screen. See [[bluepilot-untestable-surfaces]] --
  when the surface cannot be executed, assert on its source instead.
  """
  import ast
  import pathlib

  root = pathlib.Path(__file__).resolve()
  while not (root / "common" / "params_keys.h").exists():
    root = root.parent

  missing = []
  for node in ast.walk(ast.parse(SRC)):
    if not isinstance(node, ast.ImportFrom) or not node.module:
      continue
    if not node.module.startswith("openpilot."):
      continue
    mod = root / pathlib.Path(*node.module.split(".")[1:])
    path = mod.with_suffix(".py") if mod.with_suffix(".py").exists() else mod / "__init__.py"
    if not path.exists():
      missing.append(f"{node.module} (no such module)")
      continue
    defined = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for sub in ast.walk(tree):
      if isinstance(sub, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        defined.add(sub.name)
      elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
        defined.add(sub.id)
      elif isinstance(sub, ast.ImportFrom | ast.Import):
        for a in sub.names:
          defined.add(a.asname or a.name.split(".")[0])
    for a in node.names:
      if a.name != "*" and a.name not in defined:
        missing.append(f"{a.name} from {node.module}")

  assert not missing, f"imported but not defined there: {sorted(missing)}"
