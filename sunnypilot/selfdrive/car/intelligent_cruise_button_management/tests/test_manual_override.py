"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FusionPilot: behavioral tests for the ICBM driver baseline.

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
from openpilot.common.constants import CV
from openpilot.sunnypilot.selfdrive.car.cruise_ext import V_CRUISE_MAX
from openpilot.sunnypilot.selfdrive.car.intelligent_cruise_button_management.controller import (
  IntelligentCruiseButtonManagement, DEFAULT_BASELINE_RESET_DELTA,
)

OverrideState = custom.IntelligentCruiseButtonManagement.OverrideState
State = custom.IntelligentCruiseButtonManagement.IntelligentCruiseButtonManagementState
SendButtonState = custom.IntelligentCruiseButtonManagement.SendButtonState
UnconfirmedLeadState = custom.LongitudinalPlanSP.UnconfirmedLead.State
PlanSource = custom.LongitudinalPlanSP.LongitudinalPlanSource
ButtonType = car.CarState.ButtonEvent.Type
BaselineSource = custom.IntelligentCruiseButtonManagement.BaselineSource

MPH = 0.44704
LIMIT = 55    # what SLA wants: posted limit + the configured offset
DRIVER = 70   # what the driver wants instead, for this limit

ACCEL_PRESS = NS(type=NS(raw=ButtonType.accelCruise), pressed=True)
ACCEL_RELEASE = NS(type=NS(raw=ButtonType.accelCruise), pressed=False)
GAP_PRESS = NS(type=NS(raw=ButtonType.gapAdjustCruise), pressed=True)
DECEL_PRESS = NS(type=NS(raw=ButtonType.decelCruise), pressed=True)
DECEL_RELEASE = NS(type=NS(raw=ButtonType.decelCruise), pressed=False)

CC = NS(enabled=True, cruiseControl=NS(resume=False, override=False, cancel=False))
# FusionPilot: controlsd sets cruiseControl.override whenever longitudinal is being overridden, and
# gasPressedOverride is the only event that does so -- see apply_gas_handoff.
CC_override = NS(enabled=True, cruiseControl=NS(resume=False, override=True, cancel=False))


def make_cs(cluster, v_ego=None, buttons=(), enabled=True, gas_pressed=False, brake_pressed=False):
  """FusionPilot: gasPressed and brakePressed are on every real CarState. They were missing here, so
  the day the controller started reading one, 111 tests failed at once on an AttributeError that
  cannot happen on the device. A fixture thinner than the real message hides nothing useful."""
  return NS(vEgo=(cluster if v_ego is None else v_ego) * MPH,
            gasPressed=gas_pressed,
            brakePressed=brake_pressed,
            cruiseState=NS(available=True, enabled=enabled, speedCluster=cluster * MPH,
                           standstill=False, speed=cluster * MPH),
            buttonEvents=buttons)


def make_lp(target, lead_state=UnconfirmedLeadState.inactive, lead_target=0.0,
            source=PlanSource.speedLimitAssist, limit_known=True,
            curve_active=False, curve_target=0.0, map_active=False):
  """limit_known defaults TRUE because that is the ordinary road: OSM has a limit for most places.

  It was absent entirely at first, which made LP_SP.speedLimit raise and every test run as though no
  limit were ever known -- the rarer case, silently, everywhere. Holding a fixture constant at the
  wrong value is how the model-stop path passed its tests for weeks while doing nothing on the car.
  """
  # smartCruiseControl was ABSENT here entirely, so the controller's try/except fired on every frame
  # of every test in this file and the whole curve-ceiling path was unreachable -- a fixture thinner
  # than the real message, hiding exactly the code it should have been exercising. Defaults match
  # what the except produced, so nothing that passed before changes meaning.
  return NS(vTarget=target * MPH,
            longitudinalPlanSource=source,
            speedLimit=NS(resolver=NS(speedLimitValid=limit_known,
                                      speedLimitLastValid=limit_known)),
            smartCruiseControl=NS(map=NS(active=map_active, vTarget=target * MPH),
                                  vision=NS(active=curve_active, vTarget=curve_target * MPH)),
            unconfirmedLead=NS(state=lead_state, vTarget=lead_target * MPH))


def fresh(max_rise=0, max_drop=0):
  """ICBM settled with no baseline, agreeing with the driver at the limit.

  Rate limiters default off here so target assertions read the baseline logic directly; they get
  their own class below. The timer clear below is now belt-and-braces -- cruise_button_timers is a
  per-instance copy since the aliasing fix -- and is kept as a regression tripwire.
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
  a shorter settle would assert on the stand-down rather than on steady-state behavior."""
  for _ in range(frames):
    icbm.run(make_cs(cluster), CC, make_lp(target, source=source), False)


def cycle_with_set(icbm, road_speed=48, off_frames=200, source=PlanSource.speedLimitAssist):
  """Cancel, then SET. Ford puts the set speed at the CURRENT VEHICLE SPEED, and that is how the
  behavioral detector tells SET from RESUME without needing a button event. (This car DOES deliver
  button events -- an earlier version of this docstring said otherwise, and that claim nearly got
  the press path deleted.) road_speed must differ from the set speed before the cancel, or the two
  are genuinely indistinguishable."""
  for _ in range(off_frames):
    icbm.run(make_cs(DRIVER, v_ego=road_speed, enabled=False), CC, make_lp(LIMIT, source=source), False)
  for _ in range(300):                       # past CRUISE_CYCLE_SETTLE_FRAMES either way
    icbm.run(make_cs(road_speed, v_ego=road_speed, enabled=True), CC, make_lp(LIMIT, source=source), False)


def cycle_with_resume(icbm, off_frames=200, source=PlanSource.speedLimitAssist):
  """Cancel, then RESUME: the set speed comes back to exactly what it was."""
  before = icbm.v_cruise_cluster
  for _ in range(off_frames):
    icbm.run(make_cs(before, enabled=False), CC, make_lp(LIMIT, source=source), False)
  for _ in range(300):
    icbm.run(make_cs(before, enabled=True), CC, make_lp(LIMIT, source=source), False)


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
    cycle_with_set(icbm)
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
    assert icbm.cruise_button != SendButtonState.none or icbm.icbm_idle_frames < 20, \
      "harness failed to keep ICBM busy, so this would not exercise the bug"
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


class TestReturningToTheLimitHandsItBack:
  """Walking the set speed back to exactly SLA's number clears the HOLD.

  This rule was built, then withdrawn on the reasoning that it made minus unpredictable -- whether
  a press adjusted the hold or deleted it depended on a number the driver cannot see. That reading
  was wrong about what the driver can see: the posted limit is on screen, and going back to it is a
  deliberate gesture, not an accident. It is also the only way out of a hold that does not require
  disengaging cruise, which matters on this car because SET/RESUME shares a CAN signal with cancel.

  The unpredictability it was withdrawn for is handled by baseline_diverged instead: the baseline
  has to have actually been somewhere else before coming back counts. See the instant-delete test.
  """

  def test_minus_down_to_the_sla_target_clears_the_hold(self):
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
    assert icbm.override_state == OverrideState.auto, "hold survived a return to SLA's number"
    assert icbm.v_baseline == 0

  def test_minus_that_stops_short_of_the_limit_still_adjusts(self):
    """Only landing exactly on SLA's number hands it back. Anything else is a new hold."""
    icbm = fresh()
    set_baseline(icbm)
    settle(icbm, LIMIT)
    cluster = DRIVER
    for _ in range(DRIVER - LIMIT - 3):             # stop 3 short
      icbm.run(make_cs(cluster, buttons=(DECEL_PRESS,)), CC, make_lp(LIMIT), False)
      cluster -= 1
      for f in range(12):
        icbm.run(make_cs(cluster, buttons=(DECEL_RELEASE,) if f == 2 else ()), CC,
                 make_lp(LIMIT), False)
    settle(icbm, LIMIT, cluster=cluster, frames=200)
    assert icbm.override_state == OverrideState.manual, "minus deleted the hold instead of adjusting it"
    assert icbm.v_baseline == LIMIT + 3

  def test_cancel_and_reengage_clears_the_hold(self):
    """The explicit control, and the one the driver actually uses."""
    icbm = fresh()
    set_baseline(icbm)
    settle(icbm, LIMIT)
    assert icbm.override_state == OverrideState.manual
    cycle_with_set(icbm)
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
    cycle_with_set(icbm, road_speed=LIMIT)
    assert icbm.override_state == OverrideState.auto, "resume rebuilt the HOLD it just cleared"
    assert icbm.v_baseline == 0


