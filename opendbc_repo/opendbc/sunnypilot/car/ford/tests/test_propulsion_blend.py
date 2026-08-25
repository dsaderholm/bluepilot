"""FusionPilot: openpilot must be able to brake and use the engine at the same time, as Ford does.

His complaint is *"I've never seen it coast"*, and it was one `if` -- `if brake_actuate: gas =
INACTIVE_GAS`. The instant openpilot touched the brake at -0.14 m/s^2 its propulsion request became
the -5.0 "not requesting" sentinel, so it could never be in the state Ford's own frames describe:
asking the powertrain for -0.34 WHILE the brakes are working. No threshold value fixes that; only
removing the `if` does.

Ford's curve, measured over 143,745 frames of its own ACCDATA, is the table in `longitudinal_ext.py`.
What these tests protect is its SHAPE, because every part of the shape is load-bearing and each
would fail silently:

  - the request is monotone and never positive inside the deceleration band
  - it saturates at Ford's measured -0.66 and no deeper
  - it hands over to the friction brakes below -1.1 with the sentinel, exactly as Ford does
  - the sentinel is never clamped INTO the band, which would invert "no request" into "maximum
    request" -- the one way this change could be dangerous rather than merely wrong
  - the floor is inside panda's widened envelope, so nothing the blend produces can be dropped
  - the toggle off is byte-for-byte upstream's mutual exclusion
"""
import re

import pytest

from opendbc.car.ford.values import CarControllerParams
from opendbc.sunnypilot.car.ford import longitudinal_ext as lx
from opendbc.sunnypilot.car.ford.longitudinal_ext import (
  FORD_BLEND_ACCEL_BP,
  FORD_BLEND_HANDOVER_ACCEL,
  FORD_BLEND_PROPULSION_FLOOR,
  FORD_BLEND_PROPULSION_V,
  ford_propulsion_request,
)

SENTINEL = CarControllerParams.INACTIVE_GAS
UPSTREAM_GAS = 1.25  # a stand-in for "whatever upstream computed", distinctive enough to spot


class TestTheMeasuredTable:
  def test_it_reproduces_every_measured_point(self):
    """The breakpoints are Ford's own frames. Interpolation must return them unchanged."""
    for accel, expected in zip(FORD_BLEND_ACCEL_BP, FORD_BLEND_PROPULSION_V, strict=True):
      assert ford_propulsion_request(accel, UPSTREAM_GAS) == pytest.approx(expected, abs=1e-9)

  def test_the_breakpoints_ascend(self):
    """np.interp silently returns garbage on a non-increasing xp. Nothing else would notice."""
    assert list(FORD_BLEND_ACCEL_BP) == sorted(FORD_BLEND_ACCEL_BP)

  def test_the_request_is_monotone_across_the_band(self):
    """Harder braking must never ask the powertrain for MORE propulsion. A transposed pair in the
    table would produce exactly that and would still interpolate cleanly."""
    prev = None
    a = FORD_BLEND_HANDOVER_ACCEL
    while a <= FORD_BLEND_ACCEL_BP[-1] + 1e-9:
      g = ford_propulsion_request(a, UPSTREAM_GAS)
      if prev is not None:
        assert g >= prev - 1e-9, f"propulsion fell going UP the band at accel={a:.3f}"
      prev = g
      a += 0.005

  def test_it_saturates_at_fords_measured_floor(self):
    """-0.66 is what Ford was measured doing. Anything deeper is invented."""
    for a in (-1.1, -1.0, -0.9, -0.8):
      assert ford_propulsion_request(a, UPSTREAM_GAS) == pytest.approx(-0.66, abs=1e-9)


class TestTheHandover:
  def test_below_the_handover_it_is_the_sentinel(self):
    """Ford stops asking for propulsion below -1.1 and gives the car to the friction brakes.
    Copying the blend without copying the handover is copying half of it."""
    for a in (-1.11, -1.5, -2.0, -3.5):
      assert ford_propulsion_request(a, UPSTREAM_GAS) == SENTINEL

  def test_the_handover_matches_the_bottom_breakpoint(self):
    """If these two ever drift apart, the band has a gap or an overlap and one of them is dead."""
    assert FORD_BLEND_HANDOVER_ACCEL == FORD_BLEND_ACCEL_BP[0]

  def test_above_the_band_upstreams_own_gas_is_untouched(self):
    """Accelerating and coasting are not what this is about, and overriding them would replace
    upstream's planner output with a curve fitted to DECELERATION."""
    for a in (-0.09, 0.0, 0.5, 2.0):
      assert ford_propulsion_request(a, UPSTREAM_GAS) == UPSTREAM_GAS


