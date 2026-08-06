"""
BluePilot: stationary bench test for turn-signal actuation on Ford.

STOP HERE. DO NOT ASK HIM TO TEST THIS AGAIN -- 2026-08-06.
=========================================================
"I'm really getting tired of testing these blinkers for you every time... this is driving me mad
testing this every single time and not working."

Four fixes were attempted across two days -- a guard derived from the observed on-time, a disarm
retry, a stall watchdog, and a lamp-settle gate. Every one was a PACING fix. Every one was plausible,
partially right, and did not fix it. The counts stayed erratic: 6, then 1+4, then 4+2, then 0.

The cause is not pacing, and this file said so on 2026-08-04 before any of them were written:

  BO_ 131 Steering_Data_FD1: 8 GWM        <- the GATEWAY sends this frame
   SG_ TurnLghtSwtch_D_Stat ...           <- the signal we write into it

We are writing the driver's STALK POSITION onto a frame the gateway is transmitting ten times a
second with the real position, which is off. The body module sees a switch flickering between our
value and the gateway's depending on which frame landed last. No send schedule wins that, because
the contention is the mechanism rather than a symptom of bad timing -- and the "break then more
blinks" he reports is the stall watchdog recovering from exactly that.

There is no lamp command on this bus either. TurnLghtLeft_D_Rq and TurnLghtRight_D_Rq are on
BO_ 947 BodyInfo_3_FD1, also sent by GWM, addressed OUTWARD to CMR_DSMC and IPMA_ADAS. Nothing on
the powertrain bus asks the BCM for a lamp; those signals are the gateway reporting what the BCM
already decided.

WHAT IS ACTUALLY PROVEN, and it is not nothing: one commanded frame produces exactly one flash, the
lamp mirrors frames one for one and latches nothing, and his flasher runs at 760 ms. That is the
whole mechanism, established here, and it stands.

WHERE IT GOES NEXT -- AND IT IS NOT THE CANBOX. Researched properly 2026-08-06, after saying it
probably was.

The chain is: multifunction switch (a discrete input) -> SCCM -> SCCM reports TurnLghtSwtch_D_Stat
on HS-CAN -> gateway relays -> BCM lights the lamp and runs its own flash pattern. The SCCM is the
only legitimate originator of that signal, and everything else on any bus is a relay or a status
report of a decision already made.

  * There is no lamp COMMAND anywhere in this DBC. Five turn-lamp signals exist in the whole file,
    on two messages, both sent by GWM: the switch position on 131, and TurnLghtLeft/Right_D_Rq plus
    the two On_B_Stat on 947, addressed OUTWARD to CMR_DSMC and IPMA_ADAS.
  * The best public Ford MS-CAN reverse engineering (roncapat/Ford-Fiesta-MK5-MS-CAN-bus) documents
    turn signals as STATUS only -- 0x265 bits in byte 1 -- and no command message. A canbox on the
    body bus would be downstream of the decision, reading what the BCM already did.
  * AND THE CANBOX WOULD NOT FIX THE HALF THAT MATTERS ANYWAY. desire_helper gates the entire
    lane-change state machine on `carstate.leftBlinker != carstate.rightBlinker`, and carstate reads
    that from the SCCM's own copy of Steering_Data_FD1 on bus 0. Lighting the lamp by some other
    route leaves openpilot still seeing no blinker, so no lane change would start.

THE PLACE THAT WORKS IS THE SCCM'S SWITCH INPUT -- parallel the stalk contacts. Then the SCCM sees a
real deflection and everything downstream is genuinely correct at once: the BCM runs its own
seven-flash pattern at the car's own rate, the cancel behavior is the car's, and carState.leftBlinker
reads true so desire_helper engages. One injection point, both problems, no contention with anybody.

What has to be measured before building it: whether the stalk is a plain contact-to-ground or a
resistor ladder, which decides whether this is a transistor or an analog switch with a matched
resistor. And it needs a hardware self-clear -- a stuck output is a turn signal that never goes off,
which is worse than the feature not existing.

Shares its microcontroller with the rear-radar feeder that is already planned.

AND IT BLOCKS NOTHING. The blinker is needed when passing assist ACTUATES, which is gated behind
BLIS, the rear radar, and his explicit go-ahead. It was never on the critical path for the phase-1
observer, and letting it consume test drives was the mistake -- not any single one of the four
fixes. Leave the buttons; they cost nothing sitting there. Do not spend his driving on them.



ONE QUESTION: if openpilot writes TurnLghtSwtch_D_Stat into the Steering_Data_FD1 frame it already
transmits, does the BCM actually light the lamp?

READ THE DBC BEFORE GOING FURTHER -- 2026-08-04, and it should have been read first.

  BO_ 131 Steering_Data_FD1: 8 GWM
   SG_ TurnLghtSwtch_D_Stat : 5|2@0+ (1,0) [0|3] "SED"  IPMA_ADAS,PSCM

The frame is sent by the GATEWAY, and this signal is addressed to IPMA_ADAS and PSCM -- the camera
and the steering module. The body module is not a listed receiver. This is not a lamp command; it
is a status broadcast telling the ADAS modules that the driver has signalled.

The only turn-lamp signals that exist are on the body module's own frame -- TurnLghtLeft_D_Rq,
TurnLghtLeftOn_B_Stat -- and they are the BCM reporting OUTWARD, addressed to CMR_DSMC and
IPMA_ADAS. Nothing in this DBC lets anyone ask the BCM for a lamp.

Which reframes every symptom. Writing this signal means contradicting the gateway about where the
driver's physical stalk is, on a frame the gateway is also sending. The lamp responding at all is
most likely the gateway relaying our value onward to the body module over MS-CAN, in alternation
with the real stalk position it relays from the column. That is a fight no send rate wins, and it
matches what the car does: "test left signal does weird fast flashing, same with test right."

The value table closes the other door too:

  VAL_ 131 TurnLghtSwtch_D_Stat 3 "Unused_Treat_As_Off" 2 "Right" 1 "Left" 0 "Off"

There is no momentary or one-touch encoding to send. Value 3 is explicitly to be treated as off, so
the tap can only ever be a very short Left -- which is exactly what it measured: one flash, no
latch.

CONCLUSION: commanding the turn signal through this signal is probably not possible on this car,
and the remaining honest options are a different message entirely (none is documented here) or the
canbox on MS-CAN, where the body module actually lives. Do not spend more effort on send rates.

It matters because desire_helper's entire lane-change state machine keys off
carState.leftBlinker/rightBlinker, which are decoded from the SCCM's copy of that same message on
bus 0 (carstate.py). openpilot cannot self-receive its own transmissions -- panda returns them at
bus | 0x80 and the parser drops them -- so commanding the signal would NOT feed back through
carState. The answer has to come from the car: BodyInfo_3_FD1 (0x3B3) carries
TurnLghtLeftOn_B_Stat / TurnLghtRightOn_B_Stat, the body module's own report of the lamps, on the
powertrain bus openpilot already parses. Closed loop, no extra hardware, no canbox.

Nothing consumes this. It is not wired to desire_helper, to the passing-assist observer, or to any
controller. It runs a single timed pulse when explicitly requested and then disarms itself.

WHY THE GATES ARE WHAT THEY ARE
A turn signal is not an internal state -- it is a message to other drivers about what this car is
about to do. So:
  - Standstill only. A stationary car signals nothing about a maneuver, which makes an erroneous
    pulse meaningless to bystanders rather than misleading.
  - Never while cruise is engaged, so this can never overlap a real lane-change decision.
  - The driver's stalk always wins. If they are signaling, we do not touch it.
  - Hard timeout, enforced by frame count rather than by anything the requester controls, so a
    stuck param or a crashed UI cannot leave a lamp latched on.
  - The request param is CLEAR_ON_MANAGER_START and self-cleared on completion, so it cannot
    survive a reboot and cannot repeat without a fresh, deliberate request.
"""

