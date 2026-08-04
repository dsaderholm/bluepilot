"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from cereal import car, custom
from opendbc.car import structs, apply_hysteresis
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.common.realtime import DT_CTRL
from openpilot.sunnypilot import PARAMS_UPDATE_PERIOD
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
HYST_GAP = 0.0  # currently disabled; TODO-SP: might need to be brand-specific
INACTIVE_TIMER = 0.4

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
RESUME_PRESS_MEMORY_FRAMES = 150  # 1.5 s at 100 Hz, generous next to the engage delay
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
PRESS_SETTLE_STABLE_FRAMES = 40   # cluster unchanged this long => the driver has finished
PRESS_SETTLE_MAX_FRAMES = 600     # 6 s hard cap, so a stuck cluster cannot suspend ICBM forever
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
    self.v_target_ms_last = 0.0
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
    self.baseline_reset_delta = DEFAULT_BASELINE_RESET_DELTA
    self.v_cruise_cluster_prev = 0
    self.icbm_idle_frames = 0
    self.counter_move_accum = 0      # set-speed movement against ICBM's own command, display units
    self.cruise_button_prev = SendButtonState.none
    self.resume_press_frames = 0     # >0 while a RESUME press is recent enough to have re-engaged
    self.reanchor_overridden = False  # a resume kept the hold; re-measure the limit rule from here
    self.v_cluster_before_disengage = 0  # set speed when cruise last dropped; RESUME restores it
    self.cycle_decision_pending = False  # waiting for the set speed to say whether it was RESUME
    self.v_cluster_at_cycle = 0      # set speed when cruise was re-engaged; the resume jump moves it
    self.press_settle_frames = 0     # >0 while ICBM stands down after a driver press
    self.cluster_stable_frames = 0   # how long the set speed has been unchanged
    self.cruise_cycle_frames = 0     # >0 while a resume's set-speed jump is still settling
    self.v_cluster_at_press = 0      # set speed when the driver's press was seen
    self.press_suppressed = False    # the press happened while a curve/lead owned the target
    self.baseline_diverged = False   # has the baseline ever actually differed from SLA?
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

    # BluePilot: radar-blind lead detector currently owns the target
    self.unconfirmed_lead_commanding = False
    self.unconfirmed_lead_state = UnconfirmedLeadState.inactive

  def update_params(self) -> None:
    if self.frame % int(PARAMS_UPDATE_PERIOD / DT_CTRL) == 0:
      self.max_target_drop = self.params.get("IcbmMaxTargetDrop", return_default=True)
      self.max_target_rise = self.params.get("IcbmMaxTargetRise", return_default=True)
      self.baseline_reset_delta = self.params.get("IcbmBaselineResetDelta", return_default=True)

  @property
  def v_cruise_equal(self) -> bool:
    return self.v_target == self.v_cruise_cluster

  def update_calculations(self, CS: car.CarState, LP_SP: custom.LongitudinalPlanSP) -> None:
    speed_conv = CV.MS_TO_KPH if self.is_metric else CV.MS_TO_MPH
    ms_conv = CV.KPH_TO_MS if self.is_metric else CV.MPH_TO_MS

    self.v_target_ms_last = apply_hysteresis(LP_SP.vTarget, self.v_target_ms_last, HYST_GAP * ms_conv)

    self.v_target = round(self.v_target_ms_last * speed_conv)
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
      self.v_target = self.v_cruise_cluster

    # BluePilot: keep the planner's own target before the limiters touch it. Every override
    # decision compares against this, never against self.v_target -- the limiters clamp toward the
    # cluster, so a limited value drifts for reasons that have nothing to do with what was planned.
    # Comparing post-limiter values is exactly how the original re-arm bug worked.
    self.v_target_raw = self.v_target
    self.plan_source = LP_SP.longitudinalPlanSource
    self.v_target = self.apply_baseline(self.v_target)

    v_ego_conv = round(CS.vEgo * speed_conv)
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

  def apply_target_drop_limit(self, v_ego_conv: int) -> int:
    """BluePilot: cap how far below the set speed ICBM may command in one step.

    Ford's stock ACC treats a large single drop in set speed as a reason to brake hard; smaller
    drops it handles by coasting. So rather than commanding a curve or speed-limit target all at
    once, hold at (anchor - max_target_drop) and only take the next step once the car has actually
    slowed to that floor. Net deceleration is the same, but it arrives as coasting.

    Only decreases are limited -- increases are what the driver or the ceiling asked for and are
    rate-limited naturally by ICBM emitting one button press per cycle.
    """
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
    """
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
    if self.lead_present:
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

    return send_button

  def update_readiness(self, CS: car.CarState, CC: car.CarControl) -> None:
    update_manual_button_timers(CS, self.cruise_button_timers)

    ready = CC.enabled and not CC.cruiseControl.override and not CC.cruiseControl.cancel and not CC.cruiseControl.resume
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

    # Remember a RESUME press across the engage transition -- see RESUME_BUTTONS.
    if any(b.type.raw in RESUME_BUTTONS and b.pressed for b in CS.buttonEvents):
      self.resume_press_frames = RESUME_PRESS_MEMORY_FRAMES

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
      else:
        # Only worth deferring when there is a hold at stake. Otherwise the very first engagement
        # of a drive leaves a decision pending that fires 2.5 s later and wipes a hold created in
        # the meantime -- which is what eight tests caught.
        self.cycle_decision_pending = self.v_baseline > 0
      self.cruise_cycle_frames = CRUISE_CYCLE_SETTLE_FRAMES
      self.v_cluster_at_cycle = self.v_cruise_cluster
      self.resume_press_frames = 0
      return

    # Decide once the resume jump has landed. Deferring is the point: clearing on the cycle frame
    # threw the hold away before the evidence existed.
    if self.cycle_decision_pending:
      landed = (self.v_cruise_cluster != self.v_cluster_at_cycle
                and self.cluster_stable_frames >= CRUISE_CYCLE_STABLE_FRAMES)
      if landed or self.cruise_cycle_frames == 0:
        resumed = abs(self.v_cruise_cluster - self.v_cluster_before_disengage) <= RESUME_MATCH_TOLERANCE
        if resumed:
          self.reanchor_overridden = True
        else:
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
    if cruise_enabled and any(b.type.raw in MANUAL_OVERRIDE_BUTTONS and b.pressed
                              for b in CS.buttonEvents):
      if self.override_state != OverrideState.manual:
        self.v_target_overridden = self.v_target_raw
        self.baseline_diverged = False
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
      self.press_settle_frames = PRESS_SETTLE_MAX_FRAMES
      self.cluster_stable_frames = 0
      return

    # Fallback: set speed moved, ICBM has been silent long enough that it cannot be responsible.
    # Runs before the manual-only guard below so it can CREATE a baseline, not just update one.
    fallback_idle = self.icbm_idle_frames >= ADOPT_IDLE_FRAMES
    fallback_counter = self.counter_move_accum >= COUNTER_MOVE_UNITS
    if (cruise_enabled and self.cruise_cycle_frames == 0
        and self.v_cruise_cluster != self.v_cruise_cluster_prev
        and (fallback_idle or fallback_counter)):
      if self.override_state != OverrideState.manual:
        self.v_target_overridden = self.v_target_raw
        self.baseline_diverged = False
        self.v_cluster_at_press = self.v_cruise_cluster_prev
      self.override_state = OverrideState.manual
      # Same rule as the press path: under a curve or a lead, something other than the driver owns
      # the target, so movement there must not redefine the hold. Without this the press path's
      # protection is worthless -- the fallback re-baselines a frame later, which is what a probe
      # caught: the hold still fell 70 -> 50 with the press path already fixed.
      if not self.hold_suppressed:
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
      if not self.press_suppressed:
        self.v_baseline = self.v_cruise_cluster
      # The set speed must have actually MOVED before "stable" means anything. Without this the
      # counter reaches its threshold while the cluster is merely slow to report, the stand-down
      # ends on the pre-press value, and the press is undone -- the same shape of bug as the fixed
      # timer it replaced. A press that never moves the cluster (already at Ford's ceiling) is
      # released by PRESS_SETTLE_MAX_FRAMES instead.
      moved = self.v_cruise_cluster != self.v_cluster_at_press
      held = any(self.cruise_button_timers[k] > 0 for k in self.cruise_button_timers)
      settled = moved and not held and self.cluster_stable_frames >= PRESS_SETTLE_STABLE_FRAMES
      if settled:
        self.press_settle_frames = 0
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
    # Gated on baseline_diverged, which is the whole reason that flag exists. A hold is created at
    # the set speed the driver pressed from, and on the very first frame that speed can still equal
    # SLA's target -- clearing on bare equality would delete the hold before the driver's press had
    # moved anything, which is the "minus is unpredictable" failure this rule was withdrawn for the
    # first time around. Requiring the baseline to have actually been somewhere else first makes
    # the gesture unambiguous: you have to leave SLA's number and come back to it.
    #
    # Source-gated like the reset-delta rule below. Under `cruise` there is no posted limit for
    # v_target_raw to represent, and equality there is coincidence rather than intent.
    if self.plan_source == LongitudinalPlanSource.speedLimitAssist:
      if self.v_baseline != self.v_target_raw:
        self.baseline_diverged = True
      elif self.baseline_diverged:
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
    fired = self.pinned_hold > 0 and self.pinned_hold != self.pinned_hold_prev
    self.pinned_hold_prev = self.pinned_hold
    # A hold is a number for cruise to drive to. With cruise off there is nothing to hold, and
    # arming one here would have it discovered later at a speed nobody chose.
    if not fired or not cruise_enabled:
      return False

    if self.override_state != OverrideState.manual:
      self.v_target_overridden = self.v_target_raw
      self.baseline_diverged = False
    self.override_state = OverrideState.manual
    self.v_baseline = self.pinned_hold
    self.baseline_source = BaselineSource.pinned
    self.v_cluster_at_press = self.v_cruise_cluster
    return True

  def clear_baseline(self) -> None:
    self.override_state = OverrideState.auto
    self.v_baseline = 0
    self.v_target_overridden = 0
    self.baseline_diverged = False
    self.reanchor_overridden = False
    self.counter_move_accum = 0

  def run(self, CS: car.CarState, CC: car.CarControl, LP_SP: custom.LongitudinalPlanSP, is_metric: bool,
          lead_present: bool = False, pinned_hold: int = 0) -> None:
    if self.CP_SP.pcmCruiseSpeed:
      return

    self.is_metric = is_metric
    self.lead_present = lead_present
    self.pinned_hold = int(pinned_hold)

    self.update_params()
    self.update_calculations(CS, LP_SP)
    self.update_readiness(CS, CC)

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
    # unconditional: the resume jump settles on its own clock, held button or not
    self.cruise_cycle_frames = max(0, self.cruise_cycle_frames - 1)
    self.resume_press_frames = max(0, self.resume_press_frames - 1)
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
