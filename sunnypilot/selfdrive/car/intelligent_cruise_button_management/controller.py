"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from cereal import car, custom
from opendbc.car import structs
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.common.realtime import DT_CTRL
from openpilot.sunnypilot import PARAMS_UPDATE_PERIOD
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.common import Mode as SpeedLimitMode
from openpilot.sunnypilot.selfdrive.car.intelligent_cruise_button_management.helpers import get_minimum_set_speed
from openpilot.sunnypilot.selfdrive.car.cruise_ext import CRUISE_BUTTON_TIMER, V_CRUISE_MAX, update_manual_button_timers

ButtonType = car.CarState.ButtonEvent.Type
LongitudinalPlanSource = custom.LongitudinalPlanSP.LongitudinalPlanSource
State = custom.IntelligentCruiseButtonManagement.IntelligentCruiseButtonManagementState
SendButtonState = custom.IntelligentCruiseButtonManagement.SendButtonState
OverrideState = custom.IntelligentCruiseButtonManagement.OverrideState
UnconfirmedLeadState = custom.LongitudinalPlanSP.UnconfirmedLead.State
BaselineSource = custom.IntelligentCruiseButtonManagement.BaselineSource

# BluePilot: states in which the radar-blind lead detector owns the target outright.
# There is no longer a companion "...and this one also cancels the driver's baseline" list: a
# baseline changes the number ICBM aims for, never whether it acts, so a hazard needs no exception
# to get through. It simply replaces v_target like it always did.
UNCONFIRMED_LEAD_COMMANDING = (UnconfirmedLeadState.active, UnconfirmedLeadState.restoring)

ALLOWED_SPEED_THRESHOLD = 1.8  # m/s, ~4 MPH
INACTIVE_TIMER = 0.4

# BluePilot: A FALL NEVER WAITS. A BOUNCE STRAIGHT BACK HAS TO HOLD.
#
# Route 000003ae, 2026-08-23: the planner's target flipped between 27 and 30 mph 2.82 times a
# SECOND for the whole inert window, and ICBM did exactly what it is built to do -- 168 increase
# frames and 210 decrease frames, with ZERO driver button events in the window. That is the
# measured shape of "it keeps telling me set speed changed and the max speed is flashing fast".
# It is NOT the tap-vs-hold oscillation documented further down (there the target is still and the
# CLUSTER crosses it); here the target itself is shaking and the cluster is chasing it honestly.
#
# The upstream lever for this was `apply_hysteresis(..., HYST_GAP)`, left at 0.0 with a TODO. It
# could not have fixed this anyway: it is a MAGNITUDE deadband, and any gap wide enough to swallow
# a 3 mph flip is wide enough to sit out a real speed-limit change.
#
# WHAT IS FILTERED IS REVERSALS, NOT RISES. An earlier version of this made every step up wait, and
# that broke five tests defending real rules -- including the owner's own "behind a car, set the
# speed to anything, because that car is probably driving correctly". Metering honest climbs to
# fix a noise complaint is a bad trade. So the filter only ever engages on the one shape that was
# actually measured: the target coming back UP to a level it left moments ago.
#
#   FALL         adopted on the very first frame, no delay ever, and it records the level it left.
#                Aiming lower is the conservative action, and the exit-ramp incident of 2026-08-23
#                came from a set speed that could not fall -- nothing here may re-create that.
#   RISE to new  adopted immediately. A limit going up, a curve releasing, a lead pulling away.
#   BOUNCE back  to within SETTLE_EPS of a level left inside REVERSAL_MEMORY_S: must be asked for
#                continuously for SETTLE_S. A 0.1 s blip inside a 0.5 s cycle never qualifies, so
#                the oscillation collapses onto its low leg and stays there.
#
# The memory is refreshed every time a bounce is refused, so a shake that goes on for a minute
# never ages out mid-shake; once the target genuinely settles it expires REVERSAL_MEMORY_S later
# and the next climb is instant again.
#
# Deliberately NO force-adopt timeout. If the target genuinely oscillates forever the low leg is
# the right answer, and a timeout would re-introduce exactly the reversals this removes.
#
# Scope: this filters ONLY the target the button logic aims at. Holds, overrides and the divergence
# latch all compare against `v_target_raw`, which stays exactly as the planner published it -- see
# the note at the call site for what happened when that was not true.
SETTLE_S = 0.4
SETTLE_FRAMES = int(SETTLE_S / DT_CTRL)
SETTLE_EPS = 1  # display units; a rise of one is a ramp crossing an integer, not a step
REVERSAL_MEMORY_S = 2.0
REVERSAL_MEMORY_FRAMES = int(REVERSAL_MEMORY_S / DT_CTRL)

# BluePilot: buttons that count as the driver taking the set speed back from ICBM.
# gapAdjustCruise/lkas/mainCruise deliberately excluded -- they don't change the set speed.
MANUAL_OVERRIDE_BUTTONS = (ButtonType.accelCruise, ButtonType.decelCruise, ButtonType.setCruise)

# BluePilot: what a driver's set-speed press means.
#
# It is NOT "stop managing my cruise". It is "for this speed limit, I want a different number" --
# faster on a freeway, slower elsewhere. So the press records a BASELINE and every ICBM feature
# keeps running against it: curves still slow the car, the vision-lead trigger still fires, and
# when the reason for slowing passes the set speed returns to the baseline instead of to whatever
# Speed Limit Assist wanted.
#
# An earlier design made a press suspend ICBM outright. That was wrong in both directions: it lost
# curve slowing for the rest of the drive, and it needed an ever-growing set of exceptions to let
# hazards back through. Treating the press as an offset rather than an off switch removes the need
# for any of them.
RE_ARM_ON_CRUISE_CYCLE = True  # cancel (or any disengage) followed by re-engage drops the baseline
# BluePilot: which button re-engaged decides whether the hold survives, so that RESUME and SET stop
# being the same control. Every disengage/engage cycle used to drop the baseline regardless, which
# left the driver no way to get a hold back except rebuilding it by hand.
#
#   RESUME -> keep the hold. That is what the word means: go back to what I had.
#   SET    -> drop it, and Speed Limit Assist takes the set speed. The way to hand it back.
#
# The press arrives while cruise is still DISENGAGED -- carstate_ext maps CcAslButtnCnclResPress to
# resumeCruise in that state -- and cruise_enabled only flips a few frames later, so the press has
# to be remembered across the transition rather than read on the cycle frame.
RESUME_BUTTONS = (ButtonType.resumeCruise,)
# And the SYMMETRIC half, which never existed. SET- while cruise is off emits `setCruise`
# (COMBO_EVENTS["SET_DEC"][False] == 9), and that is the one button meaning that hands the speed
# back to Speed Limit Assist. MAIN-engages synthesize the same event deliberately, and mean the
# same thing by it, so both sources are welcome here.
SET_BUTTONS = (ButtonType.setCruise,)
# Tapping. Within TAP_BAND mph of the target the button is pulsed rather than held, because a held
# button moves this car 5 mph and a tap moves it 1 -- see the duty-cycle block in update_state_machine.
# ON long enough for opendbc's ford/icbm.py to put one frame on the wire (it emits at most one per
# 0.05 s), then a gap long enough that the car reads a release rather than a repeat.
TAP_BAND = 2
TAP_ON_FRAMES = 8
TAP_CYCLE_FRAMES = 60

RESUME_PRESS_MEMORY_FRAMES = 150  # 1.5 s at 100 Hz, generous next to the engage delay
SET_PRESS_MEMORY_FRAMES = 150     # same window, same reason -- the PCM reports enabled late
# ...but the button event must not be DEPENDED on, so RESUME is also recognized from BEHAVIOR.
#
# Read the reasoning below carefully, because an earlier version of this comment got it backwards
# and the wrong version is more persuasive than the right one. It claimed baselineSource read "I"
# every time, therefore CS.buttonEvents delivers no set-speed buttons at all, therefore the press
# path is dead code. That conclusion was false and nearly deleted a working path.
#
# What actually happens: the press path fires FIRST and the idle fallback relabels it moments
# later, because a press arms the stand-down, ICBM goes idle by definition, and the driver's held
# button keeps stepping the set speed for seconds afterwards. Instrumented on a real 5 mph hold --
# press at the button event, still press at idle 67, overwritten to fallback at idle 128. The
# BaselineSource.press guard at the fallback site (see "NEVER downgrade a press") is what stops
# the relabeling now.
#
# So the behavioral path below is belt-and-braces, not a replacement. It matters because RESUME
# has to survive even a frame where the event is missed or arrives outside the memory window --
# not because the events never come.
#
# Ford distinguishes the two itself, in the set speed: RESUME restores the PREVIOUS set speed, SET
# jumps to the CURRENT VEHICLE SPEED. So once the number settles after re-engaging, whichever it
# landed on says which button was pressed -- no button event required.
RESUME_MATCH_TOLERANCE = 2  # display units; Ford resumes to the exact previous value
# Wider than the resume tolerance on purpose. A resume restores an exact stored number, so 2 covers
# it; a SET lands on the vehicle speed, which is still moving as the press is processed and is read
# through a cluster that lags, so the landing scatters by several mph.
SET_MATCH_TOLERANCE = 4  # display units
# BluePilot: how far above the current set speed ICBM may still go while SCC-Vision is tracking a
# bend. Not zero: ICBM commands in 1 mph steps against a lagged cluster, so it hunts by one, and
# clamping hard to the cluster means a 1 mph undershoot can never be recovered for the length of the
# curve. One display unit leaves the hunt working and still blocks the case this exists for -- a
# 9 mph climb chased out of a noisy vision target, mid off-ramp, on 2026-08-08.
CURVE_RISE_TOLERANCE = 1
# 5 s at 100 Hz. Longer than the 3.3 s off-ramp spike this ceiling exists to reject, and short enough
# that a genuinely finished bend costs only a couple of seconds before the speed is allowed back.
CURVE_RELEASE_FRAMES = 500
# 5 s at 100 Hz. Covers the stretch where SCC-Vision has let go but the car is still coming out of the
# bend -- the logged jump ran four seconds from vision releasing.
CURVE_EXIT_LINGER_FRAMES = 500
# The baseline applies to the speed-limit/cruise component only. A curve target is a physics limit,
# not something to add an offset to -- SCC-Vision asking for 40 means 40, whatever the baseline is.
BASELINE_SOURCES = (LongitudinalPlanSource.cruise, LongitudinalPlanSource.speedLimitAssist)
# How far the posted limit must move before the baseline is discarded and SLA takes over again.
# Fallback only; the live value comes from IcbmBaselineResetDelta.
DEFAULT_BASELINE_RESET_DELTA = 10  # display units (mph/kph)
# After a driver press, ICBM stands down for this long and takes whatever the set speed settles
# at as the baseline.
#
# Three attempts failed trying to work out WHO moved the set speed after the fact. Requiring ICBM
# to be idle missed the one frame the cluster caught up on, so a press was reverted unless the
# driver pressed down first to let ICBM settle. Crediting everything inside a window after a press
# adopted ICBM's own curve deceleration. Comparing directions broke when ICBM reversed, because an
# older command of the opposite sign was still in flight and landed looking like a driver press.
#
# The ambiguity is self-inflicted: it only exists because both parties move the set speed at once.
# Standing down for half a second after a press removes it. Nothing else can be moving the number
# in that window, so whatever it settles at is the driver's, with no attribution needed. The cost
# is half a second of ICBM inaction immediately after a press, when the driver is adjusting anyway.
# The stand-down ends when the SET SPEED STOPS MOVING, not on a fixed timer. A fixed 0.6 s window
# was the fourth failed attempt at this: on a car whose cluster takes longer than that to report a
# press, the baseline was still equal to SLA's target when the window closed, and everything
# downstream then treated the override as never having happened.
# How long after a RESUME press a `+` is still assumed to be the tail of that same press, rather
# than a request for a hold.
#
# STATED IN SECONDS WITH THE FRAME COUNT DERIVED. It was written as a bare 50, narrowed to 30, and
# both comments went on saying "0.5 s" -- which is 50 frames, not 30. Caught in review the same day.
# This file already carries the rule for that mistake: "DERIVED, never restate it."
#
# MEASURED, not padded: on route 000003aa the phantom `accelCruise` arrived 0.02 s after engagement
# -- the same physical press, re-read once cruise came on -- while the two genuine `+` presses on
# the same drives came 3.5 s and later. Anything in between has never been observed.
RESUME_TAIL_S = 0.3
RESUME_TAIL_FRAMES = int(RESUME_TAIL_S / DT_CTRL)
PRESS_SETTLE_STABLE_FRAMES = 40   # cluster unchanged this long => the driver has finished
PRESS_SETTLE_MAX_FRAMES = 600     # 6 s hard cap, so a stuck cluster cannot suspend ICBM forever
# FusionPilot: how long after the LAST press event ICBM still believes a button is held. Short,
# because the only thing it gates here is the press-settle stand-down, and every second of it is a
# second a dismissed hold keeps governing the car. The engage gate in cruise_ext keeps the long
# default -- see the note on `stale_s` there.
#
# 3.0 s, not 2.0. These timers also feed `is_ready` through `button_pressed`, so expiring one
# while a button is GENUINELY held makes ICBM ready mid-press and it starts driving the set speed
# while the driver is still pressing -- the exact on-road report the press-settle re-arm exists
# for. The longest gap measured WITHIN a held press is 0.72 s; 2.0 s was only 2.8x that, from one
# window of one route. 3.0 s is 4.2x and still nothing like the 16 s the broken path took.
ICBM_BUTTON_STALE_S = 3.0
# BluePilot: last-resort fallback -- adopt set-speed movement ICBM did not command, whatever button
# produced it. The press path above is primary; this exists because it depends on the driver's
# button arriving as one of MANUAL_OVERRIDE_BUTTONS, and on a car with flashed SCCM firmware that
# is an assumption, not a fact. If the set speed moved and ICBM has been silent for well longer
# than any command of its own could take to land, a human moved it.
#
# Keyed on MOVEMENT, never on difference: after ICBM reaches a curve target the set speed differs
# from the baseline but has stopped moving, so the curve is never adopted. And never on direction,
# which broke when ICBM reversed with an older opposite command still in flight. ICBM emits at
# 20 Hz while acting, so an idle run this long cannot overlap its own activity.
#
# 90 frames, not 30. This car's set speed was measured lagging a button press by well over 60
# frames, and ICBM's own commands land on the same slow path -- a 30-frame threshold would let a
# late-arriving command of its own be credited to the driver, recreating the curve-adoption bug
# this rule is written to avoid. The cost of being generous is only that the fallback fires a
# little later; the cost of being tight is a wrong baseline.
ADOPT_IDLE_FRAMES = 90  # 0.9 s at 100 Hz, comfortably past this car's observed set-speed lag
# The idle rule above has a hole, and it is a deadlock rather than a missed case: icbm_idle_frames
# resets to 0 on every frame ICBM commands a button, so it can only reach ADOPT_IDLE_FRAMES while
# ICBM is doing nothing. ICBM walking the set speed back down is therefore the exact state in which
# the fallback can never fire -- the one state where the driver most needs it to. Reported as the
# set speed going up on the dash and being taken back down one increment at a time.
#
# Movement AGAINST the button ICBM is holding closes it. ICBM cannot raise the set speed while
# holding decrease, so that is a human, and it needs no idle period to establish. Accumulated in
# display units rather than counted in frames, because the set speed moves in discrete steps with
# stationary gaps between them -- and reset whenever the commanded direction changes, so a stale
# command of the opposite sign still in flight cannot reach the threshold by itself. Two units:
# one step of counter-movement can be in-flight residue, a second one cannot.
COUNTER_MOVE_UNITS = 2  # display units (mph/kph) moved against ICBM's own command => a human
# After cruise is re-engaged the set speed jumps to whatever it resumes at. That is not the driver
# choosing a speed, and the fallback above cannot tell the difference -- it sees uncommanded
# movement. Without this window, CNCL + RES+ built a HOLD at the resumed speed and destroyed the
# only route this car has back to Speed Limit Assist.
CRUISE_CYCLE_SETTLE_FRAMES = 250  # 2.5 s at 100 Hz, past the resume jump on this car
# BluePilot: the same protection for the gas handoff, and it is not optional.
#
# While the handoff runs, the set speed climbs with the car -- and to the fallback that is
# uncommanded movement, because ICBM sits in HOLDING for most of it (target and cluster are equal
# by construction) rather than visibly pressing. The fallback therefore adopted the overtake as a
# driver press and left a hold at the speed the driver passed at, which is the one number they
# certainly did not choose. Caught by test_the_overtake_never_creates_a_hold, not by a drive.
#
# Outlasts the release because the cluster lags: this car was measured 60+ frames behind a press,
# so movement is still arriving well after the pedal comes up.
GAS_HANDOFF_SETTLE_FRAMES = 150  # 1.5 s at 100 Hz
# ...but end it as soon as the set speed has actually settled, rather than always serving the full
# 2.5 s. The window exists to let the resume jump land, and that is over the moment the number
# stops moving. Reported on the road as having to wait after RESUME before pressing up would take
# -- the driver's habit is RESUME then immediately press-and-hold, and the fixed window swallowed
# the press. Same shape as PRESS_SETTLE_STABLE_FRAMES: stop guessing a duration, watch the number.
CRUISE_CYCLE_STABLE_FRAMES = 40  # 0.4 s unchanged => the resume jump has landed
# BluePilot: target-drop rate limiting. Stock ACC coasts for small set-speed drops and brakes for
# large ones; capping each step and walking larger drops down over several steps keeps it coasting.
#
# The "~10 mph of set-speed drop is where stock ACC starts braking" figure this was built around
# is UNVERIFIED. Searching found the general mechanism -- ACC patents describe a coasting
# deceleration rate distinct from, and smaller than, a braking one -- but nothing Ford-specific and
# no threshold. An earlier version of this comment called it documented; it is not.
#
# It is also unlikely to be a fixed number. A controller almost certainly computes the deceleration
# it needs and coasts when closing the throttle delivers it, brakes when it does not, which makes
# the apparent boundary move with speed, grade and load. Steeper downhill, coasting sheds less, so
# the brakes come in after a smaller drop. There is therefore no single correct value here, which
# is why the onroad readout matters more than the constant: ACC pill for whether the pads are in
# use, BRAKE LAMPS for whether anyone behind was told.
#
# What WAS assumed is the goal: 8 was chosen to stay in the coasting regime because coasting was
# taken to be the only way to avoid lighting the stop lamps and telling the car behind there is a
# hazard on an ordinary curve.
#
# That assumption is wrong, and it matters. UN R13-H lights the lamps above 1.3 m/s^2 of
# automatically commanded braking -- so there is a band where ACC uses the friction brakes without
# signaling anything. Coasting is not the only quiet option.
#
# Which splits one constraint into two, and they want different things:
#   signaling  -> the lamps. Governed by 1.3 m/s^2, shown by the BRAKE LAMPS readout.
#   pad wear    -> whether the friction brakes are used at all. That is AccBrkDecel_B_Rq, which is
#                  what puts the ACC pill into BRAKE.
#
# So the honest framing for tuning this is not "keep it coasting" but "is this curve worth the
# pads". Coast when there is time; a quiet brake application is acceptable when there is not, and
# is strictly better than arriving at the bend too fast. The range runs to 15 so that trade can
# actually be explored -- it was capped at 9, which allowed only one side of it.
DEFAULT_MAX_TARGET_DROP = 12  # display units (mph/kph)
# How close actual speed must get to the current step's floor before the next step is allowed.
DROP_STEP_SETTLE_MARGIN = 2  # display units (mph/kph)

