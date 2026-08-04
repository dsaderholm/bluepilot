"""BluePilot: the camera's veto over a map curve that is not there.

Both curve controllers feed a min() in the planner, so either can LOWER the target and neither can
veto the other -- SCC-Vision seeing no curve is silence, and min() ignores silence. Bad OSM
geometry therefore slows the car with nothing able to stop it. This is the missing "no".

The failure mode to guard against is the OPPOSITE one: vetoing beyond camera range would disable
the only thing SCC-Map is for, which is seeing around a bend the camera cannot.
"""
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.map_controller import (
  SmartCruiseControlMap, MODEL_HORIZON_S, MODEL_DISAGREE_LAT_ACC,
)

V_EGO = 31.3          # ~70 mph
HORIZON = V_EGO * MODEL_HORIZON_S   # ~313 m
STRAIGHT = MODEL_DISAGREE_LAT_ACC / 2      # camera sees a straight road
CURVING = MODEL_DISAGREE_LAT_ACC * 3       # camera agrees there is a bend


def controller(model_lat_acc: float, v_ego: float = V_EGO) -> SmartCruiseControlMap:
  scc = SmartCruiseControlMap()
  scc.v_ego = v_ego
  scc.model_lat_acc = model_lat_acc
  return scc


class TestWithinCameraRange:
  def test_straight_road_vetoes_the_map(self):
    """The whole point: map says curve, camera says straight, close enough to be sure."""
    assert controller(STRAIGHT)._model_disagrees(HORIZON * 0.5)

  def test_camera_agreeing_does_not_veto(self):
    assert not controller(CURVING)._model_disagrees(HORIZON * 0.5)

  def test_right_at_the_threshold_does_not_veto(self):
    """Strictly below, so a target sitting exactly on the line is left alone."""
    assert not controller(MODEL_DISAGREE_LAT_ACC)._model_disagrees(HORIZON * 0.5)


class TestBeyondCameraRange:
  """Silence past the horizon means "not yet", never "nothing there"."""

  def test_far_curve_is_never_vetoed(self):
    assert not controller(STRAIGHT)._model_disagrees(HORIZON * 2)

  def test_just_past_the_horizon_is_not_vetoed(self):
    assert not controller(STRAIGHT)._model_disagrees(HORIZON + 1)

  def test_just_inside_the_horizon_is_vetoed(self):
    assert controller(STRAIGHT)._model_disagrees(HORIZON - 1)

  def test_the_ramp_case_survives(self):
    """A 500 m trigger at -0.8 m/s^2 is the reason SCC-Map exists. At 70 mph the camera reaches
    ~313 m, so it must not be allowed to overrule a curve it physically cannot see."""
    assert not controller(STRAIGHT)._model_disagrees(500.0)


class TestDegenerateInputs:
  def test_stopped_car_never_vetoes(self):
    """Horizon collapses to zero at a standstill; dividing the world into "everything is beyond
    range" is the safe reading, and vetoing everything would be the unsafe one."""
    assert not controller(STRAIGHT, v_ego=0.0)._model_disagrees(10.0)

  def test_no_target_never_vetoes(self):
    """update_calculations leaves target_distance at infinity when nothing was selected."""
    assert not controller(STRAIGHT)._model_disagrees(float('inf'))
