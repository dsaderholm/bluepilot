import pyray as rl
from openpilot.common.params import Params
from opendbc.car.structs import ControllerStateBP
from openpilot.bluepilot.ui.lib.bp_shaders import draw_shader_circle_gradient
from openpilot.selfdrive.ui.onroad.hud_renderer import UI_CONFIG, FONT_SIZES, COLORS
from openpilot.selfdrive.ui.sunnypilot.onroad.hud_renderer import HudRendererSP
from openpilot.selfdrive.ui.bp.onroad.exp_button_bp import ExpButtonBP
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.selfdrive.ui.bp.lib.ui_debug_logger import bp_ui_log

LateralMode = ControllerStateBP.LateralMode

# BluePilot: Y center for speed display (matching upstream hardcoded values)
SPEED_CENTER_Y = 180
SPEED_UNIT_CENTER_Y = 290

# BluePilot: below this the propulsion request reads as coasting rather than accelerating. ACC
# trims constantly at small values; with no deadband the readout would never sit still.
ACC_DEADBAND = 0.15  # m/s^2
ACC_STATUS_COLORS = {
  "ACCEL": rl.Color(80, 200, 120, 255),
  "COAST": rl.Color(190, 190, 190, 255),
  "BRAKE": rl.Color(255, 180, 40, 255),
}


