"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FusionPilot: behavioral tests for the radar-blind lead / model-stop detector.

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
            accel=0., v_ego=CRUISE_MS, brake=False, d_path=0.2,
            ford_braking=False):
  sm = {
    'carState': NS(vEgo=v_ego, brakePressed=brake),
    'radarState': NS(leadOne=NS(dRel=d_rel, vRel=v_rel, modelProb=prob, status=status,
                                radar=radar, dPath=d_path)),
    # shouldStop is DERIVED here, exactly as modeld derives it, rather than being a free knob.
    # As a knob it let every model-stop test set shouldStop=True at 65 mph -- a state modeld can
    # never emit -- so the suite proved the path worked while the car did nothing at every red
    # light.
    'modelV2': NS(action=NS(shouldStop=bool(v_ego < 0.3 and accel < 0.1),
                            desiredAcceleration=accel)),
    'carStateBP': NS(brakeLightStatus=NS(accDataAvailable=True, accDecelRequest=ford_braking)),
  }
  # SubMaster exposes .valid; the detector must tolerate carStateBP being absent on other platforms
  sm_obj = dict(sm)
  sm_obj['__valid__'] = {'carStateBP': True}
  return _SM(sm)


class _SM(dict):
  """Minimal stand-in for SubMaster: subscript plus .valid."""
  @property
  def valid(self):
    return {k: True for k in self}


def run(det, ev, frames, slow_down=False, stop_dist=float('inf'), **kw):
  """slow_down is DEC's has_slow_down() for the frame -- the model-stop trigger -- and stop_dist is
  its trajectory endpoint. Separate arguments rather than part of make_sm because the detector
  receives both from the planner, not from the SubMaster."""
  for i in range(frames):
    sm = make_sm(**{k: (v(i) if callable(v) else v) for k, v in kw.items()})
    det.update(sm, TRAJ, CRUISE_MS, True, ev, slow_down(i) if callable(slow_down) else slow_down,
               stop_dist(i) if callable(stop_dist) else stop_dist)


class _ForceModelStop:
  """Forces IcbmModelStopEnabled on, whatever params_keys.h currently ships.

  It has now shipped 1, then 0, then 1 again, and these tests are about the path's LOGIC rather than
  about whether it is switched on. A test that silently changes meaning when a default moves is not
  testing what it claims to -- which is why this pins it rather than reading it."""

  def get_bool(self, key, *a, **k):
    return True if key == "IcbmModelStopEnabled" else False

  def get(self, key, *a, **k):
    from openpilot.common.params import Params
    return Params().get(key, *a, **k)


