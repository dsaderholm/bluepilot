"""
FusionPilot: passing-assist observation. PHASE 1 -- LOG ONLY.

Nothing here alerts, steers, touches the set speed, or feeds any controller. The single output is
a message on longitudinalPlanSP describing what the system WOULD have suggested and, more usefully,
which gate stopped it. It exists to answer three questions that cannot be settled by reading code.

Where this is going, and why the driver never touches the stalk
--------------------------------------------------------------
THE FINISHED FEATURE DECIDES, SIGNALS AND MOVES BY ITSELF. Stated flatly on 2026-08-09:

    "LANE CHANGES WILL NOT BE STARTED BY MY StALK"
    "If I had to manually do anything, then I might as well just keep using the SunnyPilot
     nudgeless lane changes!"

Which is exactly right, and it is the whole point of the feature. Nudgeless already performs a
crossing the driver has decided on. The thing being built here is the DECISION -- is there someone
slower ahead, is the next lane clear and going the same way, is it legal and worth the move -- and a
decision the driver has to ratify with a stalk flick is not one.

This was once thought unreachable. The original note here read "openpilot cannot initiate a lane
change on Ford", because desire_helper gates on carState.leftBlinker/rightBlinker and those come
from the SCCM's Steering_Data_FD1 on bus 0. Phase-locking against the gateway's frame removed that
on 2026-08-06 -- the car's blinker is now commandable and confirmed on the road.

But NOT by making carState.leftBlinker read back true, and that distinction decides the wiring.
carstate.py reads TurnLghtSwtch_D_Stat, which is the value WE transmit, and openpilot cannot see its
own transmissions (panda returns TX at bus | 0x80 and the parser drops it). So the switch never
reports our own command back, and a design that signals and waits for desire_helper to notice would
wait forever. The two halves are therefore separate on purpose:

  * the lane change desire is raised DIRECTLY, not round-tripped through the car;
  * the lamps are commanded, and CS.turn_lamp_left / turn_lamp_right -- the body module's report of
    what the lamps ACTUALLY did -- is the confirmation that the signal is lit before the car moves.

That second half is the driver's own rule for the timing: the confirmation has to overlap the
blinker rather than precede it. Lamps flash, so a consumer latches over a flash period rather than
trusting one frame.

None of this changes what the driver is responsible for. Automating the decision does not move this
off SAE Level 2 -- the level is set by who monitors and who carries liability, not by how much the
car does, which is why BlueCruise's hands-free automatic lane change is also Level 2. The driver
supervises and takes over. That is why an unavailable sensor must never report as clear.

The three unknowns
------------------
1. ONCOMING TRAFFIC -- ANSWERED, see adjacent_lane.py. This was the one that decided whether the
   idea survived. modelV2 publishes lane geometry, not direction of travel, so on a two-lane
   two-way road the lane to the left is oncoming traffic and looks exactly like a passing lane to
   every geometry test below. Map data could not help and still cannot on this build: mapd v1.12.0
   ships here and writes no oneway tag and no lane count. (mapd v2 publishes oneWay, lanes and
   highwayClass on a MapdOut message, which would make this a cross-check rather than the only
   source, whenever sunnypilot moves to it.)

   The front radar settles it directly. An oncoming vehicle's absolute ground speed is roughly
   minus its own, which nothing travelling our way and no roadside object can produce, and the
   lateral band excludes an opposing carriageway across a median for free. The veto is per side and
   held for a while after the last sighting; the reasoning is all in adjacent_lane.py.

   What is still worth measuring from a drive: how often it fires on a divided road it should not
   (oncomingAnySide and oncomingSeen are logged with every decision), and whether 90 s of memory is
   the right number for the roads actually driven.

2. TSR OVERTAKING. Traffic_RecognitnData carries a latched no-overtaking zone state with its own
   confidence channel. If this market's camera populates it, it is a sound VETO. It is not a
   permit: absence of a no-passing sign says nothing about whether the left lane is same-direction,
   since those zones are only ever marked on two-way roads in the first place.

3. BLIS. carState.leftBlindspot is SodDetct*_D_Stat != 0 -- blind-spot OCCUPANCY. A vehicle closing
   from 150 m back does not light it until already alongside, which is far too late to base a
   passing suggestion on. Recorded here so its behavior at decision time can be compared against
   what a safe gap actually looked like.

Thresholds are starting values, not derived constants. Refit them from logs; that is the point.
"""

from cereal import custom
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot import PARAMS_UPDATE_PERIOD
from openpilot.sunnypilot.selfdrive.controls.lib.adjacent_lane import (
  AdjacentLane, path_offset, DEFAULT_ONCOMING_MEMORY_S,
)
from openpilot.sunnypilot.selfdrive.controls.lib.overtake_progress import OvertakeProgress
from openpilot.sunnypilot.selfdrive.controls.lib.passing_maneuver import PassingManeuver

from openpilot.sunnypilot.selfdrive.controls.lib.rear_approach import RearApproach

Phase = custom.LongitudinalPlanSP.PassingAssist.Maneuver
Side = custom.LongitudinalPlanSP.PassingAssist.Side
Blocked = custom.LongitudinalPlanSP.PassingAssist.Blocked
Reason = custom.LongitudinalPlanSP.PassingAssist.Reason
Trigger = custom.LongitudinalPlanSP.PassingAssist.Trigger
RefSource = custom.LongitudinalPlanSP.PassingAssist.ReferenceSource

# --- lane line indices. modelV2 publishes exactly 4 lines and 2 road edges. ---
# y is negative to the left and positive to the right in this frame: ldw.py tests the left line
# against -(1.08 + CAMERA_OFFSET) and the right against +(1.08 - CAMERA_OFFSET), and
# lateral_curv_ext computes width as laneLines[2].y + (-laneLines[1].y).
LL_FAR_LEFT, LL_LEFT, LL_RIGHT, LL_FAR_RIGHT = 0, 1, 2, 3
RE_LEFT, RE_RIGHT = 0, 1

# --- road widening (exit / on-ramp detection) ---
# modelV2 publishes 33 points along X_IDXS = 192 * (i/32)^2, so index 4 is ~3 m and index 20 is
# ~75 m. Near is not index 0 because the very first point is noisiest; far is not the last because
# beyond ~100 m the road edge gets unreliable and every curve starts to look like a divergence.
WIDEN_NEAR_IDX, WIDEN_FAR_IDX = 4, 20
# Growth in the lane-line-to-road-edge gap that reads as the road opening up rather than a shoulder
# varying. Roughly two thirds of a lane: enough that a real off-ramp trips it well before the gore
# point, small enough that ordinary shoulder variation does not. Starting value -- fit from logs.
MAX_WIDENING_M = 2.5

# --- geometry gates ---
# Confidence that a painted line exists BEYOND ego's own lane line. Matches the 0.5 that ldw.py
# uses for "lane visible"; raised slightly because acting on it is a stronger claim than warning.
# 0.3, DOWN FROM 0.6, and the old value was stricter than openpilot is about its own best data.
#
# STALE AS WRITTEN -- the constant below is 0.5, not 0.3, and this line has said otherwise since
# whenever it moved back. Left in place rather than deleted because the REASONING is still the
# argument for lowering it; only the number is wrong. And 2026-08-09 settled that lowering is not
# the fix anyway: the measured value was 0.011, so neither 0.5 nor 0.3 nor 0.1 would have passed.
# The model has no opinion about that line, which is a different problem from a strict threshold.
#
# ldw.py calls a lane "visible" at laneLineProbs > 0.5 -- but it tests indices 1 and 2, the EGO
# lane's own lines, which are the two the model predicts most confidently. This tests 0 and 3, the
# lines beyond them, which are inherently less certain: further away, more often occluded, and not
# what the model is optimized for. Asking MORE of the worse-predicted lines was backwards, and the
# result was "No lane to move into" on roads with an obvious empty lane beside them.
#
# Nothing here actuates, so the cost of being wrong in this direction is a suggestion that can be
# looked at and judged. The cost of being wrong the other way is a feature that never speaks, which
# is what a drive already went to finding out.
MIN_ADJACENT_LINE_PROB = 0.5
# Drivable width between ego's lane line and the road edge that counts as a real lane. A US lane
# is 3.7 m; a wide shoulder is under 3. Sitting between them is deliberate -- too low and every
# breakdown lane reads as passable.
# 3.5, RAISED from 3.0, and 3.0 is what suggested moving into a shoulder.
#
# Reported: "it said it would be changing right even though I was in the furthest right lane, which
# means it would have run me right into the shoulder."
#
# The old comment claimed "a US lane is 3.7 m; a wide shoulder is under 3." The second half is
# simply wrong for the roads this drives on: AASHTO gives interstate right shoulders as 10 ft, and
# 12 ft where truck volumes are high -- 3.05 to 3.66 m. So a standard shoulder cleared a 3.0 m bar
# comfortably and read as a lane.
#
# 3.5 rejects a 10 ft shoulder and accepts a 12 ft lane, which is the only gap there is. It does
# NOT separate a 12 ft shoulder from a 12 ft lane, because nothing about width can -- that case is
# what the radar evidence below is for, and why width alone must never be the whole test.
#
# AND THEN IT KEPT HAPPENING ANYWAY: "it just keeps trying to go into the shoulder", and then the
# observation that solves it -- "I can see a red line on the right of the shoulder, where the
# barrier wall is, so it's obvious that's a shoulder."
#
# The model was never confused. It drew the road edge on the wall, on screen, correctly. The gate
# was measuring the wrong distance. edge_gap is ego's lane line out to the road edge, which is the
# next lane PLUS its shoulder from an interior lane and the shoulder ALONE from the outermost one.
# One number for two different quantities: no threshold on it can separate them, and 3.5 m only
# ever moved which of the two cases got it wrong.
#
# So this is now the width of the CANDIDATE LANE ITSELF, ego's line out to the next line beyond it,
# and 3.0 is back because it no longer has a shoulder folded into it. It accepts a 10 ft work-zone
# lane, which is a real lane. Separating lane from shoulder is MIN_EDGE_BEYOND_LINE_M's job.
MIN_LANE_WIDTH_M = 3.0
# ...and an upper bound, because an unbounded one is how a parking area or a gore point reads as a
# very generous lane. Nothing 16 ft wide is a lane.
MAX_LANE_WIDTH_M = 5.0
# How much road has to remain BEYOND the far line of the lane we would move into.
#
# This is the test that answers the shoulder, and it works because of what the model does when
# there is no lane out there: laneLines always has four entries whether or not four lines exist, so
# on the outermost lane the model puts the far one on the only strong feature left -- the road edge
# itself, the red line at the barrier. Far line and road edge land on top of each other and this
# collapses to zero. Beside a real lane, the road edge is a shoulder's width past its far line.
#
# 0.8 m, and deliberately not a shoulder width. This is a DEGENERACY test -- is the model's "lane
# line" just the road edge wearing a different name -- not a claim about how wide the shoulder past
# a real lane must be. AASHTO's right shoulder is 10 ft and would work here, but the INSIDE shoulder
# on a 4-lane divided road is 4 ft, and the same number has to serve both sides. 0.8 sits between
# the degenerate case, where the two land on top of each other, and the narrowest real shoulder
# either of them can have.
MIN_EDGE_BEYOND_LINE_M = 0.8
# Road edge measurements get unreliable at distance and in poor conditions. modelV2 publishes a
# per-edge std; above this the edge gap is not trusted and the side is reported unavailable.
#
# 1.2, MEASURED, from 34 minutes of his own driving on 2026-08-06 (route 0000031e). Every single
# geometry refusal of that drive was this term, it measured 1.04, and 1.2 is where it would have to
# sit to admit four fifths of them. Zero suggestions in 41091 planner frames.
#
# The comment here previously said "0.75, up from 0.5" while the value was 0.5 -- a change argued
# for and never made -- and justified 0.5 by reading roadEdgeStds as a 0..1 scale "where 1 is
# useless", inferred from model_renderer_bp drawing at `clip(1.0 - std, 0, 1)`. The road disagrees:
# 1.04 is an ordinary reading, and the clip exists precisely because std is NOT bounded at 1. That
# was a unit inferred from a rendering clamp rather than measured.
#
# WHAT THIS DOES NOT ESTABLISH, because _record_refusal takes the FIRST failing term in the gate's
# own order and this one is checked first: nothing about paint, lane width or room-beyond. They were
# never reached. Loosening this does not prove the lane is fine -- it lets the three checks that
# actually judge that RUN for the first time, including the room-beyond test that exists to catch
# exactly the shoulder he complained about. Expect the next drive to name a different term.
MAX_ROAD_EDGE_STD = 1.2

# --- lead gates ---
# Below this, passing is not the maneuver being considered.
#
# WAS 40 AND THAT WAS TOO HIGH, which the owner spotted: "do we really need the 40 mph rule anymore
# with everything else we've implemented?" It fails in exactly the case it most matters. Stuck
# behind a tractor on a 55 road, ACC drags you down to 30 -- and passing assist goes silent right
# when a pass is most obviously wanted. The rule was written to keep the system out of town, and
# town is already excluded by CRUISE HAVING TO BE ENGAGED, which the gate above enforces.
#
# 30 rather than lower because the geometry below it stops meaning what it says: at 20 mph a lot of
# what the model calls a lane is a turn pocket, a driveway or a parking aisle, and none of the
# tests here can tell those from a passing lane. Oncoming detection is unaffected either way -- it
# works from a vehicle's ABSOLUTE ground speed, so a car coming the other way is just as obvious at
# 30 as at 70.
DEFAULT_MIN_SPEED_MPH = 30

# --- after the driver changes lanes themselves ---
#
# Asked directly: "what if I do a sunnypilot, nudgeless lane change into an exit lane? Will it try
# to pull me out of that?" It would have. The driver-active gate silences this only WHILE the
# blinker is on; the moment it goes out the system re-evaluates from scratch, and an exit lane is
# geometrically a slow lane with somewhere to go -- so it would happily start working on getting
# out of the one you deliberately just entered.
#
# The rule that resolves it is already in the design notes: geometry gates suppress SUGGESTIONS,
# they never veto the DRIVER. So this does not fight the maneuver; it stands down afterwards.
#
# TWO DURATIONS, because the two cases are not alike:
#
#   Any lane change at all gets a short pause, just long enough for the model's lane lines and the
#   radar's tracks to re-settle around the new lane. Acting on the first frame after a lane change
#   means acting on geometry that is still mid-transition.
#
#   A change into what looked like an EXIT gets much longer -- long enough to actually reach the
#   ramp. This is the case the owner named, and getting it wrong is not a cosmetic error: being
#   told to move out of your exit lane at the gore point is worse than useless.
#
# Both are only ever a STAND-DOWN. He also said: "sometimes I'll do a nudgeless lane change if
# passing assist doesn't pass, but I still want it to take over." So a left-hand change to pass
# something is back to normal within seconds.
SETTLE_AFTER_CHANGE_S = 4

# Blocked reasons that mean THE FEATURE WAS NOT RUNNING, as opposed to it running and declining.
#
# The distinction matters because the agreement score is the number that decides whether this is
# ever worth letting steer, and on 2026-08-09 it read 2 of 106 lifetime -- which sounds like a
# system that disagrees with its driver. The most common miss reason on that drive was `tooSlow`:
# the car was under PassingAssistMinSpeed, where passing assist is deliberately switched off. A
# lane change made on a surface street counted against a feature that was not consulted.
#
# nothingSlower is deliberately NOT here. That one is a live calibration question -- it means the
# feature WAS running and judged the lead fast enough -- and _record_driver_pass exists partly to
# surface exactly those.
OFF_BY_DESIGN = frozenset({int(Blocked.disabled), int(Blocked.notEngaged), int(Blocked.tooSlow),
                           int(Blocked.suspended)})
DEFAULT_EXIT_STANDDOWN_S = 45

# ...and the same again for a maneuver made with NO BLINKER AT ALL. "I usually use sunnypilot
# nudgeless changes, but I also will just fully takeover and do my own steering."
#
# Watching only the stalk would have missed that entirely, and steering into an off-ramp without
# signaling is about as common as driving gets. So sustained steering counts as a maneuver too.
#
# SUSTAINED is the whole difficulty: steeringPressed also fires on the constant small corrections
# of ordinary driving, and a 45 second stand-down every time a hand tightens on the wheel would be
# worse than not having this. Held for most of a second is a deliberate input; anything shorter is
# a correction.
MIN_STEER_TAKEOVER_S = 0.7

# HIS OWN EXIT RULE, the half the geometry tests cannot reach: "if I nudgeless go into the most far
# right lane and go SLOWER there, then I am probably exiting."
#
# The other half of that sentence -- the far right lane -- is already what _moved_toward_an_exit
# tests, and it is the stronger evidence where it applies. What it cannot see is an exit with a
# real DECELERATION LANE: the ramp is a lane, so right_geometry_ok stays true and we are not
# outermost, and nothing widens because the lane was already there. Both existing branches go
# quiet on exactly the exit that is easiest to see out of the windshield.
#
# So the speed is watched AFTER the change rather than during it, because "slower there" happens
# there -- on the ramp, seconds after the stalk goes off. EXIT_WATCH_S has to outlive
# SETTLE_AFTER_CHANGE_S for this to be worth anything.
EXIT_WATCH_S = 12.0
# ~9 mph. Large on purpose. Small drops are ordinary traffic and this competes against a real cost
# (see _arm_exit_watch): every false positive is a pass he wanted and did not get offered.
EXIT_DECEL_MS = 4.0

# How often the drive's measurements are written to a param so they survive being parked.
#
# They are the whole output of phase 1 and they used to live only in RAM: park, screen off, gone.
# Everything measured here is read off a panel at a traffic light or not at all -- there is no log
# digging in this workflow -- so a number that evaporates at the end of a drive was never taken.
#
# 30 s rather than on shutdown because there is no reliable shutdown hook here, and a drive that
# ends with a yanked ignition is exactly the drive worth keeping.
# --- how often the chime may speak ---
#
# From the road: "it just kept beeping over and over."
#
# The chime fired on the RISING EDGE of a suggestion, which is correct exactly once and useless the
# moment a gate flickers. Geometry that sits on a threshold toggles at 20 Hz, and every toggle was
# a fresh rising edge, so a marginal lane produced a tone several times a second. The edge was the
# whole rate limit and an edge is not a rate limit.
#
# Two guards, because they stop different things. The suggestion has to HOLD before it is worth
# announcing -- half a second kills flicker outright and also means the tone only ever marks a
# decision that stayed decided. And a hard interval bounds the worst case whatever the gates do:
# even if a suggestion legitimately comes and goes, nobody needs to hear about it twice in eight
# seconds.
#
# Deliberately not tied to the confirmation time. That is about believing the radar; this is about
# not being irritating, and the two have no reason to move together.
CHIME_SETTLE_S = 0.5
CHIME_MIN_INTERVAL_S = 8.0

# --- ...and a DIFFERENT sound when it backs out ---
#
# "I'll keep reporting back to you instances where it messed up. That's why I like that it makes a
# sound. That helps me to know what it is doing without always looking at it."
#
# Which means the sound is the reporting channel, and it covered exactly one event: a decision.
# The successful case. A sequence that lit the blinker and then withdrew it made no noise at all --
# so the one number this whole dry run exists to produce, `aborts`, was the one thing he could not
# notice without staring at the screen, and therefore could not report.
#
# A LOWER TONE, not a repeat of the same one. Two events that sound identical are one event as far
# as an eyes-front driver is concerned, and the distinction being made here -- it went, versus it
# changed its mind -- is exactly the distinction worth hearing.
#
# NO SETTLE TIME, unlike the suggestion chime. That guard exists because a suggestion is a STATE
# that can flicker; an abort is a discrete event that has already happened, and waiting half a
# second to announce it would only make it later.
#
# Its own interval, and a longer one. A gate strobing signal-abort-signal is worth hearing about,
# but at 12 s apart rather than at whatever rate the gate is managing -- "it just kept beeping over
# and over" is the failure this file has already had once.
ABORT_CHIME_MIN_INTERVAL_S = 12.0

LAST_DRIVE_WRITE_S = 30

# How many drives of summaries to keep. See _archive_drive.
#
# "I'm not going to look at that after each drive. That is cool for me to see, but I didn't want it
# to be what I have to tell you."
#
# Fair, and it was the wrong workflow to have built toward. PassingAssistLastDrive is OVERWRITTEN
# every ignition cycle, so a week of driving left exactly one drive's numbers and the only way to
# keep the rest was for him to read a panel and retype it. That makes the driver the data pipeline
# for a system whose whole point is that it measures things by itself.
#
# So the car keeps its own history and he hands it over once, whenever. Twenty drives is a couple
# of weeks of ordinary use, small enough to paste in one go, and enough for the questions that need
# several drives to answer -- whether the agreement ratio is settling, whether the oncoming veto is
# rare or constant, what a genuinely quiet lane looks like.
# Raised from 20 on 2026-08-09 for a road trip at the end of the month. Twenty drives is a week of
# commuting and comfortably more than enough to answer a question about the usual roads; a trip is
# many short legs across road types this car has never driven, and the interesting ones would be
# pushed out by the ordinary ones before anybody looked. Each entry is well under a kilobyte.
DRIVE_HISTORY_MAX = 60

# --- a timeline, so a spoken report can be lined up with what actually happened ---
#
# "I will still dictate into my phone what is happening while I'm driving and what it did wrong and
# give that to you after each drive." That report is the best data this project gets, and it is
# ORDERED -- "first it did this, then I waited, then it did that". Everything stored until now was
# an aggregate, which throws the order away, so a narrative and the numbers could not be put side
# by side. "It kept saying would be changing right over and over" and "14 keep-right sequences
# between 12:03 and 12:06" are the same fact, and only one of them can be acted on.
#
# One entry per CHANGE, not per frame -- a steady state produces nothing at all. 300 covers a long
# commute with room to spare; a drive that overflows it was eventful enough that the tail is the
# interesting part, so the oldest go.
TIMELINE_MAX = 300
# In our lane, not an adjacent-lane return. Measured from the MODEL PATH, not from the car's
# straight-ahead axis, and computed here rather than read off radarState.
#
# This used to test lead.dPath, which is a dead field: nothing in openpilot has populated it since
# the LeadData struct was written, so it arrives as 0.0 and `abs(0.0) > 1.5` never rejected
# anything. The gate read as a filter and was not one. Found while checking how twilsonco's fork
# groups radar points into lanes -- its get_path_adjacent_leads uses dPath the same way, on an
# older openpilot where it was still real.
MAX_LEAD_D_PATH_M = 1.5

