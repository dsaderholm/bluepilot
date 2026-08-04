"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

BluePilot: one simulated drive, exercising the whole ICBM feature set together.

Every other ICBM test is a unit test. Both defects that reached the road were INTERACTIONS --
the re-arm rule meeting a continuously varying target, and the baseline meeting the cluster's lag
behind the button. Neither was visible in the code that contained it, and neither would have been
caught by testing the pieces separately.

So this drives one continuous scenario with the real rate limiters on and the cluster moved only
the way a real car moves it: by the driver pressing, or by ICBM asking. Nothing is teleported.
"""

from types import SimpleNamespace as NS

from cereal import car, custom
from openpilot.sunnypilot.selfdrive.car.intelligent_cruise_button_management.controller import (
  IntelligentCruiseButtonManagement, DEFAULT_BASELINE_RESET_DELTA,
  DEFAULT_MAX_TARGET_DROP, DEFAULT_MAX_TARGET_RISE,
)

OverrideState = custom.IntelligentCruiseButtonManagement.OverrideState
SendButtonState = custom.IntelligentCruiseButtonManagement.SendButtonState
UnconfirmedLeadState = custom.LongitudinalPlanSP.UnconfirmedLead.State
PlanSource = custom.LongitudinalPlanSP.LongitudinalPlanSource
ButtonType = car.CarState.ButtonEvent.Type

MPH = 0.44704
CC = NS(enabled=True, cruiseControl=NS(resume=False, override=False, cancel=False))
PRESS = NS(type=NS(raw=ButtonType.accelCruise), pressed=True)
RELEASE = NS(type=NS(raw=ButtonType.accelCruise), pressed=False)

CLUSTER_LAG = 6   # frames the set speed trails a button press, as on the real car
SEND_PERIOD = 5   # ICBM emits at most one button frame per 0.05 s (opendbc ford/icbm.py)


class Drive:
  """Holds the car state and lets ICBM actually move the set speed."""

  def __init__(self):
    self.icbm = IntelligentCruiseButtonManagement(NS(), NS(pcmCruiseSpeed=False))
    for k in self.icbm.cruise_button_timers:
      self.icbm.cruise_button_timers[k] = 0
    self.icbm.update_params = lambda: None
    self.icbm.max_target_drop = DEFAULT_MAX_TARGET_DROP
    self.icbm.max_target_rise = DEFAULT_MAX_TARGET_RISE
    self.icbm.baseline_reset_delta = DEFAULT_BASELINE_RESET_DELTA
    self.cluster = 55
    self.v_ego = 55
    self._pending = []
    self._frame = 0

  def step(self, target, source=PlanSource.speedLimitAssist, buttons=(),
           lead=UnconfirmedLeadState.inactive, lead_target=0.0):
    # BluePilot: gasPressed/brakePressed exist on every real CarState. Omitting them here meant an
    # AttributeError the device could never raise, the moment the controller read one.
    cs = NS(vEgo=self.v_ego * MPH,
            gasPressed=False,
            brakePressed=False,
            cruiseState=NS(available=True, enabled=True, speedCluster=self.cluster * MPH,
                           standstill=False, speed=self.cluster * MPH),
            buttonEvents=buttons)
    lp = NS(vTarget=target * MPH, longitudinalPlanSource=source,
            unconfirmedLead=NS(state=lead, vTarget=lead_target * MPH))
    self.icbm.run(cs, CC, lp, False)

    # ICBM holds the state for many frames but only puts a frame on the wire every SEND_PERIOD,
    # then the cluster responds CLUSTER_LAG later. Modeling the throttle matters: applying a
    # step every frame would move the set speed 5x faster relative to the dead time and turn a
    # 1-2 mph settle into a large oscillation that the car does not actually have.
    step = 0
    if self._frame % SEND_PERIOD == 0:
      if self.icbm.cruise_button == SendButtonState.decrease:
        step = -1
      elif self.icbm.cruise_button == SendButtonState.increase:
        step = +1
    self._frame += 1
    self._pending.append(step)
    if len(self._pending) > CLUSTER_LAG:
      self.cluster += self._pending.pop(0)
    # the car chases its set speed
    self.v_ego += max(-1, min(1, self.cluster - self.v_ego)) * 0.25

  def cruise(self, n, **kw):
    for _ in range(n):
      self.step(**kw)

  def tap_plus(self, times, target, source=PlanSource.speedLimitAssist):
    """A real tap: press edge, release 3 frames later, cluster moves CLUSTER_LAG after."""
    for _ in range(times):
      self.step(target, source, buttons=(PRESS,))
      self.cluster += 1                      # the driver's own press reaches the cluster
      for f in range(20):
        self.step(target, source, buttons=(RELEASE,) if f == 2 else ())


def test_one_drive_end_to_end():
  d = Drive()
  icbm = d.icbm
  LIMIT = 55

  # --- 1. cruising at the limit, ICBM agrees, sends nothing -------------------------------
  d.cruise(200, target=LIMIT)
  assert icbm.override_state == OverrideState.auto
  assert d.cluster == LIMIT, f"ICBM moved the set speed unprompted to {d.cluster}"

  # --- 2. driver taps + three times: 55 -> 58 ---------------------------------------------
  d.tap_plus(3, LIMIT)
  assert d.cluster == 58, f"taps ended at {d.cluster}, not 58"
  assert icbm.override_state == OverrideState.manual
  assert icbm.v_baseline == 58, f"baseline is {icbm.v_baseline}, not the driver's 58"

  # --- 3. keep driving: the driver's number must stand ------------------------------------
  d.cruise(600, target=LIMIT)
  assert d.cluster == 58, f"dragged back to {d.cluster} -- THE REPORTED BUG"
  assert icbm.override_state == OverrideState.manual

  # --- 4. a curve: SCC-Vision wants 40. It must still slow us -----------------------------
  d.cruise(1500, target=40, source=PlanSource.sccVision)
  # +/-1: ICBM commands in 1 mph steps against a lagged cluster, so it hunts by one.
  assert abs(d.cluster - 40) <= 1, f"curve slowing stalled at {d.cluster}"
  assert icbm.v_baseline == 58, "curve decel was mistaken for a driver press"
  assert icbm.override_state == OverrideState.manual

  # --- 5. curve ends: return to the DRIVER's speed, not the limit -------------------------
  d.cruise(2500, target=LIMIT)
  assert abs(d.cluster - 58) <= 1, f"recovered to {d.cluster}, not the driver's 58"

  # --- 6. new zone, 55 -> 35: a major change discards the baseline -------------------------
  d.cruise(3000, target=35)
  assert icbm.override_state == OverrideState.auto, "baseline survived a 20 mph limit drop"
  assert icbm.v_baseline == 0
  assert abs(d.cluster - 35) <= 1, f"did not follow the new limit, sat at {d.cluster}"

  # --- 7. a stopped car the radar never confirmed -----------------------------------------
  d.cruise(400, target=35, lead=UnconfirmedLeadState.active, lead_target=20.0)
  assert abs(d.cluster - 20) <= 1, f"hazard decel reached only {d.cluster}"

  # --- 8. hazard clears, set speed restores ------------------------------------------------
  d.cruise(3000, target=35)
  assert abs(d.cluster - 35) <= 1, f"never restored, sat at {d.cluster}"
  assert icbm.override_state == OverrideState.auto


def test_taps_are_never_undone_at_any_lag():
  """The reported bug, swept across the lag window, on a full drive rather than in isolation."""
  global CLUSTER_LAG
  original = CLUSTER_LAG
  try:
    for lag in (1, 3, 6, 12, 20):
      CLUSTER_LAG = lag
      d = Drive()
      d.cruise(200, target=55)
      d.tap_plus(2, 55)
      d.cruise(800, target=55)
      assert d.cluster == 57, f"lag {lag}: ended at {d.cluster}, driver asked for 57"
      assert d.icbm.v_baseline == 57, f"lag {lag}: baseline {d.icbm.v_baseline}"
  finally:
    CLUSTER_LAG = original