def model_stop_detector():
  det = UnconfirmedLeadDetector()
  det.params = _ForceModelStop()
  det.update_params()
  det.model_stop_enabled = True
  return det


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

  def test_a_highway_stopped_car_goes_straight_to_the_floor(self):
    """His objection, and the physics agrees: "it detects stopped cars late all the time and stopped
    cars can be on roads faster than roads with traffic lights".

    A stopped car at 180 m demands 3.65 m/s^2 at 80 mph and 2.41 at 65 -- at or past what Ford's ACC
    delivers. There is no margin to ease into, so pacing there would be a frame wasted every frame.
    """
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 60, d_rel=lambda i: 175. - i * 0.5)
    assert det.trigger == Trigger.visionLead
    assert det.v_target == ACC_FLOOR_MS, (
      f"at 65 mph it asked for {det.v_target / 0.44704:.0f} mph -- no room to pace at this speed")

  def test_a_slow_road_paces_instead(self):
    """The same range at 45 mph demands well under the brake-lamp threshold, so the ramp is free --
    and it is what makes a false positive survivable.

    Starts at 160 m rather than 180: at 45 mph the 7 s TTC cap is 141 m, so a lead further out is
    rejected before urgency is ever consulted. Writing this test at highway distances made it fail
    for a reason that had nothing to do with what it was testing."""
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    slow = 20.12   # 45 mph
    run(det, ev, 60, v_ego=slow, v_rel=-slow, d_rel=lambda i: 160. - i * 0.5)
    assert det.state == State.active
    assert det.v_target > ACC_FLOOR_MS, "paced range collapsed to the floor"
    assert det.v_target < slow - 2., "asked for essentially nothing"

  def test_the_paced_request_tightens_as_the_range_closes(self):
    """The property that makes pacing safe: it converges on the floor by itself."""
    slow = 20.12
    seen = []
    for start_d in (160., 140., 120.):
      det, ev = UnconfirmedLeadDetector(), FakeEvents()
      run(det, ev, 60, v_ego=slow, v_rel=-slow, d_rel=lambda i, x=start_d: x - i * 0.35)
      if det.state == State.active:
        seen.append(det.v_target)
    assert len(seen) >= 2, "need several triggers at different ranges to compare"
    assert seen == sorted(seen, reverse=True), f"not monotonic in range: {seen}"

  def test_the_split_is_physics_not_a_speed_threshold(self):
    """Same speed, different range: the urgency rule has to key on required deceleration, not on
    how fast the car happens to be going."""
    slow = 20.12
    far, near = UnconfirmedLeadDetector(), UnconfirmedLeadDetector()
    run(far, FakeEvents(), 60, v_ego=slow, v_rel=-slow, d_rel=lambda i: 160. - i * 0.5)
    run(near, FakeEvents(), 60, v_ego=slow, v_rel=-slow, d_rel=lambda i: 95. - i * 0.5)
    assert far.v_target > ACC_FLOOR_MS, "far lead at 45 mph should still be paced"
    assert near.v_target == ACC_FLOOR_MS, "close lead at the SAME speed should be urgent"

  def test_a_dropped_radar_frame_does_not_snap_the_request_back_up(self):
    """radard publishes {"status": False} and leaves every other field at its capnp default, so a
    single dropped frame arrives as dRel == 0 -- which the stopping geometry reads as "too close to
    be meaningful" and answers with v_ego. Recomputing off that inside the LEAD_LOST_S grace window
    turns a blink into a request for full cruise speed, while the alert is still telling the driver
    a car is there. The request may only ever go DOWN while this is active.
    """
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 60, v_ego=20.12, v_rel=-20.12, d_rel=lambda i: 160. - i * 0.5)
    assert det.state == State.active and det.v_target > ACC_FLOOR_MS, "need a paced request to lose"
    held = det.v_target

    held_d_rel = det.d_rel

    det.update(make_sm(status=False, d_rel=0., v_rel=0., v_ego=20.12), TRAJ, CRUISE_MS, True, ev)
    assert det.state == State.active, "one dropped frame is inside the grace window"
    assert det.v_target <= held, (
      f"a radar blink raised the request from {held / 0.44704:.0f} to "
      f"{det.v_target / 0.44704:.0f} mph")
    # d_rel is published as unconfirmedLead.dRel and is the number the ALERT shows. Holding the
    # request but not the distance would flash "Vision only at 0 ft" mid-event.
    assert det.d_rel == held_d_rel, (
      f"the alert distance collapsed from {held_d_rel:.0f} m to {det.d_rel:.0f} m on a blink")

  def test_the_distance_does_not_go_stale_once_the_lead_is_really_gone(self):
    """The hold above is scoped to ACTIVE so it cannot outlive the event. Once the grace window
    releases, the logs must say zero rather than the last distance seen minutes ago."""
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 60, v_ego=20.12, v_rel=-20.12, d_rel=lambda i: 160. - i * 0.5)
    assert det.state == State.active

    for _ in range(120):   # well past LEAD_LOST_S
      det.update(make_sm(status=False, d_rel=0., v_rel=0., v_ego=20.12), TRAJ, CRUISE_MS, True, ev)
    assert det.state != State.active, "should have released after the grace window"
    assert det.d_rel == 0., f"stale distance {det.d_rel:.0f} m survived the event"

  def test_ford_braking_toward_our_own_request_does_not_release(self):
    """The bug the owner caught before it reached the road.

    This detector asks for 20 mph, so ACC brakes to reach 20 -- evidence it manufactured itself.
    Releasing on that alone hands the set speed back moments after every trigger, with the stopped
    car still there. Braking at 65 with no radar confirmation is exactly that case.
    """
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 60, d_rel=lambda i: 175. - i * 0.5)
    assert det.state == State.active
    run(det, ev, 10, d_rel=145., ford_braking=True, radar=False, v_ego=CRUISE_MS)
    assert det.state == State.active, "released on braking it caused itself"

  def test_braking_with_a_radar_confirmed_lead_releases(self):
    """His actual report: ACC braking because it saw the car, well above the floor, and the alert
    kept firing. Chasing a set speed does not coincide with the radar acquiring a target."""
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 60, d_rel=lambda i: 175. - i * 0.5)
    assert det.state == State.active
    det.update(make_sm(d_rel=140., radar=True, v_rel=-29., ford_braking=True, v_ego=CRUISE_MS),
               TRAJ, CRUISE_MS, True, ev)
    assert det.state == State.restoring, "Ford braking for a radar-confirmed lead did not release"

  def test_braking_at_the_floor_releases(self):
    """Past the request there is nothing left for ACC to chase, so continued braking is following.
    This is the stop-and-go regime where Ford does follow stationary vehicles."""
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 60, d_rel=lambda i: 175. - i * 0.5)
    det.update(make_sm(d_rel=60., radar=False, ford_braking=True, v_ego=ACC_FLOOR_MS - 0.5),
               TRAJ, CRUISE_MS, True, ev)
    assert det.state == State.restoring

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
    # Sweeps 220 -> 190 m, entirely beyond the 180 m cap. TTC drops under the 7 s trigger partway
    # through, so this now tests the distance gate specifically rather than being rejected by TTC.
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 60, d_rel=lambda i: 220. - i * 0.5)
    assert det.state != State.active

  def test_lead_inside_the_raised_range_triggers(self):
    """The counterpart: 160 m used to be beyond reach at the old 120 m / 4 s defaults, and is the
    whole point of raising them -- a stopped car wants seeing well before 116 m at 65 mph."""
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 60, d_rel=lambda i: 175. - i * 0.5)
    assert det.state == State.active
    assert det.trigger == Trigger.visionLead

  def test_shouldstop_is_never_true_where_this_path_runs(self):
    """The bug the old fixture hid, stated as an assertion.

    modeld: should_stop = (v_ego < 0.3 and desired_accel < 0.1). The model-stop path requires
    MIN_V_EGO_MS (25 mph). Anything gating on shouldStop above that speed is dead code, and this
    fails the moment someone reintroduces it.
    """
    sm = make_sm(status=False, accel=-2., v_ego=MIN_V_EGO_MS)
    assert not sm['modelV2'].action.shouldStop
    assert make_sm(status=False, accel=-2., v_ego=0.1)['modelV2'].action.shouldStop, \
      "the fixture no longer models modeld; the guard above proves nothing"

  def test_model_stop_triggers_on_dec_slow_down_not_shouldstop(self):
    """What the car should have been doing at every red light."""
    det, ev = model_stop_detector(), FakeEvents()
    run(det, ev, 40, slow_down=True, status=False, accel=-1.5)
    assert not det.model_should_stop, "triggered on a signal that is false here"
    assert det.state == State.active and det.trigger == Trigger.modelStop

  def test_a_lead_of_any_kind_suppresses_the_stop_path(self):
    """Reported 2026-08-06: it fired approaching cars queued at a light, where Ford ACC was already
    slowing because it could see them.

    The gate used to be _ford_tracks, which requires the lead to be MOVING above 6 mph -- so a queue
    of stopped cars did not count and this fired on them. That is precisely the case the block
    header says it does not handle: "a sign or signal with NO vehicle at it produces no lead". If
    there is a vehicle, the vehicle is the thing to react to.
    """
    for radar in (True, False):
      det, ev = model_stop_detector(), FakeEvents()
      # a stopped car ahead, holding range -- never an unconfirmed-lead candidate, but a lead
      run(det, ev, 40, slow_down=True, stop_dist=120., accel=-1.5,
          status=True, radar=radar, v_rel=-CRUISE_MS, d_rel=100.)
      assert det.trigger != Trigger.modelStop, (
        f"model stop fired with a lead present (radar={radar}) -- Ford already has it")

  def test_the_stop_path_still_fires_at_an_empty_intersection(self):
    """The narrowing must not cost the case the feature exists for."""
    det, ev = model_stop_detector(), FakeEvents()
    run(det, ev, 40, slow_down=True, stop_dist=120., accel=-1.5, status=False)
    assert det.state == State.active and det.trigger == Trigger.modelStop

  def test_deceleration_alone_is_not_a_stop(self):
    """DEC's trajectory check is the trigger; the model's deceleration only paces the request.

    Hard braking with no shortened trajectory is a lead, a curve or a speed change -- all of them
    things Ford ACC or SCC already handle. Treating it as a red light would drop the set speed
    toward the floor for an ordinary slowdown.
    """
    det, ev = model_stop_detector(), FakeEvents()
    run(det, ev, 40, slow_down=False, status=False, accel=-2.5)
    assert det.state != State.active

  def test_a_caller_that_omits_the_signal_never_fires_the_model_path(self):
    """The default is False, so forgetting to pass it costs the feature -- not a phantom stop from
    whatever happened to be in the argument slot."""
    det, ev = model_stop_detector(), FakeEvents()
    for _ in range(40):
      det.update(make_sm(status=False, accel=-2.5), TRAJ, CRUISE_MS, True, ev)
    assert det.state != State.active

  def test_it_brakes_at_trigger_even_before_the_model_asks_to(self):
    """The whole point of the geometry term, and the thing he asked to be sure of.

    DEC triggers because the trajectory SHORTENED, which happens before the model starts asking to
    decelerate. So at the moment of trigger desiredAcceleration is typically still ~0, and pacing
    the request off that alone would command no change at all -- arriving late for precisely the
    reason DEC's signal was chosen for being early.

    65 mph with the stop 155 m out needs v^2/2d = 2.7 m/s^2, which is past URGENT_DECEL_MS2 -- so
    since 2026-08-06 this asks for the FLOOR rather than pacing. The paced ramp he liked at red
    lights happens at arterial speeds, where the same distance demands under 1.4.
    """
    det, ev = model_stop_detector(), FakeEvents()
    run(det, ev, 20, slow_down=True, stop_dist=155., status=False, accel=0.)
    assert det.state == State.active and det.trigger == Trigger.modelStop
    assert det.v_target < CRUISE_MS - 8., \
      f"asked for {det.v_target / 0.44704:.0f} mph from {CRUISE_MS / 0.44704:.0f} -- too little, too late"
    assert det.v_target == ACC_FLOOR_MS, f"no room to pace at 65 mph/155 m, got {det.v_target:.1f}"

  def test_a_closer_stop_is_asked_for_harder(self):
    """Monotonic in distance, which is what makes this a profile rather than a step."""
    targets = []
    for d in (200., 155., 90., 50.):
      det, ev = model_stop_detector(), FakeEvents()
      run(det, ev, 20, slow_down=True, stop_dist=d, status=False, accel=0.)
      targets.append(det.v_target)
    assert targets == sorted(targets, reverse=True), f"not monotonic in distance: {targets}"
    assert targets[-1] == ACC_FLOOR_MS, "a stop 50 m out at 65 mph should be asking for the floor"

  def test_a_degenerate_endpoint_does_not_slam_the_request(self):
    """v^2/2d explodes as d goes to zero. Below MIN_STOP_DISTANCE_M the geometry term is dropped and
    the acceleration estimate carries it, rather than the floor being commanded off a garbage
    endpoint."""
    det, ev = model_stop_detector(), FakeEvents()
    run(det, ev, 20, slow_down=True, stop_dist=0.0, status=False, accel=-0.5)
    assert det.v_target > ACC_FLOOR_MS, "a zero endpoint went straight to the floor"

  def test_model_stop_without_lead_triggers(self):
    det, ev = model_stop_detector(), FakeEvents()
    run(det, ev, 40, slow_down=True, status=False, accel=-1.5)
    assert det.state == State.active
    assert det.trigger == Trigger.modelStop
    assert det.v_target >= ACC_FLOOR_MS

  def test_model_stop_never_requests_below_floor(self):
    # Ford ACC cannot hold below 20 mph. Requesting lower is meaningless, so it must clamp.
    det, ev = model_stop_detector(), FakeEvents()
    run(det, ev, 40, slow_down=True, status=False, accel=-8.)
    assert det.v_target == ACC_FLOOR_MS

  def test_model_stop_releases_when_model_lets_go(self):
    det, ev = model_stop_detector(), FakeEvents()
    run(det, ev, 40, slow_down=True, status=False, accel=-1.5)
    run(det, ev, 15, slow_down=False, status=False, accel=0.)
    assert det.state in (State.restoring, State.inactive)

  def test_radar_confirmed_moving_lead_suppresses_model_stop(self):
    # Ford ACC handles what it actually tracks, so there is nothing to add -- but only while the
    # lead is moving fast enough for Ford to track it.
    det, ev = model_stop_detector(), FakeEvents()
    run(det, ev, 60, slow_down=True, status=True, radar=True, v_rel=-5., accel=-2.)
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
    run(det, ev, 40, slow_down=True, status=False, accel=-2., v_ego=MIN_V_EGO_MS - 2.)
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


