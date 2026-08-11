"""FusionPilot: the mapped-corner factor must be blended on the CORNER's speed, not the car's.

Measured on route 00000338 at t+796 on 2026-08-10. SCC-Map's own number for a highway bend was 48
mph; a single global factor of 90 asked for 43; the owner overrode with the accelerator and took the
bend at 51 mph pulling 2.9 m/s^2 without difficulty. The same 90 had been set two days earlier for
the opposite reason -- an exit ramp his retrofit PSCM wanted taken well below the yellow advisory.

Both reports were correct, which is why one knob could not serve them. A ramp is a 25 mph corner
entered at 75 and a sweeper is a 50 mph corner entered at 75: identical ego speed, opposite
requirements. So the blend keys on the corner, and this pins that -- keying it on vEgo would pass a
test that only ever checks one corner at a time.
"""
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.map_controller import SmartCruiseControlMap

MPH = 0.44704


def _controller(low, high):
  c = SmartCruiseControlMap.__new__(SmartCruiseControlMap)
  c.map_factor = low / 100.
  c.map_high_speed_factor = high / 100.
  return c


def test_a_tight_ramp_keeps_the_extra_slowing_he_asked_for():
  c = _controller(low=90, high=100)
  # A 20 mph loop ramp is below the blend band, so it gets the tight factor outright.
  assert abs(c._factor_for_corner(20 * MPH) - 0.90) < 1e-6


def test_a_highway_bend_keeps_the_maps_own_number():
  c = _controller(low=90, high=100)
  # The measured bend: the map said 48 mph. Under one global factor of 90 this asked 43.
  corner = 48 * MPH
  assert abs(c._factor_for_corner(corner) - 1.0) < 1e-6
  asked = 48 * c._factor_for_corner(corner)
  assert abs(asked - 48) < 0.5, f"asked for {asked:.0f} mph on a bend he took at 51"


def test_the_blend_is_keyed_on_the_corner_and_not_the_car():
  """Both corners below are approached at the same 75 mph. Keying on vEgo cannot separate them."""
  c = _controller(low=90, high=100)
  ramp = c._factor_for_corner(20 * MPH)
  sweeper = c._factor_for_corner(55 * MPH)
  assert ramp < sweeper, (
    f"ramp {ramp:.2f} and sweeper {sweeper:.2f} came out the same way -- the blend is not keyed on "
    f"the corner speed, so the two cases cannot be tuned apart")


def test_a_uniform_pair_reduces_to_the_old_single_factor():
  """Setting both ends the same must behave exactly as the one global factor did, at every speed."""
  c = _controller(low=85, high=85)
  for mph in (10, 25, 37, 50, 70):
    assert abs(c._factor_for_corner(mph * MPH) - 0.85) < 1e-6, f"drifted at {mph} mph"