from opendbc.car import DT_CTRL
from openpilot.common.params import Params

# Values of TurnLghtSwtch_D_Stat, per the DBC and carstate.py's decode (== 1 left, == 2 right).
SIGNAL_NONE, SIGNAL_LEFT, SIGNAL_RIGHT = 0, 1, 2

SIGNAL_TAP_LEFT, SIGNAL_TAP_RIGHT = 3, 4    # request values only; the COMMAND is still 1/2
SIGNAL_EDGE_LEFT, SIGNAL_EDGE_RIGHT = 5, 6    # ONE frame -- see EDGE_FRAMES
SIGNAL_BLINK_LEFT, SIGNAL_BLINK_RIGHT = 7, 8  # one frame per blink -- see DEFAULT_BLINK_PERIOD_S
SIGNAL_MEASURE = 9                            # command NOTHING and time the driver's own stalk

# THE HOLD AND THE TAP NO LONGER HAVE BUTTONS -- 2026-08-04.
#
# Both answered their question and both misbehave on this car, so leaving them one press away was
# a trap rather than a diagnostic: the hold flashes the lamp at the send rate, and the tap strobes
# the cornering lamps with it. Neither is something to hand a driver.
#
# The code and its tests stay. They are what established that the lamp mirrors frames one for one,
# and restoring either button is one line if a future question needs them. Deleting the mechanism
# would throw away the measurement that explains everything below.
PULSE_DURATION_S = 4.0        # long enough for several flash cycles at ~1.5 Hz

# --- WHAT THE CAR ACTUALLY SAID, 2026-08-04 ---
#
# "Before, it worked, but if I spammed the button I did it fast."
#
# A single clean press lights the lamp and flashes it normally. The erratic flashing that has
# haunted this module for two sessions is an artifact of REPEATED REQUESTS, not of the signal
# mechanism. Holding the level works.
#
# Worth recording how nearly that went the other way. The reasoning above -- level versus edge, our
# frames interleaving with the SCCM's, no send rate can fix it -- is sound, fits the symptom, and
# was one message away from being written down as settled fact on the strength of "it flashes
# really fast". It was the wrong conclusion drawn from a real observation, and the only thing that
# separated them was the owner distinguishing "I pressed it" from "I spammed it".
#
# WHICH IS WHY THE FLASH COUNT EXISTS NOW. Two runs of this test produced two recollections and
# settled nothing, because "really fast" is not a measurement. A clean 1.5 Hz signal over a four
# second hold is about six flashes; the erratic case is many times that. One number, compared
# between runs, ends the argument -- and it should have been here from the first version.
#
# THE TAP IS KEPT ANYWAY, as an option rather than a rescue. His BCM is set through FORScan to
# flash eight times from a momentary stalk deflection, and that is how he triggers every nudgeless
# lane change he makes -- so the pattern is the body module's to generate. Handing it one clean edge
# and going silent means the rate, the count and the cancel behavior are all the car's own,
# identical to a stalk tap, with nothing contending with the SCCM at all. If both work, that is the
# better one, and it is also the only one that can produce a signal longer than our own timeout.
TAP_COMMAND_S = 0.25          # about the length of a real stalk tap
# ...then STOP COMMANDING AND KEEP WATCHING. Flashes after we have gone quiet are the BCM running
# its own pattern, which is the entire measurement.
OBSERVE_AFTER_S = 3.0

# --- one frame. the last untried thing, and the smallest possible ask ---
#
# openpilot does not transmit Steering_Data_FD1 at all on a normal frame -- carcontroller only
# appends it when this module returns something. So a "tap" is not a tap: at 0.25 s and one send
# every BUTTONS_STEP it is FIVE separate CAN frames injected into the gateway's continuous stream
# of "stalk is off". Five rising edges. A real stalk tap is one mechanical event.
#
# If the body module triggers its one-touch on an edge, five edges in a quarter second is five
# retriggers -- which looks exactly like what the car does. One frame is the minimum perturbation
# anyone can make, and nobody has tried it.
#
# CONFIRMED FROM THE CAR, 2026-08-04, by a second signal nobody thought to use:
#
#   "One frame left did do one signal... I notice that the difference between TAP left signal and
#   one frame left when I spam the button is TAP left signal will like strobe my fog light and one
#   frame left won't. I have the feature on where it will turn on my left fog light and right fog
#   light when I use my blinker."
#
# The cornering lamp follows the turn signal, so it is an INDEPENDENT counter of how many discrete
# signal events the body module saw. The tap strobing it and a single frame not is direct evidence
# that each frame we send is its own event: five frames, five events. A four second hold is roughly
# eighty, which is the fast flashing.
#
# AND THE REASON TO EXPECT IT, from the owner: "how does ICBM reliably control
# stuff on the steering wheel?"
#
# ICBM drives the cruise buttons through THIS EXACT FRAME, on this bus, against the same gateway
# transmissions -- and it is completely reliable. So contention is not the difference. Edge versus
# level is:
#
#   a button press is an EVENT. One frame saying "pressed" is complete in itself and the receiver
#   latches it; later frames saying "not pressed" do not undo it, they are merely the absence of a
#   new press. Contradicting traffic is harmless.
#
#   TurnLghtSwtch_D_Stat is a LEVEL. It states where the stalk IS. Left, then Off, then Left is not
#   a request for a signal -- it is a stalk being flicked, and that is exactly what the lamp does.
#
# Which says the way to command this signal is to treat it the way ICBM treats a button: one frame,
# one edge, then silence. If the body module latches a one-touch from that, everything longer has
# simply been re-triggering it -- and the erratic flashing was never contention at all.
EDGE_FRAMES = 1
# ...and watch LONG ENOUGH to count a whole one-touch. His BCM is set to eight flashes, which at
# about 1.5 Hz is 5.3 s -- longer than the 3 s window the tap used, so an edge that DID trigger a
# full sequence would have been reported as a partial count and read as a failure.
#
# Costs nothing to be generous: a press during the window preempts it, so the driver is never made
# to wait out a measurement they have finished with.
# 2.0, DOWN FROM 8.0. Eight seconds was to watch for a self-generated one-touch after the single
# frame -- and the car has since settled that no such thing happens: one frame is one flash, always.
# So it was six seconds of waiting for something proven impossible, and it is most of "there is a
# delay between when I can test it": measured at 11.75 s before the buttons came back.
#
# Two is enough to catch a late flash if the body module ever surprises us, and short enough that
# pressing twice in a row is not a chore.
EDGE_OBSERVE_S = 2.0

