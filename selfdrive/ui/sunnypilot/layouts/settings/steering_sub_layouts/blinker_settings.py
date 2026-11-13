"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FusionPilot: the turn-signal controls, in one place, under Steering.

WHY THEY MOVED
They were on the BluePilot page, mixed in with hands-free UI and battery charging, because that is
where BluePilot's own additions go. Asked directly -- "should we move this stuff into steering where
everything else is?" -- and then plainly: "let's move all your settings out of Blue Pilot, please."

He is right, and the reason is stronger than tidiness. The blinker is not a BluePilot feature; it is
the actuator a lane change is made of. Everything that decides WHEN to signal already lives under
Steering -- Customize Lane Change, Customize Passing Assist, Pause Lateral Control with Blinker --
and the controls that prove the signal itself works were two menus away from all of them.

WHAT THESE ARE FOR
Not tuning. They are the bench, and they exist because commanding a Ford's turn signal from
openpilot had to be worked out on the car rather than from a DBC. The lamp mirrors each commanded
frame exactly once and latches nothing, so send rate IS flash rate -- which is why "One Frame" gives
exactly one flash and why every earlier attempt at a steady signal fixed flashing by sending more
frames, which was the cause. Measure and Blink are the two halves of the answer: time his own
flasher, then reproduce it.
"""
from collections.abc import Callable

import pyray as rl

from openpilot.common.params import Params
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.widgets.list_view import button_item, dual_button_item
from openpilot.system.ui.widgets.network import NavButton
from openpilot.system.ui.widgets.scroller_tici import Scroller
from openpilot.system.ui.widgets import Widget
from openpilot.selfdrive.ui.bp.widgets.float_control_item import int_control_item


class BlinkerSettingsLayout(Widget):
  def __init__(self, back_btn_callback: Callable):
    super().__init__()
    self._params = Params()
    self._back_button = NavButton(tr("Back"))
    self._back_button.set_click_callback(back_btn_callback)

    items = self._initialize_items()
    self._scroller = Scroller(items, line_separator=True, spacing=0)

  def _request_blinker_test(self, side: int) -> None:
    """Arm one turn-signal pulse. Every real gate lives in the car controller, not here -- a UI
    that could be dismissed or crash must not be what stops a lamp."""
    # int, NOT str. Params enforces the registered type: PYTHON_2_CPP has (int, INT) and no
    # (str, INT), so writing "1" to an INT key raises TypeError. This used to be caught and logged,
    # which meant the button silently did nothing.
    self._params.put("FordBlinkerTest", int(side))

  def _initialize_items(self):
    # Ordered the way the job is done: establish his car's rhythm, reproduce it, then the reference
    # and the fallback knob. It read bottom-up before -- the diagnostic first, the measurement last
    # -- which is part of "it's also not obvious what those are even doing".
    #
    # Every one of these refuses unless the car is stopped, cruise is off and the driver's own stalk
    # is idle, and every one times out and self-clears. Those gates are in the car controller, not
    # in this file, because a menu is not allowed to be what stops a lamp.

    # ONE button, not two. dual_button_item forced a left and a right, so it rendered the same
    # thing twice -- "why are there two measure right blinker buttons? Obviously they are both
    # going to be the same." There is no side to choose: it watches whichever lamp the driver uses.
    self._blinker_measure = button_item(
      lambda: tr("Measure My Blinker"),
      lambda: tr("Watch"),
      lambda: tr("Press Watch, then use your own turn signal stalk within 12 seconds. Nothing is "
                 "sent to the car -- it only watches your lamp and reports how many times it "
                 "flashed and how far apart, on the driving screen. Enable Show Passing Assist to "
                 "see the result."),
      callback=lambda: self._request_blinker_test(9),
    )

    self._blinker_blink_buttons = dual_button_item(
      lambda: tr("Blink Left"),
      lambda: tr("Blink Right"),
      left_callback=lambda: self._request_blinker_test(7),
      right_callback=lambda: self._request_blinker_test(8),
      description=lambda: tr("Sends one message per blink instead of as fast as the bus allows. "
                             "Your lamp follows each message exactly once, so the rate we send at "
                             "is the rate it flashes. This should look like an ordinary turn "
                             "signal -- eight blinks, same as your stalk."),
    )

    self._blinker_edge_buttons = dual_button_item(
      lambda: tr("One Frame Left"),
      lambda: tr("One Frame Right"),
      left_callback=lambda: self._request_blinker_test(5),
      right_callback=lambda: self._request_blinker_test(6),
      description=lambda: tr("Sends exactly one message. Your lamp flashes exactly once, which "
                             "is how we learned it mirrors each message one for one rather than "
                             "latching. Kept as the reference: if this ever stops giving one "
                             "clean flash, something below it has changed."),
    )

    self._blink_period = int_control_item(
      lambda: tr("Blink Spacing If Unmeasurable (ms)"),
      lambda: tr("Normally ignored. openpilot watches your lamp and blinks in step with it, so "
                 "the rhythm is your car's own and needs no setting. This is only used if your "
                 "car never reports its lamp, leaving nothing to follow -- then it falls back to "
                 "this fixed spacing."),
      param="FordBlinkerBlinkPeriod",
      min_value=500,
      max_value=1500,
      # 10, not 25: he measured his own flasher at 760 ms and 25 steps from 500 cannot reach it.
      # A control that cannot be set to the measured answer is not a control.
      step=10,
    )

    return [
      self._blinker_measure,
      self._blinker_blink_buttons,
      self._blinker_edge_buttons,
      self._blink_period,
    ]

  def _render(self, rect):
    # The back button has to be DRAWN, not just constructed. Without these two lines the panel is
    # a room with no door: Customize Blinker opens, and nothing on screen leaves it. Every sibling
    # under steering_sub_layouts does exactly this, which is why it reads as boilerplate and is
    # the easiest thing in the file to leave out.
    self._back_button.set_position(self._rect.x, self._rect.y + 20)
    self._back_button.render()
    content_rect = rl.Rectangle(rect.x, rect.y + self._back_button.rect.height + 40,
                                rect.width, rect.height - self._back_button.rect.height - 40)
    self._scroller.render(content_rect)

  def show_event(self):
    self._scroller.show_event()
