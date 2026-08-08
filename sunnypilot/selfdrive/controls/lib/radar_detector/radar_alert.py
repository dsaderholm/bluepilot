"""
BluePilot: let a radar detector alert change what Speed Limit Assist aims for.

The detector is a Valentine One Gen2 on its wired ESP bus (see esp_protocol.py for why the wire and
not Bluetooth). This module decides whether an alert is worth acting on. It does NOT decide a set
speed, and deliberately knows nothing about how the car is actuated.

THIS IS AN SLA FEATURE, NOT AN ICBM ONE
---------------------------------------
SLA already answers "what speed does the posted limit imply", via the resolver's
speed_limit_final = speed_limit + offset. A radar alert is nothing more than a temporary override of
that offset: whatever the driver normally runs -- +5 over, a percentage, banded by speed -- becomes
RadarDetectorMargin UNDER the limit for as long as the alert holds, and reverts when it clears.

Expressing it that way is the whole design, and everything good about it follows:

  - It composes. A driver who normally runs +5 gets a 6 mph change out of a 1 mph margin, because
    the alert replaces the offset rather than being subtracted from the limit in isolation.
  - Nothing downstream needs to know. SLA's state machine, its events, and whatever is actuating
    the car all consume speed_limit_final exactly as before.
  - It outlives ICBM. ICBM is an actuator adapter that exists only because stock Ford ACC will not
    take a longitudinal command, and it gets deleted the day alpha/e2e longitudinal works on this
    car. SLA does not. Neither does a radar detector. See "Name a feature for what it DOES, never
    for ICBM" in CLAUDE.md.

So there is no ACC floor here, no rate limiting, and no reference to cruise buttons. Ford's 20 mph
ACC minimum is real, but it is ICBM's floor to apply -- it is a property of the button layer, and
under alpha longitudinal openpilot can go below it. A module that clamped to 20 mph would be
quietly wrong the day that lands, and wrong in the direction of not slowing down.

Two consequences worth stating plainly rather than discovering later:

  - **No SLA, no effect.** With Speed Limit Assist off, or on a road where no limit is known, there
    is no offset to override and this does nothing. That is correct, not a gap to paper over: the
    target is defined relative to the posted limit, and a fixed drop from the current set speed
    would slow the car by the same amount on a road where the driver was already legal.
  - It only ever aims LOWER. The override is negative and replaces a normally-positive offset, so
    it cannot raise a target, cannot override a curve, and cannot pull a driver above a limit they
    are already under.

MUTE IS THE LOCKOUT -- PROBABLY. UNVERIFIED UNTIL THE HARDWARE IS HERE.
----------------------------------------------------------------------
Gating on the Soft Mute bit in Aux0 means a muted alert never moves the set speed. Manual mutes
definitely reach us that way: pressing the button on the detector is a mute, and the bit is in
every infDisplayData packet.

LOCATION lockouts are the open question, and an earlier version of this comment got it wrong. The
V1 Gen2 has NO GPS -- no chip, same as the Gen1 -- so it cannot learn locations at all. Lockouts on
a V1 live in the phone app, which on this car is Highway Radar over Bluetooth.

So whether they reach us depends on something not yet observed: when Highway Radar suppresses a
locked-out alert, does it send reqMuteOn to the detector (in which case the mute bit appears on the
wired bus and we inherit every lockout for free), or does it merely silence its own audio (in which
case we see an unmuted alert and this gate does nothing for locations)?

That is a five-minute check once the hardware is here -- watch the Aux0 mute bit while the app
suppresses a known false alarm -- and it must be done before anyone relies on it. Do not let this
comment harden into an assumption.

If it turns out not to propagate, the fallback is already identified and is better than
reimplementing lockout learning here: Highway Radar can EXPORT its lockout database to a file, so
openpilot can read the driver's accumulated lockouts directly rather than spending months
relearning what they have already taught. pinned_holds.py has the position matching for it.

WHY BARS AND NOT A RAW STRENGTH NUMBER
--------------------------------------
The threshold is in bar-graph LEDs, 0-8, because that is the number printed on the detector. A
setting the driver can check against the windshield is worth more than a unit-less byte nobody can
calibrate against anything. See strength_to_bars in esp_protocol.py -- the mapping is band-specific
and comes from the vendor's Table 9.1.

THE THRESHOLD SHIPS UNFITTED, ON PURPOSE
----------------------------------------
RadarDetectorMinBars has a defensible default and no evidence behind it. A V1 Gen2 sees Ka at ranges
where the alert is real but the encounter is minutes away, and strength is not distance -- an
instant-on hit arrives at full strength from a source that already has you. So the shipped
configuration has detection and logging ON and the offset override OFF
(RadarDetectorSlowdownEnabled), and the threshold is meant to be refitted from logged Ka encounters
on roads the owner actually drives. Do not quietly promote the default to "tuned" without that data.
"""