class TestMappingAgnosticFallback:
  """The press path assumes the driver's button arrives as one of MANUAL_OVERRIDE_BUTTONS. On a car
  with flashed SCCM firmware that is an assumption. If the set speed moves and ICBM has been silent
  far longer than any command of its own could take to land, a human moved it -- adopt it."""

  def _drive(self, moves, source=PlanSource.speedLimitAssist, target=LIMIT, frames=1400):
    """moves: {frame: delta} applied to the set speed with NO button event at all.

    Moves start well past frame 250: engaging cruise opens a settle window during which
    uncommanded movement is the resume jump, not the driver. Nobody presses + within 2.5 s of
    engaging, so the delay is realistic rather than a workaround.
    """
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
    icbm, cluster = self._drive({400: +1})
    assert icbm.override_state == OverrideState.manual, "no hold created without a known button"
    assert icbm.v_baseline == LIMIT + 1
    assert cluster == LIMIT + 1, f"set speed was walked back to {cluster}"

  def test_repeated_unrecognised_presses_accumulate(self):
    icbm, cluster = self._drive({400: +1, 700: +1, 1000: +1})
    assert cluster == LIMIT + 3, f"ended at {cluster}, wanted {LIMIT + 3}"
    assert icbm.v_baseline == LIMIT + 3

  def test_downward_too(self):
    icbm, cluster = self._drive({400: -1})
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


class TestResumeJumpIsNotADriverChange:
  """Re-engaging cruise makes the set speed jump to whatever it resumes at. The mapping-agnostic
  fallback sees uncommanded movement and cannot tell that apart from a press -- so without a
  settle window after a cruise cycle it built a HOLD at the resumed speed, destroying the only
  route this car has back to Speed Limit Assist (CNCL then RES+, since RES+ is a combined button).
  """

  def _cycle(self, resume_to, frames_off=200):
    icbm = fresh(max_rise=5, max_drop=8)
    for _ in range(300):
      icbm.run(make_cs(LIMIT), CC, make_lp(LIMIT), False)
    assert icbm.override_state == OverrideState.auto
    for _ in range(frames_off):                       # CNCL
      icbm.run(make_cs(LIMIT, enabled=False), CC, make_lp(LIMIT), False)
    for _ in range(3):                                # RES+
      icbm.run(make_cs(LIMIT, enabled=True), CC, make_lp(LIMIT), False)
    for _ in range(20):                               # set speed jumps to the resumed value
      icbm.run(make_cs(resume_to, enabled=True), CC, make_lp(LIMIT), False)
    for _ in range(600):
      icbm.run(make_cs(resume_to, enabled=True), CC, make_lp(LIMIT), False)
    return icbm

  @pytest.mark.parametrize("resume_to", [DRIVER, LIMIT + 5, LIMIT - 5])
  def test_resume_leaves_sla_in_charge(self, resume_to):
    icbm = self._cycle(resume_to)
    assert icbm.override_state == OverrideState.auto, \
      f"resuming to {resume_to} created a HOLD; CNCL+RES+ no longer hands the speed back"
    assert icbm.v_baseline == 0

  def test_a_press_after_the_resume_still_works(self):
    """The window must expire, not disable the fallback permanently.

    Resuming to LIMIT so ICBM has nothing to correct -- resuming to a speed SLA disagrees with
    leaves it actively driving the set speed, and movement while it is working is correctly NOT
    credited to the driver.
    """
    icbm = self._cycle(LIMIT)
    cluster = LIMIT
    for f in range(1200):
      if f == 500:
        cluster += 1                                  # driver nudges it, no known button event
      icbm.run(make_cs(cluster), CC, make_lp(LIMIT), False)
      if f % 5 == 0:
        if icbm.cruise_button == SendButtonState.decrease:
          cluster -= 1
        elif icbm.cruise_button == SendButtonState.increase:
          cluster += 1
    assert icbm.override_state == OverrideState.manual, "fallback stayed disabled after the resume"
    assert cluster == LIMIT + 1


class TestReturningToTheLimitClearsTheHold:
  """Walking the set speed back to exactly what SLA wants hands control back.

  The second way out of a hold, and the only one that does not require disengaging cruise. It was
  built, removed while fixing something else, and then not restored across the next rewrite --
  baseline_diverged was left behind as dead state, which is how the omission was found.
  """

  def test_a_press_that_lands_on_the_sla_number_is_not_instantly_deleted(self):
    """The failure the rule was withdrawn for the first time round.

    A hold is created at the set speed the driver pressed FROM, which on the first frame still
    equals SLA's target. Clearing on bare equality would delete it before the press had moved
    anything, making the minus button behave differently depending on an invisible number.
    """
    icbm = fresh()
    icbm.run(make_cs(LIMIT, buttons=(ACCEL_PRESS,)), CC, make_lp(LIMIT), False)
    icbm.run(make_cs(LIMIT), CC, make_lp(LIMIT), False)
    assert icbm.override_state == OverrideState.manual, "hold deleted on the frame it was created"

  def test_clearing_is_source_gated(self):
    """Under `cruise` there is no posted limit, so equality is coincidence rather than intent."""
    icbm = fresh()
    set_baseline(icbm, DRIVER)
    settle(icbm, DRIVER, cluster=DRIVER, source=PlanSource.cruise)
    assert icbm.override_state == OverrideState.manual


class TestCounterMovementBreaksTheDeadlock:
  """ICBM must be able to notice the driver disagreeing WHILE it is commanding.

  icbm_idle_frames resets to 0 on every frame ICBM sends a button, so the idle fallback can only
  fire when ICBM is quiet -- and ICBM walking the set speed back down is exactly when it is not.
  That is the reported symptom: the number goes up on the dash and gets taken back down one
  increment at a time, with the fallback structurally unable to intervene.
  """

  @staticmethod
  def _icbm_actively_decreasing():
    """SLA wants LIMIT, the set speed is well above it, so ICBM is commanding decrease."""
    icbm = fresh()
    for _ in range(300):  # past CRUISE_CYCLE_SETTLE_FRAMES
      icbm.run(make_cs(DRIVER), CC, make_lp(LIMIT), False)
    assert icbm.cruise_button == SendButtonState.decrease, "precondition: ICBM should be commanding"
    assert icbm.icbm_idle_frames == 0, "precondition: idle counter pinned while commanding"
    return icbm

  def test_set_speed_rising_against_a_decrease_command_is_adopted(self):
    icbm = self._icbm_actively_decreasing()
    # Driver presses and holds +. The press does not arrive as a button event -- the case the
    # fallback exists for -- and the car ramps the set speed up in one 5 mph step.
    icbm.run(make_cs(DRIVER + 5), CC, make_lp(LIMIT), False)
    assert icbm.override_state == OverrideState.manual, "ICBM never noticed the driver"
    assert icbm.v_baseline == DRIVER + 5

  def test_one_unit_of_counter_movement_is_not_enough(self):
    """A stale command of the opposite sign still in flight is worth at most one step."""
    icbm = self._icbm_actively_decreasing()
    icbm.run(make_cs(DRIVER + 1), CC, make_lp(LIMIT), False)
    assert icbm.override_state == OverrideState.auto

  def test_movement_agreeing_with_the_command_is_not_adopted(self):
    """ICBM's own commanded decrease must never look like a driver press."""
    icbm = self._icbm_actively_decreasing()
    for step in range(1, 6):
      icbm.run(make_cs(DRIVER - step), CC, make_lp(LIMIT), False)
    assert icbm.override_state == OverrideState.auto, "ICBM adopted its own commanded movement"

  def test_adopting_stands_icbm_down_so_it_stops_fighting(self):
    icbm = self._icbm_actively_decreasing()
    icbm.run(make_cs(DRIVER + 5), CC, make_lp(LIMIT), False)
    assert icbm.press_settle_frames > 0
    assert icbm.cruise_button == SendButtonState.none, "ICBM kept commanding after adopting"


class TestButtonTimersAreNotShared:
  def test_two_instances_do_not_share_timers(self):
    """Upstream binds the module-level CRUISE_BUTTON_TIMER dict by reference. Shared mutable state
    between ICBM and VCruiseHelperSP, and between every test case in this file."""
    a = IntelligentCruiseButtonManagement(NS(), NS(pcmCruiseSpeed=False))
    b = IntelligentCruiseButtonManagement(NS(), NS(pcmCruiseSpeed=False))
    assert a.cruise_button_timers is not b.cruise_button_timers
    a.cruise_button_timers[ButtonType.accelCruise] = 99
    assert b.cruise_button_timers[ButtonType.accelCruise] == 0


RESUME_PRESS = NS(type=NS(raw=ButtonType.resumeCruise), pressed=True)
SET_PRESS = NS(type=NS(raw=ButtonType.setCruise), pressed=True)


