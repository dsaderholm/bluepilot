"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from collections.abc import Callable
from enum import IntEnum

import pyray as rl
from openpilot.selfdrive.ui.bp.settings_defaults import recommended
from openpilot.selfdrive.ui.sunnypilot.layouts.settings.cruise_sub_layouts.speed_limit_policy import SpeedLimitPolicyLayout
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.common import Mode as SpeedLimitMode
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.common import OffsetType as SpeedLimitOffsetType
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.sunnypilot.widgets import get_highlighted_description
from openpilot.system.ui.sunnypilot.widgets.list_view import multiple_button_item_sp, option_item_sp, simple_button_item_sp, toggle_item_sp, LineSeparatorSP
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.network import NavButton
from openpilot.system.ui.widgets.scroller_tici import Scroller

SPEED_LIMIT_MODE_BUTTONS = [tr("Off"), tr("Info"), tr("Warning"), tr("Assist")]
SPEED_LIMIT_OFFSET_TYPE_BUTTONS = [tr("None"), tr("Fixed"), tr("%"), tr("By Limit")]

SPEED_LIMIT_MODE_DESCRIPTIONS = [
  tr("Off: Disables the Speed Limit functions."),
  tr("Information: Displays the current road's speed limit."),
  tr("Warning: Provides a warning when exceeding the current road's speed limit."),
  tr("Assist: Adjusts the vehicle's cruise speed based on the current road's speed limit when operating the +/- buttons."),
]

SPEED_LIMIT_OFFSET_DESCRIPTIONS = [
  tr("None: No Offset"),
  tr("Fixed: Adds a fixed offset [Speed Limit + Offset]"),
  tr("Percent: Adds a percent offset [Speed Limit + (Offset % Speed Limit)]"),
  tr("By Limit: A different offset for slow, medium and fast roads [Speed Limit + band offset]"),
]


class PanelType(IntEnum):
  SETTINGS = 0
  POLICY = 1


