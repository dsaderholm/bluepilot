"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import pyray as rl

from openpilot.common.constants import CV
from openpilot.selfdrive.ui.mici.onroad.torque_bar import TorqueBar
from openpilot.selfdrive.ui.sunnypilot.onroad.developer_ui import DeveloperUiRenderer, DeveloperUiState, get_bottom_dev_ui_offset
from openpilot.selfdrive.ui.sunnypilot.onroad.road_name import RoadNameRenderer
from openpilot.selfdrive.ui.sunnypilot.onroad.rocket_fuel import RocketFuel
from openpilot.selfdrive.ui.sunnypilot.onroad.speed_limit import SpeedLimitRenderer
from openpilot.selfdrive.ui.sunnypilot.onroad.smart_cruise_control import SmartCruiseControlRenderer
from openpilot.selfdrive.ui.sunnypilot.onroad.turn_signal import TurnSignalController
from openpilot.selfdrive.ui.sunnypilot.onroad.circular_alerts import CircularAlertsRenderer
from openpilot.selfdrive.ui.sunnypilot.onroad.speed_renderer import SpeedRenderer
from openpilot.selfdrive.ui.bp.onroad.icbm_hud_state import max_box_state, read_icbm_hud_state
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.selfdrive.ui.onroad.hud_renderer import HudRenderer, UI_CONFIG, FONT_SIZES, COLORS, CRUISE_DISABLED_CHAR
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached

SLA_ACTIVE_COLOR = rl.Color(0x91, 0x9b, 0x95, 0xff)

# FusionPilot: the MAX box while the driver's own HOLD is the number being driven to. Matches
# HOLD_LABEL_COLOR in hud_renderer_bp so the box and the badge below it read as one statement.
HOLD_DRIVING_COLOR = rl.Color(175, 210, 255, 0xff)
# FusionPilot: the pin mark, moved onto the box when the HOLD badge was deleted on 2026-08-22.
# Same values the badge used, so a pinned hold looks the same as it always has.
PIN_DOT_RADIUS = 9
PIN_DOT_COLOR = rl.Color(255, 214, 120, 255)


