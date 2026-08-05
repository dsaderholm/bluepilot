"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

BluePilot: behavioral tests for the radar-blind lead / model-stop detector.

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
            should_stop=False, accel=0., v_ego=CRUISE_MS, brake=False, d_path=0.2,
            ford_braking=False):
  sm = {
    'carState': NS(vEgo=v_ego, brakePressed=brake),
    'radarState': NS(leadOne=NS(dRel=d_rel, vRel=v_rel, modelProb=prob, status=status,
                                radar=radar, dPath=d_path)),
    'modelV2': NS(action=NS(shouldStop=should_stop, desiredAcceleration=accel)),
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