class TestResumeKeepsTheHoldAndSetGivesItBack:
  """RESUME and SET stopped being the same control.

  Every disengage/engage cycle used to drop the baseline whatever button caused it, so the two
  buttons were functionally identical and there was no way to get a hold back except rebuilding
  it by hand. The words on the buttons decide it: RESUME means go back to what I had, SET means
  start from here and let Speed Limit Assist have the number.
  """

  @staticmethod
  def _cycle_back_on(icbm, button, off_frames=200):
    for _ in range(off_frames):
      icbm.run(make_cs(DRIVER, enabled=False), CC, make_lp(LIMIT), False)
    # The press lands while cruise is still disengaged; engagement follows a few frames later.
    icbm.run(make_cs(DRIVER, enabled=False, buttons=(button,)), CC, make_lp(LIMIT), False)
    for _ in range(5):
      icbm.run(make_cs(DRIVER, enabled=False), CC, make_lp(LIMIT), False)
    for _ in range(30):
      icbm.run(make_cs(DRIVER, enabled=True), CC, make_lp(LIMIT), False)

  def test_resume_keeps_the_hold(self):
    icbm = fresh()
    set_baseline(icbm)
    settle(icbm, LIMIT)
    assert icbm.v_baseline == DRIVER
    self._cycle_back_on(icbm, RESUME_PRESS)
    assert icbm.override_state == OverrideState.manual, "RESUME threw the hold away"
    assert icbm.v_baseline == DRIVER

  def test_set_hands_it_back_to_sla(self):
    icbm = fresh()
    set_baseline(icbm)
    settle(icbm, LIMIT)
    cycle_with_set(icbm)
    assert icbm.override_state == OverrideState.auto, "SET kept the hold"
    assert icbm.v_baseline == 0

  def test_a_stale_resume_press_is_overruled_by_behaviour(self):
    """The button event is no longer what decides -- it cannot be, since this car does not deliver
    it. A RESUME press from minutes ago followed by a set speed landing on the ROAD SPEED is a SET,
    whatever the stale event says."""
    icbm = fresh()
    set_baseline(icbm)
    settle(icbm, LIMIT)
    icbm.run(make_cs(DRIVER, enabled=False, buttons=(RESUME_PRESS,)), CC, make_lp(LIMIT), False)
    for _ in range(400):                       # far longer than RESUME_PRESS_MEMORY_FRAMES
      icbm.run(make_cs(DRIVER, v_ego=48, enabled=False), CC, make_lp(LIMIT), False)
    for _ in range(300):                       # set speed lands on ROAD speed => it was a SET
      icbm.run(make_cs(48, v_ego=48, enabled=True), CC, make_lp(LIMIT), False)
    assert icbm.override_state == OverrideState.auto


class TestResumeWindowEndsWhenTheJumpLands:
  """The driver's habit is RESUME then immediately press-and-hold. A fixed 2.5 s window swallowed
  that press. Ending it on the jump settling gives the window back without giving up the guard."""

  def test_window_closes_once_the_set_speed_has_moved_and_settled(self):
    icbm = fresh()
    for _ in range(300):
      icbm.run(make_cs(LIMIT), CC, make_lp(LIMIT), False)
    for _ in range(200):
      icbm.run(make_cs(LIMIT, enabled=False), CC, make_lp(LIMIT), False)
    icbm.run(make_cs(LIMIT, enabled=True), CC, make_lp(LIMIT), False)
    assert icbm.cruise_cycle_frames > 0
    for _ in range(60):                        # the resume jump lands, then holds still
      icbm.run(make_cs(LIMIT + 5, enabled=True), CC, make_lp(LIMIT), False)
    assert icbm.cruise_cycle_frames == 0, "window outlived the jump it exists to cover"

  def test_window_does_not_close_before_the_jump_arrives(self):
    """The set speed is already stable for the whole time cruise is off, so a bare stability test
    closes the window early and the jump is then adopted as a driver press."""
    icbm = fresh()
    for _ in range(300):
      icbm.run(make_cs(LIMIT), CC, make_lp(LIMIT), False)
    for _ in range(200):
      icbm.run(make_cs(LIMIT, enabled=False), CC, make_lp(LIMIT), False)
    icbm.run(make_cs(LIMIT, enabled=True), CC, make_lp(LIMIT), False)
    for _ in range(60):                        # stable, but the jump has not happened yet
      icbm.run(make_cs(LIMIT, enabled=True), CC, make_lp(LIMIT), False)
    assert icbm.cruise_cycle_frames > 0, "window closed before the resume jump could land"


class TestRiseLimiterCannotStallForever:
  """Reported: ICBM stuck at a low set speed while traveling well below it, no curve involved.

  The rise limiter only advanced its ceiling once ACTUAL vehicle speed reached it, which assumes
  the set speed is what is holding the car back. Behind slower traffic, on a climb, or while ACC
  brakes for a lead, v_ego never gets there -- so the ceiling froze and the set speed could not be
  raised again for the rest of the drive.
  """

  @staticmethod
  def _held_below(icbm, cluster, v_ego, frames):
    for _ in range(frames):
      icbm.run(make_cs(cluster, v_ego=v_ego), CC, make_lp(DRIVER), False)

  def test_ceiling_advances_even_if_actual_speed_never_catches_up(self):
    icbm = fresh(max_rise=5)
    # Target 70, set speed 50, but the car is stuck at 40 behind traffic and never speeds up.
    self._held_below(icbm, cluster=50, v_ego=40, frames=400)
    assert icbm.v_target > 50, "rise limiter never released; set speed can never recover"

  def test_it_still_meters_when_the_car_is_actually_accelerating(self):
    """The timeout and the settle margin must not turn the limiter off -- one step at a time is
    still the point, however short the wait between steps gets."""
    icbm = fresh(max_rise=5)
    icbm.run(make_cs(50, v_ego=50), CC, make_lp(DRIVER), False)
    assert icbm.v_target <= 55, "limiter let the whole rise through in one step"


class TestTheHoldSurvivesADisengagement:
  """Reported: hold set, cruise canceled to make a turn, RESUME pressed, hold gone.

  Neither clearing rule was gated on cruise being engaged. Turning off a road changes the posted
  limit, which fired the 10 mph limit-change rule -- so the hold was destroyed silently, mid-turn,
  before RESUME was ever pressed. The driver disengages to turn precisely BECAUSE the road is about
  to change, so the rule was firing on the one manoeuvre where it should not.
  """

  @staticmethod
  def _turn_onto_a_slower_road(icbm, frames=300):
    """Cruise off, and SLA's target follows the new road down from 55 to 25."""
    for _ in range(frames):
      icbm.run(make_cs(DRIVER, enabled=False), CC, make_lp(25), False)

  def test_a_limit_change_while_disengaged_does_not_discard_the_hold(self):
    icbm = fresh()
    set_baseline(icbm)
    settle(icbm, LIMIT)
    assert icbm.v_baseline == DRIVER
    self._turn_onto_a_slower_road(icbm)
    assert icbm.override_state == OverrideState.manual, "the turn destroyed the hold"
    assert icbm.v_baseline == DRIVER

  def test_resume_after_the_turn_restores_it(self):
    icbm = fresh()
    set_baseline(icbm)
    settle(icbm, LIMIT)
    self._turn_onto_a_slower_road(icbm)
    icbm.run(make_cs(DRIVER, enabled=False, buttons=(RESUME_PRESS,)), CC, make_lp(25), False)
    for _ in range(60):
      icbm.run(make_cs(DRIVER, enabled=True), CC, make_lp(25), False)
    assert icbm.override_state == OverrideState.manual, "RESUME did not restore the hold"
    assert icbm.v_baseline == DRIVER

  def test_a_limit_change_after_resuming_still_discards_it(self):
    """Freezing defers the judgement, it does not skip it. Once driving again, a NEW zone change
    measured from the road actually being driven must still hand the speed back to SLA."""
    icbm = fresh()
    set_baseline(icbm)
    settle(icbm, LIMIT)
    self._turn_onto_a_slower_road(icbm)
    icbm.run(make_cs(DRIVER, enabled=False, buttons=(RESUME_PRESS,)), CC, make_lp(25), False)
    settle(icbm, 25, cluster=DRIVER, frames=100)
    assert icbm.override_state == OverrideState.manual, "re-anchor did not take"
    settle(icbm, 60, cluster=DRIVER, frames=200)   # now a real 35 mph jump from the re-anchor
    assert icbm.override_state == OverrideState.auto, "limit rule never fired after resuming"


