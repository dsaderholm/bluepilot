"""
FusionPilot: the maneuver passing assist WOULD perform, run as a dry run.

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

# How long the signal may stand while the safety gates are still deciding.
#
#     "Signaling should always start right when it notices a car is slow, and then during that one
#      second of signaling it should then check blind spots and radar and all of that before making
#      the change. I though that's what we agreed on."
#
# It is, and the recorded design says so: "signal early, keep confirming during the wait, abort and
# drop the signal if confidence collapses before the car moves." The gates used to be ENTRY
# conditions, so the blinker could not light until the lane was already clear -- which is not what
# was agreed and is not what production systems do.
#
# What signalling before certainty needs to stay honest is a BOUND, and this is it. A signal is a
# promise to the traffic behind, and one held while nothing happens is exactly the "never signal
# what you are not doing" failure. Both benchmarks solve it the same way and only the number
# differs: Super Cruise holds in lane showing "looking for an opening" and cancels if it cannot
# complete in five seconds; BlueCruise gives up after about ten and displays "not possible".
#
# 5 s, the tighter of the two, and deliberately. Both of those are DRIVER-initiated -- the human
# asked, so the signal has intent behind it for as long as it stands. This one decides for itself,
# so an unfulfilled promise is the system's alone and should expire sooner.
#
# Generous against the numbers here regardless: the blinker lead is 1 s and the confirmation runs
# concurrently, so a maneuver that is going to happen has usually committed inside two. Reaching
# this means a gate stayed unhappy, and the honest answer is to go dark and stand down.
SIGNAL_WINDOW_S = 5.0

# And how long to stay quiet after the window runs out, which is NOT the same as after a gate
# flicker. ABORT_STANDDOWN_S is 10 s and answers "something wobbled"; this answers "that lane was
# not available for a full five seconds", which is a fact about the traffic rather than about a
# sensor. Retrying on the same cadence would give 5 s of signal, 10 s of quiet, 5 s of signal --
# a slow strobe promising a pass that heavy traffic is not going to allow.
#
# 20 s, four times the window. Long enough that persistently blocked traffic produces an occasional
# ask rather than a rhythm, short enough that a lane which genuinely opens is used. It only ever
# delays a maneuver, never permits one.
WINDOW_STANDDOWN_S = 20.0

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
    # How long every gate has been CONTINUOUSLY happy on our side. See the crossing condition.
    self._clear_held_s = 0.0
    # Are they happy THIS frame. See `desire_ok`, which is what may raise a lane-change desire.
    self._clear_now = False

  @property
  def blinker_on(self) -> bool:
    """The blinker stays on THROUGH the crossing and goes out when it completes, which is how a
    person signals. Dropping it at the start of the movement would be the common mistake."""
    return self.phase in (Phase.signaling, Phase.changing)

  @property
  def desire_ok(self) -> bool:
    """May a lane-change DESIRE be raised right now? Narrower than blinker_on, deliberately.

    THE BUG THIS EXISTS TO PREVENT, found reviewing the interaction rather than any one piece.
    blinker_on covers `signaling`, which since the signal-first change begins BEFORE the safety
    gates pass -- that is the whole point of it, and it is correct for a lamp. It is catastrophic
    for a desire, because desire_helper does not consult our gates at all: preLaneChange advances to
    laneChangeStarting on its own nudgeless timer and a blind-spot check, nothing else. Raise the
    desire at `signaling` and the car starts crossing on a 1 s timer while oncoming, adjacent-slow,
    rear-approach and geometry are all still saying no.

    So: during `signaling` the gates must be satisfied THIS FRAME. Once `changing`, they no longer
    can call it off -- a car cannot un-change lanes on a change of mind -- so the desire stands.

    The two 1 s clocks then start together rather than stacking: desire_helper's nudgeless timer
    begins when the gates go good, which is the same moment _clear_held_s starts counting toward the
    blinker lead. Both finish at once, which is what "for automatic lane changes, I want it to be
    the same" requires.
    """
    if self.phase == Phase.changing:
      return True
    return self.phase == Phase.signaling and self._clear_now

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
             driver_override: bool, collision_abort: bool = False,
             actuating: bool = False,
             settle_after_change_s: float = COMPLETE_STANDDOWN_S,
             wanted: int | None = None) -> None:
    """One frame.

    `wanted`     -- a slow car is spotted and a lane exists that side. LIGHTS THE BLINKER, and says
                    nothing about whether entering it is safe. See SIGNAL_WINDOW_S. Defaults to
                    `clear`, which is the pre-2026-08-09 behaviour of gating the signal on safety.
    `clear`      -- the same, AND every safety gate passes RIGHT NOW. Commits to crossing.
    `suggested`  -- the same, AND the confirmation has completed. Commits to moving.
    `confirming` -- a slower vehicle is being confirmed, timer still running.
    `confirmed`  -- that timer has completed, so anything still stopping us is a gate.
    `driver_override` -- the driver is signaling, braking or steering. Always wins.
    `collision_abort` -- something is ARRIVING behind. The only input that can reverse a crossing.
    `actuating`  -- is this driving the car, or narrating what it would do? Two timings below turn
                    on it, both flagged in the constants as owed once a control exists. It is NOT a
                    permission -- the caller decides whether actuation is allowed and passes the
                    answer in, so this state machine keeps one shape either way.
    `settle_after_change_s` -- the detector's own anti-weave wait, passed in rather than imported.
                    passing_assist imports THIS module, so reaching back for its constants would be
                    a circular import; and the settle policy belongs to the detector regardless.
    """
    if wanted is None:
      wanted = clear
    self._clear_now = clear != Side.none and clear == self.side
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
        # See COMPLETE_STANDDOWN_S -- the one number in this file that exists because nothing is
        # wired up. Thirty seconds stops a dry run that moved nothing from restarting on the very
        # next frame, forever. Once the car really moves, the reason to pass is genuinely gone: we
        # are in the other lane with the slow vehicle behind us, and what remains is only the
        # anti-weave wait the detector already owns.
        self._standdown_target = settle_after_change_s if actuating else COMPLETE_STANDDOWN_S
        self._to(Phase.idle)
      return

    if self.phase == Phase.signaling:
      # A gate going red here is exactly the failure this module exists to count: the signal was
      # already shown to traffic behind before the sequence backed out.
      # THE WINDOW EXPIRING. A gate stayed unhappy, so the promise is withdrawn rather than held.
      if self.phase_seconds >= SIGNAL_WINDOW_S:
        self.aborts += 1
        self.side = Side.none
        if actuating:
          # See WINDOW_STANDDOWN_S -- longer than a gate flicker's, because this says the lane was
          # unavailable rather than that a reading wobbled.
          self._standdown_s = 0.0
          self._standdown_target = WINDOW_STANDDOWN_S
        self._to(Phase.waiting if confirmed else Phase.confirming if confirming else Phase.idle)
        return

      # The REASON went away, or the lane did -- which is different from a gate saying "not yet".
      if wanted == Side.none or wanted != self.side:
        self.aborts += 1
        self.side = Side.none
        # THE STROBE GUARD, and conditional on purpose. See ABORT_STANDDOWN_S: while this only
        # narrates, standing down here would suppress the very instability the abort count exists
        # to measure -- a flickering gate would read as one event per ten seconds instead of forty.
        # The moment it drives the lamp that trade inverts. The count has served its purpose and a
        # signal flashing at the gate's chatter rate is the only thing that matters, to the traffic
        # behind as much as to the driver.
        if actuating:
          self._standdown_s = 0.0
          self._standdown_target = ABORT_STANDDOWN_S
        self._to(Phase.waiting if confirmed else Phase.confirming if confirming else Phase.idle)
        return
      # BOTH clocks, not one after the other: the signal has been up long enough AND the car is
      # confirmed slow. Whichever finishes last is what the driver waits for.
      # BOTH clocks and EVERY gate. `clear` moved here from the entry condition: this is the frame
      # the car actually begins moving, which is where "is that lane safe to enter" has to be true.
      # The blinker lead is unchanged -- 1 s minimum, longer if the gates are still deciding, never
      # shorter. "The blinker should be on for 1 second before lane changes are made."
      # CONTINUOUSLY happy, not happy on whichever frame we happen to look at.
      #
      # Moving the gates off the signal took an implicit guarantee with them. Before, `clear` was
      # the entry condition AND any drop aborted at once, so reaching the crossing meant the gates
      # had been good for the whole blinker lead. Gate them only at the crossing and a lane that
      # FLICKERS -- a blind-spot return dropping in and out -- can be true on the single frame that
      # is sampled, and the car commits into a gap that was never really there.
      #
      # So the lead is measured from when the gates went good rather than from when the signal came
      # on. His rule holds either way, and more strongly: the blinker has been up at least that
      # long and usually longer, never less. "The blinker should be on for 1 second before lane
      # changes are made."
      if clear == self.side:
        self._clear_held_s += DT_MDL
      else:
        self._clear_held_s = 0.0

      if self._clear_held_s >= self.blinker_lead_s and suggested == self.side:
        self._to(Phase.changing)
      return

    # ---- idle / confirming / waiting: not yet committed to anything ----
    # `clear`, not `suggested`. Signal the instant there is a slow car and somewhere to go --
    # unless we have just been forced out of one, see ABORT_STANDDOWN_S.
    if wanted != Side.none and self._standdown_s >= self._standdown_target:
      self.side = wanted
      self._clear_held_s = 0.0
      self._to(Phase.signaling)
      return

    self.side = Side.none
    # "Confirmed but held by a gate" is a different thing to "still deciding", and worth separating
    # on screen: one is "not sure yet", the other is "sure, and something is in the way".
    self._to(Phase.waiting if confirmed else Phase.confirming if confirming else Phase.idle)
