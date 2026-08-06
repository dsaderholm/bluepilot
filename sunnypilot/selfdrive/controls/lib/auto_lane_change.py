"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from cereal import log

from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL


class AutoLaneChangeMode:
  OFF = -1
  NUDGE = 0  # default
  NUDGELESS = 1
  HALF_SECOND = 2
  ONE_SECOND = 3
  TWO_SECONDS = 4
  THREE_SECONDS = 5


AUTO_LANE_CHANGE_TIMER = {
  AutoLaneChangeMode.OFF: 0.0,            # Off
  AutoLaneChangeMode.NUDGE: 0.0,          # Nudge
  AutoLaneChangeMode.NUDGELESS: 0.05,     # Nudgeless
  AutoLaneChangeMode.HALF_SECOND: 0.5,    # 0.5-second delay
  AutoLaneChangeMode.ONE_SECOND: 1.0,     # 1-second delay
  AutoLaneChangeMode.TWO_SECONDS: 2.0,    # 2-second delay
  AutoLaneChangeMode.THREE_SECONDS: 3.0,  # 3-second delay
}

ONE_SECOND_DELAY = -1

# BluePilot: how long AFTER the blind spot goes clear before the lane change may start.
#
# Diagnosed from the owner's own report: sunnypilot's BSM delay moved over too close to a car it
# had just waited for. It is not a tuning fault, it is arithmetic. While the blind spot is
# occupied the wait timer is pinned exactly ONE_SECOND_DELAY below the threshold, so the instant
# BSM clears it needs precisely one second to cross it -- whatever delay the driver configured.
#
# One second after a car leaves your blind spot it is a meter or two off your rear quarter, and if
# it was overtaking you it is still closing. That is the maneuver reported.
#
# No timer value fixes this properly, because BLIS answers the wrong question: it goes clear when
# the car is no longer BESIDE you and says nothing about whether it is now ahead or behind, or how
# fast. A rear-facing sensor is the real answer (see BP-REAR-RADAR-PLAN.md). Until one is fitted,
# a longer hold is the honest stand-in -- it buys distance on the assumption we cannot measure it.
#
# Default 3 s rather than 1. At a 10 mph overtaking difference that is ~13 m of extra separation.
DEFAULT_BSM_HOLD_S = 3

# BluePilot: how long after a lane change begins that CANCELLING THE BLINKER calls it off.
#
# Stock behavior is that it cannot be called off at all. Once laneChangeStarting is entered, the
# blinker is never looked at again -- the state machine runs to completion on the model's own
# lane-change probability. Reported from the car: "there is no way to cancel it."
#
# Which makes the existing lane change the MANUAL VERSION of passing assist, and the same design
# rule applies: abort criteria narrow as driver intent strengthens. The driver chose this maneuver,
# so a gate must not undo it -- but the driver withdrawing that choice is the strongest signal
# there is, and it was being ignored.
#
# WHY A WINDOW RATHER THAN ANY TIME. Reverting is itself a maneuver, and there is a point where it
# is worse than finishing: most of the way across, "go back" means a second crossing through the
# space you just left. openpilot has no reverse-lane-change desire either -- clearing the desire
# makes the planner re-center on whatever lane it now believes it is in, which late in the change
# is the NEW one. So cancelling late would not go back, it would finish while confusing the
# planner about why.
#
# FOUR SECONDS, AND THE ROAD PICKED THE NUMBER. Two was reasoned from how far across the car would
# be; what it left out was how long a person takes to decide.
#
# His sequence, measured rather than assumed: a stalk tap starts eight flashes, the change begins
# one second later, and he cancels by nudging the stalk back while WATCHING the car start to move.
# That reaction lands somewhere past two seconds -- so the window closed before the gesture arrived
# and the cancel never ran. "Turning off my blinker mid lane change doesn't really seem to cancel"
# was, in the end, mostly this.
#
# Four is what the timing allows rather than a round number. The blinker times out on its own at
# about 5.5 s, which is 4.5 s into a change that started at 1.0 s, so a four second window shuts
# half a second before the eighth flash could ever be mistaken for a decision. See
# DEFAULT_ONE_TOUCH_S, which catches it anyway if the flash rate is not quite what we think.
DEFAULT_CANCEL_WINDOW_S = 4

