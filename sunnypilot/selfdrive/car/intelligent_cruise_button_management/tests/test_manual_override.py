"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

BluePilot: behavioural tests for the ICBM driver baseline.

A set-speed press means "for this speed limit I want a different number", not "stop managing my
cruise". Two real defects came from getting that wrong: ICBM dragging the set speed back down to
the limit within seconds of an override, and -- after the first attempt at a fix -- curve slowing
silently not working at all for the rest of a drive because a press forced the state machine
inactive.

The tests that matter are the ones with something MOVING: a static target passes under every
version of this logic, which is why both defects shipped.
"""

import pytest
from types import SimpleNamespace as NS

from cereal import car, custom
from openpilot.sunnypilot.selfdrive.car.intelligent_cruise_button_management.controller import (
  IntelligentCruiseButtonManagement, DEFAULT_BASELINE_RESET_DELTA,
)

OverrideState = custom.IntelligentCruiseButtonManagement.OverrideState
State = custom.IntelligentCruiseButtonManagement.IntelligentCruiseButtonManagementState
SendButtonState = custom.IntelligentCruiseButtonManagement.SendButtonState
UnconfirmedLeadState = custom.LongitudinalPlanSP.UnconfirmedLead.State
PlanSource = custom.LongitudinalPlanSP.LongitudinalPlanSource
ButtonType = car.CarState.ButtonEvent.Type

MPH = 0.44704
LIMIT = 55    # what SLA wants: posted limit + the configured offset
DRIVER = 70   # what the driver wants instead, for this limit

ACCEL_PRESS = NS(type=NS(raw=ButtonType.accelCruise), pressed=True)
ACCEL_RELEASE = NS(type=NS(raw=ButtonType.accelCruise), pressed=False)
GAP_PRESS = NS(type=NS(raw=ButtonType.gapAdjustCruise), pressed=True)
DECEL_PRESS = NS(type=NS(raw=ButtonType.decelCruise), pressed=True)
DECEL_RELEASE = NS(type=NS(raw=ButtonType.decelCruise), pressed=False)

CC = NS(enabled=True, cruiseControl=NS(resume=False, override=False, cancel=False))


def make_cs(cluster, v_ego=None, buttons=(), enabled=True):
  return NS(vEgo=(cluster if v_ego is None else v_ego) * MPH,
            cruiseState=NS(available=True, enabled=enabled, speedCluster=cluster * MPH,
                           standstill=False, speed=cluster * MPH),
            buttonEvents=buttons)


def make_lp(target, lead_state=UnconfirmedLeadState.inactive, lead_target=0.0,
            source=PlanSource.speedLimitAssist):
  return NS(vTarget=target * MPH,
            longitudinalPlanSource=source,
            unconfirmedLead=NS(state=lead_state, vTarget=lead_target * MPH))


def fresh(max_rise=0, max_drop=0):
  """ICBM settled with no baseline, agreeing with the driver at the limit.

  Rate limiters default off here so target assertions read the baseline logic directly; they get
  their own class below. cruise_button_timers is bound to the module-level CRUISE_BUTTON_TIMER
  dict by reference rather than copied, so instances share it -- clear it or state leaks.
  """
  icbm = IntelligentCruiseButtonManagement(NS(), NS(pcmCruiseSpeed=False))
  for k in icbm.cruise_button_timers:
    icbm.cruise_button_timers[k] = 0
  icbm.update_params = lambda: None
  icbm.max_target_rise, icbm.max_target_drop = max_rise, max_drop
  icbm.baseline_reset_delta = DEFAULT_BASELINE_RESET_DELTA
  for _ in range(5):
    icbm.run(make_cs(LIMIT), CC, make_lp(LIMIT), False)
  return icbm


def set_baseline(icbm, to=DRIVER):
  """Driver holds + until the set speed reaches `to`, then releases."""
  icbm.run(make_cs(LIMIT, buttons=(ACCEL_PRESS,)), CC, make_lp(LIMIT), False)
  step = 1 if to >= LIMIT else -1
  for cluster in range(LIMIT, to + step, step):
    icbm.run(make_cs(cluster, buttons=(ACCEL_PRESS,)), CC, make_lp(LIMIT), False)
  icbm.run(make_cs(to, buttons=(ACCEL_RELEASE,)), CC, make_lp(LIMIT), False)


def settle(icbm, target, cluster=DRIVER, frames=150, source=PlanSource.speedLimitAssist):
  """Default runs past PRESS_SETTLE_FRAMES: ICBM stands down for 0.6 s after a driver press, so
  a shorter settle would assert on the stand-down rather than on steady-state behaviour."""
  for _ in range(frames):
    icbm.run(make_cs(cluster), CC, make_lp(target, source=source), False)


class TestBaselineCapture:
  def test_press_records_the_driver_speed(self):
    icbm = fresh()
    set_baseline(icbm)
    assert icbm.override_state == OverrideState.manual
    assert icbm.v_baseline == DRIVER, "baseline did not settle on the final set speed"

  def test_gap_button_is_not_an_override(self):
    icbm = fresh()
    icbm.run(make_cs(LIMIT, buttons=(GAP_PRESS,)), CC, make_lp(LIMIT), False)
    assert icbm.override_state == OverrideState.auto

  def test_baseline_replaces_the_speed_limit_target(self):
    icbm = fresh()
    set_baseline(icbm)
    settle(icbm, LIMIT)
    assert icbm.v_target == DRIVER, "ICBM is still chasing the SLA target"

  def test_baseline_works_downward_too(self):
    """'Or lower, in some cases.'"""
    icbm = fresh()
    set_baseline(icbm, to=45)
    settle(icbm, LIMIT, cluster=45)
    assert icbm.v_baseline == 45
    assert icbm.v_target == 45


class TestFeaturesKeepWorkingUnderBaseline:
  """The defect that made this a rewrite: a press used to switch ICBM off entirely."""

  def test_icbm_is_not_forced_inactive(self):
    icbm = fresh()
    set_baseline(icbm)
    settle(icbm, 40, source=PlanSource.sccVision, frames=100)
    assert icbm.state != State.inactive, "a press suspended ICBM"

  def test_curve_still_slows_the_car(self):
    icbm = fresh()
    set_baseline(icbm)
    settle(icbm, 40, source=PlanSource.sccVision)
    assert icbm.v_target == 40, "curve target was ignored while a baseline was set"
    assert icbm.cruise_button == SendButtonState.decrease

  def test_curve_target_is_not_raised_by_the_baseline(self):
    """A curve is a physics limit. 40 means 40, not 40 + the driver's offset."""
    icbm = fresh()
    set_baseline(icbm)
    settle(icbm, 40, source=PlanSource.sccVision)
    assert icbm.v_target == 40

  def test_returns_to_the_baseline_after_the_curve(self):
    """The cluster must be driven BY ICBM here, not teleported.

    Baseline adoption credits any cluster movement ICBM did not command to the driver, which is
    correct on a real car -- the set speed only moves because a human pressed or ICBM asked. A
    harness that jumps the cluster 30 mph in one frame is indistinguishable from a press, and
    would have the curve silently become the new baseline.
    """
    icbm = fresh()
    set_baseline(icbm)
    cluster = DRIVER
    for _ in range(400):                       # into the curve, ICBM drives the set speed down
      icbm.run(make_cs(cluster), CC, make_lp(40, source=PlanSource.sccVision), False)
      if icbm.cruise_button == SendButtonState.decrease:
        cluster -= 1
    assert cluster == 40, f"curve slowing stalled at {cluster}"
    assert icbm.v_baseline == DRIVER, "curve decel was mistaken for a driver press"

    for _ in range(600):                       # curve ends, SLA back in charge at 55
      icbm.run(make_cs(cluster), CC, make_lp(LIMIT), False)
      if icbm.cruise_button == SendButtonState.increase:
        cluster += 1
    assert cluster == DRIVER, f"returned to {cluster}, not the driver's {DRIVER}"

  def test_hazard_still_commands_under_a_baseline(self):
    icbm = fresh()
    set_baseline(icbm)
    icbm.run(make_cs(DRIVER), CC,
             make_lp(LIMIT, UnconfirmedLeadState.active, 20.0), False)
    assert icbm.v_target == 20, "the baseline blocked a hazard decel"