# --- STOP FIGHTING THE CONTENTION AND USE IT ---
#
# From the car, and it is the whole answer: "one frame still does one flash, even if I spam the
# button." The lamp is not latching anything. It mirrors our frames ONE FOR ONE.
#
# Which explains every observation with a single rule -- THE FLASH RATE EQUALS OUR SEND RATE:
#
#   one frame                      one flash
#   five frames in a quarter second  a strobe, and his cornering lamp strobes with it
#   four seconds at 20 Hz          about eighty frames, and "really fast flashing"
#
# So the gateway's "off" frames were never the enemy. They are the other half of a blink. Our frame
# turns the lamp on, the gateway's next frame turns it off, and if our frames are paced at blinker
# rate the result IS a blinker. Three sessions were spent trying to win a contest whose loser was
# doing half the work.
#
# 1.5 Hz is the rate a Ford blinks at, so this should be indistinguishable from the real thing. It
# is also the one remaining hypothesis that fits ALL the evidence rather than most of it, and it
# costs one press to falsify: if the lamp blinks at 1.5 Hz, commanding the signal works today, with
# no canbox and no new hardware.
# 1.0 s, SLOWED FROM 0.667, and it is a setting now because this is the one number the car has to
# tell us.
#
# From the road: "when it does eventually do all seven together, they are too fast -- there's not
# enough space in between", and "sometimes the gap is more than one blink, or it's just blinking one
# less, so 5."
#
# ONE CAUSE FOR BOTH. The body module lights the lamp for its own fixed on-time from each command.
# Send the next one while it is still lit and there is nothing for it to do -- the command is
# ABSORBED, which is a missed blink and reads as a gap. Whatever does land then has only the
# leftover off-time, which is why the blinks that do appear look crowded. Too fast and randomly
# short are the same fault seen twice.
#
# FMVSS 108 requires 1-2 Hz and SAE J590b sets the on-time percentage, so 0.667 s was 1.5 Hz --
# legal, and at the fast end with the least margin against absorption. 1.0 s is the slow end of
# legal and the most room the body module can be given.
#
# Made a setting because the right value is a property of HIS car's flasher, not of this code, and
# because he is testing in a driveway where a knob beats waiting for a rebuild. See
# FordBlinkerBlinkPeriod.
#
# 0.76 IS NO LONGER A GUESS. He ran the measure mode and reported it: "Looks like my blinker is
# 760 ms." That is his own Ford's flasher, timed on his own car, and it replaces both ends of the
# reasoning above -- 1.5 Hz was the fast end of legal and 1.0 s the slow end, and the real answer
# was 1.32 Hz, between them and closer to the fast one.
#
# Changing this default only reaches a device where the key was never written. HIS was written, by
# hand, during the same testing -- so this does not touch his car and is not meant to. He sets it.
DEFAULT_BLINK_PERIOD_S = 0.76

# --- OR STOP GUESSING AND TIME HIS OWN FLASHER ---
#
# "I want it to match Ford's rate as well as you can." The best anyone can do from a desk is the
# FMVSS band, 1-2 Hz, which is a factor of two wide. His car knows the exact answer and has been
# telling us all along: BodyInfo_3_FD1 reports the lamp, and the flash counter already watches it.
#
# So this mode commands NOTHING. It watches while the driver flicks their own stalk, counts the
# rising edges and reports the mean interval between them. That number IS his Ford's rate, measured
# on his car, and it can be typed straight into the setting.
#
# The driver-stalk gate is deliberately not applied to this mode -- the driver signalling is the
# entire point rather than a reason to refuse.
MEASURE_WINDOW_S = 12.0
# ...but stop as soon as he clearly has. Two seconds dark after at least one measured interval means
# the stalk is done, and holding the machine for the rest of the window is the exact annoyance the
# blink sequence just had fixed.
MEASURE_QUIET_S = 2.0

# --- CLOSED LOOP: WAIT FOR THE LAMP, DO NOT GUESS AT IT ---
#
# "So why were some blinks getting missed at random times, sometimes missing 1, and sometimes
# missing 2?"
#
# Absorption alone does not explain RANDOM. A fixed on-time against a fixed send period gives a
# regular pattern -- every other one, forever. Random misses in runs of one and two are a BEAT: the
# body module's flasher has its own natural cycle, our fixed period does not divide into it, so our
# commands drift through its phase. Land during its ON phase and the command is swallowed; land
# during OFF and a new flash starts. As the phase walks you get one miss, then two, then none --
# and the starting phase depends on exactly when the button was pressed, which is why every attempt
# looked different and why it seemed to depend on the ignition.
#
# No fixed period fixes a beat. Matching the measured rate only slows the drift.
#
# So stop opening the loop. BodyInfo_3_FD1 reports the lamp, and this module already watches it:
# send the next command a moment after the lamp goes OUT. That is self-clocking -- it cannot drift
# out of phase because it has no phase of its own, it matches the car's natural rate exactly with
# nothing to configure, and it works on any Ford whatever its flasher does.
#
# HOW LONG TO WAIT AFTER IT GOES DARK -- measured, not chosen.
#
# A fixed small guard is wrong twice over. Too short and it lands inside the module's own refractory
# tail and is absorbed anyway; too long and the blink is slower than the car's. And the right value
# is a property of this flasher, which is the thing this whole exercise keeps rediscovering.
#
# But we can SEE it. The lamp's ON duration is right there in the report, and a standard automotive
# flasher is symmetric -- SAE J590b specifies the on-time percentage, near half. So waiting for as
# long as the lamp was just lit reproduces the car's own rhythm exactly, with nothing configured
# and nothing assumed about Ford.
#
# Clamped because one bad reading should not stall the sequence or machine-gun it.
# ...times this. A symmetric flasher would want the full on-time again, but the car is a little
# quicker than that -- "a tiny bit too slow in between maybe" -- so the off-time is slightly shorter
# than the on-time on this Ford. Trimmed rather than guessed at a new absolute number.
BLINK_GUARD_SCALE = 0.82
BLINK_GUARD_MIN_S = 0.15
BLINK_GUARD_MAX_S = 0.90
BLINK_AFTER_LAMP_OFF_S = 0.35   # until an on-time has actually been observed