# ...AND THAT REASONING WAS CONFIRMED FROM THE ROAD, THE HARD WAY.
#
# Reported after a drive: "turning off my blinker mid lane change doesn't really seem to cancel.
# They usually just go into the lane anyway."
#
# The paragraph above predicted precisely this and then shipped the version it describes as not
# working. Clearing the desire does not steer back; it makes the planner re-center on whichever lane
# it now believes it is in. Two seconds into a measured ~3.8 s change is past HALF WAY across, so
# re-centering picks the NEW lane and the change completes. The cancel was firing and doing nothing
# visible -- which is exactly what "doesn't really seem to cancel" looks like from the seat.
#
# That is half the story, and the half that was visible from a desk. The other half is above: the
# window was ALSO too short for a human reaction. Both were true, which is why the first fix helped
# and did not settle it -- a cancel that goes back is worth nothing if it never fires.
#
# WHAT ACTUALLY GOES BACK. openpilot has no reverse-lane-change desire, but it does not need one: a
# lane change in the opposite direction IS the reverse of this one, and the model already knows how
# to do that. So a cancel flips the direction and runs the crossing back, which is what was asked
# for -- "when my blinker stops, it should stop trying to change lanes and go back to where it was."
#
# THE RETURN SIDE IS CHECKED FIRST. We came from that lane seconds ago, so it is nearly always
# clear, but "nearly always" is not a thing to steer on: somebody may have moved up into it. With
# the blind spot lit on the return side, finishing the change we started is the safer of two
# imperfect options, so it falls back to the old behavior of simply releasing the desire.
DEFAULT_REVERT = True

# --- the blinker turns itself off on this car, and that is not a cancel ---
#
# His BCM is set through FORScan to flash EIGHT times from a tap -- the maximum -- and that tap is
# how he starts a nudgeless lane change in the first place. Eight flashes is roughly five and a half
# seconds, after which the lamp stops because it has finished.
#
# With the revert wired up that becomes dangerous rather than merely wrong: a signal expiring on
# schedule would read as "go back", and the car would reverse a lane change nobody cancelled. The
# window makes it worse the longer it gets, because a longer window is more likely to still be open
# when the eighth flash lands.
#
# His actual cancel is a slight nudge of the stalk back the other way, which kills the flash without
# starting the other side. That happens EARLY -- while he is watching the car begin to move -- and
# early is exactly what separates it from a timeout. So: a blinker that goes out well before the
# one-touch would have finished is a decision; one that goes out on schedule is a clock.
#
# Set this to however long YOUR one-touch flash lasts. HIS IS SEVEN FLASHES, set in FORScan --
# a car configuration he chose, not a stock value, so do not "correct" this toward a stock count.
# The blinker test measured his flash period at 760 ms, and 7 x 0.76 = 5.32 s.
DEFAULT_ONE_TOUCH_S = 5.5
# How far ahead of the timeout a blinker-off still counts as deliberate. Wide enough that a normal
# reaction lands inside it, tight enough that the last flash never does.
ONE_TOUCH_MARGIN_S = 0.75