class TestRiseLimiterOnlyMattersWithNoLead:
  """The owner's rule, which is a better discriminator than any timer: behind a car, set the speed
  to anything, because that car is probably driving correctly. ACC is gap-limited there and the set
  speed is a ceiling it never reaches. It is only with nobody ahead that the number is what the car
  chases -- and that is the only case the limiter was ever protecting.
  """

  def test_behind_a_lead_the_rise_is_not_metered(self):
    icbm = fresh(max_rise=5)
    icbm.run(make_cs(50, v_ego=45), CC, make_lp(DRIVER), False, True)
    assert icbm.v_target == DRIVER, "metered the set speed behind a lead, where it cannot bind"

  def test_with_no_lead_it_still_meters(self):
    icbm = fresh(max_rise=5)
    icbm.run(make_cs(50, v_ego=45), CC, make_lp(DRIVER), False, False)
    assert icbm.v_target <= 55, "open road rise was not metered"

  def test_a_lead_appearing_releases_a_stuck_step(self):
    """The stall this replaces: held below the ceiling with no lead, then traffic appears."""
    icbm = fresh(max_rise=5)
    for _ in range(100):
      icbm.run(make_cs(50, v_ego=45), CC, make_lp(DRIVER), False, False)
    assert icbm.v_target <= 55
    icbm.run(make_cs(50, v_ego=45), CC, make_lp(DRIVER), False, True)
    assert icbm.v_target == DRIVER


class TestPressingDuringACurveDoesNotRedefineTheHold:
  """Measured: hold at 70, SCC drags the set speed to 45 for a curve, one + press, hold becomes 50.
  The driver's number was gone for the rest of the drive from a single button press.

  The baseline is normally "wherever the press settles", which is right when the driver is choosing
  a cruising speed and wrong when something else is holding the speed down. A curve is a physics
  limit the baseline only caps, never raises -- so a press during one cannot mean "my cruising
  number is now 50". It means "ease off here", which Ford applies to the set speed directly.
  """

  @staticmethod
  def _into_a_curve(icbm, target=45):
    cluster = DRIVER
    for _ in range(400):
      icbm.run(make_cs(cluster), CC, make_lp(target, source=PlanSource.sccVision), False)
      if icbm.cruise_button == SendButtonState.decrease:
        cluster -= 1
    return cluster

  def test_a_press_mid_curve_leaves_the_hold_alone(self):
    icbm = fresh()
    set_baseline(icbm)
    cluster = self._into_a_curve(icbm)
    assert cluster == 45 and icbm.v_baseline == DRIVER
    icbm.run(make_cs(cluster, buttons=(ACCEL_PRESS,)), CC,
             make_lp(45, source=PlanSource.sccVision), False)
    cluster += 5
    icbm.run(make_cs(cluster, buttons=(ACCEL_RELEASE,)), CC,
             make_lp(45, source=PlanSource.sccVision), False)
    settle(icbm, 45, cluster=cluster, frames=200, source=PlanSource.sccVision)
    assert icbm.v_baseline == DRIVER, "a mid-curve press redefined the driver's cruising speed"

  def test_the_hold_is_still_there_when_the_curve_ends(self):
    """The point of leaving it alone: the number has to survive to be returned to."""
    icbm = fresh()
    set_baseline(icbm)
    cluster = self._into_a_curve(icbm)
    icbm.run(make_cs(cluster, buttons=(ACCEL_PRESS,)), CC,
             make_lp(45, source=PlanSource.sccVision), False)
    cluster += 5
    icbm.run(make_cs(cluster, buttons=(ACCEL_RELEASE,)), CC,
             make_lp(45, source=PlanSource.sccVision), False)
    for _ in range(800):                       # curve ends, SLA back in charge
      icbm.run(make_cs(cluster), CC, make_lp(LIMIT), False)
      if icbm.cruise_button == SendButtonState.increase:
        cluster += 1
    assert cluster == DRIVER, f"returned to {cluster}, not the driver's {DRIVER}"

  def test_scc_reclaims_the_set_speed_after_the_press(self):
    """The owner's stated expectation: on a curve they would use the pedals, and if they did press
    +/- they would want it to go back to what SCC wants. Leaving the hold alone is only half of
    that -- the curve target has to reassert itself too, and promptly."""
    icbm = fresh()
    set_baseline(icbm)
    cluster = self._into_a_curve(icbm)
    icbm.run(make_cs(cluster, buttons=(ACCEL_PRESS,)), CC,
             make_lp(45, source=PlanSource.sccVision), False)
    cluster += 5
    icbm.run(make_cs(cluster, buttons=(ACCEL_RELEASE,)), CC,
             make_lp(45, source=PlanSource.sccVision), False)
    for _ in range(300):                       # measured at ~0.8 s; 3 s is ample headroom
      icbm.run(make_cs(cluster), CC, make_lp(45, source=PlanSource.sccVision), False)
      if icbm.cruise_button == SendButtonState.decrease:
        cluster -= 1
    assert cluster == 45, f"SCC never reclaimed the set speed; stuck at {cluster}"
    assert icbm.v_baseline == DRIVER

  def test_a_press_with_no_curve_still_sets_the_hold(self):
    """The guard must be narrow -- under cruise/SLA the driver IS choosing the number."""
    icbm = fresh()
    set_baseline(icbm, to=65)
    settle(icbm, LIMIT, cluster=65)
    assert icbm.v_baseline == 65


class TestIcbmStaysOutOfItUnderMadsOnly:
  """The owner drives with MADS engaged essentially all the time, so openpilot is steering whenever
  the car is moving -- including with ACC switched off.

  That matters more than it looks. ICBM's only actuator is injected set-speed button presses, and
  while cruise is DISENGAGED those map to setCruise, which ENGAGES cruise. If ICBM ever commanded
  under MADS-only it would turn stock ACC on by itself.

  It cannot, and the guard is indirect enough to be worth pinning: controlsd sets
  cruiseControl.override = CC.enabled and not CC.longActive on this platform (pcmCruiseSpeed is
  False), which is exactly the MADS-only condition, and update_readiness requires not override.
  """

  @staticmethod
  def _mads_only():
    """openpilot engaged for lateral, longitudinal not active -- so controlsd raises override."""
    return NS(enabled=True, cruiseControl=NS(resume=False, override=True, cancel=False))

  def test_icbm_is_not_ready_under_mads_only(self):
    icbm = fresh()
    icbm.run(make_cs(LIMIT, enabled=False), self._mads_only(), make_lp(DRIVER), False)
    assert not icbm.is_ready, "ICBM considered itself ready with only lateral engaged"

  def test_icbm_commands_no_buttons_under_mads_only(self):
    """The consequence that actually matters: a press here would engage ACC unbidden."""
    icbm = fresh()
    for _ in range(300):
      icbm.run(make_cs(LIMIT, enabled=False), self._mads_only(), make_lp(DRIVER), False)
      assert icbm.cruise_button == SendButtonState.none, \
        "ICBM sent a set-speed button with cruise off; that press would engage ACC"


class TestTheBadgeGreysWhenCruiseIsOff:
  """Matters because of MADS: the owner drives with lateral engaged essentially always, so
  "openpilot steering with ACC off" is a normal cruising state, not a moment in passing.

  The hold survives that disengagement by design and stays on screen -- but +/- there map to
  setCruise, which engages cruise and DISCARDS the hold rather than adjusting it. Showing the badge
  as live would be a lie for as long as that lasts, which on this car is a lot of the time.
  """

  def test_suppressed_while_cruise_is_off(self):
    icbm = fresh()
    set_baseline(icbm)
    settle(icbm, LIMIT)
    assert not icbm.hold_suppressed
    for _ in range(50):
      icbm.run(make_cs(DRIVER, enabled=False), CC, make_lp(LIMIT), False)
    assert icbm.v_baseline == DRIVER, "the hold should still be remembered"
    assert icbm.hold_suppressed, "badge would show as live while +/- cannot adjust it"

  def test_live_again_once_cruise_returns(self):
    icbm = fresh()
    set_baseline(icbm)
    settle(icbm, LIMIT)
    for _ in range(50):
      icbm.run(make_cs(DRIVER, enabled=False), CC, make_lp(LIMIT), False)
    icbm.run(make_cs(DRIVER, enabled=False, buttons=(RESUME_PRESS,)), CC, make_lp(LIMIT), False)
    for _ in range(60):
      icbm.run(make_cs(DRIVER, enabled=True), CC, make_lp(LIMIT), False)
    assert not icbm.hold_suppressed


