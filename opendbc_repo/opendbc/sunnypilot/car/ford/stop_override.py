"""FusionPilot: the last few mph of a stop, which Ford's set speed cannot ask for.

WHAT THIS IS FOR
----------------
`get_minimum_set_speed()` returns 20 mph and it is FORD's floor, confirmed by the owner: *"No, I
can't set it lower than 20."* Every ICBM feature commands through the set speed, so the model-stop
path can walk the car down toward 20 and no further. Stock ACC completes a stop only when its OWN
radar has a lead -- so a stop sign or a red light on an empty road is the one thing this car cannot
do, and it is the entire reason the passthrough exists.

**IT AUTHORS NOTHING NEW. It chooses which already-authored frame goes out.**

That is the whole design and it is deliberate. `create_acc_msg` already exists, already clamps to
panda's bands, already drives the split brake/precharge hysteresis, and already never touches the
unpoliced bits that applied the park brake on drive A. So the override is a DECISION -- "for these
few seconds, send openpilot's command instead of Ford's" -- and not a second CAN authoring path
that would have to re-learn all of that.

THE RULE IT OBEYS, from CLAUDE.md, restated because it is the thing that goes wrong
--------------------------------------------------------------------------------------
  THE TRAP: `min(ford_accel, openpilot_accel)` -- "use whichever brakes harder". One line, handles
  every case automatically, and it is WRONG. openpilot's planner is more conservative than Ford's
  most of the time, so it would win constantly and the passthrough becomes op long again, arriving
  through a comparison operator.

  THE RULE: a NAMED, BOUNDED CONDITION. Fires explicitly, for a few seconds, and falls back to
  Ford's number the moment it is done.

There is no comparison anywhere in this file. Ford's command is never read.

AND IT IS BOUNDED IN TIME, WHICH IS NOT THE SAME AS BOUNDED BY ITS TRIGGER
--------------------------------------------------------------------------
Measured 2026-08-18 across two drives, and this bound exists because of it:

    drive A   ~40 s of ~51% refusal while the camera was braking  ->  camera LATCHED cancel,
                                                                      never cleared for 262 s
    drive B   1.3 s total contradiction, longest run 0.2 s        ->  camera never reacted

A stop from 20 mph is five to eight seconds of CONTINUOUS contradiction -- two orders of magnitude
past drive B and an order under drive A. **The camera's tolerance is a duration threshold nobody has
measured**, so the trigger condition alone is not a bound: "a stop line ahead" says when to start
and nothing about when to stop. `MAX_ACTIVE_FRAMES` is the answer, set well under drive A's 40 s.

A second unknown inside it, worth stating because it is not covered by the bound: drive A
contradicted by UNDER-braking relative to Ford, and this contradicts by OVER-braking. Whether the
camera cares about the sign is unknown.

WHY A LEAD DISQUALIFIES IT
--------------------------
With a lead its radar can see, stock ACC does the whole stop itself. Overriding there fights Ford
for nothing and spends contradiction budget on a case Ford already handles -- and Ford's stop-and-go
is years of calibration this would be replacing with openpilot's. So the override is for the EMPTY
road: a stop sign, a red light, a stop line the radar has no target for.

WHAT ENDS IT, in order of how likely each is
--------------------------------------------
  - the car is stopped -- NO LONGER. It HOLDS; Ford will not hold a stop without a lead. See the
    creep note in `update`.
  - the reason went away -- the model stopped planning a stop, or a lead appeared. Hand back.
  - `MAX_ACTIVE_FRAMES`. The bound above.
  - openpilot longitudinal went inactive. Nothing may be authored at all then.

After ANY of those it is SPENT and refuses to re-arm until the model stops asking, so a stop that
does not complete cannot re-trigger every frame and turn a bounded override into a permanent one.
"""
from __future__ import annotations

from collections import deque

# The opendbc-layer conversion, the same one ford/carstate.py and ford/interface.py use. This file
# had its own 0.44704 literal, which is a second definition of the constant that scopes ENTER_SPEED
# against `unconfirmed_lead.py`'s ACC_FLOOR_MS -- two literals for one relationship can drift, and
# the drift would be silent because both would still look about right.
from opendbc.car.common.conversions import Conversions as CV

MPH_TO_MS = CV.MPH_TO_MS

