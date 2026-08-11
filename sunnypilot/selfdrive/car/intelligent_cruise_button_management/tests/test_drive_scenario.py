"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FusionPilot: one simulated drive, exercising the whole ICBM feature set together.

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
           lead=UnconfirmedLeadState.inactive, lead_target=0.0, map_active=None):
    # FusionPilot: gasPressed/brakePressed exist on every real CarState. Omitting them here meant an
    # AttributeError the device could never raise, the moment the controller read one.
    cs = NS(vEgo=self.v_ego * MPH,
            gasPressed=False,
            brakePressed=False,
            cruiseState=NS(available=True, enabled=True, speedCluster=self.cluster * MPH,
                           standstill=False, speed=self.cluster * MPH),
            buttonEvents=buttons)
    # smartCruiseControl.map.active is what exempts SCC-Map from the drop limiter, and it is NOT
    # the same thing as being the plan source. On the real exit the source alternated
    # sccVision/sccMap/sccVision on consecutive frames while the map column stayed starred --
    # map.active was true on the vision frames too. So it is a separate knob here, defaulting to
    # the common case and overridable for the alternation that exposed the difference.
    map_on = (source == PlanSource.sccMap) if map_active is None else map_active
    lp = NS(vTarget=target * MPH, longitudinalPlanSource=source,
            smartCruiseControl=NS(map=NS(active=map_on, vTarget=target * MPH),
                                  vision=NS(active=source == PlanSource.sccVision,
                                            vTarget=target * MPH)),
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


def test_freeway_exit_is_not_metered_into_plateaus():
  """The 2026-08-07 report: "still does not slow anywhere close to enough on freeway exits".

  From the log, SCC-Map asked for 39 mph at 67 and ICBM commanded 68, then 56, then sat at 56 for
  6.5 s waiting for the car to reach it before asking for 44. Twelve seconds to work 80 down to 30,
  and the ramp was gone.

  SmartCruiseControlMapDecel is a TRIGGER DISTANCE, not a rate -- SCC-Map publishes the corner speed
  at exactly the moment the deceleration has to start. Every metered step spends road that was
  already budgeted, so the plateaus are the whole defect.
  """
  d = Drive()
  d.cluster = d.v_ego = 80

  # One frame is enough to see the difference: the limiter acts on the target, not over time.
  d.step(39, source=PlanSource.sccMap)
  assert d.icbm.v_target == 39, (
    f"SCC-Map's corner speed was metered down to {d.icbm.v_target}; it arrives with a deadline")

  # A SPEED LIMIT is what still gets metered. This half said sccVision until 2026-08-08, on the
  # belief that vision ramps its own target smoothly -- the exit log showed it asking 72, 52, 46,
  # 42 within two seconds, and the cap costing two and a half seconds of the approach. A curve has
  # a fixed place in the road; a speed limit does not, and coasting into a zone beats braking.
  d2 = Drive()
  d2.cluster = d2.v_ego = 80
  d2.step(39, source=PlanSource.speedLimitAssist)
  assert d2.icbm.v_target == 80 - DEFAULT_MAX_TARGET_DROP, (
    f"speed-limit target came through at {d2.icbm.v_target}; that one should still coast")

  # And end to end: the exit actually gets down to the corner speed.
  d.cruise(1200, target=39, source=PlanSource.sccMap)
  assert abs(d.cluster - 39) <= 1, f"exit slowing stalled at {d.cluster}"


def test_exit_exemption_survives_the_source_alternating():
  """The plan source is NOT a stable signal, and gating the exemption on it does not work.

  Found by reading the 2026-08-07 log rather than by any test. When the map and vision targets are
  close, longitudinalPlanSource alternates between them frame by frame -- sccVision/sccMap/
  sccVision on three consecutive frames at t+422 -- while smartCruiseControl.map stayed ACTIVE
  through all of them (the map column is starred on the vision frames too). So "is SCC-Map the
  source this frame" flickers while "is a mapped corner asking" does not.

  Keyed on the source, the bypass re-arms the limiter on every other frame and re-seeds its anchor
  from the current cluster, and the exemption never really applies. This drives that alternation
  with the map controller active throughout, exactly as logged.
  """
  d = Drive()
  d.cluster = d.v_ego = 80

  for i in range(600):
    src = PlanSource.sccMap if i % 2 else PlanSource.sccVision
    d.step(39, source=src, map_active=True)
    assert d.icbm.v_target == 39, (
      f"frame {i} ({src}): target came through as {d.icbm.v_target}, not the corner speed -- the "
      f"exemption is flickering with the plan source")

  assert abs(d.cluster - 39) <= 1, f"exit slowing stalled at {d.cluster} under an alternating source"


def test_a_curve_is_not_metered_but_a_speed_limit_still_is():
  """His call, 2026-08-08: "Only limit speed drops for speed limits lowering?"

  A curve is a fixed place in the road, so its target carries a deadline exactly as a mapped corner
  does. Measured on the exit that prompted this: vision asked 52 mph and the limiter held the set
  speed at 58 for two and a half seconds of the approach.

  A speed limit has no deadline, and coasting into a 35 zone beats braking into it -- so that one
  keeps the cap. This pins both halves, because the whole change is the line between them.
  """
  d = Drive()
  d.cluster = d.v_ego = 80
  d.step(39, source=PlanSource.sccVision)
  assert d.icbm.v_target == 80 - DEFAULT_MAX_TARGET_DROP, (
    f"curve target came through at {d.icbm.v_target}. Exempting vision was tried on 2026-08-08 and "
    f"reverted the same day: it produced 80 -> 50 mph on slight freeway curves, because the cap was "
    f"covering for a vision target the bend does not need.")

  d2 = Drive()
  d2.cluster = d2.v_ego = 80
  d2.step(39, source=PlanSource.speedLimitAssist)
  assert d2.icbm.v_target == 80 - DEFAULT_MAX_TARGET_DROP, (
    f"speed-limit target came through at {d2.icbm.v_target}; that one should still coast")

  d3 = Drive()
  d3.cluster = d3.v_ego = 80
  d3.step(39, source=PlanSource.cruise)
  assert d3.icbm.v_target == 80 - DEFAULT_MAX_TARGET_DROP, "plain cruise should still coast"


def test_the_set_speed_does_not_climb_while_a_curve_is_being_tracked():
  """Measured on the 2026-08-08 off-ramp, and it is why the ramp was taken at 28 instead of 20.

  SCC-Map lost the ramp at t+268.1. Vision's own target then bounced to 47-51 for a couple of
  seconds before settling at 21, ICBM chased the peak, and the set speed went 42 -> 51 -- so the car
  ACCELERATED from 41 to 44 mph mid-ramp and then had to walk all the way back down, reaching 20
  about three seconds later than it could have.

  A curve target that briefly rises is noise. The bend has ended when vision says so.
  """
  d = Drive()
  d.cluster = d.v_ego = 42
  peak = 0
  # Vision noise of the logged SHAPE and the logged DURATION. Duration is the point: the harness
  # moves the cluster one step per SEND_PERIOD against a CLUSTER_LAG delay, so a seven-frame burst
  # cannot move it at all and the test passes with or without the fix. The real spike ran from
  # t+268.1 to t+271.4 -- about 3.3 s, which is 330 frames, which is ample time to climb 9 mph.
  for t in [47, 48, 49, 51, 45, 37, 30]:
    for _ in range(48):
      d.step(t, source=PlanSource.sccVision)
      peak = max(peak, d.cluster)
  d.cruise(400, target=21, source=PlanSource.sccVision)

  # 45, not 43: the ceiling is 42 and the tolerance is 1, but presses already on the wire land after
  # the cluster reaches it, so CLUSTER_LAG buys a couple of mph of overshoot that no ceiling can
  # prevent. Without the block this reads 52, so the bound still discriminates.
  assert peak <= 45, f"set speed climbed to {peak} chasing curve-target noise; it started at 42"
  assert abs(d.cluster - 21) <= 1, f"never reached the curve speed, sat at {d.cluster}"


def test_a_curve_ending_still_lets_the_speed_come_back():
  """The block above must not strand the set speed low once the bend is actually over."""
  d = Drive()
  d.cluster = d.v_ego = 25
  d.cruise(600, target=60, source=PlanSource.speedLimitAssist)
  assert d.cluster >= 55, f"stuck at {d.cluster} after the curve ended"


def test_the_ceiling_does_not_ratchet_across_a_whole_drive_of_active_vision():
  """His report, 2026-08-10: after a slow curve the speed "stayed the speed that I overrode with my
  gas pedal" and the HOLD badge "stayed gray", and separately that he overrides with the pedal
  FREQUENTLY because curves go too slow.

  Both are one defect. The ceiling was scoped by `smartCruiseControl.vision.active`, and that is not
  a per-bend pulse -- on a highway it can stay true continuously. So the ceiling reset only when
  vision went quiet, which on a long drive is never, and it ratcheted downward monotonically: every
  dip anywhere permanently lowered the cap for everything after it. The badge stays grey the whole
  time for the same reason (hold_suppressed is true whenever the source is not cruise or
  speedLimitAssist), and apply_gas_handoff runs after this and bypasses it -- so the pedal was
  literally the only thing left that could raise the speed.

  Vision stays ACTIVE for the entire drive here. That is the condition the other curve tests do not
  create: theirs end the bend, which hides a ratchet that only shows up when it cannot reset.
  """
  d = Drive()
  d.cluster = d.v_ego = 70
  d.cruise(600, target=55, source=PlanSource.sccVision)
  assert d.cluster <= 58, f"the bend never brought the speed down, sat at {d.cluster}"

  # The road straightens. Vision is still active and still the source, but now asks for more than the
  # car is doing, which is vision saying there is no bend to cap.
  d.cruise(2000, target=75, source=PlanSource.sccVision)
  assert d.cluster >= 70, (
    f"stranded at {d.cluster} with vision active and asking 75 -- the ceiling ratcheted and never "
    f"released, which is why the gas pedal was the only way back up")


def test_a_sustained_recovery_is_required_before_the_ceiling_releases():
  """The release must not fire on the noise burst, which is itself a rise above the cluster.

  Same 3.3 s spike as the noise test, but held just under the release threshold and then taken back
  down, so a release here would prove the threshold is doing nothing.
  """
  d = Drive()
  d.cluster = d.v_ego = 42
  for _ in range(400):        # 4 s of vision asking for more, under the 5 s release
    d.step(60, source=PlanSource.sccVision)
  assert d.cluster <= 45, f"released early at {d.cluster}; the burst is noise, not a finished bend"