# BluePilot: DOES HIS CANCEL GESTURE REACH THE OPPOSITE SWITCH POSITION AT ALL?
#
# 2026-08-06: *"I usually just cancel the one touch by slightly moving my blinker back towards the
# right (if I originally did left), which cancels it, but doesn't trigger the right. It's a very
# precise process."*
#
# That is not the gesture should_cancel was built for. It answers `reversed_side`, and leftBlinker /
# rightBlinker are TurnLghtSwtch_D_Stat -- the SWITCH position from the SCCM, 1 left and 2 right.
# If his nudge returns the stalk to centre (0) without ever reaching 2, reversed_side is never true,
# the blinker simply goes out, and going out was deliberately made to mean nothing. His cancel would
# then be a no-op: the lane change carries on.
#
# Whether the switch reaches 2 for a frame is a fact about his stalk's detents, not something to
# reason out -- the gateway sends this at 10 Hz, so a nudge shorter than about 100 ms may never be
# transmitted at all. So COUNT IT rather than guess, and let one drive answer: how many changes saw
# the opposite position, against how many had the signal simply go out mid-change.
#
# AND DO NOT ASK HIM TO TAP THE OTHER WAY INSTEAD. His one-touch is seven flashes, so the tap
# broadcasts a full lane change he is not making, to the traffic beside him, at the moment he is
# already part-way across and correcting: *"it's too many flashes and will make drivers think I'm
# going into the other lane."* That is a wrong signal to other road users, not an inconvenience,
# and it rules the gesture out as a REQUIREMENT however easy it would be to detect.
#
# The same argument kills signalling the revert itself, which was floated here and is withdrawn:
# a crossing back into the lane he never left is not a lane change, and announcing one is the same
# false message. Returning to your own lane is what the drivers around you already expect.
OPPOSITE_SWITCH_WINDOW_S = 1.5

# BluePilot: MEASURE THE DRIVER'S OWN LANE CHANGES, because they are the only real ones.
#
# Passing assist cannot steer, so every constant it holds about what a lane change IS came from
# reasoning rather than observation. Two of them are guesses that this can replace outright:
#
#   CHANGE_DURATION_S = 4.0 in passing_maneuver.py -- how long the crossing takes. Invented. Every
#   change the driver makes measures the real thing, on this car, at his speeds.
#
#   THIS ONE CONVERGES FAST AND THEN STOPS BEING NEWS, which the owner pointed out: a nudgeless
#   change takes the same time every time. Its shape comes from the model and the lateral tuning
#   -- lane_change_factor_high_ang scales the requested curvature during a change, and on this car
#   (angle branch) it is 1.0, so nothing softens it. Traffic does not enter into it. So expect one
#   number after a handful of changes, and after that treat it as a REGRESSION DETECTOR: if it
#   moves, somebody changed lateral tuning. The overtake duration is the one that genuinely varies,
#   and that is measured separately in overtake_progress.py.
#
#   The abort count has no human baseline. If he abandons one signal in ten himself, then passing
#   assist backing out one in ten is NORMAL and the number means nothing until compared. Without a
#   baseline it is a figure with no scale.
#
# Written to a param on the same terms as the drive summary: periodically, never blocking, and it
# survives being parked because a number nobody can read is a number nobody took.
LANE_CHANGE_STATS_WRITE_S = 30