# Above this the set speed can still do the work, so ICBM should -- Ford picks coast vs engine-brake
# vs friction there and that blend is the thing the whole division of labour exists to keep.
#
# LOWERED 25 -> 20 on 2026-08-18, at his instruction, because 25 was inconsistent with the time
# bound below and the arithmetic says so:
#
#     from 25 mph   2.0 m/s^2 -> 5.6 s    1.5 -> 7.5 s    1.2 -> 9.3 s X   1.0 -> 11.2 s X
#     from 20 mph   2.0 m/s^2 -> 4.5 s    1.5 -> 6.0 s    1.2 -> 7.5 s     1.0 ->  8.9 s X
#
# openpilot's e2e stops run about 1.0-1.5 m/s^2, so arming at 25 put the LIKELY case over the 8 s
# bound rather than the exceptional one. At 20 the feature needs 1.12 m/s^2 instead of 1.4 to
# finish in time. It does not remove the failure -- a 1.0 m/s^2 stop still runs out -- it moves the
# threshold below where openpilot usually sits.
#
# It also stops the override burning bound-time on deceleration FORD IS ALREADY DOING. ICBM walks
# the set speed to 20 on the approach; arming at 25 meant the first seconds of the override ran
# while Ford was still perfectly capable of the request. Now it takes over where Ford genuinely
# stops: at its own floor.
#
# THE COST, and it is the one to watch on the first drive: this is now exactly Ford's set-speed
# floor, so the override arms only once the car is at or under the speed Ford is holding it at. If
# Ford settles a little high -- holding 20.4 mph steady -- `v_ego` never crosses this and the
# override never arms. The symptom is the car sitting at 20 through the intersection with no violet
# pill, which looks identical to the feature not existing. A mph or two of margin here would remove
# that failure for about 0.4 s of extra bound-time; see bp_stop_override.py's question 1 first.
# RAISED 20 -> 25 ON 2026-08-20, and the paragraph above is the measurement that forced it. The
# predicted failure happened exactly as written: on route 0000039a the arming path refused 930
# frames at this gate for "too fast", and the entire window where the car was slow enough, had no
# lead and the model had an endpoint was **13 frames -- 0.26 seconds**. A quarter second is not a
# window, it is a coincidence.
#
# 20.0 was Ford's set-speed floor exactly, which made this gate a race against the moment Ford bails
# rather than a decision made before it. The override cannot take a stop Ford is abandoning if it is
# only allowed to arm at the instant of abandonment. 25 gives the approach room to be owned before
# the handoff instead of after it.
#
# The cost is that between 20 and 25 mph this takes stops ICBM would otherwise walk the set speed
# down for. That is the intended trade: walking the set speed down cannot stop the car, and every
# such approach ends at 20 mph with Ford quitting anyway.
# 25 -> 45 THE SAME DAY, because 25 was still the wrong axis and his own report is what showed it:
# *"The light had no car at it and it did slow down to 20 and alert me."*
#
# Indexed by the alert he actually saw ("Stop sign or signal ahead") rather than by a speed window
# guessed at in advance, route 0000039c has three empty-light approaches, ALL ENGAGED, ALL WITH THE
# MODEL ASKING, ALL WITH NO LEAD:
#
#     t=76.0     32.5 mph, stop 97 m out      t=827.3   43.9 mph, stop 146 m out
#     t=1017.1   28.3 mph, stop 64 m out
#
# LIGHTS ARE APPROACHED AT CRUISING SPEED. Every one of those is above 25, so a 25 mph gate refuses
# all three -- and a 20 mph gate refused them harder. The distance test passes at each (1.08, 1.32
# and 1.05 m/s^2 required, against a 0.69 threshold); the speed gate was the only thing refusing.
#
# The old justification -- "the set speed can still express this; ICBM is strictly better" -- is
# true for SLOWING and false for STOPPING. ICBM walks the set speed down to Ford's 20 mph floor and
# has nothing below it. On an empty approach that is not a division of labour, it is a handoff to
# nobody, which is exactly what he keeps driving through.
#
# 45 covers his measured approaches with margin. THE COST IS REAL AND IS THE THING TO WATCH: the
# override may now take longitudinal authority from Ford at cruising speed for a full stop. The
# defenses against a bad model call are unchanged and are what bound it -- a lead disqualifies, the
# model must keep asking through a 0.5 s debounce, the distance test still refuses a stop that is
# merely distant, and MAX_ACTIVE_S ends it.
ENTER_SPEED = 45.0 * MPH_TO_MS

