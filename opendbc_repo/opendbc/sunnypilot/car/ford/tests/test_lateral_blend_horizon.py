"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

# The predicted/desired blend must sample the model at the horizon the planner's command is
# already aimed at, and the ENTRY DECISION must keep using the clipped base.
#
# `actuators.curvature` arrives pre-compensated by modeld at
#     lat_action_t = lat_delay + DT_MDL + DT_MDL/2
# so blending in a model sample taken anywhere else averages the road in two different places and
# silently shifts the aim point. Measured on 000003eb/000003ec that cost 5.9% of the command with
# the road tightening (p90 16.6%) and over-commanded 3.5% with it opening out -- turn in late, then
# overshoot; unwind slowly. See tools/bp_lateral_horizon.py.
#
# The clip on `_t_base` is NOT the bug and must survive: letting the ENTRY decision run out to the
# real ~0.38 s delay is the documented apex failure (kappa_entering latches True, the exit-biased
# blend is disabled, and the car commands max path_angle through the whole apex). The two jobs are
# separated, and both halves are pinned here -- the second test fails if anyone "simplifies" them
# back into one number.

import unittest
from dataclasses import dataclass
from unittest import mock

from opendbc.car import structs
from opendbc.car.interfaces import scale_tire_stiffness
from opendbc.sunnypilot.car.ford import lateral_curv_ext
from opendbc.sunnypilot.car.ford.lateral_angle_ext import (
  LateralAngleExt, _DT_MDL, _MODELD_ACTION_DELAY_S, _VLT_T_EXTRA_MAX,
  _VLT_V_LOW_MS, _VLT_V_HIGH_MS, _VLT_KAPPA_FULL, _VLT_KAPPA_TAPER,
)
from opendbc.sunnypilot.car.ford.lateral_curv_ext import LateralCurvExt

# openpilot's model time index; the profile below is built against it directly so the test does not
# depend on ModelConstants being importable in the offline runner.
T_IDXS = [0.0, 0.00976562, 0.0390625, 0.08789062, 0.15625, 0.24414062, 0.3515625,
          0.47851562, 0.625, 0.79101562, 0.9765625, 1.18164062, 1.40625, 1.65039062,
          1.9140625, 2.19726562, 2.5, 2.82226562, 3.1640625, 3.52539062, 3.90625,
          4.30664062, 4.7265625, 5.16601562, 5.625, 6.10351562, 6.6015625, 7.11914062,
          7.65625, 8.21289062, 8.7890625, 9.38476562, 10.0]


def _explorer_cp():
  CP = structs.CarParams()
  CP.mass = 2050.
  CP.wheelbase = 3.025
  CP.steerRatio = 16.8
  CP.centerToFront = CP.wheelbase * 0.44
  CP.tireStiffnessFactor = 0.82
  CP.tireStiffnessFront, CP.tireStiffnessRear = scale_tire_stiffness(
    CP.mass, CP.wheelbase, CP.centerToFront, CP.tireStiffnessFactor)
  return CP


class _FakeLiveDelay:
  lateralDelay = 0.2


class _FakeSubMaster:
  def __init__(self, *args, **kwargs):
    self.updated = {s: False for s in ('modelV2', 'liveParameters', 'selfdriveState', 'radarState', 'liveDelay')}

  def update(self, timeout=0):
    pass

  def __getitem__(self, key):
    if key == 'liveDelay':
      return _FakeLiveDelay()
    raise KeyError(key)


class _OrientationRate:
  def __init__(self, z):
    self.z = z


class _XY:
  def __init__(self, x, y):
    self.x, self.y = x, y


class _Meta:
  laneChangeState = 0
  laneChangeDirection = 0


class _Model:
  """A model whose yaw-rate profile encodes a chosen curvature-vs-time shape at v_ego."""

  def __init__(self, curvatures, v_ego):
    self.orientationRate = _OrientationRate([k * v_ego for k in curvatures])
    xs = [0.0, 10.0, 20.0, 30.0]
    self.laneLines = [_XY(xs, [-5.55] * 4), _XY(xs, [-1.85] * 4),
                      _XY(xs, [1.85] * 4), _XY(xs, [5.55] * 4)]
    self.laneLineProbs = [0.9, 0.9, 0.9, 0.9]
    self.laneLineStds = [0.1, 0.1, 0.1, 0.1]
    self.position = _XY(xs, [0.0] * 4)
    self.meta = _Meta()


@dataclass
class _CSOut:
  vEgoRaw: float = 15.0
  vEgo: float = 15.0
  steeringPressed: bool = False
  steeringAngleDeg: float = 0.0
  yawRate: float = 0.0


