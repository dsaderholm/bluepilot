"""FusionPilot: damping the one closed loop in the lateral stack, and proving 0.0 changes nothing.

`lane_center_trim` is a PURE PROPORTIONAL position controller -- `raw = 2*error/lookahead**2`, no
derivative term -- with `_SMOOTH_TAU_S` 0.4 s of filter lag on top of the car's own ~0.39 s of
steering lag. A P controller with ~0.8 s of loop lag rings, and this one does. Measured 2026-09-01
on straight road at 70+ mph, hands off, from the model's own lane lines:

    strength 0.55   29-44 cm peak-to-peak, crossing lane centre 14-20 times a minute
    strength 0.15   crossings halved, but median offset 4-6 cm -> 21 cm

Lowering the gain is the only lever that existed, and it buys calm with centring accuracy. The
`lane_centering_damping_ang` lead time is the fix that does not make that trade: the controller acts
on `error + damping * d(error)/dt`, which is dimensionally still a position and therefore goes
straight through the existing geometry and every existing limiter.

**IT SHIPS AT 0.0 AND THE FIRST TEST HERE IS WHY THAT IS SAFE.** How this particular loop responds
to a lead term is UNMEASURED, and a lateral change shipped on reasoning alone is how the 5.20 m/s^2
event happened. So the guarantee that matters most is not that damping works -- it is that a 0.0
value cannot alter a single command, which makes shipping it inert genuinely inert rather than
merely intended to be.
"""
from __future__ import annotations

import numpy as np
import pytest

from opendbc.sunnypilot.car.ford.lane_center_trim import (
  LaneCenterTrim, _MAX_LEAD_M, _MAX_DAMPING_S, _TICK_S,
)
from opendbc.sunnypilot.car.ford.tests.test_lane_center_trim import _good_model


def _drive(trim, ys, *, damping, gain=1.0, v_ego=30.0, offset=0.0):
  """Run the trim down a road where the car's own predicted path walks through `ys`.

  A MOVING error is the whole point: a derivative term is identically zero on a constant one, so a
  fixture that holds position still cannot tell the damped controller from the undamped one.
  """
  out = []
  for y in ys:
    out.append(trim.update(0.0, _good_model(lane_center_y=0.0, model_y=y), v_ego,
                           True, offset, gain, True, False, damping))
  return out


# A lateral error that grows and then reverses -- the shape a weaving car actually makes.
WEAVE = [0.0, 0.05, 0.11, 0.18, 0.26, 0.33, 0.38, 0.40, 0.38, 0.33, 0.26, 0.18, 0.11, 0.05, 0.0]


class TestZeroIsInert:
  """The guarantee that makes shipping this safe."""

  def test_zero_damping_is_bit_identical_to_the_old_controller(self):
    """Not 'approximately unchanged' -- IDENTICAL. Anything less means the shipped default quietly
    altered his lateral behaviour, which is the thing being avoided by shipping at 0.0."""
    a = _drive(LaneCenterTrim(), WEAVE, damping=0.0)
    b = _drive(LaneCenterTrim(), WEAVE, damping=0.0)
    assert a == b
    # and against the pre-damping behaviour, reconstructed by calling without the new argument
    old = LaneCenterTrim()
    ref = [old.update(0.0, _good_model(lane_center_y=0.0, model_y=y), 30.0, True, 0.0, 1.0, True, False)
           for y in WEAVE]
    assert a == ref, (
      "a 0.0 damping value does not reproduce the pure-proportional controller exactly -- the "
      "shipped default is silently changing lateral behaviour")

  def test_the_default_argument_is_zero(self):
    """Any caller that has not been taught about damping must get the old controller. The Ford
    carcontroller path is not the only thing that constructs this."""
    import inspect
    sig = inspect.signature(LaneCenterTrim.update)
    assert sig.parameters["damping"].default == 0.0


def _raw_series_with_rate(ys, damping):
  """Raw correction AND the filtered error rate that produced it, per frame."""
  trim = LaneCenterTrim()
  out = []
  for y in ys:
    ok, raw = trim._raw_correction(_good_model(lane_center_y=0.0, model_y=y), 30.0, 0.0, damping)
    assert ok
    out.append((raw, trim._error_rate))
  return out


def _raw_series(ys, damping):
  """The RAW correction per frame, before `_SMOOTH_TAU_S` and the rate limiter.

  Those two deliberately lag the output, so at the COMMAND the lead term's effect arrives blurred
  across several frames -- a first version of the closing-half test compared commands and failed
  for that reason rather than because the damping was wrong. The lead term lives in
  `_raw_correction`; testing it there isolates the mechanism from the filter that follows it.
  """
  trim = LaneCenterTrim()
  out = []
  for y in ys:
    ok, raw = trim._raw_correction(_good_model(lane_center_y=0.0, model_y=y), 30.0, 0.0, damping)
    assert ok
    out.append(raw)
  return out