class TestBaselineSourceIsRecorded:
  """TEMPORARY diagnostic. ICBM has two independent ways to notice the driver moved the set speed,
  and every drive so far suggests only the fallback fires on this car. This records which, so one
  ordinary drive answers it instead of inference -- and if the press path never appears, it and its
  hand-picked settle timers get deleted.

  Delete this class along with the field once that is settled.
  """

  def test_a_real_button_event_records_the_press_path(self):
    icbm = fresh()
    set_baseline(icbm)
    assert icbm.baseline_source == BaselineSource.press

  def test_movement_against_an_icbm_command_records_the_counter_fallback(self):
    """The case that rescued the deadlock: ICBM commanding down, set speed goes up anyway."""
    icbm = fresh()
    for _ in range(300):                       # past CRUISE_CYCLE_SETTLE_FRAMES, ICBM decreasing
      icbm.run(make_cs(DRIVER), CC, make_lp(LIMIT), False)
    assert icbm.cruise_button == SendButtonState.decrease
    icbm.run(make_cs(DRIVER + 5), CC, make_lp(LIMIT), False)
    assert icbm.override_state == OverrideState.manual
    assert icbm.baseline_source == BaselineSource.fallbackCounter

  def test_it_survives_the_hold_being_cleared(self):
    """The question is whether the press path EVER fires on a drive, so the answer has to outlive
    the hold it describes -- clear_baseline must not reset it."""
    icbm = fresh()
    set_baseline(icbm)
    assert icbm.baseline_source == BaselineSource.press
    cycle_with_set(icbm)
    assert icbm.override_state == OverrideState.auto, "precondition: hold was cleared"
    assert icbm.baseline_source == BaselineSource.press, "the diagnostic was wiped with the hold"


class TestResumeIsRecognisedWithoutAButtonEvent:
  """The reported bug, and the reason it survived every timing fix: the hold has to survive a
  cancel-and-resume even when no resumeCruise ButtonEvent ever arrives.

  Do not read this as "this car delivers no button events". It does -- baselineSource on a real
  5 mph hold showed the press path capturing first, with the fallback relabeling it a frame
  later. An earlier reading of "always I" was that relabeling, not a dead press path, and acting
  on it would have deleted working code.

  What this class actually pins down is that the resume path must not DEPEND on a button event.
  Ford separates the two itself, in the set speed: RESUME restores the previous value, SET jumps to
  the current road speed. That is enough to tell them apart with no button event at all.
  """

  def test_resume_keeps_the_hold_with_no_button_event_at_all(self):
    icbm = fresh()
    set_baseline(icbm)
    settle(icbm, LIMIT)
    assert icbm.v_baseline == DRIVER
    cycle_with_resume(icbm)                    # note: no buttons= anywhere
    assert icbm.override_state == OverrideState.manual, "RESUME lost the hold again"
    assert icbm.v_baseline == DRIVER

  def test_set_still_hands_it_back_with_no_button_event(self):
    icbm = fresh()
    set_baseline(icbm)
    settle(icbm, LIMIT)
    cycle_with_set(icbm, road_speed=48)
    assert icbm.override_state == OverrideState.auto, "SET kept the hold"

  def test_a_turn_onto_a_slower_road_then_resume(self):
    """The exact sequence reported: hold, cancel to turn, resume. The posted limit changing during
    the turn must not matter, and neither must the missing button event."""
    icbm = fresh()
    set_baseline(icbm)
    settle(icbm, LIMIT)
    for _ in range(300):                       # turning, cruise off, new road's limit is lower
      icbm.run(make_cs(DRIVER, enabled=False), CC, make_lp(35), False)
    cycle_with_resume(icbm, off_frames=20, source=PlanSource.speedLimitAssist)
    assert icbm.override_state == OverrideState.manual, "the turn still costs the hold"
    assert icbm.v_baseline == DRIVER


class TestPressIsNotRelabelledByTheFallback:
  """The diagnostic read "I" on every drive and I concluded the press path was dead, and came close
  to deleting it. It is not dead -- the label was being overwritten.

  A press arms the stand-down, which makes ICBM idle by definition, and the driver's HELD button
  keeps stepping the set speed for seconds afterwards. Past ADOPT_IDLE_FRAMES the idle fallback
  fires and relabels a capture the press path had already made. The field answered "what captured
  it last" when the question was "did the press path fire at all".
  """

  def test_a_realistic_press_and_hold_still_reads_as_press(self):
    """5 mph jumps with stationary gaps -- the car's actual behavior, not one step per frame."""
    icbm = fresh()
    icbm.run(make_cs(LIMIT, buttons=(ACCEL_PRESS,)), CC, make_lp(LIMIT), False)
    assert icbm.baseline_source == BaselineSource.press
    cluster = LIMIT
    for _ in range(3):
      for _ in range(60):                      # long enough to pass ADOPT_IDLE_FRAMES
        icbm.run(make_cs(cluster), CC, make_lp(LIMIT), False)
      cluster += 5
      icbm.run(make_cs(cluster), CC, make_lp(LIMIT), False)
    icbm.run(make_cs(cluster, buttons=(ACCEL_RELEASE,)), CC, make_lp(LIMIT), False)
    settle(icbm, LIMIT, cluster=cluster, frames=200)
    assert icbm.baseline_source == BaselineSource.press, \
      "the fallback relabeled a press; the diagnostic answers the wrong question again"
    assert icbm.v_baseline == cluster

  def test_the_fallback_still_labels_itself_when_it_is_the_only_one(self):
    icbm = fresh()
    for _ in range(300):
      icbm.run(make_cs(DRIVER), CC, make_lp(LIMIT), False)
    icbm.run(make_cs(DRIVER + 5), CC, make_lp(LIMIT), False)
    assert icbm.baseline_source == BaselineSource.fallbackCounter


class TestGasPedalHandoff:
  """FusionPilot: the set speed follows the car while the driver is on the throttle.

  Without it, gasPressedOverride stands ICBM down for as long as the pedal is held, the set speed is
  left behind, and lifting off hands a much lower number back to Ford's ACC, which then brakes."""

  @staticmethod
  def _icbm(gas: bool, cluster: int, road: int, limit: int = LIMIT):
    icbm = fresh()
    cc = CC_override if gas else CC
    for _ in range(20):
      icbm.run(make_cs(cluster, v_ego=road, gas_pressed=gas), cc, make_lp(limit), False)
    return icbm

  def test_target_follows_the_car_up_while_on_the_gas(self):
    icbm = self._icbm(gas=True, cluster=35, road=65)
    assert icbm.v_target >= 65, "the set speed must not be left behind at 35 while doing 65"
    assert icbm.gas_handoff_active

  def test_it_only_ever_raises(self):
    """On the gas but below the set speed -- e.g. accelerating within a hold -- changes nothing."""
    icbm = self._icbm(gas=True, cluster=70, road=45, limit=70)
    assert icbm.v_target <= 70

  def test_no_handoff_without_the_pedal(self):
    icbm = self._icbm(gas=False, cluster=35, road=65)
    assert not icbm.gas_handoff_active
    assert icbm.v_target <= LIMIT + 1, "with the pedal up the planner's target governs"

  def test_icbm_stays_ready_through_a_gas_override(self):
    """The readiness check is what used to stand ICBM down; gasPressedOverride is the only event
    carrying ET.OVERRIDE_LONGITUDINAL, so it is safe to single out."""
    icbm = self._icbm(gas=True, cluster=35, road=65)
    assert icbm.is_ready

  def test_a_non_gas_override_still_stands_icbm_down(self):
    """override without the pedal is something else entirely and must keep its old meaning."""
    icbm = fresh()
    for _ in range(20):
      icbm.run(make_cs(35, v_ego=65, gas_pressed=False), CC_override, make_lp(LIMIT), False)
    assert not icbm.is_ready
    assert not icbm.gas_handoff_active

  def test_the_baseline_is_not_touched(self):
    """Using the throttle is not the same statement as pressing SET. Treating it as a hold would
    rewrite the driver's number on every overtake."""
    icbm = self._icbm(gas=True, cluster=35, road=80)
    assert icbm.v_baseline == 0
    assert icbm.override_state == OverrideState.auto


