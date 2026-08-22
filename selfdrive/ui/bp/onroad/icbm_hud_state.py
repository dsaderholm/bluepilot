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
    baselineSource.raw   4 = BaselineSource.pinned

Those are positions in a capnp enum, not names, and an upstream reorder changes them silently. One
place to fix beats two places to remember.
"""
from __future__ import annotations

from dataclasses import dataclass

# capnp enum positions, named here so a reorder is a one-line fix rather than a hunt.
_SEND_BUTTON_ARROW = {1: "+", 2: "-"}
_OVERRIDE_STATE_HOLDING = 1
_BASELINE_SOURCE_PINNED = 4


@dataclass
class IcbmHudState:
  """What the HOLD badge needs to know. Defaults are the no-hold state."""

  baseline: int = 0             # the driver's held set speed; 0 means no hold
  arrow: str = ""               # "+" / "-" while ICBM is moving the set speed, "" when settled
  hold_locked: bool = False     # something else owns the target, so the hold is not being honoured
  pinned: bool = False          # created by a pin, so tapping the badge removes it
  pin_suggested: bool = False   # this place is a candidate for pinning
  pin_suggestion: int = 0       # the speed being offered, for when there is no hold to show

  # Is Speed Limit Assist actually producing a limit right now? NOT whether it is switched on --
  # SLA stays "active" on a road with no limit data, which is a documented trap in this fork.
  sla_has_limit: bool = False

  @property
  def has_hold(self) -> bool:
    """Whether a hold exists at all, regardless of whether it is worth drawing."""
    return self.baseline > 0

  @property
  def worth_showing(self) -> bool:
    """Whether the HOLD badge tells the driver anything MAX does not.

    WITHOUT SPEED LIMIT ASSIST, THE HOLD IS ALREADY THE MAX SPEED. The controller sets
    `v_baseline = v_cruise_cluster` when a press creates one, and falls back to `v_cruise_cluster`
    when there is none -- so ICBM aims at the driver's own number either way. Curves and leads dip
    the dash below it and it returns there. Drawing a second readout of the same value invents a
    concept the driver then has to learn, and an owner running without SLA reported exactly that
    confusion: two numbers on screen, no idea which one was his.

    So the badge appears only when SLA has a real limit -- which is precisely when "which number
    wins" is a live question and the two can genuinely differ.

    A PINNED hold is the exception, because as of 2026-08-15 it is the only hold that can exist
    without a limit at all (see `enforce_hold_policy`). Hiding it would leave a speed he
    deliberately pinned to a place governing the car with nothing on screen saying so, and the tap
    target that removes it unreachable.

    THAT PREMISE EXPIRED ON 2026-08-19 AND THIS RULE DID NOT FOLLOW IT. `enforce_hold_policy` was
    rekeyed that day from "is a limit known this frame" to "is SLA in assist mode", precisely so a
    hold would SURVIVE a coverage gap and let a no-limit place be pinned. From that moment an
    ordinary press-hold could exist with no limit -- so `sla_has_limit` stopped meaning "a hold is
    worth drawing" and started hiding real holds on the roads the policy change existed to serve.

    Reported 2026-08-20: *"when I do plus and minus, when SLA doesn't have a number, it should
    change my max speed and set up a hold at the same time."* Both halves were already the intent;
    the second half just could not be seen. A hold governing the car with nothing on screen saying
    so is the same defect the pinned exception above was written to prevent, reached a different
    way.

    So the limit test is gone: A HOLD THAT EXISTS IS DRAWN. The original confusion this rule
    addressed -- two numbers, neither labelled -- is now answered by the mode instead. With SLA off,
    informational or warning, `enforce_hold_policy` clears the baseline outright, so `has_hold` is
    False and no badge appears; that owner never sees a second number. In assist mode the two
    numbers are *supposed* to agree -- his words, *"max speed and hold to be together and the same"*
    -- so a badge matching MAX is confirmation that the press took, not a competing readout.

    AND A STANDING PIN SUGGESTION IS THE SECOND EXCEPTION, added 2026-08-17 to fix a hole the first
    one left open. THE BADGE IS THE ONLY TAP TARGET FOR PINNING -- `_hold_rect` is set where the
    badge is drawn and cleared to None everywhere else. So with no limit, no hold and therefore no
    badge, there was no way to CREATE a pin at all on exactly the roads he says pins are for. His
    device proved it: 6 KB of hold observations accumulating, and `IcbmPinnedHolds` still `[]`.

    When there is a suggestion but no hold, the badge shows the SUGGESTED speed -- see
    `display_value`. That is not a second number competing with MAX; it is an offer, and tapping it
    is the only way to accept.

    This is a DISPLAY rule. The hold itself is unchanged and still governs the car; hiding a
    readout must never change what the car does.
    """
    # The suggestion term no longer needs `and not self.has_hold`. It carried that guard only to
    # stop a standing suggestion re-exposing a hold `sla_has_limit` was hiding; with nothing hidden
    # there is no leak to plug, and `display_value` already prefers the hold over the suggestion
    # when both exist.
    return self.has_hold or self.pin_suggested


  @property
  def display_value(self) -> int:
    """The number on the badge: the hold when there is one, otherwise the pin being offered.

    Without this the badge drawn for a bare suggestion would read `0`, since the drawing code takes
    `baseline` directly and a suggestion is not a hold.
    """
    return self.baseline if self.has_hold else self.pin_suggestion


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
    try:
      sl = sm['longitudinalPlanSP'].speedLimit
      state.sla_has_limit = bool(sl.resolver.speedLimitValid and sl.resolver.speedLimit > 0)
    except Exception:  # noqa: BLE001 -- no SLA data reads as "no limit", which hides the badge
      state.sla_has_limit = False

    icbm = sm['selfdriveStateSP'].intelligentCruiseButtonManagement
    state.arrow = _SEND_BUTTON_ARROW.get(icbm.sendButton.raw, "")
    # OUTSIDE the hold branch below, deliberately. A suggestion is offered precisely when there is
    # no hold yet, so reading it only when one exists made it unreachable in the case it is for.
    state.pin_suggestion = round(icbm.pinSuggestion)
    state.pin_suggested = icbm.pinSuggestion > 0
    if icbm.overrideState.raw == _OVERRIDE_STATE_HOLDING and icbm.vBaseline > 0:
      state.baseline = round(icbm.vBaseline)
      state.hold_locked = bool(icbm.holdSuppressed)
      state.pinned = icbm.baselineSource.raw == _BASELINE_SOURCE_PINNED
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
  # The corner mark on the box, which is all that is left of the HOLD badge after 2026-08-22.
  # `pinned` says this hold came from a pin and tapping removes it; `pin_offer` says there is no
  # hold and tapping would CREATE one at the offered speed.
  pinned: bool = False
  pin_offer: bool = False
  # Something else owns the target, so a press cannot move the hold. The badge grayed itself out to
  # say this; the box says it by de-tinting. Kept rather than dropped because "the car is not at
  # your number" (which rank 1 already shows) is NOT the same statement as "your number is not
  # currently yours to change".
  hold_locked: bool = False


def max_box_state(hold: float, sla_fallback: float | None, set_speed: float, dash: float,
                  pin_suggestion: float = 0.0, pinned: bool = False,
                  hold_locked: bool = False) -> MaxBoxState:
  """Resolve the big number and the label slot.

  THE LABEL SLOT CAN ONLY SAY ONE THING, so this is a ranking of what he needs to know:

    1. the DASH number, whenever the car is not at the aim. Something is actively pulling him down
       -- a curve, a lead, a limit ahead -- and that outranks everything else.
    2. the SLA FALLBACK, while a hold is driving and SLA has a limit. The number that cancelling the
       hold would give back, at full size. It already exists on the speed-limit sign, but only as
       the offset, in a corner: *"the offset is such a small number in the top right, that it's hard
       to see."* Shown exactly when it is actionable and never when it is not.
    3. the PIN BEING OFFERED, when there is no hold. Added 2026-08-22 -- the HOLD badge used to
       carry this number and there is no badge any more.
    4. the word HOLD, when a hold is driving and there is no fallback to offer. The badge used to
       be what named the number; with it gone, "MAX" over his own held speed was actively wrong.
    5. the word MAX.

  With a hold and NO limit there is no fallback to offer, so it falls through to the LABEL "HOLD"
  with his own number under it -- the common case on the roads where holds matter most.

  A PIN SUGGESTION IS NOT A HOLD and must never reach `hold`: it is an offer made where no hold
  exists, and letting it move the big number would display a speed the car is not driving to. It
  reaches the LABEL instead, which is the slot for numbers that are not the aim.

  A PINNED hold, by contrast, IS a hold -- it drives the car exactly like a pressed one. The badge
  used to be what told them apart; now it is `pinned`, drawn as a dot in the box's corner.
  """
  hold_driving = hold > 0
  offer = (not hold_driving) and pin_suggestion > 0
  pin_dot = hold_driving and pinned
  if hold_driving:
    aim = hold
  elif sla_fallback is not None and sla_fallback > 0:
    aim = sla_fallback
  else:
    aim = set_speed

  locked = hold_driving and hold_locked
  if aim > 0 and round(dash) != round(aim):
    return MaxBoxState(aim, str(round(dash)), True, hold_driving,
                       pinned=pin_dot, pin_offer=offer, hold_locked=locked)
  if hold_driving and sla_fallback is not None and sla_fallback > 0:
    return MaxBoxState(aim, str(round(sla_fallback)), True, True,
                       pinned=pin_dot, pin_offer=offer, hold_locked=locked)
  # RANK 3, added 2026-08-22 when the HOLD badge was deleted: the PIN BEING OFFERED.
  #
  # The badge used to carry this number (`display_value` fell back to `pin_suggestion` when there
  # was no hold), and deleting it left the offer with a tap target and no way to say what speed it
  # was for. It cannot become the big number -- that is what the car is being driven to, and an
  # offer is not -- so the label slot is the only place it can go.
  #
  # Unambiguous against rank 2 because the two are mutually exclusive by construction: the fallback
  # is shown only while a hold IS driving, the offer only when none is. Below both of them because
  # a pin is never urgent and the dash number always is.
  if offer:
    return MaxBoxState(aim, str(round(pin_suggestion)), True, False, pin_offer=True)
  # RANK 4: THE WORD "HOLD", added 2026-08-22 on review, and it costs nothing to reach here.
  #
  # Deleting the badge took the only thing on screen that NAMED the number. Rank 2 covers the case
  # where a hold and a limit coexist -- the fallback number is itself the evidence a hold is on --
  # but a hold with NO limit fell through to the generic "MAX", which is not merely uninformative,
  # it is wrong: the number below it is his hold, not a maximum, and the pale-blue tint was the
  # only thing saying so on a sunlit screen.
  #
  # He hit this exact confusion once already, which is the whole reason `worth_showing` exists:
  # "two numbers on screen, no idea which one was his". A word costs no space the label slot was
  # using for anything else, and adds no second number -- which is what he actually asked to be
  # rid of.
  return MaxBoxState(aim, "HOLD" if hold_driving else "MAX", False, hold_driving,
                     pinned=pin_dot, pin_offer=False, hold_locked=locked)
