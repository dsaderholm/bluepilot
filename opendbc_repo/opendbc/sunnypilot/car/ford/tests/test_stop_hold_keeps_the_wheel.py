"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

# FusionPilot: the stop hold must HOLD the wheel, not wind it -- see hold_wheel_cap.py.
#
# Route 000004bb, 2026-09-28: a right turn to a stop, hold at 10 mph, hands off. The command was
# flat and the wheel went -35 -> -130 deg, 2.5x the turn the model asked for, because the PSCM turns
# the same path angle into more wheel the slower the car goes.
#
# The wheel here is PRODUCED BY THE COMMAND: every test that is about the loop feeds the real
# `update_angle_strategy` a wheel angle from a rack model fitted to that stop (gain by speed, 0.4 s
# lag, 0.3 s delay), so the limiter is judged against a rack that answers it. The first test checks
# that the model reproduces the failure without the limiter; if it stops doing that, every other
# number in this file stops meaning anything.
#
# The uncapped twin is the SAME object with `hold_wheel_cap_failed` set -- the path the code takes
# when the limiter latches off -- so the comparison differs in exactly one thing.

import math

import numpy as np
import pytest

from opendbc.sunnypilot.car.ford.hold_wheel_cap import (
  RELEASE_RATE, SCALE_MIN, WHEEL_MARGIN_DEG, WHEEL_MARGIN_FRAC, geo_wheel_deg,
)
from opendbc.sunnypilot.car.ford.lateral_angle_ext import _MPH_TO_MS
from opendbc.sunnypilot.car.ford.tests.test_lateral_blend_horizon import (
  _Actuators, _CC, _CS, _Model, T_IDXS,
)
from opendbc.sunnypilot.car.ford.tests.test_stop_hold_latches_the_curvature import _ext as _base_ext

STEER_RATIO = 17.07   # his CarParams
WHEELBASE = 2.85
TURN = 0.019          # what the 000004bb approach latched
HOLD_MPH = 10.0       # what he runs
DT = 0.05
TAU = 0.4
DELAY = 6             # calls, 0.3 s
GEO = abs(geo_wheel_deg(TURN, STEER_RATIO, WHEELBASE))       # ~53 deg
LIMIT = GEO * (1.0 + WHEEL_MARGIN_FRAC) + WHEEL_MARGIN_DEG    # ~63 deg


def _gain(v_ms):
  """Wheel degrees per radian of path angle, by speed -- the ratios recorded on 000004bb."""
  return float(np.interp(v_ms / _MPH_TO_MS, [0, 2, 4, 6, 8, 10, 12, 20],
                         [1200, 1150, 900, 720, 610, 470, 400, 380]))


def _ext(hold_mph=HOLD_MPH, capped=True):
  ext = _base_ext(hold_mph=hold_mph)
  ext.CP.steerRatio = STEER_RATIO
  ext.CP.wheelbase = WHEELBASE
  ext.hold_wheel_cap_failed = not capped
  return ext


def _stop_profile():
  """The 000004bb shape: a turn at 12 mph, slow to a stop as the model gives the turn up, then sit."""
  prof = [(12 * _MPH_TO_MS, TURN)] * 40
  n = 90
  for i in range(n):
    v = 4.4 * (1.0 - i / (n - 1))
    desired = TURN * max(0.01, (v - 1.0) / 3.4) if v > 1.0 else 0.0002
    prof.append((v, desired))
  prof += [(0.0, 0.0002)] * 80
  return prof