class TestOvertakeReturnsToTheDriversNumber:
  """FusionPilot: the question the gas handoff has to answer.

  Hold at 70, floor it to 85 to pass, lift. The handoff raised the set speed to 85 so lifting off
  did not brake -- but the driver wants their 70 back, not to cruise at the speed they overtook at.
  Nothing new was added for this: the baseline IS "what I had it set to", and the drop limiter is
  already the thing that walks an inflated set speed back down by coasting. These tests exist to
  prove that rather than assume it, because the handoff is what made the number inflate.
  """
  HOLD = 70
  PASS_SPEED = 85

  def _pass_and_release(self, icbm, limit=LIMIT):
    # on the gas, accelerating past the hold
    for _ in range(60):
      icbm.run(make_cs(self.PASS_SPEED, v_ego=self.PASS_SPEED, gas_pressed=True),
               CC_override, make_lp(limit), False)
    raised = icbm.v_target
    # pedal released, still travelling fast; ACC now owns it again
    cluster = max(icbm.v_target, self.PASS_SPEED)
    for _ in range(400):
      cluster = max(self.HOLD, cluster - 0.02)   # ACC coasts the car and cluster back down
      icbm.run(make_cs(round(cluster), v_ego=round(cluster)), CC, make_lp(limit), False)
    return raised, icbm

  def test_a_hold_survives_the_overtake_and_is_returned_to(self):
    icbm = fresh()
    set_baseline(icbm, self.HOLD)
    settle(icbm, LIMIT)
    assert icbm.v_baseline == self.HOLD

    raised, icbm = self._pass_and_release(icbm)
    assert raised >= self.PASS_SPEED, "the handoff should have carried the set speed up"
    assert icbm.v_baseline == self.HOLD, "the overtake must not rewrite the driver's number"
    assert icbm.v_target == self.HOLD, "and the set speed must come back to it"

  def test_without_a_hold_it_returns_to_the_speed_limit_target(self):
    icbm = fresh()
    settle(icbm, LIMIT)
    _, icbm = self._pass_and_release(icbm)
    assert icbm.override_state == OverrideState.auto
    assert icbm.v_target == LIMIT

  def test_the_overtake_never_creates_a_hold(self):
    """The fallback adopts uncommanded set-speed movement. During the handoff ICBM IS commanding,
    so it must not credit its own work to the driver -- that would strand a 85 mph hold."""
    icbm = fresh()
    settle(icbm, LIMIT)
    for _ in range(200):
      icbm.run(make_cs(self.PASS_SPEED, v_ego=self.PASS_SPEED, gas_pressed=True),
               CC_override, make_lp(LIMIT), False)
    assert icbm.v_baseline == 0, "the throttle is not a press"
    assert icbm.baseline_source == BaselineSource.none


class TestNoPostedLimitMeansNoHold:
  """WITHOUT A LIMIT THERE IS NO HOLD. Asked for directly on 2026-08-15.

  His words: *"I want the +/- to just affect the max speed like normal, like when ICBM is off
  entirely, not affect the little number above the max speed"* and *"there's no point in having the
  max speed be stuck where I hit set when there is no SLA"*.

  THIS CLASS REPLACES `TestAHoldMadeWhereNoLimitIsKnown`, which tested the opposite: a hold made
  where no limit was known had to survive, and then clear when a matching limit was acquired. That
  behaviour came from a 2026-08-06 report and was correct under the old model. The new rule deletes
  its premise -- the hold is never created -- so the old test is not weakened here, it is obsolete.

  Measured justification, route 00000379: SLA had a limit in 1.7% of plan frames, yet a hold was
  held for 36.5% of the drive with `baselineSource` reading fallbackIdle. He pressed SET five times
  and nothing else, so nearly every one of those holds was inferred rather than chosen.
  """

  def _press_with_no_limit(self, to=DRIVER):
    icbm = fresh()
    for _ in range(5):
      icbm.run(make_cs(LIMIT), CC, make_lp(LIMIT, limit_known=False), False)
    icbm.run(make_cs(LIMIT, buttons=(ACCEL_PRESS,)), CC, make_lp(LIMIT, limit_known=False), False)
    for cluster in range(LIMIT, to + 1):
      icbm.run(make_cs(cluster, buttons=(ACCEL_PRESS,)), CC, make_lp(LIMIT, limit_known=False), False)
    icbm.run(make_cs(to, buttons=(ACCEL_RELEASE,)), CC, make_lp(LIMIT, limit_known=False), False)
    return icbm

  def test_a_press_where_no_limit_is_known_creates_no_hold(self):
    icbm = self._press_with_no_limit()
    assert icbm.v_baseline == 0, "a hold was created with no posted limit to hold against"
    assert icbm.override_state == OverrideState.auto, "the driver was latched into manual override"

  def test_the_limit_going_away_mid_drive_drops_an_existing_hold(self):
    """Coverage ends -- a tunnel, a new road, the edge of the map. The hold has nothing left to
    mean, and leaving it would keep the max speed pinned where he last pressed."""
    icbm = fresh()
    icbm.run(make_cs(LIMIT, buttons=(ACCEL_PRESS,)), CC, make_lp(LIMIT), False)
    for cluster in range(LIMIT, DRIVER + 1):
      icbm.run(make_cs(cluster, buttons=(ACCEL_PRESS,)), CC, make_lp(LIMIT), False)
    icbm.run(make_cs(DRIVER, buttons=(ACCEL_RELEASE,)), CC, make_lp(LIMIT), False)
    assert icbm.v_baseline == DRIVER, "no hold was created against a KNOWN limit"

    # The target stays at LIMIT, NOT at DRIVER. Publishing DRIVER lets the existing
    # baseline-equals-target rule clear the hold on its own, so the test passed with the new rule
    # disabled -- caught by mutation-testing it rather than by it being green.
    for _ in range(50):
      icbm.run(make_cs(DRIVER), CC, make_lp(LIMIT, limit_known=False), False)
    assert icbm.v_baseline == 0, "the hold outlived the limit it was held against"

  def test_a_hold_against_a_KNOWN_limit_is_untouched(self):
    """The rule must not reach the ordinary road, which is where holds earn their place."""
    icbm = fresh()
    icbm.run(make_cs(LIMIT, buttons=(ACCEL_PRESS,)), CC, make_lp(LIMIT), False)
    for cluster in range(LIMIT, DRIVER + 1):
      icbm.run(make_cs(cluster, buttons=(ACCEL_PRESS,)), CC, make_lp(LIMIT), False)
    icbm.run(make_cs(DRIVER, buttons=(ACCEL_RELEASE,)), CC, make_lp(LIMIT), False)
    for _ in range(200):
      icbm.run(make_cs(DRIVER), CC, make_lp(LIMIT), False)
    assert icbm.v_baseline == DRIVER, "a hold against a real limit was discarded"

  def test_a_hold_created_AT_a_known_limit_is_not_deleted_on_its_first_frame(self):
    """Carried over from the class this replaced. A hold whose value already equals SLA's target
    has NOT diverged from it, and the clear rule requires divergence first -- otherwise creating a
    hold at the posted limit deletes it instantly. Distinct from the test above, where the hold sits
    above the limit and so has legitimately diverged from frame one."""
    icbm = fresh()
    icbm.run(make_cs(LIMIT, buttons=(ACCEL_PRESS,)), CC, make_lp(LIMIT), False)
    icbm.run(make_cs(LIMIT, buttons=(ACCEL_RELEASE,)), CC, make_lp(LIMIT), False)
    for _ in range(5):
      icbm.run(make_cs(LIMIT), CC, make_lp(LIMIT), False)
    assert icbm.baseline_diverged is False, "a hold at a known limit started already diverged"

  def test_a_PINNED_hold_survives_with_no_limit(self):
    """The one exception, and the reason this is not a blanket guard inside update_manual_override.

    A pin is an explicit gesture at an explicit place, so it is the only hold that still means
    something with no posted limit -- and `apply_pinned_hold` runs inside update_manual_override,
    so a rule applied there without this carve-out would silently delete the whole feature.
    """
    icbm = fresh()
    for _ in range(200):
      icbm.run(make_cs(48, v_ego=48), CC, make_lp(LIMIT, limit_known=False), False, pinned_hold=45)
    assert icbm.v_baseline == 45, "a pinned hold was discarded because no limit was known"
    assert icbm.baseline_source == BaselineSource.pinned


def in_zone(icbm, speed, frames, enabled=True, road_speed=48):
  """Drive with a pinned hold of `speed` in range (0 means no pin here), cruise on or off."""
  for _ in range(frames):
    icbm.run(make_cs(road_speed, v_ego=road_speed, enabled=enabled), CC, make_lp(LIMIT), False,
             pinned_hold=speed)


