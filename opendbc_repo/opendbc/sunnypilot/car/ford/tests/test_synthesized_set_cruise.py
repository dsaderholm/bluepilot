"""FusionPilot: the synthesized setCruise must not fire on top of a real button.

`carstate_ext` emits a `setCruise` when MAIN turns cruise on, so MAIN alone engages at the current
speed. The flag driving it -- `main_cruise_pressed_recently` -- was sticky with no bound: set on any
MAIN press, cleared only once cruise actually came on. Engage later with SET- or RES+ and it fired
anyway, on top of the real event.

`setCruise` is the one button meaning that CLEARS THE DRIVER'S HOLD and hands the speed to SLA. So a
press that should have created a hold created one and discarded it in the same frame -- his report,
"I adjust my speed with +/- and it changes the ICBM little speed number, not the max".

Measured on route 389, 2026-08-18: one raw SET- rising edge with cruise on produced BOTH
`decelCruise` and `setCruise`.
"""
from __future__ import annotations

from opendbc.car import structs

Type = structs.CarState.ButtonEvent.Type


def _ev(t, pressed=True):
  e = structs.CarState.ButtonEvent.new_message()
  e.type = t
  e.pressed = pressed
  return e


def _would_synthesize(button_events, cruise_just_enabled, main_recently, frames_apart=0):
  """The shipped mechanism, modelled: the flag is CLEARED by another cruise button, then the
  synthesis condition is evaluated `frames_apart` frames later with an empty event list.

  `frames_apart=1` is the case the first version of this guard missed. The PCM reports enabled a
  frame or two after the press, which is the whole reason `main_cruise_pressed_recently` is sticky
  -- so a guard that only inspects THIS frame's events reads False exactly when it matters.
  """
  def clears(evs):
    return any(e.pressed and e.type in (Type.setCruise, Type.decelCruise,
                                        Type.accelCruise, Type.resumeCruise) for e in evs)

  if clears(button_events):
    main_recently = False
  evs_at_enable = [] if frames_apart else button_events
  if clears(evs_at_enable):
    main_recently = False
  return bool(cruise_just_enabled and main_recently)


def test_main_alone_still_engages_at_the_current_speed():
  """The behaviour the synthesis exists for, unchanged."""
  assert _would_synthesize([_ev(Type.mainCruise)], cruise_just_enabled=True, main_recently=True)


def test_a_set_press_that_engages_does_not_also_clear_the_hold():
  """SET- carries its own meaning. Synthesizing setCruise on top of it discards the hold the press
  just created."""
  assert not _would_synthesize([_ev(Type.decelCruise)], cruise_just_enabled=True, main_recently=True)


def test_a_resume_press_that_engages_does_not_clear_the_hold():
  """RES+ off-cruise is `resumeCruise`, which the button contract says KEEPS the hold. A synthesized
  setCruise there is the exact 2026-08-04 bug arriving by another route."""
  assert not _would_synthesize([_ev(Type.resumeCruise)], cruise_just_enabled=True, main_recently=True)


def test_a_delayed_enable_still_suppresses_the_synthesis():
  """THE CASE THE FIRST GUARD MISSED. The press lands in frame N and the PCM reports enabled in
  N+1, where the event list is empty -- so a frame-local check reads False and fires anyway."""
  assert not _would_synthesize([_ev(Type.setCruise)], cruise_just_enabled=True,
                               main_recently=True, frames_apart=1)


def test_nothing_is_synthesized_when_cruise_was_already_on():
  assert not _would_synthesize([_ev(Type.mainCruise)], cruise_just_enabled=False, main_recently=True)


def test_the_real_condition_matches_this_model():
  """Pins the extract above to the shipped source, so a change there fails here rather than leaving
  these tests asserting against a copy nobody updated."""
  import inspect
  from opendbc.sunnypilot.car.ford import carstate_ext

  src = inspect.getsource(carstate_ext.CarStateExt.update)
  assert "self.main_cruise_pressed_recently = False" in src, (
    "nothing clears the sticky MAIN flag any more")
  # The clear must be driven by another cruise button, and must sit OUTSIDE the synthesis `if` --
  # a frame-local test inside it cannot survive a delayed enable.
  i = src.index("Type.resumeCruise")
  after = src[i:i + 400]
  assert "self.main_cruise_pressed_recently = False" in after, (
    "the button-press check no longer clears the flag; if it only gates the synthesis inline, a "
    "delayed enable defeats it")
