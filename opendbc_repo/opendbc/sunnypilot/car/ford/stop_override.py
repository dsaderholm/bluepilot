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
  - the car is stopped. Done, hand back.
  - the reason went away -- the model stopped planning a stop, or a lead appeared. Hand back.
  - `MAX_ACTIVE_FRAMES`. The bound above.
  - openpilot longitudinal went inactive. Nothing may be authored at all then.

After ANY of those it is SPENT and refuses to re-arm until the model stops asking, so a stop that
does not complete cannot re-trigger every frame and turn a bounded override into a permanent one.
"""
from __future__ import annotations

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
ENTER_SPEED = 20.0 * MPH_TO_MS

# Stopped. Ford's own AccStopStat handling takes it from here, and holding a brake command against a
# stationary car is exactly how the park brake got involved on drive A.
STOPPED_SPEED = 0.5 * MPH_TO_MS

# THE TIME BOUND, and mind the RATE. `update` is called from inside the carcontroller's ACCDATA
# block, which runs on `ACC_CONTROL_STEP = 2` -- so this counts 50 Hz frames, NOT 100 Hz control
# frames. The first version said "800 = 8 s at 100 Hz" and would have been SIXTEEN seconds, which is
# not comfortably under the 40 s that latched the camera on drive A. Stated as seconds and derived,
# so the next person cannot inherit the same factor of two.
OVERRIDE_HZ = 50.0
MAX_ACTIVE_S = 8.0
MAX_ACTIVE_FRAMES = int(MAX_ACTIVE_S * OVERRIDE_HZ)  # 400

# Long enough for a 20 mph stop at a comfortable rate, five times under drive A's 40 s. If a real
# stop needs longer than this, that is a finding to act on rather than a number to quietly raise.

# A lead this close is Ford's business. Beyond it the radar has nothing useful and the stop is ours.
LEAD_DISQUALIFIES_M = 60.0


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
    self.last_result = ""       # for logging only, never used to decide

  def _end(self, why: str) -> None:
    if self.active:
      self.last_result = why
    self.active = False
    self.spent = True
    self.frames = 0

  def update(self, long_active: bool, v_ego: float, has_slow_down: bool, op_stopping: bool,
             lead_distance: float) -> bool:
    """Args:
      long_active:   openpilot longitudinal is actually active this frame.
      v_ego:         m/s.
      has_slow_down: the MODEL is planning to stop for something ahead (dec.has_slow_down()).
      op_stopping:   openpilot's own plan has reached its stopping state.
      lead_distance: metres to the radar lead, or 0.0 / inf when there is none.

    Returns True when openpilot's authored command should go out in place of Ford's.
    """
    # Nothing may be authored with longitudinal inactive -- panda passes only the inactive frame
    # there, and `create_acc_msg` clearing Cmbb_B_Enbl is how disengagement reaches the car.
    if not long_active:
      self.active = False
      self.spent = False
      self.frames = 0
      return False

    # The reason going away is the only thing that re-arms it. Deliberately NOT keyed on the car
    # having stopped: a stop that gets abandoned half way must not be able to fire again on the
    # same approach.
    if not has_slow_down:
      if self.active:
        self._end("model stopped asking")
      # After `_end`, not before: `_end` sets spent=True, so an assignment ahead of it is dead and
      # reads as though one of the two paths needed it.
      self.spent = False
      return False

    lead_close = 0.0 < lead_distance < LEAD_DISQUALIFIES_M

    if self.active:
      self.frames += 1
      if v_ego <= STOPPED_SPEED:
        self._end("stopped")
        return False
      if lead_close:
        self._end("a lead appeared; Ford's stop-and-go owns this")
        return False
      if not op_stopping:
        self._end("the plan is no longer stopping")
        return False
      if self.frames > MAX_ACTIVE_FRAMES:
        # The bound from drive A. Handing back mid-stop is not comfortable; a latched camera for the
        # rest of the drive is worse, and this is the only thing standing between the two.
        self._end("time bound reached")
        return False
      return True

    if self.spent:
      return False

    # ---- arming, and every clause is a REASON rather than a comparison with Ford ----------------
    if v_ego > ENTER_SPEED:
      return False          # the set speed can still express this; ICBM is strictly better
    if v_ego <= STOPPED_SPEED:
      return False          # already stopped, nothing to do
    if lead_close:
      return False          # Ford's radar has it, and Ford's stop-and-go is better than ours
    if not op_stopping:
      return False          # the model wants to stop but the plan has not committed yet

    self.active = True
    self.frames = 0
    self.last_result = "stopping for something the radar cannot see"
    return True