class TestBaselineReset:
  def test_major_limit_change_discards_it(self):
    """A 55-zone baseline must not follow the driver into a 35 zone."""
    icbm = fresh()
    set_baseline(icbm)
    settle(icbm, LIMIT)
    assert icbm.override_state == OverrideState.manual
    settle(icbm, 35)
    assert icbm.override_state == OverrideState.auto
    assert icbm.v_baseline == 0

  def test_major_limit_increase_discards_it(self):
    icbm = fresh()
    set_baseline(icbm)
    settle(icbm, LIMIT + DEFAULT_BASELINE_RESET_DELTA)
    assert icbm.override_state == OverrideState.auto

  def test_minor_limit_change_keeps_it(self):
    """55 -> 60 is the same road. The driver's number survives."""
    icbm = fresh()
    set_baseline(icbm)
    for delta in (1, 2, 5, 9, -3, -9):
      settle(icbm, LIMIT + delta)
      assert icbm.override_state == OverrideState.manual, f"discarded on a {delta} mph change"

  def test_curve_never_discards_it(self):
    """THE DISCRIMINATOR. A 25 mph curve drop is bigger than the reset threshold, but a curve
    ends by itself in seconds whereas a limit change persists. Source decides, not magnitude."""
    icbm = fresh()
    set_baseline(icbm)
    settle(icbm, 30, source=PlanSource.sccVision, frames=200)
    assert icbm.override_state == OverrideState.manual, "curve slowing discarded the baseline"

  def test_lead_following_never_discards_it(self):
    icbm = fresh()
    set_baseline(icbm)
    settle(icbm, 30, source=PlanSource.cruise, frames=200)
    assert icbm.override_state == OverrideState.manual

  def test_cruise_cycle_discards_it(self):
    icbm = fresh()
    set_baseline(icbm)
    icbm.run(make_cs(DRIVER, enabled=False), CC, make_lp(LIMIT), False)
    icbm.run(make_cs(DRIVER, enabled=True), CC, make_lp(LIMIT), False)
    assert icbm.override_state == OverrideState.auto
    assert icbm.v_baseline == 0

  def test_zero_delta_means_only_cruise_cycle(self):
    icbm = fresh()
    icbm.baseline_reset_delta = 0
    set_baseline(icbm)
    settle(icbm, 25)
    assert icbm.override_state == OverrideState.auto, "0 should still reset on any change >= 0"