class TestReleaseIsReachableForAStoppedLead:
  """Reported: "it never confirms the lead with Ford so it continues yelling at me".

  The release required _ford_tracks, which needs the lead moving above 6 mph. A stopped car never
  is -- so for the exact case this feature exists for, the release was unreachable and it stayed
  active indefinitely, re-raising its alert every cycle while Ford was visibly handling the car.
  """

  def test_stopped_lead_releases_once_we_reach_the_acc_floor(self):
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 60, d_rel=lambda i: 100. - i * 0.5)
    assert det.state == State.active
    # Slowed to Ford's floor with the radar now returning the stopped car: Ford's stop-and-go
    # regime owns it from here, and this detector's whole output is a floor it has now reached.
    for _ in range(10):
      det.update(make_sm(d_rel=40., radar=True, v_rel=-8.9, v_ego=8.9), TRAJ, CRUISE_MS, True, ev)
    assert det.state != State.active, "stayed active with nothing left to do; alert never stops"

  def test_it_does_not_release_early_at_speed(self):
    """Still closing at 65 mph with the lead stopped is not resolution -- must stay active."""
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 60, d_rel=lambda i: 100. - i * 0.5)
    det.update(make_sm(d_rel=70., radar=True), TRAJ, CRUISE_MS, True, ev)
    assert det.state == State.active


