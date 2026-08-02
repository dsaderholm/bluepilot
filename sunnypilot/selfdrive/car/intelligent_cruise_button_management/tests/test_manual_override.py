"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

BluePilot: behavioural tests for the ICBM AUTO/MANUAL override latch.

Written against a real defect. The original re-arm rule released MANUAL whenever the planner
target moved RE_ARM_TARGET_DELTA from the value that was rejected. On a road that is constant
noise -- curve slowing, speed limit changes, offset recalculation -- so a driver who set their
cruise above the limit got dragged back down within seconds, repeatedly. The tests that matter
here are the ones that hold the latch against a MOVING target, not a static one: a static target
passes either way, which is why the bug shipped.
"""

from types import SimpleNamespace as NS

from cereal import car, custom
from openpilot.sunnypilot.selfdrive.car.intelligent_cruise_button_management.controller import (
  IntelligentCruiseButtonManagement, RE_ARM_TARGET_DELTA,
)

OverrideState = custom.IntelligentCruiseButtonManagement.OverrideState
State = custom.IntelligentCruiseButtonManagement.IntelligentCruiseButtonManagementState
UnconfirmedLeadState = custom.LongitudinalPlanSP.UnconfirmedLead.State
ButtonType = car.CarState.ButtonEvent.Type

MPH = 0.44704
LIMIT = 55    # what SLA wants: speed limit + offset
DRIVER = 70   # what the driver wants instead

ACCEL_PRESS = NS(type=NS(raw=ButtonType.accelCruise), pressed=True)
ACCEL_RELEASE = NS(type=NS(raw=ButtonType.accelCruise), pressed=False)
GAP_PRESS = NS(type=NS(raw=ButtonType.gapAdjustCruise), pressed=True)


def make_cs(cluster, v_ego=None, buttons=(), enabled=True):
  return NS(vEgo=(cluster if v_ego is None else v_ego) * MPH,
            cruiseState=NS(available=True, enabled=enabled, speedCluster=cluster * MPH,
                           standstill=False, speed=cluster * MPH),
            buttonEvents=buttons)


def make_lp(target, lead_state=UnconfirmedLeadState.inactive, lead_target=0.0):
  return NS(vTarget=target * MPH,
            unconfirmedLead=NS(state=lead_state, vTarget=lead_target * MPH))


CC = NS(enabled=True, cruiseControl=NS(resume=False, override=False, cancel=False))


def fresh():
  """An ICBM settled in AUTO, agreeing with the driver at the speed limit.

  cruise_button_timers is bound to the module-level CRUISE_BUTTON_TIMER dict by reference rather
  than copied, so instances share it. Clear it per-instance or state leaks between tests.
  """
  icbm = IntelligentCruiseButtonManagement(NS(), NS(pcmCruiseSpeed=False))
  for k in icbm.cruise_button_timers:
    icbm.cruise_button_timers[k] = 0
  for _ in range(5):
    icbm.run(make_cs(LIMIT), CC, make_lp(LIMIT), False)
  return icbm


def override_up(icbm):
  """Driver presses + and walks the set speed from the limit up to DRIVER.

  Press AND release: update_manual_button_timers only clears a timer on a pressed=False event,
  so a press with no release pins is_ready low forever.
  """
  icbm.run(make_cs(LIMIT, buttons=(ACCEL_PRESS,)), CC, make_lp(LIMIT), False)
  for cluster in range(LIMIT, DRIVER + 1):
    icbm.run(make_cs(cluster), CC, make_lp(LIMIT), False)
  icbm.run(make_cs(DRIVER, buttons=(ACCEL_RELEASE,)), CC, make_lp(LIMIT), False)


def settle(icbm, target, cluster=DRIVER, frames=50):
  for _ in range(frames):
    icbm.run(make_cs(cluster), CC, make_lp(target), False)


class TestManualOverrideLatch:
  def test_driver_press_latches_manual(self):
    icbm = fresh()
    icbm.run(make_cs(LIMIT, buttons=(ACCEL_PRESS,)), CC, make_lp(LIMIT), False)
    assert icbm.override_state == OverrideState.manual

  def test_gap_button_does_not_latch(self):
    """gapAdjustCruise doesn't change the set speed, so it isn't an override."""
    icbm = fresh()
    icbm.run(make_cs(LIMIT, buttons=(GAP_PRESS,)), CC, make_lp(LIMIT), False)
    assert icbm.override_state == OverrideState.auto

  def test_latch_survives_a_static_target(self):
    icbm = fresh()
    override_up(icbm)
    settle(icbm, LIMIT, frames=300)
    assert icbm.override_state == OverrideState.manual
    assert icbm.cruise_button == custom.IntelligentCruiseButtonManagement.SendButtonState.none

  def test_latch_survives_a_moving_target(self):
    """THE REGRESSION. Any target movement used to release the latch."""
    icbm = fresh()
    override_up(icbm)
    for delta in (1, RE_ARM_TARGET_DELTA, RE_ARM_TARGET_DELTA + 3, -4):
      settle(icbm, LIMIT + delta)
      assert icbm.override_state == OverrideState.manual, f"released on a {delta} mph target move"

  def test_latch_survives_a_speed_limit_change(self):
    """55 -> 60 zone change while the driver is holding 70."""
    icbm = fresh()
    override_up(icbm)
    settle(icbm, 60)
    assert icbm.override_state == OverrideState.manual

  def test_rearm_when_target_catches_up_to_the_driver(self):
    """The disagreement ending is what ends MANUAL."""
    icbm = fresh()
    override_up(icbm)
    settle(icbm, LIMIT)
    assert icbm.override_state == OverrideState.manual
    settle(icbm, DRIVER)  # limit rises to meet the driver's chosen speed
    assert icbm.override_state == OverrideState.auto

  def test_rearm_on_cruise_cycle(self):
    icbm = fresh()
    override_up(icbm)
    assert icbm.override_state == OverrideState.manual
    icbm.run(make_cs(DRIVER, enabled=False), CC, make_lp(LIMIT), False)
    icbm.run(make_cs(DRIVER, enabled=True), CC, make_lp(LIMIT), False)
    assert icbm.override_state == OverrideState.auto

  def test_active_unconfirmed_lead_overrides_manual(self):
    """A hazard outranks the driver's earlier set-speed preference."""
    icbm = fresh()
    override_up(icbm)
    assert icbm.override_state == OverrideState.manual
    icbm.run(make_cs(DRIVER), CC,
             make_lp(LIMIT, UnconfirmedLeadState.active, 20.0), False)
    assert icbm.override_state == OverrideState.auto

  def test_restoring_unconfirmed_lead_does_not_override_manual(self):
    icbm = fresh()
    override_up(icbm)
    icbm.run(make_cs(DRIVER), CC,
             make_lp(LIMIT, UnconfirmedLeadState.restoring, 60.0), False)
    assert icbm.override_state == OverrideState.manual

  def test_state_machine_wakes_up_after_rearm(self):
    """inactive -> preActive needs a RISING is_ready edge.

    MANUAL used to advance is_ready_prev while masking the machine, so the edge was consumed
    during MANUAL and re-arming left ICBM stuck inactive until the driver pressed again.
    """
    icbm = fresh()
    override_up(icbm)
    settle(icbm, LIMIT)
    assert icbm.state == State.inactive
    settle(icbm, DRIVER, frames=5)                 # converge -> re-arm
    assert icbm.override_state == OverrideState.auto
    settle(icbm, LIMIT, frames=100)                # target drops again; ICBM should act
    assert icbm.state != State.inactive, "state machine never left inactive after re-arm"