class TestTargetRiseLimit:
  """The set speed must come back up in steps, not one continuous pull.

  ICBM holds CcAslButtnSetIncPress high for as long as the state machine sits in `increasing`,
  and Ford reads a held button as a continuous ramp.
  """

  CURVE = 40
  CRUISE = 70

  def _icbm(self, max_rise):
    icbm = IntelligentCruiseButtonManagement(NS(), NS(pcmCruiseSpeed=False))
    for k in icbm.cruise_button_timers:
      icbm.cruise_button_timers[k] = 0
    icbm.update_params = lambda: None
    icbm.max_target_rise, icbm.max_target_drop = max_rise, 0
    return icbm

  def test_rise_is_capped_to_one_step(self):
    icbm = self._icbm(5)
    icbm.run(make_cs(self.CURVE), CC, make_lp(self.CURVE), False)
    icbm.run(make_cs(self.CURVE), CC, make_lp(self.CRUISE), False)
    assert icbm.v_target == self.CURVE + 5, "asked for the whole 30 mph rise at once"

  def test_step_advances_only_once_speed_catches_up(self):
    icbm = self._icbm(5)
    icbm.run(make_cs(self.CURVE), CC, make_lp(self.CURVE), False)
    icbm.run(make_cs(self.CURVE, v_ego=self.CURVE), CC, make_lp(self.CRUISE), False)
    assert icbm.v_target == self.CURVE + 5
    icbm.run(make_cs(self.CURVE + 5, v_ego=self.CURVE), CC, make_lp(self.CRUISE), False)
    assert icbm.v_target == self.CURVE + 5, "advanced before the car caught up"
    icbm.run(make_cs(self.CURVE + 5, v_ego=self.CURVE + 5), CC, make_lp(self.CRUISE), False)
    assert icbm.v_target == self.CURVE + 10, "did not advance once speed caught up"

  def test_reaches_cruise_speed_eventually(self):
    icbm = self._icbm(5)
    cluster = self.CURVE
    for _ in range(400):
      icbm.run(make_cs(cluster, v_ego=cluster), CC, make_lp(self.CRUISE), False)
      if icbm.v_target > cluster:
        cluster += 1
    assert cluster == self.CRUISE, f"stalled at {cluster}"

  def test_zero_disables_the_cap(self):
    icbm = self._icbm(0)
    icbm.run(make_cs(self.CURVE), CC, make_lp(self.CURVE), False)
    icbm.run(make_cs(self.CURVE), CC, make_lp(self.CRUISE), False)
    assert icbm.v_target == self.CRUISE

  def test_hazard_decel_is_not_metered(self):
    icbm = self._icbm(5)
    icbm.run(make_cs(self.CRUISE), CC, make_lp(self.CRUISE), False)
    icbm.run(make_cs(self.CRUISE), CC,
             make_lp(self.CRUISE, UnconfirmedLeadState.active, 20.0), False)
    assert icbm.v_target == 20, "rise limiter interfered with a hazard decel"


