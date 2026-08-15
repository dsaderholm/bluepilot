"""FusionPilot: the ACC follow-gap loop, driven against a simulated camera.

The point of these tests is not that the state machine steps correctly -- it is that EVERY
unknown the design refuses to assume is actually discovered from the readback, and that every
way the loop can fail leaves the driver's own setting in place.

The simulated camera is deliberately configurable in the three ways the real one is unknown:
which buttons it honours, which direction the numbers run, and whether it honours anything at
all. The same controller has to cope with each without being told which it is facing.
"""
from __future__ import annotations

from opendbc.sunnypilot.car.ford.gap_control import (
  CONFIRM_FRAMES,
  FAILED_PRESSES_UNAVAILABLE,
  MAX_LEASE_FRAMES,
  MODE_INC_DEC,
  MODE_TOGGLE,
  MODE_UNAVAILABLE,
  PRESS_ON_FRAMES,
  SETTLE_FRAMES,
  SIGNAL_DECREASE,
  SIGNAL_INCREASE,
  SIGNAL_TOGGLE,
  FordGapController,
)


class FakeCamera:
  """A car that reports AccTGap_D_Dsply and may or may not honour an injected press.

  `report_delay` models ACCDATA_3's 5 Hz rate: a press that lands is not visible immediately, and
  the controller must not read "no change yet" as "press ignored".
  """

  def __init__(self, gap=3, honors=("inc", "dec", "toggle"), inverted=False, report_delay=10, drop_every=0):
    self.gap = gap
    self.honors = honors
    self.inverted = inverted
    self.report_delay = report_delay
    self.drop_every = drop_every   # ignore every Nth press, so retries are actually exercised
    self.reported = gap
    self.presses = []           # every signal name that produced a rising edge
    self._prev = None
    self._pending = []          # (frames_left, value)

  def step(self, signal):
    if signal is not None and signal != self._prev:
      self.presses.append(signal)
      self._apply(signal)
    self._prev = signal

    still = []
    for frames_left, value in self._pending:
      if frames_left <= 1:
        self.reported = value
      else:
        still.append((frames_left - 1, value))
    self._pending = still

  def _apply(self, signal):
    if self.drop_every and len(self.presses) % self.drop_every == 0:
      return
    gap = self.gap
    if signal == SIGNAL_INCREASE and "inc" in self.honors:
      gap += -1 if self.inverted else 1
    elif signal == SIGNAL_DECREASE and "dec" in self.honors:
      gap += 1 if self.inverted else -1
    elif signal == SIGNAL_TOGGLE and "toggle" in self.honors:
      gap = gap + 1 if gap < 5 else 1
    gap = max(1, min(5, gap))
    if gap != self.gap:
      self.gap = gap
      self._pending.append((self.report_delay, gap))

  def force(self, gap):
    """The driver reaches over and changes it themselves -- visible immediately."""
    self.gap = gap
    self.reported = gap
    self._pending = []


def drive(ctl, cam, requested, frames, driver_pressing=False):
  """Run `frames` frames, returning the signal emitted on each."""
  out = []
  for _ in range(frames):
    sig = ctl.update(cam.reported, requested, driver_pressing)
    out.append(sig)
    cam.step(sig)
  return out


def settled(gap=3, **kwargs):
  """A controller that has watched a valid, steady readback long enough to trust it."""
  cam = FakeCamera(gap=gap, **kwargs)
  ctl = FordGapController()
  drive(ctl, cam, 0, SETTLE_FRAMES + 5)
  assert ctl.gap_readable
  return ctl, cam


# --- discovering what the car will accept -------------------------------------------------------

def test_incdec_is_discovered_and_used():
  ctl, cam = settled(gap=3)
  drive(ctl, cam, 1, 600)
  assert ctl.mode == MODE_INC_DEC
  assert cam.gap == 1
  assert not ctl.inverted
  # Two decrements, no toggling: the efficient path once inc/dec is known to work.
  assert cam.presses == [SIGNAL_DECREASE, SIGNAL_DECREASE]


def test_falls_back_to_toggle_when_incdec_is_ignored():
  """The owner's wheel only has the cycling button, so toggle is the fallback, not another guess."""
  ctl, cam = settled(gap=3, honors=("toggle",))
  drive(ctl, cam, 1, 900)
  assert ctl.mode == MODE_TOGGLE
  assert cam.gap == 1
  # One wasted probe, then toggling 3 -> 4 -> 5 -> 1.
  assert cam.presses[0] == SIGNAL_DECREASE
  assert cam.presses[1:] == [SIGNAL_TOGGLE] * 3


def test_inverted_direction_is_learned_not_assumed():
  """Whether Time_Gap_5 is the longest or shortest follow distance is never hardcoded."""
  ctl, cam = settled(gap=3, inverted=True)
  drive(ctl, cam, 1, 900)
  assert ctl.mode == MODE_INC_DEC
  assert ctl.inverted
  assert cam.gap == 1