# THE LOWEST SPEED AT WHICH WE MAY TAKE AUTHORITY. Not the lowest speed we may HOLD it -- once
# armed, carrying the car all the way to a standstill is fine and is measured to be fine.
#
# THIS IS THE FIX FOR LOSING FORD ACC FOR A WHOLE DRIVE, 2026-08-20. Every override episode across
# four drives, against what the camera did afterwards:
#
#     armed 19.3 mph  0.8 s  -> cancel +7.7 s, released
#     armed 19.8 mph  7.3 s  -> cancel +1.7 s, LATCHED FOR THE REST OF THE DRIVE
#     armed 26.3 mph  2.8 s  -> no cancel
#     armed 28.3 mph 35.4 s  -> no cancel, AND IT RAN TO A FULL STANDSTILL
#     armed 32.2 mph  1.1 s  -> no cancel
#     armed 32.9 mph  8.9 s  -> cancel +3.5 s, released after 18 s
#     armed 33.9 mph  6.3 s  -> cancel +6.8 s, released after 5 s
#     armed 40.0 mph  3.8 s  -> cancel +2.1 s, released after 29 s
#     armed 42.3 mph  0.2 s  -> no cancel
#     armed 44.9 mph  9.6 s  -> no cancel, down to 8.9 mph
#
# BOTH arms below Ford's floor provoked a cancel; one latched permanently. EVERY arm above it was
# tolerated -- including the 35 s one that took the car to zero. So it is not stopping the camera
# objects to, nor duration, nor going below 20 once underway. It is TAKING THE COMMAND AWAY FROM AN
# ACC THAT IS ALREADY AT ITS OPERATING LIMIT.
#
# When that latch happens the camera asserts `AccCancl_B_Rq` and never releases it -- measured on
# route 000003a0, exactly one cancel transition in the last 550 s of the drive, and it was the ON.
# `passthrough_admissible` then refuses every frame, Ford's command is never forwarded again, and he
# is on openpilot longitudinal until he restarts the car. He reported precisely that, and reported
# the drive whose arms were both above the floor as fine.
#
# 25, NOT 20, AND THE 5 MPH IS MEASURED RATHER THAN PADDING. Setting it at Ford's own floor was
# tried first and replayed against the same drives: the bad arm at 19.8 mph simply became a 20.0 mph
# arm 0.2 s later, because the car HOVERS at the floor -- Ford accelerates to hold 20 while the
# model wants to stop, so v_ego oscillates across the boundary and the override catches it on the
# way through. A bound exactly on the hover point is not a bound.
#
# The measured arms separate cleanly with a gap in between:
#
#     provoked a cancel   19.3   19.8   (and 20.0 once the floor was set at 20)
#     tolerated           26.3   28.3   32.2   32.9   33.9   40.0   42.3   44.9
#
# 25 sits in that gap: above every arm that has ever provoked the camera, below every arm it has
# ever tolerated. It also reads as a rule rather than a coincidence -- 5 mph of separation is enough
# that Ford is still comfortably inside its envelope rather than at the edge of handing off.
ARM_MIN_SPEED = 25.0 * MPH_TO_MS

# Stopped. NOT a hand-back any more -- see the creep note in `update`: Ford does not hold a stop
# without a lead, so handing back here is what made the car roll. `create_acc_msg` never sets
# `AccBrkPrkEl_B_Rq`, so holding cannot reproduce drive A's park brake.
STOPPED_SPEED = 0.5 * MPH_TO_MS

# THE TIME BOUND, and mind the RATE. `update` is called from inside the carcontroller's ACCDATA
# block, which runs on `ACC_CONTROL_STEP = 2` -- so this counts 50 Hz frames, NOT 100 Hz control
# frames. The first version said "800 = 8 s at 100 Hz" and would have been SIXTEEN seconds, which is
# not comfortably under the 40 s that latched the camera on drive A. Stated as seconds and derived,
# so the next person cannot inherit the same factor of two.
OVERRIDE_HZ = 50.0
# 8.0 -> 15.0 ON 2026-08-20. The note above says "if a real stop needs longer than this, that is a
# finding to act on rather than a number to quietly raise" -- so, the finding, with arithmetic:
#
# Route 0000039a, at the gate, measured: 19.8 mph (8.85 m/s) with the model's stop point 41.8 m
# away. That stop needs 8.85^2 / (2 * 41.8) = 0.94 m/s^2 and takes 2 * 41.8 / 8.85 = **9.4 s**.
# Against an 8.0 s bound the override would have handed back MID-STOP even if it had armed -- while
# moving, below Ford's floor, with the light still ahead. Two independent bugs on the same approach.
#
# 20.0 covers a stop from the 45 mph ENTER_SPEED at a comfortable rate (20.1 m/s at 1.2 m/s^2 =
# 16.8 s) with margin, and is still HALF drive A's 40 s camera latch -- which is the only thing this
# bound was ever protecting. It was 15.0 for a few minutes on the way here, sized for a 25 mph
# entry; the entry moved to 45 once his empty lights were measured, and a bound that cannot finish
# the approach it now permits would hand back mid-stop at speed, which is worse than not arming.
MAX_ACTIVE_S = 20.0
MAX_ACTIVE_FRAMES = int(MAX_ACTIVE_S * OVERRIDE_HZ)  # 1000 at 20 s; DERIVED, never restate it

# THE HOLD, which is a different regime from the approach and so gets its own bound.
#
# The approach contradicts Ford while the car is MOVING -- that is what drive A's 40 s cancel latch
# was about, and why MAX_ACTIVE_S is a tight 8. A standstill is not that: Ford has no lead to hold
# against, its ACC is below its own operating speed, and our frame asserts `AccStopStat_B_Rq`, the
# same bit Ford asserts while holding a stop of its own.
#
# 45 s covers an ordinary light without being unbounded. It is REASONED, not measured -- nothing has
# ever held a stop on this car for us to measure -- so it stays finite, and the end is logged rather
# than silent, because the failure it produces is the car starting to roll.
MAX_HOLD_S = 45.0
MAX_HOLD_FRAMES = int(MAX_HOLD_S * OVERRIDE_HZ)