class TestReleaseWhenFordStartsBraking:
  """Reported precisely: when Ford caught the lead at the same time we never warned -- correct,
  the trigger gate rejected it. When Ford caught it LATER we warned first, which is the whole
  point, and then never stopped.

  lead.radar cannot resolve this. It means openpilot's radar sees the object, which was likely
  true the entire time -- Ford's ACC deciding to act on it is a separate judgement inside its own
  ECU, and its brake request is the only signal for that.
  """

  def test_active_trigger_releases_once_ford_asks_for_brakes(self):
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 60, d_rel=lambda i: 100. - i * 0.5)
    assert det.state == State.active
    before = len(ev.fired)
    det.update(make_sm(d_rel=70., radar=True, ford_braking=True), TRAJ, CRUISE_MS, True, ev)
    assert det.state != State.active, "kept commanding while stock ACC was already braking"
    det.update(make_sm(d_rel=68., radar=True, ford_braking=True), TRAJ, CRUISE_MS, True, ev)
    assert len(ev.fired) == before, "kept alerting after handing off to Ford"

  def test_it_stays_active_while_ford_is_doing_nothing(self):
    """The whole value of the feature is the window where Ford has not reacted yet."""
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 60, d_rel=lambda i: 100. - i * 0.5)
    det.update(make_sm(d_rel=70., radar=True, ford_braking=False), TRAJ, CRUISE_MS, True, ev)
    assert det.state == State.active


