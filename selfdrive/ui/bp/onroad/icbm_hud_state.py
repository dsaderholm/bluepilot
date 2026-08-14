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

  @property
  def has_hold(self) -> bool:
    """Whether there is a hold to draw at all. The badge's own visibility test."""
    return self.baseline > 0


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
    icbm = sm['selfdriveStateSP'].intelligentCruiseButtonManagement
    state.arrow = _SEND_BUTTON_ARROW.get(icbm.sendButton.raw, "")
    if icbm.overrideState.raw == _OVERRIDE_STATE_HOLDING and icbm.vBaseline > 0:
      state.baseline = round(icbm.vBaseline)
      state.hold_locked = bool(icbm.holdSuppressed)
      state.pinned = icbm.baselineSource.raw == _BASELINE_SOURCE_PINNED
      state.pin_suggested = icbm.pinSuggestion > 0
  except Exception:  # noqa: BLE001 -- see docstring; a HUD must not raise
    return IcbmHudState()
  return state