# The real knob, and the whole judgment: is that car slower than the speed I asked for. The
# question is "how far below my set speed", not "is it dramatically slower" -- which is why this is
# 4 and not the 8 it started at.
#
# Not 2, though, and the reason is measurement rather than taste: ordinary traffic varies by a mph
# or two. A driver holding 65 oscillates, another car's cruise hunts on grades, and vLead is a
# filtered estimate. At 2 mph the threshold sits inside that band, so it fires on cars that are not
# actually slower -- just momentarily varying. Four clears the noise and still catches the case
# this exists for.
#
# speedDeficit is logged on every decision, so this can be refitted from a drive instead of argued
# about.
DEFAULT_MIN_DEFICIT_MPH = 4
# How long the slower lead must persist before committing. Short by design: waiting is the whole
# behavior this exists to remove. Long enough only to reject a single bad frame of lead tracking.
#
# ONE SECOND, DOWN FROM TWO, and the reason is that this constant was carrying work it no longer
# does. When it was set, it was the ONLY defense against a flickering lead -- so it had to be long
# enough to outlast the flicker. It is not any more: the deficit hysteresis, the range hysteresis,
# the grace window and the decay rate below all exist now, and each rejects the exact noise this
# was padded against. Two seconds was insurance being paid twice.
#
# What it costs is the thing the whole feature is about. The owner: "Can we not confirm a slower
# car in 1 second while the blinker is on and then make a lane change?" Yes -- because the
# confirmation and the blinker lead run CONCURRENTLY, not one after the other, so at 1 s each the
# total wait is one second rather than two. The blinker goes on the moment a slow car is spotted
# with a clear lane, and the crossing begins when both clocks are satisfied.
#
# Twenty frames of consistent evidence at 20 Hz. A single bad frame is 0.05 s, so this is still
# forty times the thing it was written to reject.
DEFAULT_PERSISTENCE_S = 1

# --- and why that timer is no longer destroyed by one bad frame ---
#
# Both of these come from the same report: "when a car is going in between the speed I want to pass
# at and the speed I don't. Same with it coming in and out of radar range."
#
# That was raised as a cosmetic complaint about the display, and half of it was. The other half was
# a real refusal to pass. Every failing frame used to zero the confirmation timer outright, so a
# vehicle sitting exactly on the threshold -- or blinking at the edge of radar range -- reset the
# clock faster than it could ever run, and NEVER produced a suggestion. Not late: never. The one
# case a passing aid most obviously exists for is a car only slightly slower than you.
#
# HYSTERESIS fixes the speed boundary. Once a vehicle has been judged slow enough to be worth
# passing it stays judged slow until it is a clear margin FASTER than the threshold, so a car
# hovering on the line is decided once instead of re-decided every frame. 1 mph is chosen against
# the same measurement noise that set the 4 mph threshold: ordinary traffic varies by about that
# much, so this is the width of the noise, not a second judgment.
DEFICIT_HYSTERESIS_MPH = 1

# HOW HARD HE IS TRYING TO GET SOMEWHERE, asked for directly:
#
#   "could we derive aggression from how far over the speed limit I am manually going or have a
#    hold set on my cruise (from ICBM branch)?"
#
# Both halves of that arrive already combined. _reference_speed resolves the driver's intent from
# the ICBM hold first, the dash second, SLA's target last -- so "have a hold set" is not a separate
# input, it is the case where reference_speed comes back labeled icbmHold. The only thing missing
# was the posted limit to measure it against, which the planner now passes.
#
# THE SCALE ONLY EVER ADDS PATIENCE, NEVER REMOVES IT, and that direction is the whole design:
#
#   - 8+ mph over the limit is the UNCHANGED behavior, not the aggressive one. His settings mean
#     what they say and this cannot make the system pass more than he configured.
#   - at or under the limit the required speed gain is multiplied up, so only a clearly slower car
#     is worth the maneuver. "It does want to pass more often than I want" is the report this
#     answers, and answering it by making some other case rarer is the only safe direction.
#   - no posted limit means scale 1.0. Unknown is not evidence, and on his roads it is common --
#     one drive had a limit on 1.7% of frames.
#
# It scales the DEFICIT and nothing else. Not the curve gate, which is a steering-authority limit
# rather than a preference -- the PSCM does not care how late he is. Not the blind spot, the
# oncoming veto, the approach distance or the confirmation: "evidence that opens a maneuver must
# never be cheaper than evidence that refuses one" is the rule this could most easily break.
DEFAULT_PATIENCE = 18            # tenths of a multiplier. 10 disables it.
PATIENCE_FULL_EXCESS_MPH = 8.0   # at or above this far over the limit, no extra patience at all

# A GRACE WINDOW then decay fixes the dropout, and the grace window is the part that matters.
#
# Decay alone was not enough, which is worth spelling out because it looked like it was. Decaying
# at 3x while accumulating at 1x means a track has to be present 75% of frames just to break even;
# below that the timer NEVER reaches the threshold however long the car sits there. That is exactly
# the reported failure -- "it would go in and out of range and so the pass would keep resetting" --
# so a fix that only slowed the resetting down had not fixed it at all.
#
# So a short gap now costs NOTHING. liveTracks arrives at ~8.3 Hz and the detector wants 3
# consecutive messages to re-confirm a vehicle, so 0.4 s covers a lost return and its recovery with
# margin. Only absence beyond that decays, and then quickly: from the 2 s cap, ~0.7 s of continued
# silence clears it, which is what a car genuinely leaving looks like.
LEAD_GAP_GRACE_S = 0.4
CONFIRM_DECAY_RATE = 3.0

# The same problem at the range boundary rather than the speed one. A lead sitting either side of
# the look-ahead distance would alternate in and out; once it is being tracked it may drift this
# much further out before it counts as gone.
RANGE_HYSTERESIS_M = 20.0

# Deliberately NOT applied to the deficit test: a lead that is genuinely faster than the release
# threshold is a changed situation, not a noisy one, and should reset properly.

# --- the one question ---
# "Is there a vehicle in my lane slow enough to cost me speed?"
#
# There is no second question. Closing on a slower car and sitting behind one are the same
# situation at two moments: either we are about to brake for it or we already have. Splitting them
# produced a machine that waited in one branch for a condition the driver never lets happen.
#
# On stock Ford ACC the cost is concrete: ACC brakes for a lead we were always going to pass, then
# fuel is spent winning the speed back in the other lane. Deciding early avoids both halves, and
# whether a given suggestion actually beat ACC to it is recorded rather than assumed -- see
# accBrakingAtDecision, which is what `trigger` now reports.
#
# There is no time-based bound. TTC was one, and it was actively backwards: at a small speed
# difference the closing rate is low, so a fixed TTC translates into a SHORT distance. Three mph
# under closes at 1.3 m/s, which a 60 s bound turns into about 80 m -- the gentler the difference,
# the later it would notice, which is the opposite of deciding early.
#
# Distance is the honest limit, and it means what it says: how far ahead to look. Beyond the reach
# of lead tracking there is nothing to decide on anyway.
DEFAULT_MAX_DISTANCE_M = 220

# --- how LATE to pull out ---
#
# The owner: "I would like to get as close to the car as I can before making the lane change, as
# long as Ford ACC brakes the least amount."
#
# Those two pull opposite ways and the resolution is the escape valve below, not the number. Left
# alone, this system notices a slower car at the look-ahead distance and moves over immediately,
# which is not how anyone drives -- a person closes on the car and THEN pulls out. So this holds
# the maneuver until the lead is within this distance.
#
# WITH ONE OVERRIDE THAT MATTERS MORE THAN THE NUMBER: the hold is abandoned the instant Ford's ACC
# asks for any deceleration, at any distance. That is what makes "as close as possible" safe to
# ask for -- get it too aggressive and the failure is not a late pass, it is ACC braking, and the
# system goes the moment that starts. It self-corrects toward the latest distance that still costs
# nothing.
#
# DEFAULT 0 = OFF, and deliberately so: the right value is a little beyond where ACC actually
# starts braking, and nobody knows that number yet. accBrakingOnsetDRel is being logged to measure
# it. Guessing a default here would be picking the one number this feature is most sensitive to,
# blind, when a drive can just tell us.
DEFAULT_MIN_APPROACH_M = -1     # Auto

# AUTO: hold off until the lead is this far past where Ford's ACC has actually been measured to
# start braking. The setting had no safe default before because that distance was unknown and
# guessing it wrong means braking, which is the one thing this is all for. It is not unknown any
# more -- accBrakingOnsetMax measures it every drive -- so the number can come from the car.
#
# The first real reading was 449 ft, about 137 m, which is a good deal earlier than assumed. That
# is worth stating plainly: it means there is far less room to close in than hoped, and Auto will
# hold at roughly 155 m rather than the 60 or 70 m that "as close as I can" evokes. Better to be
# told that by the car than to pick a tighter number and pay for it in braking.
#
# Self-correcting in the safe direction. If ACC ever loses patience earlier than it has before,
# the measured max grows and the hold relaxes to match, so the error can only shrink.
AUTO_APPROACH_MARGIN_M = 20.0

# Lateral acceleration above which the road is bending too hard to be passing on it, m/s^2.
#
# THE MEASURE-FIRST PHASE IS OVER. _track_curve has recorded this on every drive without gating
# anything, deliberately -- "the distribution says where a threshold belongs, which is the opposite
# of picking one and finding out afterwards". The distribution now exists, and so does the report
# that prompted it: driving Parley's to Deer Valley, "I don't want to pass on curves".
#
# Suggestion frames by bucket on the 2026-08-15 pair, against edges 0.5 / 1.0 / 1.3 / 1.6:
#
#   night  [2156, 238, 197, 218, 395]   19% above 1.3, 12% above 1.6
#   day    [1150,  99,  59,  35,  36]    5% above 1.3
#
# 1.3 is not invented here: it is vision_controller's own ENTERING-A-TURN threshold, and upstream
# has far more road behind that number than this fork could gather. "Entering a turn" is precisely
# the moment he objects to being offered a pass, so the two agree about what the number means.
#
# Gates the SUGGESTION, not a crossing already underway -- a car cannot un-change lanes because the
# road started bending, and pulling out of a committed pass mid-corner would be worse than either.
DEFAULT_MAX_PASS_LAT_ACC = 1.3

# Hysteresis on wanted_side, which is what lights the blinker.
#
# 126 ABORTS IN 37 MINUTES, measured on the day highway drive 2026-08-15. An abort is a signal shown
# to the traffic behind and then withdrawn, and that many is disqualifying on its own -- it would be
# 126 blinker flashes that went nowhere, whatever the sensors were doing.
#
# The cause is not a gate being wrong, it is a gate being UNSTEADY. wanted_side is recomputed every
# frame from the camera geometry with nothing holding it still, and the road-edge term failed 85% of
# that drive -- meaning it passed the other 15%, in flickers. Each flicker lit the signal and
# dropped it.
#
# THE RISE DELAY IS THE UNCOMFORTABLE HALF, because it argues with a stated preference: "It should
# come on instantly telling drivers I want to change lanes." 0.3 s is six frames, below what anyone
# perceives against a 1 s blinker lead, and it is the difference between signalling a decision and
# signalling a sensor artefact. A promise made 126 times and broken 126 times is not instant, it is
# noise.
WANTED_RISE_S = 0.3

# The fall side matters more and costs nothing. A single dropped frame of geometry should not
# retract a signal already shown -- that is the abort. Longer than the rise deliberately: entering
# the state should be harder than staying in it, or the hysteresis has no direction.
WANTED_FALL_S = 0.75

# The follow gap to ask ICBM for while pursuing a pass. Time_Gap_1..5; 1 is the closest.
#
# NOT a driving style. The point is headroom: at his own 3 of 5 the car begins braking so far back
# that the decision is forced ridiculously early, and closing that gap moves ACC's onset in so the
# pass can be made where a person would make it. ICBM restores his setting the moment we stop
# asking -- see the lease note in longitudinal_planner.
GAP_WHILE_PASSING = 1

# ...and stop asking if the pass is not happening. A slow car on a road where passing is never
# possible would otherwise be trailed at gap 1 indefinitely, which is closer than he chose to drive
# for no benefit at all. Long enough not to trip on the gate flicker the abort counter measures.
GAP_GIVE_UP_S = 10.0

# --- do not go round a car that is braking hard ---
#
# People do not pass a braking car, and the instinct is a good one: hard deceleration usually means
# they are turning off -- in which case the pass was never needed -- or they are braking for
# something ahead that we cannot see yet, in which case going round them is the last thing to do.
# Neither is visible to any sensor on this car. The braking itself is, and it stands in for both.
#
# SLAMMING ON, not merely slowing. Retuned on the owner's correction: "we can pass a car that is
# slowing down a little, just not if they are slamming on their brakes."
#
# He is right, and the first number was timid. A car shedding speed gently is the single best
# reason to go round it -- it is about to cost you more, not less. Holding off there would have the
# system back out of exactly the passes it exists to make.
#
# For scale: coasting is about -0.3, lifting off for a bend -0.5 to -1, ordinary traffic braking
# around -2. -3.0 is a deliberate stop -- the driver has decided something, and whatever it is we
# cannot see it. THAT is worth waiting two seconds for; nothing softer is.
LEAD_BRAKING_MS2 = -3.0
# Held briefly after they stop rather than released the instant the number crosses back: someone
# stopping hard modulates the pedal, and the gap between two stabs is not an invitation. Short,
# because at this threshold the event is rare and being timid about it costs real passes.
LEAD_BRAKING_HOLD_S = 1.5
# Kept only for the log -- how long until we reach this lead at the current closing rate. Nothing
# gates on it.
MIN_APPROACH_CLOSING_MS = 1.0
NO_TTC_S = 999.0

# --- what counts as "Ford ACC is already paying for this lead" ---
# Kept in sync with hud_renderer_bp.py, which derived these while building the ACC pill. Duplicated
# rather than imported because that is UI and this is controls; if one changes, change both.
#
# AccPrpl_A_Rq's floor is a "no request" sentinel, not a -5 m/s^2 demand -- opendbc sends
# INACTIVE_GAS = -5.0 whenever longitudinal is off or the request falls below MIN_GAS. Anything at
# or below this carries no information.
ACC_PROPULSION_INACTIVE = -4.5   # m/s^2
# Below this the propulsion request is real engine braking rather than trim around zero.
ACC_ENGINE_BRAKE_MS2 = -0.15

# --- anti-weave ---
# After a pass is suggested, hold off suggesting the return for this long. Without it, a three-lane
# road with a slow left lane produces exactly the ping-ponging that makes a system feel unfinished:
# move left, find it no faster, get told to move right, repeat. A settle period does not need to
# know what the adjacent lane is doing -- it just refuses to reverse a decision it only just made.
DEFAULT_SETTLE_TIME_S = 20


# How long behind a leftmost-lane straggler before it counts as one. Everybody slows for a moment;
# a car genuinely camped in the passing lane is there for a while, and without this the count would
# be dominated by ordinary bunching and say nothing.
HOG_MIN_S = 8.0

# --- holding a suggestion through a one-frame dip ---
#
# MEASURED on the 2026-08-09 freeway drive, and the number that forced this: 59 suggestion episodes
# against 62.7 s of wanting a pass, with a MEDIAN LENGTH OF 0.10 s -- two frames. 80 % lasted under
# half a second and the whole drive contained 28 s of suggestion. It was not flickering on screen;
# it never appeared long enough to be seen at all, which is why he took none of the 59.
#
# NO SINGLE TERM IS AT FAULT, which is the finding that decided the shape of this. Of the 59 deaths:
# a non-geometry gate 42 %, paint 31 %, width 14 %, road edge 8 %. Four independent signals each
# sitting near their threshold, and any one of them dipping for a single frame took the whole
# suggestion away. Stabilising the worst of them would have left two thirds of it.
#
# So the hysteresis goes on the OUTPUT, where every one of those paths converges: once a suggestion
# is up, a gate has to stay unhappy for this long before it is withdrawn. Appearing is unchanged --
# the confirmation window already governs that, and this must not make it quicker to suggest.
#
# 0.6 s is four times the longest observed dip and still far shorter than the shortest episode
# anyone could act on.
SUGGESTION_HOLD_S = 0.6

# AN ALLOW LIST, NOT AN EXEMPT LIST, and the first draft got this backwards. Exempting the safety
# gates left everything else holdable, which included the DRIVER TAKING OVER and the feature being
# switched off at the LKA button -- two tests caught it immediately, and both were right.
#
# The deeper problem with an exempt list is that it fails open: any gate added later is held through
# by default, silently, and whoever adds it never sees this file. Naming what MAY be held through
# fails closed instead, which is the same rule the rest of the module runs on -- evidence that keeps
# a maneuver alive must never be cheaper than evidence that ends it.
#
# These three, and only these three, are exactly the ones measured oscillating on 2026-08-09:
# noLaneAvailable 61 % of the deaths, adjacentSlow 24 %, nothingSlower 15 %. Every one of them says
# the pass is POINTLESS -- there is nowhere to go, the lane is no faster, the car ahead is not
# actually slow. None of them says it is DANGEROUS. A pointless suggestion held for another half
# second costs nothing; anything else withdrawing late could cost a great deal.
HOLD_THROUGH = (Blocked.noLaneAvailable, Blocked.adjacentSlow, Blocked.nothingSlower)

# --- keep right ---
# "Keep right except to pass" is the mirror of the passing question: nothing is holding us back and
# a lane exists to our right, so we should not be sitting out here. Deliberately slower to fire
# than the pass suggestion -- returning right is never urgent, and a short delay would nag on every
# brief gap in traffic while genuinely overtaking a line of cars.
DEFAULT_KEEP_RIGHT_DELAY_S = 10

# How long the lane to our right must have existed CONTINUOUSLY before moving into it is suggested.
#
# The owner's idea, and it is a better exit test than the two already here: an exit lane did not
# exist a moment ago and now does. A through lane has been beside us the whole time. So instead of
# asking what the lane looks like -- which is what MIN_LANE_WIDTH_M and the road-widening check do,
# and why they cannot tell an exit from a through lane -- ask how long it has been there.
#
# Complementary rather than a replacement. Road widening spots an exit OPENING UP AHEAD; this spots
# one that has JUST APPEARED beside us. They catch the same thing at different moments and both
# fail safe.
#
# Every way this is wrong is the harmless way. A lane the model briefly loses -- occluded by a
# lorry, faded paint, a shadow -- comes back looking new, and the cost is a quiet keep-right for a
# few seconds. Merging onto a highway also starts the clock late, which is correct: the lane really
# is new to us there.
#
# 15 s at 70 mph is about 470 m of continuous presence. An exit lane is rarely that long before the
# gore; a through lane has usually been there for minutes.
DEFAULT_MIN_LANE_AGE_S = 15

# TsrOvtkMsgTxt_D_Rq. 0 Null, 1 OvertakingAllowed, 2-7 are all "Lim*" -- a limitation in force or
# its explicit cancellation. Only the cancel codes clear the zone; the rest mean restricted.
TSR_OVTK_CANCELLED = (4, 7)       # LimAllCancelled, LimForTrucksCancelled
TSR_OVTK_UNRESTRICTED = (0, 1) + TSR_OVTK_CANCELLED
# TsrOvtkStatMsgTxt_D_Rq. 2 = LimitReliable (the DBC spells it "LimitReiable"). Anything else is
# Null, LimitChanged or LimitOutdated -- not a basis for a veto.
TSR_OVTK_STATUS_RELIABLE = 2


