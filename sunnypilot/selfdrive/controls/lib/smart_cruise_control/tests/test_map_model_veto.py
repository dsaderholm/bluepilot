"""FusionPilot: the camera's veto over a map curve that is not there.

Both curve controllers feed a min() in the planner, so either can LOWER the target and neither can
veto the other -- SCC-Vision seeing no curve is silence, and min() ignores silence. Bad OSM
geometry therefore slows the car with nothing able to stop it. This is the missing "no".

The failure mode to guard against is the OPPOSITE one: vetoing beyond camera range would disable
the only thing SCC-Map is for, which is seeing around a bend the camera cannot.
"""
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.map_controller import (
  SmartCruiseControlMap, MODEL_HORIZON_S, MODEL_HORIZON_HIGH_SPEED_S, MODEL_DISAGREE_LAT_ACC,
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


class TestAHighwayBendGetsTheModelsRealReach:
  """Route 0000033c t+142, 2026-08-11: "the one curve early in the drive went really slow".

  The map committed to 52 mph at 74 mph and the set speed fell to 52 in four seconds, on a road
  measuring 0.00-0.24 m/s^2. At the committing frame the model's predicted lateral acceleration was
  0.24 -- under MODEL_DISAGREE_LAT_ACC, so the camera had already answered. The veto never got to
  look: the corner was ~232 m out against a 132 m gate.

  The gate was mismatched to what it guards. max_pred_lat_acc is a percentile over the whole modelV2
  plan, ~330 m at that speed, so 4 s bounded a number that already summarized far more road.

  The bound still applies to RAMPS, and that is the safety argument rather than a compromise: on an
  exit the model predicts the path it expects to drive, straight down the highway, so a ramp's
  curvature may never enter the plan until the car is on it. That blindness belongs to slow corners.
  """

  # The measured event: 74 mph, corner 232 m out, camera seeing 0.24.
  V_HIGHWAY = 33.1
  DIST = 232.0

  def _at(self, v_target_mph, model_lat_acc, v_ego=V_HIGHWAY, dist=DIST):
    scc = controller(model_lat_acc, v_ego=v_ego)
    scc.v_target = v_target_mph * 0.44704
    return scc._model_disagrees(dist)

  def test_the_phantom_highway_corner_is_now_vetoed(self):
    assert self._at(52, 0.24), (
      "a 52 mph mapped corner 232 m out, with the camera seeing 0.24, was still not vetoed -- "
      "THE REPORTED BUG")

  def test_a_highway_corner_the_camera_confirms_is_left_alone(self):
    """CORROBORATION IS A NUMBER, not "the camera sees something".

    For a 52 mph corner to be real it must have curvature 2.0 / 23.2^2 = 0.0037, which at 74 mph
    predicts about 4.1 m/s^2. That is what agreement looks like. The old fixture here used 1.2 -- three
    times the "is there a curve at all" threshold, but nowhere near enough to justify 52 mph -- and it
    passed only because nothing was checking the magnitude against what the map claimed.
    """
    assert not self._at(52, 4.1), "vetoed a bend the camera agrees is there"

  def test_a_gentler_bend_than_the_map_claims_is_vetoed(self):
    """Route 00000348 t+1510, 2026-08-11: "it still went down to 50 from 80 for a curve on the
    freeway, which was a little ridiculous."

    The map demanded 50 mph at 79 mph with predicted lateral acceleration 1.22 -- over the "no curve
    at all" threshold, so the absolute test says nothing -- while SCC-Vision's own target across the
    same stretch was 84-98 mph. He overrode and held 65-70 comfortably.

    1.22 at 79 mph implies the bend is fine at about 100 mph, so 50 is not describing this road.
    """
    assert self._at(50, 1.22, v_ego=35.4), (
      "the map asked for 50 mph on a bend the camera says is fine at ~100 -- THE REPORTED BUG")

  def test_a_ramp_is_never_vetoed_by_the_relative_test(self):
    """The relative test must not reach ramps. At range the model may not have the ramp in its plan,
    so a low prediction there is blindness, and a 25 mph ramp would be vetoed on every approach."""
    assert not self._at(25, 1.22, v_ego=35.4, dist=60.0)

  def test_a_ramp_keeps_the_conservative_bound(self):
    """The case SCC-Map exists for. A 25 mph ramp at the same distance must NOT be vetoed, because
    the model has no reason to have the ramp in its plan yet."""
    assert not self._at(25, 0.24), (
      "a real exit ramp was vetoed on camera silence -- this disables the one thing SCC-Map is for")

  def test_a_highway_corner_beyond_even_the_wider_reach_is_left_alone(self):
    """Silence outside the model's actual plan is still not evidence."""
    assert not self._at(52, 0.24, dist=self.V_HIGHWAY * 12.0)


class TestAHighwayCornerTheCameraCannotSeeYet:
  """Measured on route 00000365, 2026-08-12.

  The map commanded 50 mph on an I-215 sweeper and walked the set speed 79 -> 64 with nothing
  questioning it. Both vetoes were unreachable: SCC-Map publishes the corner speed exactly when
  braking must BEGIN, and at 0.8 m/s^2 that is 467 m out, against a 353 m model horizon.

  The dead band was wide, not marginal. The veto can only be reached when the braking distance fits
  inside the horizon, so at 79 mph it protected corners of 58 mph and faster, while being disabled
  below 45 mph as ramp-like -- leaving 45-58 mph, the band that produces the biggest slowdowns,
  with no protection at all.
  """

  V_EGO_79 = 35.3           # m/s, 79 mph -- the measured speed
  CORNER_50 = 22.35         # m/s, 50 mph -- what the map asked for
  RAMP_30 = 13.4            # m/s, 30 mph -- below the 45 mph line

  def _scc(self, v_target: float, v_ego: float = V_EGO_79) -> SmartCruiseControlMap:
    scc = SmartCruiseControlMap()
    scc.v_ego = v_ego
    scc.v_target = v_target
    return scc

  def test_the_measured_event_is_now_suppressed(self):
    """467 m of braking distance against a 353 m horizon: the camera was never asked."""
    scc = self._scc(self.CORNER_50)
    assert scc._camera_has_not_seen_it(467.0)

  def test_the_same_corner_is_allowed_once_it_comes_into_view(self):
    """Suppression is 'not yet', not 'never'. Inside the horizon the normal vetoes decide."""
    scc = self._scc(self.CORNER_50)
    assert not scc._camera_has_not_seen_it(300.0)

  def test_a_corner_exactly_at_the_horizon_is_allowed(self):
    """Strictly beyond, matching every other boundary in this file."""
    scc = self._scc(self.CORNER_50)
    assert not scc._camera_has_not_seen_it(self.V_EGO_79 * MODEL_HORIZON_HIGH_SPEED_S)

  def test_a_ramp_beyond_the_horizon_is_NOT_suppressed(self):
    """The exit case. On a ramp the model plans straight down the highway, so its silence is
    blindness rather than evidence -- and the exit already has too little room to brake in."""
    scc = self._scc(self.RAMP_30)
    assert not scc._camera_has_not_seen_it(1000.0)

  def test_a_stopped_car_is_not_suppressed(self):
    """horizon <= 0 must not read as 'everything is out of range'."""
    scc = self._scc(self.CORNER_50, v_ego=0.0)
    assert not scc._camera_has_not_seen_it(467.0)

  def test_the_58_mph_boundary_that_defined_the_dead_band(self):
    """Corners fast enough to brake for inside the horizon were always protected; the fix is for
    the ones below that line, which were not."""
    inside = self._scc(26.5)          # ~59 mph: (v1^2-v2^2)/1.6 = 340 m, fits in 353 m
    assert not inside._camera_has_not_seen_it(340.0)
    outside = self._scc(self.CORNER_50)
    assert outside._camera_has_not_seen_it(467.0)