# BluePilot: the same treatment in the other direction, and DO NOT delete it on the grounds that
# its original justification was wrong. It was, but the instinct was right for a different reason.
#
# The old comment claimed Ford reads a held button as a continuous ramp. It does not -- a held
# button moves the set speed in 5 mph steps. The real mechanism is subtler and was found from the
# road, not from the code:
#
#   ICBM can only raise the set speed by injecting button presses, and to Ford those are
#   indistinguishable from the driver pressing +. A set-speed CHANGE is a driver request, and ACC
#   answers a driver request more assertively than it answers merely returning to a speed it was
#   already holding.
#
# So the two cases feel completely different from the seat, exactly as reported: when ACC slowed
# itself for a lead or a curve the set speed never moved, and recovery is ordinary speed
# maintenance -- gentle. When ICBM lowered the set speed and then raised it, every step reads as
# "the driver wants more speed now" and the car accelerates hard.
#
# That is the cost of ICBM's whole method, and this limiter is the only lever on it: fewer and
# smaller steps mean smaller surges. Lowering IcbmMaxTargetRise softens the acceleration; raising
# it recovers the number faster. They trade against each other and the right answer is the owner's
# preference, not a constant derived here.
DEFAULT_MAX_TARGET_RISE = 5  # display units (mph/kph)
# How close actual speed must get to the current ceiling before the next +5 is allowed. This is
# the DELAY BETWEEN STEPS, and it is the knob for "the increments are right but it is too slow".
#
# 4, not 2. The ceiling sits at anchor+5, so a margin of 2 made the car climb 3 mph before earning
# the next step; at 4 it needs 1. Reported as the 5 mph increments feeling right with too long a
# wait between them.
#
# Letting the set speed run further ahead of actual speed is safe here in a way the original
# comment did not credit: it is a ceiling, not a demand. Ford's ACC accelerates at its own pace and
# is capped at roughly 30% of manual braking authority in the other direction -- it does not lunge
# because the number above it got bigger.
RISE_STEP_SETTLE_MARGIN = 4  # display units (mph/kph)
# ...but never wait forever. Requiring actual speed to reach the ceiling assumes the SET SPEED is
# what is holding the car back. Very often it is not: behind slower traffic, on a climb, or while
# ACC is braking for a lead, v_ego simply never gets there and the ceiling never advances -- so
# ICBM could not raise the set speed again for the rest of the drive. Reported as being stuck at a
# low set speed while traveling well below it, with no curve in sight.
#
# Raising the set speed in that situation is harmless: it is a ceiling, not a demand, and ACC stays
# gap-limited behind the lead either way. So once the cluster has sat at the ceiling this long,
# advance regardless. When the car IS accelerating, v_ego catches up well inside this and the
# timeout never binds -- it only rescues the stalled case.
RISE_STEP_STALL_FRAMES = 300  # 3 s at 100 Hz


SEND_BUTTONS = {
  State.increasing: SendButtonState.increase,
  State.decreasing: SendButtonState.decrease,
}