class SpeedLimitSettingsLayout(Widget):
  def __init__(self, back_btn_callback: Callable):
    super().__init__()
    self._current_panel = PanelType.SETTINGS

    self._back_button = NavButton(tr("Back"))
    self._back_button.set_click_callback(back_btn_callback)

    self._policy_layout = SpeedLimitPolicyLayout(lambda: self._set_current_panel(PanelType.SETTINGS))

    items = self._initialize_items()
    self._scroller = Scroller(items, line_separator=False, spacing=0)

  def _initialize_items(self):
    self._speed_limit_mode = multiple_button_item_sp(
      title=lambda: tr("Speed Limit"),
      description=self._get_mode_description,
      buttons=SPEED_LIMIT_MODE_BUTTONS,
      param="SpeedLimitMode",
      button_width=380,
    )

    self._source_button = simple_button_item_sp(
      button_text=lambda: tr("Customize Source"),
      button_width=720,
      callback=lambda: self._set_current_panel(PanelType.POLICY)
    )

    self._speed_limit_offset_type = multiple_button_item_sp(
      title=lambda: tr("Speed Limit Offset"),
      description="",
      buttons=SPEED_LIMIT_OFFSET_TYPE_BUTTONS,
      param="SpeedLimitOffsetType",
      # 4 buttons, not the 3 this row was sized for. MultipleButtonActionSP lays them out as
      # len(buttons) * button_width with no wrapping and no fit check, so leaving 450 here pushed
      # the row from 1350 px to 1800 and "By Limit" rendered off the right edge of the screen --
      # reported from the car, invisible to every test we had.
      button_width=380,
    )

    self._speed_limit_value_offset = option_item_sp(
      title="",
      param="SpeedLimitValueOffset",
      min_value=-30,
      max_value=30,
      description=self._get_offset_description,
      label_callback=self._get_offset_label,
    )

    self._speed_limit_fallback = multiple_button_item_sp(
      title=lambda: tr("When No Limit Is Known"),
      description=recommended(tr("What to do where no map or sign can say what the limit is. Set Speed stands "
                     "down and leaves your own number alone; Last Known keeps the previous limit "
                     "until a new one appears. Coming off a freeway, Last Known carries the "
                     "freeway's number down the ramp and along the next road."), "SpeedLimitFallback"),
      buttons=[tr("Set Speed"), tr("Last Known")],
      param="SpeedLimitFallback",
      button_width=380)

    self._lookahead_higher = option_item_sp(
      title=tr("Speed Up Early For Faster Roads"),
      description=recommended(tr("How far ahead of a higher speed limit to start raising the set speed, so the "
                     "car is already up to speed where the faster road begins instead of crawling "
                     "up after the sign. Never applies before the sign is reached. 0 turns it off."),
                     "SpeedLimitLookaheadHigher", lambda v: tr("Off") if v == 0 else f"{v} s"),
      param="SpeedLimitLookaheadHigher",
      min_value=0, max_value=15, value_change_step=1,
      label_callback=lambda v: tr("Off") if v == 0 else f"{v} s",
      inline=True)

    # BluePilot: one offset per speed band. A single number is wrong at both ends of the range and
    # the percentage option is the same mistake in disguise -- 10% is 2.5 mph in a 25 and 7 in a
    # 70, which is roughly backwards from how anyone drives.
    self._offset_low = option_item_sp(
      title=tr("Offset on Slow Roads"),
      description=recommended(tr("Added to limits below the first breakpoint."),
                              "SpeedLimitOffsetLow", self._band_label),
      param="SpeedLimitOffsetLow",
      min_value=-15, max_value=25, label_callback=self._band_label, inline=True)

    self._offset_mid = option_item_sp(
      title=tr("Offset on Medium Roads"),
      description=recommended(tr("Added to limits between the two breakpoints."),
                              "SpeedLimitOffsetMid", self._band_label),
      param="SpeedLimitOffsetMid",
      min_value=-15, max_value=25, label_callback=self._band_label, inline=True)

    self._offset_high = option_item_sp(
      title=tr("Offset on Fast Roads"),
      description=recommended(tr("Added to limits at or above the second breakpoint."),
                              "SpeedLimitOffsetHigh", self._band_label),
      param="SpeedLimitOffsetHigh",
      min_value=-15, max_value=25, label_callback=self._band_label, inline=True)

    self._offset_mid_threshold = option_item_sp(
      title=tr("Slow / Medium Breakpoint"),
      description=recommended(tr("Posted limits below this use the slow-road offset."),
                              "SpeedLimitOffsetMidThreshold", self._band_label),
      param="SpeedLimitOffsetMidThreshold",
      min_value=15, max_value=60, value_change_step=5,
      label_callback=self._band_label, inline=True)

    self._offset_high_threshold = option_item_sp(
      title=tr("Medium / Fast Breakpoint"),
      description=recommended(tr("Posted limits at or above this use the fast-road offset."),
                              "SpeedLimitOffsetHighThreshold", self._band_label),
      param="SpeedLimitOffsetHighThreshold",
      min_value=35, max_value=85, value_change_step=5,
      label_callback=self._band_label, inline=True)

    # BluePilot: bidirectional following and its ceiling
    self._speed_limit_auto_follow = toggle_item_sp(
      title=tr("Automatic Speed Limit Following"),
      description=recommended(tr("Follow detected speed limits in both directions without confirmation. "
                     "Stock behavior only ever lowers the set speed and requires you to confirm increases. "
                     "Every automatic change is announced on screen, and any cruise button press takes back control."),
                     "SpeedLimitAutoFollow"),
      param="SpeedLimitAutoFollow")

    self._speed_limit_max_set_speed = option_item_sp(
      title=tr("Maximum Set Speed"),
      param="SpeedLimitMaxSetSpeed",
      min_value=25,
      max_value=100,
      value_change_step=5,
      # recommended() quotes the SAME callback the control renders with -- its own docstring warns
      # that a value shown one way in the description and another on the control beside it is worse
      # than saying nothing.
      description=recommended(tr("Automatic speed limit following will never request above this speed, "
                     "regardless of the detected limit."), "SpeedLimitMaxSetSpeed",
                     self._max_set_speed_label),
      label_callback=self._max_set_speed_label,
      inline=True)

    # BluePilot: radar detector. These live on the speed limit screen rather than with the cruise
    # settings because what the detector produces is an OFFSET -- the same kind of thing as the
    # controls above it, and it replaces them while an alert holds. It has nothing to do with
    # cruise buttons and keeps working when they are gone.
    self._radar_detector_enabled = toggle_item_sp(
      title=tr("Radar Detector"),
      description=recommended(tr("Read alerts from a Valentine One Gen2 connected to the accessory jack "
                     "and show them on screen. On its own this only displays and records -- it "
                     "never changes how the car drives."), "RadarDetectorEnabled"),
      param="RadarDetectorEnabled")

    self._radar_detector_slowdown = toggle_item_sp(
      title=tr("Slow Down For Strong Ka Alerts"),
      description=recommended(tr("Aim below the posted limit while a strong Ka alert is ahead of you, "
                     "replacing your usual offset until it clears. Muted alerts are ignored, so "
                     "anywhere the detector has already learned to stay quiet stays quiet here "
                     "too. The strength below is a starting guess -- drive with it, then run "
                     "bp_radar_fit.py to pick the number from your own roads."), "RadarDetectorSlowdownEnabled"),
      param="RadarDetectorSlowdownEnabled")

    self._radar_detector_min_bars = option_item_sp(
      title=tr("Alert Strength To Act On"),
      description=recommended(tr("How strong a Ka alert has to be before the set speed moves, counted in "
                     "the same signal bars the detector shows. Lower reacts sooner and further "
                     "out; higher waits for the signal to be close and certain. Ships low on "
                     "purpose -- too high and it would never fire without you noticing. Run "
                     "bp_radar_fit.py on your own drives to replace the guess."), "RadarDetectorMinBars", lambda v: f"{v} of 8"),
      param="RadarDetectorMinBars",
      min_value=1, max_value=8, value_change_step=1,
      label_callback=lambda v: f"{v} of 8",
      inline=True)

    self._radar_detector_margin = option_item_sp(
      title=tr("Speed During An Alert"),
      description=recommended(tr("How far below the posted limit to aim while an alert is active. This "
                     "replaces your normal offset for as long as it lasts."), "RadarDetectorMargin", self._band_label),
      param="RadarDetectorMargin",
      min_value=0, max_value=15, value_change_step=1,
      label_callback=self._band_label,
      inline=True)

    self._radar_detector_mute = toggle_item_sp(
      title=tr("Learn And Mute False Alarms"),
      description=recommended(tr("Remember the places your detector cries wolf -- the store doors and "
                     "the signs -- and tell it to stay quiet there, the way your old detector's "
                     "mute memory did. A place has to alert on nearly every pass before it counts, "
                     "so somewhere police actually sit is never silenced. This is the only part of "
                     "the feature that sends anything to the detector."), "RadarDetectorMuteFalseAlarms"),
      param="RadarDetectorMuteFalseAlarms")

    items = [
      self._speed_limit_mode,
      LineSeparatorSP(40),
      self._source_button,
      LineSeparatorSP(40),
      self._speed_limit_auto_follow,
      self._speed_limit_max_set_speed,
      LineSeparatorSP(40),
      self._speed_limit_fallback,
      self._lookahead_higher,
      LineSeparatorSP(40),
      self._speed_limit_offset_type,
      self._speed_limit_value_offset,
      self._offset_low,
      self._offset_mid,
      self._offset_high,
      self._offset_mid_threshold,
      self._offset_high_threshold,
      LineSeparatorSP(40),
      self._radar_detector_enabled,
      self._radar_detector_mute,
      self._radar_detector_slowdown,
      self._radar_detector_min_bars,
      self._radar_detector_margin,
    ]
    return items

  def _set_current_panel(self, panel: PanelType):
    self._current_panel = panel
    if panel == PanelType.POLICY:
      self._policy_layout.show_event()

  @staticmethod
  def _get_mode_description():
    return get_highlighted_description(ui_state.params, "SpeedLimitMode", SPEED_LIMIT_MODE_DESCRIPTIONS)

  @staticmethod
  def _band_label(value: int) -> str:
    """Display units, following the driver's mph/km-h choice like every other speed control."""
    return f'{value} {"km/h" if ui_state.is_metric else "mph"}'

  @staticmethod
  def _get_offset_description():
    return get_highlighted_description(ui_state.params, "SpeedLimitOffsetType", SPEED_LIMIT_OFFSET_DESCRIPTIONS)

  @staticmethod
  def _max_set_speed_label(value):
    # BluePilot: stored in display units -- speed_limit_assist.py converts it with KPH_TO_MS or
    # MPH_TO_MS depending on IsMetric -- so the label has to follow the driver's choice too.
    # Without it the value renders as a bare "85", which is 137 km/h or 53 mph depending.
    return f'{value} {tr("km/h") if ui_state.is_metric else tr("mph")}'

  @staticmethod
  def _get_offset_label(value):
    offset_type = int(ui_state.params.get("SpeedLimitOffsetType", return_default=True))
    unit = tr("km/h") if ui_state.is_metric else tr("mph")

    if offset_type == int(SpeedLimitOffsetType.percentage):
      return f"{value}%"
    elif offset_type == int(SpeedLimitOffsetType.fixed):
      return f"{value} {unit}"
    return str(value)

  def _update_state(self):
    super()._update_state()

    speed_limit_mode_param = ui_state.params.get("SpeedLimitMode", return_default=True)
    if ui_state.CP is not None and ui_state.CP_SP is not None:
      brand = ui_state.CP.brand
      has_long = ui_state.has_longitudinal_control
      has_icbm = ui_state.has_icbm

      """
          Speed Limit Assist is available when:
          - has_long or has_icbm, and
          - is not a release branch or not a disallowed brand, and
          - is not always disallwed
      """
      sla_disallow_in_release = brand == "tesla" and ui_state.is_sp_release
      sla_always_disallow = brand == "rivian"
      sla_available = (has_long or has_icbm) and not sla_disallow_in_release and not sla_always_disallow

      if not sla_available and speed_limit_mode_param == int(SpeedLimitMode.assist):
        ui_state.params.put("SpeedLimitMode", int(SpeedLimitMode.warning))

    else:
      sla_available = False

    if not sla_available:
      self._speed_limit_mode.action_item.set_enabled_buttons({
        int(SpeedLimitMode.off),
        int(SpeedLimitMode.information),
        int(SpeedLimitMode.warning),
      })
    else:
      self._speed_limit_mode.action_item.set_enabled_buttons(None)

    offset_type = ui_state.params.get("SpeedLimitOffsetType", return_default=True)
    by_speed = offset_type == int(SpeedLimitOffsetType.bySpeed)
    # The single-value control and the banded ones are alternatives, never both: showing five live
    # controls next to one that no longer does anything is how a setting gets adjusted for an hour
    # with no effect.
    self._speed_limit_value_offset.set_visible(
      offset_type not in (int(SpeedLimitOffsetType.off), int(SpeedLimitOffsetType.bySpeed)))
    for item in (self._offset_low, self._offset_mid, self._offset_high,
                 self._offset_mid_threshold, self._offset_high_threshold):
      item.set_visible(by_speed)

  def _render(self, rect):
    if self._current_panel == PanelType.POLICY:
      self._policy_layout.render(rect)
      return

    self._back_button.set_position(self._rect.x, self._rect.y + 20)
    self._back_button.render()

    content_rect = rl.Rectangle(rect.x, rect.y + self._back_button.rect.height + 40, rect.width, rect.height - self._back_button.rect.height - 40)
    self._scroller.render(content_rect)

  def show_event(self):
    self._current_panel = PanelType.SETTINGS
    self._scroller.show_event()
    self._speed_limit_mode.show_description(True)

  def hide_event(self):
    self._current_panel = PanelType.SETTINGS
    self._scroller.hide_event()
