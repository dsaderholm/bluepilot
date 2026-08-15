"""FusionPilot: drive the stock ACC follow-gap button, closed loop, under a lease.

WHY THIS IS CLOSED LOOP AND AN EARLIER NOTE SAID IT COULD NOT BE
----------------------------------------------------------------
CLAUDE.md used to record that the gap setting was unreadable because the GWM sends it, so commanding
a gap meant counting presses from an assumed starting position with no way to detect a missed one.
That was wrong twice over. `AccTGap_D_Dsply` lives in `BO_ 394 ACCDATA_3: 8 IPMA_ADAS` -- the CAMERA
sends it, the GWM is only a receiver on that line -- and `ford/carstate.py` ALREADY registers
ACCDATA_3 at 5 Hz and keeps the whole message in `acc_tja_status_stock_values`. Nothing new is
subscribed to make this work. The owner's "do not touch the GWM" ruling was about flashing firmware
and As-Built, never about reading a frame that is already on the bus.

So every press is verified against what the camera reports next, and a press that does not land is
simply retried. That single fact is what makes the rest of this file small.

WHAT IS ASSUMED, WHAT IS LEARNED
--------------------------------
Almost nothing is assumed, because the readback lets the car answer instead:

  - WHICH BUTTON WORKS. `Steering_Data_FD1` carries three gap signals -- `AccButtnGapIncPress`,
    `AccButtnGapDecPress` and `AccButtnGapTogglePress` -- and IPMA_ADAS is the listed receiver of
    all three. The owner's wheel only has the cycling button, so only TOGGLE is known to work from
    the driver's side; whether the camera honours INC/DEC from an injected frame is unknown and is
    NOT assumed here. The first press of a drive probes inc/dec, and a press that moves nothing
    falls back to toggle for the rest of the ignition cycle.
  - WHICH WAY THE NUMBERS RUN. Whether `Time_Gap_1` is the closest or the farthest following
    distance is not hardcoded either. The probe press reports its own direction, and an inverted
    result just sets a flag.
  - WHETHER INJECTION WORKS AT ALL. If enough presses land with no movement whatsoever, the mode
    latches unavailable for the ignition cycle and every later lease is refused. A feature that
    cannot verify itself must decline rather than press hopefully.

Note that panda does not gate these bits: `ford.h`'s Steering_Data_FD1 tx_hook checks only the
cancel and resume signals. Gap presses therefore go out whether or not controls are allowed, which
is what makes a stationary bench test possible.

THE LEASE, AND WHY IT IS ASSERTED RATHER THAN TIMED
---------------------------------------------------
The requester (passing assist) asks for a gap CONTINUOUSLY, every frame it still wants it. Silence
restores. That is deliberately not a "hold this for N seconds" grant:

  - a dead planner stops asserting, so the gap comes back on its own;
  - a dead selfdrived stops asserting, likewise;
  - there is no duration to get wrong at the requesting end, and no stored deadline to survive a
    restart with.

`MAX_LEASE_FRAMES` is a backstop for the one case assertion cannot cover -- a request stuck ON by a
wedged upstream process. It restores and then REFUSES to re-grant until the request drops to zero,
so a stuck request cannot be re-honoured in a loop.

Be honest about what the backstop costs: restoring to a longer gap while still close behind a car is
exactly the moment ACC will brake. That is accepted. It only happens on a malfunction, and the
alternative -- following at the closest setting indefinitely because a process died -- is worse.

THE DRIVER OUTRANKS THE LEASE
-----------------------------
If the gap moves when we are not expecting it to, the driver pressed their own button. The lease is
abandoned on the spot, nothing further is pressed, and the value they chose is NOT restored over.
Their press is the whole point of the control existing.
"""
from __future__ import annotations

# Signal names in Steering_Data_FD1 (0x083). All three are received by IPMA_ADAS.
SIGNAL_INCREASE = "AccButtnGapIncPress"
SIGNAL_DECREASE = "AccButtnGapDecPress"
SIGNAL_TOGGLE = "AccButtnGapTogglePress"

# Valid values of AccTGap_D_Dsply. 0 is "Not_Used" and 6/7 are "Undefined"; any of them means the
# camera is not reporting a usable setting and no lease may start.
GAP_MIN = 1
GAP_MAX = 5

# Press shape, in carcontroller frames at 100 Hz. ford/icbm.py puts at most one button frame on the
# wire every 0.05 s, so 10 frames of ON is roughly two messages -- about as short as a real press.
PRESS_ON_FRAMES = 10       # 0.10 s held
PRESS_RELEASE_FRAMES = 40  # 0.40 s released, so the camera reads a release and not a repeat
# How long to wait for the camera to report the result. ACCDATA_3 arrives at 5 Hz, so this is about
# three samples -- enough that a single dropped frame does not read as a failed press.
CONFIRM_FRAMES = 60        # 0.60 s

