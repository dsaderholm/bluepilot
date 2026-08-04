#!/usr/bin/env python3
"""Render the passing-assist panel to PNG, offline, at device scale.

BluePilot: this panel is now the entire readout for passing assist -- every gate, the dry run of
the manoeuvre, the slow-pass warning and the drive summary all land in the same three lines. It had
no preview, and the first thing rendering it found was three readouts that were being assembled and
then silently dropped, because the methods that set `_pa_sub_detail` return before the code that
folds it into `_pa_sub`. That bug survived a full test suite and would have survived a drive too --
nothing was missing, the lines were just quietly shorter.

Like preview_acc_status.py, this calls the SHIPPED drawing method out of hud_renderer_bp.py rather
than reimplementing it, so what it renders is what the car draws. Add a scene whenever a new panel
state is introduced.

Usage:  python selfdrive/ui/bp/onroad/tools/preview_passing_panel.py [outdir]
"""
import ast
import os
import sys
import types

import pyray as rl

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
HUD = os.path.join(ROOT, "selfdrive", "ui", "bp", "onroad", "hud_renderer_bp.py")
FONTS = os.path.join(ROOT, "selfdrive", "assets", "fonts")

W, H = 1120, 1080

# (caption, main, sub, progress, alert, colour)
AMBER = (240, 175, 60)
GREEN = (120, 220, 140)
BLUE = (140, 190, 230)
GREY = (170, 175, 180)
PURPLE = (190, 150, 235)
INFO = (150, 205, 235)
RED = (235, 90, 80)

SCENES = [
  ("building toward a pass",
   "Slower car ahead", "no rear data  -  2 this drive", 0.65, False, GREY),
  ("decided",
   "<<<  PASS LEFT", "no rear data  -  3 this drive", 0.0, True, GREEN),
  ("dry run: signalling",
   "WOULD SIGNAL LEFT", "waiting before moving  -  3 backed out this drive", 0.7, True, PURPLE),
  ("dry run: signalling on a camera-only lead",
   "WOULD SIGNAL LEFT", "waiting before moving  -  camera only, speed not radar-measured",
   0.4, True, PURPLE),
  ("dry run: crossing",
   "WOULD BE CHANGING LEFT", "blinker on, steering across", 0.5, True, PURPLE),
  ("dry run: backing out, something arriving behind",
   "WOULD BACK OUT", "something arriving behind", 0.0, True, RED),
  ("keep right: signalling",
   "WOULD SIGNAL RIGHT", "moving back over", 0.6, True, PURPLE),
  ("a pass that is grinding",
   "SLOW PASS  14s", "barely gaining on the car left", 0.0, True, AMBER),
  ("held: the car ahead slammed on",
   "Car ahead is braking", "no rear data  -  1 this drive", 0.0, False, GREY),
  ("held: closing in deliberately",
   "Closing in", "no rear data", 0.0, False, GREY),
  ("blocked: oncoming traffic on the left",
   "ONCOMING LEFT", "saw 62 at 410ft  -  74s left", 0.0, False, GREY),
  ("keep right",
   "MOVE RIGHT  >>>", "no rear data", 0.0, True, BLUE),
  ("stopped: what this drive measured",
   "THIS DRIVE", "2 slow passes, worst 14s  -  3 backed out  -  ACC braked by 476ft",
   0.0, False, INFO),
  ("worst case for width: everything at once",
   "WOULD BE CHANGING RIGHT",
   "blinker on, steering across  -  camera only, speed not radar-measured", 0.6, True, PURPLE),
]


def load_shipped_drawing_code():
  """Lift the panel drawing method out of the source file.

  Importing the module would pull in ui_state, Params, gui_app and both renderer base classes for
  one function whose only real dependencies are raylib and attributes on self.
  """
  tree = ast.parse(open(HUD, encoding="utf-8").read())
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "HudRendererBP")
  method = next(n for n in cls.body
                if isinstance(n, ast.FunctionDef) and n.name == "_draw_passing_assist")

  consts = [n for n in tree.body
            if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in
            ("SPEED_UNIT_CENTER_Y",)]

  ns = {
    "rl": rl,
    "FONT_SIZES": types.SimpleNamespace(speed_unit=60),
    # The panel measures its own text, so this has to be real rather than a guess.
    "measure_text_cached": lambda font, text, size, spacing=0: rl.measure_text_ex(font, text, size, spacing),
  }
  exec(compile(ast.Module(body=consts, type_ignores=[]), HUD, "exec"), ns)
  exec(compile(ast.Module(body=[method], type_ignores=[]), HUD, "exec"), ns)
  return ns


def main(outdir):
  ns = load_shipped_drawing_code()

  rl.set_config_flags(rl.ConfigFlags.FLAG_WINDOW_HIDDEN | rl.ConfigFlags.FLAG_MSAA_4X_HINT)
  rl.init_window(W, H, b"passing panel preview")
  font = rl.load_font_ex(os.path.join(FONTS, "Inter-Bold.ttf").encode(), 200, None, 0)
  rl.set_texture_filter(font.texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)

  tex = rl.load_render_texture(W, H)
  rect = rl.Rectangle(0, 0, W, H)
  widest = 0.0

  for i, (cap, main_text, sub, progress, alert, colour) in enumerate(SCENES):
    stub = types.SimpleNamespace(
      _font_bold=font, _pa_main=main_text, _pa_sub=sub, _pa_progress=progress,
      _pa_alert=alert, _pa_color=rl.Color(*colour, 255),
      _pa_panel_rect=None,
      # Tap handling needs gui_app; it is input, not drawing, so it is stubbed out entirely.
      _handle_panel_tap=lambda panel: None,
    )
    rl.begin_texture_mode(tex)
    rl.draw_rectangle_gradient_v(0, 0, W, H, rl.Color(96, 100, 106, 255), rl.Color(52, 55, 60, 255))
    ns["_draw_passing_assist"](stub, rect)
    rl.end_texture_mode()

    img = rl.load_image_from_texture(tex.texture)
    rl.image_flip_vertical(img)
    path = os.path.join(outdir, f"pa_{i:02d}.png")
    rl.export_image(img, path.encode())
    rl.unload_image(img)

    p = stub._pa_panel_rect
    widest = max(widest, p.width)
    # Printed rather than only rendered: the numbers are what catch a panel about to run off the
    # edge, which is hard to spot by eye until it does.
    print(f"{i:2d}  {p.width:6.0f} x {p.height:5.0f}   {cap}")

  print(f"\nwidest panel {widest:.0f} of {W - 40} available")
  assert widest <= W - 40, "a panel is wider than the screen allows"
  rl.close_window()
  print(f"wrote {len(SCENES)} PNGs to {outdir}")


if __name__ == "__main__":
  out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/pa_preview"
  os.makedirs(out, exist_ok=True)
  main(out)