# How long the model must stop asking before that is believed, in 50 Hz frames. `has_slow_down`
# arrives as False whenever `longitudinalPlanSP` is merely not alive-and-valid, so an undebounced
# read turns a dropped message into a released brake at a light. Half a second is long enough to
# ride out a hiccup and short enough that a genuine change of mind is acted on promptly.
NO_ASK_RELEASE_FRAMES = int(0.5 * OVERRIDE_HZ)

# Long enough for a 20 mph stop at a comfortable rate, five times under drive A's 40 s. If a real
# stop needs longer than this, that is a finding to act on rather than a number to quietly raise.

# HOW LONG THE MODEL'S STOP POINT MUST BEHAVE LIKE A REAL PLACE BEFORE WE ACT ON IT, in 50 Hz
# frames. THIS EXISTS BECAUSE HE SAID SO: *"Maybe there were some false positives but they
# self-corrected."*
#
# Raising ENTER_SPEED to 45 made that remark load-bearing. At 20 mph a phantom stop cost an alert
# and nothing else; at 45 mph it would cost a brake application on an open road, so the trigger now
# has to tell a real stop from a spurious one rather than trusting `has_slow_down`.
#
# THE TEST IS PHYSICS, NOT A TIMER. A real stop point is fixed in the world, so driving toward it
# must make the distance shrink at roughly the speed we are travelling. A phantom does not close --
# it grows, or it sits at a constant range while the car moves. Measured on route 0000039c, every
# engaged asking-episode over 3 s:
#
#     REAL (he confirmed the first as his traffic light, and two ended at a standstill)
#       t=68     13.9 s   endpoint 105 ->  32 m      t=281    29.4 s   endpoint   6 ->   0 m
#       t=1009   57.5 s   endpoint 139 ->   0 m
#     SPURIOUS -- all short, none closing
#       t=329     4.1 s   endpoint 132 -> 148 m      t=965     5.0 s   endpoint 141 -> 142 m
#       t=370     4.2 s   endpoint  32 ->  73 m
#
# 1.5 s of consistent closing separates them with room to spare, and costs the real light only the
# first 1.5 s of a 13.9 s approach.
CLOSING_CONFIRM_FRAMES = int(1.5 * OVERRIDE_HZ)

# MEASURED OVER THE WINDOW, NOT FRAME TO FRAME, and that distinction is the whole gate.
#
# The first version counted CONSECUTIVE frames where the endpoint had closed since the previous
# frame, and it rejected EVERY approach on both drives -- his real traffic light included. The raw
# 50 Hz endpoint jitters: measured on route 0000039a, consecutive samples on a genuine approach read
# 41.8, 41.8, 43.5, 43.5, 43.9 m. It is closing over seconds and rising over frames, so any
# consecutive test resets on noise and never completes.
#
# It passed its unit test because the fixture interpolated a perfectly smooth line, which is the
# same fixture-is-unphysical trap this file has now hit three times: a constant endpoint, a constant
# speed, and now a noiseless one. Compare against the sample from CLOSING_CONFIRM_FRAMES ago and the
# road covered in between, and jitter cancels.

# How much of the distance actually travelled the endpoint must give up to count as closing. Not
# 100%: the model re-plans every frame and the endpoint carries real jitter, so demanding perfect
# tracking would reject genuine approaches. Not 0 either -- that is the flat 141 -> 142 m case.
CLOSING_FRACTION = 0.35

# A single-frame endpoint move larger than this is a DIFFERENT STOP POINT, not this one moving, so
# the evidence gathered about the old one is void. Metres.
#
# Found by the regression test for the frozen-window bug, 2026-08-20: the window compares net change
# across 1.5 s, and a net test cannot tell a steady approach from ONE DISCONTINUITY. Sit behind a
# lead with the endpoint parked at 180 m, then have the model re-plan to 20 m, and the window reads
# a 160 m "close" over 20 m of road and confirms instantly. That is not a car approaching a place;
# it is the model changing its mind about which place.
#
# 15 m is comfortably above the real jitter and far below a re-plan. Measured frame-to-frame noise
# on a genuine approach is ~2 m (41.8 -> 43.5 -> 43.9), while true closing is under 0.5 m per frame
# at these speeds, so nothing legitimate comes near this.
ENDPOINT_JUMP_M = 15.0

# A lead this close is Ford's business. Beyond it the radar has nothing useful and the stop is ours.
LEAD_DISQUALIFIES_M = 60.0