# One press cycle is therefore ~1.1 s, and reaching any gap from any other takes at most four of
# them: about 4.5 s worst case on the toggle path. A requester that wants the gap closed BEFORE a
# maneuver starts has to ask that far ahead.
MAX_PRESSES = 8            # four steps plus retries; beyond this something is wrong

# Consecutive presses that move nothing before injection is declared dead for the ignition cycle.
FAILED_PRESSES_UNAVAILABLE = 3

# Backstop only -- see the module docstring. Long enough that no real maneuver reaches it.
MAX_LEASE_FRAMES = 9000    # 90 s

# The readback must be a valid, unchanging value for this long before a lease may start. Guards
# against granting on a half-populated first frame after boot.
SETTLE_FRAMES = 50         # 0.5 s

# Probe mode, learned once per ignition cycle.
MODE_UNKNOWN = "unknown"
MODE_INC_DEC = "incDec"
MODE_TOGGLE = "toggle"
MODE_UNAVAILABLE = "unavailable"

_PHASE_IDLE = "idle"
_PHASE_PRESSING = "pressing"
_PHASE_RELEASING = "releasing"
_PHASE_CONFIRMING = "confirming"


class FordGapController:
  """Press the ACC gap button until the camera reports the requested setting, then restore.

  Pure logic: `update` takes the readback and the request and returns the signal name to assert
  this frame, or None. It never touches CAN, so the whole state machine is testable offline.
  """

  def __init__(self):
    self.mode = MODE_UNKNOWN
    self.inverted = False        # True once a probe shows INCREASE lowers AccTGap_D_Dsply

    self.active = False          # a lease is being honoured
    self.restore_gap = 0         # the driver's setting, captured when the lease started
    self.target = 0              # what we are pressing toward right now
    self.abandoned = False       # driver interfered, or we gave up; latched until request clears
    self.lease_frames = 0
    self.presses = 0
    self.failed_presses = 0
    self._restoring = False

    self._phase = _PHASE_IDLE
    self._phase_frames = 0
    self._gap_at_press = 0
    self._probing = False        # this press is the inc/dec probe

    self._settle_frames = 0
    self._gap_prev = 0
    self._expected_gap = 0       # what the readback should read while no press is outstanding

    # Reported for logging, never used to decide anything.
    self.last_result = ""

  @property
  def gap_readable(self) -> bool:
    return self._settle_frames >= SETTLE_FRAMES

  def _reset_press(self) -> None:
    self._phase = _PHASE_IDLE
    self._phase_frames = 0
    self._probing = False

  def _end_lease(self, why: str) -> None:
    self.active = False
    self.target = 0
    self.restore_gap = 0
    self.lease_frames = 0
    self.presses = 0
    self._restoring = False
    self.last_result = why
    self._reset_press()

  def _direction_signal(self, gap_now: int) -> str:
    """Which signal to press to move `gap_now` toward `self.target`."""
    if self.mode == MODE_TOGGLE:
      return SIGNAL_TOGGLE
    # Unknown mode probes with inc/dec: it is the capability worth discovering, and its failure
    # mode (nothing moves) is both harmless and exactly what identifies it.
    want_higher = self.target > gap_now
    if self.inverted:
      want_higher = not want_higher
    return SIGNAL_INCREASE if want_higher else SIGNAL_DECREASE

  def _judge_press(self, gap_now: int) -> None:
    """A press has completed its confirm window. Decide what the readback proved."""
    delta = gap_now - self._gap_at_press

    if delta == 0:
      self.failed_presses += 1
      if self._probing:
        # inc/dec did nothing. Toggle is the mode the owner's own wheel uses, so it is the
        # fallback rather than another guess.
        self.mode = MODE_TOGGLE
        self.last_result = "incDec ignored, using toggle"
        self.failed_presses = 0
      elif self.failed_presses >= FAILED_PRESSES_UNAVAILABLE:
        self.mode = MODE_UNAVAILABLE
        self.abandoned = True
        self.last_result = "gap injection not accepted by the camera"
      return

    self.failed_presses = 0
    if self._probing:
      # Something moved, so inc/dec is honoured. Which way it moved is the other half of the probe:
      # whether Time_Gap_5 is the longest or the shortest follow distance is not assumed anywhere.
      wanted_higher = self.target > self._gap_at_press
      self.mode = MODE_INC_DEC
      if (delta > 0) != wanted_higher:
        self.inverted = not self.inverted
        self.last_result = "incDec works, direction inverted"
      else:
        self.last_result = "incDec works"

  def update(self, gap_now: int, requested: int, driver_pressing: bool = False) -> str | None:
    """Advance one frame.

    Args:
      gap_now: AccTGap_D_Dsply as reported by the camera, or 0 if unavailable.
      requested: the gap the requester wants (GAP_MIN..GAP_MAX), or 0 for "no request".
      driver_pressing: any physical gap button is down right now.

    Returns:
      The Steering_Data_FD1 signal to assert this frame, or None.
    """
    gap_now = int(gap_now)
    requested = int(requested)
    valid = GAP_MIN <= gap_now <= GAP_MAX

    # Readback health. A value must be valid AND unchanging to count as settled, so a lease can
    # never start off a frame where the parser has not filled the message in yet.
    if valid and gap_now == self._gap_prev:
      self._settle_frames = min(self._settle_frames + 1, SETTLE_FRAMES)
    else:
      self._settle_frames = 0
    self._gap_prev = gap_now

    # The request going away clears the abandon latch -- that is the only way back from a stuck
    # request or a driver override, and it requires the requester to explicitly stop asking.
    if requested <= 0:
      self.abandoned = False

    # The driver's own button always wins, immediately and without a restore.
    if driver_pressing and self.active:
      self.abandoned = True
      self._end_lease("driver pressed the gap button")
      return None

    # Unexpected movement between our own presses is the driver too -- their press may land on a
    # frame we do not see the button down for, but the RESULT is unmistakable.
    if self.active and self._phase == _PHASE_IDLE and valid and self._expected_gap and gap_now != self._expected_gap:
      self.abandoned = True
      self._end_lease("gap changed by the driver")
      return None

    # ---- lease bookkeeping -------------------------------------------------------------------
    if self.active:
      self.lease_frames += 1
      if self.lease_frames > MAX_LEASE_FRAMES:
        # Backstop. Fall through into the restore path rather than dropping the lease outright:
        # leaving the car at the requested gap is the thing this exists to prevent.
        self.abandoned = True
        requested = 0
        self.last_result = "lease timed out, restoring"

    if requested > 0 and not self.active:
      if self.abandoned or self.mode == MODE_UNAVAILABLE or not self.gap_readable:
        return None
      if not GAP_MIN <= requested <= GAP_MAX:
        return None
      if requested == gap_now:
        return None  # nothing to do; no lease, so nothing to restore later either
      self.active = True
      self.restore_gap = gap_now
      self._expected_gap = gap_now
      self.lease_frames = 0
      self.presses = 0
      self.last_result = ""

    if not self.active:
      return None

    # What we are driving toward: the request while it stands, the driver's setting once it stops.
    self.target = requested if requested > 0 else self.restore_gap

    # The restore gets a FRESH press budget. Sharing one budget with the outbound trip meant a
    # lease that spent retries getting there could run out getting back, which leaves the car at a
    # follow distance the driver never chose -- the single worst outcome this file has.
    if requested <= 0 and not self._restoring:
      self._restoring = True
      self.presses = 0

    # ---- press state machine -----------------------------------------------------------------
    if self._phase == _PHASE_IDLE:
      if self.mode == MODE_UNAVAILABLE:
        # The camera is not honouring injected presses at all. End the lease here rather than
        # letting the press budget drain one useless press at a time -- and note that there is no
        # restore to attempt, because restoring would need the same button that just proved dead.
        self.abandoned = True
        self._end_lease("gap injection not accepted by the camera")
        return None
      if valid and gap_now == self.target:
        if requested > 0:
          self._expected_gap = gap_now
          return None  # holding the requested gap; nothing to press until it is released
        self._end_lease("restored")
        return None
      if self.presses >= MAX_PRESSES:
        # Out of attempts. If we were restoring there is nothing further to try; either way stop
        # pressing rather than hammering a control the car is not honouring.
        self.abandoned = True
        self._end_lease("gave up after %d presses" % MAX_PRESSES)
        return None
      if not valid:
        return None
      self._phase = _PHASE_PRESSING
      self._phase_frames = 0
      self._gap_at_press = gap_now
      self._probing = self.mode == MODE_UNKNOWN
      self.presses += 1

    self._phase_frames += 1

    if self._phase == _PHASE_PRESSING:
      if self._phase_frames <= PRESS_ON_FRAMES:
        return self._direction_signal(self._gap_at_press)
      self._phase = _PHASE_RELEASING
      self._phase_frames = 0
      return None

    if self._phase == _PHASE_RELEASING:
      if self._phase_frames >= PRESS_RELEASE_FRAMES:
        self._phase = _PHASE_CONFIRMING
        self._phase_frames = 0
      return None

    if self._phase == _PHASE_CONFIRMING:
      # End the window early the moment the camera reports a change -- there is nothing more to
      # learn by waiting, and on the toggle path four steps of needless waiting is seconds.
      moved = valid and gap_now != self._gap_at_press
      if moved or self._phase_frames >= CONFIRM_FRAMES:
        self._judge_press(gap_now if valid else self._gap_at_press)
        self._expected_gap = gap_now if valid else self._expected_gap
        self._reset_press()
      return None

    return None