class AutoLaneChangeController:
  def __init__(self, desire_helper):
    self.DH = desire_helper
    self.params = Params()

    self.lane_change_wait_timer = 0.0
    self.param_read_counter = 0
    self.lane_change_delay = 0.0

    self.lane_change_set_timer = self.params.get("AutoLaneChangeTimer", return_default=True)
    self.lane_change_bsm_delay = False
    self.lane_change_bsm_hold = float(DEFAULT_BSM_HOLD_S)

    self.prev_brake_pressed = False
    self.auto_lane_change_allowed = False
    self.prev_lane_change = False
    self.lane_change_cancel_window = float(DEFAULT_CANCEL_WINDOW_S)
    self.lane_change_one_touch_s = float(DEFAULT_ONE_TOUCH_S)
    # How long the blinker has been on, and what it read at the moment it went out. See
    # DEFAULT_ONE_TOUCH_S -- the second one is the whole test, and it has to be sampled on the
    # falling edge because by the time anything asks, the timer has already been reset.
    self.blinker_held_s = 0.0
    self.blinker_last_held_s = 0.0
    self.revert_enabled = bool(DEFAULT_REVERT)
    # True from the moment a cancel decides to steer back until the reverse crossing completes.
    # Latched because the blinker is OFF throughout a revert, which is the same condition that
    # triggered the cancel -- without this it would re-cancel itself on every frame.
    self.reverting = False
    self.cancelled = False
    self._reverted = False

    # See LANE_CHANGE_STATS_WRITE_S. Counts this drive; totals carried in from previous ones.
    self.changes_completed = 0
    self.changes_abandoned = 0      # signalled, then thought better of it before moving
    self.changes_cancelled = 0      # called off mid-change
    # See OPPOSITE_SWITCH_WINDOW_S. Both counted per lane change, at most once each, so the pair
    # reads as "of the changes you tried to call off, this many reached the opposite detent".
    self.changes_saw_opposite = 0   # the switch read the other side while a change was underway
    self.changes_saw_signal_out = 0 # the signal simply went out instead, which cancels nothing
    # Of those, how many had his hands doing the work. See should_cancel.
    self.changes_signal_out_while_steering = 0
    self._saw_opposite_this_change = False
    self._saw_signal_out_this_change = False
    # Latched for the whole change. See should_cancel: sampled at the instant the signal drops, one
    # relaxed frame would read as "he asked the car to stop" mid hold-and-release.
    self._steered_this_change = False
    self.change_seconds = 0.0       # running mean of how long a completed one took
    self._prev_state = log.LaneChangeState.off
    self._change_started_s = 0.0
    self._stats_write_s = 0.0
    self._stats_seeded = False
    # Assigned here rather than only on first save. They used to be created inside the try block
    # AFTER the seeded flag was set, so a param read that threw once left the flag true and the
    # values missing -- every later save then raised, was swallowed by the same except, and the
    # drive silently recorded nothing. Existing by default makes that unreachable.
    self._base = {"changes": 0, "abandoned": 0, "cancelled": 0}
    self._base_secs = 0.0
    self._base_n = 0

    self.read_params()

  def reset(self) -> None:
    # Auto reset if parent state indicates we should
    if self.DH.lane_change_state == log.LaneChangeState.off and \
       self.DH.lane_change_direction == log.LaneChangeDirection.none:
      self.lane_change_wait_timer = 0.0
      self.prev_brake_pressed = False
      self.prev_lane_change = False

  def read_params(self) -> None:
    self.lane_change_bsm_delay = self.params.get_bool("AutoLaneChangeBsmDelay")
    self.lane_change_set_timer = self.params.get("AutoLaneChangeTimer", return_default=True)
    # BluePilot: see DEFAULT_BSM_HOLD_S.
    self.lane_change_bsm_hold = float(self.params.get("AutoLaneChangeBsmHoldTime", return_default=True))
    # BluePilot: see DEFAULT_CANCEL_WINDOW_S.
    self.lane_change_cancel_window = float(self.params.get("AutoLaneChangeCancelWindow", return_default=True))
    # BluePilot: see DEFAULT_REVERT.
    self.revert_enabled = self.params.get_bool("AutoLaneChangeRevert")
    # BluePilot: see DEFAULT_ONE_TOUCH_S.
    self.lane_change_one_touch_s = float(self.params.get("AutoLaneChangeOneTouchTime",
                                                         return_default=True))

  def update_params(self) -> None:
    if self.param_read_counter % 50 == 0:
      self.read_params()
    self.param_read_counter += 1

  def update_stats(self) -> None:
    """Watch the state machine and record what a real lane change did. See LANE_CHANGE_STATS_*.

    Reads only the parent's state, so it cannot influence the maneuver -- if this is ever the
    reason a lane change behaves differently, something is very wrong.

    WHOLLY GUARDED, and that is not defensive habit. DesireHelper runs inside modeld, so anything
    that raises here kills the model process -- and with no model there is no steering at all.
    This is measurement: its entire purpose is optional, and no optional thing may be able to stop
    the car driving. The param write was already protected; the accounting around it was not.

    Deliberately NOT extended to should_cancel, which sits in the same process. That one decides
    whether a lane change stops, and swallowing a fault there would mean silently failing to
    cancel -- worse than a loud failure, for three lines of comparison that cannot realistically
    raise. Guard what is optional; leave what is load-bearing to fail loudly.
    """
    try:
      self._update_stats()
    except Exception:  # noqa: BLE001 - measurement must never take modeld down
      pass

  def _update_stats(self) -> None:
    state = self.DH.lane_change_state
    prev, self._prev_state = self._prev_state, state

    if state == log.LaneChangeState.off:
      # Cleared at the end of a sequence rather than the start of the crossing, so the latch spans
      # preLaneChange -- where the nudge lives -- through to the end. See update_blinker_timer.
      self._steered_this_change = False

    if state in (log.LaneChangeState.laneChangeStarting, log.LaneChangeState.laneChangeFinishing):
      self._change_started_s += DT_MDL
    elif prev == log.LaneChangeState.preLaneChange and state == log.LaneChangeState.off:
      # Signalled and then did not go. His own change-of-mind rate, which is the baseline the
      # system's abort count has to be judged against.
      self.changes_abandoned += 1
    elif prev == log.LaneChangeState.laneChangeStarting and state == log.LaneChangeState.off:
      # NOT counted as a cancel here any more -- see begin_revert, which counts at the decision.
      # This transition also covers lateral going inactive, the change timing out and the
      # blinker-pause gate, none of which is the driver calling it off.
      self._change_started_s = 0.0
      self._saw_opposite_this_change = False
      self._saw_signal_out_this_change = False
    elif prev == log.LaneChangeState.laneChangeFinishing and state != log.LaneChangeState.laneChangeFinishing:
      if self._change_started_s > 0.0 and not self._reverted:
        self.changes_completed += 1
        # Running mean, so one unusually slow change cannot stand in for the drive.
        self.change_seconds += (self._change_started_s - self.change_seconds) / self.changes_completed
      # A revert reaches here too, having gone back rather than across. It is already counted as a
      # cancel, and folding it into the duration mean would bias the one number passing assist
      # takes from these measurements.
      self._reverted = False
      self._change_started_s = 0.0

    self._save_stats()

  def _save_stats(self) -> None:
    self._stats_write_s += DT_MDL
    if self._stats_write_s < LANE_CHANGE_STATS_WRITE_S:
      return
    self._stats_write_s = 0.0
    if not (self.changes_completed or self.changes_abandoned or self.changes_cancelled):
      return
    try:
      if not self._stats_seeded:
        old = self.params.get("LaneChangeStats") or {}
        self._base = {k: int(old.get(k, 0)) for k in ("changes", "abandoned", "cancelled")}
        self._base_secs = float(old.get("seconds", 0.0))
        self._base_n = int(old.get("changes", 0))
        self._stats_seeded = True   # only once the read actually succeeded
      total = self._base["changes"] + self.changes_completed
      # Weighted so the lifetime mean is over every change ever made, not a mean of means.
      secs = ((self._base_secs * self._base_n + self.change_seconds * self.changes_completed) / total
              if total else 0.0)
      self.params.put("LaneChangeStats", {
        "changes": total,
        "abandoned": self._base["abandoned"] + self.changes_abandoned,
        "cancelled": self._base["cancelled"] + self.changes_cancelled,
        "seconds": round(secs, 2),
        # See OPPOSITE_SWITCH_WINDOW_S. Not carried across drives: the question is answered the
        # first time either of these is non-zero, and a lifetime total would bury that.
        "sawOpposite": self.changes_saw_opposite,
        "sawSignalOut": self.changes_saw_signal_out,
        "signalOutSteering": self.changes_signal_out_while_steering,
      })
    except Exception:  # noqa: BLE001 - a param write must never reach the model process
      pass

  def update_lane_change_timers(self, blindspot_detected: bool) -> None:
    self.lane_change_delay = AUTO_LANE_CHANGE_TIMER.get(self.lane_change_set_timer,
                                                        AUTO_LANE_CHANGE_TIMER[AutoLaneChangeMode.NUDGE])

    self.lane_change_wait_timer += DT_MDL

    if self.lane_change_bsm_delay and blindspot_detected and self.lane_change_delay > 0:
      # BluePilot: pin the timer a full hold below the threshold, so clearing the blind spot buys
      # `lane_change_bsm_hold` seconds rather than the one second the original arithmetic gave.
      # The nudgeless special case folds in: its threshold is 0.05, so this lands at 0.05 - hold
      # and still yields `hold` seconds of wait. See DEFAULT_BSM_HOLD_S.
      self.lane_change_wait_timer = self.lane_change_delay - self.lane_change_bsm_hold

  def should_cancel(self, one_blinker: bool, elapsed_s: float,
                    reversed_side: bool = False, blinker_held_s: float = 0.0,
                    steering_pressed: bool = False) -> bool:
    """Has the driver called off a lane change already underway? See DEFAULT_CANCEL_WINDOW_S.

    Lives here rather than in desire_helper so the upstream file carries one line rather than a
    policy. Answers False past the window, which is the point of no return -- reverting from most
    of the way across is a second crossing, not an undo.

    TWO GESTURES, and the first one is why this originally did nothing.

    REVERSING THE STALK is the cancel. On this car the driver's own report -- "turning off my
    blinker mid lane change doesn't really seem to cancel, they usually just go into the lane
    anyway" -- had a simpler cause than the planner theory it was first blamed on: he cancels by
    tapping the OTHER way, and `one_blinker` is `left != right`, so it stays TRUE the whole time.
    The old test needed it to go false. It never did, so the cancel never ran once.

    THE SIGNAL GOING OUT IS NOT A CANCEL AT ALL, and the version that tried to tell "early" from
    "the one-touch expiring" was wrong about how he drives:

      "If I manually put the blinker on to do a nudged lane change instead of tapping the blinker,
      and put the blinker off, it will put me back into the lane I was just in."

    Holding the stalk and releasing it is a THIRD lifetime the one-touch heuristic never accounted
    for -- shorter than eight flashes, and not a cancel by any reading. It steered him back into the
    lane he had just left, and his conclusion was that he would have to signal the full amount every
    time, which he immediately followed with the reason that is no good:

      "Sometimes I grab the steering wheel to bypass the nudgeless lane changes with a faster lane
      change myself, which means I'll use the blinker less."

    A feature that requires a longer signal from a driver who is deliberately using shorter ones is
    a feature fighting its owner. So the blinker going out means nothing here, however long it was
    on, and REVERSING THE STALK is the only cancel -- which is what he said he does anyway, and the
    only gesture with one possible meaning.

    THE THIRD GESTURE, added 2026-08-06, because the two above left him with no way to stop a change
    at all: *"So this feature is dead? It will just continue making the lane change if I turn the
    blinker off? I will still have to fight with it to stop it?"* It would have, and that is not
    acceptable. The tap is ruled out on safety (see OPPOSITE_SWITCH_WINDOW_S) and his own nudge may
    never reach switch position 2, which between them would leave no cancel at all.

    So the signal going out IS a cancel again -- under a test that survives both cases that killed
    the previous attempt. Three-way, not two:

      held ~= the one-touch length   ->  it expired on schedule. A clock, not a decision. NO.
      held short AND he is steering  ->  hold-and-release: he is doing the change HIMSELF and does
                                         not want steering back into the lane he came from. NO.
      held short and he is NOT       ->  he asked the car to stop. CANCEL.

    Duration alone was the old test and it was wrong, because hold-and-release is short too. Torque
    alone would be wrong as well, because a one-touch expiring while he sits there hands-off looks
    identical. Together they separate all three, and both were already being measured.

    The torque is LATCHED across the whole change rather than sampled when the signal drops -- he
    steers with one hand while the other leaves the stalk, and a single relaxed frame landing on
    that instant must not read as "he asked the car to stop".
    """
    # Counted before either early return, because the QUESTION is whether the gesture is visible
    # at all -- a nudge past the cancel window is still evidence about his stalk's detents.
    if reversed_side:
      if not self._saw_opposite_this_change:
        self._saw_opposite_this_change = True
        self.changes_saw_opposite += 1
    elif not one_blinker and blinker_held_s == 0.0 and elapsed_s > 0.0:
      # The signal is out mid-change and nothing replaced it. See OPPOSITE_SWITCH_WINDOW_S: if this
      # is what his nudge looks like from here, his cancel gesture is invisible to the state machine.
      #
      # WHETHER HE IS STEERING splits the two gestures that look identical from here, and it is the
      # discriminator the one-touch-length heuristic should have been. Hold-and-release is him doing
      # the change HIMSELF -- "sometimes I grab the steering wheel to bypass the nudgeless lane
      # changes with a faster lane change myself" -- so there is torque on the wheel. A nudge-cancel
      # is him asking the car to stop, so there is not. Recorded rather than acted on: which of
      # these his nudge actually produces is still unmeasured.
      if not self._saw_signal_out_this_change:
        self._saw_signal_out_this_change = True
        self.changes_saw_signal_out += 1
        if steering_pressed:
          self.changes_signal_out_while_steering += 1

    if self.lane_change_cancel_window <= 0.0:
      return False
    if elapsed_s >= self.lane_change_cancel_window:
      return False
    if reversed_side:
      return True

    # The signal is out. blinker_held_s is blinker_last_held_s, sampled on the falling edge by
    # update_blinker_timer, which desire_helper calls before the state machine -- so it is this
    # change's number rather than a stale one.
    if one_blinker or blinker_held_s <= 0.0:
      return False
    expired = blinker_held_s >= self.lane_change_one_touch_s - ONE_TOUCH_MARGIN_S
    return not expired and not self._steered_this_change

  def update_blinker_timer(self, one_blinker: bool, steering_pressed: bool = False) -> None:
    """How long the signal has been on, sampled across the falling edge. See DEFAULT_ONE_TOUCH_S.

    Also latches the driver's torque for the whole sequence, and it has to be latched HERE rather
    than in should_cancel. A nudged lane change is triggered by torque during preLaneChange, and
    should_cancel does not run until laneChangeStarting -- so a latch that started there would miss
    the nudge entirely, see him hands-off as the stalk released, and steer him back into the lane he
    had just left. That is the exact harm this whole path exists to avoid.
    """
    self._steered_this_change = self._steered_this_change or steering_pressed
    if one_blinker:
      self.blinker_held_s += DT_MDL
    elif self.blinker_held_s > 0.0:
      self.blinker_last_held_s = self.blinker_held_s
      self.blinker_held_s = 0.0

  def begin_revert(self, return_blocked: bool) -> bool:
    """The driver called it off. Steer back, or merely stop steering across?

    Returns True if the caller should flip the lane change direction and run the crossing in
    reverse. False keeps the original behavior -- release the desire and let the planner re-center
    -- which is right when the lane we came from is no longer clear.

    Counts the cancel HERE rather than watching for a laneChangeStarting -> off transition, because
    a revert does not produce that transition: it completes through laneChangeFinishing like any
    other crossing. Counting on the transition also quietly counted things that are not driver
    cancels at all -- lateral going inactive, the change timing out, the blinker-pause gate -- so
    doing it at the decision is both necessary and more honest.
    """
    self.cancelled = True
    self.changes_cancelled += 1
    if not self.revert_enabled or return_blocked:
      return False
    self.reverting = True
    # So the reverse crossing is not also counted as a lane change the driver made.
    self._reverted = True
    return True

  def update_allowed(self) -> bool:
    # Auto lane change allowed if:
    # 1. A valid delay is set (non-zero)
    # 2. Brake wasn't previously pressed
    # 3. We've waited long enough

    if self.lane_change_set_timer in (AutoLaneChangeMode.OFF, AutoLaneChangeMode.NUDGE):
      return False

    if self.prev_brake_pressed:
      return False

    if self.prev_lane_change:
      return False

    return bool(self.lane_change_wait_timer > self.lane_change_delay)

  def update_lane_change(self, blindspot_detected: bool, brake_pressed: bool) -> None:
    if brake_pressed and not self.prev_brake_pressed:
      self.prev_brake_pressed = brake_pressed

    self.update_lane_change_timers(blindspot_detected)

    self.auto_lane_change_allowed = self.update_allowed()

  def update_state(self):
    if self.DH.lane_change_state == log.LaneChangeState.laneChangeStarting:
      self.prev_lane_change = True

    self.reset()