# The deceleration the arming distance is computed against, m/s^2. Deliberately gentler than the
# 1.3 that lights the stop lamps: this decides WHEN to take over, and taking over early enough to
# stop comfortably is the whole point. It is not a commanded rate -- `create_acc_msg` still authors
# the actual braking.
# 1.5 -> 0.9 ON 2026-08-20, because the honest description of this gate at the bottom of this block
# turned out to describe a FALSE PREMISE.
#
# The gate reduces to "arm only if the stop needs harder than STOP_DECEL/STOP_MARGIN", which at
# 1.5/1.3 was 1.15 m/s^2. The justification was: "a stop gentler than that is one Ford's set speed
# can still deliver, which is exactly when the override should stay out."
#
# FORD DELIVERS NOTHING BELOW 20 MPH. It quits at its set-speed floor and, with no lead, holds no
# stop at all -- which is the entire reason this feature exists. So on a real approach the model
# planned 0.94 m/s^2, the gate demanded 1.15, the override stayed out for being "too gentle", and
# Ford stayed out for having given up. Nobody stopped the car. Measured on route 0000039a: 13 of 13
# frames that reached this gate refused here, endpoint 41.8-43.9 m against a 33.9-34.2 m range.
#
# 0.9 puts the threshold at 0.69 m/s^2, below the ~0.95 a real light plans, so an ordinary stop
# arms. It is still a bounded urgency test rather than "any stop anywhere": a stop 200 m out at
# 25 mph needs 0.31 m/s^2 and is still correctly refused.
STOP_DECEL = 0.9

# Take over a little before the arithmetic says we must, because the handover itself costs time and
# because arriving late is the failure this feature exists to remove.
STOP_MARGIN = 1.3

# WHAT THIS GATE ACTUALLY TESTS, stated honestly after review took it apart: comparing the model's
# endpoint against `v^2/(2*STOP_DECEL)*STOP_MARGIN` reduces to "the model is planning a stop harder
# than STOP_DECEL/STOP_MARGIN", INDEPENDENT OF SPEED. It is an URGENCY test, not a proximity one,
# and it is not what "the stop point is close enough that braking is due" describes -- so do not
# reason about it that way.
#
# THE SECOND HALF OF THIS NOTE USED TO SAY the threshold was defensible because "a stop gentler than
# that is one Ford's set speed can still deliver". That was WRONG and it cost a real stop. Below
# `ENTER_SPEED` Ford delivers nothing: it is at its floor, and with no lead it holds no stop. There
# is no gentler authority to defer TO. The threshold therefore has to sit below what a real light
# plans (~0.95 m/s^2), not above it -- see STOP_DECEL for the measurement.
#
# The general lesson, because this shape has now appeared three times in this file: a gate that
# defers to another controller must name what that controller will actually DO, not what it is
# nominally responsible for.


