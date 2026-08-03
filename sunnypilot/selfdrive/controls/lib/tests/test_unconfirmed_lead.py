"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

BluePilot: behavioural tests for the radar-blind lead / model-stop detector.

These cover the two things that actually matter for safety here: that the detector never requests
below Ford's ACC floor, and that its evidence gates cannot be short-circuited. Both of the bugs
these were written to catch were real -- evidence counters being reset every cycle by an unrelated
code path, so a trigger could never accumulate.
"""

from types import SimpleNamespace as NS

from cereal import custom
from openpilot.sunnypilot.selfdrive.controls.lib.unconfirmed_lead import (
  UnconfirmedLeadDetector, ACC_FLOOR_MS, MIN_V_EGO_MS,
)

State = custom.LongitudinalPlanSP.UnconfirmedLead.State
Trigger = custom.LongitudinalPlanSP.UnconfirmedLead.Trigger

CRUISE_MS = 29.0  # ~65 mph
TRAJ = [25.0] * 33


class FakeEvents:
  def __init__(self):
    self.fired = []

  def add(self, e):
    self.fired.append(e)


def make_sm(d_rel=100., v_rel=-29., prob=0.9, status=True, radar=False,
            should_stop=False, accel=0., v_ego=CRUISE_MS, brake=False, d_path=0.2):
  return {
    'carState': NS(vEgo=v_ego, brakePressed=brake),
    'radarState': NS(leadOne=NS(dRel=d_rel, vRel=v_rel, modelProb=prob, status=status,
                                radar=radar, dPath=d_path)),
    'modelV2': NS(action=NS(shouldStop=should_stop, desiredAcceleration=accel)),
  }


def run(det, ev, frames, **kw):
  for i in range(frames):
    sm = make_sm(**{k: (v(i) if callable(v) else v) for k, v in kw.items()})
    det.update(sm, TRAJ, CRUISE_MS, True, ev)


class TestUnconfirmedLead:
  def test_persistence_alone_does_not_trigger(self):
    # A lead held at constant range is the bridge/overpass signature: no closing sweep.
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 60, d_rel=100.)
    assert det.state != State.active

  def test_persistence_plus_range_sweep_triggers(self):
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 60, d_rel=lambda i: 100. - i * 0.5)
    assert det.state == State.active
    assert det.trigger == Trigger.visionLead
    assert ev.fired, "alert must fire at trigger, not at the floor"
    assert det.restore_set_speed == CRUISE_MS

  def test_radar_acquisition_releases_into_restore(self):
    # The acquired lead must be MOVING. Ford only follows what it tracks, and its manual puts that
    # bound at 6 mph -- releasing on a stationary radar return hands the car back to a system that
    # filters stationary returns out, and ACC then accelerates toward it.
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 60, d_rel=lambda i: 100. - i * 0.5)
    det.update(make_sm(d_rel=70., radar=True, v_rel=-5.), TRAJ, CRUISE_MS, True, ev)
    assert det.state == State.restoring, "radar taking over is the expected good outcome"
    det.update(make_sm(), TRAJ, CRUISE_MS, True, ev)
    assert det.state == State.inactive

  def test_lead_beyond_max_distance_rejected(self):
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 60, d_rel=lambda i: 150. - i * 0.5)
    assert det.state != State.active

  def test_model_stop_without_lead_triggers(self):
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 40, status=False, should_stop=True, accel=-1.5)
    assert det.state == State.active
    assert det.trigger == Trigger.modelStop
    assert det.v_target >= ACC_FLOOR_MS

  def test_model_stop_never_requests_below_floor(self):
    # Ford ACC cannot hold below 20 mph. Requesting lower is meaningless, so it must clamp.
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 40, status=False, should_stop=True, accel=-8.)
    assert det.v_target == ACC_FLOOR_MS

  def test_model_stop_releases_when_model_lets_go(self):
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 40, status=False, should_stop=True, accel=-1.5)
    run(det, ev, 15, status=False, should_stop=False)
    assert det.state in (State.restoring, State.inactive)

  def test_radar_confirmed_moving_lead_suppresses_model_stop(self):
    # Ford ACC handles what it actually tracks, so there is nothing to add -- but only while the
    # lead is moving fast enough for Ford to track it.
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 60, status=True, radar=True, v_rel=-5., should_stop=True, accel=-2.)
    assert det.state == State.inactive


class TestFordDoesNotTrackStationaryReturns:
  """openpilot reads the Delphi MRR's raw detections with no stationary rejection, so a stopped
  car arrives here as a radar-confirmed lead. Ford's ACC consumes the same sensor but filters
  zero-Doppler returns -- its manual says ACC "may not detect stationary or slow moving vehicles
  below 6 mph (10 km/h)". Treating radar confirmation as "Ford has it" disabled this feature in
  exactly the case it exists for: the stopped car it will otherwise drive into."""

  def test_stopped_radar_confirmed_lead_still_triggers(self):
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 60, radar=True, d_rel=lambda i: 100. - i * 0.5)
    assert det.state == State.active, "a stopped lead was ignored because radar could see it"
    assert det.trigger == Trigger.visionLead

  def test_stopped_radar_return_does_not_release_an_active_trigger(self):
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 60, d_rel=lambda i: 100. - i * 0.5)
    assert det.state == State.active
    det.update(make_sm(d_rel=70., radar=True), TRAJ, CRUISE_MS, True, ev)
    assert det.state == State.active, "released to Ford for a lead Ford is not tracking"

  def test_confident_stopped_lead_triggers_on_a_short_sweep(self):
    """Camera confirmation replaces most of the kinematic evidence. radard will not publish a lead
    at all unless the model's lead probability clears 0.5, so a high-confidence lead is the camera
    saying "vehicle", not "bridge"."""
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    # ~5 m of closing in total -- nowhere near the 15 m a low-confidence lead must show.
    run(det, ev, 30, prob=0.95, d_rel=lambda i: 100. - i * 0.18)
    assert det.state == State.active
    assert det.trigger == Trigger.visionLead

  def test_a_low_confidence_stopped_lead_still_needs_the_full_sweep(self):
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 30, prob=0.7, d_rel=lambda i: 100. - i * 0.18)
    assert det.state != State.active, "low model confidence skipped the range sweep"

  def test_confidence_does_not_excuse_a_target_that_never_closes(self):
    """The bridge signature: the model is sure, but the range does not shrink."""
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 60, prob=0.99, d_rel=100.)
    assert det.state != State.active

  def test_a_moving_radar_lead_is_still_left_to_ford(self):
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 60, radar=True, v_rel=-5., d_rel=lambda i: 100. - i * 0.5)
    assert det.state == State.inactive

  def test_speed_gate_blocks_low_speed_trigger(self):
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 40, status=False, should_stop=True, accel=-2., v_ego=MIN_V_EGO_MS - 2.)
    assert det.state == State.inactive

  def test_disengage_clears_state_and_pending_restore(self):
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 60, d_rel=lambda i: 100. - i * 0.5)
    det.update(make_sm(d_rel=70.), TRAJ, CRUISE_MS, False, ev)
    assert det.state == State.inactive
    assert det.restore_set_speed == 0.0

  def test_off_path_lead_rejected(self):
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 60, d_rel=lambda i: 100. - i * 0.5, d_path=3.0)
    assert det.state != State.active

  def test_low_model_prob_rejected(self):
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 60, d_rel=lambda i: 100. - i * 0.5, prob=0.55)
    assert det.state != State.active
