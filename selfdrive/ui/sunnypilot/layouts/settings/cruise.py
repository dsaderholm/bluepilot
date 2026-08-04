"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from enum import IntEnum

from openpilot.selfdrive.ui.sunnypilot.layouts.settings.cruise_sub_layouts.speed_limit_settings import SpeedLimitSettingsLayout
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.multilang import tr, tr_noop
from openpilot.system.ui.sunnypilot.widgets.list_view import toggle_item_sp, option_item_sp, simple_button_item_sp
# BluePilot: SectionHeader lives under the bp layer only because that is where it was first
# needed. It is a plain Widget over system.ui with no BluePilot-specific dependencies, and both
# panels drive the same scroller_tici.Scroller, so reusing it here is safe.
from openpilot.selfdrive.ui.bp.widgets.section_header import SectionHeader
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.scroller_tici import Scroller


class PanelType(IntEnum):
  CRUISE = 0
  SLA = 1


ICBM_DESC = tr_noop("When enabled, sunnypilot will attempt to manage the built-in cruise control buttons " +
                    "by emulating button presses for limited longitudinal control.")
ICMB_UNAVAILABLE = tr_noop("Intelligent Cruise Button Management is currently unavailable on this platform.")
ICMB_UNAVAILABLE_LONG_AVAILABLE = tr_noop("Disable the sunnypilot Longitudinal Control (alpha) toggle to allow Intelligent Cruise Button Management.")
ICMB_UNAVAILABLE_LONG_UNAVAILABLE = tr_noop("sunnypilot Longitudinal Control is the default longitudinal control for this platform.")

ACC_ENABLED_DESCRIPTION = tr_noop("Enable custom Short & Long press increments for cruise speed increase/decrease.")
ACC_NOLONG_DESCRIPTION = tr_noop("This feature can only be used with sunnypilot longitudinal control enabled.")
ACC_PCMCRUISE_DISABLED_DESCRIPTION = tr_noop("This feature is not supported on this platform due to vehicle limitations.")
ONROAD_ONLY_DESCRIPTION = tr_noop("Start the vehicle to check vehicle compatibility.")


