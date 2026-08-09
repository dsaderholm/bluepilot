"""FusionPilot: every settings sub-panel must actually DRAW its back button.

Caught by hand, on the last check before a test drive, in a panel added the same day: Customize
Blinker constructed a NavButton, wired its callback, and never rendered it. A room with no door --
you open it and nothing on screen leaves.

It is the easiest thing in the file to leave out precisely because it looks like boilerplate: the
constructor half is what you copy, the render half is three lines further down in a method you are
writing from scratch. Constructing the button is not the part that matters.

Static, so it needs neither pyray nor a device. It reads the source rather than importing it,
like the other UI guards here.
"""

import ast
import pathlib

import pytest


def _repo_root() -> pathlib.Path:
  for d in pathlib.Path(__file__).resolve().parents:
    if (d / "common" / "params_keys.h").exists():
      return d
  raise RuntimeError("repo root not found")


SUB_LAYOUTS = sorted(
  (_repo_root() / "selfdrive" / "ui" / "sunnypilot" / "layouts" / "settings"
   / "steering_sub_layouts").glob("*.py")
)


def _classes_with_a_back_button(tree):
  """Classes that build a NavButton -- i.e. claim to be a sub-panel you can leave."""
  out = []
  for node in ast.walk(tree):
    if not isinstance(node, ast.ClassDef):
      continue
    src = ast.dump(node)
    if "NavButton" in src:
      out.append(node)
  return out


def test_there_are_sub_layouts_to_check():
  """A glob that matched nothing would make everything below pass on an empty repo."""
  assert len(SUB_LAYOUTS) >= 3, [p.name for p in SUB_LAYOUTS]


@pytest.mark.parametrize("path", SUB_LAYOUTS, ids=lambda p: p.name)
def test_the_back_button_is_rendered_not_just_built(path):
  tree = ast.parse(path.read_text(encoding="utf-8"))
  for cls in _classes_with_a_back_button(tree):
    render = next((n for n in cls.body
                   if isinstance(n, ast.FunctionDef) and n.name == "_render"), None)
    assert render is not None, f"{path.name}:{cls.name} builds a back button and never renders"
    calls = {ast.unparse(n.func) for n in ast.walk(render) if isinstance(n, ast.Call)}
    assert "self._back_button.render" in calls, (
      f"{path.name}:{cls.name} constructs a back button but _render never draws it -- "
      "the panel opens and cannot be left")


@pytest.mark.parametrize("path", SUB_LAYOUTS, ids=lambda p: p.name)
def test_the_content_does_not_render_under_the_back_button(path):
  """Drawing the button and then handing the scroller the FULL rect puts the first row underneath
  it. Every sibling shrinks the rect by the button height first; this asserts the shrink exists
  rather than trying to guess the arithmetic.
  """
  tree = ast.parse(path.read_text(encoding="utf-8"))
  for cls in _classes_with_a_back_button(tree):
    render = next((n for n in cls.body
                   if isinstance(n, ast.FunctionDef) and n.name == "_render"), None)
    if render is None:
      continue
    body = ast.unparse(render)
    if "self._scroller.render" not in body:
      continue
    assert "self._scroller.render(rect)" not in body, (
      f"{path.name}:{cls.name} renders the scroller over the full rect, so its first row sits "
      "under the back button")
