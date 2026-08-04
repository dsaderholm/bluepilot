"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

BluePilot: every passing-assist control, in one place.

WHY IT LIVES UNDER STEERING
It was in the Cruise panel, and only because the observer is implemented inside
longitudinal_planner.py -- which is where the lead and the set speed already were, not a statement
about what the feature is. To a driver it does one thing: tell you which LANE to be in. That is a
lane-change feature, so it belongs next to Customize Lane Change, and it now sits beside it.

WHY THE DISPLAY TOGGLES ARE HERE TOO
The ICBM panel deliberately splits these: behaviour under Cruise, display under BluePilot, on the
grounds that one changes what the car does and the other changes what you are shown. That split is
right for ICBM and wrong here, because this is a phase-1 OBSERVER. It never suggests, alerts or
steers; the readout is not a view of the feature, it is the entire output. Separating them would
put the feature in one menu and its only result in another.
"""
from collections.abc import Callable
import pyray as rl

from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.sunnypilot.widgets.list_view import toggle_item_sp, option_item_sp
from openpilot.selfdrive.ui.bp.widgets.section_header import SectionHeader
from openpilot.system.ui.widgets.network import NavButton
from openpilot.system.ui.widgets.scroller_tici import Scroller
from openpilot.system.ui.widgets import Widget


class PassingAssistSettingsLayout(Widget):
  def __init__(self, back_btn_callback: Callable):
    super().__init__()
    self._back_button = NavButton(tr("Back"))
    self._back_button.set_click_callback(back_btn_callback)

    items = self._initialize_items()
    self._scroller = Scroller(items, line_separator=True, spacing=0)

  @staticmethod
  def _speed_label(value: int) -> str:
    """Follows the driver's mph/km-h choice rather than assuming one, like the ICBM panel."""
    return f'{value} {"km/h" if ui_state.is_metric else "mph"}'

  @staticmethod
  def _distance_label(value: int) -> str:
    return f"{value} m" if ui_state.is_metric else f"{round(value * 3.28084 / 10) * 10} ft"

  def _initialize_items(self):
    # --- the feature itself ---
    self._enabled = toggle_item_sp(
      title=tr("Passing Assist (Log Only)"),
      description=tr("Watch for a slower vehicle ahead and work out whether a pass is worth "
                     "making and which side is clear. Nothing acts on the answer: no alert, no "
                     "steering, no set speed change. It records what it would have said so the "
                     "idea can be judged from real drives before anything is wired to it."),
      param="PassingAssistLogEnabled")

    # --- deciding a pass is wanted ---
    self._min_deficit = option_item_sp(
      title=tr("Slower By At Least"),
      description=tr("How far below your set speed a vehicle has to be before passing it is worth "
                     "suggesting. Below about 3 you are inside ordinary traffic variation, so it "
                     "starts firing on cars that are not really slower."),
      param="PassingAssistMinDeficit",
      min_value=1, max_value=25, value_change_step=1,
      label_callback=self._speed_label,
      inline=True)

    self._confirm_time = option_item_sp(
      title=tr("Confirm For"),
      description=tr("How long that vehicle must be seen before a pass is suggested. Short by "
                     "design -- this rejects a bad frame of radar tracking, it is not a waiting "
                     "period. Waiting is the behaviour this feature exists to remove."),
      param="PassingAssistConfirmTime",
      min_value=1, max_value=20, value_change_step=1,
      label_callback=lambda v: f"{v} s",
      inline=True)

    self._blinker_lead = option_item_sp(
      title=tr("Signal Before Moving"),
      description=tr("How long the turn signal would be on before the lane change starts. Nothing "
                     "is actuated yet -- this drives the dry run on screen, which shows the whole "
                     "sequence a fully automatic pass would go through so it can be judged from a "
                     "real drive before anything is wired to a control."),
      param="PassingAssistBlinkerLead",
      min_value=0, max_value=5, value_change_step=1,
      label_callback=lambda v: f"{v} s",
      inline=True)

    self._min_approach = option_item_sp(
      title=tr("Close In Before Passing"),
      description=tr("Hold off until the car ahead is this close, instead of pulling out as soon "
                     "as it is spotted -- which is how people actually drive. Abandoned instantly "
                     "if Ford's cruise starts slowing for that car, at any distance, so setting "
                     "it too aggressive costs a late pass rather than braking. Off by default: "
                     "the right value sits just beyond where your cruise starts braking, and that "
                     "distance is being measured on your drives before it gets a default."),
      param="PassingAssistMinApproach",
      min_value=0, max_value=200, value_change_step=10,
      label_callback=lambda v: tr("Off") if v == 0 else f"{v} m",
      inline=True)

    self._max_distance = option_item_sp(
      title=tr("Look Ahead"),
      description=tr("How far ahead to notice a slower vehicle. Higher decides earlier, which is "
                     "the whole point: it is what avoids stock ACC braking for a car you were "
                     "always going to pass. Beyond about 200 m there is rarely anything tracked "
                     "to decide on."),
      param="PassingAssistMaxDistance",
      min_value=40, max_value=250, value_change_step=10,
      label_callback=self._distance_label,
      inline=True)

    # --- the lane you would move into ---
    self._adjacent_lane = toggle_item_sp(
      title=tr("Check The Lane Before Suggesting It"),
      description=tr("Use the front radar to see traffic in the next lane over, and stay quiet "
                     "when that lane is already full of vehicles no faster than the car ahead of "
                     "you. Uses the radar already fitted to the car. If the radar is not "
                     "reporting, the onroad panel says so rather than assuming the lane is clear."),
      param="PassingAssistAdjacentLane")

    # --- two-way roads ---
    self._oncoming_veto = toggle_item_sp(
      title=tr("Never Pass Into Oncoming Traffic"),
      description=tr("Watch the front radar for vehicles coming the other way. If any are seen, "
                     "treat that side of the road as theirs and stop suggesting passes into it. "
                     "The camera cannot tell an oncoming lane from a passing lane by itself, so "
                     "leave this on unless you only ever drive divided highways."),
      param="PassingAssistOncomingVeto")

    self._oncoming_memory = option_item_sp(
      title=tr("Remember A Two-Way Road For"),
      description=tr("How long after seeing an oncoming vehicle the road stays classified as "
                     "two-way. Long is safer: meeting a car tells you about the road, not just "
                     "that moment, and on a quiet road the gaps between meeting cars are exactly "
                     "when a wrong suggestion would look most convincing."),
      param="PassingAssistOncomingMemory",
      min_value=15, max_value=600, value_change_step=15,
      label_callback=lambda v: f"{v} s" if v < 60 else f"{v // 60} min" + (f" {v % 60} s" if v % 60 else ""),
      inline=True)

    # A two-state option item reading "Turn Lane / Passing Lane" would say more than a toggle
    # does, but the param is registered BOOL and Params only accepts a python bool for a BOOL key
    # -- PYTHON_2_CPP has (bool, BOOL) and no (int, BOOL). An option item writes ints, so it would
    # raise on first use. The title carries the meaning instead.
    self._strict_two_way = toggle_item_sp(
      title=tr("Assume An Unknown Middle Lane Is A Turn Lane"),
      description=tr("On a road with oncoming traffic, a centre turn lane and an ordinary passing "
                     "lane look identical to the sensors -- same width, same position, and the "
                     "paint differs only by colour, which the camera does not report. On, it "
                     "assumes the worst until a vehicle is seen driving down that lane in your "
                     "direction, which is safer but quiets passing on two-lane highways with "
                     "alternating passing lanes such as US-6 and US-89. Off trades that "
                     "back. Oncoming traffic seen in the next lane blocks a pass either way."),
      param="PassingAssistStrictTwoWay")

    # --- returning right ---
    self._keep_right = toggle_item_sp(
      title=tr("Keep Right Except To Pass"),
      description=tr("Also work out when you could return to a lane on your right because nothing "
                     "is holding you back. Off by default: the camera cannot tell a through lane "
                     "from an exit-only or merge lane, so a suggestion here can mean take the "
                     "exit. Exit detection watches whether the road opens up ahead, which helps "
                     "but is unproven."),
      param="PassingAssistKeepRight")

    self._keep_right_delay = option_item_sp(
      title=tr("Wait Before Moving Right"),
      description=tr("How long the lane to your right must stay clear before returning would be "
                     "suggested. Longer avoids nagging during brief gaps while you overtake a "
                     "line of vehicles."),
      param="PassingAssistKeepRightDelay",
      min_value=3, max_value=60, value_change_step=1,
      label_callback=lambda v: f"{v} s",
      inline=True)

    self._min_lane_age = option_item_sp(
      title=tr("Lane Must Have Been There"),
      description=tr("How long the lane on your right must have been continuously present before "
                     "returning would be suggested. An exit lane appears out of nowhere; a "
                     "through lane has been beside you for miles. If the camera loses the lane "
                     "briefly the clock restarts, which just costs a few quiet seconds."),
      param="PassingAssistMinLaneAge",
      min_value=0, max_value=60, value_change_step=5,
      label_callback=lambda v: f"{v} s",
      inline=True)

    self._settle_time = option_item_sp(
      title=tr("Settle After A Pass"),
      description=tr("How long after suggesting a pass before suggesting a return. Without it, a "
                     "three-lane road with a slow left lane turns into a weave: move left, find it "
                     "no faster, get told to move back."),
      param="PassingAssistSettleTime",
      min_value=5, max_value=90, value_change_step=5,
      label_callback=lambda v: f"{v} s",
      inline=True)

    # --- pausing ---
    self._suspend_minutes = option_item_sp(
      title=tr("Pause For"),
      description=tr("How long a pause lasts. Pause by tapping the onroad panel or pressing the "
                     "LKA button on the stalk -- for construction zones, weather, or anywhere the "
                     "lane markings are unusual. It resumes on its own so it cannot be left "
                     "switched off and forgotten. Tap again to resume immediately."),
      param="PassingAssistSuspendMinutes",
      min_value=1, max_value=120, value_change_step=1,
      label_callback=lambda v: f"{v} min",
      inline=True)

    # --- what you see ---
    self._show_panel = toggle_item_sp(
      title=tr("Show The Onroad Panel"),
      description=tr("Show what the observer would have suggested and, more usefully, which check "
                     "stopped it. With nothing else wired up this readout IS the feature, so "
                     "leaving it off means the observer runs and you never see the result."),
      param="ShowPassingAssist")

    self._show_next_lane = toggle_item_sp(
      title=tr("Show Next Lane Speeds"),
      description=tr("Draw the speed and distance of the nearest vehicle in each lane beside you, "
                     "over the car itself. Turns amber when that lane is the reason no pass is "
                     "being suggested."),
      param="ShowAdjacentLanes")

    return [
      self._enabled,

      SectionHeader(tr("When To Suggest A Pass")),
      self._min_deficit,
      self._confirm_time,
      self._blinker_lead,
      self._max_distance,
      self._min_approach,

      SectionHeader(tr("The Lane You Would Move Into")),
      self._adjacent_lane,

      SectionHeader(tr("Two-Way Roads")),
      self._oncoming_veto,
      self._oncoming_memory,
      self._strict_two_way,

      SectionHeader(tr("Returning Right")),
      self._keep_right,
      self._keep_right_delay,
      self._min_lane_age,
      self._settle_time,

      SectionHeader(tr("Pausing")),
      self._suspend_minutes,

      SectionHeader(tr("On Screen")),
      self._show_panel,
      self._show_next_lane,
    ]

  def _render(self, rect):
    self._back_button.set_position(self._rect.x, self._rect.y + 20)
    self._back_button.render()
    content_rect = rl.Rectangle(rect.x, rect.y + self._back_button.rect.height + 40,
                                rect.width, rect.height - self._back_button.rect.height - 40)
    self._scroller.render(content_rect)

  def show_event(self):
    self._scroller.show_event()