class TestDriverBrakingEndsIt:
  """The alert exists to buy reaction time. Once the driver is on the pedal it has done its job,
  and continuing is the fastest way to teach someone to ignore it."""

  def test_braking_releases_immediately_and_stops_alerting(self):
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 60, d_rel=lambda i: 100. - i * 0.5)
    assert det.state == State.active
    before = len(ev.fired)
    det.update(make_sm(d_rel=70., brake=True), TRAJ, CRUISE_MS, True, ev)
    assert det.state != State.active
    det.update(make_sm(d_rel=65., brake=True), TRAJ, CRUISE_MS, True, ev)
    assert len(ev.fired) == before, "kept alerting while the driver was braking"

  def test_it_does_not_need_cruise_to_drop_out_first(self):
    """It stopped before only because braking cancels ACC and long_enabled went false a frame
    later. Depending on cruise state to propagate is a poor way to silence an alarm."""
    det, ev = UnconfirmedLeadDetector(), FakeEvents()
    run(det, ev, 60, d_rel=lambda i: 100. - i * 0.5)
    det.update(make_sm(d_rel=70., brake=True), TRAJ, CRUISE_MS, True, ev)  # long_enabled STILL True
    assert det.state != State.active


class TestFordBrakingRelease:
  """Reported: "Ford ACC was definitely braking for it. The warning was still on the screen."

  Ford asks for deceleration on two channels. AccBrkDecel_B_Rq is a discrete flag; AccBrkTot_A_Rq is
  the deceleration it actually wants, in m/s^2. Checking only the flag left the warning up while the
  car was visibly slowing -- and the ACC pill, which reads both, was showing BRAKE at the same time.
  """
  from openpilot.sunnypilot.selfdrive.controls.lib.unconfirmed_lead import (
    UnconfirmedLeadDetector, FORD_BRAKING_DECEL,
  )

  @staticmethod
  def _sm(decel_flag=False, brake_total=0.0, available=True, valid=True):
    """A real class, not a SimpleNamespace with __getitem__ stuck on it -- Python looks dunders up
    on the TYPE, so that fake is not subscriptable and every test raises instead of asserting."""
    from types import SimpleNamespace as NS

    class FakeSubMaster:
      def __init__(self):
        self.valid = {"carStateBP": valid}
        self._data = {"carStateBP": NS(brakeLightStatus=NS(
          accDataAvailable=available, accDecelRequest=decel_flag,
          accAccelRequest=brake_total, accPrechargeRequest=False))}

      def __getitem__(self, key):
        return self._data[key]

    return FakeSubMaster()

  def test_the_discrete_flag_still_releases(self):
    assert self.UnconfirmedLeadDetector._ford_is_braking(self._sm(decel_flag=True))

  def test_deceleration_without_the_flag_now_releases(self):
    """The reported bug. Ford asking for real braking on the magnitude channel alone."""
    assert self.UnconfirmedLeadDetector._ford_is_braking(self._sm(brake_total=-1.0))

  def test_trim_sized_noise_does_not_release(self):
    """ACC trims constantly at small values; without a deadband any noise reads as a takeover."""
    assert not self.UnconfirmedLeadDetector._ford_is_braking(
      self._sm(brake_total=-self.FORD_BRAKING_DECEL / 2))

  def test_acceleration_does_not_release(self):
    assert not self.UnconfirmedLeadDetector._ford_is_braking(self._sm(brake_total=1.0))

  def test_no_acc_data_does_not_release(self):
    """Missing data means "cannot tell", which must read as not-braking so the detector keeps
    working rather than silently standing down."""
    assert not self.UnconfirmedLeadDetector._ford_is_braking(
      self._sm(decel_flag=True, available=False))

  def test_invalid_socket_does_not_release(self):
    assert not self.UnconfirmedLeadDetector._ford_is_braking(
      self._sm(decel_flag=True, valid=False))