class TestBaselineSurvivesClusterLag:
  """cruiseState.speedCluster lags the button, and that lag broke the first two fixes.

  On a real car the release ButtonEvent arrives BEFORE the cluster reports the new set speed.
  Freezing the baseline when the button timer clears therefore captured the speed the driver was
  leaving, and ICBM drove straight back to it -- reported from a drive as "it still minuses my
  speed down when I try to increase it with the plus".

  Every case here is a single tap. The parameter is how many frames after the press the cluster
  catches up; the release lands at frame 3, so anything >= 3 is the broken window.
  """

  LIMIT = 55

  def _tap(self, cluster_lag, press_len=3, frames=300):
    icbm = fresh(max_rise=5, max_drop=8)
    for _ in range(120):                       # cruising a while first, as on a real drive
      icbm.run(make_cs(self.LIMIT), CC, make_lp(self.LIMIT), False)
    cluster = self.LIMIT
    for f in range(frames):
      btn = (ACCEL_PRESS,) if f == 0 else ((ACCEL_RELEASE,) if f == press_len else ())
      if f == cluster_lag:
        cluster += 1                           # the car finally reports the new set speed
      icbm.run(make_cs(cluster, buttons=btn), CC, make_lp(self.LIMIT), False)
      if icbm.cruise_button == SendButtonState.decrease:
        cluster -= 1
      elif icbm.cruise_button == SendButtonState.increase:
        cluster += 1
    return icbm, cluster

  @pytest.mark.parametrize("lag", [1, 2, 3, 4, 6, 10, 20])
  def test_single_tap_is_not_dragged_back(self, lag):
    icbm, cluster = self._tap(lag)
    assert cluster == self.LIMIT + 1, f"tap undone (cluster lag {lag} frames)"
    assert icbm.v_baseline == self.LIMIT + 1

  def test_icbm_does_not_adopt_its_own_commanded_change(self):
    """The mirror risk: if ICBM credited its own decel to the driver, a curve would silently
    become the new baseline and the set speed would never come back."""
    icbm = fresh(max_rise=0, max_drop=0)
    set_baseline(icbm)
    assert icbm.v_baseline == DRIVER
    cluster = DRIVER
    for _ in range(300):                       # a curve ICBM slows for, no driver input at all
      icbm.run(make_cs(cluster), CC, make_lp(40, source=PlanSource.sccVision), False)
      if icbm.cruise_button == SendButtonState.decrease:
        cluster -= 1
    assert cluster < DRIVER, "curve never slowed the car"
    assert icbm.v_baseline == DRIVER, "ICBM adopted its own curve decel as the driver's baseline"