class FordStopOverride:
  """Decide, per frame, whether to send openpilot's ACCDATA instead of Ford's.

  Pure logic: no CAN, no params, no messaging. `update` takes the state and returns a bool, so the
  whole thing is testable offline -- which matters because every other part of this feature had to
  be learned from a drive.
  """

  def __init__(self):
    self.active = False
    self.spent = False          # fired already; will not re-arm until the model stops asking
    self.frames = 0
    # The hold phase. Separate counter from `frames` because the approach and the hold are different
    # regimes with different bounds -- see MAX_HOLD_S.
    self.holding = False
    self.hold_frames = 0
    self.no_ask_frames = 0      # consecutive frames the model has not asked for a stop
    # (endpoint, metres travelled since the previous sample), one entry per asking frame over the
    # confirmation window. This is what keeps a phantom stop from arming a brake at 45 mph; see
    # CLOSING_CONFIRM_FRAMES for why it is a WINDOW and not the consecutive-frame counter it
    # replaced -- that counter armed zero times on both logged drives, because the endpoint jitters
    # frame to frame and any single rise reset it.
    self.closing_window: deque = deque(maxlen=CLOSING_CONFIRM_FRAMES)
    self.last_result = ""       # for logging only, never used to decide

  def _end(self, why: str) -> None:
    if self.active:
      self.last_result = why
    self.active = False
    self.spent = True
    self.frames = 0
    self.holding = False
    self.hold_frames = 0

  def update(self, long_active: bool, v_ego: float, has_slow_down: bool, op_stopping: bool,
             lead_distance: float, stop_endpoint_m: float = 0.0) -> bool:
    """Args:
      long_active:     openpilot longitudinal is actually active this frame.
      v_ego:           m/s.
      has_slow_down:   the MODEL is planning to stop for something ahead (dec.has_slow_down()).
      op_stopping:     openpilot's plan has reached its stopping state. UNUSED -- see below. Kept
                       on the signature because the carcontroller still has it and removing an
                       argument is a change to the call site for no gain.
      lead_distance:   metres to the radar lead, or 0.0 / inf when there is none.
      stop_endpoint_m: metres to the model's own stop point, 0.0 when it has none.

    Returns True when openpilot's authored command should go out in place of Ford's.
    """
    # Nothing may be authored with longitudinal inactive -- panda passes only the inactive frame
    # there, and `create_acc_msg` clearing Cmbb_B_Enbl is how disengagement reaches the car.
    if not long_active:
      self.active = False
      self.spent = False
      self.frames = 0
      # The closing evidence is about ONE approach. Carrying it across a disengagement would let a
      # confirmation earned before the driver took over arm instantly on re-engagement somewhere else.
      self.closing_window.clear()
      # THE HOLD MUST DIE HERE TOO. Missed on the first pass and caught by its own test: this branch
      # reset `active` and `frames` and left `holding` set, so a hold survived longitudinal going
      # inactive -- and `holding` is what latches the resume gate. He presses the gas to pull away,
      # longitudinal drops, and the gate stays latched telling the car this stop was still ours.
      self.holding = False
      self.hold_frames = 0
      return False

    # The reason going away is the only thing that re-arms it. Deliberately NOT keyed on the car
    # having stopped: a stop that gets abandoned half way must not be able to fire again on the
    # same approach.
    if not has_slow_down:
      # DEBOUNCED, and both halves of that matter. `carcontroller.py` falls back to
      # `has_slow_down = False` whenever `longitudinalPlanSP` is not alive-and-valid, so ONE dropped
      # message used to do two bad things at once: release a hold -- dropping the brake at a light
      # for a planner hiccup -- and clear `spent`, which is the once-per-approach latch. Cleared,
      # the override could re-arm the moment the car rolled, brake, release, and cycle.
      #
      # Under the old design neither mattered: the override had already ended at the standstill, so
      # there was no hold to drop and nothing to re-arm into. Holding through the stop is what made
      # a single frame load-bearing.
      self.no_ask_frames += 1
      if self.no_ask_frames <= NO_ASK_RELEASE_FRAMES:
        # Not yet convinced -- keep doing whatever we were doing, INCLUDING COUNTING.
        #
        # Returning here without advancing the bounds made them count only frames where the model
        # was asking, while the override TRANSMITTED on every frame. `has_slow_down` is a threshold
        # on a 5-sample moving average sampled at 50 Hz off a 20 Hz publisher, so chatter near the
        # threshold is the ordinary case on a marginal stop -- and at a 1-in-26 duty cycle this ran
        # 208 s of continuous contradiction against an 8 s bound. That bound exists for exactly one
        # reason: to stay under drive A's ~40 s camera latch.
        if self.active:
          if self.holding:
            self.hold_frames += 1
            if self.hold_frames > MAX_HOLD_FRAMES:
              self._end("hold bound reached during a model-request dropout")
              return False
          else:
            self.frames += 1
            if self.frames > MAX_ACTIVE_FRAMES:
              self._end("time bound reached during a model-request dropout")
              return False
          # A lead arriving during the dropout is still Ford's. Half a second of ignoring it is half
          # a second at precisely the moment a car pulls in front.
          if 0.0 < lead_distance < LEAD_DISQUALIFIES_M:
            self._end("a lead arrived during a model-request dropout")
            return False
        return self.active
      if self.active:
        self._end("model stopped asking")
      # After `_end`, not before: `_end` sets spent=True, so an assignment ahead of it is dead and
      # reads as though one of the two paths needed it.
      self.spent = False
      # The model gave up on this stop, so the evidence for it is gone too. A phantom that flickers
      # off and back on must start earning confirmation again rather than resuming a part-built case.
      self.closing_window.clear()
      return False

    # The model is asking again, so the debounce starts over.
    self.no_ask_frames = 0

    lead_close = 0.0 < lead_distance < LEAD_DISQUALIFIES_M

    if self.active:
      self.frames += 1

      # THE CREEP. Reported 2026-08-20: *"OP long tried to stop, but it crept forward a bit so I
      # gave up."* This block used to `_end("stopped")` the instant the car reached STOPPED_SPEED,
      # on the reasoning that "Ford's own AccStopStat handling takes it from here". IT DOES NOT --
      # not without a lead. Ford's stop-and-go holds a stop behind a CAR; at an empty light there is
      # nothing for its radar to hold against, so handing back at 0.5 mph hands back to a controller
      # that has no reason to keep the car still. The car rolls.
      #
      # So the override now HOLDS through standstill, and two things make that safe rather than
      # bold:
      #
      #   - `create_acc_msg` NEVER SETS `AccBrkPrkEl_B_Rq`. It is not in the values dict at all, so
      #     holding openpilot's frame against a stationary car cannot reproduce drive A's park
      #     brake -- that came from the PASSTHROUGH forwarding Ford's own copy of that bit.
      #   - `AccStopStat_B_Rq` is set from `stopping`, and `shouldStop` is measured to be true
      #     exactly at a standstill. So while holding, our frame asserts the SAME bit Ford asserts
      #     while holding a stop. We are speaking its language, not inventing one.
      #
      # The hold is bounded SEPARATELY from the approach, because they are different regimes. The
      # approach contradicts Ford while moving, which is what drive A's 40 s latch was about. At a
      # standstill with no lead there is far less to contradict -- Ford has no target and its ACC is
      # below its own operating speed. That is REASONING, not measurement, so the bound stays finite
      # and generous rather than absent, and the first drive with a real light is what checks it.
      if v_ego <= STOPPED_SPEED and lead_close:
        # A CAR STOPPED IN FRONT OF US WHILE WE WERE HOLDING. Ford's stop-and-go holds behind a lead
        # -- that is the one case it does well and the reason a lead disqualifies the override at
        # all -- so hand it back rather than keeping a brake command the radar now has a better
        # answer for. The standstill branch below returned before `lead_close` was ever evaluated,
        # so this case was unreachable and the module docstring's "a lead appeared. Hand back." was
        # false at a standstill.
        self._end("a lead arrived while holding; Ford's stop-and-go owns this")
        return False

      if v_ego <= STOPPED_SPEED:
        # THE APPROACH BOUND MUST NOT COUNT HOLD FRAMES. `frames` used to stop accumulating here
        # because this branch ended the override; now it does not, so without this the 8 s approach
        # bound expires DURING a normal light and the first frame the car rolls above STOPPED_SPEED
        # hands it back to Ford permanently, mid-roll -- the exact failure the hold exists to stop.
        #
        # ON ENTRY ONLY. Resetting every stopped frame let a creep oscillating either side of
        # STOPPED_SPEED zero the approach bound on alternate frames, so it could never fire in that
        # band -- and a 45 s hold measured 90 s of wall clock. The creep regime is precisely what
        # this feature was rewritten for, so that oscillation is the expected input, not an edge.
        if not self.holding:
          self.frames = 0
        self.holding = True
        self.hold_frames += 1
        if self.hold_frames > MAX_HOLD_FRAMES:
          self._end("hold bound reached -- the car may roll, see the creep note")
          return False
        self.last_result = "holding a stop Ford will not hold"
        return True

      # MOVING AGAIN. `holding` has to clear here or it reads as "has held" rather than "is
      # holding", and the resume gate keys on it -- it would re-assert on a moving car.
      self.holding = False

      if lead_close:
        self._end("a lead appeared; Ford's stop-and-go owns this")
        return False
      if self.frames > MAX_ACTIVE_FRAMES:
        # The bound from drive A. Handing back mid-stop is not comfortable; a latched camera for the
        # rest of the drive is worse, and this is the only thing standing between the two.
        self._end("time bound reached")
        return False
      return True

    # ---- the closing tracker runs BEFORE every gate below, and that ordering is the fix ---------
    #
    # It used to sit down with the endpoint checks, after the speed and lead gates. Those gates
    # `return False` without appending OR clearing, so the window FROZE while they refused and the
    # samples in it aged out of relevance while the car kept driving.
    #
    # Found by review, 2026-08-20, and reproduced: build honest closing evidence at 30 mph, let a
    # lead sit inside 60 m for 20 s, then drop the lead and feed a COMPLETELY STATIC endpoint. The
    # override armed within 10 frames -- `closing_window[0]` was a 200 m reading from 20 s and
    # ~270 m of road earlier, while `travelled` summed only the stale stored steps. That is the
    # phantom filter defeated by the most ordinary event on the road: `a lead appeared` ended the
    # override on 13,012 frames of the 0000039c replay, so nearly every approach passes through it.
    #
    # KEPT RATHER THAN CLEARED, deliberately. A lead ahead does not make the stop point behind it
    # imaginary -- the endpoint is still closing and that evidence is still true. Clearing would
    # throw away a real observation and make the override re-earn it after every car that merges
    # in. Updating the window on every asking frame keeps it both fresh and honest, and a gap can no
    # longer open because there is no path that skips it.
    if stop_endpoint_m > 0.0:
      # A JUMP MEANS A NEW PLACE. See ENDPOINT_JUMP_M -- without this the net-change test below
      # confirms on a single discontinuity, which is how the model re-planning reads.
      if self.closing_window and abs(stop_endpoint_m - self.closing_window[-1][0]) > ENDPOINT_JUMP_M:
        self.closing_window.clear()
      self.closing_window.append((stop_endpoint_m, v_ego / OVERRIDE_HZ))
    else:
      # No endpoint is no evidence -- and a gap in the plan must not preserve a part-built case.
      self.closing_window.clear()

    if self.spent:
      return False

    # ---- arming, and every clause is a REASON rather than a comparison with Ford ----------------
    if v_ego > ENTER_SPEED:
      return False          # the set speed can still express this; ICBM is strictly better
    if v_ego < ARM_MIN_SPEED:
      return False          # below Ford's floor, seizing authority latches the camera -- see below
    if lead_close:
      return False          # Ford's radar has it, and Ford's stop-and-go is better than ours

    # `op_stopping` NO LONGER ARMS THIS, and the reason is measured. 2026-08-20, three drives and
    # 21,936 frames where `longitudinalPlan.shouldStop` was true:
    #
    #     0000039a  5169 frames  max 1.7 mph      00000393  7103  max 2.9 mph
    #     00000397  9664 frames  max 2.8 mph      ABOVE 5 MPH: 0.0% ON ALL THREE
    #
    # It is a STOPPED-CAR state, not an approach state. Combined with the `v_ego > STOPPED_SPEED`
    # clause above, the arming window was 0.5 to 2.9 mph -- by which point there is nothing left to
    # stop. THE TRIGGER WAS CIRCULAR: it needed the plan committed to stopping in order to do the
    # stopping, and the plan only commits once the car has already stopped. It never fired on any
    # drive, and no amount of leaving the brake alone could reach it.
    #
    # His own light, route 0000039a: engaged, foot off the brake, ICBM walking the set speed 80 ->
    # 57, sitting at 20 mph, `shouldStop` false the whole way. He braked.
    #
    # WHAT ARMS IT NOW IS A DISTANCE, which keeps this a named bounded condition rather than a mood.
    # `has_slow_down` alone is far too loose -- 8,207 frames on that one drive -- so the model's own
    # stop point has to be close enough that braking is actually due:
    #
    #     endpoint <= v^2 / (2 * STOP_DECEL) * STOP_MARGIN,  floored at STOP_MIN_RANGE_M
    #
    # which is the same trigger-distance arithmetic SCC-Map uses, and for the same reason: a fixed
    # metre count is wrong at both ends of the speed range.
    #
    # FAILS CLOSED ON A MISSING ENDPOINT. `endpoint_x()` is inf when the model's plan is not full
    # length and inf is clamped to 0 on the wire, so 0 means "no endpoint" -- never "stopping right
    # here". Arming on 0 would fire at every light the model merely felt uneasy about.
    # NaN FAILS BOTH COMPARISONS AND WOULD FALL THROUGH INTO ARMING -- the exact opposite of what
    # the paragraph above claims. `not (x > 0.0)` catches NaN where `x <= 0.0` does not, and the
    # publisher's `isfinite` guard lives in a DIFFERENT REPO from this consumer, so this file has no
    # structural claim on it.
    if not (stop_endpoint_m > 0.0):
      return False

    # DOES THE STOP POINT BEHAVE LIKE A REAL PLACE? Over the window, a fixed point ahead must give
    # up at least CLOSING_FRACTION of the road actually covered. The window itself is maintained
    # above, before the gates, so that a refusal cannot freeze it.
    if len(self.closing_window) < CLOSING_CONFIRM_FRAMES:
      return False          # not enough history yet to tell a place from a phantom
    oldest_endpoint = self.closing_window[0][0]
    # Skip the oldest sample's own increment: it is the road covered BEFORE that reading was taken.
    travelled = sum(step for _, step in list(self.closing_window)[1:])
    if oldest_endpoint - stop_endpoint_m < CLOSING_FRACTION * travelled:
      return False          # not closing like a place in the world -- see CLOSING_FRACTION
    # NO FLOOR. `STOP_MIN_RANGE_M = 10.0` was added on the reasoning that the computed range gets
    # too tight to be useful at a crawl. It does the opposite: the model's trajectory horizon is
    # roughly 10*v, so below about 2.2 mph the horizon is ITSELF under 10 m and the gate armed with
    # NOTHING AHEAD -- a spurious brake-to-a-stop at walking pace. The arithmetic needs no help:
    # `brake_range` is below the free-flow horizon at every speed under ~51 mph, so it is already a
    # real test across this feature's whole range, and at low speed a short range is CORRECT because
    # the remaining stopping distance is short too.
    brake_range = (v_ego * v_ego) / (2.0 * STOP_DECEL) * STOP_MARGIN
    # AND NEVER FURTHER THAN THE TIME BOUND CAN CARRY. Found by review, 2026-08-20.
    #
    # `brake_range` reduces to "any stop needing harder than STOP_DECEL/STOP_MARGIN = 0.69 m/s^2",
    # which at ENTER_SPEED admits a stop 292 m away. Decelerating to a standstill over 292 m from
    # 20.12 m/s takes 2*292/20.12 = 29.1 s against a 20 s bound -- so the override would arm, brake,
    # and hand back at 14.0 mph with ~30 m still to run. That is BELOW Ford's 20 mph floor, where
    # Ford will not carry it either: the abandoned stop this feature exists to remove, recreated by
    # the feature itself.
    #
    # Stopping from `v` over distance `d` takes 2d/v, so the reachable range is `v * MAX_ACTIVE_S /
    # 2`. Derived from the bound rather than picked, so the two can never drift apart again.
    #
    # It does not cost his measured lights: 43.9 mph/148 m against a 196 m cap, 28.7 mph/98.6 m
    # against 128 m, 32.5 mph/97 m against 145 m. All three still arm.
    reachable_range = v_ego * MAX_ACTIVE_S / 2.0
    if stop_endpoint_m > min(brake_range, reachable_range):
      return False          # too far for the set speed to hand over, or too far to finish in time

    self.active = True
    self.frames = 0
    self.holding = False
    self.hold_frames = 0
    self.closing_window.clear()
    self.last_result = "stopping for something the radar cannot see"
    return True