# ...AND A WAY OUT IF THE LAMP NEVER REPORTS GOING DARK.
#
# This is the hole that fits every observation. The loop sends the next blink only when it has seen
# a FALLING edge later than its own last command:
#
#     ready = lamp_off_frame and dark_for >= guard and lamp_off_frame > last_blink_cmd
#     if not ready and (lamp_seen or since_cmd < period): return NONE
#
# Once `lamp_seen` is true there is NO timeout at all -- the fallback below it is gated on never
# having seen the lamp, deliberately, because as a timeout it raced the loop. So a single missed
# falling edge is terminal: `ready` can never become true again, nothing more is sent, and the run
# ends wherever it had got to when the command window expires.
#
# That predicts exactly what the car does. Counts that are short but never gapped, because the run
# stops rather than skipping. Spacing correct, because the blinks that did happen were paced by the
# lamp. Random, because it depends on which cycle the miss lands in. Unaffected by waiting between
# tests or by cycling the ignition, because nothing persists. "7, then nothing, then 2, then 6,
# then 3, then 1" is that, five times.
#
# 2.5 s cannot race a healthy loop: his flasher is 760 ms and a whole cycle is under a second, so
# this is past three of them. It only ever fires when the loop is genuinely stuck, and it recovers
# it in well under the command window instead of losing the rest of the run.
BLINK_STALL_S = 2.5

# --- AND DO NOT START WHILE THE LAMP IS STILL BUSY ---
#
# This is the invariant that was missing, and he found it: "if I wait long enough in between tests,
# the blinker works flawlessly. If I don't wait enough, I'll get less blinks or sometimes a gap."
#
# The fault depends on the gap between TESTS, not on anything inside a run -- so state is surviving
# from one run into the next, and only one thing here does. The lamp. His BCM is set through FORScan
# to flash seven times from a single stalk deflection, so when a run ends the body module is still
# working through its own sequence. Start the next one into that and the closed loop is watching
# edges the CAR is producing rather than ones we asked for: our first command lands while the lamp
# is already lit and is absorbed, and the pacing tracks somebody else's rhythm.
#
# So the arming gate has to watch the LAMP, not just the driver's stalk. It has always checked
# TurnLghtSwtch_D_Stat, which is where the stalk is -- and the stalk is idle the whole time the BCM
# is finishing a flash pattern nobody is asking for any more.
#
# Long enough to be sure the pattern has ENDED rather than being between flashes: a full period plus
# margin. At his measured 760 ms a gap this long cannot occur inside a sequence.
LAMP_SETTLE_S = 1.2

# ...and it WAITS rather than refusing, which is the difference between a fix and a new rule to
# remember.
#
# "You said I was supposed to look at some visualization or something for the blinker? I mean that
# would be hard because I'm pushing the button in the menu, so how would I see that?"
#
# Exactly right, and it settles the design. The buttons are in the settings menu and the readout is
# on the driving screen, so a refusal is INVISIBLE at the moment it happens -- he would press, get
# nothing, and be worse off than with a short run. A request that quietly waits for the lamp and
# then runs needs no readout and no rule: press it, it works, a second later than you expected.
#
# Bounded, because a lamp that never goes quiet must not leave a request armed indefinitely. Past
# this the request is dropped the same way any other refusal drops it.
LAMP_WAIT_MAX_S = 8.0
# WHAT THE SETTING IS FOR, NOW THAT THE LOOP IS CLOSED.
#
# Asked directly: "so I shouldn't need to adjust blink spacing if it can measure it?" Right -- with
# the lamp reporting, the rhythm is the car's own and nothing needs configuring.
#
# It still has one job: if the lamp never reports -- no BodyInfo_3_FD1, a wiring fault, a car
# without it -- there is nothing to close the loop on, and the choice is an open-loop period or no
# blinker at all. So the setting becomes the OPEN-LOOP spacing, used only in that case.
#
# Left wired rather than deleted because it was found doing nothing: read from the param and never
# used, with the fallback on a hardcoded constant. A control that does nothing is the same fault as
# a readout nobody renders, and this session has already produced three of those.
# SEVEN IS A BENCH-TEST NUMBER, NOT THE FEATURE'S NUMBER.
#
# It matches the one-touch he set in FORScan, which is the point: pressing this button and flicking
# the stalk should now produce the same thing, so any difference is ours.
#
# The feature will not want a count at all. From him: "7 blinks should be used for a regular lane
# change how I do it now, but who knows what passing assist will want, like if the lane change will
# take longer." Exactly right, and it is the advantage this has over the stalk -- a one-touch is a
# fixed number of blinks decided in FORScan, while sending frames ourselves means signaling for
# however long the maneuver actually takes and stopping when it ends.
#
# So whatever consumes this later takes a DURATION and keeps sending until the crossing completes.
# The count lives here, in the bench test, where the only job is comparability.
BLINK_COUNT = 7

# ONE FRAME PER BLINK IS CORRECT, and the reason is that the body module holds the lamp itself.
#
# Briefly got this wrong by reading the wrong lamp. The report was "the fog light only coming on for
# a fraction of a second for each blink", which looked like a duty-cycle failure -- so each blink
# became a burst of frames to hold the lamp on. Then, plainly: "the blinker didn't briefly turn on,
# it stayed on for the normal amount, just the fog light didn't."
#
# So the MAIN lamp already gets a full, normal on-time from a single frame. The BCM runs its own
# lamp timing from one command; it does not need us to hold anything. The cornering lamp is a
# separate circuit with its own shorter trigger, and it is a diagnostic rather than the thing being
# controlled -- which is exactly what made it so useful for counting events earlier, and exactly
# what made it misleading here.
#
# One frame, one proper blink. Nothing to hold.
#
# KEEP THIS EVEN IF THE CANBOX WORKS. His words: "if I do get the canbox to work, would still want
# this logic to be a fallback."
#
# Right, and for a better reason than redundancy. This path needs no hardware at all -- it is the
# frame openpilot already transmits, on a bus it already owns. A canbox is a purchase, a wiring
# job, and a third-party device that can fail, be unplugged, or stop being configured correctly
# after a reset. A signal that works with nothing added is the floor to fall back to.
#
# So whatever consumes a commanded blinker later should take a SOURCE rather than assume one, and
# this stays as the source that needs nothing. Do not delete it when a nicer route appears.
#
# THE CORNERING LAMP IS NOT GOING TO MATCH, AND SHOULD NOT BE CHASED. He has the FORScan feature
# that lights the fog lamp on the signalling side, and under a commanded blink it comes on only for
# a fraction of a second.
#
# The two lamps are driven differently and the difference is the whole reason this works at all.
# The main lamp LATCHES from one command and the body module runs its own blink timing. The
# cornering lamp appears to follow the switch LEVEL, so it is lit only for the milliseconds between
# our frame and the gateway's next "off".
#
# Which puts them in direct conflict: holding the level long enough to keep the fog lamp lit is
# precisely what makes the main blinker strobe -- the four second hold, already observed as "really
# fast flashing". There is no send pattern that satisfies both, because one wants an edge and the
# other wants a level.
#
# AND IT IS MOOT, WHICH IS THE ACTUAL ANSWER. Ford cornering lamps activate only BELOW 25 mph
# (FORScan forum; F150 forum). PassingAssistMinSpeed is 30 mph and every highway lane change is far
# above 25, so the cornering lamp cannot fire during any change this system would command. The only
# place the brief flicker appears is a stationary bench test, which is the one place it does not
# matter.
#
# There is also nothing to command it WITH. The only fog signals in the DBC are
# FogLghtFrontOn_B_Stat and FogLghtRearOn_B_Stat, both `_B_Stat`, both on BodyInfo_3_FD1 and both
# the body module reporting OUTWARD to the camera. No request signal exists, so it cannot be driven
# directly however anyone reaches the bus.
#
# What a canbox on MS-CAN might do is present a CLEAN sustained switch state to the body module
# rather than adding frames to a contended stream -- which is what a held stalk does, and would give
# a normal blink and a lit cornering lamp together. Plausible, unverified, and not worth buying
# hardware for a lamp that is off above 25 mph.