class TestPressWinsWhileIcbmIsBusy:
  """The reported failure: "+ minuses my speed back down, unless I go down and then back up".

  Baseline adoption used to require ICBM to have been idle. The cluster catches up ~6 frames after
  a press, so if ICBM had commanded anything recently -- which it does constantly while actively
  setting a speed -- the movement was never credited to the driver and ICBM drove the press back
  out. Pressing down first let ICBM settle, which is why that worked.

  ICBM is held deliberately busy here (SLA wants a speed it has not reached), which is the
  condition the old logic failed under. Assertions are relative to the set speed at the moment of
  the first press, not to LIMIT -- ICBM has legitimately been moving it before the driver touches
  anything.
  """

  LAG = 6

  def _sim(self, sla_target, presses, settle_frames=900):
    icbm = fresh(max_rise=5, max_drop=8)
    cluster = LIMIT
    pending = []

    def step(buttons=()):
      nonlocal cluster
      icbm.run(make_cs(cluster, buttons=buttons), CC, make_lp(sla_target), False)
      s = 0
      if icbm.cruise_button == SendButtonState.increase:
        s = +1
      elif icbm.cruise_button == SendButtonState.decrease:
        s = -1
      pending.append(s)
      if len(pending) > self.LAG:
        cluster += pending.pop(0)

    for _ in range(120):          # ICBM actively working toward the SLA target: NOT idle
      step()
    assert icbm.cruise_button != SendButtonState.none or icbm.icbm_idle_frames < 20,       "harness failed to keep ICBM busy, so this would not exercise the bug"
    before = cluster

    for _ in range(presses):
      step((ACCEL_PRESS,))
      for k in range(24):
        if k == self.LAG:
          cluster += 1          # the driver's own press reaching the set speed
        step((ACCEL_RELEASE,) if k == 3 else ())
    for _ in range(settle_frames):
      step()
    return icbm, before, cluster

  @pytest.mark.parametrize("presses", [1, 2, 3, 5])
  def test_presses_are_never_undone_while_icbm_is_busy(self, presses):
    icbm, before, after = self._sim(sla_target=50, presses=presses)
    assert after == before + presses, f"{presses} press(es) ended at {after}, expected {before + presses}"
    assert icbm.v_baseline == after, f"baseline {icbm.v_baseline} != set speed {after}"
    assert icbm.override_state == OverrideState.manual

  def test_up_only_matches_down_then_up(self):
    """Up-only must land in the same place. Before the fix it did not, which is what the driver
    noticed: the workaround was to press down first."""
    icbm, before, after = self._sim(sla_target=50, presses=2)
    assert after - before == 2


class TestMinusAlwaysAdjustsNeverCancels:
  """Returning the set speed to exactly SLA's number used to delete the HOLD.

  Removed. It made the minus button unpredictable -- whether a press adjusted the hold or deleted
  it depended on a number the driver cannot see -- and it is why "press down then up" looked like
  a workaround: down was not fixing anything, it was deleting the override so the next press built
  a fresh one. On this car SET/RESUME shares a CAN signal with cancel, so there is no separate
  resume to press while engaged; cancel + re-engage is the explicit "hand it back to the speed
  limit" control, and that still clears it.
  """

  def test_minus_down_to_the_sla_target_keeps_the_hold(self):
    icbm = fresh()
    set_baseline(icbm)
    settle(icbm, LIMIT)
    cluster = DRIVER
    for _ in range(DRIVER - LIMIT):                 # walk it back down to the limit
      icbm.run(make_cs(cluster, buttons=(DECEL_PRESS,)), CC, make_lp(LIMIT), False)
      cluster -= 1
      for f in range(12):
        icbm.run(make_cs(cluster, buttons=(DECEL_RELEASE,) if f == 2 else ()), CC,
                 make_lp(LIMIT), False)
    settle(icbm, LIMIT, cluster=cluster, frames=200)
    assert cluster == LIMIT
    assert icbm.override_state == OverrideState.manual, "minus deleted the hold instead of adjusting it"
    assert icbm.v_baseline == LIMIT

  def test_cancel_and_reengage_clears_the_hold(self):
    """The explicit control, and the one the driver actually uses."""
    icbm = fresh()
    set_baseline(icbm)
    settle(icbm, LIMIT)
    assert icbm.override_state == OverrideState.manual
    icbm.run(make_cs(DRIVER, enabled=False), CC, make_lp(LIMIT), False)
    icbm.run(make_cs(DRIVER, enabled=True), CC, make_lp(LIMIT), False)
    assert icbm.override_state == OverrideState.auto
    assert icbm.v_baseline == 0

  def test_a_curve_matching_the_baseline_does_not_clear_it(self):
    icbm = fresh()
    set_baseline(icbm)
    settle(icbm, DRIVER, source=PlanSource.sccVision)
    assert icbm.override_state == OverrideState.manual


