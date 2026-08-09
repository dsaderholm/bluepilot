"""
FusionPilot: display-only steadying for the adjacent-lane marker.

Reported from the car, and worth quoting because it is also the diagnosis: "when a car is going in
between the speed I want to pass at and the speed I don't. Same with it coming in and out of radar
range. That's fine, we just aren't close enough, or it's near the speed. It just looks strange on
the visualization."

Exactly right on both counts -- the detector is not wrong in either case, it is reporting a genuine
boundary honestly at 20 Hz, and a boundary reported honestly at 20 Hz is a flicker. So this fixes
the picture and NOT the decision:

  - Nothing here feeds back into passing_assist, into blockedBy, or into the log. What gets
    recorded is still the raw per-frame truth, because the whole point of phase 1 is measuring how
    often these boundaries are hit.
  - Both effects are bounded and stated below, so a driver reading the marker knows the worst case:
    the color can be up to `blocking_debounce_s` stale, and a marker can persist up to
    `dropout_hold_s` after its radar track went away.

WHY A SEPARATE MODULE
It is the only part of the renderer that can be tested offline -- no raylib, no fonts, no display.
Leaving it inline would have made it the kind of timing logic that only ever gets checked by
driving the car, which is how the 100 Hz turn-signal frame reached the road.
"""

# How long a marker keeps being drawn after its radar track drops out, fading as it goes.
#
# Sized from the radar, not from taste: liveTracks arrives at ~8.3 Hz and the detector needs 3
# consecutive messages to re-confirm a vehicle, so a track that blinks out at the range edge and
# comes straight back costs ~0.36 s before it is believed again. Anything shorter than that would
# still flicker; much longer and a car that genuinely left would linger.
DROPOUT_HOLD_S = 0.8

# How long a changed blocking state must persist before the color follows it.
#
# This is a debounce, not a minimum dwell: the new value has to HOLD for this long, so a vehicle
# sitting exactly on the pass threshold never chatters at all rather than chattering once a second.
BLOCKING_DEBOUNCE_S = 1.0


class MarkerHold:
  """Per-side display state for one adjacent-lane marker. Pure timing, no drawing."""

  def __init__(self, dropout_hold_s: float = DROPOUT_HOLD_S,
               blocking_debounce_s: float = BLOCKING_DEBOUNCE_S):
    self.dropout_hold_s = dropout_hold_s
    self.blocking_debounce_s = blocking_debounce_s
    self.drawing = False        # was a marker on screen last frame, held or live
    self.gap_s = 0.0            # time since the track was last seen
    self.blocking = False       # the debounced color state
    self._pending_s = 0.0

  def reset(self) -> None:
    self.drawing = False
    self.gap_s = 0.0
    self.blocking = False
    self._pending_s = 0.0

  def update(self, dt: float, available: bool, occupied: bool, blocking: bool) -> tuple[bool, float, bool]:
    """One frame. Returns (draw, alpha, fresh_vehicle).

    `fresh_vehicle` means the position filters must be reset rather than lerped -- the marker is
    starting from nothing, so the previous values belong to a different car.
    """
    # Unavailable is not a dropout and must never be dressed up as one. A dead radar, a service
    # that stopped publishing: the marker goes at once, because holding a stale car on screen while
    # the sensor that found it is gone is the one failure this must not have.
    if not available:
      was = self.drawing
      self.reset()
      return False, 0.0, was

    # Color debounce. Runs whether or not a marker is drawn, so a car that drops out and returns
    # does not also restart its color timer.
    if blocking != self.blocking:
      self._pending_s += dt
      if self._pending_s >= self.blocking_debounce_s:
        self.blocking = blocking
        self._pending_s = 0.0
    else:
      self._pending_s = 0.0

    if occupied:
      fresh = not self.drawing
      self.drawing = True
      self.gap_s = 0.0
      return True, 1.0, fresh

    if not self.drawing:
      return False, 0.0, False

    self.gap_s += dt
    if self.gap_s >= self.dropout_hold_s:
      self.drawing = False
      self.gap_s = 0.0
      return False, 0.0, False

    # Fades out over the hold. A marker that vanished at full brightness would read as a car that
    # was there a moment ago; one that is visibly dimming reads as "losing this", which is true.
    return True, max(0.0, 1.0 - self.gap_s / self.dropout_hold_s), False