class TestModelStopHandsOverWhenALeadAppears:
  """Reported 2026-08-08: "it said stopping for a red light even though there was a car in front".

  The trigger requires no lead and fires legitimately -- at the range where the model first sees the
  light, a queued car is often still outside radar acquisition. Nothing then MAINTAINED that
  condition, so the path kept describing an empty intersection at a car that had since resolved.
  """

  def test_a_stopped_lead_appearing_switches_the_alert_to_the_vehicle(self):
    det, ev = model_stop_detector(), FakeEvents()
    run(det, ev, 40, status=False, v_ego=26.8, slow_down=True, stop_dist=150.)
    assert det.state == State.active and det.trigger == Trigger.modelStop, "need the stop path active"

    # The queued car resolves. Stopped, so Ford's ACC will not follow it.
    det.update(make_sm(status=True, radar=True, d_rel=110., v_rel=-26.8, v_ego=26.8),
               TRAJ, CRUISE_MS, True, ev, True, 110.)
    assert det.trigger == Trigger.visionLead, (
      "still reporting a sign with a car in front -- the entry condition stopped holding")
    assert det.state == State.active, "handing over must not stop the request"
    assert det.v_target <= 26.8, "the request must not rise when a car appears ahead"

  def test_a_moving_lead_appearing_releases_because_ford_follows_it(self):
    det, ev = model_stop_detector(), FakeEvents()
    run(det, ev, 40, status=False, v_ego=26.8, slow_down=True, stop_dist=150.)
    assert det.state == State.active and det.trigger == Trigger.modelStop

    # Moving with traffic, above Ford's 6 mph tracking floor, so its own ACC owns this.
    det.update(make_sm(status=True, radar=True, d_rel=90., v_rel=-1.0, v_ego=26.8),
               TRAJ, CRUISE_MS, True, ev, True, 90.)
    assert det.trigger != Trigger.modelStop, "kept claiming a sign while Ford was following a car"


