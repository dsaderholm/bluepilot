"""
FusionPilot: the phase lock. THIS IS THE THING THAT MADE THE BLINKER WORK.

Confirmed on the road 2026-08-06 after four earlier attempts failed. Extracted from
blinker_test_ext so it is a component rather than test-only code, and so it can be tested at all --
tangled up with the controller it was one of this fork's untestable surfaces, and the mechanism the
whole feature rests on had no coverage.

THE PROBLEM IS CONTENTION, NOT PACING
-------------------------------------
`BO_ 131 Steering_Data_FD1` is sent by the GATEWAY at 10 Hz carrying the driver's own stalk
position. openpilot transmits its own copy of the same ID with one field changed. Both claim the
switch and the BCM obeys whichever landed last, so our command owns the switch for however long
happens to remain before the gateway's next frame overwrites it -- somewhere between nothing and
100 ms, depending on where in the gateway's cycle we happened to land.

Four fixes all adjusted how FAST we sent. None of them addressed the other sender, which is why the
lamp flashed a different number of times every attempt and why the count depended on exactly when
the button was pressed.

THE FIX
-------
Send immediately after each received gateway frame, and count time in GATEWAY frames rather than
control frames. `CANParser.ts_nanos` gives the arrival timestamp of the message, exposed as
`CS.steering_data_ts`; a changed timestamp means a new frame just landed and the switch is ours for
the next ~100 ms. Nothing drifts through the contender's phase because we no longer have a phase of
our own -- we borrow its.

`GATEWAY_LOST_S` covers the gateway going quiet: with nobody to contend with, waiting for a frame
that is not coming would stall the command forever.
"""

DT_CTRL = 0.01
GATEWAY_PERIOD_S = 0.1          # Steering_Data_FD1, ("Steering_Data_FD1", 10) in carstate.py
GATEWAY_LOST_S = 0.5            # no frame for this long: send anyway, there is nothing to contend


class BlinkerPhaseLock:
  """Decides, per control frame, whether this is a frame to transmit the signal command on.

  Owns no CAN and knows nothing about turn signals -- it answers "is the switch ours right now, and
  does the blink pattern want it on?" so that both the bench test and any real feature ask the same
  question the same way.
  """

  def __init__(self, period_s: float = 0.76):
    self.period_s = float(period_s)
    self._gw_ts = 0          # last seen Steering_Data_FD1 arrival timestamp
    self._gw_frame = 0       # control frame it arrived on, for the lost-gateway timeout
    self._gw_count = 0       # gateway frames since arming: the only clock that matters here
    self.blinks_sent = 0     # ON phases begun, which is what a driver counts as a flash

  def arm(self, frame: int) -> None:
    """Start a fresh pattern. The phase counter starts clean so the first gateway frame after this
    begins an ON slot, rather than landing wherever the previous run left off."""
    self._gw_count = 0
    self._gw_frame = frame
    self.blinks_sent = 0

  def _slot_frames(self) -> int:
    """Gateway frames per half-period. At least one, so a period shorter than the gateway's own
    cadence degrades to alternating frames instead of dividing to zero and never turning off."""
    return max(1, int(round(self.period_s / 2.0 / GATEWAY_PERIOD_S)))

  def should_send(self, frame: int, gateway_ts: int) -> bool:
    """True on frames where the command should go out. Call once per control frame.

    Has side effects -- it advances the gateway clock and counts blinks -- so it must be called
    exactly once per frame and its answer used, not called speculatively.
    """
    fresh = gateway_ts != self._gw_ts
    if fresh:
      self._gw_ts = gateway_ts
      self._gw_frame = frame
    lost = (frame - self._gw_frame) * DT_CTRL > GATEWAY_LOST_S
    if not fresh and not lost:
      return False

    slot = self._slot_frames()
    self._gw_count += 1
    phase = (self._gw_count - 1) % (slot * 2)
    if phase >= slot:
      return False
    if phase == 0:
      # One blink per ON phase, however many gateway frames hold it up.
      self.blinks_sent += 1
    return True