from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot import PARAMS_UPDATE_PERIOD
from openpilot.sunnypilot.selfdrive.controls.lib.radar_detector.esp_protocol import (
  ARROW_FRONT, ARROW_MASK as ANY_ARROW, BAND_KA, DisplayData,
)

# Continuous time a qualifying alert must hold before it overrides the offset.
#
# The V1 updates infDisplayData continuously and the bar graph is not smoothed, so a single frame
# at threshold is noise -- a passing vehicle's blind-spot monitor clips the antenna, the bar graph
# spikes for one packet. Requiring the strength to HOLD is the cheap discriminator between that and
# an approach, and unlike unconfirmed_lead's range sweep there is no geometry available here to
# cross-check against, so this is the whole filter.
MIN_PERSISTENCE_S = 1.5

# How long a qualifying alert must be absent before the override is dropped.
#
# Deliberately much longer than the trigger. Radar alerts flicker at range -- the ESP spec's own
# Tech Display section describes fringe encounters as having "an on-again, off-again quality" --
# and releasing on every gap would walk the SLA target up and down repeatedly, which is unpleasant
# however the car is being actuated.
RELEASE_S = 8.0

# Hysteresis on the way out, in bars. Releasing at the same threshold that triggered would chatter
# for an alert sitting exactly at it, which at range is the common case rather than an edge one.
RELEASE_BARS_MARGIN = 1

# Below this, acting is pointless: a limit low enough to be driven at this speed is a limit the
# driver is already managing by pedal, and an offset override would only be noise in town.
MIN_V_EGO_MS = 30 * CV.MPH_TO_MS

DEFAULT_MIN_BARS = 6
DEFAULT_MARGIN = 1        # display units under the posted limit


