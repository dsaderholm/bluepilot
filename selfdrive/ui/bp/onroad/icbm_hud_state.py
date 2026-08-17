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
    without a limit at all (see `enforce_no_limit_no_hold`). Hiding it would leave a speed he
    deliberately pinned to a place governing the car with nothing on screen saying so, and the tap
    target that removes it unreachable.

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
    return (self.has_hold and (self.sla_has_limit or self.pinned)) or self.pin_suggested


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