# How long the verdict stays on screen before the machine re-arms itself.
#
# THIS USED TO DEPEND ON THE REQUEST PARAM READING ZERO, and that is what made all four buttons go
# dead. Reported: "if I do them in rapid succession, it just will stop working for a little bit",
# and "I do tap right, it will work once and then all four buttons will stop working."
#
# The old exit from DONE was: poll every half second, and leave only if the request reads 0. A press
# during the verdict window writes a request, so that poll sees non-zero, drops the press and clears
# it -- and the next press half a second later does the same. Pressing repeatedly, which is exactly
# what anyone does when a button looks like it did nothing, held the machine in DONE for as long as
# they kept trying. The state that was supposed to prevent runaway pulses instead punished
# impatience.
#
# A clock cannot be held open by pressing a button. Three seconds is long enough to read a two-word
# verdict and a flash count, and it expires whatever the param says.
DONE_HOLD_S = 3.0
# Match every other sender of Steering_Data_FD1: CarControllerParams.BUTTONS_STEP, 20 Hz against the
# SCCM's own 10 Hz copy of the same frame.
#
# READ THIS BEFORE TRUSTING THE RATE TO FIX ANYTHING. Observed on the car, one pulse: the lamp
# flashed fast and erratically. The rate was 100 Hz at the time and lowering it is a guess, not a
# diagnosis, and quite possibly the wrong direction --
#
#   Our frames and the SCCM's interleave at EVERY rate; we cannot stop the SCCM transmitting. What
#   the rate changes is only the duty cycle the BCM sees. At 100 Hz it saw the switch commanded
#   ~91% of the time, at 20 Hz ~67%. If the flashing came from the OFF frames getting through, the
#   slower rate makes it worse, not better.
#
#   The likelier mechanism is the signal's TYPE. TurnLghtSwtch_D_Stat is a LEVEL -- where the stalk
#   is right now -- not an edge like the cruise buttons this same frame carries. openpilot's cancel
#   and resume work fine amid contradicting SCCM frames precisely because one frame saying "pressed"
#   is a complete event. A level alternating with OFF ten times a second is a stalk being flicked,
#   and no send rate makes that go away.
#
# Which is what this whole module exists to find out. It is a bench test, not a feature: run it,
# watch the lamp, and let the answer decide whether commanding the signal is possible on this car
# at all. Do not write a fix into this comment before the lamp has been watched again.
#
# The rate lives in this module rather than in carcontroller.py deliberately: that file cannot be
# tested offline, and a send rate is the exact class of mistake that reaches the car unnoticed.
BUTTONS_STEP = 5              # 100 Hz / 5 = 20 Hz, matching CarControllerParams.BUTTONS_STEP
STANDSTILL_V_EGO = 0.3        # m/s
PARAMS_POLL_S = 0.5