class TestPressSurvivesAnyClusterLag:
  """THE ONE THAT KEPT REACHING THE ROAD.

  Four fixes failed because each ended the post-press stand-down on a TIMER. On a car whose
  cruiseState.speedCluster reports slower than that timer, the stand-down closed while the
  baseline still equalled SLA's target -- and the convergence check then deleted the override as
  though the driver had just undone it. Reported four times as "it won't let me increase the
  speed, it keeps resetting it back down", and it only worked after pressing down first because
  that moved the set speed BELOW the target, so the two were never equal.

  The stand-down now ends when the set speed has actually moved and then gone quiet. These sweep
  the lag well past any timer, which is the case every previous version passed and shipped broken.
  """

  @pytest.mark.parametrize("lag", [5, 30, 59, 61, 90, 200, 400])
  def test_single_tap_survives(self, lag):
    icbm = fresh(max_rise=5, max_drop=8)
    cluster = LIMIT
    for _ in range(200):
      icbm.run(make_cs(cluster), CC, make_lp(LIMIT), False)
    for f in range(1500):
      btn = (ACCEL_PRESS,) if f == 0 else ((ACCEL_RELEASE,) if f == 3 else ())
      if f == lag:
        cluster += 1
      icbm.run(make_cs(cluster, buttons=btn), CC, make_lp(LIMIT), False)
      if f % 5 == 0:
        if icbm.cruise_button == SendButtonState.decrease:
          cluster -= 1
        elif icbm.cruise_button == SendButtonState.increase:
          cluster += 1
    assert cluster == LIMIT + 1, f"press undone with {lag}-frame cluster lag (ended {cluster})"
    assert icbm.v_baseline == LIMIT + 1
    assert icbm.override_state == OverrideState.manual

  @pytest.mark.parametrize("hold", [3, 60, 300, 700])
  def test_long_hold_survives(self, hold):
    """A hold longer than any fixed window must not freeze the baseline mid-ramp."""
    icbm = fresh(max_rise=5, max_drop=8)
    cluster = LIMIT
    for _ in range(200):
      icbm.run(make_cs(cluster), CC, make_lp(LIMIT), False)
    for f in range(2000):
      btn = (ACCEL_PRESS,) if f == 0 else ((ACCEL_RELEASE,) if f == hold else ())
      # A tap yields one increment; a hold keeps ramping. Either way the set speed moves at
      # least once, roughly 6 frames after the press, as it does on the car.
      if f == 6 or (f < hold and f % 30 == 29 and cluster < LIMIT + 5):
        cluster += 1
      icbm.run(make_cs(cluster, buttons=btn), CC, make_lp(LIMIT), False)
      if f % 5 == 0:
        if icbm.cruise_button == SendButtonState.decrease:
          cluster -= 1
        elif icbm.cruise_button == SendButtonState.increase:
          cluster += 1
    assert icbm.v_baseline == cluster, f"baseline {icbm.v_baseline} != set speed {cluster}"
    assert cluster > LIMIT, "the whole ramp was undone"


class TestStandDownDoesNotBlindTheHazardPath:
  """Suppressing every output during the post-press stand-down also suppressed the radar-blind
  lead response for up to 6 s. Adjusting cruise must not blind a stopped car the radar cannot see."""

  def test_active_hazard_commands_during_the_stand_down(self):
    icbm = fresh()
    icbm.run(make_cs(LIMIT, buttons=(ACCEL_PRESS,)), CC, make_lp(LIMIT), False)
    icbm.run(make_cs(LIMIT, buttons=(ACCEL_RELEASE,)), CC, make_lp(LIMIT), False)
    assert icbm.press_settle_frames > 0, "stand-down should be armed right after a press"
    # Long enough to clear pre_active_timer; the stand-down cap is far longer, so it is still on.
    for _ in range(120):
      icbm.run(make_cs(LIMIT), CC, make_lp(LIMIT, UnconfirmedLeadState.active, 20.0), False)
    assert icbm.press_settle_frames > 0, "stand-down ended early; test no longer covers the case"
    assert icbm.v_target == 20, "hazard target was ignored"
    assert icbm.cruise_button == SendButtonState.decrease, "hazard decel suppressed by stand-down"

  def test_stand_down_still_suppresses_normal_output(self):
    """The bypass must be hazard-only, or the whole point of the stand-down is lost."""
    icbm = fresh()
    icbm.run(make_cs(LIMIT, buttons=(ACCEL_PRESS,)), CC, make_lp(LIMIT), False)
    icbm.run(make_cs(LIMIT, buttons=(ACCEL_RELEASE,)), CC, make_lp(LIMIT), False)
    for _ in range(120):
      icbm.run(make_cs(LIMIT), CC, make_lp(40, source=PlanSource.sccVision), False)
    assert icbm.cruise_button == SendButtonState.none, "stand-down leaked a normal command"


