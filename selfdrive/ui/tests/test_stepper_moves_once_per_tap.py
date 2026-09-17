"""One tap on a settings +/- stepper must move the setting ONE step.

His report, 2026-09-17: *"that setting didn't let me set 10, I had to do 11"*, then *"it's like the
other settings in there that go in steps"*. Hold Steering Through Stops steps by 1.0 and could not
land on 10.

The + and - buttons are child widgets, and a child widget handles its own tap when it is rendered.
FloatControlAction ALSO caught the same release in its own `_handle_mouse_release` and forwarded it
to the button -- so the callback ran twice per tap. Every stepper on the page moved two steps.

These tests drive the REAL widget's render and event path with synthetic touches. Only drawing and
font metrics are stubbed; hit-testing, the event loop and the stepping are the shipped code.
"""
from __future__ import annotations

import os
import pathlib
import sys

import cffi
import pyray as rl
import pytest

import openpilot

# The offline runner stands in a bare `openpilot.system` package (for its sentry stub) with an empty
# __path__, which hides the real system/ui tree. Point it back at the real directory; the stubbed
# leaves the runner installed stay in sys.modules and still win.
_system = sys.modules.get("openpilot.system")
if _system is not None and not list(getattr(_system, "__path__", [])):
  _system.__path__ = [str(pathlib.Path(openpilot.__path__[0]) / "system")]

# application.py builds gui_app at import. Two things in that constructor need a real device: an
# auto-scale that asks for a monitor (SCALE skips it), and dlopen(None) for a raylib log hook, which
# Windows refuses outright. Neither is on the tap path under test.
os.environ.setdefault("SCALE", "1")
_real_dlopen = cffi.FFI.dlopen


def _dlopen(self, name, *a, **k):
  if name is None and sys.platform == "win32":
    return None
  return _real_dlopen(self, name, *a, **k)


cffi.FFI.dlopen = _dlopen

import openpilot.selfdrive.ui.bp.widgets.float_control_item as fci  # noqa: E402
import openpilot.system.ui.widgets.button as button_mod  # noqa: E402
from openpilot.system.ui.lib.application import MouseEvent, MousePos, gui_app  # noqa: E402

WIDTH = 600.0
HEIGHT = 100.0


class _Params:
  def __init__(self, value: float) -> None:
    self.value = value

  def get(self, _key, return_default=False):
    return self.value

  def put(self, _key, value, block=True):
    self.value = value


class _Label:
  """A button's caption. Laying text out needs a loaded font; nothing about a tap does."""

  def __init__(self, *_a, **_k):
    pass

  def __getattr__(self, _name):
    return lambda *a, **k: None


@pytest.fixture
def stepper(monkeypatch):
  # The real gui_app, with only its window-bound pieces replaced: fonts, and the per-frame event list.
  monkeypatch.setattr(gui_app, "font", lambda *_a: None)
  monkeypatch.setattr(gui_app, "_mouse_events", [])
  monkeypatch.setattr(gui_app, "_show_touches", False)
  monkeypatch.setattr(fci, "gui_label", lambda *a, **k: None)
  monkeypatch.setattr(fci, "measure_text_cached", lambda _font, text, size: rl.Vector2(len(text) * 25, size))
  monkeypatch.setattr(fci.Button, "_render", lambda self, rect: None)
  monkeypatch.setattr(button_mod, "Label", _Label)

  def make(value: float, lo: float, hi: float, step: float):
    params = _Params(value)
    monkeypatch.setattr(fci, "Params", lambda: params)
    action = fci.FloatControlAction("K", lo, hi, step)
    rect = rl.Rectangle(0, 0, WIDTH, HEIGHT)

    def render(events):
      gui_app._mouse_events = events
      action.render(rect)

    def tap(x: float) -> None:
      pos = MousePos(x, HEIGHT / 2)
      render([MouseEvent(pos, 0, True, False, True, 0.0)])
      render([MouseEvent(pos, 0, False, True, False, 0.1)])
      render([])

    def plus_x() -> float:
      return WIDTH - 20 - fci.BUTTON_SIZE / 2          # right-anchored, fixed

    def minus_x() -> float:
      text = action._value_text(action._get_value())
      total = fci.BUTTON_SIZE * 2 + fci.BUTTON_SPACING * 2 + len(text) * 25 + 20
      return WIDTH - 20 - total + fci.BUTTON_SIZE / 2

    render([])
    return action, tap, plus_x, minus_x

  return make


def test_one_tap_on_plus_moves_one_step(stepper):
  action, tap, plus_x, _ = stepper(0.0, 0.0, 15.0, 1.0)
  values = []
  for _ in range(10):
    tap(plus_x())
    values.append(action._get_value())
  assert values == [float(n) for n in range(1, 11)], values


def test_one_tap_on_minus_moves_one_step(stepper):
  action, tap, _, minus_x = stepper(15.0, 0.0, 15.0, 1.0)
  values = []
  for _ in range(5):
    tap(minus_x())
    values.append(action._get_value())
  assert values == [14.0, 13.0, 12.0, 11.0, 10.0], values


def test_ten_is_reachable_from_either_side(stepper):
  """His exact report: coming down from above landed on 11 and then 9."""
  action, tap, plus_x, minus_x = stepper(0.0, 0.0, 15.0, 1.0)
  for _ in range(12):
    tap(plus_x())
  assert action._get_value() == 12.0
  tap(minus_x())
  tap(minus_x())
  assert action._get_value() == 10.0


def test_fine_steps_move_one_hundredth(stepper):
  """The angle gains step by 0.01; a double tap there is 0.02, which he has been tuning through."""
  action, tap, plus_x, minus_x = stepper(0.78, 0.5, 1.5, 0.01)
  tap(plus_x())
  assert action._get_value() == pytest.approx(0.79)
  tap(minus_x())
  assert action._get_value() == pytest.approx(0.78)


def test_the_limits_still_hold(stepper):
  action, tap, plus_x, minus_x = stepper(15.0, 0.0, 15.0, 1.0)
  tap(plus_x())
  assert action._get_value() == 15.0
  action2, tap2, _, minus_x2 = stepper(0.0, 0.0, 15.0, 1.0)
  tap2(minus_x2())
  assert action2._get_value() == 0.0