class HudRendererSP(HudRenderer):
  def __init__(self):
    super().__init__()
    self.developer_ui = DeveloperUiRenderer()
    self.road_name_renderer = RoadNameRenderer()
    self.rocket_fuel = RocketFuel()
    self.speed_limit_renderer = SpeedLimitRenderer()
    self.smart_cruise_control_renderer = SmartCruiseControlRenderer()
    self.turn_signal_controller = TurnSignalController()
    self.circular_alerts_renderer = CircularAlertsRenderer()
    self.speed_renderer = SpeedRenderer()
    self._torque_bar = TorqueBar(scale=3.0, always=True)
    self._box = max_box_state(0.0, None, 0.0, 0.0)
    # Set every frame by `_draw_set_speed`; None until the box has been drawn once, and while
    # cruise is unavailable it is never drawn at all -- so the tap gate must not assume it exists.
    self._set_speed_rect = None

    self.pcm_cruise_speed: bool = True
    self.show_icbm_status: bool = False
    self.icbm_active_counter: int = 0
    self.speed_cluster: float = 0.0
    self.speed_conv: float = CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH

  def _update_state(self) -> None:
    if ui_state.sm.recv_frame["carState"] < ui_state.started_frame:
      return

    if ui_state.CP_SP is not None:
      self.pcm_cruise_speed = ui_state.CP_SP.pcmCruiseSpeed
    self.speed_conv = CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH
    self.speed_cluster = ui_state.sm['carState'].cruiseState.speedCluster * self.speed_conv

    super()._update_state()
    self.road_name_renderer.update()
    self.speed_limit_renderer.update()
    self.smart_cruise_control_renderer.update()
    self.turn_signal_controller.update()
    self.circular_alerts_renderer.update()
    self.speed_renderer.update()

  def _set_speed_aim(self):
    """Read the messages, hand the decision to `max_box_state`.

    NOT ICBM-SPECIFIC, and named accordingly -- his observation, 2026-08-20: *"most of this isn't
    really ICBM related, like it could work if someone was fully OP long."* Correct. The rule is
    "show what the car is being driven to", which is true of any longitudinal controller. Under full
    openpilot longitudinal there is no `vBaseline`, so `hold` reads 0 and the same call falls
    through to SLA and then to the set speed -- today's behaviour, no branch required.

    The rule itself lives in `selfdrive/ui/bp/onroad/icbm_hud_state.py` so it can be tested without
    raylib -- this file cannot be imported offline, and a rule that only runs on the device is a
    rule nobody checks. All this does is fetch and convert.

    Never raises: a HUD that throws takes the on-road screen with it, so every read is guarded and
    the fallback is today's behaviour.
    """
    # THROUGH THE SHARED READER, not inline. The pin state used to be read only by the HOLD badge
    # in hud_renderer_bp; with the badge gone (2026-08-22) the box carries the mark, and a second
    # copy of the enum positions here is exactly the drift `icbm_hud_state` exists to prevent.
    icbm_state = read_icbm_hud_state(ui_state.sm)
    try:
      icbm = ui_state.sm['selfdriveStateSP'].intelligentCruiseButtonManagement
      hold = float(icbm.vBaseline)
    except Exception:  # noqa: BLE001 -- see docstring
      hold = 0.0

    sla = None
    try:
      resolver = ui_state.sm['longitudinalPlanSP'].speedLimit.resolver
      if resolver.speedLimitValid or resolver.speedLimitLastValid:
        conv = CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH
        final = float(resolver.speedLimitFinalLast) * conv
        if final > 0:
          sla = final
    except Exception:  # noqa: BLE001
      sla = None

    return max_box_state(hold, sla, self.set_speed, self.speed_cluster,
                         pin_suggestion=float(icbm_state.pin_suggestion),
                         pinned=icbm_state.pinned,
                         hold_locked=icbm_state.hold_locked)

  def _get_icbm_status(self):
    # Compared against the AIM, not against `vCruiseCluster`. The little number means "the car is
    # not at your number right now", and the thing it should differ from is what ICBM is steering
    # toward -- otherwise it fires on the harmless drift between openpilot's own count and Ford's.
    if not self.pcm_cruise_speed and ui_state.sm['carControl'].enabled:
      if round(self._box.aim) != round(self.speed_cluster):
        self.icbm_active_counter = 3 * gui_app.target_fps  # 3 seconds usually
      elif self.icbm_active_counter > 0:
        self.icbm_active_counter -= 1
    else:
      self.icbm_active_counter = 0

    self.show_icbm_status = self.icbm_active_counter > 0

  def _draw_set_speed(self, rect: rl.Rectangle) -> None:
    long_plan_sp = ui_state.sm['longitudinalPlanSP']
    long_override = ui_state.sm['carControl'].cruiseControl.override
    # RESOLVED ONCE PER FRAME. `_get_icbm_status` and the drawing below both need the aim, and
    # calling it twice re-read both messages -- and could disagree, since a message arriving between
    # the two calls would decide `show_icbm_status` from a different aim than the one drawn.
    self._box = self._set_speed_aim()
    self._get_icbm_status()

    set_speed_width = UI_CONFIG.set_speed_width_metric if ui_state.is_metric else UI_CONFIG.set_speed_width_imperial
    x = rect.x + 60 + (UI_CONFIG.set_speed_width_imperial - set_speed_width) // 2
    y = rect.y + 45

    set_speed_rect = rl.Rectangle(x, y, set_speed_width, UI_CONFIG.set_speed_height)
    rl.draw_rectangle_rounded(set_speed_rect, 0.35, 10, COLORS.BLACK_TRANSLUCENT)
    rl.draw_rectangle_rounded_lines_ex(set_speed_rect, 0.35, 10, 6, COLORS.BORDER_TRANSLUCENT)
    # THE TAP TARGET FOR PINNING, published for `HudRendererBP._handle_mouse_release`. Recorded
    # where the geometry actually is rather than recomputed there: the badge that used to own this
    # rect was deleted on 2026-08-22 and two copies of the same four numbers is how they drift.
    self._set_speed_rect = set_speed_rect

    max_color = COLORS.GREY
    set_speed_color = COLORS.DARK_GREY
    if self.is_cruise_set:
      set_speed_color = COLORS.WHITE
      if long_plan_sp.speedLimit.assist.active:
        set_speed_color = SLA_ACTIVE_COLOR if long_override else rl.Color(0, 0xff, 0, 0xff)
        max_color = SLA_ACTIVE_COLOR if long_override else rl.Color(0x80, 0xd8, 0xa6, 0xff)
      else:
        if ui_state.status == UIStatus.ENGAGED:
          max_color = COLORS.ENGAGED
        elif ui_state.status == UIStatus.DISENGAGED:
          max_color = COLORS.DISENGAGED
        elif ui_state.status == UIStatus.OVERRIDE:
          max_color = COLORS.OVERRIDE

    box = self._box

    # THE LABEL SLOT, in priority order. It can only say one thing, so the order is the ranking of
    # what he needs to know:
    #
    #   1. the DASH number, when the car is not at the aim. Something is actively pulling him down
    #      -- a curve, a lead, a limit ahead -- and that outranks everything.
    #   2. the SLA FALLBACK, while a hold is driving and SLA has a limit. This is the number
    #      cancelling the hold would give back, shown full size instead of as a corner digit on the
    #      sign. It appears exactly when it is actionable and never when it is not.
    #   3. the word MAX.
    #
    # With a hold and NO limit there is no fallback to offer, so it falls through to MAX -- which is
    # his common case on the roads where holds matter most.
    max_str_size = 60 if box.label_is_number else 40
    max_str_y = 15 if box.label_is_number else 27
    max_text = box.label if box.label_is_number else tr("MAX")

    # Tinted while the hold owns the number, so whose number it is reads at a glance without
    # anything being spelled out. Only when a hold is actually driving -- SLA keeps its own colours.
    #
    # NOT while the hold is LOCKED. Something else owns the target then, so the hold is not the
    # thing being honoured and claiming the number is his would be a lie. The badge said this by
    # going grey; the box says it by simply not tinting.
    if box.hold_driving and self.is_cruise_set and not box.hold_locked:
      set_speed_color = HOLD_DRIVING_COLOR
      if not self.show_icbm_status:
        max_color = HOLD_DRIVING_COLOR
    max_text_width = measure_text_cached(self._font_semi_bold, max_text, max_str_size).x
    rl.draw_text_ex(
      self._font_semi_bold,
      max_text,
      rl.Vector2(x + (set_speed_width - max_text_width) / 2, y + max_str_y),
      max_str_size,
      0,
      max_color,
    )

    set_speed_text = CRUISE_DISABLED_CHAR if not self.is_cruise_set else str(round(box.aim))
    speed_text_width = measure_text_cached(self._font_bold, set_speed_text, FONT_SIZES.set_speed).x
    rl.draw_text_ex(
      self._font_bold,
      set_speed_text,
      rl.Vector2(x + (set_speed_width - speed_text_width) / 2, y + 77),
      FONT_SIZES.set_speed,
      0,
      set_speed_color,
    )

    # THE PIN MARK, in the corner the HOLD badge used to put it in. A filled dot means this hold is
    # pinned to this place and tapping removes it; a ring means there is no hold and tapping would
    # CREATE one at the speed now shown in the label slot.
    #
    # A ring rather than `draw_circle_lines`, which is a single hairline and vanished at a glance
    # against the badge fill -- for a mark whose whole job is to be noticed that is no mark at all.
    # Top-LEFT, because the label is centred and the right corner is where the ICBM arrow used to
    # hang; the two once landed within a pixel of each other.
    if box.pinned:
      rl.draw_circle(int(x + 20), int(y + 20), PIN_DOT_RADIUS, PIN_DOT_COLOR)
    elif box.pin_offer:
      rl.draw_ring(rl.Vector2(x + 20, y + 20), PIN_DOT_RADIUS - 3, PIN_DOT_RADIUS, 0, 360, 24,
                   PIN_DOT_COLOR)

  def _draw_current_speed(self, rect: rl.Rectangle) -> None:
    self.speed_renderer.render(rect)

  def _render(self, rect: rl.Rectangle) -> None:
    super()._render(rect)

    if ui_state.torque_bar:
      torque_rect = rect
      if ui_state.developer_ui in (DeveloperUiState.BOTTOM, DeveloperUiState.BOTH):
        torque_rect = rl.Rectangle(rect.x, rect.y, rect.width, rect.height - get_bottom_dev_ui_offset())
      self._torque_bar.render(torque_rect)

    self.developer_ui.render(rect)
    self.road_name_renderer.render(rect)
    self.speed_limit_renderer.render(rect)
    self.smart_cruise_control_renderer.render(rect)
    self.turn_signal_controller.render(rect)
    self.circular_alerts_renderer.render(rect)
    self.rocket_fuel.render(rect, ui_state.sm)
