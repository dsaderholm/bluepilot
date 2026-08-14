#!/usr/bin/env python3
"""Render the comma 4 HOLD badge offline, at the real 536x240, before it ever reaches the car.

The big screen has `selfdrive/ui/bp/onroad/tools/preview_acc_status.py` and the owner confirmed
after driving that the car looks EXACTLY like what it renders. The small screen needs the same, more
so: 536x240 is where a layout that reads fine in source is unreadable at 70 mph, and there is no way
to find that out from the code.

Like the big-screen tool, this LIFTS THE SHIPPED METHOD out of the source with ast rather than
reimplementing it. A preview that redraws the badge its own way is a drawing of a drawing, and it
agrees with the car right up until someone edits one of them.

    python selfdrive/ui/bp/mici/onroad/tools/preview_hold_badge_mici.py [outdir]
"""
from __future__ import annotations

import ast
import os
import sys
import types

import pyray as rl

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", ".."))
HUD = os.path.join(ROOT, "selfdrive", "ui", "bp", "mici", "onroad", "hud_renderer_bp.py")
FONTS = os.path.join(ROOT, "selfdrive", "assets", "fonts")

W, H = 536, 240        # the comma 4 display, exactly
# Where mici's own HUD already draws, sketched so the badge is judged against a real screen rather
# than an empty one. Both come from selfdrive/ui/mici/onroad/hud_renderer.py.
SET_SPEED_CIRCLE = 162      # _draw_set_speed's drop shadow diameter, top-left at the rect origin
WHEEL_D = 86                # the steering wheel, bottom-left

# (caption, baseline, locked, pinned, pin_suggested, lamp_available, lamps_lit, acc, accel)
SCENES = [
  ("holding 70, coasting, lamps dark", 70, False, False, False, True, False, "COAST", 0.0),
  ("braking hard enough to light the lamps", 70, False, False, False, True, True, "BRAKE", 1.4),
  ("braking too lightly to light them", 70, False, False, False, True, False, "BRAKE", 0.4),
  ("engine braking: slowing, no pads, no lamps", 70, False, False, False, True, False,
   "ENG BRAKE", 0.9),
  ("precharging: pressurised, not yet slowing", 70, False, False, False, True, False,
   "PRE-BRAKE", 0.0),
  ("accelerating back to the hold", 70, False, False, False, True, False, "ACCEL", 0.6),
  ("hold suppressed by a curve, ACC braking", 70, True, False, False, True, True, "BRAKE", 1.4),
  ("pinned here", 45, False, True, False, True, False, "COAST", 0.0),
  ("no hold -- the stack closes up", 0, False, False, False, True, True, "BRAKE", 1.4),
  ("cruise off: no ACC state, no lamp data", 0, False, False, False, False, False, "", 0.0),
]


def load_shipped_drawing_code():
  """Lift `_draw_hold_badge` and the module constants out of the source file."""
  tree = ast.parse(open(HUD, encoding="utf-8").read())
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MiciHudRendererBP")
  wanted = ("_draw_hold_badge", "_draw_acc_pill", "_draw_brake_lamp_pill")
  methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
  assert len(methods) == len(wanted), f"expected {wanted}, found {[m.name for m in methods]}"

  ns = {
    "rl": rl,
    "measure_text_cached": lambda font, text, size: rl.measure_text_ex(font, text, size, 0),
  }
  for node in (n for n in tree.body if isinstance(n, ast.Assign)):
    try:
      exec(compile(ast.Module(body=[node], type_ignores=[]), "<const>", "exec"), ns)
    except NameError:
      pass   # constants referencing car structs are irrelevant here
  exec(compile(ast.Module(body=methods, type_ignores=[]), "<methods>", "exec"), ns)

  for required in ("HOLD_HEIGHT", "HOLD_FILL", "HOLD_LOCKED_FILL", "HOLD_MARGIN", "HOLD_DOT_COLOR",
                   "LAMP_HEIGHT", "LAMP_ON_FILL", "LAMP_OFF_FILL", "LAMP_LABEL_ON",
                   "ACC_HEIGHT", "ACC_STATUS_COLORS", "ACC_QUIET_STATES"):
    assert required in ns, f"{required} did not survive extraction -- the preview would be a lie"
  return ns


def main(outdir: str) -> int:
  os.makedirs(outdir, exist_ok=True)
  ns = load_shipped_drawing_code()

  rl.set_config_flags(rl.ConfigFlags.FLAG_WINDOW_HIDDEN)
  rl.init_window(W, H, "mici hold badge")
  f_semi = rl.load_font_ex(os.path.join(FONTS, "Inter-SemiBold.ttf"), 64, None, 0)
  f_bold = rl.load_font_ex(os.path.join(FONTS, "Inter-Bold.ttf"), 64, None, 0)
  for f in (f_semi, f_bold):
    rl.set_texture_filter(f.texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)

  rect = rl.Rectangle(0, 0, W, H)
  for i, (caption, baseline, locked, pinned, suggested, lamp_avail, lit, acc, accel) in enumerate(SCENES):
    tex = rl.load_render_texture(W, H)
    rl.begin_texture_mode(tex)
    # A mid-grey stand-in for the camera feed: the badge has to survive a road, not a black screen.
    rl.clear_background(rl.Color(64, 68, 74, 255))
    # Sketch what mici already occupies, so a collision is visible rather than theoretical.
    rl.draw_circle(SET_SPEED_CIRCLE // 2, SET_SPEED_CIRCLE // 2, SET_SPEED_CIRCLE / 2,
                   rl.Color(0, 0, 0, 90))
    rl.draw_circle(21 + WHEEL_D // 2, H - 14 - WHEEL_D // 2, WHEEL_D / 2, rl.Color(0, 0, 0, 90))

    self = types.SimpleNamespace(_font_semi_bold=f_semi, _font_bold=f_bold,
                                 _lamp_data_available=lamp_avail, _brakes_on=lit)
    ns["read_icbm_hud_state"] = lambda _sm, b=baseline, lo=locked, p=pinned, s=suggested: \
      types.SimpleNamespace(has_hold=b > 0, baseline=b, hold_locked=lo, pinned=p, pin_suggested=s,
                            arrow="")
    ns["ui_state"] = types.SimpleNamespace(sm={})
    ns["read_acc_hud_state"] = lambda _sm, a=acc, mag=accel: types.SimpleNamespace(
      has_state=bool(a), state=a, accel=mag)
    below = ns["_draw_hold_badge"](self, rect)
    below = ns["_draw_acc_pill"](self, rect, below)
    ns["_draw_brake_lamp_pill"](self, rect, below)
    rl.end_texture_mode()

    path = os.path.join(outdir, f"mici_hold_{i}.png")
    img = rl.load_image_from_texture(tex.texture)
    rl.image_flip_vertical(img)
    rl.export_image(img, path)
    rl.unload_image(img)
    rl.unload_render_texture(tex)
    print(f"  {os.path.basename(path)}  {caption}")

  rl.close_window()
  print(f"\n{len(SCENES)} scenes at {W}x{H} -> {outdir}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "mici_preview"))
