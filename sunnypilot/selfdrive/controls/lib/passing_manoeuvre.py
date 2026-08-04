"""
BluePilot: the manoeuvre passing assist WOULD perform, run as a dry run.

Nothing here actuates. It consumes the detector's per-frame verdict and models the full sequence a
fully-automatic pass would go through -- spot the slow car, confirm it, signal, wait, cross, drop
the signal -- so the sequence can be watched on a real drive and judged before anything is wired to
a control.

WHY THIS IS THE THING WORTH BUILDING NEXT
The detector already answers "would I suggest a pass right now". That is a single frame's verdict,
and it is NOT the question that decides whether an automatic system works. The question is whether
the verdict HOLDS STILL long enough to act on. A gate that flickers is invisible in a
frame-by-frame display and fatal to a manoeuvre: the blinker goes on, a gate blinks red, the
blinker goes off, and the car has lied to the traffic behind it.

So the number this exists to produce is `aborts` -- how many times a sequence got as far as
signalling and then had to back out. On a drive where that is zero or near it, the sequence is
sound. Where it is not, it names exactly which gate is unstable, and no amount of reasoning about
the code would have found it.

THE POINT OF NO RETURN
Gates stop being able to abort once the crossing starts. A real car cannot un-change lanes halfway
across, and a model that pretends otherwise would report a clean sequence that reality could not
have delivered. The driver can always abort -- that is not a gate, that is the driver taking their
car back, and it is the one input that overrides everything at any phase.

WHAT IS ESTIMATED, AND SAID SO
CHANGE_DURATION_S is a nominal lane change, not a measurement. Everything before it is real: the
confirmation is the detector's own timer, the signal hold is the configured one, and the gates are
the live ones. Only the crossing is assumed, because nothing is steering.
"""

from cereal import custom
from openpilot.common.realtime import DT_MDL

Side = custom.LongitudinalPlanSP.PassingAssist.Side
Phase = custom.LongitudinalPlanSP.PassingAssist.Manoeuvre

# How long the blinker is held before lateral motion begins. The owner's own habit: "I put the
# blinker on, wait a second, and then change lanes."
#
# The signal goes on the MOMENT a slow car is spotted with a clear lane beside it -- not once the
# confirmation finishes. The owner, on being told otherwise: "It should come on instantly telling
# drivers I want to change lanes... I want this to realize the car is slow faster than I can and
# change lanes as fast as possible."
#
# So the two clocks OVERLAP. The blinker is lit while the confirmation is still running underneath
# it, and the crossing begins when both are satisfied -- max(confirm, lead), not confirm + lead. If
# the confirmation fails or a gate closes before then, the signal goes out and it is counted as an
# abort, which is the honest cost of signalling early and exactly what the count is for.
DEFAULT_BLINKER_LEAD_S = 1

# A nominal lane change, used only to give the dry run a plausible duration for the phase nothing
# can measure. openpilot's own LANE_CHANGE_TIME_MAX is 10 s as an upper bound; a comfortable
# highway lane change is around four. Nothing depends on this being right -- it changes how long
# the readout says "crossing" and nothing else.
CHANGE_DURATION_S = 4.0

# How long the completed state is held so it is readable, before returning to idle.
FINISH_HOLD_S = 1.5


class PassingManoeuvre:
  """The dry run. One instance, fed once per frame from the detector."""

  def __init__(self):
    self.phase = Phase.idle
    self.phase_seconds = 0.0
    self.side = Side.none
    self.blinker_lead_s = float(DEFAULT_BLINKER_LEAD_S)
    # The number this module exists to produce. Counts sequences that reached `signalling` and then
    # backed out -- a blinker shown to traffic behind for a manoeuvre that did not happen.
    self.aborts = 0

  @property
  def blinker_on(self) -> bool:
    """The blinker stays on THROUGH the crossing and goes out when it completes, which is how a
    person signals. Dropping it at the start of the movement would be the common mistake."""
    return self.phase in (Phase.signalling, Phase.changing)

  @property
  def steering_active(self) -> bool:
    return self.phase == Phase.changing

  @property
  def committed(self) -> bool:
    """Past the point where a gate may still call it off."""
    return self.phase in (Phase.changing, Phase.finishing)

  def _to(self, phase) -> None:
    if phase != self.phase:
      self.phase = phase
      self.phase_seconds = 0.0

  def update(self, *, clear: int, suggested: int, confirming: bool, confirmed: bool,
             driver_override: bool) -> None:
    """One frame.

    `clear`      -- a slow car is spotted and this side is clear RIGHT NOW. Lights the blinker.
    `suggested`  -- the same, AND the confirmation has completed. Commits to moving.
    `confirming` -- a slower vehicle is being confirmed, timer still running.
    `confirmed`  -- that timer has completed, so anything still stopping us is a gate.
    `driver_override` -- the driver is signalling, braking or steering. Always wins.
    """
    self.phase_seconds += DT_MDL

    # The driver taking their car back is not a gate and is not an abort worth counting against
    # the system -- it is the correct outcome, at any phase, including mid-crossing.
    if driver_override:
      self._to(Phase.idle)
      self.side = Side.none
      return

    if self.phase == Phase.changing:
      # Committed. Only the clock ends this.
      if self.phase_seconds >= CHANGE_DURATION_S:
        self._to(Phase.finishing)
      return

    if self.phase == Phase.finishing:
      self.side = Side.none
      if self.phase_seconds >= FINISH_HOLD_S:
        self._to(Phase.idle)
      return

    if self.phase == Phase.signalling:
      # A gate going red here is exactly the failure this module exists to count: the signal was
      # already shown to traffic behind before the sequence backed out.
      if clear == Side.none or clear != self.side:
        self.aborts += 1
        self.side = Side.none
        self._to(Phase.waiting if confirmed else Phase.confirming if confirming else Phase.idle)
        return
      # BOTH clocks, not one after the other: the signal has been up long enough AND the car is
      # confirmed slow. Whichever finishes last is what the driver waits for.
      if self.phase_seconds >= self.blinker_lead_s and suggested == self.side:
        self._to(Phase.changing)
      return

    # ---- idle / confirming / waiting: not yet committed to anything ----
    # `clear`, not `suggested`. Signal the instant there is a slow car and somewhere to go.
    if clear != Side.none:
      self.side = clear
      self._to(Phase.signalling)
      return

    self.side = Side.none
    # "Confirmed but held by a gate" is a different thing to "still deciding", and worth separating
    # on screen: one is "not sure yet", the other is "sure, and something is in the way".
    self._to(Phase.waiting if confirmed else Phase.confirming if confirming else Phase.idle)