class TestPressAndHold:
  """The driver's actual habit: press and HOLD to change speed, rather than tapping.

  openpilot emits button events on EDGES only, so a hold produces one press event, then nothing
  for seconds, then one release. The stand-down cap counted down through all of that, expired
  mid-hold, froze the baseline at that instant, and ICBM then walked the set speed back down one
  increment at a time while the button was still held. Reported as "the number goes up on the dash
  but instantly gets lowered one by one back to where it just was".
  """

  RAMP_EVERY = 30   # Ford steps the set speed roughly every 0.3 s while held

  def _hold(self, seconds, prior_hold=False):
    icbm = fresh(max_rise=5, max_drop=8)
    cluster = LIMIT
    f = 0

    def step(buttons=()):
      nonlocal cluster, f
      icbm.run(make_cs(cluster, buttons=buttons), CC, make_lp(LIMIT), False)
      if f % 5 == 0:
        if icbm.cruise_button == SendButtonState.decrease:
          cluster -= 1
        elif icbm.cruise_button == SendButtonState.increase:
          cluster += 1
      f += 1

    for _ in range(100):
      step()
    if prior_hold:                                  # establish a HOLD first
      step((ACCEL_PRESS,))
      for k in range(200):
        if k == 20:
          cluster += 1
        step((ACCEL_RELEASE,) if k == 3 else ())

    start = cluster
    step((ACCEL_PRESS,))
    for k in range(int(seconds * 100)):             # held: no events, set speed ramps
      if k % self.RAMP_EVERY == self.RAMP_EVERY - 1:
        cluster += 1
      step()
    step((ACCEL_RELEASE,))
    for _ in range(600):
      step()
    return icbm, cluster, start

  @pytest.mark.parametrize("seconds", [1, 3, 6, 9, 15])
  def test_hold_is_never_walked_back(self, seconds):
    icbm, cluster, start = self._hold(seconds)
    expected = start + int(seconds * 100 / self.RAMP_EVERY)
    assert cluster == expected, f"{seconds}s hold walked back to {cluster}, wanted {expected}"
    assert icbm.v_baseline == cluster

  @pytest.mark.parametrize("seconds", [1, 6, 15])
  def test_hold_raises_an_existing_hold(self, seconds):
    """Raising a HOLD that is already set -- the case reported as impossible."""
    icbm, cluster, start = self._hold(seconds, prior_hold=True)
    assert cluster > start, f"existing HOLD was not raised (stuck at {cluster})"
    assert icbm.v_baseline == cluster
    assert icbm.override_state == OverrideState.manual


class TestResumeDoesNotCreateAHold:
  """This wheel has combined RES+ / SET- buttons and a separate CNCL. The driver's only route back
  to Speed Limit Assist is CNCL then RES+, so resuming must not immediately create a new HOLD."""

  SET_CRUISE = NS(type=NS(raw=ButtonType.setCruise), pressed=True)

  def test_press_while_disengaged_creates_no_hold(self):
    icbm = fresh()
    icbm.run(make_cs(LIMIT, buttons=(self.SET_CRUISE,), enabled=False), CC, make_lp(LIMIT), False)
    assert icbm.override_state == OverrideState.auto, "a press while disengaged created a HOLD"
    assert icbm.v_baseline == 0

  def test_cancel_then_resume_leaves_sla_in_charge(self):
    icbm = fresh()
    set_baseline(icbm)
    settle(icbm, LIMIT)
    assert icbm.override_state == OverrideState.manual
    icbm.run(make_cs(DRIVER, buttons=(self.SET_CRUISE,), enabled=False), CC, make_lp(LIMIT), False)
    for _ in range(20):
      icbm.run(make_cs(LIMIT, enabled=True), CC, make_lp(LIMIT), False)
    assert icbm.override_state == OverrideState.auto, "resume rebuilt the HOLD it just cleared"
    assert icbm.v_baseline == 0


