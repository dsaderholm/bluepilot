"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from enum import IntEnum

from openpilot.selfdrive.ui.sunnypilot.layouts.settings.cruise_sub_layouts.speed_limit_settings import SpeedLimitSettingsLayout
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.multilang import tr, tr_noop
from openpilot.selfdrive.ui.bp.settings_defaults import recommended
from openpilot.system.ui.sunnypilot.widgets.list_view import toggle_item_sp, option_item_sp, simple_button_item_sp
# BluePilot: SectionHeader lives under the bp layer only because that is where it was first
# needed. It is a plain Widget over system.ui with no BluePilot-specific dependencies, and both
# panels drive the same scroller_tici.Scroller, so reusing it here is safe.
from openpilot.selfdrive.ui.bp.widgets.section_header import SectionHeader
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.scroller_tici import Scroller
from openpilot.system.ui.widgets.confirm_dialog import ConfirmDialog
from openpilot.system.ui.lib.application import gui_app


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
      description=recommended(tr("Use vision path predictions to estimate the appropriate speed to drive through turns ahead."), "SmartCruiseControlVision"),
      param="SmartCruiseControlVision")

    # BluePilot: curve aggressiveness, split by speed regime like the angle-steering feel factors.
    # Shown as a percentage because that is what the value is -- a scale on the computed target.
    self.scc_v_low_speed_factor = option_item_sp(
      title=tr("Curve Sensitivity (Low Speed)"),
      description=recommended(tr("How much to slow for turns taken at low speed. Above 100% slows more than "
                     "the model asks for, below 100% slows less."), "SmartCruiseControlVisionLowSpeedFactor", lambda v: f"{v}%"),
      param="SmartCruiseControlVisionLowSpeedFactor",
      min_value=50, max_value=150, value_change_step=5,
      label_callback=lambda v: f"{v}%",
      inline=True)

    self.scc_v_high_speed_factor = option_item_sp(
      title=tr("Curve Sensitivity (High Speed)"),
      description=recommended(tr("The same scale for turns taken at highway speed, where the same corner "
                     "needs a very different amount of slowing."), "SmartCruiseControlVisionHighSpeedFactor", lambda v: f"{v}%"),
      param="SmartCruiseControlVisionHighSpeedFactor",
      min_value=50, max_value=150, value_change_step=5,
      label_callback=lambda v: f"{v}%",
      inline=True)


    # BluePilot: cap on how far ICBM drops the set speed in one step
    self.icbm_max_target_drop = option_item_sp(
      title=tr("Max Set Speed Drop Per Step"),
      description=recommended(tr("How far the set speed may fall in one step for a curve or a "
                     "speed limit. This does not change how fast the car slows down -- it changes "
                     "how. Stock ACC brakes for one large drop and coasts through a series of "
                     "small ones, so smaller steps trade braking for coasting at the same net "
                     "deceleration. Watch the BRAKE LAMPS readout: regulations light the stop "
                     "lamps above 1.3 m/s2 of automatic braking, so lower this if the lamps come "
                     "on during routine slowing. 0 disables the cap. Never applies to a stop sign "
                     "or an unconfirmed vehicle -- those bypass it."), "IcbmMaxTargetDrop", self._speed_step_label),
      param="IcbmMaxTargetDrop",
      min_value=0, max_value=15, value_change_step=1,
      label_callback=self._speed_step_label,
      inline=True)

    # BluePilot: same cap in the other direction -- how fast the set speed comes back up
    self.icbm_max_target_rise = option_item_sp(
      title=tr("Max Set Speed Rise Per Step"),
      description=recommended(tr("How much the set speed may climb in one step when returning to cruise "
                     "speed after a curve or a low speed limit. ICBM holds the button rather "
                     "than tapping it, so without a cap the car accelerates back up as hard as "
                     "it can. Lower is gentler. 0 disables the cap."), "IcbmMaxTargetRise", self._speed_step_label),
      param="IcbmMaxTargetRise",
      min_value=0, max_value=15, value_change_step=1,
      label_callback=self._speed_step_label,
      inline=True)

    # BluePilot: when a driver's set-speed press stops applying
    self.icbm_baseline_reset = option_item_sp(
      title=tr("Forget My Set Speed On Limit Change"),
      description=recommended(tr("When you adjust the set speed yourself, ICBM keeps every other feature "
                     "working but aims at your number instead of the speed limit target. This is "
                     "how far the posted limit has to move before your number is discarded and "
                     "Speed Limit Assist takes over again. Curves and lead vehicles never discard "
                     "it. You can also hand it back at any time by setting the speed to exactly "
                     "the limit, or by canceling and re-engaging."), "IcbmBaselineResetDelta", lambda v: tr("Never") if v == 0 else self._speed_step_label(v)),
      param="IcbmBaselineResetDelta",
      min_value=0, max_value=30, value_change_step=1,
      label_callback=lambda v: tr("Never") if v == 0 else self._speed_step_label(v),
      inline=True)

    # BluePilot: radar-blind lead detector reach. TTC is the control that actually binds --
    # against a stopped lead TTC = dRel / v_ego, so at 65 mph 4.0 s already caps range near 116 m
    # and the distance bound never fires. Distance stays as a sanity limit.
    self.icbm_lead_max_ttc = option_item_sp(
      title=tr("Slow For Unconfirmed Vehicles"),
      description=recommended(tr("How early to slow for a vehicle the camera sees but the radar has not "
                     "confirmed -- most importantly a stopped car ahead. Measured as how many "
                     "seconds away it is. Higher reacts sooner. This is the control that changes "
                     "behavior; the distance limit below rarely comes into play."), "IcbmLeadMaxTtc", lambda v: f"{v / 10:.1f} s"),
      param="IcbmLeadMaxTtc",
      min_value=10, max_value=80, value_change_step=5,
      # Stored in tenths of a second, so the raw number is 10x what the driver should read.
      label_callback=lambda v: f"{v / 10:.1f} s",
      inline=True)

    self.icbm_lead_max_distance = option_item_sp(
      title=tr("Unconfirmed Vehicle Max Distance"),
      description=recommended(tr("A sanity limit on the setting above: never react to an unconfirmed vehicle "
                     "further away than this, however early the timing says."), "IcbmLeadMaxDistance", lambda v: f"{v} m"),
      param="IcbmLeadMaxDistance",
      min_value=40, max_value=200, value_change_step=10,
      label_callback=lambda v: f"{v} m",
      inline=True)

    # BluePilot: act on the model's own stop intent. The trigger is DEC's slow-down detection --
    # the model's trajectory falling short of what it should see at this speed.
    self.icbm_model_stop = toggle_item_sp(
      title=tr("Slow For Stop Signs And Lights"),
      description=recommended(tr("Use the driving model's own stop intent to bring the set speed down for "
                     "stop signs and red lights with no vehicle at them. This is the one case "
                     "the setting above cannot catch, since an empty intersection produces "
                     "no vehicle to measure. Weaker evidence than the vehicle case -- how long "
                     "the model insists, and a speed floor, are its only filters."), "IcbmModelStopEnabled"),
      param="IcbmModelStopEnabled")

    # BluePilot: the earliness control for the stop path. Reported "stopping for red lights a little
    # too early", and route 0000032c measured it firing at 34 mph with 193 m still to run -- 0.60
    # m/s^2, gentler than coasting.
    self.icbm_model_stop_min_decel = option_item_sp(
      title=tr("Stop Sign Sensitivity"),
      description=recommended(tr("How hard the stop would have to be braked for before slowing starts. "
                     "Lower reacts further from the sign or light; higher waits until braking is "
                     "genuinely needed. Below this the car arrives in time by coasting anyway."),
                     "IcbmModelStopMinDecel", self._decel_label),
      param="IcbmModelStopMinDecel",
      min_value=4, max_value=20, value_change_step=1,
      label_callback=self._decel_label,
      inline=True)

    # BluePilot: hold openpilot's standstill resume until the lead has actually gone
    # BluePilot: holds pinned to a place -- see pinned_holds.py for why this survives TSR working.
    self.icbm_pinned_holds = toggle_item_sp(
      title=tr("Remember Holds By Location"),
      description=recommended(tr("Tap the HOLD badge while driving to pin that hold to the spot you are in. "
                     "It comes back on its own every time you drive through there. For the few "
                     "places that need the same correction every trip: a sign the camera reads "
                     "wrong, a limit nobody drives, a school zone outside school hours. Tap a "
                     "pinned hold again to remove it."), "IcbmPinnedHoldsEnabled"),
      param="IcbmPinnedHoldsEnabled")

    self.icbm_pinned_hold_radius = option_item_sp(
      title=tr("Pinned Hold Range"),
      description=recommended(tr("How close you have to get before a pinned hold takes effect. A pin only has "
                     "to catch once, then it behaves like any other hold, so this covers GPS "
                     "wander rather than the length of the road. Raise it if a pin gets missed; "
                     "lower it if one fires on a road running alongside."), "IcbmPinnedHoldRadius", self._distance_label),
      param="IcbmPinnedHoldRadius",
      min_value=15, max_value=250, value_change_step=5,
      label_callback=self._distance_label,
      inline=True)

    # BluePilot: a pin can only be removed by driving back to it, which is fine for the one you
    # just made and useless for one set 50 miles away. This is the escape hatch.
    self.icbm_clear_pins = simple_button_item_sp(
      button_text=self._pinned_hold_count_label,
      button_width=400,
      enabled=self._has_pinned_holds,
      callback=self._clear_pinned_holds)

    self.icbm_resume_gate = toggle_item_sp(
      title=tr("Wait For The Car Ahead Before Resuming"),
      description=recommended(tr("Wait for the vehicle ahead to actually move before resuming from a stop. "
                     "Without this, openpilot requests resume from its own plan and stock ACC "
                     "accelerates toward the set speed, then brakes hard when its radar finds the "
                     "lead still close."), "IcbmResumeGateEnabled"),
      param="IcbmResumeGateEnabled")

    self.icbm_gap_control = toggle_item_sp(
      title=tr("Let openpilot Change The Follow Gap"),
      description=recommended(tr("Allow features like passing assist to briefly ask stock ACC for a closer "
                     "follow distance, then put your own setting back. openpilot presses the same gap "
                     "button you do and reads the result back from the car, so a press that does not "
                     "land is simply retried. Your own press always wins and ends the request."),
                     "IcbmGapControl"),
      param="IcbmGapControl")

    self.icbm_resume_min_gap = option_item_sp(
      title=tr("Resume Minimum Gap"),
      description=recommended(tr("How far the car ahead must have pulled away before resuming counts as safe."), "IcbmResumeMinGap", lambda v: f"{v} m"),
      param="IcbmResumeMinGap",
      min_value=2, max_value=20, value_change_step=1,
      label_callback=lambda v: f"{v} m",
      inline=True)

    self.icbm_resume_min_lead_speed = option_item_sp(
      title=tr("Resume Minimum Lead Speed"),
      description=recommended(tr("How fast the car ahead must be moving before resuming. Together with the "
                     "gap above, this is what separates it rolling away from it merely creeping."), "IcbmResumeMinLeadSpeed", self._speed_step_label),
      param="IcbmResumeMinLeadSpeed",
      min_value=1, max_value=15, value_change_step=1,
      label_callback=self._speed_step_label,
      inline=True)
    # BluePilot: the magnitude knob SCC-Map never had. MapDecel moves WHEN it starts; this moves
    # how slow it gets. Asked for after an off-ramp whose mapped target matched the yellow advisory
    # sign -- correct for a stock car, too fast for this one's retrofit PSCM to steer.
    self.scc_map_factor = option_item_sp(
      title=tr("Mapped Corner Speed - Tight"),
      description=recommended(tr("Scales the speed tight mapped corners and exit ramps are taken at. "
                     "100% uses the map's own number, which matches the posted advisory. Lower it "
                     "if the steering struggles to hold those curves at the advisory speed. This one "
                     "governs corners of 25 mph and below, blending out to the highway setting by "
                     "45 mph."),
                     "SmartCruiseControlMapFactor", self._percent_label),
      param="SmartCruiseControlMapFactor",
      min_value=50, max_value=100, value_change_step=5,
      label_callback=self._percent_label,
      inline=True)

    # FusionPilot: split from the control above on 2026-08-10. A ramp is a 25 mph corner entered at 75
    # and a sweeper is a 50 mph corner entered at 75, so one factor could not serve both -- the value
    # that made ramps steerable was cutting 5 mph off highway bends the map had already got right.
    self.scc_map_high_factor = option_item_sp(
      title=tr("Mapped Corner Speed - Highway"),
      description=recommended(tr("Scales the speed faster mapped corners are taken at, from 45 mph "
                     "upward. 100% uses the map's own number. Separate from the setting above "
                     "because a tight ramp and a highway sweeper need opposite adjustments."),
                     "SmartCruiseControlMapHighSpeedFactor", self._percent_label),
      param="SmartCruiseControlMapHighSpeedFactor",
      min_value=50, max_value=100, value_change_step=5,
      label_callback=self._percent_label,
      inline=True)


    self.scc_m_toggle = toggle_item_sp(
      title=tr("Smart Cruise Control - Map"),
      description=recommended(tr("Use map data to estimate the appropriate speed to drive through turns ahead. "
                     "Unlike the camera-based control above, this knows a turn is coming before it "
                     "can be seen, so it is the one that works on a freeway exit ramp that is still "
                     "straight where you join it."), "SmartCruiseControlMap"),
      param="SmartCruiseControlMap")

    # BluePilot: SCC-Map's one knob, and it really is one knob -- see the map_controller comment.
    self.scc_m_decel = option_item_sp(
      title=tr("Map Curve Braking Rate"),
      description=recommended(tr("How hard to slow for a turn the map knows about. This also sets how early it "
                     "starts: a gentler rate needs more distance, so the set speed begins falling "
                     "sooner. Lower this if exit ramps come up too fast. Above 1.3 m/s2 the stop "
                     "lamps light, so values past that trade the BRAKE LAMPS readout for a later, "
                     "harder slowdown."), "SmartCruiseControlMapDecel", lambda v: f"{v / 10:.1f} m/s2"),
      param="SmartCruiseControlMapDecel",
      min_value=4, max_value=25, value_change_step=1,
      label_callback=lambda v: f"{v / 10:.1f} m/s2",
      inline=True)

    self.custom_acc_toggle = toggle_item_sp(
      title=tr("Custom ACC Speed Increments"),
      description="",
      param="CustomAccIncrementsEnabled",
      callback=self._on_custom_acc_toggle)

    # BluePilot: these two had no description at all. They are not preferences -- they must match
    # what the car actually does, because ICBM counts presses against them. On this Ford a tap
    # moves the set speed 1 and a press-and-hold moves it 5, which is what the defaults say.
    self.custom_acc_short_increment = option_item_sp(
      title=tr("Short Press Increment"),
      description=recommended(tr("How far one tap of the cruise button moves the set speed. This "
                     "must match what your car actually does, not what you would prefer -- ICBM "
                     "counts button presses against this number to reach a target."),
                              "CustomAccShortPressIncrement", self._speed_step_label),
      param="CustomAccShortPressIncrement",
      min_value=1, max_value=10, value_change_step=1,
      label_callback=self._speed_step_label,
      inline=True)

    self.custom_acc_long_increment = option_item_sp(
      title=tr("Long Press Increment"),
      description=recommended(tr("How far one press-and-hold of the cruise button moves the set "
                     "speed. Same rule as above: this describes the car, not a preference. Get it "
                     "wrong and ICBM lands beside the speed it aimed for on every large change."),
                              "CustomAccLongPressIncrement", self._speed_step_label),
      param="CustomAccLongPressIncrement",
      value_map={1: 1, 2: 5, 3: 10},
      min_value=1, max_value=3, value_change_step=1,
      label_callback=self._speed_step_label,
      inline=True)

    self.sla_settings_button = simple_button_item_sp(
      button_text=lambda: tr("Speed Limit"),
      button_width=800,
      callback=lambda: self._set_current_panel(PanelType.SLA)
    )

    self.dec_toggle = toggle_item_sp(
      title=tr("Enable Dynamic Experimental Control"),
      description=recommended(tr("Enable toggle to allow the model to determine when to use sunnypilot ACC or sunnypilot End to End Longitudinal."), "DynamicExperimentalControl"),
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
      self.icbm_model_stop_min_decel,

      SectionHeader(tr("Resuming From A Stop")),
      self.icbm_pinned_holds,
      self.icbm_pinned_hold_radius,
      self.icbm_clear_pins,
      self.icbm_resume_gate,
      self.icbm_resume_min_gap,
      self.icbm_resume_min_lead_speed,
      self.icbm_gap_control,

      SectionHeader(tr("Curves")),
      self.scc_v_toggle,
      self.scc_v_low_speed_factor,
      self.scc_v_high_speed_factor,
      self.scc_m_toggle,
      self.scc_map_factor,
      self.scc_map_high_factor,
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
  def _has_pinned_holds() -> bool:
    return bool(CruiseLayout._pinned_holds())

  # Label callbacks run on every render frame while this screen is up, so the raw string is
  # compared before json.loads is allowed to run. Same mistake as the control-loop reader had,
  # cheaper here only because a settings screen is transient.
  _pins_raw = object()
  _pins_cache: list = []

  @staticmethod
  def _pinned_holds() -> list:
    from openpilot.sunnypilot.selfdrive.car.intelligent_cruise_button_management.pinned_holds import PinnedHolds
    raw = ui_state.params.get("IcbmPinnedHolds")
    if raw != CruiseLayout._pins_raw:   # value, not identity: params.get returns a fresh object
      CruiseLayout._pins_raw = raw
      CruiseLayout._pins_cache = PinnedHolds._parse(raw)
    return CruiseLayout._pins_cache

  @staticmethod
  def _pinned_hold_count_label() -> str:
    n = len(CruiseLayout._pinned_holds())
    return tr("Clear All ({n})").format(n=n) if n else tr("None Pinned")

  def _clear_pinned_holds(self) -> None:
    """Confirmed, because it is irreversible and the button sits next to two harmless toggles."""
    from openpilot.sunnypilot.selfdrive.car.intelligent_cruise_button_management.pinned_holds import PinnedHolds
    holds = PinnedHolds(ui_state.params)
    holds.update_params()
    if not holds.pins:
      return
    dialog = ConfirmDialog(
      tr("Remove all {n} pinned holds? This cannot be undone.").format(n=len(holds.pins)),
      tr("Remove All"),
      callback=lambda result: holds.clear() if result else None)
    gui_app.push_widget(dialog)

  @staticmethod
  def _distance_label(value: int) -> str:
    """BluePilot: stored in metres because that is what the geometry works in, shown in feet to a
    driver who measures in miles. Same rule as _speed_step_label below -- the unit follows the
    driver, and a settings label must never state one without asking which they use."""
    return f"{value} m" if ui_state.is_metric else f"{round(value * 3.28084 / 5) * 5} ft"

  @staticmethod
  def _percent_label(value):
    return f"{value}%"

  @staticmethod
  def _decel_label(value):
    # Stored in tenths of m/s^2. Deceleration is SI everywhere in openpilot and there is no useful
    # US customary form of it, so the number is shown as-is with its unit rather than converted.
    return f"{value / 10.:.1f} m/s²"

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
      self.icbm_model_stop_min_decel,
      self.icbm_resume_gate,
      self.icbm_resume_min_gap,
      self.icbm_resume_min_lead_speed,
      self.icbm_gap_control,
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
