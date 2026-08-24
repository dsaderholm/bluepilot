"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FusionPilot: the planner's target is allowed to shake. ICBM is not allowed to chase it.

Route 000003ae, 2026-08-23, measured from his own rlogs: `vTargetRaw` alternated between 27 and 30
mph 2.82 times a second for the whole inert window -- roughly 0.1 s high, 0.4 s low, over and over.
ICBM sent 168 increase frames and 210 decrease frames into that window with ZERO driver button
events in it. That is "it keeps telling me set speed changed and the max speed is flashing fast".

These tests drive the REAL controller with the REAL measured waveform. They are deliberately not a
unit test of `settle_target` alone: the value it returns is consumed by `v_target`, `v_target_raw`
and the override arming, and a filter that settled the local variable while the rest of the class
kept seeing the shake would pass a narrower test and fix nothing.
"""

from types import SimpleNamespace as NS

from cereal import custom
from openpilot.sunnypilot.selfdrive.car.intelligent_cruise_button_management.controller import (
  IntelligentCruiseButtonManagement, SETTLE_FRAMES, REVERSAL_MEMORY_FRAMES,
)

PlanSource = custom.LongitudinalPlanSP.LongitudinalPlanSource
UnconfirmedLeadState = custom.LongitudinalPlanSP.UnconfirmedLead.State
MPH = 0.44704


def _icbm():
  icbm = IntelligentCruiseButtonManagement(NS(), NS(pcmCruiseSpeed=False))
  icbm.update_params = lambda: None
  icbm.sla_assist_enabled = True
  return icbm


def _feed(icbm, mph_values, cluster=None):
  """Run update_calculations for each planner target, returning what ICBM adopted each frame.

  The LP_SP shape is copied wholesale from test_drive_scenario's harness rather than hand-rolled.
  That fixture carries three fields (`speedLimit.resolver`, `assist.enabled`, `smartCruiseControl`)
  that were each discovered missing the hard way, and a short fixture here would silently put the
  controller on a road with no posted limit and SLA switched off.
  """
  out = []
  for v in mph_values:
    c = v if cluster is None else cluster
    cs = NS(vEgo=c * MPH, gasPressed=False, brakePressed=False,
            cruiseState=NS(available=True, enabled=True, speedCluster=c * MPH,
                           standstill=False, speed=c * MPH),
            buttonEvents=[])
    lp = NS(vTarget=v * MPH, longitudinalPlanSource=PlanSource.speedLimitAssist,
            speedLimit=NS(resolver=NS(speedLimitValid=True, speedLimitLastValid=True),
                          assist=NS(enabled=True)),
            smartCruiseControl=NS(map=NS(active=False, vTarget=v * MPH),
                                  vision=NS(active=False, vTarget=v * MPH)),
            unconfirmedLead=NS(state=UnconfirmedLeadState.inactive, vTarget=0.0))
    icbm.update_calculations(cs, lp)
    # `v_target` is the settled number the button logic aims at, and the only one the filter
    # touches. `v_target_raw` is deliberately left as the planner published it -- asserting on it
    # here would assert that the filter does NOT run.
    #
    # Feed the cluster along with the target: `v_target` is also post-limiter, and a fixture that
    # pins the cluster while the target moves reports max_target_rise/max_target_drop clamping
    # rather than the settle.
    out.append(icbm.v_target)
  return out


# The measured waveform: 0.1 s at 30, 0.4 s at 27, repeated. 100 Hz.
def _ae_waveform(cycles=20):
  wave = []
  for _ in range(cycles):
    wave += [30.0] * 10
    wave += [27.0] * 40
  return wave


def _longest_plateau(seq):
  """Longest run of one value. A filter that re-pays its delay shows up as a long flat step."""
  best = run = 1
  for a, b in zip(seq, seq[1:], strict=False):
    run = run + 1 if a == b else 1
    best = max(best, run)
  return best


def test_the_measured_oscillation_settles_on_its_low_leg():
  """Route 000003ae's actual waveform must produce ONE target, not a square wave."""
  icbm = _icbm()
  adopted = _feed(icbm, _ae_waveform(), cluster=27)

  # Skip the first cycle: frame 0 has nothing adopted yet and legitimately takes 30.
  steady = adopted[50:]
  assert set(steady) == {27}, f"target still shaking: {sorted(set(steady))}"

  reversals = sum(1 for a, b in zip(steady, steady[1:], strict=False) if a != b)
  assert reversals == 0, f"{reversals} target reversals survived the filter"