def _run(ext, profile, wheel_trace=None, pressed=None, lat=None):
  """Drive the real strategy. With no trace, the wheel is the rack model answering the commands."""
  w = 0.0
  sent = []
  rows = []
  for k, (v, desired) in enumerate(profile):
    wheel = wheel_trace[k] if wheel_trace is not None else w
    # The model's yaw plan has to agree with the car's speed, as it does on the road: a fixed plan
    # built at one speed turns into a DIFFERENT predicted curvature at every other one, and the
    # prediction is half the blend above the hold's fade.
    ext.model = _Model([desired for _ in T_IDXS], max(v, 0.01))
    cs = _CS(vEgoRaw=v, vEgo=v, steeringAngleDeg=wheel,
             steeringPressed=bool(pressed[k]) if pressed is not None else False)
    active = True if lat is None else bool(lat[k])
    res = ext.update_angle_strategy(_CC(latActive=active), cs, _Actuators(curvature=desired), ext.CP)
    sent.append(res.path_angle)
    c = sent[k - DELAY] if k >= DELAY else sent[0]
    w += DT / TAU * (-_gain(v) * c - w)
    rows.append((v, wheel, res.path_angle, ext.bp_angle_hold_wheel_trim))
  return rows


def _at_stop(rows):
  return [r for r in rows if r[0] == 0.0]


def test_the_rack_model_reproduces_the_wind_up():
  """Without the limiter the model has to do what the car did, or this file tests nothing."""
  rows = _run(_ext(capped=False), _stop_profile())
  held = np.mean([abs(r[1]) for r in _at_stop(rows)])
  assert held > 2.0 * GEO, f"uncapped standstill wheel {held:.0f} deg; 000004bb wound to 2.5x geo"


def test_the_wheel_is_held_near_the_turn_asked_for():
  rows = _run(_ext(), _stop_profile())
  stop = _at_stop(rows)
  held = np.mean([abs(r[1]) for r in stop])
  assert held <= LIMIT * 1.15, f"standstill wheel {held:.0f} deg against a {LIMIT:.0f} deg limit"
  assert held >= 0.9 * GEO, f"standstill wheel {held:.0f} deg -- it must still HOLD the turn ({GEO:.0f})"
  assert all(r[1] < 0.0 for r in stop), "a right turn is held with the wheel to the RIGHT (negative)"
  peak = max(abs(r[1]) for r in rows)
  assert peak < 1.7 * GEO, f"peak {peak:.0f} deg on the way down; uncapped reaches ~2.5x"
  assert max(r[3] for r in rows) > 0.2, "the limiter must actually have acted"


def test_a_stopped_car_s_wheel_sits_still():
  """The first version released 3 deg under the limit, and the rack model cycled +-7 deg at a
  standstill -- the wheel moving at a red light. Once settled it must not move."""
  rows = _run(_ext(), _stop_profile() + [(0.0, 0.0002)] * 120)
  settled = [r[1] for r in _at_stop(rows)][-120:]
  assert max(settled) - min(settled) < 1.0, f"wheel wandered {max(settled) - min(settled):.1f} deg while stopped"


def test_it_only_ever_takes_command_away():
  """Same inputs to both -- the uncapped closed-loop wheel -- and the capped command is never larger."""
  prof = _stop_profile()
  trace = [r[1] for r in _run(_ext(capped=False), prof)]
  capped = _run(_ext(), prof, wheel_trace=trace)
  plain = _run(_ext(capped=False), prof, wheel_trace=trace)
  for (_, _, pc, _), (_, _, pu, _) in zip(capped, plain, strict=True):
    assert abs(pc) <= abs(pu) + 1e-12
    assert pc * pu >= 0.0, "it must never reverse the command"
  assert any(abs(pc) < abs(pu) - 1e-6 for (_, _, pc, _), (_, _, pu, _) in zip(capped, plain, strict=True))


def test_with_the_hold_off_it_is_bit_identical():
  prof = _stop_profile()
  trace = [-400.0 * math.sin(k / 7.0) for k in range(len(prof))]   # anything at all
  capped = _run(_ext(hold_mph=0.0), prof, wheel_trace=trace)
  plain = _run(_ext(hold_mph=0.0, capped=False), prof, wheel_trace=trace)
  assert [r[2] for r in capped] == [r[2] for r in plain]
  assert all(r[3] == 0.0 for r in capped)