def test_gives_up_and_latches_unavailable_when_nothing_is_honoured():
  ctl, cam = settled(gap=3, honors=())
  drive(ctl, cam, 1, 1500)
  assert ctl.mode == MODE_UNAVAILABLE
  assert not ctl.active
  assert cam.gap == 3
  # It stopped pressing rather than draining the whole budget hoping.
  assert len(cam.presses) <= FAILED_PRESSES_UNAVAILABLE + 1

  # And a later request is refused outright rather than probing again every time.
  before = len(cam.presses)
  drive(ctl, cam, 0, 10)
  drive(ctl, cam, 2, 600)
  assert len(cam.presses) == before


def test_a_slow_readback_is_not_read_as_an_ignored_press():
  """ACCDATA_3 is 5 Hz. A press that landed but has not been reported yet must not count as failed."""
  ctl, cam = settled(gap=3, report_delay=CONFIRM_FRAMES - 5)
  drive(ctl, cam, 2, 900)
  assert ctl.mode == MODE_INC_DEC
  assert cam.gap == 2
  assert cam.presses == [SIGNAL_DECREASE]


# --- the lease --------------------------------------------------------------------------------

def test_silence_restores_the_drivers_setting():
  ctl, cam = settled(gap=4)
  drive(ctl, cam, 1, 600)
  assert cam.gap == 1
  drive(ctl, cam, 0, 900)          # requester stops asking -- e.g. its process died
  assert cam.gap == 4
  assert not ctl.active


def test_a_stuck_request_times_out_restores_and_is_not_re_granted():
  ctl, cam = settled(gap=4)
  drive(ctl, cam, 1, MAX_LEASE_FRAMES + 900)
  assert cam.gap == 4, "the backstop must put the driver's setting back"
  assert not ctl.active

  # Still asserted, still refused: a wedged request cannot be honoured in a loop.
  presses_before = len(cam.presses)
  drive(ctl, cam, 1, 900)
  assert cam.gap == 4
  assert len(cam.presses) == presses_before

  # Dropping the request is what clears the latch.
  drive(ctl, cam, 0, 10)
  drive(ctl, cam, 2, 600)
  assert cam.gap == 2


def test_no_lease_and_nothing_to_restore_when_already_at_the_requested_gap():
  ctl, cam = settled(gap=2)
  drive(ctl, cam, 2, 300)
  assert cam.presses == []
  assert not ctl.active


def test_refuses_to_start_when_the_gap_cannot_be_read():
  for unreadable in (0, 6, 7):
    cam = FakeCamera(gap=unreadable)
    ctl = FordGapController()
    drive(ctl, cam, 1, 600)
    assert not ctl.gap_readable
    assert not ctl.active
    assert cam.presses == [], f"pressed blind with readback {unreadable}"


def test_restore_gets_its_own_press_budget():
  """A lease that spent retries getting there must still be able to get back.

  drop_every makes the camera ignore some presses, which is the whole reason a budget can run
  short. With one shared budget the outbound trip eats most of it and the car is left following at
  a distance the driver never chose -- the worst outcome this file has.
  """
  ctl, cam = settled(gap=1, drop_every=3)
  drive(ctl, cam, 5, 2000)
  assert cam.gap == 5
  used_going_out = len(cam.presses)
  assert used_going_out > 4, "camera was not actually dropping presses; the test proves nothing"

  drive(ctl, cam, 0, 2000)
  assert cam.gap == 1, "ran out of presses restoring after a retry-heavy outbound trip"
  assert len(cam.presses) - used_going_out > 4


# --- the driver outranks everything -------------------------------------------------------------

def test_driver_button_ends_the_lease_and_their_choice_stands():
  ctl, cam = settled(gap=4)
  drive(ctl, cam, 1, 600)
  assert cam.gap == 1

  drive(ctl, cam, 1, 5, driver_pressing=True)
  assert not ctl.active
  assert ctl.abandoned

  # Their press is not pressed back over, and the request no longer moves anything.
  cam.force(3)
  presses_before = len(cam.presses)
  drive(ctl, cam, 1, 900)
  assert cam.gap == 3
  assert len(cam.presses) == presses_before


def test_unexplained_movement_is_treated_as_the_driver():
  """Their press may land on a frame we never see the button down for. The result is unmistakable."""
  ctl, cam = settled(gap=4)
  drive(ctl, cam, 2, 600)
  assert cam.gap == 2

  cam.force(5)
  drive(ctl, cam, 2, 300)
  assert not ctl.active
  assert cam.gap == 5, "pressed over a change the driver made"


# --- press shape --------------------------------------------------------------------------------

def test_press_is_a_pulse_not_a_hold():
  """A held signal is a different input to the camera than a press, and repeats are ambiguous."""
  ctl, cam = settled(gap=3)
  out = drive(ctl, cam, 2, 400)
  runs, run = [], 0
  for sig in out:
    if sig is None:
      if run:
        runs.append(run)
      run = 0
    else:
      run += 1
  if run:
    runs.append(run)
  assert runs, "never pressed"
  assert all(r == PRESS_ON_FRAMES for r in runs), runs
