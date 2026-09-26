"""FusionPilot: the ICBM hold state both screens draw, read in exactly one place.

Written 2026-08-13 for the comma 4 on-road port. The big screen and the comma 4 (`mici`) have
separate renderer trees -- `HudRendererBP` extends sunnypilot's, `MiciHudRendererBP` extends mici's
own -- so there is no shared base class to hang this on and the obvious thing is to copy the reader
into the second one.

**Do not.** Two readers of the same message drift: one gets a new field, one keeps an old enum
number, and the two screens then disagree about whether the driver has a hold at all. The drawing is
genuinely different on a 536x240 screen and has to be written twice; deciding WHAT IS TRUE does not.

The enum raw values are the reason this is worth isolating on its own:

    sendButton.raw       1 = increase, 2 = decrease   -> the +/- arrow beside the label
    overrideState.raw    1 = the driver is holding a set speed of their own

Those are positions in a capnp enum, not names, and an upstream reorder changes them silently. One
place to fix beats two places to remember.
"""
from __future__ import annotations

from dataclasses import dataclass

# capnp enum positions, named here so a reorder is a one-line fix rather than a hunt.
_SEND_BUTTON_ARROW = {1: "+", 2: "-"}
_OVERRIDE_STATE_HOLDING = 1


@dataclass
class IcbmHudState:
  """What the HOLD badge needs to know. Defaults are the no-hold state."""

  baseline: int = 0             # the driver's held set speed; 0 means no hold
  arrow: str = ""               # "+" / "-" while ICBM is moving the set speed, "" when settled
  hold_locked: bool = False     # something else owns the target, so the hold is not being honoured

  # `sla_has_limit` LIVED HERE AND IS GONE, 2026-08-22. It answered "is SLA producing a limit right
  # now", and `worth_showing` was the only thing that ever asked -- until 2026-08-20, when the limit
  # test was removed from that rule because it was hiding real holds on the roads holds are for.
  #
  # From that day it was computed on every frame of every render, on two screens, and read by
  # nothing. It was also the ONLY reason this reader touched `longitudinalPlanSP` at all, so
  # deleting it takes a whole second message out of the HUD path.
  #
  # Deleted rather than parked, per the rule about additions that stopped earning their place: a
  # field nobody reads still reads as load-bearing to whoever finds it next. If a future rule wants
  # the question again it is four lines to ask it, and it should ask with its own reason attached.

  @property
  def has_hold(self) -> bool:
    """Whether a hold exists at all, regardless of whether it is worth drawing."""
    return self.baseline > 0

  # `worth_showing` AND `display_value` LIVED HERE AND ARE GONE, 2026-09-25, with pinned holds.
  #
  # Both existed to make the badge say something a bare hold could not. `worth_showing` was
  # `has_hold or pin_suggested` and `display_value` was "the hold, or the pin being offered when
  # there is none" -- so with pins deleted each collapsed to the field it was wrapping. An alias
  # that adds nothing still reads as load-bearing to whoever finds it next, so mici's badge now
  # gates on `has_hold` and draws `baseline` directly.
  #
  # `sla_has_limit` went the same way on 2026-08-22, for the same reason.


def read_icbm_hud_state(sm) -> IcbmHudState:
  """Current hold state from `selfdriveStateSP`, or the no-hold default if it is unavailable.

  Never raises: a HUD that throws takes the whole on-road screen with it, and a missing message
  means "no hold to draw", which is exactly the default.

  NOT gated on the brake-status toggle, deliberately. Whether ICBM is holding the driver's own set
  speed or chasing Speed Limit Assist is basic state rather than a debug readout -- hiding it behind
  an unrelated toggle once meant the owner spent days unable to tell whether an override had taken.
  """
  state = IcbmHudState()
  try:
    # ONE MESSAGE. The `longitudinalPlanSP` read that used to open this function went with
    # `sla_has_limit` on 2026-08-22 -- see the note on the dataclass.
    icbm = sm['selfdriveStateSP'].intelligentCruiseButtonManagement
    state.arrow = _SEND_BUTTON_ARROW.get(icbm.sendButton.raw, "")
    if icbm.overrideState.raw == _OVERRIDE_STATE_HOLDING and icbm.vBaseline > 0:
      state.baseline = round(icbm.vBaseline)
      state.hold_locked = bool(icbm.holdSuppressed)
  except Exception:  # noqa: BLE001 -- see docstring; a HUD must not raise
    return IcbmHudState()
  return state