def test_a_drop_is_adopted_on_the_very_first_frame():
  """The exit-ramp rule: aiming LOWER may never be delayed by even one frame."""
  icbm = _icbm()
  settled = _feed(icbm, [55.0] * (SETTLE_FRAMES * 3), cluster=55)   # settle high
  assert settled[-1] == 55

  # 55 -> 48, inside max_target_drop (12). A larger drop would be clamped by the drop limiter and
  # the test would be measuring that instead of the settle.
  adopted = _feed(icbm, [48.0], cluster=55)
  assert adopted[0] == 48, "a falling target was delayed -- this is the exit-ramp failure mode"


def test_a_whole_descent_tracks_frame_by_frame():
  """A curve descent must arrive exactly, not lag behind by a settle window."""
  icbm = _icbm()
  _feed(icbm, [63.0] * (SETTLE_FRAMES * 3))
  ramp = [63.0 - 0.07 * i for i in range(330)]   # 63 -> 40, off the .5 round-half-to-even ties
  adopted = _feed(icbm, ramp)
  assert adopted[-1] == round(ramp[-1]), f"descent stalled at {adopted[-1]}"
  # Comparing each frame against round(want) is a trap: a 0.07 step lands on exact .5 ties where
  # Python rounds half-to-even and the controller's m/s round trip does not. The invariant that
  # matters is that the descent never PAUSES -- a re-paid settle delay would show as a plateau at
  # least SETTLE_FRAMES long, and honest tracking holds each integer only as long as the ramp does.
  assert adopted == sorted(adopted, reverse=True), "the descent went back up"
  assert _longest_plateau(adopted) < SETTLE_FRAMES, "the descent stalled for a settle window"


def test_a_rise_to_a_new_level_is_never_delayed():
  """The owner's rule: behind a car the set speed may go anywhere, so a climb is not metered.

  An earlier version of this filter made EVERY step up wait out the settle window. It broke five
  tests in test_manual_override, including the two defending that rule. The filter is aimed at
  reversals; a target going somewhere it has not just been is adopted on the frame it arrives.
  """
  icbm = _icbm()
  _feed(icbm, [27.0] * (SETTLE_FRAMES * 3), cluster=27)
  adopted = _feed(icbm, [45.0], cluster=27)
  assert adopted[0] > 27, "an honest climb was metered"


def test_a_bounce_back_to_a_level_just_left_must_hold():
  """Fall 30 -> 27, then ask for 30 again: that is the route 000003ae shape, and it has to wait."""
  icbm = _icbm()
  _feed(icbm, [30.0] * (SETTLE_FRAMES * 3), cluster=27)
  _feed(icbm, [27.0] * 5, cluster=27)                        # the fall, taken at once
  bounce = _feed(icbm, [30.0] * (SETTLE_FRAMES * 2), cluster=27)

  assert bounce[0] == 27, "the bounce was waved through on its first frame"
  assert bounce[SETTLE_FRAMES - 3] == 27, "the bounce beat the settle window"
  assert bounce[-1] == 30, "a bounce that was asked for continuously never got through"


def test_the_reversal_memory_expires_once_the_target_settles():
  """After the shake stops, the next climb must be instant again -- the filter does not linger."""
  icbm = _icbm()
  _feed(icbm, [30.0] * SETTLE_FRAMES, cluster=27)
  _feed(icbm, [27.0] * (REVERSAL_MEMORY_FRAMES + 20), cluster=27)   # quiet long enough to forget
  adopted = _feed(icbm, [30.0], cluster=27)
  assert adopted[0] == 30, "a climb was still being metered long after the shake ended"


def test_an_ordinary_climb_is_never_delayed_at_all():
  """A real ramp moves less than the noise band per frame, so it must cost nothing.

  This is not the same claim as "the delay is paid once". Between two 10 ms frames a 3.3 mph/s
  climb moves 0.03 mph, far inside SETTLE_EPS, so it never enters the settle path in the first
  place. Written the other way round, this test passed against a controller that re-paid the delay
  on every step -- because on a physical ramp there is no delay to re-pay.
  """
  icbm = _icbm()
  _feed(icbm, [40.0] * (SETTLE_FRAMES * 3))
  ramp = [40.0 + 0.033 * i for i in range(400)]   # 3.3 mph/s, the car's real set-speed slew
  adopted = _feed(icbm, ramp)

  assert adopted == sorted(adopted), "the climb went backwards"
  assert adopted[-1] == round(ramp[-1]), f"the climb never arrived: {adopted[-1]}"
  assert _longest_plateau(adopted) < SETTLE_FRAMES, "an ordinary climb was made to wait"


def test_the_target_stops_shaking_with_the_dash_held_still():
  """The post-limiter number the button logic aims at must also settle, not just the raw one."""
  icbm = _icbm()
  _feed(icbm, _ae_waveform(2), cluster=27)
  seen = []
  for v in _ae_waveform(6):
    _feed(icbm, [v], cluster=27)
    seen.append(icbm.v_target)
  assert len(set(seen)) == 1, f"v_target still shaking: {sorted(set(seen))}"
