import pyray as rl
from openpilot.common.params import Params
from opendbc.sunnypilot.car.ford.lateral_curv_ext import PrimaryLateralControl
from opendbc.car.structs import ControllerStateBP
from openpilot.selfdrive.ui.mici.onroad.hud_renderer import HudRenderer
from openpilot.selfdrive.ui.bp.mici.onroad.powerflow_gauge import MiciPowerflowGauge
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.selfdrive.ui.bp.lib.steering_wheel_style import (
  ensure_steering_wheel_icon_style_initialized,
  get_steering_wheel_icon_style,
  SteeringWheelIconStyle,
)
from openpilot.selfdrive.ui.bp.onroad.icbm_hud_state import read_icbm_hud_state
from openpilot.selfdrive.ui.bp.onroad.acc_hud_state import read_acc_hud_state
from openpilot.selfdrive.ui.bp.lib.ui_debug_logger import bp_ui_log
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.lib.application import gui_app
from openpilot.bluepilot.ui.lib.bp_shaders import draw_shader_circle_gradient
# BluePilot: Override upstream Mici torque bar with BP shared state math.
from openpilot.selfdrive.ui.bp.mici.onroad.torque_bar_bp import TorqueBarBP as TorqueBar

LateralMode = ControllerStateBP.LateralMode

# BluePilot: the HOLD badge on a 536x240 screen. Deliberately NOT the big screen's numbers -- see
# _draw_hold_badge. Colors ARE shared with it, so the same state reads the same on either display.
HOLD_LABEL = "HOLD"
HOLD_HEIGHT = 44
HOLD_MARGIN = 14          # from the top and right edges of the screen
# Wide enough to clear the pin dot, which is drawn inside this padding. Reserved in EVERY
# state, not only when pinned: sizing the pill to its contents would make it change width
# the moment a hold is pinned, and it is right-aligned, so the whole badge would jump.
HOLD_PAD_X = 24
HOLD_PAD_BOTTOM = 9       # text baseline inset, tuned against the render rather than guessed
HOLD_LABEL_GAP = 9
HOLD_LABEL_SIZE = 20
HOLD_VALUE_SIZE = 30
HOLD_DOT_INSET = 12
HOLD_DOT_RADIUS = 4
HOLD_FILL = rl.Color(30, 78, 176, 235)
HOLD_EDGE = rl.Color(130, 185, 255, 255)
HOLD_LABEL_COLOR = rl.Color(175, 210, 255, 255)
HOLD_LOCKED_FILL = rl.Color(84, 90, 98, 225)
HOLD_LOCKED_EDGE = rl.Color(140, 148, 156, 235)
HOLD_LOCKED_LABEL = rl.Color(178, 186, 194, 255)
HOLD_DOT_COLOR = rl.Color(255, 214, 90, 255)

# BluePilot: the stop lamps, under the HOLD badge. Same colors as the big screen's pill.
#
# Ported second, ahead of the ACC pill, because it is the only readout in that column that is a
# MEASURED FACT rather than a request -- StopLghtOn_B_Stat is the body module reporting actual lamp
# state, whatever lit them. The owner verified it against the ground behind the car at night before
# trusting it. Everything else on that stack is something the system WANTS.
LAMP_LABEL_ON = "BRAKE LAMPS ON"
LAMP_LABEL_OFF = "LAMPS OFF"
LAMP_HEIGHT = 32
LAMP_LABEL_SIZE = 18
LAMP_GAP = 8              # below the HOLD badge
LAMP_ON_FILL = rl.Color(228, 40, 40, 240)
LAMP_OFF_FILL = rl.Color(0, 0, 0, 150)
LAMP_OFF_EDGE = rl.Color(120, 126, 132, 190)
LAMP_OFF_INK = rl.Color(150, 156, 162, 255)

