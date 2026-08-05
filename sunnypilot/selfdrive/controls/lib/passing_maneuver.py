"""
BluePilot: the maneuver passing assist WOULD perform, run as a dry run.

Nothing here actuates. It consumes the detector's per-frame verdict and models the full sequence a
fully-automatic pass would go through -- spot the slow car, confirm it, signal, wait, cross, drop
the signal -- so the sequence can be watched on a real drive and judged before anything is wired to
a control.

WHY THIS IS THE THING WORTH BUILDING NEXT
The detector already answers "would I suggest a pass right now". That is a single frame's verdict,
and it is NOT the question that decides whether an automatic system works. The question is whether
the verdict HOLDS STILL long enough to act on. A gate that flickers is invisible in a
frame-by-frame display and fatal to a maneuver: the blinker goes on, a gate blinks red, the
blinker goes off, and the car has lied to the traffic behind it.

So the number this exists to produce is `aborts` -- how many times a sequence got as far as
signaling and then had to back out. On a drive where that is zero or near it, the sequence is
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
Phase = custom.LongitudinalPlanSP.PassingAssist.Maneuver

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
# abort, which is the honest cost of signaling early and exactly what the count is for.
DEFAULT_BLINKER_LEAD_S = 1

# How long the crossing takes -- a FALLBACK only, used until the car has measured its own.
#
# Settled with the owner: passing assist must not have its own steering. When it acts, it drives
# the SAME lane change sunnypilot already performs -- same model, same lateral tuning, same
# lane_change_factor -- with the trigger coming from the system instead of the stalk. "I do like
# how nudgeless lane changes work right now, and would like passing assist ones to match."
#
# So the honest duration is not a number chosen here at all; it is whatever his own nudgeless
# changes take, which auto_lane_change.py measures and stores. This value is what the dry run uses
# before there is a measurement, and it is superseded the moment there is one -- which also means
# that if lateral tuning is ever retuned, the dry run follows without anyone remembering to.
CHANGE_DURATION_S = 4.0

# How long the completed state is held so it is readable, before returning to idle.
FINISH_HOLD_S = 1.5

# How long backing out of a crossing takes. Roughly the same as the crossing itself -- it is the
# same maneuver in reverse, from wherever we had got to.
ABORT_DURATION_S = 2.5

# ...and then STAND DOWN. Without this the machine returns to idle with every input unchanged --
# slow car still there, lane still clear once the vehicle behind has gone past -- and immediately
# signals again. Backing out of a crossing and re-signaling three seconds later is worse than
# either doing it or not: whoever just went past has no idea what this car is doing.
#
# Long enough for the situation that caused it to actually resolve. A vehicle that arrived fast
# enough to force an abort is past and gone well inside this.
#
# APPLIES TO COLLISION ABORTS ONLY, and that is deliberate for phase 1 but must NOT stay that way
# once this actuates. A gate going red during `signaling` backs out and is free to re-signal on the
# very next frame, so a flickering gate -- BLIS is the raw carState bool, with no debounce of its
# own -- strobes the turn signal. Right now that is the measurement working correctly: `aborts` is
# the whole output of this module, and standing down after a gate abort would suppress exactly the
# instability it exists to count, under-reporting a flickering gate as one event per ten seconds.
#
# When a control is wired up, the count has served its purpose and the strobe becomes the only thing
# that matters. Extend this to gate aborts then, and debounce whichever gate the drive data shows is
# doing it.
ABORT_STANDDOWN_S = 10.0

# ...and the same after a sequence RUNS ALL THE WAY THROUGH, which is a different problem with the
# same shape.
#
# From the road: "it got stuck in an endless cycle of would be changing right, would be done, and it
# just kept saying that over and over again."
#
# It was doing exactly what it was told. A real pass ends with the car in the other lane and the
# slow vehicle behind it, so the reason to pass is gone and nothing re-arms. This one actuates
# NOTHING -- at the end of the dry run the car is still in the same lane behind the same car, every
# input reads the same as it did a moment ago, and `clear` is still set. So finishing dropped to
# idle and the next frame started the whole sequence again. Forever.
#
# 30 s because the readout is the product here and it has to be readable at a glance: long enough
# that a completed run reads as one event, short enough that a second slow car on the same stretch
# still gets one. It is the ONLY number in this file that exists because nothing is wired up --
# when a control is, a completed pass really will have moved the car, and this should come down to
# the detector's own SETTLE_AFTER_CHANGE_S.
COMPLETE_STANDDOWN_S = 30.0


class PassingManeuver:
  """The dry run. One instance, fed once per frame from the detector."""

  def __init__(self):
    self.phase = Phase.idle
    self.phase_seconds = 0.0
    self.side = Side.none
    self.blinker_lead_s = float(DEFAULT_BLINKER_LEAD_S)
    # Replaced by the measured duration of the driver's own lane changes. See CHANGE_DURATION_S.
    self.change_duration_s = float(CHANGE_DURATION_S)
    # The number this module exists to produce. Counts sequences that reached `signaling` and then
    # backed out -- a blinker shown to traffic behind for a maneuver that did not happen.
    self.aborts = 0
    # Crossings REVERSED because something arrived behind, counted apart from the above: one is
    # changing our mind, the other is avoiding a collision, and averaging them hides the second.
    self.emergency_aborts = 0
    self._standdown_s = 1e3
    # Which duration the current stand-down is measured against: a reversal and a completed run
    # both stop the next sequence, for different lengths of time and different reasons.
    self._standdown_target = ABORT_STANDDOWN_S

  @property
  def blinker_on(self) -> bool:
    """The blinker stays on THROUGH the crossing and goes out when it completes, which is how a
    person signals. Dropping it at the start of the movement would be the common mistake."""
    return self.phase in (Phase.signaling, Phase.changing)

  @property
  def steering_active(self) -> bool:
    return self.phase == Phase.changing

  @property
  def committed(self) -> bool:
    """Past the point where a GATE may still call it off. An arriving vehicle still can."""
    return self.phase in (Phase.changing, Phase.finishing)

  @property
  def aborting(self) -> bool:
    return self.phase == Phase.aborting

  @property
  def standdown_remaining(self) -> float:
    """Seconds until a new sequence may start, after a reversal or after one that ran through."""
    return max(0.0, self._standdown_target - self._standdown_s)

  @property
  def standdown_after_completion(self) -> bool:
    """...and WHICH of those two it is. The screen must not call a completed run a reversal --
    "BACKED OUT" for a sequence that ran cleanly start to finish is the display contradicting the
    thing it just showed, which is the failure mode this whole panel keeps having."""
    return self._standdown_target == COMPLETE_STANDDOWN_S

  def _to(self, phase) -> None:
    if phase != self.phase:
      self.phase = phase
      self.phase_seconds = 0.0

  def update(self, *, clear: int, suggested: int, confirming: bool, confirmed: bool,
             driver_override: bool, collision_abort: bool = False) -> None:
    """One frame.

    `clear`      -- a slow car is spotted and this side is clear RIGHT NOW. Lights the blinker.
    `suggested`  -- the same, AND the confirmation has completed. Commits to moving.
    `confirming` -- a slower vehicle is being confirmed, timer still running.
    `confirmed`  -- that timer has completed, so anything still stopping us is a gate.
    `driver_override` -- the driver is signaling, braking or steering. Always wins.
    `collision_abort` -- something is ARRIVING behind. The only input that can reverse a crossing.
    """
    self.phase_seconds += DT_MDL

    # The driver taking their car back is not a gate and is not an abort worth counting against
    # the system -- it is the correct outcome, at any phase, including mid-crossing.
    if driver_override:
      self._to(Phase.idle)
      self.side = Side.none
      return

    # ABORT CRITERIA NARROW AS THE MANEUVER PROGRESSES, which is the whole shape of this.
    #
    # A gate going red stops a sequence that has not moved yet and is powerless once the crossing
    # begins, because a car cannot un-change lanes on a change of mind. A vehicle ARRIVING behind
    # is a different question with a different answer: continuing would put us in front of it, so
    # reversing is worth doing from anywhere.
    #
    # Never fires with no rear sensor -- see RearApproachSide.demands_abort, which answers False
    # when unavailable rather than guessing in either direction.
    if collision_abort and self.phase in (Phase.signaling, Phase.changing):
      self.emergency_aborts += 1
      self._standdown_s = 0.0
      self._standdown_target = ABORT_STANDDOWN_S
      self._to(Phase.aborting)
      return

    self._standdown_s = min(self._standdown_s + DT_MDL, 1e3)

    if self.phase == Phase.aborting:
      self.side = Side.none
      if self.phase_seconds >= ABORT_DURATION_S:
        self._to(Phase.idle)
      return

    if self.phase == Phase.changing:
      # Committed against gates. Only the clock, or something arriving behind, ends this.
      if self.phase_seconds >= self.change_duration_s:
        self._to(Phase.finishing)
      return

    if self.phase == Phase.finishing:
      self.side = Side.none
      if self.phase_seconds >= FINISH_HOLD_S:
        # See COMPLETE_STANDDOWN_S: nothing moved, so every reason to go is still true and the
        # next frame would start the same sequence over. This is what stops the loop.
        self._standdown_s = 0.0
        self._standdown_target = COMPLETE_STANDDOWN_S
        self._to(Phase.idle)
      return

    if self.phase == Phase.signaling:
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
    # `clear`, not `suggested`. Signal the instant there is a slow car and somewhere to go --
    # unless we have just been forced out of one, see ABORT_STANDDOWN_S.
    if clear != Side.none and self._standdown_s >= self._standdown_target:
      self.side = clear
      self._to(Phase.signaling)
      return

    self.side = Side.none
    # "Confirmed but held by a gate" is a different thing to "still deciding", and worth separating
    # on screen: one is "not sure yet", the other is "sure, and something is in the way".
    self._to(Phase.waiting if confirmed else Phase.confirming if confirming else Phase.idle)
