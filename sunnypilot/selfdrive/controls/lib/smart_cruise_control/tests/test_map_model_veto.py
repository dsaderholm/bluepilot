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


# ---------------------------------------------------------------------------------------------
# FusionPilot 2026-08-26: A LOW CORNER SPEED IS ONLY A RAMP IF WE ARRIVE AT HIGHWAY SPEED.
#
# Four phantom corners and one real one, measured on routes 000003c8 and 000003c9. The radius is
# the map's own claim inverted against the radius the car then actually drove:
#
#     t+1074  v_ego 21 mph  target 13  R_map 19 m  R_drove 422 m   22.3x too tight
#     t+1085  v_ego 33 mph  target 13  R_map 19 m  R_drove 422 m   22.3x
#     t+ 289  v_ego 29 mph  target 12  R_map 16 m  R_drove 306 m   19.0x
#     t+ 300  v_ego 25 mph  target 14  R_map 22 m  R_drove 442 m   20.1x
#     t+ 785  v_ego 71 mph  target 21  R_map 49 m  R_drove 122 m   map agrees   <- REAL
#
# Each phantom held the target at 12-14 mph for 6-7 seconds on a road with no corner in it, and the
# recovery afterwards is what he reported as "dropping and increasing by 5 mph at a time" -- ICBM
# holds the button out-of-band, so the dash moves 5 at a time coming back.
#
# The veto could not fire on any of them, and not by a narrow margin. Worked, t+289: braking
# 29 -> 12 mph at 0.8 m/s^2 is 87 m, against a ramp horizon of 29 mph x 4 s = 52 m. The distance
# gate returned False before the camera was consulted at all.
# ---------------------------------------------------------------------------------------------

SLOW_EGO = 13.0        # ~29 mph, the measured onset of the c9 phantom
RAMP_EGO = 31.3        # ~70 mph, a genuine exit approach
LOW_TARGET = 5.4       # ~12 mph, what the map demanded
HIGH_TARGET = 24.0     # ~54 mph, a highway bend


def _c(model_lat_acc: float, v_ego: float, v_target: float) -> SmartCruiseControlMap:
  scc = SmartCruiseControlMap()
  scc.v_ego = v_ego
  scc.model_lat_acc = model_lat_acc
  scc.v_target = v_target
  return scc


class TestASlowCornerOnASlowRoadIsNotARamp:
  def test_the_camera_is_now_asked_at_all(self):
    """87 m is the real braking distance for his t+289 event. Under the ramp horizon (52 m) the gate
    refused to look; under the model's real reach (130 m) it looks and sees a straight road."""
    assert _c(STRAIGHT, SLOW_EGO, LOW_TARGET)._model_disagrees(87.0)

  def test_and_it_still_defers_to_a_camera_THAT_AGREES(self):
    """The veto removes the MAP's contribution only when the camera CONTRADICTS it.

    THIS TEST WAS WRONG WHEN FIRST WRITTEN AND THE CODE CAUGHT IT. It used `CURVING` (1.2 m/s^2)
    to mean "the camera sees the bend" -- but 1.2 at 13 m/s implies a road safely takeable at about
    37 mph, so a map demanding 12 mph there is a THREEFOLD disagreement and the relative veto is
    right to fire. `CURVING` clears the "is there a curve at all" test; it does not agree with a
    12 mph corner.

    For the camera to genuinely agree it has to predict what a 12 mph corner PRODUCES at 29 mph:

        v_target <= implied_ok_v * 0.75,  implied_ok_v = v_ego * sqrt(2.0 / lat)
        -> lat >= 6.52 m/s^2,  which is a 26 m radius -- which is what a 12 mph corner is

    So AGREEING is 7.0, not 1.2. A test that asserts deference has to hand the camera a reading
    consistent with the map's own claim, or it is asserting that the veto never fires."""
    AGREEING = 7.0
    assert not _c(AGREEING, SLOW_EGO, LOW_TARGET)._model_disagrees(87.0)

  def test_a_MILD_bend_under_a_severe_map_claim_still_vetoes(self):
    """The other half, and the one that catches route 000003c9 t+289: the camera saw 0.46-0.68 --
    real curvature, clearing defense 2 -- while the map demanded a corner twenty times tighter."""
    assert _c(0.68, SLOW_EGO, LOW_TARGET)._model_disagrees(87.0)


class TestTheRampExemptionSurvives:
  """*"RAMPS ARE DELIBERATELY EXEMPT AND MUST STAY THAT WAY."* On an exit the model predicts the
  path it expects to drive -- straight down the highway -- so a ramp's curvature may never enter its
  plan. Vetoing there would delay the one case that already has too little room, which is his oldest
  standing complaint. This is the half of the change that must NOT have moved."""

  def test_a_low_corner_approached_at_highway_speed_keeps_the_short_horizon(self):
    """313 m is inside the 10 s reach and far outside the 4 s ramp horizon (125 m). If the exemption
    had been dropped, the camera's silence here would veto a real ramp."""
    assert not _c(STRAIGHT, RAMP_EGO, LOW_TARGET)._model_disagrees(RAMP_EGO * MODEL_HORIZON_S * 1.5)

  def test_the_ramp_horizon_still_applies_close_in(self):
    """A phantom curve right in front of the car is still caught on a ramp approach -- the exemption
    bounds the veto's REACH, it does not switch it off."""
    assert _c(STRAIGHT, RAMP_EGO, LOW_TARGET)._model_disagrees(RAMP_EGO * MODEL_HORIZON_S * 0.5)


class TestTheHighwayCaseIsUntouched:
  def test_a_highway_bend_still_gets_the_full_reach(self):
    d = RAMP_EGO * MODEL_HORIZON_HIGH_SPEED_S * 0.8
    assert _c(STRAIGHT, RAMP_EGO, HIGH_TARGET)._model_disagrees(d)

  def test_beyond_the_full_reach_nothing_is_vetoed_at_any_speed(self):
    """Past the model's own plan there is no opinion to have, and silence must not read as a no."""
    for v_ego, v_target in ((SLOW_EGO, LOW_TARGET), (RAMP_EGO, HIGH_TARGET)):
      d = v_ego * MODEL_HORIZON_HIGH_SPEED_S * 1.2
      assert not _c(STRAIGHT, v_ego, v_target)._model_disagrees(d)


def test_the_gate_uses_the_one_shared_45_mph_definition():
  """Both halves key on _MAP_FACTOR_V_BP[1], the same constant the corner-speed factors use, so the
  four defenses cannot drift apart. A duplicated literal here is how that starts."""
  import inspect
  src = inspect.getsource(SmartCruiseControlMap._model_disagrees)
  assert src.count("_MAP_FACTOR_V_BP[1]") >= 2, "the ramp test duplicated the 45 mph line"
  assert "self.v_ego >= _MAP_FACTOR_V_BP[1]" in src, "the ramp test no longer looks at v_ego"