class BlinkerTestExt:
  """Mixed into CarController. Owns one pulse at a time and nothing else."""

  def __init__(self):
    self.bt_params = Params()
    self.bt_state = 0          # 0 idle, 1 pulsing, 2 done -- mirrors the capnp enum
    self.bt_commanded = SIGNAL_NONE
    self.bt_frames_left = 0
    self.bt_blocked = 0        # mirrors capnp Blocked
    self.bt_lamp_seen = False
    self.bt_blink_period_s = float(DEFAULT_BLINK_PERIOD_S)
    self.bt_measuring = False
    self.bt_measured_ms = 0        # mean interval between the driver's own flashes
    self._bt_last_edge = 0
    self._bt_intervals: list = []
    # HOW MANY TIMES the lamp lit, not merely whether it did. See the note above TAP_COMMAND_S:
    # "really fast" is not a measurement, and two runs of this test settled nothing because of it.
    self.bt_flashes = 0
    self.bt_flashes_after = 0
    self.bt_watching = SIGNAL_NONE
    self.bt_blinking = False
    self._bt_lamp_off_frame = 0
    self._bt_lamp_quiet_frames = int(LAMP_SETTLE_S / DT_CTRL)   # nothing has flashed yet
    self._bt_wait_frames = 0
    self._bt_lamp_on_frame = 0
    self._bt_guard_s = float(BLINK_AFTER_LAMP_OFF_S)
    self._bt_last_blink_cmd = 0
    self._bt_blinks_sent = 0
    self._bt_lamp_prev = False
    self._bt_command_frames_left = 0
    self._bt_done_frames = 0
    # Has the request param been SEEN at zero since the last pulse was armed?
    #
    # This is what separates "the driver pressed again" from "our disarm write failed", and those
    # two need opposite handling. Without it the module had to pick one disaster: trust the param
    # and a failed write restarts the lamp forever, or distrust it and a second press deadlocks the
    # buttons until reboot. The car produced the second one.
    self._bt_saw_clear = True
    self._bt_frame = 0

  @property
  def bt_seconds_remaining(self) -> float:
    return self.bt_frames_left * DT_CTRL

  def _read_request(self) -> int:
    try:
      return int(self.bt_params.get("FordBlinkerTest", return_default=True))
    except (ValueError, TypeError):
      return SIGNAL_NONE

  def _verdict_press(self) -> bool:
    """Is there a genuinely NEW press waiting while the verdict is on screen?

    Reading zero here is the proof that our disarm write landed; only after that can a non-zero
    read mean a driver rather than a param we cannot clear. See _bt_saw_clear.
    """
    return self._read_request() != SIGNAL_NONE and self._bt_saw_clear

  def _disarm(self) -> None:
    """Clear the request so a pulse can never repeat on its own."""
    # int, NOT str -- see the note in bluepilot.py::_request_blinker_test. Writing "0" here raised
    # TypeError, so DONE could never be cleared and a second pulse could never be armed.
    try:
      # block=True, and it has to be. put() defaults to putNonBlocking, so the read-back below
      # raced the write and usually returned the OLD value -- which set _bt_saw_clear False and
      # left the runaway guard holding every button down until some later poll happened to see a
      # zero. That is "tap left will do one signal and then stop working for a while."
      #
      # A blocking write is acceptable here and nowhere else in this file: this path only runs at
      # standstill, once, in response to a button the driver is standing there waiting on.
      self.bt_params.put("FordBlinkerTest", 0, block=True)
    except Exception:  # noqa: BLE001 - a param write failure must not stop the timeout above
      pass
    # READ IT BACK NOW. This is the only proof the store took the write, and it has to be taken
    # here rather than waiting for a later poll to notice a zero -- that is a race the driver wins
    # by pressing again, which is precisely when the answer matters. See _bt_saw_clear.
    try:
      self._bt_saw_clear = self._read_request() == SIGNAL_NONE
    except Exception:  # noqa: BLE001 - unreadable is not proof of anything
      self._bt_saw_clear = False

  def update_blinker_test(self, CS) -> int:
    """Advance the state machine. Returns the TurnLghtSwtch_D_Stat value to transmit, or
    SIGNAL_NONE on frames where nothing should be sent.

    Returns SIGNAL_NONE in every case except an active, gated pulse -- and the caller passes that
    through to create_button_msg, which otherwise copies the driver's own switch position.

    **The state machine advances every frame; only the RETURN is rate-limited** to BUTTONS_STEP.
    That keeps the timeout, the standstill re-check and the lamp observation running at full rate
    while the message goes out at the rate the bus expects. See BUTTONS_STEP for what happened when
    it did not.
    """
    self._bt_frame += 1
    send_frame = (self._bt_frame % BUTTONS_STEP) == 0

    # How long the lamps have BOTH been dark, tracked every frame whatever state we are in -- the
    # arming gate below needs it, and by then the previous run is long over. See LAMP_SETTLE_S.
    if getattr(CS, 'turn_lamp_left', False) or getattr(CS, 'turn_lamp_right', False):
      self._bt_lamp_quiet_frames = 0
    else:
      self._bt_lamp_quiet_frames += 1
    # THE VERDICT HAS TO GET OUT OF THIS OBJECT. This machine lives in the CarController; the
    # carStateBP message is built in CarState. Nothing bridged them, so `state`, `lampSeen`,
    # `secondsRemaining` and `blockedReason` were read by the panel and written by nobody -- the
    # whole test reported its answer to an empty room, and the only way to read it was to watch a
    # mirror. Stashed onto the CarState the controller is already handed, which carstate_ext then
    # publishes. One cycle stale by construction, which at 100 Hz is 10 ms.
    self._publish_to(CS)

    # ---- active pulse: timeout is checked FIRST, before anything that could throw or block ----
    if self.bt_state == 1:
      # A press DURING a running test is dropped, not honored, and that is a known annoyance rather
      # than a design: "it still seems like there is a delay between when I can test it." A blink
      # is seven cycles, so the buttons are inert for about five seconds and then again for the
      # three second verdict.
      #
      # Two attempts at interrupting it broke working tests, because while a pulse runs the request
      # param still holds the value that started it -- a leftover is indistinguishable from a new
      # press without more bookkeeping than this is worth today. The verdict window IS preemptible,
      # which recovers most of the wait. Left as it is deliberately, with the reason written down.
      self.bt_frames_left -= 1

      self._bt_command_frames_left = max(0, self._bt_command_frames_left - 1)
      commanding = self._bt_command_frames_left > 0

      lamp_left, lamp_right = self._lamp_state(CS)
      # Watch the side we ASKED for. bt_commanded is cleared for transmission during a tap's
      # observe phase, so it is the wrong thing to watch once we go quiet -- which is exactly when
      # the interesting flashes happen.
      if self.bt_measuring:
        lamp = lamp_left or lamp_right
      else:
        lamp = lamp_left if self.bt_watching == SIGNAL_LEFT else lamp_right
      if lamp:
        self.bt_lamp_seen = True
      # Rising edges only. The lamp is a square wave; counting the level would count frames.
      if lamp and not self._bt_lamp_prev:
        self._bt_lamp_on_frame = self._bt_frame
        self.bt_flashes += 1
        if not commanding:
          self.bt_flashes_after += 1
        # ONLY WHILE MEASURING. Timing our own commanded flashes sets measuredPeriodMs on a blink
        # test too, and the panel renders that as "YOUR BLINKER" -- so a commanded test would
        # report itself as a measurement of his stalk. Two rising-edge blocks sat here doing
        # different halves of this, which is how it went unnoticed.
        if self.bt_measuring:
          gap = (self._bt_frame - self._bt_last_edge) * DT_CTRL
          if self._bt_last_edge and gap < 3.0:
            self._bt_intervals.append(gap)
            self.bt_measured_ms = int(1000 * sum(self._bt_intervals) / len(self._bt_intervals))
        self._bt_last_edge = self._bt_frame
      if self._bt_lamp_prev and not lamp:
        self._bt_lamp_off_frame = self._bt_frame
        # The on-time we just watched. See BLINK_GUARD_MIN_S -- a symmetric flasher wants the same
        # again as its off-time, so this IS the car's own rhythm.
        if self._bt_lamp_on_frame:
          lit_s = (self._bt_frame - self._bt_lamp_on_frame) * DT_CTRL * BLINK_GUARD_SCALE
          self._bt_guard_s = min(max(lit_s, BLINK_GUARD_MIN_S), BLINK_GUARD_MAX_S)
      self._bt_lamp_prev = lamp

      # Any of these ends the pulse immediately. Standstill is re-checked every frame, not just at
      # the start: if the car begins rolling mid-pulse the signal drops at once.
      # The driver's stalk ends every mode except MEASURING, where it is the input being measured.
      # Adding that exemption at the arming gate only meant the window closed on its own first
      # frame -- caught by a test rather than by another trip to the driveway.
      # MEASURING ENDS WHEN HE STOPS. Otherwise it holds the machine for the full twelve seconds
      # plus the verdict -- the same "I still need to wait a little bit in between tests" that was
      # just fixed for blinking, in the mode he reaches for first.
      if self.bt_measuring and self._bt_intervals and self._bt_lamp_off_frame:
        if (self._bt_frame - self._bt_lamp_off_frame) * DT_CTRL > MEASURE_QUIET_S:
          self.bt_frames_left = 0

      # THE SIDE WE ARE COMMANDING DOES NOT COUNT AS THE DRIVER, while a run is in progress.
      #
      # carState.leftBlinker is TurnLghtSwtch_D_Stat off Steering_Data_FD1 -- the exact signal this
      # writes. Whether our own value can come back around (the frame is the gateway's, and the
      # gateway is evidently relaying our value to the body module, since the lamp lights) is not
      # something this file can settle. What is certain is that while we are commanding a side,
      # that side's reported switch position is not evidence about the driver, and treating it as
      # such lets a run abort itself partway through.
      #
      # The opposite side still stops it instantly, which is the case that matters: the driver
      # reaching for the stalk to go the other way. The arming check further down is unchanged and
      # still refuses to start against any blinker at all.
      driver_left = CS.out.leftBlinker and self.bt_commanded != SIGNAL_LEFT
      driver_right = CS.out.rightBlinker and self.bt_commanded != SIGNAL_RIGHT
      driver_stalk = (driver_left or driver_right) and not self.bt_measuring
      if self.bt_frames_left <= 0 or CS.out.vEgo > STANDSTILL_V_EGO or          driver_stalk or CS.out.cruiseState.enabled:
        # SAY WHY IT STOPPED. Every one of these ends a run that was already under way, and until
        # now all four ended it silently -- the panel then showed the flash count and "SIGNAL
        # WORKS", which reads as a car that half-ignored us.
        #
        # From the road: "I tapped blink right and it only did two flashes... waited a bit, did a
        # blink right and it only did six... waited more, three. It was only ever short a few
        # blinks. It never had a gap." Short, never gapped, and varying with how long he waited is
        # the exact signature of a run being CUT OFF rather than dropping blinks.
        #
        # And the cause is almost certainly the first branch. STANDSTILL_V_EGO is 0.3 m/s, which is
        # 0.7 mph -- below a creep. Testing at a traffic light, any roll at all ends the run
        # wherever it had got to. The gate is right; operating a lamp other drivers read while the
        # car is moving is not something to soften. Being silent about it was the bug.
        if CS.out.vEgo > STANDSTILL_V_EGO:
          self.bt_blocked = 1
        elif CS.out.cruiseState.enabled:
          self.bt_blocked = 2
        elif driver_stalk:
          self.bt_blocked = 3
        self.bt_state = 2
        self.bt_commanded = SIGNAL_NONE
        self.bt_frames_left = 0
        self._bt_done_frames = int(DONE_HOLD_S / DT_CTRL)
        self._disarm()
        return SIGNAL_NONE

      # Silent once the command window closes: the point of a tap's observe phase is to find out
      # what the car does WITHOUT us.
      if not commanding:
        return SIGNAL_NONE
      # Blink mode sends ONE frame per period rather than at the bus rate -- the lamp mirrors our
      # frames one for one, so the send rate IS the flash rate. See BLINK_PERIOD_S.
      if self.bt_blinking:
        # CLOSED LOOP. See BLINK_AFTER_LAMP_OFF_S -- command the next blink a moment after the lamp
        # goes out, so there is no phase to drift and nothing to configure.
        since_cmd = (self._bt_frame - self._bt_last_blink_cmd) * DT_CTRL
        if self._bt_blinks_sent >= BLINK_COUNT:
          # DONE MEANS DONE. The command window is a generous backstop -- 14 s for a sequence that
          # finishes in six -- and leaving the machine sitting in it is the whole of "I still need
          # to wait a little bit in between tests", "occasionally pressing does absolutely nothing",
          # and the truncated counts: a press landing in that dead window was dropped, and the next
          # one arrived mid-sequence.
          #
          # Close it as soon as the last lamp goes dark, so the wait is the test and nothing more.
          if not lamp:
            self.bt_frames_left = min(self.bt_frames_left, int(0.4 / DT_CTRL))
          return SIGNAL_NONE
        dark_for = (self._bt_frame - self._bt_lamp_off_frame) * DT_CTRL if self._bt_lamp_off_frame else 0.0
        ready = (self._bt_lamp_off_frame and dark_for >= self._bt_guard_s
                 and self._bt_lamp_off_frame > self._bt_last_blink_cmd)
        # ...unless the lamp never reports AT ALL, in which case fall back to the fixed period.
        #
        # Gated on never having seen it, not on a timeout. As a timeout it RACED the closed loop:
        # a flasher slower than the configured spacing would have the fallback fire while the loop
        # was still correctly waiting for the lamp, putting the command straight back into the
        # refractory window the loop exists to avoid. A test caught it immediately, which is the
        # only reason this is not another trip to the driveway.
        # See BLINK_STALL_S. A lamp report that misses one falling edge used to end the run.
        stalled = since_cmd >= BLINK_STALL_S
        if not ready and not stalled and (self.bt_lamp_seen or since_cmd < self.bt_blink_period_s):
          return SIGNAL_NONE
        self._bt_last_blink_cmd = self._bt_frame
        self._bt_blinks_sent += 1
        return self.bt_commanded
      return self.bt_commanded if send_frame else SIGNAL_NONE

    # ---- showing the verdict: a clock, not a condition. See DONE_HOLD_S ----
    #
    # Runs EVERY frame rather than on the poll, and depends on nothing the driver can do. Presses
    # that land in here are still dropped -- queueing would light a lamp seconds after the button,
    # with nothing on screen connecting the two -- but they can no longer extend the wait.
    if self.bt_state == 2:
      self._bt_done_frames -= 1
      if self._bt_done_frames <= 0:
        self.bt_state = 0
        self.bt_blocked = 0
        self._disarm()
      elif self._bt_frame % int(PARAMS_POLL_S / DT_CTRL) == 0 and self._verdict_press():
        # A FRESH PRESS PREEMPTS THE VERDICT rather than being dropped.
        #
        # Measured before this: a hold locked the buttons for 7.5 s and a tap for 6.75 s -- four
        # seconds of pulse and three of verdict, during which every button was dead. That is the
        # whole of "the other three buttons don't work", and dropping the press made it worse by
        # wasting the one input that says the driver has finished reading.
        #
        # Safe because the request param is a ONE-SHOT: it is cleared the moment a pulse ends, so a
        # non-zero read here is a new click and not a stale one. A stuck param cannot repeat,
        # because nothing but the UI ever writes a value.
        self.bt_state = 0
        self.bt_blocked = 0
      if self.bt_state == 2:
        return SIGNAL_NONE
      # ...otherwise fall through and arm on this same frame, which is already a poll frame.

    # ---- idle: look for a request, at a low rate ----
    if self._bt_frame % int(PARAMS_POLL_S / DT_CTRL) != 0:
      return SIGNAL_NONE

    request = self._read_request()
    if request == SIGNAL_NONE:
      # Proof the store is taking our writes. Until this is seen, a non-zero read cannot be told
      # apart from our own disarm having silently failed. See _bt_saw_clear.
      self._bt_saw_clear = True

    if request not in (SIGNAL_LEFT, SIGNAL_RIGHT, SIGNAL_TAP_LEFT, SIGNAL_TAP_RIGHT,
                       SIGNAL_EDGE_LEFT, SIGNAL_EDGE_RIGHT,
                       SIGNAL_BLINK_LEFT, SIGNAL_BLINK_RIGHT, SIGNAL_MEASURE):
      self.bt_blocked = 0
      return SIGNAL_NONE

    # A request we can never clear must not start a second pulse. This is the runaway guard, and it
    # is the only thing between a failed param write and a lamp that pulses until the ignition
    # goes off.
    #
    # AND IT USED TO LATCH FOREVER, which is the whole of "working sometimes" and "I think a delay
    # was preventing them from working". The race is small and its consequence was permanent:
    #
    #   pulse ends -> _disarm() writes 0 -> HE PRESSES A BUTTON HERE -> _disarm() reads back, sees
    #   his 7 instead of our 0, concludes the store rejected the write, sets _bt_saw_clear False.
    #
    # After that the only line that could set it True again is the one above, which needs a poll to
    # read SIGNAL_NONE -- and the param is now his 7, and nothing else ever writes this key. So it
    # never reads zero, the guard never lifts, and every button is dead until the ignition cycles.
    # Pressing again could not help; pressing again was the cause. The wait between tests he keeps
    # describing is the window: press late and it works, press on the seam and it stops working.
    #
    # RETRY THE CLEAR instead of latching. If the store is healthy the very next poll proves it and
    # the buttons come back; if it is genuinely broken the retry fails too and the guard still
    # holds, so nothing about the safety property changes. The cost is that the press which landed
    # in the seam is discarded rather than run -- one lost press instead of all of them, and the
    # discard is the correct call anyway, because that press cannot be told apart from a stale
    # value we failed to clear.
    if not self._bt_saw_clear:
      self._disarm()
      return SIGNAL_NONE

    # Gates. Each records why so a refused request is visible rather than silently ignored.
    if CS.out.vEgo > STANDSTILL_V_EGO:
      self.bt_blocked = 1
      return SIGNAL_NONE
    if CS.out.cruiseState.enabled:
      self.bt_blocked = 2
      return SIGNAL_NONE
    # ...except when MEASURING, where the driver using their stalk is the whole point.
    if (CS.out.leftBlinker or CS.out.rightBlinker) and request != SIGNAL_MEASURE:
      self.bt_blocked = 3
      return SIGNAL_NONE
    # THE LAMP, not just the stalk. See LAMP_SETTLE_S -- the body module is still running its own
    # seven flashes from the last test, the stalk is idle throughout, and starting into that is the
    # whole of "if I don't wait enough, I'll get less blinks or sometimes a gap".
    #
    # HOLD the request rather than dropping it: he cannot see the panel from inside the settings
    # menu, so a refusal is invisible and reads as a dead button. This just runs a moment later.
    #
    # Measuring is exempt for the same reason as above: it commands nothing, and the driver's own
    # lamp is what it is there to watch.
    if request != SIGNAL_MEASURE and self._bt_lamp_quiet_frames < int(LAMP_SETTLE_S / DT_CTRL):
      self._bt_wait_frames += int(PARAMS_POLL_S / DT_CTRL)
      if self._bt_wait_frames > int(LAMP_WAIT_MAX_S / DT_CTRL):
        # A lamp that never settles is a fault of its own, and holding a request forever against it
        # is how a lamp ends up pulsing long after anybody asked.
        self._bt_wait_frames = 0
        self._disarm()
      self.bt_blocked = 5
      return SIGNAL_NONE
    self._bt_wait_frames = 0

    self.bt_blocked = 0
    self.bt_state = 1
    self._bt_saw_clear = False
    self._bt_last_edge = 0
    self._bt_intervals = []
    self.bt_measured_ms = 0
    self.bt_measuring = request == SIGNAL_MEASURE
    if self.bt_measuring:
      # Commands nothing at all. Watches, counts and times.
      self.bt_commanded = SIGNAL_NONE
      self.bt_watching = SIGNAL_NONE
      self._bt_command_frames_left = 0
      self.bt_frames_left = int(MEASURE_WINDOW_S / DT_CTRL)
      self.bt_lamp_seen = False
      self.bt_flashes = 0
      self.bt_flashes_after = 0
      self._bt_lamp_prev = False
      return SIGNAL_NONE
    tap = request in (SIGNAL_TAP_LEFT, SIGNAL_TAP_RIGHT, SIGNAL_EDGE_LEFT, SIGNAL_EDGE_RIGHT)
    edge = request in (SIGNAL_EDGE_LEFT, SIGNAL_EDGE_RIGHT)
    self.bt_blinking = request in (SIGNAL_BLINK_LEFT, SIGNAL_BLINK_RIGHT)
    self.bt_commanded = (SIGNAL_LEFT if request in (SIGNAL_LEFT, SIGNAL_TAP_LEFT, SIGNAL_EDGE_LEFT,
                                                    SIGNAL_BLINK_LEFT)
                         else SIGNAL_RIGHT)
    self.bt_watching = self.bt_commanded
    # A tap commands briefly then watches in silence; a hold commands throughout. Same counter for
    # both, so the two runs are directly comparable with one variable changed.
    # EDGE_FRAMES is counted in SENDS, not control frames -- the send is rate-limited to
    # BUTTONS_STEP, so one send needs the window to stay open that long.
    self._bt_command_frames_left = (EDGE_FRAMES * BUTTONS_STEP if edge else
                                    int((TAP_COMMAND_S if tap else PULSE_DURATION_S) / DT_CTRL))
    observe = EDGE_OBSERVE_S if edge else OBSERVE_AFTER_S
    if self.bt_blinking:
      # One frame per blink, for as many blinks as the one-touch does. See BLINK_PERIOD_S.
      # BLINK_COUNT whole periods, computed from the SAME integer period the send condition uses.
      # Deriving them separately rounded differently and opened a truncated extra burst at the end
      # -- a short odd blink after the intended ones, which is what a test caught and may well be
      # the "small break after four" reported from the car.
      try:
        self.bt_blink_period_s = float(self.bt_params.get("FordBlinkerBlinkPeriod",
                                                          return_default=True)) / 1000.0
      except Exception:  # noqa: BLE001 - a bad param must not stop a parked bench test
        self.bt_blink_period_s = float(DEFAULT_BLINK_PERIOD_S)
      # Generous: the loop stops itself after BLINK_COUNT blinks, so this is only a backstop.
      self._bt_command_frames_left = int(BLINK_COUNT * 2.0 / DT_CTRL)
      self._bt_lamp_off_frame = 0
      self._bt_lamp_on_frame = 0
      self._bt_guard_s = float(BLINK_AFTER_LAMP_OFF_S)
      self._bt_last_blink_cmd = self._bt_frame
      # The arming frame below returns a command, so it IS the first blink. Not counting it is
      # where "blink left did eight flashes" came from, against a BLINK_COUNT of seven.
      self._bt_blinks_sent = 1
      # A short tail only. There is nothing to observe after a blink -- the lamp follows our frames
      # and stops when we do -- so a full second here was a second of dead buttons for nothing.
      self.bt_frames_left = self._bt_command_frames_left + int(0.3 / DT_CTRL)
    else:
      self.bt_frames_left = int(((TAP_COMMAND_S + observe) if tap else PULSE_DURATION_S) / DT_CTRL)
    self.bt_lamp_seen = False
    self.bt_flashes = 0
    self.bt_flashes_after = 0
    self._bt_lamp_prev = False
    return self.bt_commanded

  def _publish_to(self, CS) -> None:
    """Hand this cycle's state to CarState, which owns the message. See update_blinker_test."""
    CS.bt_state = self.bt_state
    CS.bt_commanded = self.bt_commanded
    CS.bt_seconds_remaining = self.bt_seconds_remaining
    CS.bt_lamp_seen = self.bt_lamp_seen
    CS.bt_blocked = self.bt_blocked
    # How far the run actually got. See blinksSent -- a truncated count on its own cannot be told
    # apart from a car that ignored half the request.
    CS.bt_blinks_sent = self._bt_blinks_sent if self.bt_blinking else 0
    CS.bt_blinks_wanted = BLINK_COUNT if self.bt_blinking else 0
    CS.bt_flashes = self.bt_flashes
    CS.bt_flashes_after = self.bt_flashes_after
    CS.bt_measured_ms = self.bt_measured_ms

  @staticmethod
  def _lamp_state(CS) -> tuple[bool, bool]:
    """The car's own report of the lamps, decoded in carstate.py from BodyInfo_3_FD1.

    This is the measurement the whole test exists to take -- what the BCM actually did, as opposed
    to what we asked for.
    """
    return bool(getattr(CS, 'turn_lamp_left', False)), bool(getattr(CS, 'turn_lamp_right', False))