class _CS:
  def __init__(self, **kwargs):
    self.out = _CSOut(**kwargs)
    self.lat_ctl_lim_stat = 0


@dataclass
class _CC:
  latActive: bool = True


@dataclass
class _Actuators:
  curvature: float = 0.0


class _ForcedDetector:
  def __init__(self, active):
    self.active = active

  def update(self, *_args):
    return self.active

  def reset(self):
    pass


class _Harness(LateralCurvExt, LateralAngleExt):
  def __init__(self, CP, CP_SP=None):
    self.CP = CP
    with mock.patch.object(lateral_curv_ext.messaging, 'SubMaster', _FakeSubMaster):
      LateralCurvExt.__init__(self, CP, CP_SP)
    LateralAngleExt.__init__(self, CP, CP_SP)


def _ramp(k0, k1):
  """Curvature rising linearly in TIME from k0 at t=0 to k1 at t=1s, flat after."""
  return [k0 + (k1 - k0) * min(t, 1.0) for t in T_IDXS]


def _run(delay, v_ego, desired, curvatures, calls=1):
  _FakeLiveDelay.lateralDelay = delay
  CP = _explorer_cp()
  ext = _Harness(CP)
  ext.human_turn_detector = _ForcedDetector(False)
  ext.model = _Model(curvatures, v_ego)
  cs = _CS(vEgoRaw=v_ego, vEgo=v_ego)
  for _ in range(calls):
    ext.update_angle_strategy(_CC(latActive=True), cs, _Actuators(curvature=desired), CP)
  return ext


class TestTheBlendSamplesThePlannersHorizon(unittest.TestCase):
  """The whole fix, asserted on the expression rather than on a window of the file."""

  def tearDown(self):
    _FakeLiveDelay.lateralDelay = 0.2

  def test_the_constant_mirrors_modeld(self):
    # modeld.py: lat_action_t = lat_delay + frame_delay + action_delay, with frame_delay = DT_MDL
    # and action_delay = DT_MDL / 2. If modeld changes, this is the line that has to move.
    self.assertAlmostEqual(_MODELD_ACTION_DELAY_S, _DT_MDL + _DT_MDL / 2.0)

  def test_at_highway_speed_the_lookup_is_the_planners_own_horizon(self):
    # Above _VLT_V_HIGH_MS the speed taper zeroes the extra, so the lookup IS the blend base and
    # the arithmetic is exposed with nothing else on top of it.
    delay = 0.38
    ext = _run(delay, _VLT_V_HIGH_MS + 5.0, 0.0015, _ramp(0.0010, 0.0030))
    self.assertAlmostEqual(ext.bp_curvature_lookup_time, delay + _MODELD_ACTION_DELAY_S, places=6)

  def test_it_is_no_longer_pinned_to_the_decision_clip(self):
    # The bug: the old base was clip(delay, 0.1, 0.15) + DT_MDL, so at any real delay it sat at
    # 0.20 s no matter what the car had learned. A regression to that shows up here as 0.20.
    ext = _run(0.38, _VLT_V_HIGH_MS + 5.0, 0.0015, _ramp(0.0010, 0.0030))
    self.assertGreater(ext.bp_curvature_lookup_time, 0.30)
    self.assertNotAlmostEqual(ext.bp_curvature_lookup_time, 0.15 + _DT_MDL, places=3)

  def test_a_larger_learned_delay_moves_the_sample_deeper(self):
    shallow = _run(0.25, _VLT_V_HIGH_MS + 5.0, 0.0015, _ramp(0.0010, 0.0030))
    deep = _run(0.40, _VLT_V_HIGH_MS + 5.0, 0.0015, _ramp(0.0010, 0.0030))
    self.assertAlmostEqual(deep.bp_curvature_lookup_time - shallow.bp_curvature_lookup_time,
                           0.15, places=6)