class TestTheClampCannotInvertTheSentinel:
  def test_the_sentinel_is_never_clamped_into_the_band(self):
    """-5.0 sits BELOW the floor by construction. A clamp applied to it would turn "not requesting"
    into the deepest engine-braking request available -- the one way this change could be dangerous
    rather than merely wrong. The guard is the `!= INACTIVE_GAS` test in update()."""
    src = _update_source()
    assert "if gas != CarControllerParams.INACTIVE_GAS:" in src, \
      "the sentinel is no longer exempt from the clamp"
    assert SENTINEL < FORD_BLEND_PROPULSION_FLOOR, \
      "the sentinel is inside the clamp range; exempting it is now the ONLY thing saving it"

  def test_the_floor_never_binds(self):
    """It is a backstop, not a shaper. If it ever starts binding, the table moved and somebody
    should find out from a red test rather than from the car."""
    a = FORD_BLEND_HANDOVER_ACCEL
    while a <= FORD_BLEND_ACCEL_BP[-1] + 1e-9:
      assert ford_propulsion_request(a, UPSTREAM_GAS) >= FORD_BLEND_PROPULSION_FLOOR
      a += 0.005


class TestItStaysInsidePandasEnvelope:
  def test_the_floor_fits_the_widened_panda_band(self):
    """`FordSafetyFlagsSP.WIDE_PROPULSION_BAND` widens panda's gas floor to -2.8 and is set only
    under op long. Panda DROPS a frame it will not pass -- it does not soften it -- so a blended
    request outside the band makes a 50 Hz message vanish. Parsed from ford.h, which
    bp_offline_test.py never builds."""
    import pathlib
    here = pathlib.Path(lx.__file__).resolve()
    ford_h = next(p for p in here.parents if (p / "safety/modes/ford.h").exists()) / "safety/modes/ford.h"
    raw = int(re.search(r"#define FORD_MIN_GAS_WIDE (\d+)", ford_h.read_text(encoding="utf-8")).group(1))
    panda_floor = raw / 100.0 - 5.0
    assert FORD_BLEND_PROPULSION_FLOOR > panda_floor, \
      f"blend floor {FORD_BLEND_PROPULSION_FLOOR} is outside panda's {panda_floor}"

  def test_the_stock_panda_band_would_NOT_fit(self):
    """Stated so the dependency is explicit rather than incidental: with the widened band off,
    Ford's own -0.66 is outside what panda accepts. The blend REQUIRES the widening, and both are
    gated on op long for that reason."""
    import pathlib
    here = pathlib.Path(lx.__file__).resolve()
    ford_h = next(p for p in here.parents if (p / "safety/modes/ford.h").exists()) / "safety/modes/ford.h"
    raw = int(re.search(r"#define FORD_MIN_GAS_STOCK (\d+)", ford_h.read_text(encoding="utf-8")).group(1))
    assert raw / 100.0 - 5.0 > min(FORD_BLEND_PROPULSION_V)


def _update_source():
  import inspect
  return inspect.getsource(lx.LongitudinalExt.update)


class TestTheToggle:
  def test_off_restores_the_mutual_exclusion_exactly(self):
    """Not "something similar" -- upstream's line, reachable, unchanged. A revert has to be a real
    revert or the toggle is not a way back."""
    src = _update_source()
    assert "elif brake_actuate:" in src
    assert "gas = CarControllerParams.INACTIVE_GAS" in src

  def test_the_blend_is_gated_and_not_unconditional(self):
    src = _update_source()
    assert "if self.propulsion_blend:" in src, "the blend runs unconditionally"

  def test_the_param_is_read_every_frame(self):
    """`update_long_params` is called per frame, so the toggle takes effect on the drive he flips
    it. A read moved into __init__ would silently need an ignition cycle."""
    import inspect
    src = inspect.getsource(lx.LongitudinalExt.update_long_params)
    assert 'params.get_bool("FordPropulsionBlend")' in src

  def test_the_key_ships_on(self):
    """Every feature this fork builds ships ON -- a feature defaulting off is a recommendation not
    to use it, and it is how one goes untested for weeks."""
    import pathlib
    here = pathlib.Path(lx.__file__).resolve()
    root = next(p for p in here.parents if (p / "common/params_keys.h").exists())
    keys = (root / "common/params_keys.h").read_text(encoding="utf-8")
    m = re.search(r'\{"FordPropulsionBlend",\s*\{([^}]*)\}\}', keys)
    assert m, "FordPropulsionBlend is not declared in params_keys.h"
    assert "PERSISTENT" in m.group(1)
    assert re.search(r'BOOL,\s*"1"', m.group(1)), "it does not ship ON"
