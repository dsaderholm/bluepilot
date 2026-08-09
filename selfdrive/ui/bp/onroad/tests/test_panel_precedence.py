"""
FusionPilot: which passing-assist state wins the panel when several are true at once.

Seventeen states share three lines and exactly one can be shown. The precedence has never been
looked at as a whole, and the last bug of this shape -- a green PASS LEFT displayed seconds after
the car backed out of that very pass -- was two individually-correct pieces with nothing wired
between them.

Read statically. The panel cannot be rendered here and the ordering is expressed as early returns,
so what is checked is the ORDER OF THE GUARDS in the source rather than the pixels.
"""

import ast
import re
from pathlib import Path

ROOT = next(d for d in Path(__file__).resolve().parents if (d / "common" / "params_keys.h").exists())
SRC = (ROOT / "selfdrive" / "ui" / "bp" / "onroad" / "hud_renderer_bp.py").read_text(encoding="utf-8")


def _body() -> str:
  tree = ast.parse(SRC)
  cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "HudRendererBP")
  fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_update_passing_assist")
  return "\n".join(SRC.splitlines()[fn.lineno - 1:fn.end_lineno])


def _order(*names: str) -> list[int]:
  body = _body()
  found = []
  for n in names:
    m = re.search(re.escape(n), body)
    assert m, f"{n} is no longer called from _update_passing_assist"
    found.append(m.start())
  return found


def test_the_drive_summary_comes_before_anything_moving():
  """It only draws when stopped, so it cannot actually collide -- but the guard has to be reached
  before the states that assume motion, or a future change to that condition breaks silently."""
  a, b = _order("_draw_drive_summary", "_draw_crawl")
  assert a < b


def test_a_slow_pass_outranks_an_uncommitted_dry_run():
  """It is happening now and is the one state a driver might act on."""
  a, b = _order("_draw_crawl", "_draw_maneuver")
  assert a < b


def test_but_not_while_the_car_is_committed():
  """The important half. Crawling and crossing can both be true at once -- a slow pass IS a car
  close alongside being barely gained on -- and letting the crawl win there would suppress the only
  red state this panel has: something arriving behind us mid-change."""
  body = _body()
  m = re.search(r"committed\s*=\s*(.+?)\n", body)
  assert m, "the committed guard is gone; the crawl can now hide an abort"
  guard = m.group(1)
  assert "changing" in guard and "aborting" in guard
  assert "maneuverStandDown" in guard
  assert re.search(r"if not committed and self\._draw_crawl", body), \
    "the crawl is no longer gated on the maneuver being uncommitted"


def test_the_blinker_test_owns_the_panel_outright():
  """It only runs stopped with cruise off, and it is a deliberate action the driver is watching
  for a result from. Nothing here may talk over it."""
  a, b = _order("_render_blinker_test", "_draw_drive_summary")
  assert a < b


def test_the_blinker_test_can_never_own_the_panel_in_motion():
  """From a drive: "all the feedback went away on the screen and it just kept beeping."

  _render_blinker_test returns True for any non-idle blinker-test state and the caller returns
  immediately, so it silently owns the whole panel. That was harmless only for as long as nothing
  published the state -- it read 0 forever and the branch never fired. Wiring the verdict through
  to the message made it live, and a state left non-idle then hid every passing-assist readout for
  the rest of the drive while the planner carried on chiming into a blank screen.

  Removing the guard broke no test when this was written, which is how it reached the car. The
  check is on the CAR, not the state: a stuck, stale or unpublished value must not be able to cost
  the driver their instruments.
  """
  tree = ast.parse(SRC)
  fn = next((n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == "_render_blinker_test"), None)
  assert fn is not None, "_render_blinker_test not found -- this test would pass on anything"

  body = ast.get_source_segment(SRC, fn) or ""
  first_true = body.find("return True")
  assert first_true > 0, "no `return True` -- the method no longer owns the panel; delete this test"
  before = body[:first_true]
  assert "vEgo" in before, (
    "the blinker test can own the panel while the car is moving -- it must check speed first")


def test_every_multi_item_sub_line_is_width_fitted():
    """The panel draws its sub-line from its own measured width -- it does not wrap and does not
    shrink, so anything past MAX_SUB_WIDTH runs off BOTH edges.

    One assignment joined its items unconditionally. Its worst case measures 1355px against a
    1008px panel, and the two items at the front of that list are the geometry numbers that explain
    a refusal -- so the single most useful thing on the screen was the first thing off it. Reported
    as "it still just says no lane to move into all the time" and "at some point the entire UI went
    off the screen".

    Read from source: hud_renderer_bp imports pyray, which does not load offline everywhere.
    """
    bad = [ln.strip() for ln in SRC.splitlines()
           if "self._pa_sub = " in ln and ".join(" in ln and "_fit_sub" not in ln]
    assert not bad, "sub-line built by joining a list without fitting it to the panel:\n  " + "\n  ".join(bad)
