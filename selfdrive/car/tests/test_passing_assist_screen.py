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
      assert m, f"unrecognised non-control entry: {ast.unparse(el)[:60]}"
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
  built = set(re.findall(r"self\.(_\w+) = (?:toggle|option)_item_sp\(", SRC))
  shown = set(_returned_items())
  assert not (built - shown), f"built but unreachable: {sorted(built - shown)}"


def test_nothing_is_shown_that_was_never_built():
  built = set(re.findall(r"self\.(_\w+) = (?:toggle|option)_item_sp\(", SRC))
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