class TestTargetRiseLimit:
  """BluePilot: the set speed must come back up in steps, not one continuous pull.

  ICBM holds CcAslButtnSetIncPress high for as long as the state machine sits in `increasing`,
  and Ford reads a held button as a continuous ramp. Reported from a drive as speeding back up
  way too fast after curves and speed-limit zones.
  """

  CURVE = 40   # what SCC-Vision wants through the bend
  CRUISE = 70  # what to return to afterwards

  def _icbm(self, max_rise):
    icbm = IntelligentCruiseButtonManagement(NS(), NS(pcmCruiseSpeed=False))
    for k in icbm.cruise_button_timers:
      icbm.cruise_button_timers[k] = 0
    icbm.max_target_rise = max_rise
    icbm.update_params = lambda: None  # pin it; params would overwrite from defaults
    return icbm

  def test_rise_is_capped_to_one_step(self):
    icbm = self._icbm(5)
    icbm.run(make_cs(self.CURVE), CC, make_lp(self.CURVE), False)
    icbm.run(make_cs(self.CURVE), CC, make_lp(self.CRUISE), False)  # curve ends, target jumps
    assert icbm.v_target == self.CURVE + 5, "asked for the whole 30 mph rise at once"

  def test_step_advances_only_once_speed_catches_up(self):
    icbm = self._icbm(5)
    icbm.run(make_cs(self.CURVE), CC, make_lp(self.CURVE), False)

    # curve ends: the rise starts here, so the anchor is captured at the current set speed
    icbm.run(make_cs(self.CURVE, v_ego=self.CURVE), CC, make_lp(self.CRUISE), False)
    assert icbm.v_target == self.CURVE + 5

    # set speed has reached the ceiling but the car has not accelerated to it yet -- hold
    icbm.run(make_cs(self.CURVE + 5, v_ego=self.CURVE), CC, make_lp(self.CRUISE), False)
    assert icbm.v_target == self.CURVE + 5, "advanced before the car caught up"

    # now the car is actually there -- next step is allowed
    icbm.run(make_cs(self.CURVE + 5, v_ego=self.CURVE + 5), CC, make_lp(self.CRUISE), False)
    assert icbm.v_target == self.CURVE + 10, "did not advance once speed caught up"

  def test_reaches_cruise_speed_eventually(self):
    icbm = self._icbm(5)
    cluster = self.CURVE
    for _ in range(400):
      icbm.run(make_cs(cluster, v_ego=cluster), CC, make_lp(self.CRUISE), False)
      if icbm.v_target > cluster:
        cluster += 1
    assert cluster == self.CRUISE, f"stalled at {cluster}, never reached {self.CRUISE}"

  def test_zero_disables_the_cap(self):
    icbm = self._icbm(0)
    icbm.run(make_cs(self.CURVE), CC, make_lp(self.CURVE), False)
    icbm.run(make_cs(self.CURVE), CC, make_lp(self.CRUISE), False)
    assert icbm.v_target == self.CRUISE

  def test_drops_are_untouched_by_the_rise_limiter(self):
    """The hazard path must not be metered. An ACTIVE lead only ever lowers the target."""
    icbm = self._icbm(5)
    icbm.max_target_drop = 0  # isolate: drop limiter off
    icbm.run(make_cs(self.CRUISE), CC, make_lp(self.CRUISE), False)
    icbm.run(make_cs(self.CRUISE), CC,
             make_lp(self.CRUISE, UnconfirmedLeadState.active, 20.0), False)
    assert icbm.v_target == 20, "rise limiter interfered with a hazard decel"
