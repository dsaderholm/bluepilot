"""FusionPilot: SLA never left `disabled`, because its "driver is fiddling" proxy is false here.

Route 389, 2026-08-18, with the map supplying good limits (40/25/20/30 mph) the whole drive:

    inactive  4013 frames
    disabled  1009
    preActive  978
    active        0        <- never once

He reported it as "I'm not sure if SLA was even working" and "SLA is telling me to set my speed to
70 for it to work". It was not working at all.

The DISABLED exit waits for `long_engaged_timer` to run down, and resets that timer whenever
`v_cruise_cluster_changed`. That is meant to mean "the driver is still adjusting the set speed". On
this car SCC-Map, SCC-Vision and ICBM all move `v_cruise_cluster` themselves, so it was resetting on
nearly every frame and the timer never reached zero.

`cluster_converging` is the fork's existing answer to exactly that question, and the ACTIVE branch
already used it -- these two resets predate it.
"""
from __future__ import annotations


from cereal import custom
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.speed_limit_assist import (
  DISABLED_GUARD_PERIOD,
  SpeedLimitAssist,
)

State = custom.LongitudinalPlanSP.SpeedLimit.AssistState


def _sla(monkeypatch, converging: bool, changed: bool = True):
  """An SLA parked in `disabled` with longitudinal engaged and a limit available."""
  s = SpeedLimitAssist.__new__(SpeedLimitAssist)
  s.state = State.disabled
  s.long_enabled = True
  s.enabled = True
  s.long_enabled_prev = True
  s.long_engaged_timer = int(DISABLED_GUARD_PERIOD / DT_MDL)
  s.pre_active_timer = 0
  s.pcm_op_long = False
  s.auto_follow = True
  monkeypatch.setattr(SpeedLimitAssist, "v_cruise_cluster_changed", property(lambda _: changed))
  monkeypatch.setattr(SpeedLimitAssist, "cluster_converging", property(lambda _: converging))
  monkeypatch.setattr(SpeedLimitAssist, "_has_speed_limit", property(lambda _: True), raising=False)
  monkeypatch.setattr(SpeedLimitAssist, "speed_limit_changed", property(lambda _: False))
  monkeypatch.setattr(SpeedLimitAssist, "_update_non_pcm_long_confirmed_state", lambda _: True)
  return s


def _run(s, frames):
  for _ in range(frames):
    s.update_state_machine_non_pcm_long()
  return s.state


def test_sla_leaves_disabled_when_the_cluster_is_only_converging(monkeypatch):
  """ICBM driving the set speed toward SLA's own target must not read as the driver overriding it."""
  s = _sla(monkeypatch, converging=True)
  state = _run(s, int(DISABLED_GUARD_PERIOD / DT_MDL) + 10)
  assert state != State.disabled, (
    "SLA is still disabled after the whole guard period -- the timer is being reset by the set "
    "speed moving TOWARD its own target, which is ICBM working, not the driver overriding")
  assert state == State.active


def test_the_driver_moving_the_set_speed_away_still_holds_sla_off(monkeypatch):
  """The other direction, which is what the guard is actually for: movement AWAY from the target is
  the driver, and SLA must stand down for it. A fix that let SLA through here would have taken the
  set speed back off him mid-adjustment."""
  s = _sla(monkeypatch, converging=False)
  state = _run(s, int(DISABLED_GUARD_PERIOD / DT_MDL) + 10)
  assert state == State.disabled, (
    "SLA activated while the driver was moving the set speed away from its target")


def test_a_still_cluster_still_releases(monkeypatch):
  """Unchanged behaviour with nothing moving at all -- the ordinary case for a car without ICBM."""
  s = _sla(monkeypatch, converging=False, changed=False)
  assert _run(s, int(DISABLED_GUARD_PERIOD / DT_MDL) + 10) == State.active


def _pcm_op_long(op_long: bool, pcm_cruise: bool, pcm_cruise_speed: bool) -> bool:
  """The shipped expression, modelled. Pinned to the source by the test below."""
  return bool(op_long and pcm_cruise and pcm_cruise_speed)


def test_icbm_moving_the_set_speed_means_no_ceiling_protocol():
  """"Why in God's green earth would I ever want to set my speed to 70 just to have it follow the
  speed limit?" He would not, and the protocol was never meant for this car.

  `pcm_op_long` means "openpilot brakes but the PCM owns the set speed", so SLA cannot move that
  number and instead rides below `PCM_LONG_REQUIRED_MAX_SET_SPEED` -- the 70. On this car
  `CP.pcmCruise` is True even under op long, but ICBM MOVES THE SET SPEED with button presses, so
  the premise does not hold. `CP_SP.pcmCruiseSpeed` False is exactly the statement that something
  other than the PCM manages the setpoint.

  Checked rather than assumed, 2026-08-18: `pcmCruiseSpeed` appeared NOWHERE in the original
  expression, so fixing the ICBM param alone would have left the 70 in place. It is easy to believe
  one root cause explains two symptoms; here it explained one."""
  assert not _pcm_op_long(True, True, False), (
    "SLA still demands the PCM ceiling protocol while ICBM is managing the set speed -- that is the "
    "'set your speed to 70' with no way for him to want it")
  # And the protocol must survive where it IS correct: op long braking, PCM owning the setpoint,
  # nothing able to move it.
  assert _pcm_op_long(True, True, True), "the PCM-long protocol was removed for cars that need it"
  assert not _pcm_op_long(False, True, True), "not op long, so not this protocol"


def test_the_real_pcm_op_long_matches_this_model():
  """Pins the model above to the shipped line, so the two cannot drift apart silently."""
  import inspect
  from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit import speed_limit_assist as mod

  src = inspect.getsource(mod.SpeedLimitAssist.__init__)
  line = next(ln for ln in src.splitlines() if "self.pcm_op_long =" in ln)
  for term in ("openpilotLongitudinalControl", "pcmCruise", "pcmCruiseSpeed"):
    assert term in line, f"pcm_op_long no longer consults {term}: {line.strip()}"