class TestMappingAgnosticFallback:
  """The press path assumes the driver's button arrives as one of MANUAL_OVERRIDE_BUTTONS. On a car
  with flashed SCCM firmware that is an assumption. If the set speed moves and ICBM has been silent
  far longer than any command of its own could take to land, a human moved it -- adopt it."""

  def _drive(self, moves, source=PlanSource.speedLimitAssist, target=LIMIT, frames=900):
    """moves: {frame: delta} applied to the set speed with NO button event at all."""
    icbm = fresh(max_rise=5, max_drop=8)
    cluster = LIMIT
    for f in range(frames):
      cluster += moves.get(f, 0)
      icbm.run(make_cs(cluster), CC, make_lp(target, source=source), False)
      if f % 5 == 0:
        if icbm.cruise_button == SendButtonState.decrease:
          cluster -= 1
        elif icbm.cruise_button == SendButtonState.increase:
          cluster += 1
    return icbm, cluster

  def test_unrecognised_button_still_creates_a_hold(self):
    """THE POINT. No ButtonEvent ICBM knows about -- only the set speed moving."""
    icbm, cluster = self._drive({200: +1})
    assert icbm.override_state == OverrideState.manual, "no hold created without a known button"
    assert icbm.v_baseline == LIMIT + 1
    assert cluster == LIMIT + 1, f"set speed was walked back to {cluster}"

  def test_repeated_unrecognised_presses_accumulate(self):
    icbm, cluster = self._drive({200: +1, 400: +1, 600: +1})
    assert cluster == LIMIT + 3, f"ended at {cluster}, wanted {LIMIT + 3}"
    assert icbm.v_baseline == LIMIT + 3

  def test_downward_too(self):
    icbm, cluster = self._drive({200: -1})
    assert cluster == LIMIT - 1
    assert icbm.v_baseline == LIMIT - 1

  def test_a_curve_is_never_adopted(self):
    """The failure this fallback could reintroduce: ICBM slows for a curve, goes idle at the curve
    target, and the lowered set speed gets mistaken for the driver. Keyed on MOVEMENT, so once
    ICBM arrives the set speed stops moving and there is nothing to adopt."""
    icbm = fresh(max_rise=5, max_drop=8)
    set_baseline(icbm)
    cluster = DRIVER
    for _ in range(1500):                     # ICBM drives the set speed down for a curve
      icbm.run(make_cs(cluster), CC, make_lp(40, source=PlanSource.sccVision), False)
      if icbm.cruise_button == SendButtonState.decrease:
        cluster -= 1
    assert cluster <= 41, f"curve slowing stalled at {cluster}"
    assert icbm.v_baseline == DRIVER, f"curve was adopted as the baseline ({icbm.v_baseline})"

  def test_icbm_own_recovery_is_never_adopted(self):
    """Same risk on the way back up."""
    icbm = fresh(max_rise=5, max_drop=8)
    set_baseline(icbm)
    cluster = DRIVER
    for _ in range(1500):
      icbm.run(make_cs(cluster), CC, make_lp(40, source=PlanSource.sccVision), False)
      if icbm.cruise_button == SendButtonState.decrease:
        cluster -= 1
    for _ in range(2500):                     # curve ends, ICBM climbs back to the driver's number
      icbm.run(make_cs(cluster), CC, make_lp(LIMIT), False)
      if icbm.cruise_button == SendButtonState.increase:
        cluster += 1
    assert icbm.v_baseline == DRIVER, f"recovery was adopted ({icbm.v_baseline})"
    assert cluster == DRIVER, f"did not return to the driver's number ({cluster})"