class TestTheEntryDecisionKeepsTheClip(unittest.TestCase):
  """The apex guard. This is the half that must NOT move, and it is easy to delete by accident."""

  def tearDown(self):
    _FakeLiveDelay.lateralDelay = 0.2

  def _kappa_factor_from(self, ext, delay, v_ego):
    """Recover the kappa_factor the run used, from the published lookup time."""
    base = min(max(delay, 0.1), 0.45) + _MODELD_ACTION_DELAY_S
    speed_factor = 1.0 if v_ego <= _VLT_V_LOW_MS else 0.0
    return (ext.bp_curvature_lookup_time - base) / (_VLT_T_EXTRA_MAX * speed_factor)

  def test_the_entry_decision_reads_the_clipped_base_not_the_learned_delay(self):
    # Built so the two bases disagree about whether the curve is deepening ahead:
    #   at the CLIPPED decision depth (0.15 + 0.05 = 0.20 s)  the path is BELOW desired -> not entering
    #   at the raw learned depth      (0.45 + 0.05 = 0.50 s)  the path is ABOVE desired -> entering
    # so a regression that feeds the learned delay into _kappa_entering flips kappa_factor from the
    # taper value to 1.0, and the published lookup time moves. Verified to fail with the clip removed.
    v_ego = _VLT_V_LOW_MS - 1.0            # full speed factor, so kappa_factor is visible
    desired = 0.010
    # ramp over 1 s: 0.20 s -> 0.006 (below desired), 0.50 s -> 0.015 (above desired)
    curvatures = [0.001 + 0.028 * min(t, 1.0) for t in T_IDXS]
    ext = _run(0.45, v_ego, desired, curvatures)
    expected_taper = (_VLT_KAPPA_TAPER - desired) / (_VLT_KAPPA_TAPER - _VLT_KAPPA_FULL)
    self.assertAlmostEqual(self._kappa_factor_from(ext, 0.45, v_ego), expected_taper, places=3)

  def test_a_genuinely_deepening_curve_still_gets_full_lookahead(self):
    # The other side of the same branch: when the CLIPPED depth already shows more curvature than
    # the planner is asking for, entry is real and the extra lookahead must not be tapered away.
    v_ego = _VLT_V_LOW_MS - 1.0
    desired = 0.010
    curvatures = [0.012 + 0.010 * min(t, 1.0) for t in T_IDXS]   # above desired everywhere
    ext = _run(0.45, v_ego, desired, curvatures)
    self.assertAlmostEqual(self._kappa_factor_from(ext, 0.45, v_ego), 1.0, places=3)


class TestTheBlendWeightRampRunsForReal(unittest.TestCase):
  """Drives the SHIPPED path, unlike test_blend_weight_ramp.py which mirrors the arithmetic.

  A mirror test passes whether or not the real code matches it -- which is the vacuous-test shape
  this repo keeps recording. This one reads `ext.b_blend` off a harness that actually called
  `update_angle_strategy`, so deleting the ramp fails it."""

  def tearDown(self):
    _FakeLiveDelay.lateralDelay = 0.2

  # A STRAIGHTAWAY selects target = b * 0.35 = 0.175 on the very first call, with no history
  # needed: desired below 0.00125 makes `_on_straightaway` true, while `_kappa_entering` and
  # `_desired_falling` both refuse below their 0.001 floor. That is the cheapest scenario in which
  # the target differs from the seeded weight, which is what makes the ramp observable at all.
  _STRAIGHT_DESIRED = 0.0005

  def test_one_call_cannot_move_the_weight_more_than_one_step(self):
    v = _VLT_V_HIGH_MS + 5.0
    ext = _run(0.38, v, self._STRAIGHT_DESIRED, _ramp(0.0004, 0.0004), calls=1)
    self.assertAlmostEqual(ext.b_blend, 0.40, places=6,
                           msg="one call must move exactly one b_step from 0.50 toward 0.175; "
                               "landing on 0.175 is the instant jump ba20937aac removes")

  def test_it_takes_several_calls_to_reach_the_straightaway_weight(self):
    v = _VLT_V_HIGH_MS + 5.0
    ext = _run(0.38, v, self._STRAIGHT_DESIRED, _ramp(0.0004, 0.0004), calls=10)
    self.assertAlmostEqual(ext.b_blend, 0.175, places=6)

  def test_the_weight_is_seeded_before_any_call(self):
    CP = _explorer_cp()
    ext = _Harness(CP)
    self.assertAlmostEqual(ext.b_blend, 0.50,
                           msg="unseeded state on the CarController is what made the car undrivable once")

  def test_repeated_calls_settle_rather_than_oscillate(self):
    v = _VLT_V_HIGH_MS + 5.0
    a = _run(0.38, v, self._STRAIGHT_DESIRED, _ramp(0.0004, 0.0004), calls=20)
    b = _run(0.38, v, self._STRAIGHT_DESIRED, _ramp(0.0004, 0.0004), calls=40)
    self.assertAlmostEqual(a.b_blend, b.b_blend, places=6,
                           msg="20 and 40 calls must agree -- otherwise the ramp is hunting")


if __name__ == "__main__":
  unittest.main()