def test_above_the_hold_speed_it_never_trims():
  """Ordinary driving: a wheel far past geo at 20 mph is none of this feature's business."""
  v = 20 * _MPH_TO_MS
  rows = _run(_ext(), [(v, 0.01)] * 80, wheel_trace=[-5.0 * k for k in range(80)])
  assert all(r[3] == 0.0 for r in rows)


def test_a_stuck_rack_at_a_standstill_is_left_alone():
  """Past the limit and NOT MOVING: trimming a rack that is not answering only stores up a step for
  the pull-away. 10 of the 15 hands-off holds on record sat at a standstill with a sticky rack."""
  prof = [(12 * _MPH_TO_MS, TURN)] * 40 + [(3.0, TURN)] * 20 + [(0.0, 0.0002)] * 200
  stuck = -(LIMIT + 25.0)
  trace = [stuck] * 260          # already there on the approach: a rack that got there and stopped
  rows = _run(_ext(), prof, wheel_trace=trace)
  assert all(r[3] == 0.0 for r in rows[60:]), "a still wheel past the limit must not be trimmed"


def test_a_winding_wheel_past_the_limit_is_trimmed():
  """The same stop with the wheel still going out -- the other half of the rule above."""
  prof = [(12 * _MPH_TO_MS, TURN)] * 40 + [(3.0, TURN)] * 20 + [(0.0, 0.0002)] * 60
  trace = [0.0] * 60 + [-(LIMIT + 2.0 * k) for k in range(60)]
  rows = _run(_ext(), prof, wheel_trace=trace)
  assert rows[-1][3] > 0.1


def test_the_quantisation_flicker_of_a_still_wheel_is_not_winding():
  """The angle is quantised at 0.1 deg. A still wheel reading back and forth by one step must not
  trim -- a one-call rate would read that as 2 deg/s."""
  prof = [(12 * _MPH_TO_MS, TURN)] * 40 + [(0.0, 0.0002)] * 200
  base = -(LIMIT + 20.0)
  trace = [base] * 40 + [base - (0.1 if k % 2 else 0.0) for k in range(200)]
  rows = _run(_ext(), prof, wheel_trace=trace)
  assert all(r[3] == 0.0 for r in rows)


def test_his_hands_freeze_it():
  """Hands on: neither trims nor gives back -- what the wheel reads is him, not the rack answering.
  Started from the SETTLED closed-loop stop, where the scale sits mid-range; from the floor or from
  1.0 a missing freeze could not show in one of the two directions."""
  ext = _ext()
  rows = _run(ext, _stop_profile())
  trim_before = rows[-1][3]
  assert 0.1 < trim_before < 1.0 - SCALE_MIN - 0.05, "the fixture must leave room in both directions"
  w0 = rows[-1][1]
  out = _run(ext, [(0.0, 0.0002)] * 40, wheel_trace=[w0 - 3.0 * k for k in range(40)], pressed=[True] * 40)
  back = _run(ext, [(0.0, 0.0002)] * 40, wheel_trace=[-5.0] * 40, pressed=[True] * 40)
  assert all(r[3] == pytest.approx(trim_before) for r in out + back)


def test_a_wheel_on_the_other_side_releases():
  prof = _stop_profile()
  trace = [r[1] for r in _run(_ext(capped=False), prof)]
  ext = _ext()
  rows = _run(ext, prof[:170], wheel_trace=trace[:170])
  trim = rows[-1][3]
  assert trim > 0.1
  # Far out to the LEFT and still going: past the right turn's limit in MAGNITUDE, the wrong way.
  # Trimming here would take right-turn command away because the wheel is turned left.
  after = _run(ext, [(0.0, 0.0002)] * 20, wheel_trace=[80.0 + 3.0 * k for k in range(20)])
  assert after[-1][3] < trim