class TestAPinnedHoldSurvivesCruiseBeingOff:
  """The pin has to be there on the drives that START inside its radius.

  That is a fresh boot in the driveway, a workplace lot within the radius, or any engagement made
  after arriving -- and on this car it is most of them, since the pin exists for roads driven daily.
  The edge used to be consumed the instant GPS matched, cruise state ignored, so by the time cruise
  came on the pin was already marked as fired and the number silently never applied. Nothing showed
  it: no alert, no event, and the pinned-holds tests only ever exercised the storage class.
  """

  def test_engaging_inside_a_pinned_zone_applies_the_pin(self):
    icbm = fresh()
    in_zone(icbm, 45, 200, enabled=False)          # parked or coasting in the zone, cruise off
    in_zone(icbm, 45, 300, enabled=True)           # driver engages, still inside it
    assert icbm.v_baseline == 45, "the pin was consumed while cruise was off"
    assert icbm.override_state == OverrideState.manual
    assert icbm.baseline_source == BaselineSource.pinned

  def test_a_pin_entered_while_already_engaged_still_fires(self):
    """The case that did work, kept as the counterweight: the fix must not trade one for the other."""
    icbm = fresh()
    in_zone(icbm, 0, 50, enabled=True)
    in_zone(icbm, 45, 50, enabled=True)
    assert icbm.v_baseline == 45
    assert icbm.baseline_source == BaselineSource.pinned

  def test_leaving_the_zone_with_cruise_off_still_re_arms_it(self):
    """The drop to 0 must keep being tracked while disengaged, or a pin fires once per boot."""
    icbm = fresh()
    in_zone(icbm, 45, 200, enabled=False)
    in_zone(icbm, 0, 100, enabled=False)           # drives out of range, cruise still off
    in_zone(icbm, 45, 300, enabled=True)           # comes back and engages
    assert icbm.v_baseline == 45, "the pin did not re-arm after leaving the radius"

  def test_the_pin_that_applies_is_the_one_you_engaged_in(self):
    """Two zones with different numbers, both passed through with cruise off."""
    icbm = fresh()
    in_zone(icbm, 45, 100, enabled=False)
    in_zone(icbm, 0, 50, enabled=False)
    in_zone(icbm, 50, 100, enabled=False)
    in_zone(icbm, 50, 300, enabled=True)
    assert icbm.v_baseline == 50, f"applied the wrong zone's number: {icbm.v_baseline}"


class TestALiveHoldOutranksAPinnedOne:
  """Measured, route 0000033c t+333 on 2026-08-11, and confirmed by the owner the same day.

  He set 75 by hand at t+134, then drove into a zone with 70 pinned from an earlier drive, and the
  pin silently replaced his number. His words: "at some point, my hold dropped by 5 miles per hour,
  which was strange." Nothing he did caused it and nothing on screen said why.

  A pin records what he wanted on a previous drive; a hold he set minutes ago is what he wants now.
  """

  def test_a_pin_does_not_overwrite_a_hold_he_set_by_hand(self):
    icbm = fresh()
    # A real press creates the hold, exactly as it did on the road.
    icbm.run(make_cs(LIMIT, buttons=(ACCEL_PRESS,)), CC, make_lp(LIMIT), False)
    for cluster in range(LIMIT, DRIVER + 1):
      icbm.run(make_cs(cluster, buttons=(ACCEL_PRESS,)), CC, make_lp(LIMIT), False)
    icbm.run(make_cs(DRIVER, buttons=(ACCEL_RELEASE,)), CC, make_lp(LIMIT), False)
    assert icbm.v_baseline == DRIVER, "no hold to defend"
    assert icbm.baseline_source == BaselineSource.press

    in_zone(icbm, DRIVER - 5, 300, enabled=True, road_speed=DRIVER)
    assert icbm.v_baseline == DRIVER, (
      f"the pin replaced his live hold with {icbm.v_baseline} -- THE REPORTED BUG")
    assert icbm.baseline_source == BaselineSource.press, "the pin took ownership of his number"

  def test_a_pin_still_applies_when_there_is_no_hold(self):
    """The case the pin exists for. The guard must not cost it."""
    icbm = fresh()
    in_zone(icbm, 45, 300, enabled=True)
    assert icbm.v_baseline == 45, "the guard blocked a pin that had nothing to overwrite"
    assert icbm.baseline_source == BaselineSource.pinned

  def test_one_pin_can_still_supersede_another(self):
    """Two remembered numbers is not a preference being overwritten -- the later place wins."""
    icbm = fresh()
    in_zone(icbm, 45, 300, enabled=True)
    assert icbm.v_baseline == 45
    in_zone(icbm, 0, 50, enabled=True)      # leave the first zone, which re-arms
    # 65, not LIMIT: a hold that lands exactly on SLA's target is cleared by the divergence rule,
    # which would fail this test for a reason that has nothing to do with pins.
    in_zone(icbm, 65, 300, enabled=True)    # enter a different one
    assert icbm.v_baseline == 65, "a pinned hold blocked the next pin"
    assert icbm.baseline_source == BaselineSource.pinned


class TestAStandstillReEngageIsNotASet:
  """Measured, route 0000033c t+471-482 on 2026-08-11: "when I resumed, my hold went away."

    t+471  CRUISE OFF      set speed 62, 67 mph
    t+480  CRUISE ENGAGED  set speed 62,  2 mph
    t+482  HOLD CLEARED (was 75)

  The re-engage was decided by comparing the landed set speed against the one before the disengage:
  69 against 62, 7 apart with a tolerance of 2, so it was read as a SET and his hold was discarded.

  But Ford's SET jumps to the CURRENT VEHICLE SPEED, floored at the 20 mph minimum -- the comment on
  RESUME_MATCH_TOLERANCE says exactly this. At 2 mph a SET lands at 20, nowhere near 69. So there was
  positive evidence AGAINST a set, sitting unused, while "did not land on the previous set speed" was
  being treated as proof of one. Those are different claims.

  Landing on neither number is evidence of nothing. Discarding a hold is destructive and silent;
  keeping one he can always change is not. So ambiguity keeps it.
  """

  def _held_at(self, icbm, to):
    icbm.run(make_cs(LIMIT, buttons=(ACCEL_PRESS,)), CC, make_lp(LIMIT), False)
    for cluster in range(LIMIT, to + 1):
      icbm.run(make_cs(cluster, buttons=(ACCEL_PRESS,)), CC, make_lp(LIMIT), False)
    icbm.run(make_cs(to, buttons=(ACCEL_RELEASE,)), CC, make_lp(LIMIT), False)
    for _ in range(200):
      icbm.run(make_cs(to), CC, make_lp(LIMIT), False)
    return icbm

  def test_re_engaging_from_a_stop_keeps_the_hold(self):
    icbm = self._held_at(fresh(), DRIVER)
    assert icbm.v_baseline == DRIVER

    # A curve walks the set speed down to 62 -- BY ICBM'S OWN BUTTONS. Moving the cluster directly
    # instead re-baselines the hold on the first frame, because an unexplained set-speed move is
    # exactly what fallbackIdle exists to catch. The bug needs a set speed lowered by ICBM and a hold
    # that survived it, which is the state he was actually in.
    cluster = DRIVER
    for i in range(3000):
      if cluster <= 62:
        break
      icbm.run(make_cs(cluster, v_ego=cluster), CC, make_lp(45, source=PlanSource.sccVision), False)
      if i % 20 == 0 and icbm.cruise_button == SendButtonState.decrease:
        cluster -= 1
    assert cluster <= 62, f"ICBM never brought the set speed down, stalled at {cluster}"
    assert icbm.v_baseline == DRIVER, (
      f"the hold was re-baselined to {icbm.v_baseline} during the curve, before the resume was "
      f"reached -- the fixture is testing the wrong thing")
    for _ in range(50):
      icbm.run(make_cs(62, v_ego=62), CC, make_lp(45, source=PlanSource.sccVision), False)
    for _ in range(100):
      icbm.run(make_cs(62, v_ego=2, enabled=False), CC, make_lp(LIMIT), False)
    # Re-engages at a standstill and the set speed lands at 69 -- neither the pre-disengage 62 nor
    # anything a SET at 2 mph could produce.
    for _ in range(400):
      icbm.run(make_cs(69, v_ego=2), CC, make_lp(LIMIT), False)

    assert icbm.v_baseline == DRIVER, (
      f"hold went to {icbm.v_baseline} -- a standstill re-engage was read as the driver pressing "
      f"SET and handing the speed back to SLA. THE REPORTED BUG.")

  def test_a_real_set_still_clears_the_hold(self):
    """The counterweight. A SET lands on the vehicle speed, and that must still hand the speed back."""
    icbm = self._held_at(fresh(), DRIVER)
    assert icbm.v_baseline == DRIVER

    for _ in range(50):
      icbm.run(make_cs(DRIVER, v_ego=DRIVER), CC, make_lp(LIMIT), False)
    for _ in range(100):
      icbm.run(make_cs(DRIVER, v_ego=58, enabled=False), CC, make_lp(LIMIT), False)
    # Ford's SET jumps to the current vehicle speed: rolling at 58, the set speed lands on 58.
    for _ in range(400):
      icbm.run(make_cs(58, v_ego=58), CC, make_lp(LIMIT), False)

    assert icbm.v_baseline == 0, (
      f"hold survived at {icbm.v_baseline}; a SET must still hand the speed back to SLA")