class PassingAssistDetector:
  def __init__(self):
    self.suggestion = Side.none
    self.blocked_by = Blocked.disabled
    self.reason = Reason.none
    self.approach_seconds = 0.0
    self.keep_right_seconds = 0.0
    # Latched by the hysteresis above: is the lead currently judged slow enough to be worth
    # passing. Latched rather than recomputed so the answer cannot chatter frame to frame.
    self.lead_is_slow = False
    self._lead_gap_s = 0.0
    # The side that is clear RIGHT NOW, before the confirmation timer has anything to say about it.
    # `suggestion` is what commits to moving.
    self.clear_side = Side.none
    # WEAKER THAN clear_side, and it is what lights the blinker. A slow car worth passing and a lane
    # that exists on that side -- nothing yet about whether entering it is safe. See SIGNAL_WINDOW_S.
    self.wanted_side = Side.none
    # See WANTED_RISE_S. The raw per-frame answer, and how long it has held.
    self._wanted_raw = Side.none
    self._wanted_held_s = 0.0

    self.has_lead = False
    self.lead_d_rel = 0.0
    self.lead_v_lead = 0.0
    self.speed_deficit = 0.0
    # See leadRadarConfirmed in custom.capnp. Recorded, not gated on.
    self.lead_radar_confirmed = False
    self.lead_model_prob = 0.0
    self.lead_ttc = 0.0
    self.lead_d_path = 0.0
    self.trigger = Trigger.none
    self.acc_braking_at_decision = False
    self.acc_precharge_at_decision = False
    self.acc_braking_available = False
    # See accBrakingOnsetDRel in custom.capnp -- the margin this whole design assumes, measured
    # rather than estimated. 0 means ACC never asked for deceleration during this approach.
    self.acc_onset_d_rel = 0.0
    # Drive-level: the earliest ACC has ever started. See accBrakingOnsetMax.
    self.acc_onset_max = 0.0
    # Seconds per blocked reason, counted only while a pass was actually wanted. See wantedSeconds.
    self._block_seconds: dict[int, float] = {}
    self.wanted_seconds = 0.0
    # HOW BENT THE ROAD WAS WHEN IT SUGGESTED. Measurement only -- nothing gates on it. See
    # _track_curve: the concern is his retrofit PSCM being asked to add a lane change on top of a
    # curve it is already working to hold, and the number that would answer it does not exist yet.
    self.lat_acc = 0.0
    self.max_pass_lat_acc = DEFAULT_MAX_PASS_LAT_ACC
    self.suggested_lat_acc_max = 0.0
    self._lat_acc_hist = [0] * 5
    self._hold_s = 0.0        # see SUGGESTION_HOLD_S
    self._held_side = Side.none
    self._held_reason = Reason.none
    self._held_trigger = Trigger.none
    # LEFT LANE HOGS, asked for by name 2026-08-09 after a less printable list of alternatives.
    # See _track_lane_hog for what counts as one and, more importantly, what does not.
    self.hog_seconds = 0.0
    self.hog_count = 0
    self._hog_held_s = 0.0
    self._hog_counted = False
    self.elapsed_s = 0.0
    self._timeline: list = []
    self._timeline_prev: tuple = ()
    self._last_drive_write_s = 0.0
    self._last_oncoming = (0.0, 0.0, 0.0, False)
    # See oncomingSeenSeconds -- how much of the oncoming veto is a live sighting and how much is
    # the tail of its memory.
    self.oncoming_seen_seconds = 0.0
    self.oncoming_remembered_seconds = 0.0
    # Does anything here command the blinker yet? No -- and until it does, _driver_override must
    # behave exactly as the plain test it replaced, because in phase 1 every blinker on the car
    # IS the driver's. One flag, flipped when the lamp is actually wired, and the fix is already
    # in place and tested rather than remembered.
    # See PassingAssistActuate. The SWITCH; may_actuate() is the gate that actually decides.
    self.actuate_enabled = True
    self.actuating = False
    # See overtakenSeconds in custom.capnp -- the longest either lane has gone without anyone
    # passing us. Tracked at drive level because the per-side clock resets on every overtake, so the
    # live value can never say how quiet the road got.
    self.overtaken_quietest_s = 0.0
    self.reference_speed = 0.0
    self.reference_source = RefSource.cluster

    self.left_line_prob = 0.0
    self.right_line_prob = 0.0
    self.left_edge_gap = 0.0
    self.right_edge_gap = 0.0
    self.left_lane_width = 0.0
    self.right_lane_width = 0.0
    self.left_edge_beyond = 0.0
    self.right_edge_beyond = 0.0
    self.left_edge_std = 1e3
    self.right_edge_std = 1e3
    self.left_geometry_ok = False
    # Which geometry term refuses the LEFT side, counted over the whole drive. See _record_refusal.
    self._geo_refusals = [0, 0, 0, 0]
    # HOW OFTEN EACH TERM FAILS ON ITS OWN, independent of which one got the blame.
    #
    # _geo_refusals is an if/elif chain, so it records the FIRST failing term and says nothing about
    # the others -- its own comment admits it "names A binding term, not THE one". That is the exact
    # question blocking any fix: when paint refuses at 0.011, would the road edge have said yes?
    #
    #   fail TOGETHER  -> nothing in the current sensor set fixes this, and no amount of gate
    #                     restructuring helps. Stop looking here.
    #   fail ALTERNATELY -> requiring BOTH is what costs three quarters of the drive, and the gate
    #                     is the problem rather than the camera.
    #
    # Counted only on frames where a pass was actually wanted, same as the tally above, or an empty
    # road dominates it.
    self._geo_term_fails = [0, 0, 0, 0]
    self._geo_frames = 0
    # THE ROAD EDGE, BY SPEED BAND -- the one discriminator left that needs no new hardware.
    #
    # Threshold tuning is finished as an avenue: the edge std measured 3.9, 4.9, 15.1 and 16.3
    # across four drives against a limit of 1.2, and no single value admits any two of them. But the
    # thing the edge term protects against -- a painted median, a left-turn pocket -- IS AN ARTERIAL
    # FEATURE. Those do not exist at 75 mph. At freeway speed the failure mode is an oncoming lane,
    # which the radar veto answers.
    #
    # So the question worth one drive: is the edge untrustworthy EVERYWHERE, or only where the
    # median risk lives? If it is fine above 65 mph then the California run works untouched. If it
    # is equally bad at every speed, relaxing it at speed becomes a real choice with a real risk,
    # and this is the number that says which.
    #
    # [<40, 40-55, 55-70, 70+] mph. Pairs of (frames where a pass was wanted, frames the edge
    # refused), so the answer is a rate rather than a count that road time can distort.
    self._edge_by_speed = [[0, 0], [0, 0], [0, 0], [0, 0]]
    self._geo_sums = [0.0, 0.0, 0.0, 0.0]
    # ...and the SPREAD, not just the mean. See geo_refusal_loosen_to.
    self._geo_hist = [[0] * 10 for _ in range(4)]
    # DOES A LEFT LANE EVEN EXIST? Pairs of (refused frames where the radar could answer, frames
    # where it saw a vehicle over there). See the note at the end of _record_refusal.
    # See _record_refusal: [radar could answer, something was there, something was TRAVELLING].
    self._geo_left_proof = [0, 0, 0]
    self.right_geometry_ok = False
    self.right_widening_m = 0.0
    self.right_widening = False
    self.right_lane_age_s = 0.0

    self.left_blindspot = False
    self.right_blindspot = False
    self.blindspot_available = False

    self.overtake_restricted = False
    self.overtake_msg = 0
    self.overtake_status = 0
    self.tsr_available = False
    self.road_name = ""
    self.rear = RearApproach()
    self.adjacent = AdjacentLane()

    self.params = Params()
    self.frame = 0
    self.enabled = True
    self.min_deficit_ms = DEFAULT_MIN_DEFICIT_MPH * CV.MPH_TO_MS
    # See DEFAULT_PATIENCE. The configured multiplier, the posted limit it is measured against,
    # what it currently works out to, and the threshold every gate actually reads.
    self.patience = DEFAULT_PATIENCE / 10.0
    self.posted_limit = 0.0
    self.patience_scale = 1.0
    self.min_deficit_active_ms = DEFAULT_MIN_DEFICIT_MPH * CV.MPH_TO_MS
    self.patience_refused_s = 0.0
    # ...and passes HE made that patience refused, which is the stronger version of the same
    # question: seconds are exposure, this is a maneuver he actually wanted.
    self.patience_missed = 0
    self.persistence_s = float(DEFAULT_PERSISTENCE_S)
    self.keep_right_enabled = True
    self.keep_right_delay_s = float(DEFAULT_KEEP_RIGHT_DELAY_S)
    self.min_lane_age_s = float(DEFAULT_MIN_LANE_AGE_S)
    self.adjacent_enabled = True
    self.oncoming_veto = True
    self.strict_two_way = True
    self.oncoming_memory_s = float(DEFAULT_ONCOMING_MEMORY_S)
    self.settle_time_s = float(DEFAULT_SETTLE_TIME_S)
    # Starts settled: at boot we have not just passed anyone, and a fresh detector must not
    # spend its first settle period refusing to suggest a return.
    self._settle_s = 1e3
    self._lka_prev = False
    self.max_distance_m = float(DEFAULT_MAX_DISTANCE_M)
    self.min_approach_setting = float(DEFAULT_MIN_APPROACH_M)
    self.min_approach_m = 0.0
    self._seeded_onset = False
    self.min_speed_ms = DEFAULT_MIN_SPEED_MPH * CV.MPH_TO_MS
    self.exit_standdown_s = float(DEFAULT_EXIT_STANDDOWN_S)
    self.driver_change_standdown = 0.0
    # Time SINCE the last one, as opposed to the stand-down counting down. See OvertakeProgress:
    # a lane change he just made is the evidence that a pass is underway, and it is the only such
    # evidence available while this system suggests nothing itself.
    self.since_driver_change_s = 1e3
    self.last_v_ego = 0.0
    self.driver_change_was_exit = False
    self._driver_blinker = None       # side currently being signaled, or None
    self._signalled_over_widening = False
    self._steer_held_s = 0.0
    # See EXIT_WATCH_S. Speed just before the driver's current maneuver began, and the watch it
    # arms afterwards.
    self._change_entry_v = 0.0
    self._exit_watch_s = 0.0
    self._exit_watch_v = 0.0
    # Which test caught each rightward driver change: widening, outermost, slowed after, none.
    # This is the measurement _moved_toward_an_exit's own note asks for -- "how often
    # driver_change_was_exit comes out false on a freeway drive with known exits" -- and it now
    # rides along with the drive instead of needing a separate tool and a separate drive.
    self._exits_by = [0, 0, 0, 0]
    self._build_sha: str | None = None   # see _build
    # See driverPasses in custom.capnp -- agreement with the driver, the readiness measure.
    self.driver_passes = 0
    self.driver_passes_agreed = 0
    self.driver_pass_lead_s = 0.0
    self._suggest_held_s = 0.0
    self._miss_reasons: dict[int, int] = {}
    # Passes made while the feature was actually ELIGIBLE to have an opinion. See
    # OFF_BY_DESIGN: the agreement score is meaningless measured against passes it was
    # switched off for, and 2 of 106 was being read as "it disagrees with him" when a large part of
    # it is "it was not running".
    self.driver_passes_eligible = 0
    # See missedDeficitMph -- what the deficit actually was on the passes the threshold rejected.
    self.missed_deficit_mph = 0.0
    self._missed_deficit_n = 0
    # See suggestionsMade in custom.capnp -- the other error direction.
    self.suggestions_made = 0
    self.suggestions_taken = 0
    # True for the single frame the chime should sound. See CHIME_SETTLE_S.
    self.suggestion_started = False
    # True for the single frame the lower "backed out" tone should sound. See
    # ABORT_CHIME_MIN_INTERVAL_S.
    self.abort_started = False
    self._since_abort_chime_s = 1e3
    self._aborts_seen = 0
    self._chime_held_s = 0.0
    self._since_chime_s = 1e3
    self.chime_enabled = True
    self.longest_ignored_s = 0.0
    self._episode_taken = False
    self._prev_suggesting = False
    # Totals carried in from previous drives. See lifetimeDrives -- this drive's counts are added
    # to these when published, never written back into them, so the 30 s save cannot double-count.
    self._life_drives = 0
    self._life_passes = 0
    self._life_agreed = 0
    self.closing_in = False
    # See gap_request. 0 asks for nothing; ICBM releases the lease on silence.
    self.gap_request = 0
    self._gap_blocked_s = 0.0
    self._gap_pursuing = False
    self.lead_accel = 0.0
    self.lead_braking_enabled = True
    self.lead_braking_hold = False
    self._lead_braking_s = 1e3
    # The dry run: what a fully-automatic pass would be doing right now. Actuates nothing.
    self.maneuver = PassingManeuver()
    # Keep-right's own dry run. A separate machine rather than a mode on the one above, so its
    # abort count stays its own -- that number is the readiness metric for each maneuver, and one
    # combined figure would say something is unstable without saying which.
    self.keep_right_maneuver = PassingManeuver()
    # Is a pass grinding? The one case that may ever earn the set-speed actuator. Measures only.
    self.overtake = OvertakeProgress()

  def update_params(self) -> None:
    if self.frame % int(PARAMS_UPDATE_PERIOD / DT_MDL) == 0:
      self.enabled = self.params.get_bool("PassingAssistLogEnabled")
      self.min_deficit_ms = self.params.get("PassingAssistMinDeficit", return_default=True) * CV.MPH_TO_MS
      # Tenths, like the curve gate above it. 18 -> 1.8x.
      self.patience = float(self.params.get("PassingAssistPatience", return_default=True)) / 10.0
      self.persistence_s = float(self.params.get("PassingAssistConfirmTime", return_default=True))
      self.keep_right_enabled = self.params.get_bool("PassingAssistKeepRight")
      self.keep_right_delay_s = float(self.params.get("PassingAssistKeepRightDelay", return_default=True))
      self.min_lane_age_s = float(self.params.get("PassingAssistMinLaneAge", return_default=True))
      # Tenths of m/s^2, because Params carries ints. 13 -> 1.3.
      self.max_pass_lat_acc = float(
        self.params.get("PassingAssistMaxCurve", return_default=True)) / 10.0
      self.actuate_enabled = bool(self.params.get_bool("PassingAssistActuate"))
      self.adjacent_enabled = self.params.get_bool("PassingAssistAdjacentLane")
      self.oncoming_veto = self.params.get_bool("PassingAssistOncomingVeto")
      self.strict_two_way = self.params.get_bool("PassingAssistStrictTwoWay")
      self.oncoming_memory_s = float(self.params.get("PassingAssistOncomingMemory", return_default=True))
      self.maneuver.blinker_lead_s = float(self.params.get("PassingAssistBlinkerLead", return_default=True))
      self.keep_right_maneuver.blinker_lead_s = self.maneuver.blinker_lead_s

      # The crossing takes as long as this car's own lane changes take -- measured from the
      # driver's nudgeless ones, because that is the exact maneuver passing assist would perform.
      # A guess only until there has been one.
      try:
        lc = self.params.get("LaneChangeStats")
        measured = float(lc.get("seconds", 0.0)) if lc else 0.0
        if measured > 0.5:
          self.maneuver.change_duration_s = measured
          self.keep_right_maneuver.change_duration_s = measured
      except Exception:  # noqa: BLE001 - a malformed param must not reach the planner
        pass
      self.min_approach_setting = float(self.params.get("PassingAssistMinApproach", return_default=True))

      # Carry the last drive's measurement across the ignition cycle. Without this, Auto is off for
      # the first part of every drive -- until ACC happens to brake once -- which is exactly the
      # part of a drive where it would have done the most good.
      if not self._seeded_onset:
        self._seeded_onset = True
        try:
          last = self.params.get("PassingAssistLastDrive")
          # Exactly once per ignition cycle, and `last` is by definition the PREVIOUS drive --
          # this one has written nothing yet. That makes this the natural place to archive it, and
          # it needs no shutdown hook, which is the thing this codebase does not have.
          self._archive_drive(last)
          if last:
            self.acc_onset_max = max(self.acc_onset_max, float(last.get("accOnsetMax", 0.0)))
            self._life_drives = int(last.get("lifetimeDrives", 0))
            self._life_passes = int(last.get("lifetimePasses", 0))
            self._life_agreed = int(last.get("lifetimeAgreed", 0))
        except Exception:  # noqa: BLE001 - a missing or malformed param must not reach the planner
          pass
      self.min_speed_ms = self.params.get("PassingAssistMinSpeed", return_default=True) * CV.MPH_TO_MS
      self.exit_standdown_s = float(self.params.get("PassingAssistExitStandDown", return_default=True))
      self.overtake.crawl_time_s = float(self.params.get("PassingAssistCrawlTime", return_default=True))
      self.lead_braking_enabled = self.params.get_bool("PassingAssistLeadBrakingHold")
      self.settle_time_s = float(self.params.get("PassingAssistSettleTime", return_default=True))
      self.max_distance_m = float(self.params.get("PassingAssistMaxDistance", return_default=True))
      self.chime_enabled = self.params.get_bool("PassingAssistChime")

  def _debounce_wanted(self, raw: int) -> int:
    """Hold wanted_side still. See WANTED_RISE_S -- this is what 126 aborts in 37 minutes bought.

    Asymmetric on purpose. Entering costs WANTED_RISE_S of agreement so a one-frame flicker in the
    camera geometry cannot light the signal; leaving costs WANTED_FALL_S so a one-frame dropout
    cannot retract one already shown. The second is the abort, and it is the longer of the two.

    A side CHANGE resets rather than crossing over, because left and right are different promises
    and sliding between them without passing through none would signal the wrong way for a frame.
    """
    if raw != self._wanted_raw:
      self._wanted_raw = raw
      self._wanted_held_s = 0.0
    else:
      self._wanted_held_s += DT_MDL

    if raw == self.wanted_side:
      return self.wanted_side
    if raw == Side.none:
      return Side.none if self._wanted_held_s >= WANTED_FALL_S else self.wanted_side
    return raw if self._wanted_held_s >= WANTED_RISE_S else self.wanted_side

  def _reset_outputs(self, blocked: int, keep_wanted: bool = False) -> None:
    self.clear_side = Side.none
    # AND wanted_side, or it keeps whatever a previous frame decided. Missing this let the passing
    # machine light its blinker during a KEEP-RIGHT: wanted_side is geometry alone, so a stale value
    # survived every early return that means "no pass is warranted here" -- no lead, nothing slower,
    # too slow, driver active. Caught by the drive scenario asserting the passing machine stays out
    # of a keep-right, which is exactly the signal-for-no-reason failure the whole design forbids.
    # keep_wanted: a GATE refusing is not the same as no pass being warranted. Geometry wobbling
    # is exactly what the debounce exists to ride out, and hard-clearing here would defeat it --
    # which it did, until a test caught it. Every OTHER caller means "no pass here at all" (no
    # lead, nothing slower, too slow, driver active) and those must clear at once, without waiting
    # out WANTED_FALL_S.
    if not keep_wanted:
      self.wanted_side = Side.none
      self._wanted_raw = Side.none
      self._wanted_held_s = 0.0
    self.suggestion = Side.none
    self.blocked_by = blocked
    self.reason = Reason.none
    self.trigger = Trigger.none

  @staticmethod
  def _edge_gap(model, line_idx: int, edge_idx: int) -> float:
    """Drivable width between ego's lane line and the road edge on that side, in meters.

    Returned as a positive magnitude on both sides so the two are directly comparable. Uses y[0],
    the nearest point, because that is where the measurement is most reliable and because a lane
    that exists beside us now is what matters -- not one 50 m ahead.
    """
    line_y = PassingAssistDetector._near_y(model.laneLines, line_idx)
    edge_y = PassingAssistDetector._near_y(model.roadEdges, edge_idx)
    if line_y is None or edge_y is None:
      return 0.0
    return abs(edge_y - line_y)

  @staticmethod
  def _near_y(series, idx: int) -> float | None:
    """y of the nearest point of one modelV2 polyline, or None if it is not there.

    None rather than 0.0 because 0.0 is a legal position -- straight under the car -- and every
    caller here is measuring a distance BETWEEN two of these. A missing line that reads as 0.0
    silently becomes a several-meter gap to whatever it is subtracted from.
    """
    try:
      return float(series[idx].y[0])
    except (IndexError, AttributeError, TypeError):
      return None

  @staticmethod
  def _lane_and_beyond(model, near_line: int, far_line: int, edge_idx: int,
                       sign: float) -> tuple[float, float]:
    """Width of the lane beyond ego's own, and how much road is left past it. Meters.

    y runs negative to the left, so `sign` is -1 on the left and +1 on the right; both come back
    positive, and a far line INSIDE ego's own line or a road edge INSIDE the far line comes back
    negative, which is a refusal rather than an absolute value that hides the disagreement.
    """
    near_y = PassingAssistDetector._near_y(model.laneLines, near_line)
    far_y = PassingAssistDetector._near_y(model.laneLines, far_line)
    edge_y = PassingAssistDetector._near_y(model.roadEdges, edge_idx)
    if near_y is None or far_y is None:
      return 0.0, 0.0
    width = sign * (far_y - near_y)
    beyond = 0.0 if edge_y is None else sign * (edge_y - far_y)
    return width, beyond

  def _road_widening(self, model, right_std: float) -> None:
    """Does the road open up to our right between here and ~75 m ahead?

    This is the cue a human uses to spot an off-ramp without reading the signs: a through lane runs
    parallel, an exit peels away. Measured as the growth in the gap between ego's right lane line
    and the right road edge, which cancels curvature -- both bend together through a corner, so
    only a genuine divergence shows up.

    It also fires on on-ramps, rest areas and truck pullouts. That is correct rather than a false
    positive: none of them is somewhere to move over into.

    Reported even when the edge is untrusted, so a log can show whether the measurement or the
    threshold is what needs work.
    """
    self.right_widening_m = 0.0
    self.right_widening = False
    if right_std > MAX_ROAD_EDGE_STD:
      return
    try:
      line = model.laneLines[LL_RIGHT].y
      edge = model.roadEdges[RE_RIGHT].y
      if len(line) <= WIDEN_FAR_IDX or len(edge) <= WIDEN_FAR_IDX:
        return
      near = float(edge[WIDEN_NEAR_IDX]) - float(line[WIDEN_NEAR_IDX])
      far = float(edge[WIDEN_FAR_IDX]) - float(line[WIDEN_FAR_IDX])
    except (IndexError, AttributeError, TypeError):
      return

    # Only growth counts. The road narrowing ahead is a lane ending, which the availability test
    # already handles, and treating it as a divergence would double-count it.
    self.right_widening_m = max(0.0, far - near)
    self.right_widening = self.right_widening_m > MAX_WIDENING_M

  def _geometry(self, model) -> None:
    """Evaluate whether a lane exists either side, recording both evidence channels separately.

    They are NOT redundant and are deliberately not combined into one score:
      - lineProb asks "is there paint beyond my lane line" -- present on a multi-lane road, but
        equally present for the oncoming lane of a two-way road.
      - edgeGap asks "is there drivable width out to the road edge" -- collapses to a shoulder when
        we are already in the outermost lane, which is the case lineProb handles badly.
    Which one discriminates better, and whether either separates divided from two-way, is the
    open question this phase exists to answer.
    """
    probs = model.laneLineProbs
    stds = model.roadEdgeStds

    self.left_line_prob = float(probs[LL_FAR_LEFT]) if len(probs) > LL_FAR_LEFT else 0.0
    self.right_line_prob = float(probs[LL_FAR_RIGHT]) if len(probs) > LL_FAR_RIGHT else 0.0
    self.left_edge_gap = self._edge_gap(model, LL_LEFT, RE_LEFT)
    self.right_edge_gap = self._edge_gap(model, LL_RIGHT, RE_RIGHT)
    self.left_lane_width, self.left_edge_beyond = self._lane_and_beyond(model, LL_LEFT, LL_FAR_LEFT,
                                                                       RE_LEFT, -1.0)
    self.right_lane_width, self.right_edge_beyond = self._lane_and_beyond(model, LL_RIGHT,
                                                                         LL_FAR_RIGHT, RE_RIGHT, 1.0)

    left_std = float(stds[RE_LEFT]) if len(stds) > RE_LEFT else 1e3
    right_std = float(stds[RE_RIGHT]) if len(stds) > RE_RIGHT else 1e3
    # Kept so the panel can say WHICH of the three terms refused a side. All three are invented
    # numbers, and "No lane to move into" on a road with an obvious empty lane beside it is the
    # report they produced -- unanswerable, because none of the values that decided it were shown.
    self.left_edge_std, self.right_edge_std = left_std, right_std

    self._road_widening(model, right_std)

    # Both channels must agree before a side is called available. Requiring agreement is the
    # conservative reading and keeps phase 2 honest if this ever stops being log-only.
    # THE RADAR EVIDENCE PATH IS GONE, and it was mine. "A vehicle travelling our way in that lane
    # proves the lane exists" is true and was still the wrong thing to OR into this gate: it made a
    # side available with no geometry at all, and the road answered immediately -- "it just keeps
    # trying to go into the shoulder", from the rightmost lane, twice.
    #
    # The band is 2.0 to 5.5 m from the PATH, and both ends of that are approximate: the radar is
    # mounted off-center with no correction, its lateral estimate degrades with range, and
    # path_offset is a model output. Any of those can put a vehicle from our own lane or the next
    # one but one into the right-hand band, and then a shoulder reads as a lane.
    #
    # Geometry alone from here. If it refuses a real lane, the panel now names which of the three
    # terms did it and by how much -- that is a number to fix, where this was a gate with no floor.
    #
    # AND EDGE GAP IS NO LONGER IN THE GATE, for the reason written at MIN_LANE_WIDTH_M: it adds the
    # candidate lane to its shoulder and reports the sum, so from the outermost lane it reports a
    # shoulder and calls it drivable width. It is still published, because it is the number that
    # shows the sum is not the same as its parts, but the two quantities that mean something on
    # their own are the lane's own width and the road left past it.
    # A CAR DRIVING DOWN IT OUR WAY IS BETTER PROOF THAN A ROAD EDGE, so it may stand in for one.
    #
    # REVERTED 2026-08-09, the same evening it shipped. See TestTrafficMayNotStandInForTheRoadEdge.
    #
    # It let same-direction traffic waive both edge-derived terms, on a freeway measurement: the
    # edge was the ONLY objection on 2865 frames and the radar had already seen traffic going our
    # way in that lane on 94.1 % of them. The reasoning still looks right and the road disproved it
    # in two drives:
    #
    #     "It tried to change lanes into the center turn lane median thing 3 times!"
    #
    # What the measurement could not see is that it was taken on a road with no turn lane. A center
    # turn lane is painted like a travel lane, sized like one, and has cars moving down it our way
    # -- so every term that survived the waiver passes, and the waived ones were the only terms
    # that did not.
    #
    # THE STRUCTURAL FAULT, which matters more than the tuning: three protections cover this case
    # and all three were down at once.
    #
    #   blocks_oncoming case 1  needs opposing traffic seen IN that lane. A turn lane has turning
    #                           traffic, not opposing traffic. Never fires.
    #   blocks_oncoming case 2  the turn-lane guard proper -- "road is two-way and nothing has
    #                           driven down that lane our way, so assume turn lane". Needs the road
    #                           KNOWN two-way, and it is unblocked by the very same
    #                           same_direction_recent this waiver used.
    #   these two edge terms    the last one standing, and this waived it.
    #
    # Case 2 never engaged because adjacent_lane.MAX_ROAD_EDGE_STD is 0.5 while the limit here is
    # 1.2 -- a different constant of the same name. Both drives recorded oncomingEdgeTrusted false,
    # so _on_our_carriageway was in its narrowed 5.5 m fallback throughout, and on a 1 + TWLTL + 1
    # arterial the opposing traffic sits at 7.4 m. The detector could not see the traffic that
    # would have classified the road, so the guard that depends on that classification never armed.
    #
    # So the untrusted edge DEGRADED the veto and, with this waiver, also excused the refusal. One
    # missing measurement must not do both. Stated as the rule this module runs on: evidence that
    # OPENS a maneuver must never be cheaper than evidence that refuses one -- and a waiver keyed
    # to the same evidence that unblocks case 2 collapses two independent protections into one.
    #
    # The freeway problem it was solving is REAL and is now unsolved again: geoRefusedShare ran
    # 0.98-1.0 before the waiver. Whatever replaces it has to tell a freeway passing lane from an
    # arterial turn lane, which this did not, and must be validated on a road that HAS a turn lane.
    #
    # WHAT HAS CHANGED SINCE, checked 2026-08-16, because two thirds of the paragraph above is now
    # stale and reading it cost most of a session -- it describes protections as down that have
    # since been repaired, which makes the revert look more permanent than the evidence supports:
    #
    #   the 5.5 m band   FIXED. UNTRUSTED_EDGE_ONCOMING_M is 9.0 now, introduced specifically to
    #                    reach past a median to opposing traffic near 7.4 m -- the exact distance
    #                    quoted above as out of reach. Case 2 can arm on that arterial today.
    #   the discriminator ADDED. SAME_DIRECTION_MIN_FRACTION (0.6) means a car decelerating into a
    #                    turn lane no longer vouches for the lane it is about to stop in. That is
    #                    the "tell a freeway passing lane from an arterial turn lane" requirement,
    #                    and it is met -- in adjacent_lane, not here.
    #
    # SO ONE CRITERION IS LEFT, AND IT IS THE STRUCTURAL ONE, UNMOVED: a waiver keyed to
    # same-direction traffic still keys on the same evidence that unblocks case 2, so a single
    # sighting would remove two protections at once. Repairing case 2 does not fix that; it only
    # means the second protection is now capable of arming when the first is spent.
    #
    # AND THE MEASUREMENT THAT WOULD SETTLE IT DID NOT EXIST UNTIL NOW. geoLeftProven counted
    # `occupied`, which has no speed test in it, so the 25-37% quoted across four drives INCLUDES
    # cars slowing into turn lanes and cannot distinguish the good case from the one that caused
    # the revert. geoLeftTravelProven is the same share with the speed test applied; the gap
    # between them is the turn-lane exposure. Read both, on a road with a turn lane, before
    # proposing this again -- and note that neither number needs map data, which is the point:
    # this has to work where tileLoaded is false.
    left_edge_ok = (left_std <= MAX_ROAD_EDGE_STD and
                    self.left_edge_beyond >= MIN_EDGE_BEYOND_LINE_M)
    right_edge_ok = (right_std <= MAX_ROAD_EDGE_STD and
                     self.right_edge_beyond >= MIN_EDGE_BEYOND_LINE_M)

    self.left_geometry_ok = (self.left_line_prob >= MIN_ADJACENT_LINE_PROB and
                             MIN_LANE_WIDTH_M <= self.left_lane_width <= MAX_LANE_WIDTH_M and
                             left_edge_ok)
    self.right_geometry_ok = (self.right_line_prob >= MIN_ADJACENT_LINE_PROB and
                              MIN_LANE_WIDTH_M <= self.right_lane_width <= MAX_LANE_WIDTH_M and
                              right_edge_ok)

    # How long that lane has been there WITHOUT INTERRUPTION. See DEFAULT_MIN_LANE_AGE_S: a lane
    # that did not exist a moment ago and now does is an exit or an on-ramp, and this is the only
    # test here that can tell that apart from a through lane -- every other one asks what the lane
    # looks like, and they look identical.
    if self.right_geometry_ok:
      self.right_lane_age_s = min(self.right_lane_age_s + DT_MDL, 1e3)
    else:
      self.right_lane_age_s = 0.0

  # The four terms of the left-hand gate, in the order it evaluates them. Index is what gets
  # published; the panel turns it back into a word.
  GEO_EDGE_STD, GEO_PAINT, GEO_WIDTH, GEO_BEYOND = 0, 1, 2, 3
  # Plausible full range of each term, for bucketing. Not the threshold -- the range the measurement
  # itself lives in, so a bucket means the same thing whatever the threshold is set to today.
  # Histogram range per term. Edge-std was 2.0 and SATURATED: a drive on 2026-08-06 averaged 6.44,
  # so every refusal landed in the top bucket and the percentile could only ever answer "the top of
  # the range". 8.0 covers what the model actually publishes. A value past the span is still
  # possible and the report says so rather than quoting the ceiling as a recommendation.
  GEO_SPAN = (8.0, 1.0, 8.0, 4.0)

  def _record_refusal(self) -> None:
    """Tally which term refuses the LEFT side, and its value, across the drive.

    WHY THIS IS AGGREGATED RATHER THAN SHOWN LIVE. The per-side reason is already on the panel, and
    asking him to read "L paint 0.31" off a screen at 70 mph earned exactly the answer it deserved:
    "and you expect me to read all of that while driving?" He does not read the panel live and has
    said so repeatedly -- he reads the summary at a stop and reports it back.

    So the diagnosis has to survive to the end of the drive as one sentence. Five drives, twenty-one
    passes, zero suggestions, and no idea which of four numbers is responsible is the situation this
    exists to end -- in one drive, without him reading anything at speed.

    LEFT ONLY. It is the side a pass is made to; the right side is legitimately a shoulder most of
    the time he is driving, so mixing them in would bury the signal in the expected answer.
    """
    if self.left_geometry_ok:
      return
    # First failing term. NOT the gate's own order, which is paint, width, beyond, edge-std -- this
    # checks edge-std FIRST, and the difference decides what gets reported whenever more than one
    # term fails at once.
    #
    # Deliberate, because these are not peers: left_edge_beyond is measured FROM the road edge, so
    # an edge the model does not trust makes the term derived from it meaningless rather than
    # merely false. Reporting "beyond" when the edge underneath it is unreliable would send him to
    # tune the wrong constant.
    #
    # But it does mean the tally OVER-ATTRIBUTES to edge-std relative to a strict reading of the
    # gate, and the 2026-08-07 drive is exactly the case where that matters: every suggestion was
    # marginal on paint (0.52-0.59) AND edge-std (1.00-1.16) at the same time, so each of those
    # refusals could as fairly have been called paint. Worth remembering before the next threshold
    # moves on the strength of this number alone -- it names A binding term, not THE one.
    if self.left_edge_std > MAX_ROAD_EDGE_STD:
      idx, val = self.GEO_EDGE_STD, self.left_edge_std
    elif self.left_line_prob < MIN_ADJACENT_LINE_PROB:
      idx, val = self.GEO_PAINT, self.left_line_prob
    elif not (MIN_LANE_WIDTH_M <= self.left_lane_width <= MAX_LANE_WIDTH_M):
      idx, val = self.GEO_WIDTH, self.left_lane_width
    else:
      idx, val = self.GEO_BEYOND, self.left_edge_beyond
    self._geo_refusals[idx] += 1
    self._geo_sums[idx] += val

    # ...and independently, which is the part the chain above cannot answer. See _geo_term_fails.
    # See _edge_by_speed. Banded on the frame's own speed, not the drive's average.
    mph = float(self.last_v_ego) * CV.MS_TO_MPH
    band = 0 if mph < 40 else 1 if mph < 55 else 2 if mph < 70 else 3
    self._edge_by_speed[band][0] += 1
    if self.left_edge_std > MAX_ROAD_EDGE_STD:
      self._edge_by_speed[band][1] += 1

    self._geo_frames += 1
    for i, failed in enumerate((
        self.left_edge_std > MAX_ROAD_EDGE_STD,
        self.left_line_prob < MIN_ADJACENT_LINE_PROB,
        not (MIN_LANE_WIDTH_M <= self.left_lane_width <= MAX_LANE_WIDTH_M),
        self.left_edge_beyond < MIN_EDGE_BEYOND_LINE_M)):
      if failed:
        self._geo_term_fails[i] += 1
    # Ten buckets across the term's plausible range. A mean says "paint averaged 0.31" and cannot
    # answer the only question worth asking -- what would I have to set it to. Half the refusals
    # could be at 0.45 and half at 0.17, and 0.30 would fix neither cleanly.
    span = self.GEO_SPAN[idx]
    self._geo_hist[idx][min(9, max(0, int(val / span * 10)))] += 1

    # THE QUESTION EVERY TERM ABOVE IS STRUCTURALLY UNABLE TO ANSWER: is there a lane there at all?
    #
    # All four read the camera, so "no lane line" and "a lane line the camera could not see" produce
    # an identical refusal. On 2026-08-12 that cost two confident wrong diagnoses of the same drives
    # -- darkness, then a code regression -- when he had simply been in the left lane already, and
    # the tell was buried in who had overtaken him on which side.
    #
    # `in_leftmost` cannot settle it either: it is defined as `not left_geometry_ok`, so asking it
    # here asks the same camera the same question twice and agrees with itself.
    #
    # The RADAR is independent of all of it. A vehicle tracked in the left lane proves a left lane
    # exists whatever the paint says -- so refusals with traffic over there are the camera failing,
    # and a drive of refusals with none is consistent with there being nothing to fail at.
    #
    # Gated on `available`, NOT just `occupied`. An unavailable side reports False, which would read
    # as "no lane" and manufacture exactly the false conclusion this exists to prevent.
    # ...AND `occupied` IS THE WEAKER OF THE TWO CLAIMS AVAILABLE, which is the whole reason the
    # second counter exists. It means "something is in the band". It does NOT apply
    # SAME_DIRECTION_MIN_FRACTION, so a car decelerating into a center turn lane -- still moving,
    # comfortably over MIN_MOVING_MS -- counts here exactly as a car cruising down a passing lane
    # does. That is precisely the confusion that reverted the road-edge waiver on 2026-08-09, and a
    # number carrying it cannot be the evidence for bringing the waiver back.
    #
    # `same_direction_recent` is the same sighting with the speed test applied: at 45 mph it asks
    # for 27, which a through lane clears and a turning car does not. THE GAP BETWEEN THE TWO IS
    # THE TURN-LANE EXPOSURE, measured rather than argued about, and it is the number a future
    # waiver has to answer for.
    if self.adjacent.left.available:
      self._geo_left_proof[0] += 1
      if self.adjacent.left.occupied:
        self._geo_left_proof[1] += 1
      if self.adjacent.left.same_direction_recent:
        self._geo_left_proof[2] += 1

  @property
  def geo_refusal_loosen_to(self) -> float:
    """Where the dominant term would have to sit to admit FOUR FIFTHS of the refusals.

    The number to change the constant to, which a mean cannot give. paint averaging 0.31 could be
    tightly clustered -- in which case 0.30 fixes almost everything -- or half at 0.45 and half at
    0.17, where 0.30 fixes half and nothing else. This is the twentieth percentile for the terms
    that need a value ABOVE them, which is every one of them except the road-edge std.
    """
    idx, _, _ = self.geo_refusal
    hist = self._geo_hist[idx]
    total = sum(hist)
    if not total:
      return 0.0
    want = total * (0.8 if idx == self.GEO_EDGE_STD else 0.2)
    seen = 0
    for i, n in enumerate(hist):
      seen += n
      if seen >= want:
        # Lower edge of the bucket for a minimum, upper for a maximum -- the conservative side of
        # the bucket in each direction, so the reported number actually admits the share it claims.
        edge = i if idx != self.GEO_EDGE_STD else i + 1
        return round(edge / 10.0 * self.GEO_SPAN[idx], 3)
    return round(self.GEO_SPAN[idx], 3)

  @property
  def geo_refusal(self) -> tuple[int, float, float]:
    """(term, its mean value, share of refused frames) for whatever refuses the left side most."""
    total = sum(self._geo_refusals)
    if not total:
      return 0, 0.0, 0.0
    idx = max(range(4), key=lambda i: self._geo_refusals[i])
    n = self._geo_refusals[idx]
    return idx, (self._geo_sums[idx] / n if n else 0.0), n / total

  def _lka_toggle(self, car_state_bp) -> bool:
    """Rising edge of the stalk-end LKA button.

    A physical control for the thing most often wanted while driving. Edge-triggered, not level:
    the signal reads Pressed for as long as it is held, and a held button must be one request.
    """
    try:
      pressed = bool(car_state_bp.lkaButtonPressed) if car_state_bp is not None else False
    except AttributeError:
      pressed = False
    edge = pressed and not self._lka_prev
    self._lka_prev = pressed
    return edge

  def _update_enable(self, lka_edge: bool = False) -> None:
    """The LKA button turns the feature ON and OFF. Not a pause.

    "I want this fully turned on and off with the LKA button. Not pause. But have it automatically
    turn on at speed or whatever, but then when I turn it off, leave it off until I turn it on."

    So the button writes the SAME key the settings toggle writes. One state, two ways to reach it:
    the screen shows what the button did, and the button changes what the screen shows. A separate
    "running" flag beside the settings toggle would be two sources of truth for one question, and
    the panel would eventually disagree with the menu about whether the feature was on.

    OFF IS STICKY, which is the whole request. PassingAssistLogEnabled is PERSISTENT | BACKUP, so
    it survives the ignition and stays off across drives until he presses the button again. That is
    the difference from the timed suspend this replaces -- a countdown always came back on its own,
    and coming back on its own is exactly what he does not want.

    "Automatically turn on at speed" needs nothing here: enabled is not the same as active. Once it
    is on, the speed gate below (Only Above, 30 mph) decides when it actually does anything, which
    it already did.

    Writing a settings key from here is the one case where that is right: it is HIS press, on HIS
    control, asking for exactly this. The rule against writing settings is about changing values he
    chose without being asked -- not about a button doing what a button is for.
    """
    if not lka_edge:
      return
    try:
      self.enabled = not self.enabled
      # block=True, and it has to be. put_bool defaults to putNonBlocking, and the periodic param
      # refresh re-reads this key -- so a non-blocking write can lose to its own read-back and the
      # button appears not to work. Exactly the race that made the +/- settings controls look dead.
      # Acceptable here and almost nowhere else: this runs once, on a deliberate press.
      self.params.put_bool("PassingAssistLogEnabled", self.enabled, block=True)
    except (AttributeError, TypeError):
      pass

  def _acc_braking(self, car_state_bp) -> None:
    """Is Ford's ACC already SLOWING THE CAR for this lead?

    The quality metric for the preemptive path. A suggestion made while this is False could have
    avoided the deceleration entirely; made while True, ACC has already started paying for the lead
    and the pass is only recovering.

    Two corrections, both from what the ICBM work established while building the ACC pill:

    PRECHARGE IS NOT BRAKING. It pressurises the system so a later application arrives without
    slack -- no meaningful deceleration, no stop lamps, no pad wear. Counting it here labeled a
    genuinely preemptive suggestion as reactive, which is backwards for a metric whose entire job
    is to measure how often we beat ACC to the decision. It gets its own field instead, because
    "we beat even the precharge" is a stronger claim worth being able to make separately.

    ENGINE BRAKING IS BRAKING, for this purpose. Ford documents ACC slowing by transmission
    downshift to avoid wearing the pads. No stop lamps and no pad wear, but the car IS losing speed
    for the lead, which is exactly what this measures. Missing it under-reported the reactive case.

    The two errors pushed in opposite directions, so neither cancelled the other -- they just made
    the number mean nothing in particular.
    """
    self.acc_braking_at_decision = False
    self.acc_precharge_at_decision = False
    self.acc_braking_available = False
    if car_state_bp is None:
      return
    bls = getattr(car_state_bp, 'brakeLightStatus', None)
    if bls is None or not bls.accDataAvailable:
      return

    self.acc_braking_available = True
    self.acc_precharge_at_decision = bool(bls.accPrechargeRequest)

    propulsion = float(getattr(bls, 'accPropulsionRequest', 0.0) or 0.0)
    engine_braking = ACC_PROPULSION_INACTIVE < propulsion < ACC_ENGINE_BRAKE_MS2
    self.acc_braking_at_decision = bool(bls.accDecelRequest) or engine_braking

  def _blindspot(self, car_state_bp) -> None:
    """Is BLIS actually reporting, as opposed to silently reading 'clear' because it is absent?

    Critical to record: carState.leftBlindspot defaults False, so an unavailable sensor is
    indistinguishable from a clear lane at the point of decision. Without this flag every logged
    suggestion from before the canbox lands would look blind-spot-checked when it was not.
    """
    self.blindspot_available = False
    if car_state_bp is None:
      return
    left = getattr(car_state_bp, 'blisLeft', None)
    right = getattr(car_state_bp, 'blisRight', None)
    self.blindspot_available = bool((left is not None and left.dataAvailable) or
                                    (right is not None and right.dataAvailable))

  def _traffic_signs(self, car_state_bp) -> None:
    """Read the TSR overtaking zone state.

    Restricted means: a limitation code is in force AND the camera says its own reading is
    reliable. Both halves matter -- LimitOutdated on a stale zone would otherwise veto passes for
    the rest of the drive.
    """
    self.overtake_restricted = False
    self.overtake_msg = 0
    self.overtake_status = 0
    self.tsr_available = False

    tsr = getattr(car_state_bp, 'trafficSignData', None)
    if tsr is None or not tsr.dataAvailable:
      return

    self.tsr_available = True
    self.overtake_msg = int(tsr.overtakeMsg)
    self.overtake_status = int(tsr.overtakeStatus)
    self.overtake_restricted = (self.overtake_msg not in TSR_OVTK_UNRESTRICTED and
                                self.overtake_status == TSR_OVTK_STATUS_RELIABLE)

  def _should_pass(self, lead, v_cruise: float, model=None) -> bool:
    """The one question: is there a vehicle in our lane slow enough to cost us speed?

    Deliberately does NOT ask whether we are closing on it or already behind it. Those are the same
    situation at two moments -- about to brake, or already braked -- and treating them separately is
    what made the old version wait for a state this driver never reaches.

    THE GOAL IS TO NEVER BE STUCK WAITING FOR THIS, and the measure of it is how little Ford's ACC
    has to brake. Getting stuck behind a car is not always avoidable and this does not pretend
    otherwise -- with no lane open there is nowhere to go, and sitting there is the correct
    outcome, reported as noLaneAvailable. What is avoidable is being stuck because the system was
    still making up its mind.
    So the only timer left in this path is a CONFIRMATION timer -- long enough that radar noise
    cannot trigger a maneuver, and no longer -- rather than a "have we suffered enough yet" timer.
    Every frame it costs is a frame nearer ACC deciding to shed speed for a car we were always
    going to pass, which is the expensive sequence: brake, then win the speed back in the next
    lane. trigger/accBrakingAtDecision is what measures whether we beat it.

    So: in our lane, slower than the SET speed by a margin worth the maneuver, near enough to be
    real. The margin is the judgment; everything else is a sanity bound.
    """
    # radarState yRel is left-POSITIVE like the radar's; flip to the camera frame before comparing
    # against the path. See MAX_LEAD_D_PATH_M for why this is no longer lead.dPath.
    self.lead_d_path = abs(-float(lead.yRel) - path_offset(model, float(lead.dRel))) if model is not None else 0.0
    # Once a lead is being tracked it may drift a little further out before it counts as gone --
    # see RANGE_HYSTERESIS_M. Without this a car hovering at the look-ahead distance alternates in
    # and out of range and the confirmation never completes.
    max_d = self.max_distance_m + (RANGE_HYSTERESIS_M if self.approach_seconds > 0.0 else 0.0)
    if self.lead_d_path > MAX_LEAD_D_PATH_M or lead.dRel > max_d:
      # Momentarily outside a bound. Free for a short while, then decaying. See LEAD_GAP_GRACE_S.
      self._lead_gap()
      return False

    # See DEFICIT_HYSTERESIS_MPH. Harder to become slow than to stay slow.
    hysteresis = DEFICIT_HYSTERESIS_MPH * CV.MPH_TO_MS if self.lead_is_slow else 0.0
    threshold = self.min_deficit_active_ms - hysteresis
    self.lead_is_slow = self.speed_deficit >= threshold
    if not self.lead_is_slow:
      # WHAT PATIENCE COST, measured where it is spent rather than inferred later. This lead was
      # slow enough by the number he set and was refused only because he is not going anywhere in
      # a hurry -- which is the whole question of whether the feature earns its place.
      if self.speed_deficit >= self.min_deficit_ms - hysteresis:
        self.patience_refused_s += DT_MDL
      self._clear_confirmation()
      return False

    # A good frame. Closes the gap window so an intermittent track keeps making progress rather
    # than trading one step forward for three back.
    self._lead_gap_s = 0.0

    # CAPPED at the threshold, which matters now that failing frames decay instead of erasing:
    # uncapped, a lead followed for five minutes would carry five minutes of credit and take over
    # a minute of absence to clear. The field means "confirmation progress" -- there is nothing to
    # be more confirmed than confirmed -- which is also what makes the progress bar 0..1 honestly.
    self.approach_seconds = min(self.approach_seconds + DT_MDL, self.persistence_s)
    # Returns SPOTTED, not confirmed. The caller runs every gate from this point, so a lane can be
    # found -- and a blinker lit -- while the confirmation is still running underneath. See
    # `confirmed` there for what actually commits the car to moving.
    return True

  def _archive_drive(self, last) -> None:
    """Push the previous drive's summary onto a rolling history. See DRIVE_HISTORY_MAX.

    Nothing depends on this succeeding. It is a convenience for reading a fortnight of drives in one
    paste instead of one drive at a time, and a param write must never be able to take the planner
    down -- same terms as the summary write itself.
    """
    if not last:
      return
    try:
      hist = self.params.get("PassingAssistHistory") or []
      # A boot with no driving leaves LastDrive untouched, so without this the same drive is
      # archived again every time the car is started and the history fills with one drive.
      #
      # COMPARED ON WHAT BOTH SIDES ACTUALLY SHARE. The stored entry has a build and no timeline;
      # the incoming one has a timeline and no build. A straight == never matches, and every start
      # of the car appends the same drive again.
      #
      # This is the SECOND time in a day that adding a field to one side and not the other broke
      # this check, which is why the exclusion list is named once and used on both sides rather
      # than written out at each call.
      def comparable(d):
        return {k: v for k, v in d.items() if k not in ("build", "timeline")}

      if hist and comparable(hist[-1]) == comparable(last):
        return
      # STAMP THE BUILD. Asked directly: "are we keeping logs from previous versions or wiping
      # them with each commit since you keep changing things?"
      #
      # Kept -- the key is PERSISTENT and nothing clears it, so a fortnight survives any number of
      # updates. Which is exactly the problem: the thresholds this measures move between drives, so
      # a run from before the geometry was rewritten is not comparable to one from after, and until
      # now nothing in the record said which was which. Twenty drives of that is worse than five
      # honest ones, because the mixture reads as noise in the gates rather than as two different
      # gates.
      #
      # Short SHA only. It is enough to sort drives into builds and to look up what changed, and it
      # costs eight characters in a param that already holds twenty-three numbers per drive.
      # THE TIMELINE DOES NOT GO IN THE HISTORY. It rides in LastDrive, where it is the record of
      # the drive just finished and gets read once. Archiving it too would put three hundred entries
      # into each of DRIVE_HISTORY_MAX drives -- at 60 that is around 390 KB in a single
      # PERSISTENT | BACKUP param, rewritten in full every time a drive ends, to hold a sequence
      # nobody is going to read a fortnight later. Stated against the constant rather than a number,
      # because the number moved from 20 to 60 the same day and this comment did not.
      # History is for aggregates; the timeline is for the drive he is describing.
      last = {k: v for k, v in last.items() if k != "timeline"}
      try:
        # STAMPED HERE ONLY AS A FALLBACK. See _save_drive_summary: the build is now written into
        # the record while the drive is RUNNING, because archiving happens at the start of the NEXT
        # drive -- so a drive that ran on build A and was archived after an update was labelled B.
        # Seen 2026-08-16: drives 5 and 6 were stamped 91e43d4f while plainly lacking the fields
        # that build added. A stamp that records who ARCHIVED a drive answers nothing anyone asked.
        last.setdefault("build", str(self.params.get("GitCommit") or "")[:8])
      except Exception:  # noqa: BLE001 - an unstamped drive is still worth keeping
        pass
      hist.append(last)
      self.params.put("PassingAssistHistory", hist[-DRIVE_HISTORY_MAX:])
    except Exception:  # noqa: BLE001 - a param failure must never reach the planner
      pass

  def _track_lane_hog(self) -> None:
    """Time spent behind someone sitting in the leftmost lane below the set speed.

    Asked for by name on 2026-08-09, at the end of a list whose other entries were the horn, the
    high beams, a brake check and "a little love tap". This is the one of them that tells the next
    drive something.

    THREE TERMS, and the third is what makes it a hog rather than traffic:

      a lead, slow enough to be worth passing   the same test the whole feature runs on, so this
                                                cannot disagree with the panel about what "slow" is
      no lane to our LEFT                       we are already as far over as the road goes. Behind
                                                someone slow in the middle of a freeway is ordinary
                                                traffic; behind them in the passing lane is not
      a lane to our RIGHT                       somewhere for them to go. Without it they are not
                                                hogging anything, they are just the front of the
                                                queue, and counting that would make the number
                                                meaningless on a two-lane road

    Deliberately NOT gated on whether a pass was suggested or refused. The question is how much of
    the drive was spent stuck behind one of these, and a gate that only counted the ones the system
    reacted to would report the feature's coverage rather than the road's behavior.

    HOG_MIN_S keeps a momentary queue out of the count: everyone slows for a moment, and a car that
    is genuinely camped there is there for a while.
    """
    hogging = (self.lead_is_slow and not self.left_geometry_ok and self.right_geometry_ok)
    if not hogging:
      self._hog_held_s = 0.0
      self._hog_counted = False
      return

    self._hog_held_s += DT_MDL
    if self._hog_held_s < HOG_MIN_S:
      return
    self.hog_seconds += DT_MDL
    if not self._hog_counted:
      self._hog_counted = True
      self.hog_count += 1

  def _record_timeline(self) -> None:
    """Append one entry whenever the visible state changes. See TIMELINE_MAX.

    The tuple is deliberately the four things a driver would narrate: what it decided, what stopped
    it, and what each of the two machines was doing. A change in any of them is an event he might
    mention; a change in none of them is not.
    """
    # No int() here: these are this class's own attributes and are already plain ints. The calls
    # were no-ops that made them read like the live-capnp kind, which is the distinction the
    # enum guard exists to police -- so writing them the same way costs the guard its meaning.
    now = (self.suggestion, self.blocked_by,
           self.maneuver.phase, self.keep_right_maneuver.phase)
    if now == self._timeline_prev:
      return
    self._timeline_prev = now
    self._timeline.append([round(self.elapsed_s, 1), *now])
    if len(self._timeline) > TIMELINE_MAX:
      del self._timeline[0]

  def _build(self) -> str:
    """The short SHA of the running code, or "" if it cannot be read.

    ITS OWN TRY, and not for tidiness. This is read inside the record dict passed to params.put, so
    an exception here does not lose a field -- it loses THE WHOLE SUMMARY, silently, because the
    caller wraps the write in a bare except by design ("a param write must never be able to take the
    planner down"). Caught offline by a stub that raises on unknown keys exactly as the device does,
    which is the behaviour that makes the stub worth having.

    Cached after the first success: it cannot change without a restart, and this runs on every
    periodic write.
    """
    if self._build_sha is None:
      try:
        self._build_sha = str(self.params.get("GitCommit") or "")[:8]
      except Exception:
        return ""
    return self._build_sha

  def _save_drive_summary(self) -> None:
    """Persist what this drive measured. See LAST_DRIVE_WRITE_S.

    Nothing depends on this succeeding -- it is a convenience for reading numbers after the fact,
    and a param write must never be able to take the planner down.
    """
    self._last_drive_write_s += DT_MDL
    if self._last_drive_write_s < LAST_DRIVE_WRITE_S:
      return
    self._last_drive_write_s = 0.0

    top_key, top_share = self.top_blocked
    try:
      self.params.put("PassingAssistLastDrive", {
        # WHICH BUILD ACTUALLY DROVE THIS, stamped while the drive is running rather than when it is
        # archived. Archiving happens at the start of the NEXT drive, so a drive recorded on build A
        # and archived after an update was labelled with B -- and on 2026-08-16 drives 5 and 6 were
        # stamped 91e43d4f while visibly lacking the fields that build introduced. The stamp exists
        # to sort drives by the code that produced them, and stamping it at archive time answered
        # the opposite question.
        "build": self._build(),
        # See TIMELINE_MAX. Written with the rest rather than on its own schedule so a drive that
        # ends with a yanked ignition keeps whatever the last periodic write had.
        "timeline": list(self._timeline),
        "elapsed": round(self.elapsed_s, 1),
        # WHICH geometry term refused the left side, and by how much. Published live since it was
        # written and never stored, so the one number that explains a drive with sixty refusals and
        # zero suggestions could only be read off a running car -- "I guess I need to go back to my
        # car?" is the cost of that, and the answer was no, it was my omission.
        # See _geo_term_fails. Shares of the refused frames each term failed on, INDEPENDENTLY --
        # they sum to more than 1 when terms fail together, which is the whole point of recording
        # them. Order: edge-std, paint, width, room-past-the-line.
        "geoTermFails": [round(n / self._geo_frames, 3) if self._geo_frames else 0.0
                         for n in self._geo_term_fails],
        # See _edge_by_speed. Refusal RATE per band, so the arterial and the interstate can be told
        # apart -- the whole question being whether the edge is unusable everywhere or only where
        # painted medians live.
        "edgeFailBySpeed": [round(bad / n, 3) if n else -1.0 for n, bad in self._edge_by_speed],
        "geoRefusedBy": int(self.geo_refusal[0]),
        "geoRefusedValue": round(self.geo_refusal[1], 3),
        "geoRefusedShare": round(self.geo_refusal[2], 3),
        "geoLoosenTo": self.geo_refusal_loosen_to,
        # See the radar note in _record_refusal. -1 when the radar could not answer on any refused
        # frame, which is NOT zero -- zero is a claim ("a lane was never seen"), -1 is a silence
        # ("nobody could look"), and collapsing them is the mistake this field exists to stop.
        "geoLeftProven": (round(self._geo_left_proof[1] / self._geo_left_proof[0], 3)
                          if self._geo_left_proof[0] else -1.0),
        # The same share with the travel-speed test applied. Lower than geoLeftProven by exactly
        # the traffic that was slowing down over there -- which on an arterial is a car entering a
        # turn lane, and on a freeway is nothing. Same -1 convention, same reason.
        "geoLeftTravelProven": (round(self._geo_left_proof[2] / self._geo_left_proof[0], 3)
                                if self._geo_left_proof[0] else -1.0),
        "wantedSeconds": round(self.wanted_seconds, 1),
        "hogSeconds": round(self.hog_seconds, 1),
        "suggestedLatAccMax": round(self.suggested_lat_acc_max, 2),
        "suggestedLatAccHist": list(self._lat_acc_hist),
        "hogCount": int(self.hog_count),
        "topBlockedBy": int(top_key),
        "topBlockedShare": round(top_share, 3),
        "clearShare": round(self.clear_share, 3),
        "crawlEvents": int(self.overtake.crawl_events),
        "crawlLongest": round(self.overtake.crawl_longest, 1),
        "aborts": self.maneuver.aborts,
        "accOnsetMax": round(self.acc_onset_max, 1),
        # See _moved_toward_an_exit. Every RIGHTWARD driver change on the drive, split by which
        # test recognized it: widening, outermost lane, slowed afterwards, nothing. The last
        # bucket is the one to read -- it is the count of exits all three tests missed, which is
        # the number the note in that method has been asking for since it was written.
        "exitsBy": list(self._exits_by),
        # See DEFAULT_PATIENCE. What the extra fussiness cost -- seconds where a lead was slow
        # enough by his own setting, and passes he then made himself.
        "patienceRefused": round(self.patience_refused_s, 1),
        "patienceMissed": int(self.patience_missed),
        "driverPasses": int(self.driver_passes),
        "driverPassesAgreed": int(self.driver_passes_agreed),
        "driverPassLead": round(self.driver_pass_lead_s, 1),
        # A REASON CODE, not a count, and the key name has misled a reader of this record more
        # than once. Kept for continuity with the archived history; the unambiguous name is
        # alongside it.
        "driverPassMiss": int(self.driver_pass_miss_reason),
        "driverPassMissReason": int(self.driver_pass_miss_reason),
        # See OFF_BY_DESIGN -- the denominator that makes driverPassesAgreed mean something.
        "driverPassesEligible": int(self.driver_passes_eligible),
        "missedDeficit": round(self.missed_deficit_mph, 1),
        "lifetimeDrives": int(self.lifetime[0]),
        "lifetimePasses": int(self.lifetime[1]),
        "lifetimeAgreed": int(self.lifetime[2]),
        "suggestionsMade": int(self.suggestions_made),
        "suggestionsTaken": int(self.suggestions_taken),
        "longestIgnored": round(self.longest_ignored, 1),
        # See overtakenSeconds. The LONGEST quiet stretch, because the question this measurement
        # exists to answer is whether a genuinely empty lane ever happens -- a mean would bury it
        # under the busy stretches, which is the wrong way round for something meant to license
        # going rather than to refuse.
        "overtakenLeft": int(self.adjacent.left.overtaken_count),
        "overtakenRight": int(self.adjacent.right.overtaken_count),
        "overtakenQuietest": round(self.overtaken_quietest_s, 1),
        "oncomingSeen": round(self.oncoming_seen_seconds, 1),
        "oncomingRemembered": round(self.oncoming_remembered_seconds, 1),
        # The evidence behind the LAST oncoming veto of the drive. Kept because the live panel only
        # shows it while the veto is up, and catching that means glancing at the screen at the right
        # moment on a road where it may only fire once.
        "oncomingDRel": round(self._last_oncoming[0], 1),
        # yRel is already published live and DRAWN, but the drawing is gone the moment the drive
        # ends -- so the one record that survives to be read afterwards was missing both of the
        # fields that say WHICH bug fired. See oncomingEdgeTrusted in custom.capnp.
        "oncomingYRel": round(self._last_oncoming[1], 1),
        "oncomingVAbs": round(self._last_oncoming[2], 1),
        "oncomingEdgeTrusted": bool(self._last_oncoming[3]),
        # See ONCOMING_LAT_BUCKETS. The whole point of collecting it -- kept per drive so a trip
        # across several road types gives one distribution each rather than one blurred average.
        "oncomingLatHist": list(self.adjacent.oncoming_lat_hist),
      })
    except Exception:  # noqa: BLE001 - a param write failure must never reach the planner
      pass

  @property
  def top_blocked(self) -> tuple[int, float]:
    """The reason that consumed most of the time a pass was wanted, and its share.

    Excludes `none` -- "nothing was stopping it" is reported separately as clearShare, and letting
    it win here would hide the actual answer behind the good news on any drive that mostly worked.
    """
    blocked = {k: v for k, v in self._block_seconds.items() if k != int(Blocked.none)}
    if not blocked or self.wanted_seconds <= 0.0:
      return int(Blocked.none), 0.0
    key = max(blocked, key=lambda k: blocked[k])
    return key, blocked[key] / self.wanted_seconds

  @property
  def clear_share(self) -> float:
    if self.wanted_seconds <= 0.0:
      return 0.0
    return self._block_seconds.get(int(Blocked.none), 0.0) / self.wanted_seconds

  def _track_driver_change(self, CS) -> None:
    """Watch the driver's own stalk and stand down after they use it. See SETTLE_AFTER_CHANGE_S.

    The exit test is latched WHILE they signal, not sampled after. Once the car has moved into the
    ramp lane the road edge belongs to the ramp and the widening that identified it is gone -- so
    the only moment the evidence exists is while the maneuver is still happening.
    """
    self.driver_change_standdown = max(0.0, self.driver_change_standdown - DT_MDL)

    # Read from LAST frame's state, deliberately: on the frame the stalk first appears this still
    # sees "no maneuver in progress" and captures the speed he was holding before it began. A
    # change that starts with a lift then measures its own drop from the right number.
    if self._driver_blinker is None and self._steer_held_s == 0.0:
      self._change_entry_v = float(CS.vEgo)
    self._tick_exit_watch(float(CS.vEgo))

    # How long the current suggestion has been up. Reset on any change, so at the moment the driver
    # acts this is the warning it actually gave -- which is the whole benefit being claimed: enough
    # lead time to be out of the lane before Ford's ACC starts braking.
    suggesting = self.suggestion != Side.none and self.reason == Reason.passing
    if suggesting:
      self._suggest_held_s += DT_MDL
    # See CHIME_SETTLE_S. Not the rising edge: an edge fires again every time a gate flickers, and
    # a marginal lane flickers at 20 Hz. The suggestion has to hold, and then not repeat for a
    # while, before the car says anything out loud.
    self._chime_held_s = self._chime_held_s + DT_MDL if suggesting else 0.0
    self._since_chime_s = min(self._since_chime_s + DT_MDL, 1e4)
    self.suggestion_started = (self._chime_held_s >= CHIME_SETTLE_S and
                               self._since_chime_s >= CHIME_MIN_INTERVAL_S)
    if self.suggestion_started:
      self._since_chime_s = 0.0

    # Counted from BOTH machines and BOTH kinds of reversal. A keep-right that backs out is the
    # same event to a driver as a pass that does, and the emergency count is the one that matters
    # most -- reporting it as silence because it lives in a different counter would be absurd.
    aborts = (self.maneuver.aborts + self.maneuver.emergency_aborts +
              self.keep_right_maneuver.aborts + self.keep_right_maneuver.emergency_aborts)
    self._since_abort_chime_s = min(self._since_abort_chime_s + DT_MDL, 1e4)
    self.abort_started = (aborts > self._aborts_seen and
                          self._since_abort_chime_s >= ABORT_CHIME_MIN_INTERVAL_S)
    # Advanced whether or not it sounded, so a suppressed abort is skipped rather than queued --
    # a tone that arrives twelve seconds after the thing it describes is worse than no tone.
    self._aborts_seen = aborts
    if self.abort_started:
      self._since_abort_chime_s = 0.0
    if suggesting and not self._prev_suggesting:
      self.suggestions_made += 1
      self._episode_taken = False
    elif self._prev_suggesting and not suggesting:
      # The episode ended. If the driver never acted on it, how long it stood is the interesting
      # part -- one that lapsed after three seconds is ordinary traffic changing its mind, one held
      # for half a minute while the driver sat there is the system wanting something they did not.
      if not self._episode_taken:
        self.longest_ignored_s = max(self.longest_ignored_s, self._suggest_held_s)
    if not suggesting:
      self._suggest_held_s = 0.0
    self._prev_suggesting = suggesting

    # A takeover with no stalk. Tracked alongside the blinker rather than instead of it: doing
    # both at once is normal, and whichever ends last is what the stand-down should follow.
    if CS.steeringPressed:
      self._steer_held_s += DT_MDL
      if self._steer_held_s >= MIN_STEER_TAKEOVER_S and self.right_widening:
        self._signalled_over_widening = True
    elif self._steer_held_s > 0.0:
      held = self._steer_held_s
      self._steer_held_s = 0.0
      if held >= MIN_STEER_TAKEOVER_S and self._driver_blinker is None:
        # No stalk, so no direction -- the widening seen during the takeover is the only evidence
        # about where they went, and it is right-hand by construction.
        self._stand_down(self._signalled_over_widening)
        return

    side = 'left' if CS.leftBlinker else 'right' if CS.rightBlinker else None
    if side is not None:
      if self._driver_blinker != side:
        if side == 'left' and self.has_lead:
          self._record_driver_pass()
        self._driver_blinker = side
        # NOT cleared if the wheel is already being held. Drifting over and signaling afterwards
        # is one maneuver, and clearing here threw away the widening the steering had already
        # seen -- so an exit taken in that order got the four second pause instead of the full
        # one, which is the case the stand-down exists for.
        if self._steer_held_s == 0.0:
          self._signalled_over_widening = False
      # THE GAP AFTER A MANUAL MOVE RIGHT, from the road 2026-08-09. Narrow, and worth stating
      # precisely because the first draft of this note had it much too wide.
      #
      #   "if I manually do a nudgeless sunnypilot lane change to the right from a faster lane to
      #    a slower lane, then I am probably exiting soon."
      #
      # His speed argument is already implemented, twice over, on the SUGGESTION side:
      #
      #   - keep-right refuses a right lane whose nearest car is slower than the set speed by the
      #     deficit margin. See _keep_right; it is the passing threshold read backwards.
      #   - the lane must also have EXISTED for MinLaneAge. That is his own earlier exit test and
      #     the better one, because an exit lane appears and a through lane has been beside us for
      #     miles -- so it catches the exit lanes that are NOT slower, which he raised himself as
      #     the hole in a speed-only rule.
      #
      # So nothing is missing from what the system OFFERS. What is missing is how long it stays
      # quiet after HE acts: _stand_down gets SETTLE_AFTER_CHANGE_S unless _signalled_over_widening
      # saw the ramp, and moving over two lanes early defeats that, because the ramp is not yet
      # there to be seen.
      #
      # The buildable version is therefore NOT about lane speed at all -- lane age and lane speed
      # gate suggestions, not the stand-down. It is whether a DRIVER-INITIATED RIGHTWARD change
      # deserves the long pause by default on a road where he is above the passing floor. Measure
      # first: how often driver_change_was_exit comes out false on a freeway drive with known
      # exits. Every one of those is a case the widening test missed.

      # Right-hand only: the road opening up on the left is not an exit, it is a lane being added.
      if side == 'right' and self.right_widening:
        self._signalled_over_widening = True
      return


    if self._driver_blinker is None:
      return

    # Stalk just went off: the change is done, or they thought better of it. Either way, pause.
    rightward = self._driver_blinker == 'right'
    self._stand_down(rightward and self._moved_toward_an_exit(), rightward=rightward)
    self._driver_blinker = None

  def _moved_toward_an_exit(self) -> bool:
    """Rightward, and it looks like exit preparation rather than lane discipline.

    THE CASE THIS EXISTS FOR HAS NO EXIT LANE AT ALL. From the road 2026-08-09, an I-80 exit he
    takes daily: the ramp simply leaves from the rightmost through lane, so he moves over early to
    prepare. Every exit test we had misses it, and each for its own structural reason:

      road widening      nothing widens -- there is no ramp opening alongside to see.
      lane age           the rightmost lane is an ordinary through lane, beside us for miles. It
                         passes the age test comfortably.
      lane speed         "sometimes they aren't slower", his own caveat, and correct.

    What is left is where he ENDED UP: in the outermost lane, with no lane to the right. That is
    already computed -- right_geometry_ok collapses to the shoulder there -- so this needs no new
    measurement, only the observation that a DRIVER-CHOSEN move into the outermost lane is either
    exit preparation or lane discipline, and suggesting a pass straight afterwards is wrong in both
    cases.

    Which is why the failure mode is benign and this ships without waiting for more road data: if
    he was merely being courteous rather than exiting, the cost is that the system stays quiet for
    a while on the side he just deliberately left. That is what keep-right wanted anyway.

    The widening test stays as an OR rather than being replaced -- it still catches the ordinary
    kind of exit, where a ramp lane really does open up and he moves into that instead.
    """
    if self._signalled_over_widening:
      self._exits_by[0] += 1
      return True
    # No lane to the right of where we now are. Read after the change, which is what makes it mean
    # "outermost" rather than "there was no lane before I moved".
    if not self.right_geometry_ok:
      self._exits_by[1] += 1
      return True
    # Neither test could see it. Counted here rather than at the call site so the totals cannot
    # drift apart, and provisionally: the speed watch may still claim this one, which moves it out
    # of this bucket and into the third.
    self._exits_by[3] += 1
    return False

  def _arm_exit_watch(self) -> None:
    """A rightward change that neither geometry test claimed. Keep watching the speed.

    See EXIT_WATCH_S. NOT ARMED WITH A LEAD IN FRONT, which is the discriminator that makes this
    safe to ship, and the reason it is not simply "he slowed down":

      following   he moves right and gets behind a truck. He slows to the truck's speed. That is
                  the single most common rightward change on a two-lane interstate, and it is also
                  the exact moment he most wants to be offered the pass back. A stand-down there
                  would fight the feature.
      exiting     he moves right with clear road ahead and slows anyway. Nothing in front explains
                  it, cruise would not do it, so it is him coming off for a reason.

    The lead is read at the moment of the change rather than during the watch because the gate at
    driver_change_standdown returns before has_lead is updated -- it would be frozen for the whole
    watch regardless, and a value frozen at a meaningful instant beats one frozen by accident.

    The climb is a deliberate no-op rather than an exception: he moves right and slows on a grade
    with nothing ahead, and this reads it as an exit. He does not want to be offered a pass there
    either -- "I don't want to pass going uphill when my engine is already stressed enough" -- so
    the wrong reason reaches the right silence.
    """
    if self.has_lead:
      return
    self._exit_watch_s = EXIT_WATCH_S
    self._exit_watch_v = self._change_entry_v

  def _tick_exit_watch(self, v_ego: float) -> None:
    """Did he slow down after moving over? Then that was an exit after all.

    Upgrades a stand-down already in progress. It does NOT restart since_driver_change_s or the
    right lane age: the change itself happened seconds ago and re-zeroing them here would age the
    lane from the moment we changed our mind about it rather than from the moment the road did.
    """
    if self._exit_watch_s <= 0.0:
      return
    self._exit_watch_s = max(0.0, self._exit_watch_s - DT_MDL)
    if v_ego > self._exit_watch_v - EXIT_DECEL_MS:
      return
    self._exit_watch_s = 0.0
    self.driver_change_was_exit = True
    self._exits_by[3] -= 1
    self._exits_by[2] += 1
    # max, not assignment: an ordinary settle is shorter than the exit pause, but a watch that
    # fires one frame before the exit stand-down would have expired must never shorten it.
    self.driver_change_standdown = max(self.driver_change_standdown, self.exit_standdown_s)

  def _stand_down(self, was_exit: bool, rightward: bool = False) -> None:
    """The driver just finished a maneuver of their own. Pause, and forget what was beside us.

    Called from both routes into a stand-down -- the stalk and a silent steering takeover -- for the
    same reason `_record_oncoming_refusal` is shared: two sites doing this by hand is two sites that
    can drift, and one of them already had.

    THE LANE AGE HAS TO RESTART HERE, and missing it left the exit gate with a hole the size of the
    thing it was built for. That counter ages the SIDE, not the lane: it only ever zeroed when the
    model stopped seeing a lane to our right. Change lanes and the lane on our right is a different
    piece of road -- on a highway, very often an exit-only lane -- but the counter carried on from
    the through lane that used to be there. A brand-new lane arrived pre-aged and walked through the
    one test that exists to catch it.

    Restarted for a LEFT change as well, where the lane to our right afterwards is the one we just
    left and its age really is known. Re-proving it costs nothing -- the anti-weave settle is longer
    than the age gate, so the wait does not change -- and a gate that reasons about which way we
    went is a gate with a second way to be wrong.
    """
    self.driver_change_was_exit = was_exit
    self.driver_change_standdown = self.exit_standdown_s if was_exit else float(SETTLE_AFTER_CHANGE_S)
    self.since_driver_change_s = 0.0
    self._signalled_over_widening = False
    self.right_lane_age_s = 0.0
    # Cleared unconditionally first, so a second change during a watch replaces it rather than
    # leaving the old one to fire against a speed reference that belongs to a maneuver ago.
    self._exit_watch_s = 0.0
    if rightward and not was_exit:
      self._arm_exit_watch()

  def _record_driver_pass(self) -> None:
    """The driver just started a pass. Did we agree, and how long had we been saying so?

    Sampled on the RISING EDGE of the stalk, not afterwards: the driver-active gate blanks the
    suggestion on the very next frame, so a moment later there is nothing left to compare against.

    COUNTED ON ANY LEAD, not only one already judged slow enough. That distinction is the whole
    value of the miss reason. Requiring lead_is_slow meant a pass the system never even considered
    -- a car below the deficit threshold, or one it had lost -- incremented nothing at all, so the
    single most damning failure was the one failure the readiness score could not express. It now
    lands as a miss against nothingSlower, which is exactly the calibration question worth asking.

    The cost is that a left-hand change made for some other reason, with a lead ahead, counts as a
    pass. Accepted: with a car in front, passing is overwhelmingly the reason, and a metric that
    flatters itself by discarding its own misses is worth nothing.
    """
    self.driver_passes += 1
    # See OFF_BY_DESIGN. The total still counts everything -- "a metric that flatters itself by
    # discarding its own misses is worth nothing" -- but the DENOMINATOR for agreement has to be
    # passes it could have had an opinion about, or the score measures the minimum speed setting.
    if int(self.blocked_by) not in OFF_BY_DESIGN:
      self.driver_passes_eligible += 1
    if self.suggestion == Side.left and self.reason == Reason.passing:
      self.driver_passes_agreed += 1
      # Closes the open episode so it is not also counted as ignored -- the driver-active gate
      # blanks the suggestion on the next frame, which otherwise looks exactly like it lapsing.
      self.suggestions_taken += 1
      self._episode_taken = True
      # Running mean rather than the latest, so one unusually long or short warning cannot stand
      # in for the drive.
      n = self.driver_passes_agreed
      self.driver_pass_lead_s += (self._suggest_held_s - self.driver_pass_lead_s) / n
    else:
      key = int(self.blocked_by)
      self._miss_reasons[key] = self._miss_reasons.get(key, 0) + 1
      # Only for the threshold's own refusals. A pass refused for a blind spot says nothing about
      # whether 4 mph is the right number, and averaging it in would bury the cases that do.
      if key == int(Blocked.nothingSlower) and self.has_lead:
        # ATTRIBUTED BEFORE IT IS AVERAGED, because the panel turns missedDeficitMph into "try N"
        # -- a recommendation about the DEFICIT SETTING. A lead his own setting would have taken,
        # refused because patience raised the bar, says nothing about that setting: telling him to
        # lower it would be advice to fix the wrong number, and he follows this advice deliberately
        # ("I will go through each setting, check the description for recommended value").
        if self.patience_scale > 1.0 and self.speed_deficit >= self.min_deficit_ms:
          self.patience_missed += 1
        else:
          self._missed_deficit_n += 1
          mph = float(self.speed_deficit) * CV.MS_TO_MPH
          self.missed_deficit_mph += (mph - self.missed_deficit_mph) / self._missed_deficit_n

  @property
  def lifetime(self) -> tuple[int, int, int]:
    """(drives, passes, agreed) including this drive.

    Computed rather than accumulated, so the periodic save is idempotent: writing it ten times in a
    drive must not count the drive ten times, and a counter that drifts upward every save would be
    worse than not having one.
    """
    return (self._life_drives + (1 if self.driver_passes else 0),
            self._life_passes + self.driver_passes,
            self._life_agreed + self.driver_passes_agreed)

  @property
  def longest_ignored(self) -> float:
    """The longest a suggestion has stood unacted -- INCLUDING one still standing.

    Recorded only on the falling edge before, which meant a suggestion that was still up when the
    drive ended was never counted at all. A system that offered one enormous unacted pass per drive
    and nothing else would have reported a spotless record, which is the exact opposite of what
    that behavior means.
    """
    live = self._suggest_held_s if (self._prev_suggesting and not self._episode_taken) else 0.0
    return max(self.longest_ignored_s, live)

  @property
  def driver_pass_miss_reason(self) -> int:
    """Which gate most often stopped it when the driver passed anyway."""
    if not self._miss_reasons:
      return int(Blocked.none)
    return max(self._miss_reasons, key=lambda k: self._miss_reasons[k])

  def _lead_gap(self) -> None:
    """One frame where the lead failed a bound or was not there at all.

    Free inside the grace window -- a lost radar return is the same car, and charging for it is
    what made an intermittent track impossible to confirm. Beyond the window it decays quickly, so
    a car that has really gone still clears.
    """
    self._lead_gap_s += DT_MDL
    if self._lead_gap_s <= LEAD_GAP_GRACE_S:
      return
    self.approach_seconds = max(0.0, self.approach_seconds - CONFIRM_DECAY_RATE * DT_MDL)
    if self.approach_seconds == 0.0:
      self.lead_is_slow = False

  def _clear_confirmation(self) -> None:
    """A changed situation rather than a noisy one: start over properly."""
    self.approach_seconds = 0.0
    self.lead_is_slow = False
    self._lead_gap_s = 0.0
    # The approach is over, so the next one measures its own onset rather than inheriting this one.
    self.acc_onset_d_rel = 0.0

  def _lead_state(self, lead, v_cruise: float) -> None:
    """Record what the lead is doing, whichever trigger ends up using it."""
    self.has_lead = bool(lead.status)
    # aLeadK is the lead's own acceleration, Kalman-filtered -- not aRel, which folds in whatever
    # we are doing ourselves and would read as braking every time WE accelerate.
    self.lead_accel = float(getattr(lead, 'aLeadK', 0.0))
    self._lead_braking_s = 0.0 if self.lead_accel <= LEAD_BRAKING_MS2 else self._lead_braking_s + DT_MDL
    self.lead_radar_confirmed = bool(getattr(lead, 'radar', False))
    self.lead_model_prob = float(getattr(lead, 'modelProb', 0.0))
    self.lead_d_rel = float(lead.dRel)
    self.lead_v_lead = float(lead.vLead)
    self.speed_deficit = float(v_cruise - lead.vLead)
    closing = -float(lead.vRel)
    self.lead_ttc = (lead.dRel / closing) if closing > MIN_APPROACH_CLOSING_MS else NO_TTC_S

  def _reference_speed(self, CS, sm, v_cruise: float, speed_limit_target: float) -> float:
    """The speed the driver asked for -- the operand the deficit is measured against.

    NOT the number on the dash. With ICBM running, Veh_V_DsplyCcSet is the CURRENT commanded set
    speed, which ICBM lowers for curves, speed limits and the radar-blind lead and then restores.
    Differencing against it means that the moment anything slows the car, every lead stops looking
    slow -- exactly when a pass is most wanted.

    A MANUAL OVERRIDE TAKES THE SPEED LIMIT OUT OF IT. If ICBM reports the driver took the set
    speed back, the limit stops being evidence of anything: they have said what they want. That is
    the fix for a real and ordinary case -- set 60 where the limit plus offset is 70, and the old
    max() measured against 70, so a car ahead doing 62 (FASTER than the driver asked to go) read as
    8 under and produced a pass suggestion. Deliberately driving below the limit is not a condition
    to be talked out of.

    The dash still floors it, even under an override. ICBM sets v_baseline from the cluster when the
    override latches, so the two agree in normal operation; if they ever drift apart the dash is the
    more trustworthy of the two, because that is the number the car is physically driving toward. A
    lead below it really is holding us back whatever ICBM's record says.

    Otherwise the intent is the highest of what is left, and the max is what makes that safe: with
    no override, ICBM only ever moves the dash value DOWN from the driver's baseline, so a maximum
    recovers the baseline without needing to know which feature lowered it or why.

    Source is recorded, because an operand that is silently wrong produces a system that looks
    correct and never fires -- which is how this was wrong twice.
    """
    cluster = float(CS.cruiseState.speedCluster)

    # The driver took the set speed back and ICBM is holding their number. Checked FIRST and
    # returned immediately: an explicit choice is not one of several candidates.
    try:
      icbm = sm['selfdriveStateSP'].intelligentCruiseButtonManagement
      baseline = float(icbm.vBaseline)
      # str(), not int(). This is a LIVE capnp message, so overrideState is a _DynamicEnum and
      # int() raises TypeError on the device -- caught by the except below, which means the driver's
      # own held speed was silently never honored rather than crashing. The same call crashed the
      # drive-summary panel outright on 2026-08-07; here it failed quietly, which is worse.
      if str(icbm.overrideState) == "manual" and baseline > 0:
        held = max(baseline, cluster)
        self.reference_speed = held
        self.reference_source = RefSource.icbmHold if baseline >= cluster else RefSource.cluster
        self._apply_patience()
        return held
    except (KeyError, AttributeError, TypeError, ValueError):
      pass

    best, source = (cluster if cluster > 0 else v_cruise), RefSource.cluster

    # WITH NO SLA NUMBER, THE DRIVER'S MAX IS THE INTENT AND THE DASH IS NOT.
    #
    # Added 2026-08-17, when ICBM's `enforce_no_limit_no_hold` began clearing the baseline wherever
    # SLA has no limit -- about 14% of the road he covers. Their session flagged it: "if any gate of
    # yours reads the hold as the driver's chosen speed, it now reads zero there."
    #
    # It does, and the consequence lands exactly on the fault the top of this docstring exists to
    # prevent. With the baseline gone the branch above no longer fires, so the reference falls to
    # `cluster` -- the DASH -- which ICBM lowers for curves, leads and the hazard path. Differencing
    # against a number that drops every time the car slows means every lead stops looking slow at
    # the moment a pass is most wanted, which is the whole reason this method does not simply read
    # the dash.
    #
    # `v_cruise` is `vCruiseCluster` in m/s, and under ICBM it tracks DRIVER BUTTON PRESSES ONLY --
    # nothing lowers it for a curve. So a max recovers the driver's number with no new capnp field
    # and without ICBM's `no_limit_hold_speed`, which is not on the wire and cannot be read here.
    #
    # ONLY WHERE SLA HAS NOTHING, which is the narrow case and keeps the managed case untouched. If
    # SLA is driving to a limit, taking the MAX over its target would suggest passes to reach a
    # speed the system is deliberately not driving -- "a valid limit is not consent to drive it",
    # the same fault the planner's own comment records for feeding the limit in.
    if speed_limit_target <= 0.0 and v_cruise > best:
      best, source = v_cruise, RefSource.cluster

    # SLA following the limit plus offset. Only reaches here when the driver has not overridden,
    # and the planner only supplies it when Speed Limit Assist is actually switched on.
    if speed_limit_target > best:
      best, source = speed_limit_target, RefSource.speedLimit

    self.reference_speed = best
    self.reference_source = source
    self._apply_patience()
    return best

  def _apply_patience(self) -> None:
    """Turn "how far over the limit did he ask to go" into the speed gain a pass has to be worth.

    See DEFAULT_PATIENCE. Called from BOTH exits of _reference_speed rather than after it, because
    the driver's own held speed returns early -- and a hold is the strongest statement of intent
    there is, so it is the last case that should fall through to a stale scale.

    The excess is measured against the RAW POSTED LIMIT, not SLA's limit-plus-offset. His offset is
    a standing preference that applies everywhere; going beyond it is the thing being detected.
    Measuring against the offset target would read his ordinary cruising as unhurried on every road
    and as hurried on none, which is the same as switching the feature off.
    """
    if self.patience <= 1.0 or self.posted_limit <= 0.0 or self.reference_speed <= 0.0:
      self.patience_scale = 1.0
    else:
      excess_mph = (self.reference_speed - self.posted_limit) * CV.MS_TO_MPH
      # Clamped at both ends: below the limit is not MORE patient than at it -- he may simply be
      # in traffic -- and there is nothing above unmodified.
      frac = min(1.0, max(0.0, excess_mph) / PATIENCE_FULL_EXCESS_MPH)
      # SNAPPED TO EXACTLY 1.0 AT THE TOP, and snapped on the RESULT rather than on frac, because
      # the epsilon does not come from the clamp -- 8 mph converted to m/s and back is 7.999999999,
      # so frac arrives just under 1 and 1.8 + (-0.8) * frac lands on 1.0000000000000002.
      #
      # That passes `patience_scale > 1.0`, which is the comparison deciding whether a pass he made
      # gets blamed on patience or on his deficit setting. A float epsilon at the knee would have
      # quietly corrupted the panel's recommendation at exactly the speeds he drives.
      scale = self.patience + (1.0 - self.patience) * frac
      self.patience_scale = 1.0 if scale <= 1.0 + 1e-6 else scale
    self.min_deficit_active_ms = self.min_deficit_ms * self.patience_scale

  def update(self, sm, v_cruise: float, long_enabled: bool, speed_limit_target: float = 0.0,
             posted_limit: float = 0.0) -> None:
    """Decide, then advance the dry run of the maneuver that decision would produce."""
    # THE FLAG IS CLEARED BEFORE _decide, NOT IN _reset_outputs. Gate refusals -- blind spot,
    # oncoming, geometry -- go through _reset_outputs too, and those are exactly the frames the
    # request must survive: asking only once a lane is clear would arrive after the ~4.5 s the gap
    # takes to reach. So only a return BEFORE the `spotted` check leaves this False.
    self._gap_pursuing = False
    self._decide(sm, v_cruise, long_enabled, speed_limit_target, posted_limit)
    self._update_gap_request()
    self._hold_suggestion()
    self._track_curve(sm, float(sm['carState'].vEgo))
    self._run_maneuver(sm['carState'])

  def _track_curve(self, sm, v_ego: float) -> None:
    """How hard the car is already cornering, and how bent it was when a pass was suggested.

    MEASUREMENT ONLY. Nothing gates on this, deliberately, and the reason is a specific one rather
    than caution: THE NUMBER THAT WOULD SET A THRESHOLD DOES NOT EXIST. From the road 2026-08-09,
    the concern is the retrofitted Edge PSCM being asked to add a lane change on top of a curve it
    is already working to hold -- and tools/bp_pscm_limit.py, which set out to find where that
    module stops holding a commanded angle, is kept in the tree marked "BROKEN AS AN ANSWER, KEPT
    AS A LESSON". It read latcontrol_angle's error, which under this fork's angle scheme is not the
    signal in the loop, and two settings defaults were changed on it and reverted.

    So this records what the road actually did at the moment of each suggestion. If it turns out
    suggestions never land in a meaningful curve, no gate is needed and the worry is answered for
    free. If they do, the distribution says where a threshold belongs -- which is the opposite of
    picking one and finding out afterwards.

    THE SAME QUANTITY SCC USES, on purpose: v_ego^2 * |curvature| off controlsState, which is what
    vision_controller.py computes for current_lat_acc. Its own thresholds are 1.3 entering a turn
    and 1.6 turning, and those are upstream's numbers with far more road under them than anything
    invented here -- so when this does get a threshold, that is the scale it should be read against.
    """
    try:
      self.lat_acc = v_ego * v_ego * abs(sm['controlsState'].curvature)
    except (KeyError, AttributeError):
      return
    if self.suggestion == Side.none or self.reason != Reason.passing:
      return
    self.suggested_lat_acc_max = max(self.suggested_lat_acc_max, self.lat_acc)
    # Coarse histogram against SCC's own scale, so the readout is comparable to the thresholds it
    # would eventually be judged by rather than to an arbitrary axis.
    for i, edge in enumerate((0.5, 1.0, 1.3, 1.6)):
      if self.lat_acc < edge:
        self._lat_acc_hist[i] += 1
        break
    else:
      self._lat_acc_hist[4] += 1

  def _update_gap_request(self) -> None:
    """Ask ICBM for a closer follow gap, or stop asking. Once per frame, after the decision.

    ICBM releases on SILENCE, so this is a lease reasserted every frame it is still wanted -- and
    the property that matters is that it stops, not that it starts. A latched request leaves the car
    following at gap 1 after passing assist has stopped wanting anything, which the driver never
    chose and would have no way to attribute.

    Blocked is not the same as not pursuing. A lane that is occupied right now is the normal case
    during an approach and the request stands through it; a lane that stays unavailable for
    GAP_GIVE_UP_S is a road where the pass is not going to happen, and trailing a slow car at gap 1
    there is cost with no benefit.
    """
    if not self._gap_pursuing:
      self.gap_request = 0
      self._gap_blocked_s = 0.0
      return
    if self.clear_side != Side.none:
      self._gap_blocked_s = 0.0
    else:
      self._gap_blocked_s += DT_MDL
    self.gap_request = GAP_WHILE_PASSING if self._gap_blocked_s < GAP_GIVE_UP_S else 0

  def _hold_suggestion(self) -> None:
    """Keep a standing suggestion alive through a brief dip in a non-safety gate.

    See SUGGESTION_HOLD_S for the measurement that forced this. In one sentence: the decision was
    correct and unusable, because four independent gates each hovered at their threshold and any
    one of them faltering for a single frame withdrew the whole thing. Median episode 0.10 s.

    ONLY EVER EXTENDS AN EXISTING SUGGESTION. It cannot create one, cannot change the side, and
    cannot make anything appear sooner -- the confirmation window still decides that, and a hold
    that could suggest on its own would be a second, hidden trigger.

    Only the three gates in HOLD_THROUGH may be held through. Everything else -- the driver
    taking over, the feature being switched off, anything about danger -- ends it at once.
    """
    if self.suggestion != Side.none:
      self._hold_s = 0.0
      self._held_side, self._held_reason = self.suggestion, self.reason
      self._held_trigger = self.trigger
      return

    if self._held_side == Side.none:
      return

    # Anything not on the allow list ends it now, and forgets the hold so it cannot resume.
    if self.blocked_by not in HOLD_THROUGH:
      self._held_side = Side.none
      self._hold_s = 0.0
      return

    self._hold_s += DT_MDL
    if self._hold_s >= SUGGESTION_HOLD_S:
      self._held_side = Side.none
      return

    # Still within the hold: restore what was being shown, and say so rather than reporting the
    # gate that momentarily objected -- blockedBy is what the drive summary counts, and recording a
    # block during a frame the driver was still being shown a suggestion would corrupt it.
    self.suggestion = self._held_side
    self.reason = self._held_reason
    self.trigger = self._held_trigger
    self.blocked_by = Blocked.none

  def _run_maneuver(self, CS) -> None:
    """Feed the dry run. See passing_maneuver.py -- this actuates nothing.

    Scoped to PASSING only. Keep-right is a different maneuver with different gates and a
    different urgency, and folding it in here would make the abort count -- the one number this
    produces -- mean two things at once.
    """
    # Runs on EVERY frame, including the ones where the gates above returned early. A pass that is
    # grinding is happening in the other lane, where the lead-based gates have nothing to say -- so
    # hanging this off the decision path would have measured only the crawls that began while a
    # fresh suggestion was still live, which is the subset least in need of measuring.
    # in_leftmost: no lane to our LEFT, the same term _track_lane_hog uses. See the note in
    # OvertakeProgress.update for why a slow pass only matters from the far left lane.
    self.overtake.update(CS.vEgo, self.adjacent.left, self.adjacent.right, self._settle_s,
                         self.since_driver_change_s, not self.left_geometry_ok)

    # Counted AFTER every gate has run, so blocked_by is final for this frame. Only while a
    # slower car is actually spotted -- an empty road is not evidence about anything -- and only
    # once the CONFIRMATION HAS COMPLETED. Before that, blocked_by reads nothingSlower, which here
    # means "still deciding" rather than "a gate stopped it"; counting those frames would have put
    # two seconds of ordinary confirmation at the top of every drive's list and buried the real
    # answer under it.
    # Clock and timeline first, and NOT gated on a pass being wanted. The events he narrates
    # include the ones that happened when nothing was warranted -- "it kept saying would be
    # changing right" on an empty road is exactly such an event, and gating this the way
    # wanted_seconds is gated would have thrown that away.
    self.elapsed_s += DT_MDL
    self._record_timeline()

    if self.lead_is_slow and self.approach_seconds >= self.persistence_s:
      self.wanted_seconds += DT_MDL
      key = int(self.blocked_by)
      self._block_seconds[key] = self._block_seconds.get(key, 0.0) + DT_MDL
    self._track_lane_hog()

    # Only once there is something worth keeping, so an idle commute cannot overwrite the drive
    # that actually produced numbers.
    if self.wanted_seconds > 0.0:
      self._save_drive_summary()

    override = self._driver_override(CS)

    # Keep-right signals when it has DECIDED, not when it first sees somewhere to go -- unlike
    # passing, where the whole point of signaling early is beating Ford's ACC to the brakes.
    # Nothing is being raced here: moving back over is never urgent, and a blinker lit through the
    # keep-right delay would be several seconds of telling traffic behind about a maneuver that
    # may not happen.
    kr_side = self.suggestion if self.reason == Reason.keepRight else Side.none
    kr_rear = self._must_abort(self.keep_right_maneuver.side)
    # too_slow HERE TOO, and this is the machine that produced the report -- "it said would be
    # changing RIGHT" while coming to a stop. Wiring it to the passing maneuver alone would have
    # fixed the half he did not hit.
    self.keep_right_maneuver.update(clear=kr_side, suggested=kr_side, confirming=False,
                                     confirmed=kr_side != Side.none, driver_override=override,
                                     collision_abort=kr_rear,
                                     too_slow=bool(CS.vEgo < self.min_speed_ms))

    confirmed = self.approach_seconds >= self.persistence_s
    self.maneuver.update(
      clear=self.clear_side,
      wanted=self.wanted_side,
      # See PassingManeuver.update. The detector refuses below this speed, but a refusal cannot
      # reach a committed crossing -- this can.
      too_slow=bool(CS.vEgo < self.min_speed_ms),
      # See PassingManeuver.update. Despite its name this is the LIVE per-frame value -- _acc_braking
      # recomputes it every update from accDecelRequest plus engine braking. Holds the crossing, not
      # the decision: ACC deceleration is what RELEASES the approach hold above.
      acc_braking=bool(self.acc_braking_at_decision),
      suggested=self.suggestion if self.reason == Reason.passing else Side.none,
      confirming=self.approach_seconds > 0.0 and not confirmed,
      confirmed=confirmed,
      # Exactly the inputs the detector already treats as the driver taking over. Reusing the same
      # test rather than restating it means the dry run cannot disagree with the gate above it.
      driver_override=override,
      # See PassingManeuver.update. Both are inert until self.actuating is true.
      actuating=self.actuating,
      settle_after_change_s=float(SETTLE_AFTER_CHANGE_S),
      # The narrow tier: what may reverse a crossing already begun, as opposed to merely refusing
      # to start one.
      collision_abort=self._must_abort(self.maneuver.side),
    )

  def _record_oncoming_refusal(self, on_the_left: bool) -> None:
    """Account for one frame of the oncoming veto. See oncomingSeenSeconds.

    Called from BOTH places that report oncomingLane, which is the whole reason it is a method.
    That veto has two returns -- an early one before the sign check, and again in the per-side
    priority chain -- and the early one is what fires in practice, so accounting written into the
    chain alone records nothing. That trap is documented in test_passing_assist.py and I walked
    into it anyway; a shared helper makes it impossible to feed only one.
    """
    side = self.adjacent.left if on_the_left else self.adjacent.right
    if side.oncoming_d_rel > 0:
      self._last_oncoming = (float(side.oncoming_d_rel), float(side.oncoming_y_rel),
                             float(side.oncoming_v_abs), bool(side.oncoming_edge_trusted))
    # `oncoming` is this frame's sighting; the memory outlives it by up to the full window.
    if side.oncoming:
      self.oncoming_seen_seconds += DT_MDL
    else:
      self.oncoming_remembered_seconds += DT_MDL

  def may_actuate(self, side: int) -> bool:
    """May a maneuver into THIS side command anything? Consult before every commanded output.

    PassingAssistActuate is the switch. This is the gate, and the distinction matters: the switch is
    the driver's stated intent, the gate is whether the car can currently back that intent with a
    sensor. A switch that were also the gate would mean turning it on before the hardware exists
    silently enables a lane change with no way to see what is arriving behind.

    REAR COVERAGE ON THE SIDE BEING MOVED INTO. Not RearApproach.available, which is left OR right
    -- the permissive combiner. With one sensor working that answers yes for both sides and would
    allow a move into the side with no coverage at all. Suggesting into it is acceptable, because
    the driver still looks; MOVING into it is the exact failure this module exists to prevent.

    Side.none answers False, so "no side chosen" can never be read as permission.
    """
    if not self.actuate_enabled:
      return False
    if side == Side.left:
      return self.rear.left.available
    if side == Side.right:
      return self.rear.right.available
    return False

  def _own_blinker(self) -> int:
    """The side WE are lighting the blinker for, or none.

    Answers none until this actuates, so nothing below changes today -- see `self.actuating`.

    EITHER dry run, not just the passing one. Keep-right signals too, and checking only `maneuver`
    left the identical self-abort bug in the other machine -- a returning-right maneuver would see
    its own blinker, call it driver input, and cancel itself. The fix was written twice and applied
    once, which is the failure mode `live_maneuver` exists to prevent.
    """
    if not self.actuating:
      return Side.none
    live, _ = self.live_maneuver
    return live.side if live.blinker_on else Side.none

  def _driver_override(self, CS) -> bool:
    """Is the DRIVER taking over -- as opposed to us watching our own blinker?

    THE BUG THIS EXISTS TO PREVENT is one the system would have inflicted on itself. The test used
    to be `leftBlinker or rightBlinker or brakePressed or steeringPressed`, which is exactly right
    while nothing here commands anything. The moment this lights its own blinker, that same test
    sees the signal, calls it driver input, and aborts the pass it just started -- every time,
    forever. ICBM already solved this for cruise buttons: what we commanded and what the driver did
    are trivially separable as long as you bother to subtract your own.

    Subtracting it also hands us the cancel gesture for free, and it is the one Ford uses on
    BlueCruise: signaling the OTHER way calls the maneuver off. That falls out rather than being
    added, because the other side is by definition not the side we are signaling.

    And signaling the SAME way no longer aborts, which is the half worth stating. Reaching for the
    stalk in the direction the car is already going is agreement -- the driver saying "yes, that
    one" -- and treating agreement as a takeover is how a system teaches you not to touch it.
    """
    ours = self._own_blinker()
    driver_left = bool(CS.leftBlinker) and ours != Side.left
    driver_right = bool(CS.rightBlinker) and ours != Side.right
    return bool(driver_left or driver_right or CS.brakePressed or CS.steeringPressed)

  def _must_abort(self, side: int) -> bool:
    """Is there something in that lane worth REVERSING a crossing for?

    Two things, and the second was missing until a whole-drive test walked into it. Something
    arriving fast behind is the obvious one. Traffic coming the OTHER WAY in the lane being crossed
    into is the more serious one, and the rule that gates cannot abort a committed crossing was
    swallowing it -- correctly for every other gate, catastrophically for this one. "A car cannot
    un-change lanes on a change of mind" is true; meeting someone head-on is not a change of mind.
    """
    if side == Side.left:
      rear, adjacent = self.rear.left, self.adjacent.left
    elif side == Side.right:
      rear, adjacent = self.rear.right, self.adjacent.right
    else:
      return False
    # The oncoming half is gated on the SETTING, and that deserves stating because it means a
    # convenience switch can disable a safety behavior. It is the right way round anyway: the one
    # reason to turn the oncoming veto off is that it false-fires -- which is exactly what was
    # reported on I-15 -- and an abort driven by phantom sightings would reverse real lane changes
    # mid-maneuver. Off must mean off, not off-except-when-it-matters-most.
    return rear.demands_abort or (self.oncoming_veto and adjacent.blocks_oncoming)

  @property
  def live_maneuver(self):
    """Whichever dry run is actually running, and what it is for.

    Only one can ever be: keep-right is evaluated solely on the frames where no pass is warranted.
    Passing wins a tie on principle rather than necessity -- if that assumption ever breaks, the
    more urgent maneuver should be the one on screen.
    """
    if self.maneuver.phase != Phase.idle:
      return self.maneuver, Reason.passing
    if self.keep_right_maneuver.phase != Phase.idle:
      return self.keep_right_maneuver, Reason.keepRight
    return self.maneuver, Reason.none

  def _decide(self, sm, v_cruise: float, long_enabled: bool, speed_limit_target: float = 0.0,
              posted_limit: float = 0.0) -> None:
    """
    Args:
      sm: SubMaster with carState, radarState, modelV2 and (BluePilot, Ford) carStateBP
      v_cruise: current set speed in m/s -- the speed we would be doing without this lead
      long_enabled: cruise engaged

    Publishes nothing itself; the planner copies the fields out. Gates are evaluated in order and
    the FIRST failure is recorded in blocked_by, so the log shows which one is actually binding
    rather than just that nothing happened.
    """
    self.update_params()
    self.frame += 1

    CS = sm['carState']
    self.last_v_ego = float(CS.vEgo)
    lead = sm['radarState'].leadOne

    self.posted_limit = float(posted_limit)
    v_cruise = self._reference_speed(CS, sm, v_cruise, speed_limit_target)

    # BLIS is read every cycle regardless of the gates below -- its behavior approaching a pass
    # is exactly what needs measuring, including on the frames where nothing is suggested.
    self.left_blindspot = bool(CS.leftBlindspot)
    self.right_blindspot = bool(CS.rightBlindspot)

    # carStateBP is BluePilot-and-Ford only. Availability comes from the message's own
    # dataAvailable flags rather than SubMaster liveness, because that is the flag that actually
    # answers the question: on this car BLIS stays unavailable until the canbox routes
    # Side_Detect_L/R_Stat from MS-CAN onto the bus openpilot reads.
    # Where we are, recorded with every decision. See the capnp comment: this is the candidate
    # divided-highway gate, logged before it is trusted.
    try:
      self.road_name = str(sm['liveMapDataSP'].roadName or "")
    except (KeyError, AttributeError):
      self.road_name = ""

    # NOT `if 'carStateBP' in sm`. SubMaster defines __getitem__ and no __contains__, so `in`
    # falls back to the old sequence-iteration protocol and calls sm[0] -- which raises
    # KeyError: 0 out of its internal dict. Catching the lookup is the only correct membership
    # test here, and it is what a plain dict in a test fixture will never tell you.
    try:
      car_state_bp = sm['carStateBP']
    except KeyError:
      car_state_bp = None
    self.rear.update(sm)
    # Both sides, because the maneuver timings in passing_maneuver.py are properties of the whole
    # sequence rather than of a side. may_actuate() is what decides whether a PARTICULAR move is
    # allowed, and it is the one that must be consulted before anything is commanded.
    # BOTH sides. The stand-down timings this feeds are properties of the sequence, not of a side,
    # and a flag that flipped with whichever side happened to be chosen would make them ambiguous.
    # Requiring full coverage is also not a practical restriction: one rear radar covers both.
    self.actuating = self.may_actuate(Side.left) and self.may_actuate(Side.right)
    # Runs every cycle, before any gate. What the next lane over is doing is worth logging on the
    # frames where nothing is suggested too -- that is how the band and the debounce get fitted.
    if self.adjacent_enabled:
      self.adjacent.update(sm, float(CS.vEgo), self.max_distance_m,
                           dt=DT_MDL, memory_s=self.oncoming_memory_s,
                           strict=self.strict_two_way)
    else:
      self.adjacent.reset()
    self._blindspot(car_state_bp)
    self._acc_braking(car_state_bp)
    self._traffic_signs(car_state_bp)
    self._geometry(sm['modelV2'])

    # Advances every cycle regardless of the gates below, so it measures real elapsed time rather
    # than time-spent-in-a-particular-branch.
    self._settle_s = min(self._settle_s + DT_MDL, 1e3)  # capped; only the threshold matters
    self.since_driver_change_s = min(self.since_driver_change_s + DT_MDL, 1e3)

    # The longest either lane has gone unpassed. Only counts once a side has actually been passed
    # at least once -- see tick_overtaken: an unvisited lane reads 0, which is "never", not "quiet
    # for zero seconds", and taking a max over it would report every empty road as busy.
    for _side in (self.adjacent.left, self.adjacent.right):
      if _side.overtaken_count:
        self.overtaken_quietest_s = max(self.overtaken_quietest_s, _side.overtaken_seconds)

    # Before every gate below, so the stand-down keeps counting down on the frames they return
    # early on. It used to sit after the speed gate, which meant slowing below the minimum froze
    # it -- and slowing down is exactly what taking an exit involves, so the pause would have been
    # waiting on the driveway instead of expiring on the ramp.
    self._track_driver_change(CS)

    self._update_enable(self._lka_toggle(car_state_bp))

    # OFF beats every other gate, and reports as `disabled` rather than as something more specific.
    # He turned it off; a panel saying "no lane to move into" would misrepresent why it is silent.
    if not self.enabled:
      self._clear_confirmation()
      self.keep_right_seconds = 0.0
      self._reset_outputs(Blocked.disabled)
      return

    if not long_enabled:
      self._clear_confirmation()
      self.keep_right_seconds = 0.0
      self._reset_outputs(Blocked.notEngaged)
      return

    if CS.vEgo < self.min_speed_ms:
      self._clear_confirmation()
      self.keep_right_seconds = 0.0
      self._reset_outputs(Blocked.tooSlow)
      return

    # The driver is already doing something about it. Suggesting a pass mid-maneuver is noise,
    # and it would corrupt the confirmation timer for the far more interesting no-input case.
    # Standing down after the driver's own lane change. Checked before driverActive so the reason
    # on screen is the useful one -- "just changed lanes" explains a silence that outlasts the
    # blinker, where "you are driving" would look like it had simply not noticed the stalk go off.
    if self.driver_change_standdown > 0.0:
      self._clear_confirmation()
      self.keep_right_seconds = 0.0
      self._reset_outputs(Blocked.driverChangedLanes)
      return

    if self._driver_override(CS):
      self._clear_confirmation()
      self.keep_right_seconds = 0.0
      self._reset_outputs(Blocked.driverActive)
      return

    in_grace = False
    if not lead.status:
      # A single missed radar return is the same car, not a different situation. Free inside the
      # grace window, decaying after it -- exactly as for the range bound above.
      self._lead_gap()
      self.has_lead = False
      self.lead_ttc = NO_TTC_S
      if self._lead_gap_s > LEAD_GAP_GRACE_S or self.approach_seconds == 0.0:
        self._reset_outputs(Blocked.noLead)
        self._keep_right()
        return
      # Inside the window with a live confirmation: carry on. Absorbing a dropped return in the
      # timer but NOT in the verdict would be the worst of both -- the confirmation survives while
      # the suggestion blinks off for a frame, which is exactly what aborts a signaling sequence.
      # Only the lead's own numbers are stale here, by at most LEAD_GAP_GRACE_S; every gate below
      # is re-evaluated live.
      in_grace = True
    else:
      self._lead_state(lead, v_cruise)

    spotted = self.lead_is_slow if in_grace else self._should_pass(lead, v_cruise, sm['modelV2'])

    # FIRST frame of this approach on which ACC asked for deceleration, recorded as a distance.
    # Latched: what matters is where it started, not that it is still going.
    if spotted and self.acc_braking_at_decision and self.acc_onset_d_rel == 0.0:
      self.acc_onset_d_rel = float(self.lead_d_rel)
      self.acc_onset_max = max(self.acc_onset_max, self.acc_onset_d_rel)

    # They are braking hard. Wait and see -- see LEAD_BRAKING_MS2. Checked before the close-in
    # hold because it is the more specific reason and the one a driver would recognize: "that car
    # is stopping" beats "still closing" when both are true.
    self.lead_braking_hold = bool(spotted and self.lead_braking_enabled and
                                  self._lead_braking_s < LEAD_BRAKING_HOLD_S)
    if self.lead_braking_hold:
      self.keep_right_seconds = 0.0
      self._reset_outputs(Blocked.leadBraking)
      return

    # Resolve Auto against what this car's ACC has actually been measured to do. Falls back to no
    # hold when nothing has been measured yet, which is the honest answer rather than a guess.
    if self.min_approach_setting < 0:
      self.min_approach_m = (self.acc_onset_max + AUTO_APPROACH_MARGIN_M) if self.acc_onset_max > 0 else 0.0
    else:
      self.min_approach_m = self.min_approach_setting

    # Hold off while we are still a long way back -- see DEFAULT_MIN_APPROACH_M. The confirmation
    # timer keeps running underneath, so when the distance is reached the maneuver starts at once
    # rather than beginning a fresh two-second wait.
    #
    # The ACC override is the whole point: any deceleration request, at any distance, abandons the
    # hold immediately. Waiting is only free while it is costing nothing.
    self.closing_in = bool(spotted and self.min_approach_m > 0.0 and
                           self.lead_d_rel > self.min_approach_m and
                           not self.acc_braking_at_decision and not self.acc_precharge_at_decision)
    if self.closing_in:
      self.keep_right_seconds = 0.0
      self._reset_outputs(Blocked.closingIn)
      return

    if not spotted:
      self._reset_outputs(Blocked.nothingSlower)
      self._keep_right()
      return

    # ASK FOR THE GAP HERE, past the approach hold and every reason to want nothing. `spotted` means
    # a slower vehicle is confirmed ahead, which is the earliest honest moment to want a closer gap
    # -- and early is the requirement: reaching a gap costs up to ~4.5 s of confirmed toggle steps.
    #
    # Independent of whether a LANE is available, deliberately. Closing the gap is about the car
    # ahead, not about where we would go, and waiting for the geometry gates would put the request
    # after the moment it exists for. What it is not independent of is time: if nothing clears for
    # GAP_GIVE_UP_S the request drops, because trailing a slow car at gap 1 on a road that will
    # never allow a pass is closer than he chose to drive for no benefit.
    self._gap_pursuing = True

    # trigger now reports the OUTCOME rather than the mechanism: did the suggestion land before
    # Ford's ACC started braking for a lead we were always going to pass, or after. That is the
    # only distinction worth recording, and it is measured rather than inferred.
    pending_trigger = Trigger.heldUp if self.acc_braking_at_decision else Trigger.approaching

    # Past here a pass is warranted, so we are not sitting in a lane we should be leaving.
    self.keep_right_seconds = 0.0

    if not (self.left_geometry_ok or self.right_geometry_ok):
      # HERE, not in _geometry. Tallying on every frame of every road meant a commute's worth of
      # town driving -- where there genuinely is no passing lane and no pass is wanted -- swamped
      # the handful of highway frames the question is about, and the summary would have named
      # whichever term refuses a residential street. This branch is reached only when a pass IS
      # warranted and geometry is the thing standing in the way, which is exactly the question.
      self._record_refusal()
      # THE FLICKER'S OWN PATH, and the one that made the first debounce useless: geometry failing
      # on both sides returns here, before wanted_side is computed below. So the debounce has to be
      # applied here too, with the raw answer this frame actually gives -- none.
      self.wanted_side = self._debounce_wanted(Side.none)
      self._reset_outputs(Blocked.noLaneAvailable, keep_wanted=True)
      return

    # THE ROAD IS BENDING. See DEFAULT_MAX_PASS_LAT_ACC. Checked here -- past "a pass is warranted"
    # and past geometry, before the lane-choosing gates -- because it is a fact about the ROAD and
    # not about either lane, so naming a side for it would be arbitrary.
    #
    # Suggestion only. A crossing already underway is left alone: a car cannot un-change lanes
    # because the road started bending, and abandoning one mid-corner is worse than finishing it.
    if self.max_pass_lat_acc > 0.0 and self.lat_acc > self.max_pass_lat_acc:
      self._reset_outputs(Blocked.inCurve)
      return

    # Two-way road. Evaluated before the sign veto, and it is the one this whole design was waiting
    # on: geometry cannot tell an oncoming lane from a passing lane, so until now every gate below
    # would happily clear a pass into head-on traffic on any two-lane road.
    #
    # PER SIDE, not per road, and that is what keeps this from costing more than it should. On a
    # four-lane two-way arterial sitting in the left lane, the oncoming lane is one over to the
    # LEFT and a perfectly ordinary through lane is one over to the RIGHT. A whole-road veto gives
    # up on both; this gives up only on the side the opposing traffic is actually on.
    #
    # On a true two-lane road it costs nothing extra: there is no lane to the right, so
    # right_geometry_ok collapses to the shoulder and nothing is suggested there anyway.
    onc_left = self.oncoming_veto and self.adjacent.left.blocks_oncoming
    onc_right = self.oncoming_veto and self.adjacent.right.blocks_oncoming

    # If oncoming rules out every side geometry offered, report it NOW, ahead of the sign veto.
    # That is the two-lane case, where the two are true together constantly and the road fact is
    # the more useful of the two: a no-passing zone explains a passing silence, a two-way road
    # explains the whole road. Reaching this line means geometry offered something, so oncoming is
    # necessarily what took it away.
    if not ((self.left_geometry_ok and not onc_left) or (self.right_geometry_ok and not onc_right)):
      self._record_oncoming_refusal(onc_left)
      self._reset_outputs(Blocked.oncomingLane)
      return

    # TSR veto before the blind-spot check: a no-overtaking zone makes the blind spot irrelevant,
    # and ordering it this way means blockedBy distinguishes "would have been clear but the sign
    # said no" from "the sign was silent and BLIS stopped it".
    if self.overtake_restricted:
      self._reset_outputs(Blocked.overtakeRestricted)
      return

    # Rear approach. Sits here -- after geometry and the sign veto, before the side is chosen --
    # because it is per-side: a car closing on the left must not veto a pass on the right.
    #
    # An UNAVAILABLE side does not block. That is the honest behavior while no rear sensor is
    # fitted (blocking would disable the feature outright and hide the real reason), and it is why
    # rearAvailable is published and shown: a suggestion made with no rear sensing must be legible
    # as such rather than pass for a checked one. When a source is fitted this becomes a real gate
    # with no code change here.
    # Adjacent lane occupancy, from the front radar's off-path tracks. Last of the per-side gates
    # and deliberately so: it is the only one that can be wrong in a merely wasteful direction. The
    # blind spot and rear approach answer "is this move unsafe"; this answers "is it worth making",
    # so it must not be able to mask either of them in blockedBy.
    #
    # Beat the LEAD, not our own set speed. We are not asking whether the other lane is fast, we are
    # asking whether it is faster than what we are stuck behind -- a queue crawling at 45 is still
    # worth moving into if the lead is doing 40 and we want 70. The margin is the same deficit that
    # decided the pass was worth wanting, so one knob governs both halves of the judgment.
    adj_left = self.adjacent.left.blocks_move(self.lead_v_lead, self.min_deficit_active_ms, CS.vEgo)
    adj_right = self.adjacent.right.blocks_move(self.lead_v_lead, self.min_deficit_active_ms, CS.vEgo)

    # SIGNAL FIRST, THEN CHECK. His design, and what production systems do -- Super Cruise holds in
    # lane showing "looking for an opening" with the signal already up, BlueCruise gives up after
    # about ten seconds and says "not possible". Both are driver-initiated; this one decides for
    # itself, which is the argument for the tighter window rather than for a different shape.
    # NOT geometry alone. Measured on the 2026-08-09 I-15 drive: entering `signaling` on geometry
    # produced 47 aborts in 13 minutes, against 29 the drive before. Every one of those is a
    # five-second blinker episode once a control is wired, on a road where nothing was wrong.
    #
    # The split that fixes it is his own wording -- "check blind spots and radar and all of that
    # before making the change". Those are the gates that can plausibly change their mind inside the
    # window: a car moving out of the blind spot, something behind finishing its pass. Waiting on
    # those with the signal up is the whole point.
    #
    # The others describe a SITUATION rather than a moment, and no amount of waiting resolves them:
    # a lane full of traffic no faster than our lead is still full five seconds later, and the
    # oncoming veto is a ninety second memory by construction. Signalling into either is a promise
    # made against something that was never going to move.
    want_left = self.left_geometry_ok and not onc_left and not adj_left
    want_right = self.right_geometry_ok and not onc_right and not adj_right
    raw_wanted = Side.left if want_left else Side.right if want_right else Side.none
    self.wanted_side = self._debounce_wanted(raw_wanted)

    left_ok = (self.left_geometry_ok and not onc_left and not self.left_blindspot and
               not self.rear.left.blocks_lane_change and not adj_left)
    right_ok = (self.right_geometry_ok and not onc_right and not self.right_blindspot and
                not self.rear.right.blocks_lane_change and not adj_right)

    if not (left_ok or right_ok):
      # Name the gate that actually decided it, most severe first. Oncoming outranks everything:
      # it is the only one here about a dangerous maneuver rather than a wasted one, and it
      # explains a SUSTAINED silence where the others explain a passing one -- a driver reading
      # "two-way road" understands the feature is off for this whole road.
      if (self.left_geometry_ok and onc_left) or (self.right_geometry_ok and onc_right):
        blocked = Blocked.oncomingLane
        self._record_oncoming_refusal(onc_left)
      elif ((self.left_geometry_ok and not self.left_blindspot and self.rear.left.blocks_lane_change) or
            (self.right_geometry_ok and not self.right_blindspot and self.rear.right.blocks_lane_change)):
        blocked = Blocked.rearApproaching
      elif ((self.left_geometry_ok and not self.left_blindspot and adj_left) or
            (self.right_geometry_ok and not self.right_blindspot and adj_right)):
        blocked = Blocked.adjacentSlow
      else:
        blocked = Blocked.blindspotOccupied
      # keep_wanted: see _debounce_wanted. wanted_side was already set from this frame's geometry
      # just above, and this branch is the gate saying no -- which is the flicker case.
      self._reset_outputs(blocked, keep_wanted=True)
      # AND PUT IT BACK, because this is the one path it has to survive. _reset_outputs clears
      # wanted_side, which is right for every early return above -- those mean no pass is warranted
      # at all, and a stale value there lit the blinker during a keep-right. THIS branch means the
      # opposite: a pass IS warranted and a gate is what is stopping it, which is precisely when the
      # signal should be up waiting. Clearing it here made the whole signal-first change a no-op on
      # the only path that matters.
      self.wanted_side = Side.left if want_left else Side.right if want_right else Side.none
      return

    # Left is preferred where both are available: passing on the right is the wrong default, and
    # on a divided highway the right side being "available" usually means a slower lane or an
    # exit-only lane rather than somewhere to pass.
    self.clear_side = Side.left if left_ok else Side.right

    # The confirmation gates COMMITTING, not spotting. Everything above has already run, so the
    # lane is known clear and the blinker is already on -- the two clocks overlap rather than
    # stacking into a four second wait for a maneuver wanted immediately.
    if self.approach_seconds < self.persistence_s:
      self.blocked_by = Blocked.nothingSlower
      self.suggestion = Side.none
      self.reason = Reason.none
      self.trigger = Trigger.none
      return

    self.suggestion = self.clear_side
    self.blocked_by = Blocked.none
    self.reason = Reason.passing
    self.trigger = pending_trigger
    self._settle_s = 0.0

  def _keep_right(self) -> None:
    """FusionPilot: "keep right except to pass", the mirror of the passing question.

    Evaluated ONLY on the paths where no pass is warranted -- no lead, or a lead that is not
    holding us back. That ordering is the whole design: if a pass is on, we are out here for a
    reason and should not be told to move over mid-overtake.

    A lane existing to the right is the entire positive signal, and it is a decent one: on a
    two-lane-each-way highway, rightGeometryOk collapses to the shoulder once you ARE in the right
    lane, so the suggestion stops on its own without needing to know which lane we occupy.

    What this cannot see, and why it stays observation-only: an exit-only or merge lane is
    geometrically identical to a through lane, so "move right" could mean "take the exit". The
    same modelV2 limitation that cannot tell an oncoming lane from a passing lane applies here,
    and phase 1 exists to measure how often it bites.
    """
    # Do not reverse a pass we just suggested. This is what stops a three-lane road with a slow
    # left lane turning into a weave.
    if self._settle_s < self.settle_time_s:
      self.keep_right_seconds = 0.0
      return

    if not self.keep_right_enabled or not self.right_geometry_ok:
      self.keep_right_seconds = 0.0
      return

    # The road opening up ahead means an exit, on-ramp or pullout, and none of those is a lane to
    # settle into. Unlike the outermost rule this works on a two-lane road, because it asks what
    # the lane DOES rather than merely whether another lane exists beyond it.
    if self.right_widening:
      self.keep_right_seconds = 0.0
      return

    # Blind spot is a hard gate here, unlike geometry: moving into an occupied lane is the failure
    # mode, and returning right is never urgent enough to justify acting on stale evidence.
    # Resetting here is what makes the delay below mean "time since the blind spot went clear"
    # rather than "time since a lane appeared". That is the driver's own cue -- wait for the lamp
    # to go out -- and the delay on top of it lands nearer the textbook "both headlights in the
    # mirror", which is a little later. One timer, not two: an extra margin stage before this one
    # would double-count the same wait.
    if self.right_blindspot or self.rear.right.blocks_lane_change:
      self.keep_right_seconds = 0.0
      return

    # The oncoming gate applies here too, and it was missed the first time round -- the pass path
    # got it and this one did not, which is precisely the ordering bug the rear-approach interface
    # was designed early to avoid.
    #
    # Rare but real, and Utah has the road: a reversible flex lane can put opposing traffic in the
    # lane to our RIGHT, and 5400 South runs three of them. Keeping right into that would be the
    # worst suggestion this system could make, and nothing else in this function would have stopped
    # it -- the blind spot only lights once they are already alongside.
    #
    # Costs nothing on an ordinary two-way road: opposing traffic there is on the left, so
    # right.blocks_oncoming stays false and keep-right works normally.
    if self.oncoming_veto and self.adjacent.right.blocks_oncoming:
      self.keep_right_seconds = 0.0
      return

    # Do not move over behind a car we would immediately want to pass. "Keep right except to pass"
    # assumes the right lane is moving; dropping in behind traffic slow enough to trip the passing
    # threshold buys a pair of lane changes and no progress, and does it at exactly the moment the
    # settle timer is expired and least able to stop the second one.
    #
    # Expressed as the passing threshold read backwards -- slower than the set speed by the deficit
    # margin -- so the two behaviors cannot disagree about what "slow" means.
    if self.adjacent.right.blocks_move(self.reference_speed - self.min_deficit_active_ms, 0.0,
                                       self.last_v_ego):
      self.keep_right_seconds = 0.0
      return

    self.keep_right_seconds += DT_MDL

    # The lane also has to have BEEN there a while -- the owner's own exit test, and a better one
    # than road widening because it needs no guess about what the edge is doing ahead. An exit lane
    # appears; a through lane has been beside us for miles.
    #
    # This is deliberately NOT one of the resets above. Every gate up there is "the situation is
    # unsafe right now", so restarting the clear-lane clock is the whole point of them. A young
    # lane is not unsafe, it is merely unproven, and zeroing the clock here would chain the two
    # waits end to end: 15 s of age and THEN 10 s of clear, 25 s in total, by which time the
    # suggestion is stale. Run concurrently, both clocks start when the lane appears and the wait
    # is max(15, 10) = 15 s, which is what the two numbers on the settings screen say.
    #
    # Failing safe in every direction: a lane the model briefly loses comes back looking new and
    # costs a few quiet seconds, nothing more.
    if self.right_lane_age_s < self.min_lane_age_s:
      return

    if self.keep_right_seconds >= self.keep_right_delay_s:
      self.suggestion = Side.right
      self.blocked_by = Blocked.none
      self.reason = Reason.keepRight

  def publish(self, passingAssist) -> None:
    """Copy this observer's state onto the capnp message.

    Lives here rather than in the planner deliberately. It is forty lines of mechanical field
    copying that belong to this feature, and every one of them sitting in an upstream file is a
    merge conflict paid on every future sunnypilot rebase, forever. The planner keeps one call.

    Takes the sub-message rather than the whole plan so it cannot reach anything else.
    """
    pa = self
    passingAssist.suggestion = pa.suggestion
    passingAssist.blockedBy = pa.blocked_by
    # One timer now. The field keeps its name so older logs stay comparable.
    passingAssist.confirmSeconds = float(pa.approach_seconds)
    passingAssist.hasLead = pa.has_lead
    passingAssist.leadDRel = float(pa.lead_d_rel)
    passingAssist.leadVLead = float(pa.lead_v_lead)
    passingAssist.speedDeficit = float(pa.speed_deficit)
    passingAssist.leftLineProb = float(pa.left_line_prob)
    passingAssist.rightLineProb = float(pa.right_line_prob)
    passingAssist.leftEdgeGap = float(pa.left_edge_gap)
    passingAssist.rightEdgeGap = float(pa.right_edge_gap)
    passingAssist.leftLaneWidth = float(pa.left_lane_width)
    passingAssist.rightLaneWidth = float(pa.right_lane_width)
    passingAssist.leftEdgeBeyond = float(pa.left_edge_beyond)
    geo_term, geo_value, geo_share = pa.geo_refusal
    passingAssist.geoRefusedBy = int(geo_term)
    passingAssist.geoRefusedValue = float(geo_value)
    passingAssist.geoRefusedShare = float(geo_share)
    passingAssist.geoLoosenTo = float(pa.geo_refusal_loosen_to)
    passingAssist.rightEdgeBeyond = float(pa.right_edge_beyond)
    passingAssist.leftEdgeStd = float(min(pa.left_edge_std, 1e3))
    passingAssist.rightEdgeStd = float(min(pa.right_edge_std, 1e3))
    passingAssist.leftGeometryOk = pa.left_geometry_ok
    passingAssist.rightGeometryOk = pa.right_geometry_ok
    passingAssist.leftBlindspot = pa.left_blindspot
    passingAssist.rightBlindspot = pa.right_blindspot
    passingAssist.blindspotAvailable = pa.blindspot_available
    passingAssist.overtakeRestricted = pa.overtake_restricted
    passingAssist.overtakeMsg = pa.overtake_msg
    passingAssist.overtakeStatus = pa.overtake_status
    passingAssist.tsrAvailable = pa.tsr_available
    passingAssist.reason = pa.reason
    passingAssist.keepRightSeconds = float(pa.keep_right_seconds)
    passingAssist.roadName = pa.road_name
    for dest, side in ((passingAssist.rearLeft, pa.rear.left), (passingAssist.rearRight, pa.rear.right)):
      dest.available = side.available
      dest.detected = side.detected
      dest.closing = side.closing
      dest.dRel = float(side.d_rel)
      dest.vRel = float(side.v_rel)
      dest.ttc = float(side.ttc)
      dest.source = side.source
    passingAssist.rightWideningM = float(pa.right_widening_m)
    passingAssist.rightWidening = pa.right_widening
    passingAssist.trigger = pa.trigger
    passingAssist.leadTtc = float(pa.lead_ttc)
    passingAssist.approachSeconds = float(pa.approach_seconds)
    passingAssist.accBrakingAtDecision = pa.acc_braking_at_decision
    passingAssist.accBrakingAvailable = pa.acc_braking_available
    passingAssist.accPrechargeAtDecision = pa.acc_precharge_at_decision
    passingAssist.accBrakingOnsetDRel = float(pa.acc_onset_d_rel)
    passingAssist.accBrakingOnsetMax = float(pa.acc_onset_max)
    top_key, top_share = pa.top_blocked
    passingAssist.wantedSeconds = float(pa.wanted_seconds)
    passingAssist.hogSeconds = float(pa.hog_seconds)
    passingAssist.hogCount = min(pa.hog_count, 65535)
    passingAssist.topBlockedBy = top_key
    passingAssist.topBlockedShare = float(top_share)
    passingAssist.clearShare = float(pa.clear_share)

    passingAssist.crawlSeconds = float(pa.overtake.crawl_seconds)
    passingAssist.crawlLongestSeconds = float(pa.overtake.crawl_longest)
    passingAssist.crawlEvents = min(pa.overtake.crawl_events, 65535)
    passingAssist.crawlSide = pa.overtake.crawl_side
    passingAssist.crawlAfterSuggestion = pa.overtake.crawl_after_suggestion
    passingAssist.leadAccel = float(pa.lead_accel)
    passingAssist.leadBrakingHold = pa.lead_braking_hold
    # suspendedSeconds is retired -- the LKA button is an on/off now, not a countdown. The capnp
    # field stays because an ordinal cannot be reused; it publishes zero forever.
    passingAssist.suspendedSeconds = 0.0
    passingAssist.referenceSpeed = float(pa.reference_speed)
    passingAssist.referenceSource = pa.reference_source

    # The dry run. See passing_maneuver.py.
    live, live_reason = pa.live_maneuver
    passingAssist.maneuver = live.phase
    passingAssist.maneuverSeconds = float(live.phase_seconds)
    passingAssist.maneuverSide = live.side
    # See `actuating` in custom.capnp. The car side reads blinkerWouldBeOn and maneuverSide, which
    # the dry run publishes on every drive; this is what separates a command from a description.
    passingAssist.actuating = bool(pa.actuating)
    passingAssist.desireOk = bool(live.desire_ok)
    passingAssist.maneuverReason = live_reason
    passingAssist.blinkerWouldBeOn = live.blinker_on
    passingAssist.steeringWouldBeActive = live.steering_active
    passingAssist.keepRightAborts = min(pa.keep_right_maneuver.aborts, 65535)
    passingAssist.minApproachActive = float(pa.min_approach_m)
    passingAssist.minDeficitActive = float(pa.min_deficit_ms * CV.MS_TO_MPH)
    passingAssist.patienceScale = float(pa.patience_scale)
    passingAssist.patienceMissed = int(pa.patience_missed)
    passingAssist.driverPasses = min(pa.driver_passes, 65535)
    passingAssist.driverPassesAgreed = min(pa.driver_passes_agreed, 65535)
    passingAssist.driverPassLeadSeconds = float(pa.driver_pass_lead_s)
    passingAssist.driverPassMissReason = pa.driver_pass_miss_reason
    passingAssist.driverPassesEligible = min(pa.driver_passes_eligible, 65535)
    passingAssist.missedDeficitMph = float(pa.missed_deficit_mph)
    passingAssist.oncomingSeenSeconds = float(pa.oncoming_seen_seconds)
    passingAssist.oncomingRememberedSeconds = float(pa.oncoming_remembered_seconds)
    passingAssist.suggestionsMade = min(pa.suggestions_made, 65535)
    passingAssist.suggestionsTaken = min(pa.suggestions_taken, 65535)
    passingAssist.longestIgnoredSeconds = float(pa.longest_ignored)
    life_drives, life_passes, life_agreed = pa.lifetime
    passingAssist.lifetimeDrives = min(life_drives, 65535)
    passingAssist.lifetimePasses = min(life_passes, 65535)
    passingAssist.lifetimeAgreed = min(life_agreed, 65535)
    passingAssist.maneuverStandDown = float(max(pa.maneuver.standdown_remaining,
                                                 pa.keep_right_maneuver.standdown_remaining))
    # Whichever of the two is actually holding the clock is the one whose reason gets reported.
    standing = (pa.keep_right_maneuver
                if pa.keep_right_maneuver.standdown_remaining > pa.maneuver.standdown_remaining
                else pa.maneuver)
    passingAssist.maneuverStandDownComplete = bool(standing.standdown_after_completion)
    passingAssist.driverChangeStandDown = float(pa.driver_change_standdown)
    passingAssist.driverChangeWasExit = pa.driver_change_was_exit
    passingAssist.emergencyAborts = min(
      pa.maneuver.emergency_aborts + pa.keep_right_maneuver.emergency_aborts, 65535)
    # Saturates rather than wraps: a UInt16 rolling over to 0 would read as a clean drive, which is
    # the exact opposite of what a huge abort count means.
    passingAssist.maneuverAborts = min(pa.maneuver.aborts, 65535)
    passingAssist.leadRadarConfirmed = pa.lead_radar_confirmed
    passingAssist.leadModelProb = float(pa.lead_model_prob)
    for dest, side in ((passingAssist.adjacentLeft, pa.adjacent.left),
                       (passingAssist.adjacentRight, pa.adjacent.right)):
      dest.available = side.available
      dest.occupied = side.occupied
      dest.dRel = float(side.d_rel)
      dest.yRel = float(side.y_rel)
      dest.vRel = float(side.v_rel)
      dest.vAbs = float(side.v_abs)
      dest.oncoming = side.oncoming
      dest.oncomingCorroborated = side.oncoming_corroborated
      dest.oncomingDRel = float(side.oncoming_d_rel)
      dest.oncomingVAbs = float(side.oncoming_v_abs)
      dest.oncomingYRel = float(side.oncoming_y_rel)
      dest.oncomingEdgeTrusted = bool(side.oncoming_edge_trusted)
      dest.oncomingAdjacent = side.oncoming_adjacent_seconds > 0.0
      dest.oncomingSeconds = float(side.oncoming_seconds)
      dest.sameDirectionRecent = side.same_direction_recent
      dest.overtakenSeconds = float(side.overtaken_seconds)
      dest.overtakenCount = min(side.overtaken_count, 65535)
      dest.overtakenVAbs = float(side.overtaken_v_abs)
    passingAssist.oncomingAnySide = pa.adjacent.oncoming_any_side
    passingAssist.oncomingSecondsLeft = float(pa.adjacent.oncoming_seconds_left)
    passingAssist.oncomingSeen = pa.adjacent.oncoming_seen