def test_the_release_is_a_ramp_not_a_step():
  """Pulling away above the hold speed after a trimmed stop: the command comes back at
  RELEASE_RATE, never in one call. A 0.5 scale snapping to 1 at 10 mph doubles the command at once."""
  prof = _stop_profile()
  trace = [r[1] for r in _run(_ext(capped=False), prof)]
  ext = _ext()
  rows = _run(ext, prof, wheel_trace=trace)
  assert rows[-1][3] > 0.2
  away = _run(ext, [(6.0, TURN)] * 60, wheel_trace=[trace[-1]] * 60)
  trims = [rows[-1][3]] + [r[3] for r in away]
  assert all(a - b <= RELEASE_RATE * DT + 1e-9 for a, b in zip(trims, trims[1:], strict=False))
  assert trims[-1] == 0.0


def test_it_never_takes_away_more_than_scale_min():
  """A wheel that winds out forever -- a rack running away from the command -- still leaves the
  command at SCALE_MIN, never zero. This feature trims; it must never be the thing that drops a
  held turn."""
  prof = [(12 * _MPH_TO_MS, TURN)] * 40 + [(0.0, 0.0002)] * 400
  trace = [0.0] * 40 + [-(LIMIT + 1.0 + 2.0 * k) for k in range(400)]
  rows = _run(_ext(), prof, wheel_trace=trace)
  assert max(r[3] for r in rows) == pytest.approx(1.0 - SCALE_MIN)


def test_the_driver_steering_resets_it():
  """The human-turn bail-out resets it beside the latch, exactly like lateral dropping."""
  from opendbc.sunnypilot.car.ford.tests.test_lateral_blend_horizon import _ForcedDetector
  prof = _stop_profile()
  trace = [r[1] for r in _run(_ext(capped=False), prof)]
  ext = _ext()
  assert _run(ext, prof, wheel_trace=trace)[-1][3] > 0.1
  ext.human_turn_detector = _ForcedDetector(True)
  _run(ext, [(0.0, 0.0002)], wheel_trace=[trace[-1]])
  assert ext.hold_wheel_cap.scale == 1.0
  assert ext.bp_angle_hold_wheel_trim == 0.0


def test_lateral_dropping_resets_it():
  prof = _stop_profile()
  trace = [r[1] for r in _run(_ext(capped=False), prof)]
  ext = _ext()
  rows = _run(ext, prof, wheel_trace=trace)
  assert rows[-1][3] > 0.1
  _run(ext, [(0.0, 0.0002)], wheel_trace=[trace[-1]], lat=[False])
  assert ext.hold_wheel_cap.scale == 1.0
  assert ext.bp_angle_hold_wheel_trim == 0.0


def test_a_broken_limiter_does_not_take_the_car_with_it():
  """It runs inside CarController.update. An exception there stops the car, so it latches off and
  the command is exactly what it would have been without it."""
  prof = _stop_profile()
  trace = [r[1] for r in _run(_ext(capped=False), prof)]
  ext = _ext()
  calls = []

  def boom(*_a, **_k):
    calls.append(1)
    raise ValueError("limiter blew up")

  ext.hold_wheel_cap.update = boom
  broken = _run(ext, prof, wheel_trace=trace)
  plain = _run(_ext(capped=False), prof, wheel_trace=trace)
  assert ext.hold_wheel_cap_failed
  assert len(calls) == 1, "latched off after the first failure, not retried every frame"
  assert [r[2] for r in broken] == [r[2] for r in plain]
  assert all(r[3] == 0.0 for r in broken)


def test_the_target_is_the_turn_asked_for_with_his_car_s_geometry():
  """+ curvature is RIGHT, + wheel is LEFT (lateral_angle_ext, above ANGLE_HOLD_MAX_MPH)."""
  assert geo_wheel_deg(TURN, STEER_RATIO, WHEELBASE) == pytest.approx(-52.91, abs=0.01)
  assert geo_wheel_deg(-TURN, STEER_RATIO, WHEELBASE) == pytest.approx(52.91, abs=0.01)
  assert geo_wheel_deg(0.0, STEER_RATIO, WHEELBASE) == 0.0