# BluePilot: what stock ACC is asking for. Same one green-to-red scale as the big screen, so the
# colour means the same thing on either display -- position on the scale IS the reading.
# ENG BRAKE sits deliberately OFF that scale in teal: it is the one state both slowing the car and
# costing nothing, no pads and no stop lamps, which does not fit a "how hard is it slowing" axis.
ACC_HEIGHT = 34
ACC_LABEL_SIZE = 19
ACC_VALUE_SIZE = 19
ACC_INK = rl.Color(10, 14, 20, 255)
ACC_QUIET_STATES = ("COAST", "PRE-BRAKE")     # no magnitude to report; the colour is the reading
ACC_STATUS_COLORS = {
  "ACCEL": rl.Color(70, 200, 115, 235),
  "COAST": rl.Color(196, 176, 70, 205),
  "ENG BRAKE": rl.Color(55, 185, 195, 235),
  "PRE-BRAKE": rl.Color(245, 145, 35, 235),
  "BRAKE": rl.Color(232, 58, 48, 240),
}


class MiciHudRendererBP(HudRenderer):
  """BluePilot MICI HudRenderer with brake status coloring and powerflow gauge."""

  def __init__(self):
    super().__init__()
    # BluePilot: HudRenderer initializes upstream TorqueBar; replace it with ours.
    self._torque_bar = TorqueBar()
    self._bp_params = Params()
    self._brakes_on = False
    self._lamp_data_available = False
    self._power_flow = MiciPowerflowGauge()
    self._txt_wheel_comma_3x = gui_app.texture("icons/chffr_wheel.png", self._txt_wheel.width, self._txt_wheel.height)
    self._animate_steering_wheel = self._bp_params.get_bool("BPAnimateSteeringWheel")
    self._wheel_icon_style = ensure_steering_wheel_icon_style_initialized(self._bp_params, SteeringWheelIconStyle.COMMA_4)
    self._animate_wheel_param_counter = 0
    self.show_lateral_control = False
    # BluePilot: actual mode from controllerStateBP (None = not published, e.g. non-Ford)
    self.lateral_mode = None
    # BluePilot: Track overlay hit-area for click-to-toggle
    self._overlay_center_x = 0
    self._overlay_center_y = 0
    self._overlay_size = 0
    # Latched PER READOUT on a drawing error; keeps a display bug off the screen without taking
    # the other readouts with it. See _render.
    self._readout_failed: dict[str, bool] = {}
    # Last drawn HOLD badge rect; the tap target for pinning. None = nothing to hit.
    self._hold_rect = None

  def _update_state(self) -> None:
    super()._update_state()

    # BluePilot: Refresh the shared wheel-animation toggle periodically.
    self._animate_wheel_param_counter += 1
    if self._animate_wheel_param_counter >= 60:
      self._animate_wheel_param_counter = 0
      self._animate_steering_wheel = self._bp_params.get_bool("BPAnimateSteeringWheel")
      self._wheel_icon_style = get_steering_wheel_icon_style(self._bp_params, SteeringWheelIconStyle.COMMA_4)

    if self._bp_params.get_bool("ShowBrakeStatus"):
      sm = ui_state.sm
      try:
        car_state_bp = sm['carStateBP']
        brake_light_status = car_state_bp.brakeLightStatus
        self._lamp_data_available = bool(brake_light_status.dataAvailable)
        self._brakes_on = brake_light_status.dataAvailable and brake_light_status.brakeLightsOn
      except (KeyError, AttributeError):
        self._brakes_on = self._lamp_data_available = False
    else:
      self._brakes_on = self._lamp_data_available = False

    self.show_lateral_control = self._bp_params.get_bool("BpShowLateralControl")
    if self.show_lateral_control:
      sm = ui_state.sm
      self.lateral_mode = sm['controllerStateBP'].activeLateralMode if sm.alive['controllerStateBP'] else None

    bp_ui_log.state("MiciHudRenderer", "brakes_on", self._brakes_on)

  def _render(self, rect: rl.Rectangle) -> None:
    """Render HUD elements to the screen."""
    self._torque_bar.render(rect)

    if self.is_cruise_set:
      self._draw_set_speed(rect)

    self._draw_steering_wheel(rect)
    # Each readout is latched OFF SEPARATELY. A shared latch meant one bad pill silently took the
    # other two with it, and the HOLD badge is the one worth keeping longest -- it shows a number
    # that exists nowhere else on screen.
    #
    # Guarded at all because the big screen's equivalent block had to be: `int()` on a capnp
    # _DynamicEnum once raised inside _update_state and CRASH-LOOPED THE UI, and because those
    # readouts only run when cruise is available, it only happened with the car on -- the hardest
    # kind of failure to attribute. These have the same shape: they read capnp enums and only draw
    # once cruise is engaged. A display bug must cost the readout, not the screen.
    below = self._safe_draw("hold", self._draw_hold_badge, rect,
                            fallback=rect.y + HOLD_MARGIN)
    below = self._safe_draw("acc", self._draw_acc_pill, rect, below, fallback=below)
    self._safe_draw("lamp", self._draw_brake_lamp_pill, rect, below, fallback=below)

  def _safe_draw(self, name: str, draw, *args, fallback):
    """Draw one readout, latching it off for the session if it ever raises.

    Returns `fallback` when the readout is latched off or throws, so the stack below it closes up
    rather than leaving a gap where a failed pill would have been.
    """
    if self._readout_failed.get(name):
      return fallback
    try:
      result = draw(*args)
    except Exception as e:  # noqa: BLE001 -- the screen outranks any one readout
      self._readout_failed[name] = True
      # A latched-off badge leaves no tap target behind. _hold_rect is only cleared by the badge's
      # own no-hold path, so without this a badge that threw once would stop drawing while its last
      # rectangle kept firing pin requests -- an invisible button, which is worse than none.
      if name == "hold":
        self._hold_rect = None
      bp_ui_log.state("MiciHudRenderer", f"{name}_readout_error", repr(e))
      return fallback
    return fallback if result is None else result

  def _draw_hold_badge(self, rect: rl.Rectangle) -> float:
    """The driver's own set speed, on a 536x240 screen.

    REDESIGNED, not scaled. The big screen's badge is 172x124 sized against a 204 px MAX box and
    stacks three more readouts under it -- on a 240 px tall screen that box alone is most of the
    display. So this is a single horizontal pill in the top-right, the one corner mici leaves free:
    the set-speed box owns the top-left (a 162 px circle) and the steering wheel the bottom-left.

    Horizontal rather than the big screen's label-over-number, because at this size stacking two
    lines inside 44 px leaves neither legible. Reading "HOLD 70" across is worth more here than
    matching the other screen's shape.

    WHY THIS ONE FIRST, of the four readouts the big screen has. It is the only one showing a number
    that exists nowhere else: the speed ICBM returns to once a curve or limit has passed. The ACC and
    lamp pills describe things the driver can also feel through the car; a hold is invisible without
    it, and the owner has twice reported being unable to tell whether an override had taken.

    No tap target here. On the big screen this badge is the pin/unpin control, but mici's touch
    handling lives in a different tree and a control that silently does nothing is worse than none.
    """
    hold = read_icbm_hud_state(ui_state.sm)
    # worth_showing, not has_hold: without Speed Limit Assist the hold IS the MAX speed, so a
    # second readout of the same number is a concept the driver has to learn for nothing.
    if not hold.worth_showing:
      self._hold_rect = None           # no badge on screen, no tap target
      return rect.y + HOLD_MARGIN      # nothing drawn; the stack closes up

    value = str(hold.baseline)
    label_w = measure_text_cached(self._font_semi_bold, HOLD_LABEL, HOLD_LABEL_SIZE).x
    value_w = measure_text_cached(self._font_bold, value, HOLD_VALUE_SIZE).x
    width = HOLD_PAD_X * 2 + label_w + HOLD_LABEL_GAP + value_w
    x = rect.x + rect.width - HOLD_MARGIN - width
    y = rect.y + HOLD_MARGIN

    locked = hold.hold_locked
    box = rl.Rectangle(x, y, width, HOLD_HEIGHT)
    rl.draw_rectangle_rounded(box, 0.4, 10, HOLD_LOCKED_FILL if locked else HOLD_FILL)
    rl.draw_rectangle_rounded_lines_ex(box, 0.4, 10, 3, HOLD_LOCKED_EDGE if locked else HOLD_EDGE)

    # Baseline-align the two texts rather than centring each in the pill: the label is 20 px and the
    # value 30, and centring them independently makes the word visibly float above the number.
    baseline = y + HOLD_HEIGHT - HOLD_PAD_BOTTOM
    rl.draw_text_ex(self._font_semi_bold, HOLD_LABEL,
                    rl.Vector2(x + HOLD_PAD_X, baseline - HOLD_LABEL_SIZE),
                    HOLD_LABEL_SIZE, 0, HOLD_LOCKED_LABEL if locked else HOLD_LABEL_COLOR)
    rl.draw_text_ex(self._font_bold, value,
                    rl.Vector2(x + HOLD_PAD_X + label_w + HOLD_LABEL_GAP,
                               baseline - HOLD_VALUE_SIZE),
                    HOLD_VALUE_SIZE, 0, rl.WHITE)

    # A pinned hold gets a dot on the LEFT edge, clear of the arrow that hangs off the right. The
    # big screen learned that the hard way -- arrow and dot landed within a pixel of each other.
    if hold.pinned:
      rl.draw_circle(int(x + HOLD_DOT_INSET), int(y + HOLD_HEIGHT / 2), HOLD_DOT_RADIUS, HOLD_DOT_COLOR)
    elif hold.pin_suggested:
      rl.draw_ring(rl.Vector2(x + HOLD_DOT_INSET, y + HOLD_HEIGHT / 2),
                   HOLD_DOT_RADIUS - 2, HOLD_DOT_RADIUS, 0, 360, 20, HOLD_DOT_COLOR)
    return y + HOLD_HEIGHT + LAMP_GAP

  def _draw_acc_pill(self, rect: rl.Rectangle, y: float) -> float:
    """What stock Ford ACC is asking for, between the hold and the lamps.

    Ordered this way for a reason, and it is the big screen's order: HOLD is the number that exists
    nowhere else, ACC is what the system WANTS, lamps are what is measurably true. Request above
    fact, because the request is the thing you cannot otherwise see.

    Filled rather than outlined, because the colour is the reading -- one green-to-red scale for how
    much the car is slowing, with ENG BRAKE off that scale in teal since it slows the car for free.
    COAST is muted on purpose: it is on screen most of the time, and a bright resting state trains
    you to stop looking.

    Returns the y the next readout should use, so the stack closes up when this is absent.
    """
    acc = read_acc_hud_state(ui_state.sm)
    if not acc.has_state:
      return y

    label = acc.state
    show_value = label not in ACC_QUIET_STATES and abs(acc.accel) >= 0.05
    value = f"{abs(acc.accel):.1f}" if show_value else ""

    label_w = measure_text_cached(self._font_semi_bold, label, ACC_LABEL_SIZE).x
    value_w = measure_text_cached(self._font_bold, value, ACC_VALUE_SIZE).x if show_value else 0.0
    gap = HOLD_LABEL_GAP if show_value else 0.0
    width = HOLD_PAD_X * 2 + label_w + gap + value_w
    x = rect.x + rect.width - HOLD_MARGIN - width

    rl.draw_rectangle_rounded(rl.Rectangle(x, y, width, ACC_HEIGHT), 0.45, 10,
                              ACC_STATUS_COLORS.get(label, LAMP_OFF_FILL))
    rl.draw_text_ex(self._font_semi_bold, label,
                    rl.Vector2(x + HOLD_PAD_X, y + (ACC_HEIGHT - ACC_LABEL_SIZE) / 2 - 1),
                    ACC_LABEL_SIZE, 0, ACC_INK)
    if show_value:
      rl.draw_text_ex(self._font_bold, value,
                      rl.Vector2(x + HOLD_PAD_X + label_w + gap,
                                 y + (ACC_HEIGHT - ACC_VALUE_SIZE) / 2 - 1),
                      ACC_VALUE_SIZE, 0, ACC_INK)
    return y + ACC_HEIGHT + LAMP_GAP

  def _draw_brake_lamp_pill(self, rect: rl.Rectangle, y: float) -> None:
    """Are the stop lamps lit right now -- the one readout here that is measured, not requested.

    Drawn in BOTH states, deliberately, and that is not padding: an indicator that only appears when
    lit cannot be told apart from one that is broken, and "are my lamps on" is a question about both
    answers. It draws only when the car is actually reporting lamp state, so a silent bus shows
    nothing rather than a confident OFF.

    Right-aligned under the HOLD badge, and it slides up into the badge's place when there is no
    hold -- on a 240 px screen a reserved empty slot is a luxury.
    """
    if not self._lamp_data_available:
      return

    lit = self._brakes_on
    label = LAMP_LABEL_ON if lit else LAMP_LABEL_OFF
    text_w = measure_text_cached(self._font_semi_bold, label, LAMP_LABEL_SIZE).x
    width = text_w + HOLD_PAD_X * 2
    x = rect.x + rect.width - HOLD_MARGIN - width

    box = rl.Rectangle(x, y, width, LAMP_HEIGHT)
    rl.draw_rectangle_rounded(box, 0.45, 10, LAMP_ON_FILL if lit else LAMP_OFF_FILL)
    if not lit:
      rl.draw_rectangle_rounded_lines_ex(box, 0.45, 10, 2, LAMP_OFF_EDGE)
    rl.draw_text_ex(self._font_semi_bold, label,
                    rl.Vector2(x + HOLD_PAD_X, y + (LAMP_HEIGHT - LAMP_LABEL_SIZE) / 2 - 1),
                    LAMP_LABEL_SIZE, 0, rl.WHITE if lit else LAMP_OFF_INK)

  def _draw_steering_wheel(self, rect: rl.Rectangle) -> None:
    """Override to add brake status coloring to wheel icon, powerflow gauge, and lateral control overlay."""
    normal_wheel_txt = self._txt_wheel_comma_3x if self._wheel_icon_style == SteeringWheelIconStyle.COMMA_3X else self._txt_wheel
    # BluePilot: Preserve the upstream critical-alert wheel regardless of the user's normal wheel style.
    wheel_txt = self._txt_wheel_critical if self._show_wheel_critical else normal_wheel_txt

    bsm_detected = self._has_blind_spot_detected() if hasattr(self, '_has_blind_spot_detected') else False

    show_lateral = True

    if self._show_wheel_critical:
      self._wheel_alpha_filter.update(255)
      self._wheel_y_filter.update(0)
    else:
      if ui_state.status == UIStatus.DISENGAGED or bsm_detected:
        self._wheel_alpha_filter.update(0)
        self._wheel_y_filter.update(wheel_txt.height / 2)
        show_lateral = False
      else:
        self._wheel_alpha_filter.update(255 * 0.9)
        self._wheel_y_filter.update(0)

    pos_x = int(rect.x + 21 + wheel_txt.width / 2)
    pos_y = int(rect.y + rect.height - 14 - wheel_txt.height / 2 + self._wheel_y_filter.x)
    rotation = -ui_state.sm['carState'].steeringAngleDeg if self._animate_steering_wheel else 0.0

    turn_intent_margin = 25
    self._turn_intent.render(rl.Rectangle(
      pos_x - wheel_txt.width / 2 - turn_intent_margin,
      pos_y - wheel_txt.height / 2 - turn_intent_margin,
      wheel_txt.width + turn_intent_margin * 2,
      wheel_txt.height + turn_intent_margin * 2,
    ))

    src_rect = rl.Rectangle(0, 0, wheel_txt.width, wheel_txt.height)
    dest_rect = rl.Rectangle(pos_x, pos_y, wheel_txt.width, wheel_txt.height)
    origin = (wheel_txt.width / 2, wheel_txt.height / 2)

    # BluePilot: Red color when braking
    if self._brakes_on:
      color = rl.Color(255, 60, 60, int(self._wheel_alpha_filter.x))
    else:
      color = rl.Color(255, 255, 255, int(self._wheel_alpha_filter.x))
    rl.draw_texture_pro(wheel_txt, src_rect, dest_rect, origin, rotation, color)

    if self._show_wheel_critical:
      EXCLAMATION_POINT_SPACING = 10
      exclamation_pos_x = pos_x - self._txt_exclamation_point.width / 2 + wheel_txt.width / 2 + EXCLAMATION_POINT_SPACING
      exclamation_pos_y = pos_y - self._txt_exclamation_point.height / 2
      rl.draw_texture(self._txt_exclamation_point, int(exclamation_pos_x), int(exclamation_pos_y), rl.WHITE)

    if show_lateral:
      self._draw_lateral_control_overlay(pos_x, pos_y, wheel_txt.width)

    # BluePilot: Render powerflow gauge around steering wheel
    power_flow_radius = self._power_flow.RADIUS
    power_rect = rl.Rectangle(
      int(rect.x + 21) - power_flow_radius,
      int(rect.y + rect.height - wheel_txt.height - 14) - power_flow_radius,
      wheel_txt.width + power_flow_radius * 2,
      wheel_txt.height + power_flow_radius * 2)
    self._power_flow.set_wheel_rect(power_rect)
    self._power_flow.render(rect)

  def _draw_lateral_control_overlay(self, center_x: int, center_y: int, wheel_size: int) -> None:
    """Draw a letter overlay indicating current lateral control mode (only when wheel is visible)."""
    if not self.show_lateral_control or self._wheel_alpha_filter.x <= 0 or self.lateral_mode is None:
      self._overlay_size = 0
      return

    text_size = int(wheel_size * 0.65)
    self._overlay_center_x = center_x
    self._overlay_center_y = center_y
    self._overlay_size = text_size

    if self.lateral_mode == LateralMode.angle:
      letter, color = "A", rl.Color(50, 100, 255, 220)  # Blue-ish
    elif self.lateral_mode == LateralMode.curvature:
      letter, color = "C", rl.Color(255, 165, 0, 220)  # Orange
    else:
      letter, color = "OP", rl.Color(100, 100, 100, 220)  # Grey

    text_dims = measure_text_cached(self._font_bold, letter, text_size)
    text_x = center_x - text_dims.x / 2
    text_y = center_y - text_dims.y / 2

    top = rl.Color(250, 250, 250, 200)
    bottom = rl.Color(200, 200, 200, 200)
    draw_shader_circle_gradient(center_x, center_y, text_size / 2, top, bottom)

    rl.draw_text_ex(self._font_bold, letter, rl.Vector2(text_x, text_y), text_size, 0, color)

  def _handle_mouse_press(self, mouse_pos):
    """Tap the HOLD badge to pin this hold to this place, or unpin it. Then the lateral overlay.

    CHECKED FIRST, and it consumes the event. The big screen learned this the hard way: it called
    super() before its own hit test, so every badge tap also reached upstream's handler and slid the
    sidebar out. The pin request was still raised underneath, but the menu is what the driver sees,
    so the gesture read as dead. Checking our own target first is what makes it a button rather
    than a side effect of a tap that also does something else.

    Only a REQUEST is raised. selfdrived does the work, because that is where the GPS fix and the
    live baseline are; the UI has neither and must not grow a second copy of either.

    The badge is the target because it is already the thing on screen that means "hold", and
    because the cruise buttons are full -- every one carries a settled meaning, and adding a gesture
    would mean relearning one to gain a rare action.
    """
    if self._hold_rect is not None and rl.check_collision_point_rec(mouse_pos, self._hold_rect):
      gui_app._mouse_events.clear()
      try:
        self._bp_params.put_bool("IcbmPinHoldRequest", True)
      except Exception:  # noqa: BLE001 -- a failed pin must not take the on-road screen down
        bp_ui_log.state("MiciHudRenderer", "pin_request_failed", True)
      return

    if self._overlay_size <= 0 or self.lateral_mode not in (LateralMode.curvature, LateralMode.angle):
      return

    hit_rect = rl.Rectangle(
      self._overlay_center_x - self._overlay_size/2,
      self._overlay_center_y - self._overlay_size/2,
      self._overlay_size,
      self._overlay_size,
    )
    if rl.check_collision_point_rec(mouse_pos, hit_rect):
      gui_app._mouse_events.clear()
      current = PrimaryLateralControl(self._bp_params.get("FordPrefLateralControl") or 0)
      new_value = PrimaryLateralControl.curvature if current == PrimaryLateralControl.angle else PrimaryLateralControl.angle
      self._bp_params.put("FordPrefLateralControl", int(new_value))
