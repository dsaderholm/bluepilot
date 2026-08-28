"""FusionPilot: defense 4 claims the camera has not LOOKED yet. Prove it is actually blind.

`_camera_has_not_seen_it` suppresses a highway corner that sits beyond the model's horizon, on the
argument that silence from a camera which cannot see the corner is not evidence. Correct -- but it
was deciding "cannot see" from DISTANCE ALONE, so it kept firing while the model was actively
describing the corner.

Measured, SLC -> Yosemite route 000003d1, t+1988. 100% of the vetoed frames in that window were
cameraNotSeen, with the model's own prediction climbing right through them:

    t+1987.5   84.5 mph   VETOED   maxPred 2.48
    t+1988.5   84.5 mph   VETOED   maxPred 3.60      <- the camera plainly sees the corner
    t+1989.5   82.5 mph   acts                       <- ~2 s late
    t+1991.0   79.2 mph            latAcc 5.53       <- peak 5.91 over the event

For scale on this car: openpilot alone has never exceeded 3.19, his hands-on max is 4.20, and the
exit that nearly put him off the road was 5.20.

The gate makes the car SLOWER TO SLOW DOWN, which this fork's own rule says is never acceptable, so
it needs the narrowest reason to fire. `MODEL_DISAGREE_LAT_ACC` is already this file's definition of
"the camera sees no curve at all" and is reused rather than inventing a second threshold.
"""
from __future__ import annotations

from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.map_controller import (
  MODEL_DISAGREE_LAT_ACC,
  MODEL_HORIZON_HIGH_SPEED_S,
  SmartCruiseControlMap,
  _MAP_FACTOR_V_BP,
)

HIGHWAY_CORNER = _MAP_FACTOR_V_BP[1] + 5.0     # a corner defense 4 is allowed to act on
RAMP_CORNER = _MAP_FACTOR_V_BP[1] - 5.0        # below the highway line -- defense 4 never applies
V_EGO = 37.5                                   # ~84 mph, the speed of the measured event


def _scc(v_target: float, model_lat_acc: float, v_ego: float = V_EGO) -> SmartCruiseControlMap:
  s = SmartCruiseControlMap.__new__(SmartCruiseControlMap)
  s.v_target = v_target
  s.v_ego = v_ego
  s.model_lat_acc = model_lat_acc
  return s


def _far() -> float:
  """Comfortably beyond the model's reach at V_EGO, so the distance test alone would veto."""
  return V_EGO * MODEL_HORIZON_HIGH_SPEED_S + 100.0


def _near() -> float:
  return V_EGO * MODEL_HORIZON_HIGH_SPEED_S - 100.0


def test_a_model_predicting_a_curve_is_not_a_blind_camera():
  """THE FIX, and the measured event. Corner beyond the horizon, but the model is reporting
  3.6 m/s^2 -- it has looked and found something, so 'has not seen it yet' is simply false."""
  scc = _scc(HIGHWAY_CORNER, model_lat_acc=3.6)
  assert not scc._camera_has_not_seen_it(_far())


def test_a_genuinely_silent_camera_still_suppresses():
  """The original argument is untouched. Below the threshold the camera really is quiet, and a
  far-off corner it cannot see must still wait -- otherwise this reopens route 00000365, where the
  map walked the set speed 79 -> 64 with nothing able to question it."""
  scc = _scc(HIGHWAY_CORNER, model_lat_acc=0.0)
  assert scc._camera_has_not_seen_it(_far())


def test_the_threshold_is_the_files_own_definition_of_seeing_nothing():
  """Reuses MODEL_DISAGREE_LAT_ACC rather than a second number. Just under still suppresses; at the
  threshold it does not."""
  assert _scc(HIGHWAY_CORNER, MODEL_DISAGREE_LAT_ACC - 0.01)._camera_has_not_seen_it(_far())
  assert not _scc(HIGHWAY_CORNER, MODEL_DISAGREE_LAT_ACC)._camera_has_not_seen_it(_far())


def test_a_corner_inside_the_horizon_was_never_suppressed():
  """Unchanged behavior: within reach, the camera has had its chance and defenses 2 and 3 answer."""
  assert not _scc(HIGHWAY_CORNER, 0.0)._camera_has_not_seen_it(_near())


def test_a_ramp_like_corner_is_still_exempt():
  """Ramps stay exempt whatever the model says -- on an exit the model predicts straight down the
  highway, so waiting there delays the one case that already has too little room."""
  assert not _scc(RAMP_CORNER, 0.0)._camera_has_not_seen_it(_far())
  assert not _scc(RAMP_CORNER, 3.6)._camera_has_not_seen_it(_far())


def test_an_idle_map_never_reads_as_unseen():
  """`target_distance` is inf and `v_target` unset when the map has nothing to say. Both
  comparisons are True against inf, so without the finite guard this returns True on most of a
  drive -- a state that flips continuously while longitudinal is engaged."""
  assert not _scc(HIGHWAY_CORNER, 0.0)._camera_has_not_seen_it(float("inf"))
  assert not _scc(float("inf"), 0.0)._camera_has_not_seen_it(_far())


def test_the_fix_can_only_ever_remove_a_suppression():
  """The safety argument for landing this on one event: for every input where the old gate said
  False, the new one still says False. It can make the car slow EARLIER and never later."""
  for v_target in (RAMP_CORNER, HIGHWAY_CORNER):
    for dist in (_near(), _far(), float("inf")):
      for lat in (0.0, MODEL_DISAGREE_LAT_ACC, 3.6):
        blind = _scc(v_target, 0.0)._camera_has_not_seen_it(dist)
        seeing = _scc(v_target, lat)._camera_has_not_seen_it(dist)
        assert blind or not seeing, (v_target, dist, lat)