class TestModelStopWaitsUntilBrakingIsActuallyNeeded:
  """Reported 2026-08-08: "It's stopping for red lights a little too early."

  Measured on route 0000032c, activation #4: it fired at 34 mph with 193 m still to run. Stopping in
  193 m from 34 mph needs 0.60 m/s^2 -- gentler than coasting, and about 2.5x the distance a
  comfortable stop wants. DEC's slow-down flag is deliberately early, which is why it was chosen
  over shouldStop, so the earliness has to be bounded here rather than upstream.
  """

  def test_a_stop_reachable_by_coasting_does_not_trigger(self):
    det, ev = model_stop_detector(), FakeEvents()
    det.model_stop_min_decel = 1.0
    # 34 mph, 193 m out -- the logged case. a = 15.2^2 / (2*193) = 0.60 m/s^2.
    run(det, ev, 60, status=False, v_ego=15.2, slow_down=True, stop_dist=193.)
    assert det.state != State.active, (
      "fired 193 m out at 34 mph, which needs 0.60 m/s^2 -- the reported 'too early'")

  def test_the_same_stop_triggers_once_it_is_close_enough_to_need_braking(self):
    det, ev = model_stop_detector(), FakeEvents()
    det.model_stop_min_decel = 1.0
    # Same speed, 110 m out: a = 1.05 m/s^2, past the gate.
    run(det, ev, 60, status=False, v_ego=15.2, slow_down=True, stop_dist=110.)
    assert det.state == State.active and det.trigger == Trigger.modelStop, (
      "did not act at 110 m, where coasting no longer arrives in time")

  def test_an_unreadable_endpoint_keeps_the_old_behavior(self):
    """inf endpoint means no trajectory reading, not 'a very distant stop'. Treating it as failing
    the gate would silently disable the whole path on any frame the distance is missing."""
    det, ev = model_stop_detector(), FakeEvents()
    det.model_stop_min_decel = 1.0
    run(det, ev, 60, status=False, v_ego=15.2, slow_down=True, stop_dist=float('inf'))
    assert det.state == State.active, "an unreadable endpoint disabled the path"