class IntelligentCruiseButtonManagement:
  def __init__(self, CP: structs.CarParams, CP_SP: structs.CarParamsSP):
    self.CP = CP
    self.CP_SP = CP_SP

    self.v_target = 0
    self.v_cruise_cluster = 0
    self.v_cruise_min = 0
    self.cruise_button = SendButtonState.none
    self.state = State.inactive
    self.pre_active_timer = 0

    self.is_ready = False
    self.is_ready_prev = False
    self.v_target_up_frames = 0     # consecutive frames the planner has asked for a STEP up
    self.v_target_settled = 0       # the settled set-speed target, in display units
    self.v_target_fell_from = 0     # the level a recent fall left behind
    self.v_target_fell_frames = 0   # how long that level stays worth remembering
    self.is_metric = False

    # BluePilot: a COPY. Upstream binds the module-level dict directly, which makes these timers
    # shared mutable state between every ICBM instance and VCruiseHelperSP, which binds the same
    # object at cruise_ext.py:55 and calls update_manual_button_timers on it too. Two owners
    # double-increment it, and either one zeroing it on !ready wipes the other's view of a held
    # button -- the exact signal the press stand-down depends on. In tests it also leaks button
    # state from one case into the next, which is a good way to have a green suite and a car that
    # misbehaves.
    self.cruise_button_timers = dict(CRUISE_BUTTON_TIMER)

    # BluePilot: manual override latch. AUTO = ICBM drives the set speed toward v_target;
    # MANUAL = the driver has taken it back and ICBM stops chasing entirely.
    self.override_state = OverrideState.auto
    self.v_target_overridden = 0   # the SLA target in force when the baseline was set
    self.v_baseline = 0            # the driver's chosen speed; 0 = no baseline, follow SLA
    self.v_target_raw = 0
    self.plan_source = LongitudinalPlanSource.cruise
    self.deadline_requesting = False  # SCC-MAP ONLY: a target with a fixed place in the road.
                                      # Vision is deliberately NOT in here -- exempting it produced
                                      # 80 -> 50 mph on gentle curves and was reverted the same day.
                                      # See apply_target_drop_limit's docstring before widening it.
    self.curve_active = False        # SCC-Vision is tracking a bend right now
    self.curve_ceiling = 0           # highest target allowed for the rest of this bend
    self.v_curve_target = 0          # SCC-Vision's own ask, display units; releases the ceiling
    self.curve_release_frames = 0     # consecutive frames vision has asked for more than the ceiling
    self.curve_exit_frames = 0        # counts down after a bend; the lead bypass waits this out
    self.baseline_reset_delta = DEFAULT_BASELINE_RESET_DELTA
    self.v_cruise_cluster_prev = 0
    self.tap_phase = 0               # duty-cycle counter for small corrections
    self.icbm_idle_frames = 0
    self.counter_move_accum = 0      # set-speed movement against ICBM's own command, display units
    self.cruise_button_prev = SendButtonState.none
    self.resume_press_frames = 0     # >0 while a RESUME press is recent enough to have re-engaged
    self.set_press_frames = 0        # >0 while a SET press is recent enough to have engaged
    self.reanchor_overridden = False  # a resume kept the hold; re-measure the limit rule from here
    self.v_cluster_before_disengage = 0  # set speed when cruise last dropped; RESUME restores it
    self.cycle_decision_pending = False  # waiting for the set speed to say whether it was RESUME
    self.v_cluster_at_cycle = 0      # set speed when cruise was re-engaged; the resume jump moves it
    self.press_settle_frames = 0     # >0 while ICBM stands down after a driver press
    self.cluster_moved_since_press = False  # has the cluster moved at all since this press began
    # Starts AT the bound, not 0: before cruise has ever engaged there is no resume press for a
    # `+` to be the tail of, so nothing should be suppressed.
    self.frames_since_resume_press = RESUME_TAIL_FRAMES
    self.cluster_stable_frames = 0   # how long the set speed has been unchanged
    self.cruise_cycle_frames = 0     # >0 while a resume's set-speed jump is still settling
    self.v_cluster_at_press = 0      # set speed when the driver's press was seen
    self.press_suppressed = False    # the press happened while a curve/lead owned the target
    self.baseline_diverged = False   # has the baseline ever actually differed from SLA?
    self.speed_limit_known = False   # did the resolver have a posted limit this frame, live OR remembered?
    self.speed_limit_live = False    # is it LIVE -- speedLimitValid alone. What the clearing rule uses.
    # SLA's own target with his offset applied -- the speed the car would drive to with no hold.
    # What "I matched the SLA speed" means, and what the clearing rule compares against.
    self.v_sla_target = 0
    # BluePilot: is Speed Limit Assist in ASSIST mode -- actually allowed to move the set speed --
    # as opposed to off, informational or warning? It is the discriminator for whether a hold may
    #
    # READ FROM THE PARAM, NOT FROM SLA'S MESSAGE, and that distinction is the whole bug of
    # 2026-08-20. This was `LP_SP.speedLimit.assist.enabled`, on the stated belief that it was
    # "SLA's own copy of `SpeedLimitMode == assist`". IT IS NOT. `longitudinal_planner.py` publishes
    # `assist.enabled = sla.is_enabled`, and `is_enabled` is the STATE MACHINE's output --
    # `state in ENABLED_STATES`, which excludes `inactive`. SLA's own param copy is `self.enabled`,
    # a different attribute that is never published at all.
    #
    # The two come apart on exactly the gesture this policy is about. `update_state_machine_*`
    # moves ACTIVE -> INACTIVE on `v_cruise_cluster_changed` -- a manual `+/-` press. So the press
    # captured the hold and, in the same frame, knocked SLA out of ENABLED_STATES; the policy below
    # then read "not in assist mode" and destroyed the hold it had just made. Self-cancelling, so
    # no hold could ever survive a press.
    #
    # MEASURED, route 0000039c vs 0000039a: 7,473 frames of `vBaseline > 0` (all source `press`)
    # before this field was introduced, and ZERO across the whole later drive after it. Reported as
    # *"when I changed the speed with plus and minus it changed the ICBM speed and didn't do a
    # hold"*, which is precisely what a hold deleted on its own creation frame looks like.
    #
    # The mode is CONFIGURATION -- it changes when he changes a setting, never because of where the
    # car is or what he just pressed -- so it belongs with the other cached params and must never
    # again be inferred from a runtime state.
    self.sla_assist_enabled = False
    # BluePilot: which mechanism last captured the hold. Logged, no longer shown on screen -- the
    # badge tag that used to display it existed to settle whether the press path was dead, and it
    # is not (see RESUME_BUTTONS above). Kept because it is the only way to tell the two capture
    # paths apart in a route. Not cleared by clear_baseline: the question it answers is "did the
    # press path EVER fire this drive", so it has to survive the hold it describes.
    self.baseline_source = BaselineSource.none
    self.cruise_enabled_prev = False
    self.cruise_enabled = False      # current engagement; hold_suppressed reads it
    self.v_target_valid = False

    # BluePilot: target-drop rate limiting
    self.params = Params()
    self.frame = 0
    self.max_target_drop = DEFAULT_MAX_TARGET_DROP
    self.drop_anchor = 0
    self.max_target_rise = DEFAULT_MAX_TARGET_RISE
    self.rise_anchor = 0
    self.rise_stall_frames = 0   # cluster sitting at the ceiling with actual speed not catching up
    self.lead_present = False    # a vehicle ahead; the set speed is then a ceiling, not a demand

    # BluePilot: a hold pinned to this place, in display units; 0 = none here. Edge-triggered --
    # see apply_pinned_hold.
    self.pinned_hold = 0
    self.pinned_hold_prev = 0

    # BluePilot: the driver is on the throttle, so ACC is not driving and the set speed is free to
    # follow. See apply_gas_handoff.
    self.gas_pressed = False
    self.gas_handoff_active = False
    self.gas_handoff_frames = 0   # >0 while handoff movement may still be reaching the cluster

    # BluePilot: radar-blind lead detector currently owns the target
    self.unconfirmed_lead_commanding = False
    self.unconfirmed_lead_state = UnconfirmedLeadState.inactive

    # BluePilot: the ACC follow gap being asked for, republished for the car layer. This controller
    # only gates the request -- the closed loop that actually presses the button, and the readback
    # it closes on, live in opendbc's ford/gap_control.py where the camera's ACCDATA_3 already is.
    self.gap_control_enabled = False
    self.gap_target = 0


  def update_params(self) -> None:
    if self.frame % int(PARAMS_UPDATE_PERIOD / DT_CTRL) == 0:
      self.max_target_drop = self.params.get("IcbmMaxTargetDrop", return_default=True)
      self.max_target_rise = self.params.get("IcbmMaxTargetRise", return_default=True)
      self.baseline_reset_delta = self.params.get("IcbmBaselineResetDelta", return_default=True)
      self.gap_control_enabled = self.params.get_bool("IcbmGapControl")
      # See `self.sla_assist_enabled` for why this is a param read and not a message field.
      # `return_default=True` so a device that has never written the key reads SLA's own default
      # rather than raising -- and a missing key must not silently mean "no holds allowed".
      self.sla_assist_enabled = (self.params.get("SpeedLimitMode", return_default=True)
                                 == SpeedLimitMode.assist)

  @property
  def v_cruise_equal(self) -> bool:
    return self.v_target == self.v_cruise_cluster

  def _adopt_target(self, v_target: int) -> int:
    """Take this target as the settled one and start any future bounce's case over."""
    self.v_target_up_frames = 0
    self.v_target_settled = v_target
    return v_target

  def settle_target(self, v_target: int) -> int:
    """Take a fall at once; make a BOUNCE BACK to a level just left prove it is not a blip.

    Display units, not m/s: this is the number the button logic aims at, and the oscillation it
    exists to absorb was measured in whole mph on the dash.

    See the SETTLE_S note at the top of this file for why the filter is time rather than magnitude,
    why it is aimed at reversals rather than at rises, and why there is no timeout that would
    eventually adopt a value that never settles.
    """
    if self.v_target_fell_frames > 0:
      self.v_target_fell_frames -= 1
    else:
      self.v_target_fell_from = 0

    if self.v_target_settled <= 0:
      # Nothing adopted yet -- the first frame is not a move, it is the starting point.
      return self._adopt_target(v_target)

    if v_target < self.v_target_settled:
      # A FALL, taken on this frame, always. Aiming lower is the conservative action and the
      # exit-ramp incident of 2026-08-23 came from a set speed that could not fall. Remember the
      # level being left, so an immediate bounce back to it is recognisable as one.
      self.v_target_fell_from = max(self.v_target_settled, self.v_target_fell_from)
      self.v_target_fell_frames = REVERSAL_MEMORY_FRAMES
      return self._adopt_target(v_target)

    if v_target <= self.v_target_settled + SETTLE_EPS:
      # Level, or a ramp crossing one mph. No delay.
      return self._adopt_target(v_target)

    if self.v_target_fell_frames <= 0 or v_target > self.v_target_fell_from + SETTLE_EPS:
      # A rise to somewhere NEW. Nothing here meters an honest climb: a speed limit going up, a
      # curve releasing, a lead pulling away. The owner's rule that behind a car the set speed may
      # go anywhere it likes stays exactly as it was.
      return self._adopt_target(v_target)

    # A BOUNCE: back up to a level we left moments ago. This is the shape measured on route
    # 000003ae and nothing else looks like it. Make it hold.
    self.v_target_fell_frames = REVERSAL_MEMORY_FRAMES   # keep the memory alive while it repeats
    self.v_target_up_frames += 1
    if self.v_target_up_frames >= SETTLE_FRAMES:
      return self._adopt_target(v_target)

    return self.v_target_settled

  def update_calculations(self, CS: car.CarState, LP_SP: custom.LongitudinalPlanSP) -> None:
    speed_conv = CV.MS_TO_KPH if self.is_metric else CV.MS_TO_MPH

    # A LOCAL, not instance state. It was `self.v_target_ms_last`, the memory
    # `apply_hysteresis(..., HYST_GAP)` needed across frames -- and that call was a no-op with the
    # gap left at 0.0. Nothing carries across frames here any more, and leaving it on `self` invited
    # someone to reintroduce a filter on the raw planner value, which is exactly the placement that
    # destroyed a driver hold. The settle that replaced it runs further down, below v_target_raw.
    v_target_ms = LP_SP.vTarget

    self.v_target = round(v_target_ms * speed_conv)
    self.v_cruise_min = get_minimum_set_speed(self.is_metric)
    self.v_cruise_cluster = round(CS.cruiseState.speedCluster * speed_conv)

    # BluePilot: reject planner targets that aren't real requests, rather than substituting a
    # remembered speed for them.
    #
    # longitudinal_planner clamps vTarget to V_CRUISE_MAX (145 kph) whenever carState.vCruise is
    # still V_CRUISE_UNSET, and publishes 0 before it has run at all. Neither is a speed anyone
    # asked for, so ICBM holds the current cluster speed instead of chasing them.
    #
    # This replaces an earlier fallback that substituted a kph-valued engage speed (vEgo * MS_TO_KPH)
    # into a target that is mph-valued on an imperial device -- at 65 mph that produced v_target=105,
    # which then re-tripped the same >= 90 guard and pinned the state machine. 145 kph rounds to
    # exactly 90 mph, which is why the guard fired on every unset frame.
    v_target_unset = round(V_CRUISE_MAX * CV.KPH_TO_MS * speed_conv)
    self.v_target_valid = 0 < self.v_target < v_target_unset
    if not self.v_target_valid:
      # THE DRIVER'S HOLD FIRST, the cluster only when there is no hold. Holding the cluster
      # unconditionally freezes the set speed wherever it stands, and if nothing starts asking again
      # it stays there: on route 00000348 (2026-08-11) every planner candidate was unset at once and
      # the set speed sat at 38 through a full stop and the restart, with a hold of 50 that could
      # never pull it back, because the hold is applied to a target that has already been replaced by
      # the cluster. Only cancelling and re-engaging cleared it.
      #
      # The root cause is fixed in longitudinal_planner -- Speed Limit Assist no longer displaces the
      # cruise baseline while it has no limit to follow, so the all-unset frame should not recur. This
      # is the second line of defense, and it is the RIGHT default independently: when nothing at all
      # is asking, the driver's own number is the correct thing to aim at. A hold is only ever set by
      # a deliberate gesture, so aiming at it can never invent a speed nobody chose.
      self.v_target = self.v_baseline if self.v_baseline > 0 else self.v_cruise_cluster

    # BluePilot: keep the planner's own target before the limiters touch it. Every override
    # decision compares against this, never against self.v_target -- the limiters clamp toward the
    # cluster, so a limited value drifts for reasons that have nothing to do with what was planned.
    # Comparing post-limiter values is exactly how the original re-arm bug worked.
    self.v_target_raw = self.v_target
    self.plan_source = LP_SP.longitudinalPlanSource

    # BluePilot: and only NOW is the shake filtered -- see the SETTLE_S note at the top. This sits
    # BELOW the `v_target_raw` capture deliberately. An earlier version of this filter sat above it,
    # on the theory that a shaking raw value could arm and disarm the override at
    # `baseline_reset_delta`. That theory was wrong twice over: the reset delta is 10 mph and the
    # measured shake was 3, so it could never have armed anything -- and filtering there put every
    # hold and override decision on a delayed value, which DESTROYED a driver hold outright in
    # `test_icbm_own_recovery_is_never_adopted`. The oscillation costs button presses, so the fix
    # belongs on the number that drives button presses and nowhere else.
    self.v_target = self.settle_target(self.v_target)

    # THE DIVERGENCE ARM THAT LIVED HERE IS GONE, 2026-08-22, in the same review that found the
    # stale-limit bug below. It was added this morning to fix a hold walked back to SLA's number,
    # by observing divergence on frames the press path skipped -- and by the afternoon the clearing
    # rule had stopped gating on the flag at all, which made the arm inert the moment it landed.
    #
    # `baseline_diverged` survives ONLY as a published diagnostic (`selfdrived.py` ->
    # `icbm.baselineDiverged`), and it earned that: reading it off a route is how the whole
    # clearing investigation was settled. It is maintained by the clearing rule itself, against the
    # number that rule actually uses. Nothing reads it to decide anything, and no new code should.

    # Did SLA have a posted limit at all this frame? Not the same as "is SLA the active source" --
    # a limit can exist while a curve owns the target. See where baseline_diverged is seeded.
    try:
      resolver = LP_SP.speedLimit.resolver
      self.speed_limit_known = bool(resolver.speedLimitValid or resolver.speedLimitLastValid)
      # LIVE, NOT REMEMBERED -- `speedLimitValid` ALONE, and the distinction is the whole finding.
      #
      # `speed_limit_known` deliberately includes `speedLimitLastValid`, which means "there WAS a
      # limit recently". That is right for the seeding it was written for. It is wrong for the
      # clearing rule: leaving a 35 zone onto an unmapped road keeps `speedLimitFinalLast` at 35,
      # so a hold he then presses to 35 on the NEW road would be destroyed by a number belonging to
      # a road he has already left -- on exactly the roads holds matter most.
      #
      # Caught in review 2026-08-22, hours after the source gate was removed. The gate that was
      # removed (`plan_source == speedLimitAssist`) implied a LIVE limit as a side effect, so
      # replacing it with `speed_limit_known` widened the rule without anyone deciding to.
      self.speed_limit_live = bool(resolver.speedLimitValid)
      # SLA'S OWN NUMBER, offset included -- the speed it would drive to if the hold went away.
      # Added 2026-08-22, and it is the value the hold should have been compared against all along.
      self.v_sla_target = (round(float(resolver.speedLimitFinalLast) * speed_conv)
                           if self.speed_limit_live else 0)
    except (AttributeError, KeyError):
      self.speed_limit_known = False
      self.speed_limit_live = False
      self.v_sla_target = 0
    # Is there a mapped corner ahead with a deadline on it? NOT "is SCC-Map the source this frame",
    # which is a different and much less stable question: when the map and vision targets are close
    # the plan source alternates between them frame by frame. Measured on the 2026-08-07 exit, the
    # source read sccVision/sccMap/sccVision on three consecutive frames. Gating the drop-limiter
    # bypass on that would have let it re-arm on every other frame and seeded a fresh anchor from
    # the current cluster each time, so the exemption would flicker instead of apply.
    #
    # `active` is the map controller's own statement that it is asking for something, and it stays
    # true across the whole approach.
    #
    # SCC-VISION COUNTS TOO, as of 2026-08-08. A curve is a fixed place in the road, so its target
    # carries a deadline exactly like a mapped corner does, and metering it spends road that was
    # needed. Measured on the exit that prompted this: vision asked for 52 mph at t+257.6 and the
    # limiter held the set speed at 58 for two and a half seconds, breaking free only when SCC-Map
    # fired and bypassed it. That is the approach, which is the one stretch where runway is the
    # entire problem.
    #
    # Keeping vision limited was justified here last night by its docstring -- it "ramps its own
    # target smoothly through the ENTERING state". The log says otherwise: 72, 52, 46, 42 in under
    # two seconds. Reasoning from a docstring instead of data, again.
    try:
      scc = LP_SP.smartCruiseControl
      # VISION IS METERED AGAIN as of 2026-08-08, reverting the same day's change. Removing the cap
      # for curves produced 80 -> 50 mph on two slight freeway curves, with traffic behind reacting.
      # The cap was doing load-bearing work nobody had identified: SCC-Vision's target on a gentle
      # bend is far lower than the bend needs, and metering the DESCENT meant the curve was usually
      # past before the set speed ever arrived. Take the cap away and the car actually goes there.
      #
      # So the fix for that has to be the vision TARGET, not the rate at which ICBM chases it. Until
      # then the cap stays, because it is the only thing standing between a bad target and the road.
      self.deadline_requesting = bool(scc.map.active)
      self.curve_active = bool(scc.vision.active)
      # Vision's OWN ask, in display units. The curve ceiling needs this rather than the post-limiter
      # target: metering walks v_target through every value between the old speed and the curve's,
      # so ratcheting on it drags the ceiling below what the bend ever actually demanded.
      self.v_curve_target = round(scc.vision.vTarget * speed_conv) if self.curve_active else 0
      # Re-armed while a bend is tracked, counted down after. See the lead bypass in
      # apply_target_rise_limit for why the exit needs covering separately from the bend itself.
      self.curve_exit_frames = (CURVE_EXIT_LINGER_FRAMES if self.curve_active
                                else max(0, self.curve_exit_frames - 1))
    except (AttributeError, KeyError):
      self.deadline_requesting = False
      # curve_active and its ceiling latched here before: neither was cleared, so one bad frame left
      # the ceiling pinned at whatever it held with nothing able to reset it.
      self.curve_active = False
      self.curve_ceiling = 0
      self.v_curve_target = 0
      self.curve_release_frames = 0
      self.curve_exit_frames = 0
    self.v_target = self.apply_baseline(self.v_target)

    v_ego_conv = round(CS.vEgo * speed_conv)
    # Reads self.deadline_requesting, set just above -- SCC-Map is exempt. See the docstring.
    self.v_target = self.apply_target_drop_limit(v_ego_conv)

    # BluePilot: the radar-blind lead detector supersedes everything above, including the drop
    # limiter. That limiter exists to keep Ford's ACC coasting through routine speed-limit and
    # curve changes; metering out a hazard decel over several settling steps is exactly wrong.
    # Its target is already the MPC's own geometry-scaled plan, floored at Ford's 20 mph minimum,
    # so it needs no rate limiting of its own. The same channel carries the restore request that
    # returns the set speed once the event resolves.
    unconfirmed_lead = LP_SP.unconfirmedLead
    self.unconfirmed_lead_state = unconfirmed_lead.state
    self.unconfirmed_lead_commanding = unconfirmed_lead.state in UNCONFIRMED_LEAD_COMMANDING
    if self.unconfirmed_lead_commanding:
      self.v_target = round(unconfirmed_lead.vTarget * speed_conv)
      self.v_target_valid = True
      self.drop_anchor = 0

    # BluePilot: the rise limiter runs last, and unlike the drop limiter it is NOT bypassed for
    # the radar-blind lead. Rising is never the urgent direction -- an ACTIVE hazard only ever
    # lowers the target -- so the only thing this can meter is the RESTORING half, which returns
    # the set speed after the hazard has cleared and has no reason to be abrupt.
    self.v_target = self.apply_target_rise_limit(v_ego_conv)
    self.v_target = self.apply_gas_handoff(CS, v_ego_conv)

  def apply_target_drop_limit(self, v_ego_conv: int) -> int:
    """BluePilot: cap how far below the set speed ICBM may command in one step.

    Ford's stock ACC treats a large single drop in set speed as a reason to brake hard; smaller
    drops it handles by coasting. So rather than commanding a curve or speed-limit target all at
    once, hold at (anchor - max_target_drop) and only take the next step once the car has actually
    slowed to that floor. Net deceleration is the same, but it arrives as coasting.

    Only decreases are limited -- increases are what the driver or the ceiling asked for and are
    rate-limited naturally by ICBM emitting one button press per cycle.

    SCC-MAP IS EXEMPT, and the reason is in map_controller.py rather than here. Its v_target is the
    corner speed, and SmartCruiseControlMapDecel is not a rate applied to it -- it is the TRIGGER
    DISTANCE. The test is "am I within the distance needed to reach the corner speed at this
    deceleration", so the target appears at exactly the moment the deceleration has to begin. It
    arrives with a deadline already attached, and metering it out afterwards spends road that was
    already budgeted, which guarantees the corner speed is missed.

    Measured on a freeway exit, 2026-08-07: SCC-Map asked for 39 mph at 67 and ICBM commanded 68,
    then 56, then sat at 56 for 6.5 s waiting for the car to catch up before asking for 44. Twelve
    seconds to work 80 down to 30, by which point the ramp was gone. Every step was correct and the
    sum of them was far too slow.

    SCC-VISION IS NOT EXEMPT, having been exempt for a few hours on 2026-08-08. The deadline
    argument for it is sound and the outcome was still bad: 80 -> 50 mph on two slight freeway
    curves, traffic behind reacting. Reported the same day and reverted.

    What that revealed is that the cap was covering for something else. SCC-Vision asks for a speed
    a gentle bend does not need, and metering the descent meant the curve was normally past before
    the set speed got there -- so the bad target never showed. Remove the cap and the car goes
    where the target actually points. The cure is the TARGET, not the rate ICBM chases it at, and
    until that is fixed the cap is the only thing between it and the road.

    SCC-Map stays exempt: its targets come from mapped geometry, it was the case that prompted all
    of this, and the exit it fixed drove well.

    WHAT THIS WAS ACTUALLY FOR, from the owner who asked for it (2026-08-08): *"I originally planned
    that feature so that the brake lights wouldn't be turning on all the time. But then I found out
    that Ford ACC occasionally coasts and when it brakes, the lights don't come on for a bit."*

    So the purpose was never gentleness -- it was the STOP LAMPS. Worth having written down,
    because the comment here used to say the guard was against a violent application, and that
    reading is what made the cap look load-bearing everywhere rather than in one case.

    Two findings have since narrowed it, and they point the same way:

      - Ford cannot be violent. Measured at 1.31 m/s^2 on the 2026-08-08 exit -- the UN R13-H lamp
        threshold, its ceiling -- and it holds that rate whatever the size of the drop. Metering
        never bought gentleness, only delay.
      - Ford already does some of this itself: it coasts of its own accord, and light applications
        do not light the lamps at all. That last part is a THRESHOLD, not a delay, which is the
        useful correction -- carstate_ext.py reads BrkLamp_B_Rq (what traffic sees) separately from
        AccBrkTot_A_Rq (what ACC asked for) precisely because ACC applies brake too light to trigger
        the lamps.

    AND THE RULE THAT GOVERNS THIS CAR IS NOT A RATE. The 1.3 m/s^2 figure quoted elsewhere in this
    repo is UN R13-H, which is UNECE. This car is in the US, where FMVSS 108 S5.5.4 says the stop
    lamps are activated UPON APPLICATION OF THE SERVICE BRAKES, and NHTSA has interpreted a bare
    deceleration threshold as not a permissible trigger on its own -- a stop lamp signals that the
    operator intends to diminish speed BY BRAKING, not that the vehicle is slowing.

    So the line is whether the service brakes are applied at all, and speed reduction achieved by
    coasting or powertrain drag correctly lights nothing. That is what the owner wanted from this
    feature and it is the better-founded version of it: keeping each step small keeps Ford in the
    coast regime, so no service brake, so no lamps -- rather than keeping a magnitude under a
    threshold that does not apply here.

    It also means AccBrkDecel_B_Rq, the boolean, tracks the legal trigger far better than
    AccBrkTot_A_Rq does. The 1.31 m/s^2 measured on the exit says the brakes were firmly applied
    there; it is evidence of braking, not the criterion for it.

    What survives is narrow and real: on a target with no deadline, keeping each step small keeps
    Ford in the coasting regime rather than the braking one, and the lamps stay dark. That is worth
    having for a speed limit and worth nothing when the road has set a deadline.

    The exemption is keyed on a deadline-bearing source ASKING, not on it winning the frame -- see
    where deadline_requesting is set for why the frame's plan source is the wrong test.
    """
    if self.deadline_requesting:
      self.drop_anchor = 0
      return self.v_target

    if self.max_target_drop <= 0:  # 0 disables the limiter
      self.drop_anchor = 0
      return self.v_target

    if self.v_target >= self.v_cruise_cluster:
      self.drop_anchor = 0
      return self.v_target

    if self.drop_anchor == 0:
      self.drop_anchor = self.v_cruise_cluster

    floor = self.drop_anchor - self.max_target_drop
    if self.v_target >= floor:
      return self.v_target  # the whole requested drop fits inside one step

    # Requested drop is larger than one step. Advance the anchor only once the cluster has reached
    # the current floor AND actual speed has caught up to it, so each step is a separate, gentle
    # request rather than a continuous slide that Ford reads as one big drop.
    if self.v_cruise_cluster <= floor and v_ego_conv <= floor + DROP_STEP_SETTLE_MARGIN:
      self.drop_anchor = self.v_cruise_cluster
      floor = self.drop_anchor - self.max_target_drop

    return max(self.v_target, floor)

  @property
  def hold_suppressed(self) -> bool:
    """Is something other than the driver's number currently owning the target?

    Cruise being OFF counts, and matters more on this car than it looks: the owner drives with
    MADS engaged essentially always, so "openpilot steering with ACC off" is a normal cruising
    state rather than a moment in passing. The hold survives that disengagement by design and the
    badge stays on screen -- but +/- there map to setCruise, which engages cruise and DISCARDS the
    hold rather than adjusting it. Blue would be a lie for as long as that lasts.

    While this is true a set-speed press cannot change the hold -- it gives a momentary bump that
    the curve or hazard then reclaims. That is deliberate (a curve is a physics limit the baseline
    only caps), but it means the press does not do what a press normally does, so the UI greys the
    HOLD badge while it holds.

    The unconfirmed-lead term is not redundant with the plan-source test: the hazard path replaces
    v_target directly and leaves longitudinalPlanSource alone, so a plan-source test by itself
    misses it and a press during a radar-blind slowdown would re-baseline at the lowered speed --
    the same defect as the curve case, reached a different way.
    """
    return bool(self.v_baseline > 0 and
                (not self.cruise_enabled
                 or self.plan_source not in BASELINE_SOURCES
                 or self.unconfirmed_lead_commanding))

  def apply_baseline(self, v_target: int) -> int:
    """BluePilot: substitute the driver's chosen speed for the speed-limit component.

    With no baseline this is the identity. With one:

      speed limit / cruise  -> the baseline outright. This is the component the driver overrode,
                               so their number replaces it. Above or below the posted limit; the
                               baseline wins either way and SLA does not pull them back.
      curve / map / lead    -> min(planned, baseline). A curve target is a physics limit and is
                               honored as-is; the baseline only ever caps, never raises it. This
                               is what keeps SCC slowing you down while overridden.

    Because the baseline is a value rather than a mode, everything downstream -- the state machine,
    the rate limiters, the hazard path -- keeps working unchanged.
    """
    if self.v_baseline <= 0:
      return v_target

    if self.plan_source in BASELINE_SOURCES:
      return self.v_baseline

    return min(v_target, self.v_baseline)

  def apply_target_rise_limit(self, v_ego_conv: int) -> int:
    """BluePilot: cap how far above the set speed ICBM may command in one step.

    The mirror of apply_target_drop_limit, and needed for the same reason the drop version is:
    ICBM does not tap the button, it holds it. Coming out of a curve or leaving a low-limit zone,
    the target jumps back to cruise speed all at once and Ford ramps continuously until it gets
    there, which is a much harder acceleration than a driver would ask for.

    Hold at (anchor + max_target_rise) and only take the next step once actual speed has caught
    up. Net acceleration ends up the same, but it arrives in stages instead of one pull.

    NOTHING RISES WHILE A CURVE IS BEING TRACKED. Measured on the 2026-08-08 exit: SCC-Map lost the
    ramp at t+268.1 and vision's own target bounced to 47-51 for a couple of seconds before settling
    at 21. ICBM chased the peak, took the set speed from 42 up to 51, and the car ACCELERATED from
    41 to 44 mph in the middle of an off-ramp -- then had to walk all the way back down, reaching 20
    about three seconds later than it could have. He got to the tight part still doing 28.

    A curve target that briefly rises is noise, not the bend ending. The bend has ended when vision
    says so by going inactive, and until then the target is a LIMIT: follow it down, never up.
    """
    if self.curve_active:
      # Anchored to where the curve STARTED and ratcheted downward, never to the live cluster. A
      # ceiling of (current cluster + tolerance) is not a ceiling at all: each step it allows raises
      # the cluster, which raises the ceiling, which allows another step. That version metered the
      # climb to 1 mph a step and still arrived at 52 -- the test caught it.
      #
      # SCOPED TO ONE BEND, and that scoping is the whole correctness argument. `curve_active` is
      # SCC-Vision's `active`, which on a highway can stay true continuously -- it is not a per-bend
      # pulse. Resetting the ceiling only when it falls made this a one-way ratchet for the entire
      # drive: every dip anywhere permanently lowered the cap for everything after it, the HOLD badge
      # stayed grey the whole time because the source never returned to a baseline one, and the only
      # thing left that could raise the speed was the gas pedal, since apply_gas_handoff runs after
      # this and bypasses it. The owner reported exactly that, and reported reaching for the pedal
      # frequently, which is the same fact from the driver's seat.
      #
      # So the release condition is VISION'S OWN TARGET recovering above the ceiling. That is the
      # bend letting go, or a wider one replacing it. Safe to key on where the live cluster is not:
      # vision's target is upstream of every button ICBM sends, so there is no feedback path. Keying
      # on the cluster self-raises -- each allowed step lifts the cluster, which lifts the ceiling,
      # which allows another step, and it walked to 52 one mile per hour at a time.
      # A bend caps nothing while vision is asking for MORE than the car is already doing -- there is
      # no bend to cap. That is the arming condition, and it has to be sustained, because vision's
      # target rising is not by itself the bend ending: the logged off-ramp spike read 47, 48, 49, 51
      # before settling at 21, so an instant release re-anchors on exactly the noise this rejects. A
      # bend genuinely letting go holds its higher ask indefinitely; the spike held 3.3 s.
      if self.v_curve_target > self.v_cruise_cluster:
        self.curve_release_frames += 1
      else:
        self.curve_release_frames = 0
      released = self.curve_release_frames >= CURVE_RELEASE_FRAMES
      if released:
        # Stays released, with no ceiling at all, until vision asks for less than the cluster again --
        # which resets the counter and re-arms below. Re-anchoring to the live cluster while still
        # released instead rebuilds the ceiling at the speed the bend left behind, and the set speed
        # then climbs 1 mph per release interval rather than recovering.
        self.curve_ceiling = 0
      else:
        # The cluster ALONE. Seeding from vision's ask -- max(cluster, v_curve_target) -- takes the
        # anchor straight from the noise burst, which read 47 on the frame the bend began.
        if self.curve_ceiling == 0:
          self.curve_ceiling = self.v_cruise_cluster
        # Ratchets on the METERED target, deliberately. Using the planner's raw ask collapses the
        # ceiling to the corner speed on frame one, and it then fights the drop limiter -- the limiter
        # says coast 80 -> 68 and the ceiling answers 40. Tried it; two tests said no.
        self.curve_ceiling = min(self.curve_ceiling, self.v_target)
        if self.v_target > self.curve_ceiling + CURVE_RISE_TOLERANCE:
          self.rise_anchor = 0
          return self.curve_ceiling + CURVE_RISE_TOLERANCE
    else:
      self.curve_ceiling = 0
      self.curve_release_frames = 0

    if self.max_target_rise <= 0:  # 0 disables the limiter
      self.rise_anchor = 0
      return self.v_target

    # Behind a vehicle the set speed does not bind, so there is nothing to meter.
    #
    # This limiter exists to stop the car lunging when the target jumps back up -- coming out of a
    # curve, or leaving a low-limit zone. That only happens on an open road, where the set speed is
    # what ACC chases. With a lead ahead ACC is gap-limited: it follows the car in front and the
    # set speed is a ceiling it never reaches, so raising it changes nothing about how the car
    # drives. Metering there bought no safety and cost real behavior -- it is what left the set
    # speed stuck low behind traffic, since actual speed could never catch up to release the step.
    #
    # The owner's framing, which is the right one: behind a car, set the speed to anything, because
    # that car is probably driving correctly. It is only with no one ahead that the number matters.
    # A LEAD SKIPS THIS -- EXCEPT JUST AFTER A BEND. The owner's rule stands and is a better
    # discriminator than any timer: behind a car, set the speed to anything, because that car is
    # probably driving correctly. ACC is gap-limited there and the set speed is a ceiling it never
    # reaches.
    #
    # It has one hole, and it is the moment the bend ends. The curve ceiling above is scoped to
    # SCC-Vision being ACTIVE, so it lets go the instant vision does -- and vision lets go while the
    # car is still physically in the corner. With a lead present nothing else meters the recovery, so
    # the set speed jumps the whole way at once. Route 00000348 t+1060, 2026-08-11, lead at 31-38 m:
    #
    #   t+1058  36 mph  dash 34  sccVision   latAcc 1.91   (the bend peaked at 2.32 a second later)
    #   t+1060  34 mph  dash 33  cruise      latAcc 0.99   <- vision releases, still cornering
    #   t+1064  40 mph  dash 50
    #
    # 17 mph in four seconds, and the car pulled about 1.4 m/s^2 coming out of the bend. His report:
    # "it slowed down to 30 but then hit the gas way too fast while I was still in the curve."
    #
    # So the bypass waits out the exit. During the linger the ordinary limiter applies, which walks
    # the same recovery up in 5 mph steps instead of one jump, and RISE_STEP_STALL_FRAMES still
    # guarantees it completes even though actual speed cannot catch up behind a lead. Everywhere else
    # -- ordinary following, traffic, a limit change with a car ahead -- the rule is unchanged.
    if self.lead_present and self.curve_exit_frames == 0:
      self.rise_anchor = 0
      self.rise_stall_frames = 0
      return self.v_target

    if self.v_target <= self.v_cruise_cluster:
      self.rise_anchor = 0
      return self.v_target

    if self.rise_anchor == 0:
      self.rise_anchor = self.v_cruise_cluster

    ceiling = self.rise_anchor + self.max_target_rise
    if self.v_target <= ceiling:
      return self.v_target  # the whole requested rise fits inside one step

    at_ceiling = self.v_cruise_cluster >= ceiling
    self.rise_stall_frames = self.rise_stall_frames + 1 if at_ceiling else 0
    caught_up = v_ego_conv >= ceiling - RISE_STEP_SETTLE_MARGIN
    stalled = self.rise_stall_frames >= RISE_STEP_STALL_FRAMES
    if at_ceiling and (caught_up or stalled):
      self.rise_anchor = self.v_cruise_cluster
      ceiling = self.rise_anchor + self.max_target_rise
      self.rise_stall_frames = 0

    return min(self.v_target, ceiling)

  def apply_gas_handoff(self, CS: car.CarState, v_ego_conv: int) -> int:
    """BluePilot: keep the set speed with the car while the driver is accelerating on the pedal.

    Without this, using the throttle leaves the set speed behind. controlsd raises
    gasPressedOverride, which clears longActive, which sets cruiseControl.override, which fails
    ICBM's readiness check -- so ICBM stops commanding for as long as the pedal is down. Accelerate
    from a 35 zone onto a 65 road and the number is still 35 when you lift, at which point Ford's
    ACC decelerates you back to it. Then the rise limiter walks it up five at a time.

    Reported as entrance ramps being painfully slow. That was read as a ramp problem; most of it is
    this, and it happens on any manual acceleration.

    Only ever RAISES, and only to the speed the driver has actually reached. The set speed is a
    ceiling, not a demand: moving it while the driver owns the throttle commands nothing at all, and
    it means lifting off is a handoff rather than a pullback. Whatever the number should really be
    is then settled by the ordinary path -- the drop limiter walks it back toward the speed-limit or
    curve target by coasting, which is exactly what it exists for.

    Deliberately does NOT touch the baseline. Pressing the accelerator is not the same statement as
    pressing SET: it usually means "past this one", not "my cruising number is now 78". Treating it
    as a hold would silently rewrite the driver's number on every overtake.
    """
    self.gas_handoff_active = bool(self.gas_pressed and CS.cruiseState.enabled and v_ego_conv > 0)
    if not self.gas_handoff_active:
      return self.v_target

    # Past the rise limiter on purpose. That limiter exists to stop ACC lunging when the target
    # jumps up, and ACC is not driving here -- metering the number would just recreate the lag.
    if v_ego_conv > self.v_target:
      self.rise_anchor = 0
      self.rise_stall_frames = 0
      return v_ego_conv
    return self.v_target

  def update_state_machine(self) -> custom.IntelligentCruiseButtonManagement.SendButtonState:
    self.pre_active_timer = max(0, self.pre_active_timer - 1)

    # HOLDING, ACCELERATING, DECELERATING, PRE_ACTIVE
    if self.state != State.inactive:
      if not self.is_ready:
        self.state = State.inactive

      else:
        # PRE_ACTIVE
        if self.state == State.preActive:
          if self.pre_active_timer <= 0:
            if self.v_cruise_equal:
              self.state = State.holding

            elif self.v_target > self.v_cruise_cluster:
              # BluePilot: don't push a cluster the driver hasn't set yet -- wait for their first SET.
              # The former MAX_REASONABLE_TARGET / MAX_INITIAL_INCREASE caps are gone: unreasonable
              # targets are now rejected in update_calculations, and the upper bound on what ICBM may
              # request belongs to the configurable speed ceiling, not to a fixed +5 from engage speed.
              if self.v_cruise_cluster == 0:
                self.state = State.holding
              else:
                self.state = State.increasing

            elif self.v_target < self.v_cruise_cluster and self.v_cruise_cluster > self.v_cruise_min:
              self.state = State.decreasing

        # HOLDING
        elif self.state == State.holding:
          if not self.v_cruise_equal:
            self.state = State.preActive

        # ACCELERATING
        elif self.state == State.increasing:
          if self.v_target <= self.v_cruise_cluster:
            self.state = State.holding

        # DECELERATING
        elif self.state == State.decreasing:
          if self.v_target >= self.v_cruise_cluster or self.v_cruise_cluster <= self.v_cruise_min:
            self.state = State.holding

    # INACTIVE
    elif self.state == State.inactive:
      if self.is_ready and not self.is_ready_prev:
        self.pre_active_timer = int(INACTIVE_TIMER / DT_CTRL)
        self.state = State.preActive

    send_button = SEND_BUTTONS.get(self.state, SendButtonState.none)

    # TAP FOR SMALL CORRECTIONS, HOLD FOR LARGE ONES.
    #
    # This car moves the set speed 1 mph for a TAP and 5 mph for a HELD button. ICBM asserts the
    # button continuously until the cluster crosses the target, which is a hold -- so when it needs a
    # 1 mph correction it asks for 5, overshoots, and then asks for 5 the other way.
    #
    # That is the oscillation measured on route 00000361 at t+2704, when the limit changed to 25: the
    # target sat at a constant 27 while the dash bounced 26 -> 29 -> 26, eighteen reversals in twenty
    # seconds. "It raised and lowered my cruise over and over." The controller was not failing to
    # settle on a reachable number; it had no way to ASK for a small change.
    #
    # So the shape of the request changes, and nothing else does. Three earlier attempts tried to
    # make the state machine tolerate being a mile per hour off, and each broke something the tests
    # defend: a deadband stalled a curve descent at 63 instead of 40, because the drop limiter needs
    # exact arrival before releasing its next step; keying re-entry on target movement made ICBM
    # overshoot a driver press by 6. The transitions are left completely alone here -- only the duty
    # cycle of the output changes, so `increasing` still means increasing and arrives exactly.
    if send_button != SendButtonState.none and abs(self.v_target - self.v_cruise_cluster) <= TAP_BAND:
      self.tap_phase += 1
      if self.tap_phase % TAP_CYCLE_FRAMES >= TAP_ON_FRAMES:
        send_button = SendButtonState.none
    else:
      self.tap_phase = 0

    return send_button

  def update_readiness(self, CS: car.CarState, CC: car.CarControl) -> None:
    update_manual_button_timers(CS, self.cruise_button_timers, stale_s=ICBM_BUTTON_STALE_S)

    # BluePilot: gasPressedOverride is the ONLY event carrying ET.OVERRIDE_LONGITUDINAL, so on this
    # car cruiseControl.override means precisely "the driver is on the throttle" -- nothing else.
    # ICBM stays ready through it so the set speed can follow the car up; see apply_gas_handoff.
    # Any OTHER reason the override appears must still stand ICBM down, hence the gasPressed test
    # rather than simply dropping the term.
    gas_override = CC.cruiseControl.override and CS.gasPressed
    ready = (CC.enabled and (not CC.cruiseControl.override or gas_override)
             and not CC.cruiseControl.cancel and not CC.cruiseControl.resume)
    button_pressed = any(self.cruise_button_timers[k] > 0 for k in self.cruise_button_timers)

    # BluePilot: Clear button timers when cruise is disabled to prevent stale presses
    # This ensures that when cruise is re-enabled, ICBM doesn't see stale button presses
    if not ready:
      for k in self.cruise_button_timers:
        self.cruise_button_timers[k] = 0

    self.is_ready = ready and not button_pressed

  def update_manual_override(self, CS: car.CarState) -> None:
    """BluePilot: capture, hold and discard the driver's baseline.

    Every ButtonEvent reaching here is a genuine driver press. ICBM's own virtual presses cannot
    appear: panda returns transmitted frames with src = bus | CAN_RETURNED_BUS_OFFSET (0x80), and
    Ford's CANParser binds Steering_Data_FD1 to bus 0, so the injected frames are dropped by the
    parser before carstate_ext ever decodes them. No sent-command bookkeeping is needed to tell the
    two apart -- if a set-speed button shows up in CS.buttonEvents, a human pressed it.
    """
    cruise_enabled = CS.cruiseState.available and CS.cruiseState.enabled
    self.cruise_enabled = cruise_enabled
    cruise_cycled = cruise_enabled and not self.cruise_enabled_prev
    self.cruise_enabled_prev = cruise_enabled
    # Distance from the last RESUME press, for the resume-tail guard below. Keyed on the resume
    # itself rather than on the engage edge: a `+` pressed deliberately a moment after engaging is
    # ordinary and must still create a hold, but a `+` arriving milliseconds after a `resumeCruise`
    # is the same physical press being re-read.
    if any(b.type.raw == ButtonType.resumeCruise and b.pressed for b in CS.buttonEvents):
      self.frames_since_resume_press = 0
    else:
      self.frames_since_resume_press = min(self.frames_since_resume_press + 1, RESUME_TAIL_FRAMES)

    # Remember a RESUME press across the engage transition -- see RESUME_BUTTONS.
    if any(b.type.raw in RESUME_BUTTONS and b.pressed for b in CS.buttonEvents):
      self.resume_press_frames = RESUME_PRESS_MEMORY_FRAMES

    # Remember a SET press the same way -- see SET_BUTTONS.
    if any(b.type.raw in SET_BUTTONS and b.pressed for b in CS.buttonEvents):
      self.set_press_frames = SET_PRESS_MEMORY_FRAMES

    # Remember the set speed while engaged; at the moment cruise drops this is what RESUME will
    # restore, and comparing against it is how RESUME is told from SET without a button event.
    # Frozen while a decision is pending, because it IS the evidence for that decision. Updating
    # it on the frames between the cycle and the verdict overwrites the pre-cancel speed with the
    # post-engage one, both sides of the comparison become equal, and every cycle reads as a
    # resume. Instrumented and confirmed: correct at the cycle frame, clobbered on the next.
    #
    # Same shape as press_suppressed needing to be latched -- evidence gathered at an instant must
    # not be re-derived later from a world that has moved on.
    if cruise_enabled and not cruise_cycled and not self.cycle_decision_pending:
      self.v_cluster_before_disengage = self.v_cruise_cluster
    elif not cruise_enabled:
      self.cycle_decision_pending = False

    # Re-engaging. RESUME keeps the hold, SET hands the set speed back to SLA. The button event
    # settles it when it arrives; otherwise the decision waits for the set speed to land.
    if RE_ARM_ON_CRUISE_CYCLE and cruise_cycled:
      if self.resume_press_frames > 0:
        self.reanchor_overridden = True
        self.cycle_decision_pending = False
      elif self.set_press_frames > 0:
        # THE BUTTON SAYS SET, SO DO NOT ASK THE SPEEDS. Reported from the road 2026-08-25:
        # cruise off while accelerating onto the highway, SET pressed at ~75, and the ~22 mph hold
        # from the surface street before survived instead of the speed going to SLA's 75.
        #
        # The comment above this block already says "the button event settles it when it arrives"
        # -- but only the RESUME half was ever built. A SET fell through to the behavioural
        # detector, which compares the DASH set speed against vEgo. That detector exists because
        # the button used to be mislabelled (see values_ext.py: this wheel sends ResInc, and
        # `CcAslButtnSetIncPress` reached openpilot as `setCruise` when off). It is not needed for
        # SET any more, and it is strictly worse than the event: the dash carries the OLD set speed
        # for some frames after the press, so both sides of `resumed` read 22 and the cycle scored
        # as a resume.
        #
        # ORDER MATTERS AND RESUME WINS. Both flags can be live -- CNCL emits `resumeCruise` when
        # off, so cancel-then-set inside 1.5 s sets both. On ambiguity keep the hold: discarding one
        # is destructive and silent, keeping one the driver can always change. That is the same
        # rule the detector below states for itself.
        self.clear_baseline()
        self.cycle_decision_pending = False
      else:
        # Only worth deferring when there is a hold at stake. Otherwise the very first engagement
        # of a drive leaves a decision pending that fires 2.5 s later and wipes a hold created in
        # the meantime -- which is what eight tests caught.
        self.cycle_decision_pending = self.v_baseline > 0
      self.cruise_cycle_frames = CRUISE_CYCLE_SETTLE_FRAMES
      self.v_cluster_at_cycle = self.v_cruise_cluster
      self.resume_press_frames = 0
      self.set_press_frames = 0
      return

    # Decide once the resume jump has landed. Deferring is the point: clearing on the cycle frame
    # threw the hold away before the evidence existed.
    if self.cycle_decision_pending:
      landed = (self.v_cruise_cluster != self.v_cluster_at_cycle
                and self.cluster_stable_frames >= CRUISE_CYCLE_STABLE_FRAMES)
      if landed or self.cruise_cycle_frames == 0:
        resumed = abs(self.v_cruise_cluster - self.v_cluster_before_disengage) <= RESUME_MATCH_TOLERANCE
        # Ford's SET jumps to the CURRENT VEHICLE SPEED, floored at the 20 mph minimum. That is the
        # POSITIVE signature of a SET, and it was never checked -- the old test read "did not land on
        # the previous set speed" as proof of a SET, which is not the same claim.
        #
        # It cost him a hold. Route 0000033c, t+480: cruise re-engaged at 2 mph after a stop, the set
        # speed landed at 69 against 62 before the disengage, 7 apart with a tolerance of 2, so the
        # cycle was read as a SET and his 75 was discarded. But a SET at 2 mph lands at 20, nowhere
        # near 69 -- so there was positive evidence AGAINST a set and it went unused. A standstill
        # re-engage is not the driver handing the speed back to Speed Limit Assist.
        #
        # Landing on neither number is not evidence of anything, and discarding a hold is
        # destructive and silent while keeping one the driver can always change. So ambiguity keeps
        # it: only a landing that actually looks like a SET clears.
        v_ego_set = max(round(CS.vEgo * (CV.MS_TO_KPH if self.is_metric else CV.MS_TO_MPH)),
                        self.v_cruise_min)
        looks_like_set = abs(self.v_cruise_cluster - v_ego_set) <= SET_MATCH_TOLERANCE
        if resumed:
          self.reanchor_overridden = True
        elif looks_like_set:
          self.clear_baseline()
        self.cycle_decision_pending = False

    # A pin re-applies here: after the cycle bookkeeping above, so a resume's set-speed jump is not
    # mistaken for it, and before the press path below, so a real press in the same frame wins.
    self.apply_pinned_hold(cruise_enabled)

    # While the driver is pressing, the baseline follows the cluster. It therefore settles wherever
    # they stop, and holding the button through several increments records the final speed rather
    # than the first. v_target_overridden captures the SLA target being rejected, once per override.
    # Only while cruise is actually engaged. On this wheel RES+ and SET- are combined buttons, so
    # the same signals that adjust the speed are also how cruise is resumed and set -- and a press
    # made while disengaged reports as setCruise, which would otherwise create a HOLD at whatever
    # speed the car resumed to. That would break the only way the driver has to hand the speed back
    # to Speed Limit Assist: CNCL, then RES+. You cannot hold a speed that cruise is not driving.
    # THE TAIL OF A RESUME PRESS IS NOT A REQUEST FOR A HOLD. Reported from the road as *"a hold
    # got set without me doing plus and minus"* and measured on route 000003aa, 2026-08-22:
    #
    #     809.83  enab=False  resumeCruise    <-- he presses RES+ to resume, nothing else
    #     809.85  enab=False  resumeCruise
    #     809.86  enab=True   accelCruise     <-- SAME physical press; cruise engaged mid-way
    #     809.88  enab=True   accelCruise     -> HOLD CREATED at 32 mph on a 35 road
    #
    # RES+ is one button whose MEANING is derived per frame from the cruise state: `resumeCruise`
    # while off, `accelCruise` while on. And this car's SCCM clears the button bit between frames,
    # so a single physical hold of RES+ arrives as a burst of press/release cycles rather than one
    # event. The moment cruise engages part-way through that burst, the remaining cycles read as
    # `accelCruise` -- and the press path below turns the first one into a brand-new hold at
    # whatever speed the car happens to be doing.
    #
    # RESUME_TAIL_S SEPARATES THE TWO CASES CLEANLY, measured rather than picked: the phantom
    # arrived 0.02 s after engagement, while the two genuine + presses came 3.5 s and later.
    # Only hold CREATION is suppressed -- raising an existing hold is untouched, because that is a
    # press against a hold he already has and cannot be the tail of a resume.
    # THE WHOLE PRESS BLOCK IS SKIPPED, not just the capture inside it. Guarding only the capture
    # was tried first and did nothing: `override_state = OverrideState.manual` is set unconditionally
    # a few lines below it, and the press-settle path then assigns `v_baseline` from the cluster --
    # so the hold appeared anyway, at the same 32 mph, with the capture never having run.
    #
    # Only CREATION is suppressed. With a hold already up, `override_state` is manual and the tail
    # falls through to the ordinary raise path, which is right: raising a hold he already has is not
    # inventing one, and RES+ keeping an existing hold is the documented contract.
    # ONLY `accelCruise` CAN BE A RESUME TAIL. RES+ is the button whose meaning flips with the
    # cruise state; SET- is a different physical button and cannot be re-read as a `+`. Gating the
    # whole of MANUAL_OVERRIDE_BUTTONS swallowed a deliberate SET- pressed just after resuming, for
    # a defect that button cannot produce. Narrowed in review, 2026-08-22.
    tail_buttons = [b for b in CS.buttonEvents
                    if b.type.raw in MANUAL_OVERRIDE_BUTTONS and b.pressed]
    resume_tail_creates = (self.frames_since_resume_press < RESUME_TAIL_FRAMES
                           and self.override_state != OverrideState.manual
                           and all(b.type.raw == ButtonType.accelCruise for b in tail_buttons))
    if cruise_enabled and not resume_tail_creates and tail_buttons:
      if self.override_state != OverrideState.manual:
        self.v_target_overridden = self.v_target_raw
        # Seeded from whether a posted limit exists at all, not flat False.
        #
        # Reported 2026-08-06: he held a speed on a road OpenStreetMap had no limit for; the limit
        # was acquired later and matched his hold exactly, and the hold did not clear. The clear
        # rule needs the baseline to have DIVERGED from SLA's target before bare equality counts,
        # which stops a fresh hold being deleted on its first frame. But that flag was only ever
        # set while speedLimitAssist was the active source -- and with no limit known, it never is.
        # So the flag stayed False, the limit arrived matching, and the equality branch was skipped.
        #
        # A hold created where SLA has no number to offer has already diverged from it: there was
        # nothing to agree with. Seeding True makes the first genuine agreement clear the hold,
        # while a hold created against a known limit still has to leave and come back.
        self.baseline_diverged = not self.speed_limit_known
      self.override_state = OverrideState.manual
      # A press while something ELSE is holding the set speed down must not redefine the hold.
      #
      # The baseline is normally "wherever the driver's press settles", which is right when the
      # driver is choosing a cruising speed. It is badly wrong mid-curve: SCC has already dragged
      # the set speed from 70 to 45, so one + press re-baselined the hold to 50 and the 70 was
      # gone for the rest of the drive. Measured exactly that, 70 -> 50 from a single press.
      #
      # A curve or a lead is a physics limit that the baseline only ever caps, never raises -- so
      # a press during one cannot mean "my cruising number is now 50". It means "ease off a bit
      # here", which Ford honours directly on the set speed anyway. The hold is left alone, the
      # curve target reasserts itself when the stand-down ends, and the original number is still
      # there when the curve is over.
      #
      # BASELINE_SOURCES is the test: under cruise/speedLimitAssist the driver IS choosing the
      # number, so capture it. Under sccVision/sccMap/a lead, something else owns the target.
      # Latched at the moment of the press, not re-evaluated per frame. The stand-down outlives
      # the curve that started it -- once the source flips back to speedLimitAssist a per-frame
      # test stops guarding and the stand-down captures the suppressed speed anyway, which is
      # exactly how the first attempt at this still ended up with 70 -> 50.
      if self.press_settle_frames == 0:
        self.press_suppressed = self.hold_suppressed
      if not self.press_suppressed:
        self.v_baseline = self.v_cruise_cluster
        self.baseline_source = BaselineSource.press
      # Only the FIRST press of a sequence sets the reference. Re-arming it on every press would
      # move the goalposts to wherever the set speed had already got to, so "has it moved yet"
      # could never become true while the driver kept pressing.
      if self.press_settle_frames == 0:
        self.v_cluster_at_press = self.v_cruise_cluster
        self.cluster_moved_since_press = False
      self.press_settle_frames = PRESS_SETTLE_MAX_FRAMES
      self.cluster_stable_frames = 0
      return

    # Fallback: set speed moved, ICBM has been silent long enough that it cannot be responsible.
    # Runs before the manual-only guard below so it can CREATE a baseline, not just update one.
    fallback_idle = self.icbm_idle_frames >= ADOPT_IDLE_FRAMES
    fallback_counter = self.counter_move_accum >= COUNTER_MOVE_UNITS
    if (cruise_enabled and self.cruise_cycle_frames == 0 and self.gas_handoff_frames == 0
        and self.v_cruise_cluster != self.v_cruise_cluster_prev
        and (fallback_idle or fallback_counter)):
      if self.override_state != OverrideState.manual:
        self.v_target_overridden = self.v_target_raw
        # Seeded from whether a posted limit exists at all, not flat False.
        #
        # Reported 2026-08-06: he held a speed on a road OpenStreetMap had no limit for; the limit
        # was acquired later and matched his hold exactly, and the hold did not clear. The clear
        # rule needs the baseline to have DIVERGED from SLA's target before bare equality counts,
        # which stops a fresh hold being deleted on its first frame. But that flag was only ever
        # set while speedLimitAssist was the active source -- and with no limit known, it never is.
        # So the flag stayed False, the limit arrived matching, and the equality branch was skipped.
        #
        # A hold created where SLA has no number to offer has already diverged from it: there was
        # nothing to agree with. Seeding True makes the first genuine agreement clear the hold,
        # while a hold created against a known limit still has to leave and come back.
        self.baseline_diverged = not self.speed_limit_known
        self.v_cluster_at_press = self.v_cruise_cluster_prev
        # RESET WITH THE ANCHOR. `cluster_moved_since_press` means "the cluster has moved since
        # THIS anchor", so re-anchoring without clearing it leaves the new stand-down starting with
        # `moved` already True -- and `settled` can then fire on the first stable frame, ending the
        # stand-down mid-gesture. That is the documented failure the stand-down exists to prevent:
        # the baseline froze and ICBM walked the set speed back down while the button was held.
        self.cluster_moved_since_press = False
      self.override_state = OverrideState.manual
      # Same rule as the press path: under a curve or a lead, something other than the driver owns
      # the target, so movement there must not redefine the hold. Without this the press path's
      # protection is worthless -- the fallback re-baselines a frame later, which is what a probe
      # caught: the hold still fell 70 -> 50 with the press path already fixed.
      # A PIN OWNS ITS VALUE, not just its label. This branch is the INFERRED path -- it fires on
      # set-speed movement with no button event behind it, and route 00000379 showed it firing all
      # drive on a road with no posted limit. Letting it rewrite v_baseline replaces the number the
      # owner deliberately pinned to this place with whatever the cluster happens to read, and the
      # pin is edge-triggered and already spent, so nothing restores it.
      #
      # A real button press is a different matter and still wins: that site sets both the value and
      # `press` unconditionally, a few lines up. Overriding a pin by hand is deliberate; drifting
      # off one because the cluster moved is not.
      if not self.hold_suppressed and self.baseline_source != BaselineSource.pinned:
        self.v_baseline = self.v_cruise_cluster
        # NEVER downgrade a press. The question this field exists to answer is "does the press
        # path fire at all on this car", and a last-writer-wins field answers a different one.
        #
        # It read "I" on every drive and I concluded the press path was dead. It is not: a press
        # arms the stand-down, ICBM goes idle by definition, and the driver's held button keeps
        # stepping the set speed for seconds afterwards -- so the idle fallback fires and relabels
        # a capture the press path had already made. Instrumented: press at the button event, still
        # press after the first 5 mph jump at idle 67, overwritten to fallback at idle 128.
        #
        # I nearly deleted a working code path on that reading.
        #
        # No `pinned` clause is needed here, and adding one was reverted: this whole block is nested
        # inside the value guard above, which already returns early for a pin. Mutation-testing it
        # proved it unreachable -- removing the clause broke no test. A guard that cannot fire, with
        # a comment claiming it is load-bearing, is worse than no guard at all.
        if self.baseline_source != BaselineSource.press:
          self.baseline_source = (BaselineSource.fallbackIdle if fallback_idle
                                  else BaselineSource.fallbackCounter)
      self.press_settle_frames = PRESS_SETTLE_MAX_FRAMES
      self.cluster_stable_frames = 0
      # Spent. The stand-down now suppresses ICBM's output, so nothing is left to move against.
      self.counter_move_accum = 0
      return

    if self.override_state != OverrideState.manual:
      return

    # Adopt any cluster movement ICBM did not command.
    #
    # This is the whole ballgame, and getting it wrong is what made the first two attempts fail on
    # the road. cruiseState.speedCluster LAGS the button: the release event arrives before the
    # cluster reflects the new set speed. Freezing the baseline when the button timer clears
    # therefore records the speed the driver was leaving, not the one they chose -- and ICBM then
    # drives straight back to it. Reproduced: with the cluster updating 3+ frames after release, a
    # single tap from 55 to 56 was pulled back to 55 every time.
    #
    # So do not key off the button at all. If the set speed moved and ICBM was not the one moving
    # it, a human was, whatever the timers say. That covers taps, holds, lag and repeat presses
    # with one rule. The idle requirement keeps the tail of ICBM's own commanded change from being
    # read as a fresh press; a curve is safe regardless, since the cluster stops moving once ICBM
    # reaches its target and an unmoved cluster is never adopted.
    # Standing down after a press (see PRESS_SETTLE_STABLE_FRAMES / PRESS_SETTLE_MAX_FRAMES): ICBM
    # emits nothing, so the set speed can only be moving because the driver is still pressing.
    # Track it, and keep standing down until it stops moving AND no button is still held. Ending
    # this on a timer instead is what broke it on the road -- the baseline froze at the pre-press
    # speed on any car whose cluster reports slower than the timer.
    if self.press_settle_frames > 0:
      # Honour what was true when the press happened, not what is true now -- see press_suppressed.
      #
      # A pin is exempt here for the same reason as the fallback capture above, and this is the site
      # that actually bit: the INFERRED fallback arms this stand-down too, so guarding only the
      # capture left the pin's value rewritten here a few lines later. Found by tracing every write
      # to v_baseline rather than by reading, after the first guard did not fix the test.
      #
      # A genuine button press is unaffected: the press path sets `press` (and the value)
      # unconditionally before this runs, so `pinned` is only still set when nobody pressed anything.
      if not self.press_suppressed and self.baseline_source != BaselineSource.pinned:
        self.v_baseline = self.v_cruise_cluster
      # The set speed must have actually MOVED before "stable" means anything. Without this the
      # counter reaches its threshold while the cluster is merely slow to report, the stand-down
      # ends on the pre-press value, and the press is undone -- the same shape of bug as the fixed
      # timer it replaced. A press that never moves the cluster (already at Ford's ceiling) is
      # released by PRESS_SETTLE_MAX_FRAMES instead.
      # LATCHED, because a ROUND TRIP IS NOT "NEVER MOVED". FusionPilot, 2026-08-23.
      #
      # Comparing the live cluster against the value at the FIRST press means a sequence that ends
      # where it began reads as motionless -- and "walk the hold back to SLA's number" is exactly
      # that sequence. He starts at SLA's number, presses up to make a hold, presses back down to
      # SLA's number: first press 22, last frame 22, `moved` False. So `settled` never fires, the
      # stand-down runs its full 6 s cap, and the clearing rule below it is unreachable until then.
      #
      # That is why it is ALWAYS this gesture he reports. Fourth time: "SLA wants 20+2 and I set a
      # hold up and then back to 22 and it didn't clear."
      if self.v_cruise_cluster != self.v_cluster_at_press:
        self.cluster_moved_since_press = True
      moved = self.cluster_moved_since_press
      held = any(self.cruise_button_timers[k] > 0 for k in self.cruise_button_timers)
      settled = moved and not held and self.cluster_stable_frames >= PRESS_SETTLE_STABLE_FRAMES
      if settled:
        self.press_settle_frames = 0

      # The divergence latch is NOT armed here. It is armed in `update_calculations`, which no early
      # return in this method can skip -- see the note there.
      return

    if not self.v_target_valid:
      return

    # Nothing below this discards the hold while cruise is DISENGAGED.
    #
    # Both rules decide whether the driver's number still applies to the driving ICBM is doing.
    # While disengaged ICBM is doing nothing, and the driver is usually disengaged precisely
    # because they are turning off the road -- which changes the posted limit, which fired the
    # 10 mph rule below and destroyed the hold silently, mid-turn, before RESUME was ever pressed.
    # Reported exactly that way: hold, cancel for a turn, resume, hold gone.
    #
    # Freezing here does not skip the judgement, it defers it: v_target_overridden is re-anchored
    # on resume (see the cycle branch), so the rule then measures from the road actually being
    # driven rather than from one left behind several minutes ago.
    if not cruise_enabled:
      return

    # A resume re-asserts the driver's number for wherever they now are, so the limit-change rule
    # below has to measure from here rather than from the zone the hold was created in.
    if self.reanchor_overridden and self.plan_source == LongitudinalPlanSource.speedLimitAssist:
      self.v_target_overridden = self.v_target_raw
      self.reanchor_overridden = False

    # Returning the set speed to exactly what Speed Limit Assist wants hands control back. This is
    # the second way out of a hold, alongside cancel + re-engage, and the only one that does not
    # require disengaging cruise.
    #
    # NOT GATED ON `baseline_diverged` ANY MORE. The paragraph that used to sit here explained
    # that flag as load-bearing -- "requiring the baseline to have actually been somewhere else
    # first" -- and it went on saying so for hours after the gate was removed, directly above the
    # code that removed it. Deleted rather than annotated: two places in one file disagreeing about
    # one fact is how a line of investigation gets closed on the wrong evidence.
    #
    # What that gate genuinely protected -- not deleting a fresh hold on its first frame -- is done
    # by the press path and the press-settle stand-down, both of which return before this line.
    # A LEVEL RULE, NOT AN EDGE ONE, since 2026-08-22. His words, twice: *"if I set the speed back
    # to the SLA speed, there shouldn't be a hold"* and *"I don't want a hold if I +/- back to SLA
    # speed."* That is a statement about the CURRENT state, and the code was asking a question about
    # history instead.
    #
    # `baseline_diverged` used to gate this: the hold had to have DIFFERED from SLA at some point
    # before equality would clear it. Measured on route 000003ac -- a hold born at 35 with
    # `v_target_raw` already 35 sat there for 164.7 SECONDS with the source speedLimitAssist the
    # whole time, because it had never differed and the latch could never arm. It survived until
    # something else killed it. He photographed exactly this and asked, correctly, whether the hold
    # was still there.
    #
    # THE LATCH'S ONE REAL JOB IS ALREADY DONE ELSEWHERE. Its comment says it exists "to stop a
    # fresh hold being deleted on its first frame" -- but this rule is unreachable during a press:
    # the press path returns near the top of this method, and the press-settle stand-down returns
    # above it. By the time execution arrives here the press has finished and the driver has
    # deliberately landed on SLA's number. There is nothing left to protect.
    #
    # `baseline_diverged` is maintained here PURELY as a published diagnostic -- nothing reads it to
    # decide anything any more. An earlier version of this note claimed the reset-delta rule and the
    # no-limit seeding still read it; they do not, and that claim was already false when written.
    # COMPARED AGAINST SLA'S OWN NUMBER, NOT THE WINNING PLAN'S. Third and final version of this
    # rule, 2026-08-22, after he asked the same question three times and was right every time.
    #
    # `v_target_raw` is whatever source is WINNING the plan this frame. So the old rule had to be
    # gated on `plan_source == speedLimitAssist`, and could therefore only run on the frames SLA
    # happened to win. Any curve, any lead, any drop to `cruise` and the check simply did not
    # execute -- he photographed exactly that, a hold of 35 against a posted 30 + 5 offset with
    # `BRAKE 0.1` on screen, meaning something else owned the target at that instant.
    #
    # `v_sla_target` is SLA's own number with his offset applied -- the speed the car would drive to
    # if the hold went away. That is the thing "I matched SLA" means, and it is meaningful whether
    # or not SLA is currently winning. With it, the source gate is not needed and is gone.
    #
    # The two earlier versions each fixed a real case and neither was enough:
    #   * the latch never armed during a press, so a hold WALKED BACK never cleared (route a8)
    #   * a hold BORN equal never armed it either, so it sat 164.7 s (route ac)
    #   * and both were still gated on SLA winning, which is this one.
    #
    # A PINNED HOLD IS EXEMPT, which is his whole sentence and not a footnote to it: *"if I set my
    # hold that I had back to the SLA speed, I want that hold gone UNLESS IT'S PINNED."* A pin is a
    # deliberate statement about a PLACE -- he chose this speed here, on an earlier drive, and
    # matching the posted limit today does not retract that. Clearing it would delete the pin's
    # effect every time the limit happened to agree with it, on exactly the roads pins are for.
    # EXACT EQUALITY, and a ±1 tolerance was TRIED AND REVERTED on 2026-08-22. Review raised that
    # `v_sla_target` rounds a continuous m/s value while `v_baseline` comes off a dash that moves in
    # whole units, so with a percentage offset the two might never land on the same integer and the
    # rule would never fire.
    #
    # The concern does not hold, and the existing tests are what showed it. `v_target` is
    # `round(v_target_ms * speed_conv)` -- ICBM aims at SLA's number ALREADY ROUNDED, by the
    # same `round`, so the cluster lands on exactly the integer this compares against. The two
    # cannot straddle.
    #
    # And the tolerance was actively harmful: it deleted a hold one increment above the limit, which
    # is an ordinary thing to want and which `TestPressSurvivesAnyClusterLag` asserts. A tolerance
    # wide enough to absorb rounding is wide enough to swallow the smallest hold he can express.
    if self.speed_limit_live and self.v_sla_target > 0:
      if self.v_baseline != self.v_sla_target:
        self.baseline_diverged = True
      elif self.baseline_source != BaselineSource.pinned:
        self.clear_baseline()
        return

    # Discard the baseline when the posted limit itself moves materially. A new zone is a new
    # situation the driver has not ruled on, and carrying a 55-zone baseline into a 35 zone is
    # exactly the failure worth avoiding.
    #
    # Source-gated deliberately. Magnitude alone cannot tell "entered a school zone" from
    # "SCC-Vision is slowing for a bend", and a curve must never discard the baseline: it ends by
    # itself in seconds, whereas a limit change persists. Only speedLimitAssist counts.
    if (self.plan_source == LongitudinalPlanSource.speedLimitAssist and
        abs(self.v_target_raw - self.v_target_overridden) >= self.baseline_reset_delta):
      self.clear_baseline()

  def apply_pinned_hold(self, cruise_enabled: bool) -> bool:
    """BluePilot: re-apply a hold that was pinned to this place on an earlier drive.

    EDGE-triggered, not level-triggered, and that is the whole design. It fires once on entering the
    radius and then gets out of the way, so the pin decides the NUMBER and WHERE and nothing else.
    Everything after is an ordinary hold: the driver can adjust it and their adjustment stands,
    curves still slow the car, hazards still override, and the usual clearing rules apply.

    Level-triggering would have meant re-asserting the pinned number every frame inside the zone,
    which takes the set speed away from the driver for as long as they are in it -- the same
    "fighting the driver" failure the manual override latch exists to prevent.

    Leaving and re-entering re-arms it, which is what makes a pin useful on a road driven daily.
    """
    # A hold is a number for cruise to drive to, so nothing is applied while cruise is off -- but
    # the edge must not be CONSUMED there either. Recording it unconditionally meant a pin entered
    # with cruise off was marked as already-fired, and since the engagement frame itself returns
    # early (the cruise-cycle bookkeeping above), the pin was gone by the frame after. Every drive
    # that began inside a pin's radius -- a fresh boot, a driveway or a workplace lot within 60 m of
    # one -- silently lost it, which is most of the point of pinning a road you drive daily.
    #
    # Only the drop to 0 is tracked while disengaged, so leaving the radius still re-arms.
    if not cruise_enabled:
      if self.pinned_hold == 0:
        self.pinned_hold_prev = 0
      return False

    fired = self.pinned_hold > 0 and self.pinned_hold != self.pinned_hold_prev
    self.pinned_hold_prev = self.pinned_hold
    if not fired:
      return False

    # A LIVE HOLD OUTRANKS A REMEMBERED ONE. Measured on route 0000033c at t+333, 2026-08-11: he set
    # 75 by hand at t+134, drove into a zone with 70 pinned from an earlier drive, and the pin
    # silently replaced his number. He reported it as "my hold dropped by 5 mph, which was strange",
    # and strange is exactly right -- nothing he did caused it and nothing on screen said why.
    #
    # A pin is a record of what he wanted on some previous drive. A hold he set minutes ago is what
    # he wants now, and when the two disagree the live one wins. The pin still applies when there is
    # no hold, which is the case it exists for, and a pin still replaces a hold that came from
    # another pin -- that is one remembered number superseding another, not a preference being
    # overwritten.
    #
    # The edge is consumed above whether or not it applies, so a blocked pin does not retry every
    # frame inside the radius. Leaving and re-entering still re-arms it.
    # ...AND "LIVE" MEANS CHOSEN, NOT MERELY PRESENT. Narrowed 2026-08-25, when retiring
    # `enforce_hold_policy` made a hold exist in every SLA mode: with the old gate a pin could then
    # never fire on the roads pins are FOR, because there was always some baseline sitting there.
    # That is the 2026-08-16 failure ("no limit means no hold" killing pinned holds outright)
    # arriving from the other direction.
    #
    # `baseline_source` already separates the two, and route 00000379 is the evidence that they are
    # genuinely different things: a hold was up for 36.5% of that drive reading `fallbackIdle` --
    # the path that INFERS a press from set-speed movement -- while he pressed SET five times and
    # nothing else. Nearly every hold on it was inferred rather than chosen.
    #
    #   press                      he pressed +/- at a number. A decision. A pin defers to it.
    #   fallbackIdle / Counter     the set speed moved and we inferred a hold. Carried in from the
    #                              last road, not a statement about this place. The pin wins.
    #   pinned                     one remembered number superseding another. The pin wins, as before.
    #
    # Route 0000033c is still honoured, which is the point of using the source rather than "has he
    # pressed since entering the radius": his 75 there came from a real `+`, so it reads `press`
    # and the pin still stands aside. His words for the alternative -- *"my hold dropped by 5 mph,
    # which was strange"* -- remain the thing this gate exists to prevent.
    if self.v_baseline > 0 and self.baseline_source == BaselineSource.press:
      return False

    if self.override_state != OverrideState.manual:
      self.v_target_overridden = self.v_target_raw
      self.baseline_diverged = False
    self.override_state = OverrideState.manual
    self.v_baseline = self.pinned_hold
    self.baseline_source = BaselineSource.pinned
    self.v_cluster_at_press = self.v_cruise_cluster
    self.cluster_moved_since_press = False   # resets with the anchor -- see the note in the fallback
    return True

  def enforce_hold_policy(self) -> None:
    """BluePilot: WITHOUT SPEED LIMIT ASSIST, EVERYTHING IS A HOLD. Rewritten 2026-08-25.

    His spec, and it is simpler than the three versions before it:

      *"Without SLA, then everything should be a hold, and function identically to how stock Ford
       ACC functions, just with the added benefit of holds being remembered."*

    THIS USED TO DESTROY THE BASELINE whenever SLA was not in assist mode, and he is the one who
    spotted why that expired:

      *"no posted limit means no hold was probably an old rule back before we had the max and hold
       speeds combined."*

    Confirmed from the history. The policy landed 2026-08-15 ("+/- just moves the max speed");
    `max_box_state` landed 2026-08-21 ("the big number is what the car is being driven to"); the
    HOLD badge was deleted 2026-08-22. For those six days a hold really was a SECOND number on the
    screen with its own badge, so "do the max speed, not the little number" was a meaningful
    instruction. Since the badge went, the big number IS the hold -- so his 2026-08-19 wording,
    *"max speed and hold to be together and the same"*, now describes every mode rather than just
    assist mode, and the distinction this rule enforced changes nothing he can SEE.

    What it still did was throw away the two things he wants kept: the number persisting across a
    cruise cycle, and the pinned-holds path having a trace to learn from.

    WHAT IS LEFT IS STOCK ACC PLUS MEMORY. With SLA off or informational, `plan_source` is never
    `speedLimitAssist`, so `apply_baseline` returns the baseline for the cruise component and caps
    curves and leads with it -- the car is driven to his number and still slows for corners and
    traffic. The addition is that the number is recorded, so `observe_hold` can suggest a pin.

    NOTHING REPLACES IT, deliberately: a hold is whatever the driver last asked for, in every SLA
    mode. `apply_pinned_hold`'s gate was narrowed in the same change to keep pins working now that
    a baseline is usually present -- see the note there.
    """
    return

  def clear_baseline(self) -> None:
    self.override_state = OverrideState.auto
    self.v_baseline = 0
    self.v_target_overridden = 0
    self.baseline_diverged = False
    self.reanchor_overridden = False
    self.counter_move_accum = 0

  def update_gap_request(self, CS: car.CarState, LP_SP: custom.LongitudinalPlanSP) -> None:
    """BluePilot: decide whether a longitudinal feature's follow-gap request is allowed through.

    Deliberately thin. Everything that needs the camera's readback -- refusing to start when the
    gap cannot be read, restoring the driver's setting, standing down when the driver presses their
    own button -- happens one layer down in ford/gap_control.py, because that is where the readback
    is. Duplicating any of it here would create a second opinion about the same state, and the two
    would drift.

    What DOES belong here is the gating that has nothing to do with the button:

      - the owner's toggle, so the feature can be switched off outright;
      - cruise actually being engaged. Pressing the gap button with ACC off changes a setting the
        driver will meet later with no idea why it moved. The requester has no business asking in
        that state and this is the cheapest place to be certain of it.

    The request is passed through per-frame with no memory. Silence restores, so anything that
    stops asking -- including this method deciding not to pass it on -- ends the lease.
    """
    if not self.gap_control_enabled or not CS.cruiseState.enabled:
      self.gap_target = 0
      return

    self.gap_target = int(getattr(LP_SP, "accGapRequest", 0) or 0)

  def run(self, CS: car.CarState, CC: car.CarControl, LP_SP: custom.LongitudinalPlanSP, is_metric: bool,
          lead_present: bool = False, pinned_hold: int = 0) -> None:
    if self.CP_SP.pcmCruiseSpeed:
      return

    self.is_metric = is_metric
    self.lead_present = lead_present
    self.pinned_hold = int(pinned_hold)
    self.gas_pressed = bool(CS.gasPressed)

    self.update_params()
    self.update_calculations(CS, LP_SP)
    self.update_readiness(CS, CC)
    self.update_gap_request(CS, LP_SP)

    # BluePilot: how long ICBM has gone without commanding anything, counted unconditionally --
    # not only while a baseline is held, or it would read 0 at the exact moment a fresh press
    # needs it. self.cruise_button is still last frame's value here, which is the one that would
    # have caused any cluster movement visible now.
    self.icbm_idle_frames = 0 if self.cruise_button != SendButtonState.none else self.icbm_idle_frames + 1
    # Counter-movement, the other half of the fallback (see COUNTER_MOVE_UNITS). self.cruise_button
    # is still last frame's command here, which is the one that could have moved the set speed now.
    if self.cruise_button != self.cruise_button_prev:
      self.counter_move_accum = 0
    cluster_delta = self.v_cruise_cluster - self.v_cruise_cluster_prev
    if self.cruise_button == SendButtonState.decrease and cluster_delta > 0:
      self.counter_move_accum += cluster_delta
    elif self.cruise_button == SendButtonState.increase and cluster_delta < 0:
      self.counter_move_accum -= cluster_delta
    self.cruise_button_prev = self.cruise_button
    # The stand-down cap exists for a press that never moves the set speed. It must NOT run while
    # the driver is still holding the button: on a press-and-hold long enough to reach it, the cap
    # expired mid-hold, the baseline froze at that instant, and ICBM woke up and walked the set
    # speed back down one increment at a time while the button was still pressed. Reported exactly
    # that way. While held, the window is re-armed every frame instead.
    if any(self.cruise_button_timers[k] > 0 for k in MANUAL_OVERRIDE_BUTTONS):
      if self.press_settle_frames > 0:
        self.press_settle_frames = PRESS_SETTLE_MAX_FRAMES
    else:
      self.press_settle_frames = max(0, self.press_settle_frames - 1)
    # Re-armed every frame the pedal is down, then decays -- so the window covers the whole
    # acceleration however long it lasts, plus the cluster lag after the driver lifts.
    if self.gas_handoff_active:
      self.gas_handoff_frames = GAS_HANDOFF_SETTLE_FRAMES
    else:
      self.gas_handoff_frames = max(0, self.gas_handoff_frames - 1)
    # unconditional: the resume jump settles on its own clock, held button or not
    self.cruise_cycle_frames = max(0, self.cruise_cycle_frames - 1)
    self.resume_press_frames = max(0, self.resume_press_frames - 1)
    self.set_press_frames = max(0, self.set_press_frames - 1)
    self.cluster_stable_frames = (self.cluster_stable_frames + 1
                                  if self.v_cruise_cluster == self.v_cruise_cluster_prev else 0)
    # End the resume window early once the resume jump has landed AND settled, so the driver's
    # habitual RESUME-then-immediately-press-and-hold is not swallowed by a fixed 2.5 s.
    #
    # "Moved, then stable" -- not "stable" alone. The set speed is typically already stable for the
    # whole time cruise was off, so a bare stability test closes the window before the jump even
    # arrives, and the jump is then adopted as a driver press. That is the exact bug this window
    # exists to prevent, and it is what the resume tests caught.
    if (self.cruise_cycle_frames > 0
        and self.v_cruise_cluster != self.v_cluster_at_cycle
        and self.cluster_stable_frames >= CRUISE_CYCLE_STABLE_FRAMES):
      self.cruise_cycle_frames = 0

    self.update_manual_override(CS)
    self.enforce_hold_policy()

    # BluePilot: the state machine runs unconditionally. A baseline changes WHAT ICBM aims for,
    # not WHETHER it aims -- see apply_baseline. The previous design forced State.inactive here,
    # which is why curve slowing silently stopped working for the rest of a drive after a single
    # button press, and why hazards needed an explicit exception to get back through.
    #
    # With the baseline folded into v_target there is nothing left to except: an ACTIVE radar-blind
    # lead already owns v_target outright further up, and the driver's number cannot suppress it.
    # Stand down while the driver's press settles. This is what makes the baseline unambiguous:
    # with ICBM emitting nothing, any set-speed movement in this window is the driver's by
    # construction, so no attempt to attribute it after the fact is needed. is_ready_prev is held
    # low so the inactive -> preActive edge fires cleanly when ICBM picks back up.
    # An ACTIVE radar-blind lead is the one thing that outranks the stand-down. Suppressing every
    # output made the baseline unambiguous, but it also meant a stopped car the radar cannot see
    # got no response for as long as the window lasted -- normally ~0.5 s, but up to the 6 s cap
    # when a press moves nothing at all, e.g. at Ford's set-speed ceiling. Adjusting cruise must
    # not blind the hazard path. Attribution is unaffected: the detector owns v_target outright
    # here, so any set-speed movement is still explained.
    if self.press_settle_frames > 0 and self.unconfirmed_lead_state != UnconfirmedLeadState.active:
      self.state = State.inactive
      self.cruise_button = SendButtonState.none
      self.is_ready_prev = False
    else:
      self.cruise_button = self.update_state_machine()
      self.is_ready_prev = self.is_ready
    self.v_cruise_cluster_prev = self.v_cruise_cluster

    self.frame += 1