class CruiseLayout(Widget):
  def __init__(self):
    super().__init__()
    self._current_panel = PanelType.CRUISE
    self._speed_limit_layout = SpeedLimitSettingsLayout(lambda: self._set_current_panel(PanelType.CRUISE))

    items = self._initialize_items()
    self._scroller = Scroller(items, line_separator=True, spacing=0)

  def _initialize_items(self):

    self.icbm_toggle = toggle_item_sp(
      title=tr("Intelligent Cruise Button Management (ICBM) (Alpha)"),
      description="",
      param="IntelligentCruiseButtonManagement")

    self.scc_v_toggle = toggle_item_sp(
      title=tr("Smart Cruise Control - Vision"),
      description=tr("Use vision path predictions to estimate the appropriate speed to drive through turns ahead."),
      param="SmartCruiseControlVision")

    # BluePilot: curve aggressiveness, split by speed regime like the angle-steering feel factors.
    # Shown as a percentage because that is what the value is -- a scale on the computed target.
    self.scc_v_low_speed_factor = option_item_sp(
      title=tr("Curve Sensitivity (Low Speed)"),
      description=tr("How much to slow for turns taken at low speed. Above 100% slows more than "
                     "the model asks for, below 100% slows less."),
      param="SmartCruiseControlVisionLowSpeedFactor",
      min_value=50, max_value=150, value_change_step=5,
      label_callback=lambda v: f"{v}%",
      inline=True)

    self.scc_v_high_speed_factor = option_item_sp(
      title=tr("Curve Sensitivity (High Speed)"),
      description=tr("The same scale for turns taken at highway speed, where the same corner "
                     "needs a very different amount of slowing."),
      param="SmartCruiseControlVisionHighSpeedFactor",
      min_value=50, max_value=150, value_change_step=5,
      label_callback=lambda v: f"{v}%",
      inline=True)

    # BluePilot: how early the curve cycle starts, independent of how much it slows
    self.scc_v_earliness = option_item_sp(
      title=tr("Curve Detection Earliness"),
      description=tr("How far ahead of a turn to begin slowing. This changes the timing only; "
                     "how much speed comes off is set by the two sensitivity controls above."),
      param="SmartCruiseControlVisionEarliness",
      min_value=50, max_value=200, value_change_step=10,
      label_callback=lambda v: f"{v}%",
      inline=True)

    # BluePilot: cap on how far ICBM drops the set speed in one step
    self.icbm_max_target_drop = option_item_sp(
      title=tr("Max Set Speed Drop Per Step"),
      description=tr("How far the set speed may fall in one step. Bigger steps slow the car sooner "
                     "for curves and speed limits, but past a point stock ACC stops coasting and "
                     "brakes. Watch the BRAKE LAMPS readout: regulations light the stop lamps "
                     "above 1.3 m/s2 of automatic braking, so raise this until the lamps start "
                     "coming on during routine slowing, then go back one. 0 disables the cap."),
      param="IcbmMaxTargetDrop",
      min_value=0, max_value=15, value_change_step=1,
      label_callback=self._speed_step_label,
      inline=True)

    # BluePilot: same cap in the other direction -- how fast the set speed comes back up
    self.icbm_max_target_rise = option_item_sp(
      title=tr("Max Set Speed Rise Per Step"),
      description=tr("How much the set speed may climb in one step when returning to cruise "
                     "speed after a curve or a low speed limit. ICBM holds the button rather "
                     "than tapping it, so without a cap the car accelerates back up as hard as "
                     "it can. Lower is gentler. 0 disables the cap."),
      param="IcbmMaxTargetRise",
      min_value=0, max_value=15, value_change_step=1,
      label_callback=self._speed_step_label,
      inline=True)

    # BluePilot: when a driver's set-speed press stops applying
    self.icbm_baseline_reset = option_item_sp(
      title=tr("Forget My Set Speed On Limit Change"),
      description=tr("When you adjust the set speed yourself, ICBM keeps every other feature "
                     "working but aims at your number instead of the speed limit target. This is "
                     "how far the posted limit has to move before your number is discarded and "
                     "Speed Limit Assist takes over again. Curves and lead vehicles never discard "
                     "it. You can also hand it back at any time by setting the speed to exactly "
                     "the limit, or by canceling and re-engaging."),
      param="IcbmBaselineResetDelta",
      min_value=0, max_value=30, value_change_step=1,
      label_callback=lambda v: tr("Never") if v == 0 else self._speed_step_label(v),
      inline=True)

    # BluePilot: radar-blind lead detector reach. TTC is the control that actually binds --
    # against a stopped lead TTC = dRel / v_ego, so at 65 mph 4.0 s already caps range near 116 m
    # and the distance bound never fires. Distance stays as a sanity limit.
    self.icbm_lead_max_ttc = option_item_sp(
      title=tr("Slow For Unconfirmed Vehicles"),
      description=tr("How early to slow for a vehicle the camera sees but the radar has not "
                     "confirmed -- most importantly a stopped car ahead. Measured as how many "
                     "seconds away it is. Higher reacts sooner. This is the control that changes "
                     "behavior; the distance limit below rarely comes into play."),
      param="IcbmLeadMaxTtc",
      min_value=10, max_value=80, value_change_step=5,
      # Stored in tenths of a second, so the raw number is 10x what the driver should read.
      label_callback=lambda v: f"{v / 10:.1f} s",
      inline=True)

    self.icbm_lead_max_distance = option_item_sp(
      title=tr("Unconfirmed Vehicle Max Distance"),
      description=tr("A sanity limit on the setting above: never react to an unconfirmed vehicle "
                     "further away than this, however early the timing says."),
      param="IcbmLeadMaxDistance",
      min_value=40, max_value=200, value_change_step=10,
      label_callback=lambda v: f"{v} m",
      inline=True)

    # BluePilot: act on the model's own stop intent. Off by default -- with no lead there is no
    # dRel, vRel or TTC, so persistence and the speed floor are the entire filter.
    self.icbm_model_stop = toggle_item_sp(
      title=tr("Slow For Stop Signs And Lights"),
      description=tr("Use the driving model's own stop intent to bring the set speed down for "
                     "stop signs and red lights with no vehicle at them. This is the one case "
                     "the setting above cannot catch, since an empty intersection produces "
                     "no vehicle to measure. Weaker evidence than the vehicle case -- how long "
                     "the model insists, and a speed floor, are its only filters."),
      param="IcbmModelStopEnabled")

    # BluePilot: hold openpilot's standstill resume until the lead has actually gone
    self.icbm_resume_gate = toggle_item_sp(
      title=tr("Wait For The Car Ahead Before Resuming"),
      description=tr("Wait for the vehicle ahead to actually move before resuming from a stop. "
                     "Without this, openpilot requests resume from its own plan and stock ACC "
                     "accelerates toward the set speed, then brakes hard when its radar finds the "
                     "lead still close."),
      param="IcbmResumeGateEnabled")

    self.icbm_resume_min_gap = option_item_sp(
      title=tr("Resume Minimum Gap"),
      description=tr("How far the car ahead must have pulled away before resuming counts as safe."),
      param="IcbmResumeMinGap",
      min_value=2, max_value=20, value_change_step=1,
      label_callback=lambda v: f"{v} m",
      inline=True)

    self.icbm_resume_min_lead_speed = option_item_sp(
      title=tr("Resume Minimum Lead Speed"),
      description=tr("How fast the car ahead must be moving before resuming. Together with the "
                     "gap above, this is what separates it rolling away from it merely creeping."),
      param="IcbmResumeMinLeadSpeed",
      min_value=1, max_value=15, value_change_step=1,
      label_callback=self._speed_step_label,
      inline=True)

    self.scc_m_toggle = toggle_item_sp(
      title=tr("Smart Cruise Control - Map"),
      description=tr("Use map data to estimate the appropriate speed to drive through turns ahead. "
                     "Unlike the camera-based control above, this knows a turn is coming before it "
                     "can be seen, so it is the one that works on a freeway exit ramp that is still "
                     "straight where you join it."),
      param="SmartCruiseControlMap")

    # BluePilot: SCC-Map's one knob, and it really is one knob -- see the map_controller comment.
    self.scc_m_decel = option_item_sp(
      title=tr("Map Curve Braking Rate"),
      description=tr("How hard to slow for a turn the map knows about. This also sets how early it "
                     "starts: a gentler rate needs more distance, so the set speed begins falling "
                     "sooner. Lower this if exit ramps come up too fast. Above 1.3 m/s2 the stop "
                     "lamps light, so values past that trade the BRAKE LAMPS readout for a later, "
                     "harder slowdown."),
      param="SmartCruiseControlMapDecel",
      min_value=4, max_value=25, value_change_step=1,
      label_callback=lambda v: f"{v / 10:.1f} m/s2",
      inline=True)

    self.custom_acc_toggle = toggle_item_sp(
      title=tr("Custom ACC Speed Increments"),
      description="",
      param="CustomAccIncrementsEnabled",
      callback=self._on_custom_acc_toggle)

    self.custom_acc_short_increment = option_item_sp(
      title=tr("Short Press Increment"),
      param="CustomAccShortPressIncrement",
      min_value=1, max_value=10, value_change_step=1,
      inline=True)

    self.custom_acc_long_increment = option_item_sp(
      title=tr("Long Press Increment"),
      param="CustomAccLongPressIncrement",
      value_map={1: 1, 2: 5, 3: 10},
      min_value=1, max_value=3, value_change_step=1,
      inline=True)

    self.sla_settings_button = simple_button_item_sp(
      button_text=lambda: tr("Speed Limit"),
      button_width=800,
      callback=lambda: self._set_current_panel(PanelType.SLA)
    )

    self.dec_toggle = toggle_item_sp(
      title=tr("Enable Dynamic Experimental Control"),
      description=tr("Enable toggle to allow the model to determine when to use sunnypilot ACC or sunnypilot End to End Longitudinal."),
      param="DynamicExperimentalControl")

    # BluePilot: grouped under headings rather than run together as one list. ICBM alone now owns
    # ten controls, and flat they read as a wall of unrelated numbers -- there was no way to tell
    # which ones affect the set speed, which affect hazards, and which only matter at a stop.
    #
    # Behavior settings live here, next to the feature that owns them; the display side of the
    # same work (Show Ford ACC Status) stays in the BluePilot panel with the other display
    # toggles. That split is deliberate: this panel changes what the car does, that one changes
    # what you are shown.
    items = [
      self.icbm_toggle,

      SectionHeader(tr("Set Speed Management")),
      self.icbm_max_target_drop,
      self.icbm_max_target_rise,
      self.icbm_baseline_reset,

      SectionHeader(tr("Slowing For Hazards")),
      self.icbm_lead_max_ttc,
      self.icbm_lead_max_distance,
      self.icbm_model_stop,

      SectionHeader(tr("Resuming From A Stop")),
      self.icbm_resume_gate,
      self.icbm_resume_min_gap,
      self.icbm_resume_min_lead_speed,

      SectionHeader(tr("Curves")),
      self.scc_v_toggle,
      self.scc_v_low_speed_factor,
      self.scc_v_high_speed_factor,
      self.scc_v_earliness,
      self.scc_m_toggle,
      self.scc_m_decel,

      SectionHeader(tr("Other")),
      self.dec_toggle,
      self.custom_acc_toggle,
      self.custom_acc_short_increment,
      self.custom_acc_long_increment,
      self.sla_settings_button,
    ]
    return items

  def _render(self, rect):
    if self._current_panel == PanelType.SLA:
      self._speed_limit_layout.render(rect)
    else:
      self._scroller.render(rect)

  def show_event(self):
    self._set_current_panel(PanelType.CRUISE)
    self._scroller.show_event()
    self.icbm_toggle.show_description(True)
    self.custom_acc_toggle.show_description(True)

  def _set_current_panel(self, panel: PanelType):
    self._current_panel = panel
    if panel == PanelType.SLA:
      self._speed_limit_layout.show_event()

  @staticmethod
  def _speed_step_label(value: int) -> str:
    """BluePilot: these params are stored in display units -- the same units the set speed is shown
    in -- so the label has to follow the driver's mph/km-h choice rather than assume one."""
    return f'{value} {"km/h" if ui_state.is_metric else "mph"}'

  @property
  def _icbm_tunables(self):
    """BluePilot: the ICBM settings that only do anything while ICBM is actually driving the
    set speed. Greyed out otherwise, so a dead control never looks live."""
    return (
      self.icbm_max_target_drop,
      self.icbm_max_target_rise,
      self.icbm_baseline_reset,
      self.icbm_lead_max_ttc,
      self.icbm_lead_max_distance,
      self.icbm_model_stop,
      self.icbm_resume_gate,
      self.icbm_resume_min_gap,
      self.icbm_resume_min_lead_speed,
    )

  def _update_state(self):
    super()._update_state()

    for item in self._icbm_tunables:
      item.action_item.set_enabled(ui_state.has_icbm)

    if ui_state.CP is not None and ui_state.CP_SP is not None:
      has_icbm = ui_state.has_icbm
      has_long = ui_state.has_longitudinal_control

      if ui_state.CP_SP.intelligentCruiseButtonManagementAvailable and not has_long:
        self.icbm_toggle.action_item.set_enabled(ui_state.is_offroad())
        self.icbm_toggle.set_description(tr(ICBM_DESC))
      else:
        ui_state.params.remove("IntelligentCruiseButtonManagement")
        self.icbm_toggle.action_item.set_enabled(False)

        long_desc = ICMB_UNAVAILABLE
        if has_long:
          if ui_state.CP.alphaLongitudinalAvailable:
            long_desc += " " + ICMB_UNAVAILABLE_LONG_AVAILABLE
          else:
            long_desc += " " + ICMB_UNAVAILABLE_LONG_UNAVAILABLE

        new_desc = "<b>" + tr(long_desc) + "</b>\n\n" + tr(ICBM_DESC)
        if self.icbm_toggle.description != new_desc:
          self.icbm_toggle.set_description(new_desc)
          self.icbm_toggle.show_description(True)

      if has_long or has_icbm:
        self.custom_acc_toggle.action_item.set_enabled(((has_long and not ui_state.CP.pcmCruise) or has_icbm) and ui_state.is_offroad())
        self.dec_toggle.action_item.set_enabled(has_long)
        self.scc_v_toggle.action_item.set_enabled(True)
        self.scc_m_toggle.action_item.set_enabled(True)
      else:
        ui_state.params.remove("CustomAccIncrementsEnabled")
        ui_state.params.remove("DynamicExperimentalControl")
        ui_state.params.remove("SmartCruiseControlVision")
        ui_state.params.remove("SmartCruiseControlMap")
        self.custom_acc_toggle.action_item.set_enabled(False)
        self.dec_toggle.action_item.set_enabled(False)
        self.scc_v_toggle.action_item.set_enabled(False)
        self.scc_m_toggle.action_item.set_enabled(False)

    else:
      has_icbm = has_long = False
      self.icbm_toggle.action_item.set_enabled(False)
      self.icbm_toggle.set_description(tr(ONROAD_ONLY_DESCRIPTION))

    show_custom_acc_desc = False

    if ui_state.is_offroad():
      new_custom_acc_desc = tr(ONROAD_ONLY_DESCRIPTION)
      show_custom_acc_desc = True
    else:
      if has_long or has_icbm:
        if has_long and ui_state.CP.pcmCruise:
          new_custom_acc_desc = tr(ACC_PCMCRUISE_DISABLED_DESCRIPTION)
          show_custom_acc_desc = True
        else:
          new_custom_acc_desc = tr(ACC_ENABLED_DESCRIPTION)
      else:
        new_custom_acc_desc = tr(ACC_NOLONG_DESCRIPTION)
        show_custom_acc_desc = True
        self.custom_acc_toggle.action_item.set_state(False)

    if self.custom_acc_toggle.description != new_custom_acc_desc:
      self.custom_acc_toggle.set_description(new_custom_acc_desc)
      if show_custom_acc_desc:
        self.custom_acc_toggle.show_description(True)

    self._on_custom_acc_toggle(self.custom_acc_toggle.action_item.get_state())

  def _on_custom_acc_toggle(self, state):
    self.custom_acc_short_increment.set_visible(state)
    self.custom_acc_long_increment.set_visible(state)
    self.custom_acc_short_increment.action_item.set_enabled(self.custom_acc_toggle.action_item.enabled)
    self.custom_acc_long_increment.action_item.set_enabled(self.custom_acc_toggle.action_item.enabled)