class TestTheLeadTerm:
  def test_damping_ACTS_SOONER_while_the_error_is_growing(self):
    """The lead adds to a growing error, so the controller starts correcting before the error has
    finished developing -- which is what stops it having to chase an overshoot afterwards."""
    undamped = _raw_series(WEAVE, 0.0)
    damped = _raw_series(WEAVE, 0.4)
    grow = slice(2, 7)
    assert all(abs(d) > abs(u) for d, u in zip(damped[grow], undamped[grow])), (
      "damping is not strengthening the correction while the error grows")

  def test_damping_BACKS_OFF_while_the_error_is_already_closing(self):
    """The half that actually stops the ringing.

    ASSERTED ON THE SIGNED VALUE, gated on the FILTERED rate, and both of those are corrections a
    first version got wrong:

    - `abs(damped) < abs(undamped)` is false at the end of a fast close, because a strong lead
      legitimately drives the command PAST zero and out the other side. That is not the damper
      misbehaving; it is what a derivative term does, and measuring it with `abs()` reads the
      correct behaviour as a failure.
    - the window cannot be picked by where the ERROR turns, because `_error_rate` is filtered
      (`_DERIV_TAU_S`) and lags it by a few frames. Gating on the rate the controller actually
      used is the only honest window.
    """
    undamped = _raw_series(WEAVE, 0.0)
    damped = _raw_series_with_rate(WEAVE, 0.4)
    checked = 0
    for (d, rate), u in zip(damped, undamped):
      if rate <= 0.0:
        continue          # error still opening, or the filter has not caught up yet
      checked += 1
      assert d > u, (
        "while the error is closing the lead must make the correction algebraically GREATER "
        "(less negative) than the undamped one -- it is adding to the correction instead of "
        "backing it off, which is more gain, not damping")
    assert checked >= 3, "the fixture never reached a closing phase; the test proves nothing"

  def test_the_two_halves_are_the_SAME_term_with_opposite_sign(self):
    """Guards against a 'damper' that only ever increases the command, which would read as working
    on the growing half and be pure extra gain."""
    undamped = _raw_series(WEAVE, 0.0)
    damped = _raw_series(WEAVE, 0.4)
    deltas = [d - u for d, u in zip(damped, undamped)]
    assert max(deltas) > 0 and min(deltas) < 0, (
      "the lead term never changes sign across a weave -- it is an offset, not a derivative")

  def test_a_CONSTANT_error_is_unaffected_by_damping(self):
    """A derivative term must not change the steady state. If it does, it is not a lead term --
    it is an offset, and it would move where the car sits in the lane."""
    steady = [0.25] * 40
    a = _drive(LaneCenterTrim(), steady, damping=0.0)
    b = _drive(LaneCenterTrim(), steady, damping=0.6)
    assert a[-1] == pytest.approx(b[-1], abs=1e-9), (
      "damping changed the steady-state correction -- it is shifting lane position, not damping")