class HudRendererBP(HudRendererSP):
  """BluePilot HudRenderer with brake status display.

  Note: Torque bar is rendered by TorqueBarRendererBP in AugmentedRoadViewBP,
  not here. This keeps the torque bar above gauges in draw order and allows
  repositioning above the battery/power flow gauges.
  """

  def __init__(self):
    super().__init__()
    # BluePilot: Restore the animated C3X wheel without modifying the upstream ExpButton.
    self._exp_button = ExpButtonBP(UI_CONFIG.button_size, UI_CONFIG.wheel_icon_size)
    self._bp_params = Params()
    self._brakes_on = False
    # BluePilot: Ford ACC asking for brakes, which is not the same event as the lamps lighting.
    # Light applications decelerate without ever reaching the stop-lamp threshold.
    self._acc_braking = False
    # BluePilot: what Ford ACC is asking for, and what ICBM is doing about it. The speed colours
    # above say what traffic behind you sees; these say what the systems are requesting. Those are
    # different facts, which is why this is a separate readout rather than more colours.
    self._acc_state = ""      # "ACCEL" / "COAST" / "BRAKE", "" when unknown
    self._acc_accel = 0.0     # m/s^2, signed
    self._icbm_text = ""      # "" when ICBM has nothing to say
    self._acc_status_failed = False   # latched on any error; keeps a display bug off the screen
    self.speed_right = 0
    self._gradient_rect = None  # BluePilot: Full-width rect for header gradient

    # BluePilot: Cache params to avoid per-frame disk I/O (refresh every ~60 frames)
    self._param_counter = 0
    self._show_brake_status = self._bp_params.get_bool("ShowBrakeStatus")
    self._hide_v_ego_ui = self._bp_params.get_bool("HideVEgoUI")
    self._show_lateral_control = self._bp_params.get_bool("BpShowLateralControl")
    # BluePilot: actual mode from controllerStateBP (None = not published, e.g. non-Ford)
    self._lateral_mode = None

  def set_gradient_rect(self, rect: rl.Rectangle):
    """Set full-width rect for header gradient (when HUD renders offset for confidence ball)."""
    self._gradient_rect = rect

  def get_speed_right(self) -> int:
    return self.speed_right

  def _update_state(self) -> None:
    super()._update_state()

    # BluePilot: Refresh cached params periodically (~1s at 20fps)
    self._param_counter += 1
    if self._param_counter >= 60:
      self._param_counter = 0
      self._show_brake_status = self._bp_params.get_bool("ShowBrakeStatus")
      self._hide_v_ego_ui = self._bp_params.get_bool("HideVEgoUI")
      self._show_lateral_control = self._bp_params.get_bool("BpShowLateralControl")

    if self._show_lateral_control:
      sm = ui_state.sm
      self._lateral_mode = sm['controllerStateBP'].activeLateralMode if sm.alive['controllerStateBP'] else None

    # Check brake status if enabled
    if self._show_brake_status:
      sm = ui_state.sm
      if sm.valid['carStateBP']:
        try:
          car_state_bp = sm['carStateBP']
          brake_light_status = car_state_bp.brakeLightStatus
          self._brakes_on = brake_light_status.dataAvailable and brake_light_status.brakeLightsOn
          self._acc_braking = (brake_light_status.accDataAvailable and
                               (brake_light_status.accDecelRequest or brake_light_status.accPrechargeRequest))
        except (KeyError, AttributeError):
          self._brakes_on = False
          self._acc_braking = False
      else:
        self._brakes_on = False
        self._acc_braking = False
    else:
      self._brakes_on = False
      self._acc_braking = False

    # BluePilot: a cosmetic readout must never be able to take the screen down. This one did --
    # int() on a capnp _DynamicEnum raised TypeError inside _update_state, which crash-looped the
    # UI, and because it only runs when cruise is available it only happened with the car on.
    # Anything unexpected here now disables the readout for the session instead of the display.
    if not self._acc_status_failed:
      try:
        self._update_acc_status()
      except Exception as e:
        self._acc_status_failed = True
        self._acc_state, self._acc_accel, self._icbm_text = "", 0.0, ""
        bp_ui_log.state("HudRendererBP", "acc_status_error", repr(e))

    bp_ui_log.state("HudRendererBP", "brakes_on", self._brakes_on)
    bp_ui_log.state("HudRendererBP", "acc_braking", self._acc_braking)
    bp_ui_log.state("HudRendererBP", "acc_state", self._acc_state)

  def _update_acc_status(self) -> None:
    """BluePilot: is Ford ACC accelerating, coasting or braking, and is ICBM moving the set speed?

    Read from the stock ACCDATA the camera sends, so this is Ford's own request even though
    openpilot is not the longitudinal controller.
    """
    self._acc_state, self._acc_accel, self._icbm_text = "", 0.0, ""
    sm = ui_state.sm

    # BluePilot: the ICBM line is NOT gated on the brake-status toggle. Whether ICBM is holding
    # the driver's own set speed or chasing Speed Limit Assist is basic state, not a debug
    # readout -- and hiding it behind an unrelated toggle meant the driver spent days unable to
    # see whether an override had taken at all. The ACC accel/coast/brake line below stays behind
    # the toggle; that one really is diagnostic.
    try:
      icbm = sm['selfdriveStateSP'].intelligentCruiseButtonManagement
      arrow = {1: "+", 2: "-"}.get(icbm.sendButton.raw, "")
      if icbm.overrideState.raw == 1 and icbm.vBaseline > 0:
        self._icbm_text = f"{arrow} HOLD {round(icbm.vBaseline)}".strip()
      elif arrow:
        self._icbm_text = arrow
    except Exception:
      pass

    if not self._show_brake_status:
      return

    if sm.valid['carStateBP']:
      try:
        bls = sm['carStateBP'].brakeLightStatus
        if bls.accDataAvailable:
          # The friction-brake bits win outright: they mean the pads are being used, whatever the
          # propulsion request says. Otherwise the two m/s^2 requests decide between them.
          # accAccelRequest is AccBrkTot_A_Rq -- the BRAKE total, despite the name -- so it cannot
          # tell accelerating from coasting on its own. That is what accPropulsionRequest is for.
          if bls.accDecelRequest or bls.accPrechargeRequest:
            self._acc_state, self._acc_accel = "BRAKE", bls.accAccelRequest
          elif bls.accPropulsionRequest > ACC_DEADBAND:
            self._acc_state, self._acc_accel = "ACCEL", bls.accPropulsionRequest
          elif bls.accAccelRequest < -ACC_DEADBAND:
            self._acc_state, self._acc_accel = "BRAKE", bls.accAccelRequest
          else:
            self._acc_state, self._acc_accel = "COAST", 0.0
      except Exception:
        pass


  def _render(self, rect: rl.Rectangle) -> None:
    # BluePilot: Draw header gradient at full content width (not offset by confidence ball)
    gradient_rect = self._gradient_rect if self._gradient_rect else rect
    rl.draw_rectangle_gradient_v(
      int(gradient_rect.x), int(gradient_rect.y), int(gradient_rect.width),
      UI_CONFIG.header_height,
      COLORS.HEADER_GRADIENT_START, COLORS.HEADER_GRADIENT_END,
    )

    # HUD elements use the (possibly offset) rect for positioning
    if self.is_cruise_available:
      self._draw_set_speed(rect)
      self._draw_acc_status(rect)
    self._draw_current_speed(rect)

    button_x = rect.x + rect.width - UI_CONFIG.border_size - UI_CONFIG.button_size
    button_y = rect.y + UI_CONFIG.border_size
    self._exp_button.render(rl.Rectangle(button_x, button_y, UI_CONFIG.button_size, UI_CONFIG.button_size))
    self._draw_lateral_control_overlay(
      button_x + UI_CONFIG.button_size / 2,
      button_y + UI_CONFIG.button_size / 2,
      UI_CONFIG.button_size,
    )

    # SP additions (dev UI, road name, speed limit, SCC, turn signals, circular alerts, rocket fuel)
    self.developer_ui.render(rect)
    self.road_name_renderer.render(rect)
    self.speed_limit_renderer.render(rect)
    self.smart_cruise_control_renderer.render(rect)
    self.turn_signal_controller.render(rect)
    self.circular_alerts_renderer.render(rect)
    self.rocket_fuel.render(rect, ui_state.sm)

  def _draw_acc_status(self, rect: rl.Rectangle) -> None:
    """BluePilot: a compact line under the MAX box -- what ACC is asking for, what ICBM is doing.

    Placed here rather than as another icon because the two renderers that already exist say WHY
    the target moved (SmartCruiseControl shows a curve, SpeedLimit shows the sign). Neither says
    what the car is doing about it, and nothing at all showed ICBM's state.
    """
    if self._acc_status_failed or (not self._acc_state and not self._icbm_text):
      return

    set_speed_width = UI_CONFIG.set_speed_width_metric if ui_state.is_metric else UI_CONFIG.set_speed_width_imperial
    x = rect.x + 60 + (UI_CONFIG.set_speed_width_imperial - set_speed_width) // 2
    y = rect.y + 45 + UI_CONFIG.set_speed_height + 12

    font_size = 34
    if self._acc_state:
      label = self._acc_state if self._acc_state == "COAST" else f"{self._acc_state} {abs(self._acc_accel):.1f}"
      color = ACC_STATUS_COLORS.get(self._acc_state, COLORS.WHITE)
      width = measure_text_cached(self._font_bold, label, font_size).x
      rl.draw_text_ex(self._font_bold, label,
                      rl.Vector2(x + (set_speed_width - width) / 2, y), font_size, 0, color)

    if self._icbm_text:
      y2 = y + (font_size + 6 if self._acc_state else 0)
      width = measure_text_cached(self._font_semi_bold, self._icbm_text, font_size).x
      rl.draw_text_ex(self._font_semi_bold, self._icbm_text,
                      rl.Vector2(x + (set_speed_width - width) / 2, y2), font_size, 0,
                      rl.Color(120, 170, 255, 255))

  def _draw_lateral_control_overlay(self, center_x: float, center_y: float, wheel_size: int) -> None:
    """Draw the current lateral control mode over the steering wheel icon."""
    if not self._show_lateral_control or self._lateral_mode is None:
      return

    text_size = int(wheel_size * 0.4)
    if self._lateral_mode == LateralMode.angle:
      letter, color = "A", rl.Color(50, 100, 255, 220)
    elif self._lateral_mode == LateralMode.curvature:
      letter, color = "C", rl.Color(255, 165, 0, 220)
    else:
      letter, color = "OP", rl.Color(100, 100, 100, 220)

    text_dims = measure_text_cached(self._font_bold, letter, text_size)
    text_pos = rl.Vector2(center_x - text_dims.x / 2, center_y - text_dims.y / 2)

    top = rl.Color(250, 250, 250, 200)
    bottom = rl.Color(200, 200, 200, 200)
    draw_shader_circle_gradient(center_x, center_y, text_size / 2, top, bottom)
    rl.draw_text_ex(self._font_bold, letter, text_pos, text_size, 0, color)

  def _draw_current_speed(self, rect: rl.Rectangle) -> None:
    """Override to add brake status red coloring and track speed_right."""
    # BluePilot: Respect "Speedometer: Hide from Onroad Screen" (HideVEgoUI) from Visuals.
    if self._hide_v_ego_ui:
      self.speed_right = 0
      return
    speed_text = str(round(self.speed))
    speed_text_size = measure_text_cached(self._font_bold, speed_text, FONT_SIZES.current_speed)
    speed_pos = rl.Vector2(
      rect.x + rect.width / 2 - speed_text_size.x / 2,
      SPEED_CENTER_Y - speed_text_size.y / 2
    )
    self.speed_right = speed_pos.x + speed_text_size.x

    # BluePilot: colour the speed by what the brakes are doing, if brake status is enabled.
    #   red   -> stop lamps are lit: traffic behind you is being told you are slowing
    #   amber -> ACC is asking for brakes but the lamps have not lit, i.e. a light application
    #            below the stop-lamp threshold. This is the "slowed without anyone noticing"
    #            case, and the one worth tuning IcbmMaxTargetDrop against.
    #   white -> no braking of either kind
    if self._brakes_on:
      speed_color = rl.Color(255, 60, 60, 255)
    elif self._acc_braking:
      speed_color = rl.Color(255, 180, 40, 255)
    else:
      speed_color = COLORS.WHITE
    rl.draw_text_ex(self._font_bold, speed_text, speed_pos, FONT_SIZES.current_speed, 0, speed_color)

    unit_text = "km/h" if ui_state.is_metric else "mph"
    unit_text_size = measure_text_cached(self._font_medium, unit_text, FONT_SIZES.speed_unit)
    unit_pos = rl.Vector2(rect.x + rect.width / 2 - unit_text_size.x / 2, SPEED_UNIT_CENTER_Y - unit_text_size.y / 2)
    # Draw drop shadow for readability over camera feed
    shadow_offset = 2
    shadow_pos = rl.Vector2(unit_pos.x + shadow_offset, unit_pos.y + shadow_offset)
    rl.draw_text_ex(self._font_medium, unit_text, shadow_pos, FONT_SIZES.speed_unit, 0, rl.Color(0, 0, 0, 150))
    rl.draw_text_ex(self._font_medium, unit_text, unit_pos, FONT_SIZES.speed_unit, 0, COLORS.WHITE_TRANSLUCENT)