@dataclass
class MaxBoxState:
  """FusionPilot: what the MAX box shows. Pure, so it can be tested without raylib.

  THE BIG NUMBER IS WHAT THE CAR IS BEING DRIVEN TO. Settled with the owner on 2026-08-20 after
  walking the five on-screen speeds one at a time. It replaces "the big number is `vCruiseCluster`",
  which under ICBM is openpilot's own bookkeeping and drives nothing -- ICBM drives the car by
  tapping the stalk, so the number that means anything is ICBM's aim.

  His three cases collapse into one rule:

    hold exists              -> the hold. *"should we have the target be the hold when there is a
                                hold at all"* -- yes.
    no hold, SLA has a limit -> limit + offset. *"I like having that fall back if I cancel my
                                hold."* Unchanged from today.
    neither                  -> wherever SET left him. On a road with no limit that number is
                                arbitrary, which is exactly why a hold should take over from it.
  """
  aim: float = 0.0
  label: str = "MAX"
  label_is_number: bool = False
  hold_driving: bool = False
  # Something else owns the target, so a press cannot move the hold. The badge grayed itself out to
  # say this; the box says it by de-tinting. Kept rather than dropped because "the car is not at
  # your number" (which rank 1 already shows) is NOT the same statement as "your number is not
  # currently yours to change".
  hold_locked: bool = False


def max_box_state(hold: float, sla_fallback: float | None, set_speed: float, dash: float,
                  hold_locked: bool = False) -> MaxBoxState:
  """Resolve the big number and the label slot.

  THE LABEL SLOT CAN ONLY SAY ONE THING, so this is a ranking of what he needs to know:

    1. the DASH number, whenever the car is not at the aim. Something is actively pulling him down
       -- a curve, a lead, a limit ahead -- and that outranks everything else.
    2. the SLA FALLBACK, while a hold is driving and SLA has a limit. The number that cancelling the
       hold would give back, at full size. It already exists on the speed-limit sign, but only as
       the offset, in a corner: *"the offset is such a small number in the top right, that it's hard
       to see."* Shown exactly when it is actionable and never when it is not.
    3. the word HOLD, when a hold is driving and there is no fallback to offer. The badge used to
       be what named the number; with it gone, "MAX" over his own held speed was actively wrong.
    4. the word MAX.

  With a hold and NO limit there is no fallback to offer, so it falls through to the LABEL "HOLD"
  with his own number under it -- the common case on the roads where holds matter most.

  RANK 3 USED TO BE THE PIN BEING OFFERED. Pinned holds were deleted on 2026-09-25 -- he had asked
  three times not to have them and one had never once been created on the car -- so the ranking is
  one shorter and the corner mark on the box is gone with it.
  """
  hold_driving = hold > 0
  if hold_driving:
    aim = hold
  elif sla_fallback is not None and sla_fallback > 0:
    aim = sla_fallback
  else:
    aim = set_speed

  locked = hold_driving and hold_locked
  if aim > 0 and round(dash) != round(aim):
    return MaxBoxState(aim, str(round(dash)), True, hold_driving, hold_locked=locked)
  # A FALLBACK EQUAL TO THE AIM IS NOT WORTH SAYING, and saying it is actively harmful. Reported
  # from the car 2026-08-22 with a photo: hold 35, posted 30, offset +5 -- so the fallback WAS 35,
  # and the box drew "35" over "35" in blue with no word anywhere on it. His question was the right
  # one: *"I'm not sure if that means the hold is still there or not."*
  #
  # Rank 2 exists to answer "what would I get back if I cancelled". When the answer is the number
  # already filling the box it answers nothing, and it costs the slot that rank 4 would have used to
  # say HOLD -- which is the one thing he actually wanted to know. Fall through instead.
  if (hold_driving and sla_fallback is not None and sla_fallback > 0
      and round(sla_fallback) != round(aim)):
    return MaxBoxState(aim, str(round(sla_fallback)), True, True, hold_locked=locked)
  # RANK 3: THE WORD "HOLD", added 2026-08-22 on review, and it costs nothing to reach here.
  #
  # Deleting the badge took the only thing on screen that NAMED the number. Rank 2 covers the case
  # where a hold and a limit coexist -- the fallback number is itself the evidence a hold is on --
  # but a hold with NO limit fell through to the generic "MAX", which is not merely uninformative,
  # it is wrong: the number below it is his hold, not a maximum, and the pale-blue tint was the
  # only thing saying so on a sunlit screen.
  #
  # He hit this exact confusion once already -- "two numbers on screen, no idea which one was his".
  # It is why the deleted `worth_showing` rule existed at all. A word costs no space the label slot was
  # using for anything else, and adds no second number -- which is what he actually asked to be
  # rid of.
  return MaxBoxState(aim, "HOLD" if hold_driving else "MAX", False, hold_driving,
                     hold_locked=locked)