class TestItCannotMisbehave:
  def test_the_first_frame_after_a_reset_has_NO_derivative(self):
    """`_error_last` starts as None. Treating that as 0.0 would manufacture a full-scale rate on
    the first frame of every engagement -- a kick exactly when the driver just handed over."""
    trim = LaneCenterTrim()
    first = trim.update(0.0, _good_model(lane_center_y=0.0, model_y=0.5), 30.0,
                        True, 0.0, 1.0, True, False, 1.0)
    ref = LaneCenterTrim().update(0.0, _good_model(lane_center_y=0.0, model_y=0.5), 30.0,
                                  True, 0.0, 1.0, True, False, 0.0)
    assert first == pytest.approx(ref), "a derivative was applied with no previous sample"

  def test_reset_clears_the_rate_state(self):
    trim = LaneCenterTrim()
    _drive(trim, WEAVE, damping=0.5)
    trim.reset()
    assert trim._error_last is None
    assert trim._error_rate == 0.0

  def test_a_disengagement_resets_it(self):
    """`update` resets on `not lat_active`, so a re-engage must not inherit the last drive's rate."""
    trim = LaneCenterTrim()
    _drive(trim, WEAVE, damping=0.5)
    trim.update(0.0, _good_model(), 30.0, True, 0.0, 1.0, False, False, 0.5)
    assert trim._error_last is None and trim._error_rate == 0.0

  @pytest.mark.parametrize("bad", [float("nan"), float("inf"), -5.0, 99.0])
  def test_a_nonsense_damping_value_cannot_reach_the_geometry(self, bad):
    """The param is clipped in the UI, but a hand-edited params file is not, and this runs inside
    CarController.update where an exception stops the car."""
    trim = LaneCenterTrim()
    out = _drive(trim, WEAVE, damping=bad)
    assert all(np.isfinite(v) for v in out)

  def test_THE_DERIVATIVE_IS_FILTERED(self):
    """Raw D on a noisy signal is how a damper becomes an oscillator, and the model position IS
    noisy. A one-tick step must NOT produce its full instantaneous rate."""
    trim = LaneCenterTrim()
    trim._raw_correction(_good_model(model_y=0.0), 30.0, 0.0, 0.5)   # prime _error_last
    trim._raw_correction(_good_model(model_y=0.5), 30.0, 0.0, 0.5)   # 0.5 m in one 0.05 s tick
    instantaneous = 0.5 / _TICK_S                                     # 10 m/s
    assert abs(trim._error_rate) < 0.5 * instantaneous, (
      f"the error rate is unfiltered ({trim._error_rate:.2f} vs {instantaneous:.2f} m/s "
      "instantaneous) -- a single noisy model frame reaches the steering command at full weight")

  def test_a_sustained_rate_still_converges_through_the_filter(self):
    """The other side of it: the filter must not be so heavy that a real, sustained drift never
    reaches the controller. A lag that swallows the signal is not damping, it is deafness."""
    trim = LaneCenterTrim()
    for i in range(60):
      trim._raw_correction(_good_model(model_y=0.02 * i), 30.0, 0.0, 0.5)
    assert abs(trim._error_rate) > 0.8 * (0.02 / _TICK_S) * 0.9, (
      "a steady 0.4 m/s drift never reaches the derivative term")

  def test_the_lead_contribution_is_CLAMPED(self):
    """D on a noisy model position is the classic way a damper becomes an oscillator. The lead can
    never exceed _MAX_LEAD_M of equivalent position error however violently the model jumps."""
    trim = LaneCenterTrim()
    jumpy = [0.0, 2.0, -2.0, 2.0, -2.0, 2.0]
    out = _drive(trim, jumpy, damping=_MAX_DAMPING_S)
    assert all(np.isfinite(v) for v in out)
    # the clamp is on the lead in metres; confirm it is actually consulted
    assert _MAX_LEAD_M > 0.0
    trim2 = LaneCenterTrim()
    trim2._error_last = 0.0
    trim2._error_rate = 1e6
    _, raw = trim2._raw_correction(_good_model(model_y=0.0), 30.0, 0.0, _MAX_DAMPING_S)
    lookahead = 30.0
    assert abs(raw) <= 2.0 * (abs(_MAX_LEAD_M) + 1e-6) / (lookahead ** 2) + 1e-9, (
      "an unbounded error rate reached the geometry -- the lead clamp is not applied")


class TestItShipsInert:
  """The whole safety argument for adding a control change to a car he drives."""

  def test_the_SHIPPED_DEFAULT_is_zero(self):
    """0.0 is what makes this safe to land without a validating drive. If a later edit ships it
    active, that is a lateral behaviour change arriving with no road evidence -- the 5.20 m/s^2
    pattern -- and it must fail here rather than on a highway."""
    import os
    # Walk up to the MARKER. A fixed `..` count breaks depending on whether pytest collected this
    # by file path or through the opendbc package alias -- the two differ by one directory, which
    # is the same bug that made test_lateral_telemetry_published fail only in the full run.
    d = os.path.dirname(os.path.abspath(__file__))
    while not os.path.exists(os.path.join(d, "common", "params_keys.h")):
      parent = os.path.dirname(d)
      assert parent != d, "params_keys.h not found in any ancestor"
      d = parent
    keys = open(os.path.join(d, "common", "params_keys.h"), encoding="utf-8").read()
    line = next(x for x in keys.splitlines() if "lane_centering_damping_ang" in x and "{" in x)
    assert '"0.0"' in line, (
      f"lane_centering_damping_ang no longer ships inert: {line.strip()}. Turning damping on by "
      "default is a control change to the one closed loop in the lateral stack, on a car being "
      "driven, with no drive behind it.")


class TestTheConstantsAreCoherent:
  def test_the_tick_matches_the_smoothing_filter_it_sits_beside(self):
    """`_SMOOTH_TAU_S`'s alpha is computed from a hardcoded 0.05 s. If the lateral tick ever moves,
    both must move together or the derivative is scaled wrong while the filter is not."""
    src = open(
      __file__.replace("tests\\test_lane_center_damping.py", "lane_center_trim.py")
              .replace("tests/test_lane_center_damping.py", "lane_center_trim.py"),
      encoding="utf-8").read()
    assert "np.exp(-0.05 / _SMOOTH_TAU_S)" in src, (
      "the smoothing filter no longer assumes a 0.05 s tick; _TICK_S must be reconciled with it")
    assert _TICK_S == 0.05
