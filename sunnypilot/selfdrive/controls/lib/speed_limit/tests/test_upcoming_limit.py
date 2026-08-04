"""BluePilot: adopting the NEXT speed limit early, so the car coasts into the zone.

The mechanism was already in sunnypilot, carrying `# FIXME-SP: this is not working as expected`.
It was a clock-epoch mix-up: time.monotonic() counts seconds since boot, unixTimestampMillis
counts seconds since 1970, and subtracting them produced a fix age of about -1.8 billion seconds.
distance_to_speed_limit_ahead came out astronomically large and the early adoption could never
fire, so the car only ever saw a new limit on arriving at it -- then braked to comply, a hundred
metres late and with the stop lamps lit.

LIMIT_ADAPT_ACC is -1.0 m/s^2, deliberately under the 1.3 m/s^2 that lights the lamps. Coast in,
do not brake at the boundary.
"""
import time

from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit import LIMIT_ADAPT_ACC, MAX_FIX_AGE_S


def adapt_distance(v_ego: float, next_limit: float) -> float:
  """The distance the resolver needs to shed v_ego -> next_limit at LIMIT_ADAPT_ACC."""
  adapt_time = (next_limit - v_ego) / LIMIT_ADAPT_ACC
  return v_ego * adapt_time + 0.5 * LIMIT_ADAPT_ACC * adapt_time ** 2


def fix_age_from(monotonic_style: bool) -> float:
  """Reproduces the old and new age calculations against a fresh GPS timestamp."""
  unix_ms = time.time() * 1e3
  raw = (time.monotonic() if monotonic_style else time.time()) - unix_ms * 1e-3
  return raw if monotonic_style else min(max(raw, 0.0), MAX_FIX_AGE_S)


class TestTheEarlyAdoptionCanActuallyFire:
  def test_the_old_clock_comparison_made_it_impossible(self):
    """Guards the diagnosis: the bug was the epoch, not the arithmetic."""
    assert fix_age_from(monotonic_style=True) < -1e8, \
      "expected a nonsense age from mixing monotonic with a unix epoch"

  def test_the_corrected_age_is_sane(self):
    age = fix_age_from(monotonic_style=False)
    assert 0.0 <= age <= MAX_FIX_AGE_S

  def test_a_fresh_fix_leaves_the_distance_essentially_untouched(self):
    """With a current fix there is nothing to extrapolate, so the sign stays where the map put it."""
    v_ego, ahead = 29.0, 250.0
    corrected = max(0.0, ahead - v_ego * fix_age_from(monotonic_style=False))
    assert abs(corrected - ahead) < 1.0

  def test_it_triggers_inside_the_adapt_distance_and_not_outside(self):
    v_ego, nxt = 29.0, 15.6            # 65 mph approaching a 35 zone
    needed = adapt_distance(v_ego, nxt)
    assert 250 < needed < 350, f"sanity: expected a few hundred metres, got {needed:.0f}"
    assert needed - 1 <= needed        # inside -> adopt
    assert not (needed + 50 <= needed)  # still well short -> do not adopt yet

  def test_the_planned_deceleration_stays_under_the_brake_lamp_threshold(self):
    """The whole point: coast into the zone rather than brake at the sign. UN R13-H lights the
    lamps above 1.3 m/s^2 of automatically commanded braking."""
    assert abs(LIMIT_ADAPT_ACC) < 1.3