class RadarAlertDetector:
  def __init__(self, params: Params | None = None):
    self.params = params or Params()
    self.frame = 0

    self.enabled = False
    self.slowdown_enabled = False
    self.min_bars = DEFAULT_MIN_BARS
    self.margin = DEFAULT_MARGIN

    # Live state, all of it published for logging and the onroad readout.
    self.active = False
    self.bars = 0
    self.ka_present = False
    self.ka_ahead = False
    self.muted = False
    self.link_ok = False
    self.bogey_count = 0

    self._qualified_s = 0.0
    self._clear_s = 0.0

  def update_params(self) -> None:
    if self.frame % int(PARAMS_UPDATE_PERIOD / DT_MDL) == 0:
      self.enabled = self.params.get_bool("RadarDetectorEnabled")
      self.slowdown_enabled = self.params.get_bool("RadarDetectorSlowdownEnabled")
      self.min_bars = int(self.params.get("RadarDetectorMinBars", return_default=True))
      self.margin = int(self.params.get("RadarDetectorMargin", return_default=True))

  def offset_override(self, is_metric: bool) -> float | None:
    """The speed limit offset to use right now (m/s), or None to leave the driver's own alone.

    Negative by construction: the margin is how far UNDER the posted limit to aim. Converted here
    rather than stored in m/s so that a 1 mph setting lands on exactly 1 mph, matching how
    SpeedLimitOffsetValue is handled in the resolver.
    """
    if not self.active:
      return None
    return -float(self.margin * (CV.KPH_TO_MS if is_metric else CV.MPH_TO_MS))

  def _qualifies(self, display: DisplayData) -> bool:
    """Frame-level gates. Persistence is accumulated by the caller.

    Ka only. K band is real enforcement in places and also every automatic door and half the
    blind-spot monitors on the road; X is effectively dead for enforcement here. Neither belongs on
    a path that moves the car. Both are still logged -- the log is where the case for widening this
    would have to be made, and it has not been made yet.

    ANY DIRECTION, including behind. This was front-only, and the owner corrected it with an
    argument that is better than the one it replaced (2026-08-06).

    The original reasoning was that a Ka source behind you has already measured you, so slowing is
    theatre. The first half is true -- Ka measures in a fraction of a second and the recorded speed
    will not change. The second half assumed the only thing that matters is the number on the
    officer's display, and it is not.

    On this car, stock ACC bleeds speed off almost entirely without illuminating the brake lights.
    So the car ends up at the posted limit having shown no braking behavior at all, which is not
    what a driver reacting to being caught looks like. Whether that actually changes an officer's
    read of their own measurement is not something anyone here can test, and there is no feedback
    loop to learn from -- not being pulled over tells you nothing about why. It is his call on his
    own car, the action itself is "drive at the speed limit", and the cost of being wrong is that
    the car briefly drives slower than he set it to.

    Direction is still recorded in every log entry, so if it ever turns out front and rear deserve
    different treatment, the evidence for that will already be sitting in the file.
    """
    if not display.searching:
      return False
    if display.muted:
      return False    # the driver's own lockout, or their own mute. See the module docstring.
    if not (display.bands & BAND_KA):
      return False
    if not (display.arrows & ANY_ARROW):
      return False    # a band with no direction at all is a partial frame, not an alert
    return display.bars >= self.min_bars

  def update(self, display: DisplayData | None, v_ego: float, long_enabled: bool) -> None:
    """
    Args:
      display: the most recent decoded infDisplayData, or None if the link is down or stale. None
        is not the same as "no alert" -- it means we cannot tell, which must release rather than
        hold an override built on data that has stopped arriving.
      v_ego: current speed (m/s).
      long_enabled: longitudinal engaged and under our control.
    """
    self.update_params()
    self.frame += 1

    self.link_ok = display is not None
    if display is not None:
      self.bars = display.bars
      self.ka_present = bool(display.bands & BAND_KA)
      self.ka_ahead = self.ka_present and bool(display.arrows & ARROW_FRONT)
      self.muted = display.muted
      self.bogey_count = display.bogey_count
    else:
      # Do not carry stale indicators into the readout. A dead link must look dead on the screen,
      # not like a quiet road -- the failure mode that matters is the driver reading "no alerts"
      # off a link that stopped ten minutes ago.
      self.bars = 0
      self.ka_present = False
      self.ka_ahead = False
      self.muted = False
      self.bogey_count = 0

    if not (self.enabled and self.slowdown_enabled and long_enabled) or v_ego < MIN_V_EGO_MS:
      self._release()
      return

    if self.active:
      # Release on the alert genuinely going away, with hysteresis and a long timer -- see
      # RELEASE_S. Note this reads the RELEASE threshold, not the trigger one.
      holding = (display is not None and display.searching and not display.muted
                 and bool(display.bands & BAND_KA)
                 and display.bars >= max(self.min_bars - RELEASE_BARS_MARGIN, 1))
      if holding:
        self._clear_s = 0.0
      else:
        self._clear_s += DT_MDL
        if self._clear_s >= RELEASE_S:
          self._release()
      return

    if not (display is not None and self._qualifies(display)):
      self._qualified_s = 0.0
      return

    self._qualified_s += DT_MDL
    if self._qualified_s >= MIN_PERSISTENCE_S:
      self.active = True
      self._clear_s = 0.0

  def _release(self) -> None:
    self.active = False
    self._qualified_s = 0.0
    self._clear_s = 0.0
