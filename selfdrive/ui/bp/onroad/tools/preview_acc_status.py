#!/usr/bin/env python3
"""Render the HOLD badge and Ford ACC pill to PNG, offline, at device scale.

FusionPilot: these two readouts sit under the MAX box, and until now the only way to judge their
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
SP_HUD = os.path.join(ROOT, "selfdrive", "ui", "sunnypilot", "onroad", "hud_renderer.py")
FONTS = os.path.join(ROOT, "selfdrive", "assets", "fonts")

sys.path.insert(0, ROOT)
# The pure rule that decides what the box shows. Imported for real -- it has no raylib or ui_state
# dependency precisely so that it can be, and the preview must not re-derive the ranking.
from openpilot.selfdrive.ui.bp.onroad.icbm_hud_state import max_box_state  # noqa: E402

W, H = 1120, 1080          # the left portion of the 2160x1080 display; all of this lives there
HEADER_H = 300
SET_W, SET_H = 172, 204    # UI_CONFIG.set_speed_width_imperial / set_speed_height

# DICTS, NOT TUPLES, since 2026-08-22. The old tuples had one `hold` column doing two jobs -- the
# badge's `display_value` was the hold OR the pin being offered, so an offer scene was written as
# "hold 45". With the badge gone those are different inputs to `max_box_state` and conflating them
# renders the wrong thing: a hold of 45 tints the box and suppresses the offer ring entirely.
#
#   dash    what the car is DOING; the label slot shows it whenever it differs from the aim
#   limit   the posted limit, 0 for a road with none
#   hold    the driver's own held speed, 0 for none
#   offer   a pin being suggested here. Mutually exclusive with `hold` by construction.
SCENES = [
  # HIS PHOTO, 2026-08-22. Hold 35, posted 30, offset +5 -- so the fallback is also 35 and rank 2
  # used to draw "35" over "35" in blue with no word on it. He could not tell whether the hold was
  # still up, which is the one question this box exists to answer.
  dict(cap="hold equals the SLA fallback -- says HOLD, not 35 over 35",
       dash=35, limit=30, fallback=35, hold=35, acc="COAST", mag=0.0, lamps=False),
  dict(cap="settled, holding 70 in a 55 -- lamps dark",
       dash=70, limit=55, hold=70, acc="COAST", mag=0.0, lamps=False),
  dict(cap="curve: ACC braking hard enough to light the lamps",
       dash=44, limit=55, hold=70, acc="BRAKE", mag=1.4, lamps=True, locked=True),
  dict(cap="ACC braking too lightly to light them",
       dash=50, limit=55, hold=70, acc="BRAKE", mag=0.4, lamps=False),
  dict(cap="precharging: no decel, no lamps, no pads",
       dash=58, limit=55, hold=70, acc="PRE-BRAKE", mag=0.0, lamps=False),
  dict(cap="engine braking: slowing, no pads, no lamps",
       dash=52, limit=55, hold=70, acc="ENG BRAKE", mag=0.9, lamps=False),
  dict(cap="no hold, ACC accelerating -- the box is SLA's number",
       dash=55, limit=55, hold=0, acc="ACCEL", mag=0.6, lamps=False),
  dict(cap="TSR not working -- the camera's own reason",
       dash=70, limit=55, hold=70, acc="COAST", mag=0.0, lamps=False, tsr="TSR REGION N/A"),
  dict(cap="hold pinned to this place -- dot in the corner, tap the box to unpin",
       dash=45, limit=55, hold=45, acc="COAST", mag=0.0, lamps=False, pinned=True),
  dict(cap="worst case today: every readout at once, TSR still down",
       dash=70, limit=55, hold=70, acc="BRAKE", mag=1.4, lamps=True, locked=True,
       tsr="TSR NO NAV DATA", pinned=True),
  dict(cap="you have set a hold here before -- ring, and the offer in the label",
       dash=45, limit=55, hold=0, acc="COAST", mag=0.0, lamps=False, offer=45),
  # No posted limit and no hold, so the box is showing wherever SET left him. The ring is the only
  # tap target that can accept a pin, and the label is now the only thing that can say what speed
  # is on offer -- the badge used to carry that number.
  # Deliberately a DIFFERENT number from the aim: an offer that happens to equal what the car is
  # already doing renders as "45 over 45" and proves nothing about whether the slot is readable.
  dict(cap="no limit here: nothing held, a 45 offered -- tap the ring to keep it",
       dash=55, limit=0, hold=0, acc="COAST", mag=0.0, lamps=False, offer=45),
  # A HOLD WITH NO LIMIT: rank 2 has no fallback to offer, so the label falls through to HOLD and
  # the box tints. His common case on the roads holds are actually for.
  dict(cap="hold with no posted limit -- labelled HOLD, tinted, his own number",
       dash=45, limit=0, hold=45, acc="COAST", mag=0.0, lamps=False),
  # FusionPilot: the stop override. openpilot has taken the command from Ford for a few seconds to
  # finish a stop the set speed could not ask for. Violet so it does not read as "more braking" --
  # it is a different AUTHOR, which on this car is the thing worth seeing.
  dict(cap="stop override: openpilot is braking, not Ford",
       dash=20, limit=25, hold=0, acc="OP STOP", mag=2.1, lamps=True),
  dict(cap="the same moment with a hold and the lamps lit",
       dash=20, limit=25, hold=45, acc="OP STOP", mag=2.6, lamps=True),
]


def load_shipped_drawing_code():
  """Lift the constants and drawing methods out of the source file.

  Importing the module would pull in the whole UI stack -- ui_state, Params, gui_app, the SP and
  upstream renderer base classes -- for three functions whose only dependencies are raylib and
  attributes on self.
  """
  tree = ast.parse(open(HUD, encoding="utf-8").read())
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "HudRendererBP")
  # `_draw_hold_badge` and `_draw_arrow` were deleted on 2026-08-22 with the badge. The hold is
  # drawn by the SET-SPEED BOX now, which lives in the sunnypilot renderer -- see `_draw_max_box`.
  wanted = ("_draw_acc_pill", "_draw_brake_lamp_pill", "_draw_tsr_pill")
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
  # The box's own palette moved to the sunnypilot renderer with the pin mark. Pulled from the
  # SOURCE rather than copied, so a color change there cannot leave this preview showing the old
  # one -- which is the whole reason the tool is trusted.
  sp_tree = ast.parse(open(SP_HUD, encoding="utf-8").read())
  for node in (n for n in sp_tree.body if isinstance(n, ast.Assign)):
    try:
      exec(compile(ast.Module(body=[node], type_ignores=[]), "<sp-const>", "exec"), ns)
    except NameError:
      pass
  exec(compile(ast.Module(body=methods, type_ignores=[]), "<methods>", "exec"), ns)

  for required in ("ACC_PILL_WIDTH", "ACC_STATUS_COLORS", "STACK_GAP", "LAMP_PILL_WIDTH",
                   "LAMP_ON_FILL", "PIN_DOT_RADIUS", "PIN_DOT_COLOR", "HOLD_DRIVING_COLOR"):
    assert required in ns, f"{required} did not survive extraction -- the preview would be a lie"
  return ns


def _text_w(font, s, size):
  return rl.measure_text_ex(font, s, size, 0).x


def _centered(font, s, size, cx, y, color):
  rl.draw_text_ex(font, s, rl.Vector2(cx - _text_w(font, s, size) / 2, y), size, 0, color)


def _draw_max_box(f_semi, f_bold, x, y, box, ns):
  """The set-speed box, which since 2026-08-22 is where the HOLD lives.

  MIRRORS `HudRendererSP._draw_set_speed` rather than extracting it: that method starts by reading
  two capnp messages off `ui_state.sm` and resolving colors from `UIStatus`, none of which exists
  here. What it does NOT re-derive is the part that decides anything -- `box` comes from the real
  `max_box_state`, and the palette is pulled out of the real source file. So a change to the RULE
  or to a COLOR shows up here; a change to the pixel offsets is the one thing that could drift,
  which is why they are quoted from the shipped method beside each call.
  """
  r = rl.Rectangle(x, y, SET_W, SET_H)
  rl.draw_rectangle_rounded(r, 0.35, 10, rl.Color(0, 0, 0, 166))
  rl.draw_rectangle_rounded_lines_ex(r, 0.35, 10, 6, rl.Color(255, 255, 255, 75))

  max_color = rl.Color(128, 216, 166, 255)
  aim_color = rl.WHITE
  if box.hold_driving and not box.hold_locked:
    aim_color = ns["HOLD_DRIVING_COLOR"]
    max_color = ns["HOLD_DRIVING_COLOR"]

  # max_str_size / max_str_y, straight out of the shipped method.
  size, ly = (60, 15) if box.label_is_number else (40, 27)
  _centered(f_semi, box.label, size, x + SET_W / 2, y + ly, max_color)
  _centered(f_bold, str(round(box.aim)), 90, x + SET_W / 2, y + 77, aim_color)

  if box.pinned:
    rl.draw_circle(int(x + 20), int(y + 20), ns["PIN_DOT_RADIUS"], ns["PIN_DOT_COLOR"])
  elif box.pin_offer:
    rl.draw_ring(rl.Vector2(x + 20, y + 20), ns["PIN_DOT_RADIUS"] - 3, ns["PIN_DOT_RADIUS"],
                 0, 360, 24, ns["PIN_DOT_COLOR"])


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
  print(f"ACC_PILL={ns['ACC_PILL_WIDTH']}x{ns['ACC_PILL_HEIGHT']} STACK_GAP={ns['STACK_GAP']} "
        f"PIN_DOT_RADIUS={ns['PIN_DOT_RADIUS']}")

  rl.set_config_flags(rl.ConfigFlags.FLAG_WINDOW_HIDDEN | rl.ConfigFlags.FLAG_MSAA_4X_HINT)
  rl.init_window(W, H, b"acc status preview")
  fonts = {}
  for key, name in (("bold", "Inter-Bold"), ("semi", "Inter-SemiBold"), ("med", "Inter-Medium")):
    fonts[key] = rl.load_font_ex(os.path.join(FONTS, f"{name}.ttf").encode(), 200, None, 0)
    rl.set_texture_filter(fonts[key].texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)

  tex = rl.load_render_texture(W, H)
  for i, scene in enumerate(SCENES):
    cap = scene["cap"]
    dash, limit, hold = scene["dash"], scene["limit"], scene["hold"]
    acc, mag, lamps = scene["acc"], scene["mag"], scene["lamps"]
    tsr = scene.get("tsr", "")
    # THE REAL RULE DECIDES WHAT THE BOX SAYS. Re-deriving the ranking here is how a preview starts
    # agreeing with itself instead of with the car.
    # THE SIGN AND THE FALLBACK ARE DIFFERENT NUMBERS and every scene here conflated them until
    # 2026-08-22. The sign shows the POSTED limit; the fallback SLA would hand back is that plus his
    # offset, which is +5. His screen had posted 30 and a fallback of 35, and passing 30 for both is
    # why the preview could not reproduce what he was looking at.
    fallback = scene.get("fallback", limit)
    box = max_box_state(hold, fallback or None, dash, dash,
                        pin_suggestion=scene.get("offer", 0),
                        pinned=scene.get("pinned", False),
                        hold_locked=scene.get("locked", False))
    stub = types.SimpleNamespace(
      _font_bold=fonts["bold"], _font_semi_bold=fonts["semi"],
      _acc_state=acc, _acc_accel=mag,
      _brakes_on=lamps, _show_brake_status=True, _lamp_data_available=True,
      _tsr_fault=tsr,
    )
    rl.begin_texture_mode(tex)
    # Mid-gray stands in for road: bright enough to catch anything relying on a dark backdrop.
    rl.draw_rectangle_gradient_v(0, 0, W, H, rl.Color(96, 100, 106, 255), rl.Color(52, 55, 60, 255))
    rl.draw_rectangle_gradient_v(0, 0, W, HEADER_H, rl.Color(0, 0, 0, 114), rl.Color(0, 0, 0, 0))

    x, y = 60, 45
    _draw_max_box(fonts["semi"], fonts["bold"], x, y, box, ns)
    if limit:
      _draw_speed_limit_sign(fonts["semi"], fonts["bold"], x + SET_W + 30 - 6, y, limit)

    # The badge used to be the first thing in this stack. With it gone the ACC pill sits directly
    # under the box, which is the visible half of the change and the reason to look at these PNGs.
    cy = y + SET_H + 16
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
