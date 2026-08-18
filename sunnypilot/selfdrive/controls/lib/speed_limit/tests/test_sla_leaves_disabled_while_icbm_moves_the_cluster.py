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
