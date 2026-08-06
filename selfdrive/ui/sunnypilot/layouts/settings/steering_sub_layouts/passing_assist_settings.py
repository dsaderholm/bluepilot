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
The ICBM panel deliberately splits these: behavior under Cruise, display under BluePilot, on the
grounds that one changes what the car does and the other changes what you are shown. That split is
right for ICBM and wrong here, because this is a phase-1 OBSERVER. It never suggests, alerts or
steers; the readout is not a view of the feature, it is the entire output. Separating them would
put the feature in one menu and its only result in another.
"""
from collections.abc import Callable
import pyray as rl

from openpilot.common.params import Params
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.sunnypilot.widgets.list_view import toggle_item_sp, option_item_sp
from openpilot.system.ui.widgets.list_view import button_item
from openpilot.selfdrive.ui.bp.widgets.section_header import SectionHeader
from openpilot.system.ui.widgets.network import NavButton
from openpilot.system.ui.widgets.scroller_tici import Scroller
from openpilot.system.ui.widgets import Widget
# BluePilot: every control states the value it ships with, read from params_keys.h at draw time.
from openpilot.selfdrive.ui.bp.settings_defaults import recommended


class PassingAssistSettingsLayout(Widget):
  def __init__(self, back_btn_callback: Callable):
    super().__init__()
    self._params = Params()
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

  def _request_lane_display_test(self) -> None:
    """Arm the standstill lane-display walk. The standstill gate lives in the car controller, not
    here, for the same reason the blinker test's does -- a menu must not be what stops it."""
    # int, NOT str: Params enforces the registered type and writing "1" to an INT key raises.
    self._params.put("FordLaneDisplayTest", 1)

  def _initialize_items(self):
    # --- the feature itself ---
    self._enabled = toggle_item_sp(
      title=tr("Passing Assist (Log Only)"),
      description=recommended(tr("Watch for a slower vehicle ahead and work out whether a pass is worth "
                     "making and which side is clear. Nothing acts on the answer: no alert, no "
                     "steering, no set speed change. It records what it would have said so the "
                     "idea can be judged from real drives before anything is wired to it."), "PassingAssistLogEnabled"),
      param="PassingAssistLogEnabled")

    self._chime = toggle_item_sp(
      title=tr("Chime When It Decides Or Backs Out"),
      description=recommended(tr("Two short tones, so you can tell what it did without looking. A higher one "
                     "the moment a pass is worked out. A LOWER one when it lights the blinker and "
                     "then withdraws it, which is the case worth telling me about and the one you "
                     "could not hear before. Neither covers the screen. Ford sounds one before a "
                     "BlueCruise lane change for the same reason: at the moment a car moves, "
                     "nobody is reading a display."), "PassingAssistChime"),
      param="PassingAssistChime")

    # --- deciding a pass is wanted ---
    self._min_deficit = option_item_sp(
      title=tr("Slower By At Least"),
      description=recommended(tr("How far below your set speed a vehicle has to be before passing it is worth "
                     "suggesting. Below about 3 you are inside ordinary traffic variation, so it "
                     "starts firing on cars that are not really slower."), "PassingAssistMinDeficit", self._speed_label),
      param="PassingAssistMinDeficit",
      min_value=1, max_value=25, value_change_step=1,
      label_callback=self._speed_label,
      inline=True)

    self._exit_standdown = option_item_sp(
      title=tr("Stay Quiet After You Take An Exit"),
      description=recommended(tr("If you change lanes yourself into a lane that was opening up like an exit, "
                     "stay out of the way for this long. Being told to move out of your own exit "
                     "lane at the gore point is worse than useless. Any other lane change you "
                     "make only gets a few seconds, so pulling out to pass something manually "
                     "hands control straight back."), "PassingAssistExitStandDown", lambda v: tr("Off") if v == 0 else f"{v} s"),
      param="PassingAssistExitStandDown",
      min_value=0, max_value=120, value_change_step=15,
      label_callback=lambda v: tr("Off") if v == 0 else f"{v} s",
      inline=True)

    self._min_speed = option_item_sp(
      title=tr("Only Above"),
      description=recommended(tr("Below this speed a pass is not the maneuver being considered. Kept low on "
                     "purpose: stuck behind a tractor on a 55 road your cruise drags you down to "
                     "30, and that is exactly when a pass is most obviously wanted. Town driving "
                     "is already excluded by needing cruise engaged. Much below 30 the lane "
                     "detection starts calling turn pockets and driveways passing lanes."), "PassingAssistMinSpeed", self._speed_label),
      param="PassingAssistMinSpeed",
      min_value=20, max_value=60, value_change_step=5,
      label_callback=self._speed_label,
      inline=True)

    self._confirm_time = option_item_sp(
      title=tr("Confirm For"),
      description=recommended(tr("How long that vehicle must be seen before a pass is suggested. Short by "
                     "design -- this rejects a bad frame of radar tracking, it is not a waiting "
                     "period. Waiting is the behavior this feature exists to remove."), "PassingAssistConfirmTime", lambda v: f"{v} s"),
      param="PassingAssistConfirmTime",
      min_value=1, max_value=20, value_change_step=1,
      label_callback=lambda v: f"{v} s",
      inline=True)

    self._lead_braking = toggle_item_sp(
      title=tr("Wait If The Car Ahead Slams On"),
      description=recommended(tr("Do not start a pass while the vehicle in front is braking hard -- they are "
                     "usually turning off, or braking for something ahead you cannot see yet. "
                     "Only a deliberate stop counts. A car merely slowing is the best reason "
                     "there is to go round it, and this stays out of the way for that."), "PassingAssistLeadBrakingHold"),
      param="PassingAssistLeadBrakingHold")

    self._crawl_time = option_item_sp(
      title=tr("Call It A Slow Pass After"),
      description=recommended(tr("How long grinding past a car you are barely faster than counts as a pass "
                     "that is taking too long. Measured only for now -- this is the one situation "
                     "where passing assist would ever be allowed to nudge your set speed, and the "
                     "size of that nudge should come from what your own drives show rather than "
                     "from a guess."), "PassingAssistCrawlTime", lambda v: f"{v} s"),
      param="PassingAssistCrawlTime",
      min_value=3, max_value=30, value_change_step=1,
      label_callback=lambda v: f"{v} s",
      inline=True)

    self._blinker_lead = option_item_sp(
      title=tr("Signal Before Moving"),
      description=recommended(tr("How long the turn signal would be on before the lane change starts. Nothing "
                     "is actuated yet -- this drives the dry run on screen, which shows the whole "
                     "sequence a fully automatic pass would go through so it can be judged from a "
                     "real drive before anything is wired to a control. Defaults to 1 s, which is "
                     "how the owner drives; Utah asks for 2 before a lane change."), "PassingAssistBlinkerLead", lambda v: f"{v} s"),
      param="PassingAssistBlinkerLead",
      min_value=0, max_value=5, value_change_step=1,
      label_callback=lambda v: f"{v} s",
      inline=True)

    self._min_approach = option_item_sp(
      title=tr("Close In Before Passing"),
      description=recommended(tr("Hold off until the car ahead is this close, instead of pulling out as soon "
                     "as it is spotted -- which is how people actually drive. Abandoned instantly "
                     "if Ford's cruise starts slowing for that car, at any distance, so setting "
                     "it too aggressive costs a late pass rather than braking.\n"
                     "Auto uses the distance your own cruise has actually been measured starting "
                     "to brake at, plus a margin, and re-learns it every drive."), "PassingAssistMinApproach", self._distance_label),
      param="PassingAssistMinApproach",
      min_value=-1, max_value=200, value_change_step=10,
      label_callback=lambda v: (tr("Auto") if v < 0 else tr("Off") if v == 0
                                else self._distance_label(v)),
      inline=True)

    self._max_distance = option_item_sp(
      title=tr("Look Ahead"),
      description=recommended(tr("How far ahead to notice a slower vehicle. Higher decides earlier, which is "
                     "the whole point: it is what avoids stock ACC braking for a car you were "
                     "always going to pass. Beyond about 200 m there is rarely anything tracked "
                     "to decide on."), "PassingAssistMaxDistance", self._distance_label),
      param="PassingAssistMaxDistance",
      min_value=40, max_value=250, value_change_step=10,
      label_callback=self._distance_label,
      inline=True)

    # --- the lane you would move into ---
    self._adjacent_lane = toggle_item_sp(
      title=tr("Check The Lane Before Suggesting It"),
      description=recommended(tr("Use the front radar to see traffic in the next lane over, and stay quiet "
                     "when that lane is already full of vehicles no faster than the car ahead of "
                     "you. Uses the radar already fitted to the car. If the radar is not "
                     "reporting, the onroad panel says so rather than assuming the lane is clear."), "PassingAssistAdjacentLane"),
      param="PassingAssistAdjacentLane")

    # --- oncoming traffic ---
    self._oncoming_veto = toggle_item_sp(
      title=tr("Never Pass Into Oncoming Traffic"),
      description=recommended(tr("Watch the front radar for vehicles coming the other way. If any are seen, "
                     "treat that side of the road as theirs and stop suggesting passes into it. "
                     "The camera cannot tell an oncoming lane from a passing lane by itself, so "
                     "leave this on unless you only ever drive divided highways."), "PassingAssistOncomingVeto"),
      param="PassingAssistOncomingVeto")

    self._oncoming_memory = option_item_sp(
      title=tr("Remember Oncoming Traffic For"),
      description=recommended(tr("How long after meeting a vehicle that side of the road stays treated as "
                     "theirs. Per side, not per road: meeting someone on your left says nothing "
                     "about the lane on your right, and that one stays available. Long is safer "
                     "-- meeting a car tells you about the road, not just that moment, and on a "
                     "quiet road the gaps between meeting cars are exactly when a wrong "
                     "suggestion would look most convincing."), "PassingAssistOncomingMemory", lambda v: f"{v} s" if v < 60 else f"{v // 60} min" + (f" {v % 60} s" if v % 60 else "")),
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
      description=recommended(tr("On a road with oncoming traffic, a center turn lane and an ordinary passing "
                     "lane look identical to the sensors -- same width, same position, and the "
                     "paint differs only by color, which the camera does not report. On, it "
                     "assumes the worst until a vehicle is seen driving down that lane in your "
                     "direction, which is safer but quiets passing on two-lane highways with "
                     "alternating passing lanes such as US-6 and US-89. Off trades that "
                     "back. Oncoming traffic seen in the next lane blocks a pass either way."), "PassingAssistStrictTwoWay"),
      param="PassingAssistStrictTwoWay")

    # --- returning right ---
    self._keep_right = toggle_item_sp(
      title=tr("Keep Right Except To Pass"),
      description=recommended(tr("Also work out when you could return to a lane on your right because nothing "
                     "is holding you back. The hard part is that a camera cannot tell a through "
                     "lane from an exit-only one, so a suggestion here can mean take the exit. Two "
                     "things guard it: the road opening up ahead, and the lane having been "
                     "continuously there for a while. Both are unproven on real roads, which is "
                     "why this is on -- nothing acts on it, so a wrong suggestion costs a wrong "
                     "line on screen and buys a measurement."), "PassingAssistKeepRight"),
      param="PassingAssistKeepRight")

    self._keep_right_delay = option_item_sp(
      title=tr("Wait Before Moving Right"),
      description=recommended(tr("How long the lane to your right must stay clear before returning would be "
                     "suggested. Longer avoids nagging during brief gaps while you overtake a "
                     "line of vehicles."), "PassingAssistKeepRightDelay", lambda v: f"{v} s"),
      param="PassingAssistKeepRightDelay",
      min_value=3, max_value=60, value_change_step=1,
      label_callback=lambda v: f"{v} s",
      inline=True)

    self._min_lane_age = option_item_sp(
      title=tr("Lane Must Have Been There"),
      description=recommended(tr("How long the lane on your right must have been continuously present before "
                     "returning would be suggested. An exit lane appears out of nowhere; a "
                     "through lane has been beside you for miles. If the camera loses the lane "
                     "briefly the clock restarts, which just costs a few quiet seconds."), "PassingAssistMinLaneAge", lambda v: f"{v} s"),
      param="PassingAssistMinLaneAge",
      min_value=0, max_value=60, value_change_step=5,
      label_callback=lambda v: f"{v} s",
      inline=True)

    self._settle_time = option_item_sp(
      title=tr("Settle After A Pass"),
      description=recommended(tr("How long after suggesting a pass before suggesting a return. Without it, a "
                     "three-lane road with a slow left lane turns into a weave: move left, find it "
                     "no faster, get told to move back."), "PassingAssistSettleTime", lambda v: f"{v} s"),
      param="PassingAssistSettleTime",
      min_value=5, max_value=90, value_change_step=5,
      label_callback=lambda v: f"{v} s",
      inline=True)

    # --- pausing ---

    # --- what you see ---
    self._show_panel = toggle_item_sp(
      title=tr("Show The Onroad Panel"),
      description=recommended(tr("Show what the observer would have suggested and, more usefully, which check "
                     "stopped it. With nothing else wired up this readout IS the feature, so "
                     "leaving it off means the observer runs and you never see the result."), "ShowPassingAssist"),
      param="ShowPassingAssist")

    self._show_next_lane = toggle_item_sp(
      title=tr("Show Next Lane Speeds"),
      description=recommended(tr("Draw the speed and distance of the nearest vehicle in each lane beside you, "
                     "over the car itself. Turns amber when that lane is the reason no pass is "
                     "being suggested."), "ShowAdjacentLanes"),
      param="ShowAdjacentLanes")

    self._show_oncoming = toggle_item_sp(
      title=tr("Show Oncoming Speeds"),
      description=recommended(tr("Mark vehicles coming the other way as well, in red. Only ever appears where "
                     "the opposing carriageway is within radar reach -- on a divided highway it "
                     "should stay empty, and a marker there is worth telling me about. Drawn only "
                     "while a vehicle is actually being seen, never from the memory that keeps "
                     "the road classified afterwards."), "ShowOncomingSpeeds"),
      param="ShowOncomingSpeeds")

    self._show_in_cluster = toggle_item_sp(
      title=tr("Show It On The Dash"),
      description=recommended(tr("Use the instrument cluster's own lane lines to show which way it "
                                 "wants to go: the line on that side fades out, so the lane opens "
                                 "toward the gap. It cannot cover a lane departure warning -- "
                                 "openpilot does not watch for departures while it is steering, so "
                                 "that display is idle exactly when this uses it."),
                              "ShowPassingInCluster"),
      param="ShowPassingInCluster")

    # The walk that reads the cluster's vocabulary off the car. It sits directly under the toggle it
    # serves: the only reason to run it is to find out what "the line fades out" actually looks like
    # on this cluster, and only two of the five states have ever been sent to it.
    self._lane_display_test = button_item(
      lambda: tr("Show Me The Lane Lines"),
      lambda: tr("Walk"),
      lambda: tr("Press Walk with the car stopped, then watch the cluster's LEFT lane line. It "
                 "steps through all five looks it can draw, three seconds each, naming each one on "
                 "this screen. The right line stays normal green to compare against. Tell me what "
                 "each looked like and the dash display gets the one that reads best."),
      callback=self._request_lane_display_test,
    )

    # ORDERED THE WAY A DRIVER ARRIVES AT A QUESTION, not the order these were built in. They were
    # added one at a time over a long session and the first section had drifted to nine controls,
    # three of which were not about when to suggest a pass at all -- the exit stand-down is about
    # the driver's own lane change, the signal lead is about the maneuver, and the slow-pass timer
    # is about a pass already underway. Each read sensibly on its own and the screen as a whole did
    # not, which is a thing only reading it end to end can catch.
    #
    # The order follows the sequence itself: decide, move, check the lane, check for oncoming,
    # come back right. Then the things that are about the DRIVER rather than the maneuver, then
    # what appears on screen.
    return [
      self._enabled,
      self._chime,

      SectionHeader(tr("Deciding To Pass")),
      self._min_deficit,
      self._min_speed,
      self._max_distance,
      self._confirm_time,
      self._lead_braking,

      SectionHeader(tr("Moving Over")),
      self._min_approach,
      self._blinker_lead,

      SectionHeader(tr("The Lane You Would Move Into")),
      self._adjacent_lane,

      SectionHeader(tr("Oncoming Traffic")),
      self._oncoming_veto,
      self._oncoming_memory,
      self._strict_two_way,

      SectionHeader(tr("Returning Right")),
      self._keep_right,
      self._keep_right_delay,
      self._min_lane_age,
      self._settle_time,

      SectionHeader(tr("When You Take Over")),
      self._exit_standdown,

      SectionHeader(tr("A Pass That Drags")),
      self._crawl_time,

      SectionHeader(tr("On Screen")),
      self._show_panel,
      self._show_next_lane,
      self._show_oncoming,
      self._show_in_cluster,
      self._lane_display_test,
    ]

  def _render(self, rect):
    self._back_button.set_position(self._rect.x, self._rect.y + 20)
    self._back_button.render()
    content_rect = rl.Rectangle(rect.x, rect.y + self._back_button.rect.height + 40,
                                rect.width, rect.height - self._back_button.rect.height - 40)
    self._scroller.render(content_rect)

  def show_event(self):
    self._scroller.show_event()
