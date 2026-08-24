"""FusionPilot: overriding on the throttle is not the driver taking the set speed back.

Route 000003b7, 2026-08-24. Across 20 gas-override episodes SLA left and re-entered `active` **339
times**, with ZERO driver button events in them:

    t+359.8   32.4 s of gas   96 flips   0 buttons   vCruiseCluster 75 -> 85, dash steady at 75
    t+392.5   23.0 s of gas   92 flips   0 buttons   vCruiseCluster 80 -> 85

`_update_v_cruise` floors the set speed at `max(v_cruise_kph, vEgo)`, so pulling away on the pedal
walks `v_cruise_cluster` up to whatever speed the car reaches. The ACTIVE branch reads any such
movement as a takeover and drops to `inactive`; the moment the value settles it re-enters, and every
re-entry calls `update_active_event` -- "Set speed changed", with a chime, and the set-speed number
flips between green and white because the colour is chosen by `assist.active`.

Reported as: *"it made the noise and said changing set speed when I overrode cruise with the gas"*.

`cluster_converging` cannot cover this case. The movement is AWAY from the target, which is exactly
what that property exists to call a takeover.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from cereal import custom
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.speed_limit_assist import SpeedLimitAssist

State = custom.LongitudinalPlanSP.SpeedLimit.AssistState


def _sla(monkeypatch, gas: bool, changed: bool = True, converging: bool = False):
  """An SLA sitting in `active`, with the set speed moving for a reason that is not a button."""
  s = SpeedLimitAssist.__new__(SpeedLimitAssist)
  s.state = State.active
  s.long_enabled = True
  s.enabled = True
  s.long_enabled_prev = True
  s.long_engaged_timer = 0
  s.pre_active_timer = 0
  s.pcm_op_long = False
  s.auto_follow = True
  s._gas_pressed = gas
  monkeypatch.setattr(SpeedLimitAssist, "v_cruise_cluster_changed", property(lambda _: changed))
  monkeypatch.setattr(SpeedLimitAssist, "cluster_converging", property(lambda _: converging))
  monkeypatch.setattr(SpeedLimitAssist, "_has_speed_limit", property(lambda _: True), raising=False)
  monkeypatch.setattr(SpeedLimitAssist, "speed_limit_changed", property(lambda _: False))
  monkeypatch.setattr(SpeedLimitAssist, "apply_confirm_speed_threshold", property(lambda _: False))
  monkeypatch.setattr(SpeedLimitAssist, "_update_non_pcm_long_confirmed_state", lambda _: True)
  return s


def _run(s, frames):
  """Run the machine and return (final state, number of times it LEFT `active`).

  The count is the point. An earlier version of this file returned only the final state, and every
  mutation of the guard still passed: the machine leaves `active` and is put straight back by the
  INACTIVE branch on the next frame, so after any number of frames it ends on `active` either way.
  That is exactly the defect being fixed -- 1238 round trips in 723 s, each one an announcement and
  a colour flip -- and a test that reads the endpoint cannot see it at all.
  """
  exits = 0
  prev = s.state
  for _ in range(frames):
    s.update_state_machine_non_pcm_long()
    if prev == State.active and s.state != State.active:
      exits += 1
    prev = s.state
  return s.state, exits


def test_the_set_speed_walking_up_under_gas_does_not_stand_sla_down(monkeypatch):
  """THE REPORTED BUG. 32 s of throttle must not produce 96 exits from active."""
  s = _sla(monkeypatch, gas=True)
  state, exits = _run(s, 300)
  assert exits == 0, f"SLA left `active` {exits} times on the gas; each exit re-announces and flashes"
  assert state == State.active


def test_a_real_takeover_off_the_gas_still_stands_sla_down(monkeypatch):
  """The guard this is built on. A set-speed change with no pedal is still the driver."""
  s = _sla(monkeypatch, gas=False)
  _, exits = _run(s, 5)
  assert exits >= 1, "a set-speed change off the gas must still stand SLA down"


def test_a_still_set_speed_under_gas_is_untouched(monkeypatch):
  """No movement, no exit -- with or without the pedal."""
  s = _sla(monkeypatch, gas=True, changed=False)
  state, exits = _run(s, 100)
  assert (state, exits) == (State.active, 0)


def test_converging_under_gas_is_still_active(monkeypatch):
  """The pre-existing ICBM-convergence exemption must survive the new term."""
  s = _sla(monkeypatch, gas=True, converging=True)
  state, exits = _run(s, 100)
  assert (state, exits) == (State.active, 0)


def test_the_guard_is_not_a_blanket_pass(monkeypatch):
  """Releasing the pedal mid-episode hands the takeover check straight back."""
  s = _sla(monkeypatch, gas=True)
  assert _run(s, 100) == (State.active, 0)
  s._gas_pressed = False
  _, exits = _run(s, 5)
  assert exits >= 1, "letting off the gas must restore the takeover check"


def test_converging_without_gas_is_still_exempt(monkeypatch):
  """The pre-existing ICBM exemption, tested with the pedal UP so the new term cannot cover for it.

  Every other converging case here also has gas on, so deleting `not self.cluster_converging`
  passed them all. That would have silently handed the 2026-08-18 "SLA never leaves disabled" bug
  back on the ACTIVE side.
  """
  s = _sla(monkeypatch, gas=False, converging=True)
  state, exits = _run(s, 100)
  assert (state, exits) == (State.active, 0), "ICBM converging on the target read as a takeover"


def test_the_flag_actually_comes_from_car_state():
  """The wiring, not the branch. Nothing else here calls `update_car_state`, so removing the read
  entirely left every test passing against a flag only the fixtures ever set."""
  s = SpeedLimitAssist.__new__(SpeedLimitAssist)
  s._last_carstate_ts = 0.0
  s._plus_hold = 0.0
  s._minus_hold = 0.0

  s.update_car_state(NS(gasPressed=True, buttonEvents=[]))
  assert s._gas_pressed is True, "gasPressed on the CarState never reached the state machine"

  s.update_car_state(NS(gasPressed=False, buttonEvents=[]))
  assert s._gas_pressed is False, "the flag latched on and never cleared"

  # The `__init__` default is deliberately NOT tested. `update_car_state` runs every frame ahead of
  # the state machine, so a wrong default would survive exactly one frame -- and a test for it here
  # would have to skip `__init__` (which needs a real CP) and would then assert nothing at all. An
  # earlier version of this file did exactly that and passed unconditionally.
