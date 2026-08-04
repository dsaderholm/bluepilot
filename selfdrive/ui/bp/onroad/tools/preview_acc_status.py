#!/usr/bin/env python3
"""Render the HOLD badge and Ford ACC pill to PNG, offline, at device scale.

BluePilot: these two readouts sit under the MAX box, and until now the only way to judge their
size and placement was to flash the device and drive. That is a poor loop for a cosmetic change,
and it is why they shipped as 34 px unbacked text that could not be read at a glance.

This calls the SHIPPED drawing methods out of hud_renderer_bp.py rather than reimplementing them,
so what it renders is what the car draws. The rest of the corner -- MAX box, speed limit sign --
is redrawn here from the same UI_CONFIG geometry purely to give them something to be judged
against.

Usage:  python selfdrive/ui/bp/onroad/tools/preview_acc_status.py [outdir]
"""
import ast
import os
import sys
import types

import pyray as rl

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
HUD = os.path.join(ROOT, "selfdrive", "ui", "bp", "onroad", "hud_renderer_bp.py")
FONTS = os.path.join(ROOT, "selfdrive", "assets", "fonts")

W, H = 1120, 1080          # the left portion of the 2160x1080 display; all of this lives there
HEADER_H = 300
SET_W, SET_H = 172, 204    # UI_CONFIG.set_speed_width_imperial / set_speed_height

# (caption, set speed, posted limit, hold baseline, icbm arrow, acc state, acc m/s^2, lamps lit,
#  hold locked)
SCENES = [
  ("settled, holding 70 in a 55 -- lamps dark", 70, 55, 70, "", "COAST", 0.0, False, False),
  ("curve: ACC braking hard enough to light the lamps", 44, 55, 70, "-", "BRAKE", 1.4, True, True),
  ("ACC braking too lightly to light them", 50, 55, 70, "-", "BRAKE", 0.4, False, False),
  ("precharging: no decel, no lamps, no pads", 58, 55, 70, "", "PRE-BRAKE", 0.0, False, False),
  ("engine braking: slowing, no pads, no lamps", 52, 55, 70, "-", "ENG BRAKE", 0.9, False, False),
  ("no hold, ACC accelerating", 55, 55, 0, "", "ACCEL", 0.6, False, False),
  ("TSR not working -- the camera's own reason", 70, 55, 70, "", "COAST", 0.0, False, False,
   "TSR REGION N/A"),
  ("hold pinned to this place -- tap the badge to unpin", 45, 55, 45, "", "COAST", 0.0, False,
   False, "", True),
  ("worst case today: every readout at once, TSR still down", 70, 55, 70, "-", "BRAKE", 1.4, True,
   True, "TSR NO NAV DATA", True),
]


def load_shipped_drawing_code():
  """Lift the constants and drawing methods out of the source file.

  Importing the module would pull in the whole UI stack -- ui_state, Params, gui_app, the SP and
  upstream renderer base classes -- for three functions whose only dependencies are raylib and
  attributes on self.
  """
  tree = ast.parse(open(HUD, encoding="utf-8").read())
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "HudRendererBP")
  wanted = ("_draw_hold_badge", "_draw_acc_pill", "_draw_arrow", "_draw_brake_lamp_pill",
            "_draw_tsr_pill")
  methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
  assert len(methods) == len(wanted), f"expected {wanted}, found {[m.name for m in methods]}"

  ns = {
    "rl": rl,
    "COLORS": types.SimpleNamespace(WHITE=rl.WHITE),
    "measure_text_cached": lambda font, text, size: rl.measure_text_ex(font, text, size, 0),
  }
  # One at a time: a few module constants reference car structs that are irrelevant here and
  # would otherwise take the whole block down with them.
  for node in (n for n in tree.body if isinstance(n, ast.Assign)):
    try:
      exec(compile(ast.Module(body=[node], type_ignores=[]), "<const>", "exec"), ns)
    except NameError:
      pass
  exec(compile(ast.Module(body=methods, type_ignores=[]), "<methods>", "exec"), ns)

  for required in ("HOLD_HEIGHT", "HOLD_FILL", "ACC_PILL_WIDTH", "ACC_STATUS_COLORS", "STACK_GAP",
                   "LAMP_PILL_WIDTH", "LAMP_ON_FILL", "HOLD_LOCKED_FILL"):
    assert required in ns, f"{required} did not survive extraction -- the preview would be a lie"
  return ns


def _text_w(font, s, size):
  return rl.measure_text_ex(font, s, size, 0).x


def _centered(font, s, size, cx, y, color):
  rl.draw_text_ex(font, s, rl.Vector2(cx - _text_w(font, s, size) / 2, y), size, 0, color)