class TestNobodyAskingDoesNotStrandTheHold:
  """Route 00000348 t+838-876, 2026-08-11: "it got stuck at 38, even though my hold was set to 50.
  The hold never resumed until I canceled and resumed."

  Measured: SCC-Vision inactive at 570 mph (V_CRUISE_UNSET), SCC-Map the same, Speed Limit Assist the
  same because the road had no limit data, and the published plan target 570. Every candidate unset at
  once. ICBM rejects that as unreal -- correctly -- and then held the CURRENT set speed, so 38 froze
  for 40 seconds through a full stop and the restart.

  The plan source read sccVision the whole time, which is a trap worth keeping in the test: min() over
  equally-unset candidates still names one, so the label pointed at a controller asking for nothing.

  Root cause is fixed in longitudinal_planner. This pins the second line of defense, which is also the
  right default on its own: when nothing is asking, aim at the driver's number.
  """

  def test_an_unset_target_falls_back_to_the_hold(self):
    icbm = fresh()
    icbm.run(make_cs(LIMIT, buttons=(ACCEL_PRESS,)), CC, make_lp(LIMIT), False)
    for cluster in range(LIMIT, DRIVER + 1):
      icbm.run(make_cs(cluster, buttons=(ACCEL_PRESS,)), CC, make_lp(LIMIT), False)
    icbm.run(make_cs(DRIVER, buttons=(ACCEL_RELEASE,)), CC, make_lp(LIMIT), False)
    for _ in range(200):
      icbm.run(make_cs(DRIVER), CC, make_lp(LIMIT), False)
    assert icbm.v_baseline == DRIVER

    # A curve walks the set speed down to 38 BY ICBM'S OWN BUTTONS. Moving the cluster there directly
    # re-baselines the hold to 38 on the first frame -- an unexplained set-speed move is what
    # fallbackIdle exists to catch -- and the test would then pass for the wrong reason.
    cluster = DRIVER
    for i in range(4000):
      if cluster <= 38:
        break
      icbm.run(make_cs(cluster, v_ego=cluster), CC, make_lp(35, source=PlanSource.sccVision), False)
      if i % 20 == 0 and icbm.cruise_button == SendButtonState.decrease:
        cluster -= 1
    assert cluster <= 38, f"ICBM never lowered the set speed, stalled at {cluster}"
    assert icbm.v_baseline == DRIVER, f"hold was re-baselined to {icbm.v_baseline} before the test"

    # Now everything goes quiet at once, exactly as logged: every candidate unset.
    unset = V_CRUISE_MAX * CV.KPH_TO_MS
    for _ in range(300):
      icbm.run(make_cs(cluster, v_ego=cluster),
               CC, make_lp(unset * CV.MS_TO_MPH, source=PlanSource.sccVision), False)

    assert icbm.v_target == DRIVER, (
      f"target fell back to {icbm.v_target} instead of the hold -- the set speed is stranded at the "
      f"cluster with no way back. THE REPORTED BUG.")

  def test_with_no_hold_it_still_holds_the_cluster(self):
    """The original behavior, which is right when there is no driver number to aim at."""
    icbm = fresh()
    unset = V_CRUISE_MAX * CV.KPH_TO_MS
    for _ in range(50):
      icbm.run(make_cs(38, v_ego=38), CC, make_lp(unset * CV.MS_TO_MPH), False)
    assert icbm.v_baseline == 0
    assert icbm.v_target == 38, f"invented a target of {icbm.v_target} with no hold to aim at"


class TestComingOutOfACurveBehindALeadIsStillMetered:
  """Route 00000348 t+1060, 2026-08-11, with a lead at 31-38 m:

    t+1058  36 mph  dash 34  sccVision   latAcc 1.91   (the bend peaked at 2.32 a second later)
    t+1060  34 mph  dash 33  cruise      latAcc 0.99   <- vision releases, still cornering
    t+1064  40 mph  dash 50

  17 mph of set speed in four seconds, and the car pulled about 1.4 m/s^2 coming out of the bend.
  "It slowed down to 30 but then hit the gas way too fast while I was still in the curve."

  The lead bypass is the owner's rule and stays. Its one hole is this moment: the curve ceiling is
  scoped to SCC-Vision being ACTIVE, so it lets go the instant vision does, and vision lets go while
  the car is still in the corner. With a lead present nothing else was metering the recovery.
  """

  def test_the_jump_out_of_a_bend_is_metered(self):
    icbm = fresh(max_rise=5)
    for _ in range(300):
      icbm.run(make_cs(33, v_ego=33), CC,
               make_lp(30, source=PlanSource.sccVision, curve_active=True, curve_target=30),
               False, True)
    # Vision lets go. The planner asks for the full number again and the lead is still there.
    icbm.run(make_cs(33, v_ego=33), CC, make_lp(DRIVER, source=PlanSource.cruise), False, True)
    assert icbm.v_target <= 33 + 5, (
      f"set speed jumped straight to {icbm.v_target} coming out of a bend behind a lead -- "
      f"THE REPORTED BUG")

  def test_it_still_completes_rather_than_sticking_low(self):
    """Metering must not reintroduce the stuck-behind-traffic problem the bypass was added for.
    RISE_STEP_STALL_FRAMES advances a step that actual speed cannot consume."""
    icbm = fresh(max_rise=5)
    for _ in range(300):
      icbm.run(make_cs(33, v_ego=33), CC,
               make_lp(30, source=PlanSource.sccVision, curve_active=True, curve_target=30),
               False, True)
    cluster = 33
    for i in range(4000):
      icbm.run(make_cs(cluster, v_ego=33), CC, make_lp(DRIVER, source=PlanSource.cruise), False, True)
      if i % 20 == 0 and icbm.cruise_button == SendButtonState.increase:
        cluster += 1
      if cluster >= DRIVER:
        break
    assert cluster >= DRIVER, f"stuck at {cluster} behind a lead -- metering must still complete"

  def test_an_ordinary_rise_behind_a_lead_is_untouched(self):
    """Nowhere near a bend, the owner's rule applies exactly as before."""
    icbm = fresh(max_rise=5)
    icbm.run(make_cs(50, v_ego=45), CC, make_lp(DRIVER), False, True)
    assert icbm.v_target == DRIVER, "metered a rise that had nothing to do with a curve"


class TestSmallCorrectionsAreTappedNotHeld:
  """This car moves the set speed 1 mph for a TAP and 5 mph for a HELD button.

  ICBM asserts the button continuously until the cluster crosses the target, which is a hold. So a
  1 mph correction requests 5, overshoots, and requests 5 back the other way -- measured on route
  00000361 at t+2704 as eighteen reversals in twenty seconds around a target that never moved.

  NOTE ON WHAT THIS CAN AND CANNOT PROVE. The Drive harness moves the cluster 1 mph per emitted
  button frame, so it models tapping and cannot reproduce a held-button overshoot at all -- which is
  exactly why no existing test caught the oscillation. So this asserts the DUTY CYCLE rather than the
  resulting speed: within the band the button must not be asserted on every frame. Whether the gap is
  long enough for the car to read a release rather than a repeat can only be confirmed on the road.
  """

  # The first frames are preActive and command nothing, so they are skipped. Counting them made the
  # pulse test pass with tapping DISABLED -- the startup ramp alone put it under the total, which is
  # passing for the wrong reason. Only the steady state says whether the button is held or pulsed.
  SETTLE = 60

  @staticmethod
  def _frames_asserted(icbm, cluster, target, n=240, skip=60):
    asserted = 0
    for i in range(n):
      icbm.run(make_cs(cluster, v_ego=cluster), CC, make_lp(target), False)
      if i >= skip and icbm.cruise_button != SendButtonState.none:
        asserted += 1
    return asserted

  def test_a_small_correction_pulses(self):
    icbm = fresh()
    asserted = self._frames_asserted(icbm, cluster=26, target=27)
    steady = 240 - self.SETTLE
    assert 0 < asserted < steady, (
      f"button asserted on {asserted}/{steady} steady-state frames for a 1 mph correction -- a "
      f"continuous assert is a HOLD, which moves this car 5 mph and overshoots. THE REPORTED BUG.")

  def test_a_large_correction_still_holds(self):
    """Holding is right when there is real distance to cover -- 3.3 mph/s is already the constraint
    on every exit, and pulsing a big descent would make that worse.

    Not asserted on EVERY frame, because the state machine spends its first frames in preActive
    before it commands anything. Compared against the small-correction case instead, which is the
    distinction that matters and cannot pass by accident.
    """
    small = self._frames_asserted(fresh(), cluster=26, target=27)
    large = self._frames_asserted(fresh(), cluster=70, target=40)
    assert large > small * 2, (
      f"a 30 mph correction asserted the button on {large}/240 frames and a 1 mph one on {small} -- "
      f"large moves must stay held or exits get slower still")