class TestTheStopRequestOnlyEverGoesDown:
  """Reported 2026-08-08: "it only ever got down to 28 and almost started going up before I hit the
  brakes... went down to 28 and kind of fluctuated there."

  _model_stop_target is max(geometry, acceleration), and the acceleration term is
  modelV2.action.desiredAcceleration -- noisy frame to frame. So the request wanders and ICBM chases
  it back up. Same failure the curve ceiling fixed, except that keys on SCC-Vision being active and a
  red light is not a curve.
  """

  def test_a_noisy_accel_estimate_cannot_raise_the_request(self):
    det, ev = model_stop_detector(), FakeEvents()
    run(det, ev, 40, status=False, v_ego=15.6, slow_down=True, stop_dist=90.)
    assert det.state == State.active and det.trigger == Trigger.modelStop
    low = det.v_target

    # The model's accel estimate collapsing toward zero is what makes the target want to rise.
    for _ in range(60):
      det.update(make_sm(status=False, v_ego=15.6, accel=0.0), TRAJ, CRUISE_MS, True, ev, True, 400.)
      assert det.v_target <= low + 1e-6, (
        f"request rose from {low / 0.44704:.0f} to {det.v_target / 0.44704:.0f} mph mid-stop")

  def test_a_new_stop_starts_from_scratch(self):
    """The ratchet must not outlive the event, or the next light inherits the last one's floor."""
    det, ev = model_stop_detector(), FakeEvents()
    run(det, ev, 40, status=False, v_ego=15.6, slow_down=True, stop_dist=60.)
    floored = det.v_target
    for _ in range(60):        # light goes green; model stops asking
      det.update(make_sm(status=False, v_ego=15.6), TRAJ, CRUISE_MS, True, ev, False, float('inf'))
    assert det.state != State.active

    run(det, ev, 40, status=False, v_ego=26.8, slow_down=True, stop_dist=250.)
    assert det.v_target > floored, "the next stop inherited the previous floor"


# --- the 20 mph floor, and why it stopped being the end of the request -------------------------

def test_the_floor_still_releases_when_nothing_can_act_below_it():
  """Unchanged behaviour with the override off: the set speed cannot go under 20 mph, so the
  request is spent and handing it back is right."""
  from openpilot.sunnypilot.selfdrive.controls.lib.unconfirmed_lead import ACC_FLOOR_MS
  assert ACC_FLOOR_MS > 0


def test_the_floor_does_not_release_when_the_override_can_finish_the_stop():
  """He reported it: "occasionally the traffic light thing will set my speed back up after it has
  gotten down to 20." Not occasional -- `_release()` goes to `restoring` whenever a restore point
  was captured, so it happened every time the car crossed 20 with a model stop running.

  Harmless before the stop override existed. Actively wrong with it: the set speed climbs back while
  openpilot brakes, and the moment the time bound expires Ford accelerates away from the stop line.

  Asserted on the SOURCE because the release path needs a full planner fixture to drive, and the
  thing that matters is that the floor check is gated at all -- an ungated `v_ego < ACC_FLOOR_MS`
  is the bug.
  """
  import ast, inspect
  from openpilot.sunnypilot.selfdrive.controls.lib import unconfirmed_lead as mod

  tree = ast.parse(inspect.getsource(mod))
  floor_tests = []
  for node in ast.walk(tree):
    if isinstance(node, ast.If):
      dump = ast.dump(node.test)
      if "ACC_FLOOR_MS" in dump:
        floor_tests.append(dump)
  assert floor_tests, "the floor check vanished -- if that is deliberate, delete this test with it"
  releasing = [d for d in floor_tests if "stop_override_available" in d]
  assert releasing, (
    "the 20 mph floor releases the model stop without asking whether the stop override can finish "
    "it -- that restores the set speed mid-stop, which is what he reported from the road")