def _draw_max_box(f_semi, f_bold, x, y, set_speed):
  r = rl.Rectangle(x, y, SET_W, SET_H)
  rl.draw_rectangle_rounded(r, 0.35, 10, rl.Color(0, 0, 0, 166))
  rl.draw_rectangle_rounded_lines_ex(r, 0.35, 10, 6, rl.Color(255, 255, 255, 75))
  _centered(f_semi, "MAX", 40, x + SET_W / 2, y + 27, rl.Color(128, 216, 166, 255))
  _centered(f_bold, str(set_speed), 90, x + SET_W / 2, y + 77, rl.WHITE)


def _draw_speed_limit_sign(f_semi, f_bold, x, y, limit):
  w = 200
  r = rl.Rectangle(x, y - 6, w, SET_H + 12)
  rl.draw_rectangle_rounded(r, 0.28, 10, rl.WHITE)
  rl.draw_rectangle_rounded_lines_ex(r, 0.28, 10, 6, rl.BLACK)
  _centered(f_semi, "SPEED", 40, r.x + w / 2, r.y + 22, rl.BLACK)
  _centered(f_semi, "LIMIT", 40, r.x + w / 2, r.y + 62, rl.BLACK)
  _centered(f_bold, str(limit), 90, r.x + w / 2, r.y + 112, rl.BLACK)


def main(outdir):
  os.makedirs(outdir, exist_ok=True)
  ns = load_shipped_drawing_code()
  print(f"HOLD_HEIGHT={ns['HOLD_HEIGHT']} "
        f"ACC_PILL={ns['ACC_PILL_WIDTH']}x{ns['ACC_PILL_HEIGHT']} STACK_GAP={ns['STACK_GAP']}")

  rl.set_config_flags(rl.ConfigFlags.FLAG_WINDOW_HIDDEN | rl.ConfigFlags.FLAG_MSAA_4X_HINT)
  rl.init_window(W, H, b"acc status preview")
  fonts = {}
  for key, name in (("bold", "Inter-Bold"), ("semi", "Inter-SemiBold"), ("med", "Inter-Medium")):
    fonts[key] = rl.load_font_ex(os.path.join(FONTS, f"{name}.ttf").encode(), 200, None, 0)
    rl.set_texture_filter(fonts[key].texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)

  tex = rl.load_render_texture(W, H)
  for i, scene in enumerate(SCENES):
    cap, set_speed, limit, hold, arrow, acc, mag, lamps, locked = scene[:9]
    tsr = scene[9] if len(scene) > 9 else ""
    pinned = scene[10] if len(scene) > 10 else False
    stub = types.SimpleNamespace(
      _font_bold=fonts["bold"], _font_semi_bold=fonts["semi"],
      _icbm_baseline=hold, _icbm_arrow=arrow, _acc_state=acc, _acc_accel=mag,
      _brakes_on=lamps, _show_brake_status=True, _lamp_data_available=True,
      _icbm_hold_locked=locked, _tsr_fault=tsr, _icbm_pinned=pinned, _hold_rect=None,
      _draw_arrow=ns["_draw_arrow"],
    )
    rl.begin_texture_mode(tex)
    # Mid-gray stands in for road: bright enough to catch anything relying on a dark backdrop.
    rl.draw_rectangle_gradient_v(0, 0, W, H, rl.Color(96, 100, 106, 255), rl.Color(52, 55, 60, 255))
    rl.draw_rectangle_gradient_v(0, 0, W, HEADER_H, rl.Color(0, 0, 0, 114), rl.Color(0, 0, 0, 0))

    x, y = 60, 45
    _draw_max_box(fonts["semi"], fonts["bold"], x, y, set_speed)
    _draw_speed_limit_sign(fonts["semi"], fonts["bold"], x + SET_W + 30 - 6, y, limit)

    cy = y + SET_H + 16
    if hold:
      cy += ns["_draw_hold_badge"](stub, x, cy, SET_W) + ns["STACK_GAP"]
    if acc:
      cy += ns["_draw_acc_pill"](stub, x, cy) + ns["STACK_GAP"]
    cy += ns["_draw_brake_lamp_pill"](stub, x, cy) + ns["STACK_GAP"]
    ns["_draw_tsr_pill"](stub, x, cy)

    rl.draw_text_ex(fonts["med"], cap, rl.Vector2(60, H - 70), 34, 0, rl.Color(255, 255, 255, 210))
    rl.end_texture_mode()

    img = rl.load_image_from_texture(tex.texture)
    rl.image_flip_vertical(img)
    path = os.path.join(outdir, f"acc_status_{i}.png")
    rl.export_image(img, path.encode())
    rl.unload_image(img)
    # Verify rather than announce. raylib's export_image returns void and only complains on
    # stderr, so a missing outdir produced six "wrote ..." lines and zero files -- a preview tool
    # that reports success on failure is worse than no preview tool.
    if not os.path.isfile(path):
      raise SystemExit(f"export failed: {path} was not written (see the raylib FILEIO warning)")
    print("wrote", path)
  rl.close_window()


if __name__ == "__main__":
  main(sys.argv[1] if len(sys.argv) > 1 else os.getcwd())
