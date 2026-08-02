"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

BluePilot: behavioural tests for the phase-1 passing-assist observer.

The detector cannot actuate anything, so these are not safety tests. They cover the two ways a
log-only observer can still waste a drive's worth of data:

  - recording a suggestion that was never actually gated (blind spot unavailable being silently
    read as "clear", geometry passing on one evidence channel alone)
  - blockedBy reporting the wrong gate, which would make the whole dataset misleading about which
    filter is doing the work

Plus the sign convention on lane geometry, which is easy to get backwards and impossible to spot
in a log after the fact.
"""

from types import SimpleNamespace as NS

from cereal import custom
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot.selfdrive.controls.lib.passing_assist import (
  PassingAssistDetector, MIN_LANE_WIDTH_M, MIN_V_EGO_MS, MAX_WIDENING_M,
)

Side = custom.LongitudinalPlanSP.PassingAssist.Side
Blocked = custom.LongitudinalPlanSP.PassingAssist.Blocked
Reason = custom.LongitudinalPlanSP.PassingAssist.Reason
Trigger = custom.LongitudinalPlanSP.PassingAssist.Trigger
RefSource = custom.LongitudinalPlanSP.PassingAssist.ReferenceSource

CRUISE_MS = 31.0            # ~70 mph set speed
SLOW_LEAD_MS = 24.0         # ~54 mph lead -> ~7 m/s deficit, over the 8 mph default


def xyz(y, widen=0.0):
  """33 points along X_IDXS. `widen` grows the value with distance, which is what an exit or
  on-ramp looks like on the right road edge."""
  return NS(y=[y + widen * (i / 32.0) ** 2 * (32 / 20.0) ** 2 for i in range(33)])


class FakeSubMaster:
  """Mimics cereal SubMaster, deliberately NOT a dict.

  SubMaster exposes __getitem__ and no __contains__, so `'x' in sm` falls back to sequence
  iteration and calls sm[0] -- KeyError: 0. A dict fixture makes that work perfectly in tests and
  crash-loop plannerd on the car, which is what happened. Anything sm-shaped here must reproduce
  SubMaster's actual protocol, not a convenient superset.
  """

  def __init__(self, data: dict):
    self.data = data

  def __getitem__(self, s):
    return self.data[s]

  def __delitem__(self, s):
    del self.data[s]


def make_sm(*, v_lead=SLOW_LEAD_MS, v_ego=None, d_rel=40., d_path=0.2, status=True,
            left_bs=False, right_bs=False, blis_avail=True,
            # geometry: ego lane lines at -1.85/+1.85, road edges default to one clear lane left
            ll=(-5.5, -1.85, 1.85, 5.5), probs=(0.9, 0.99, 0.99, 0.2),
            edges=(-5.6, 2.4), edge_stds=(0.1, 0.1), right_edge_widen=0.0,
            tsr_avail=True, ovtk_msg=1, ovtk_status=2,
            blinker=False, brake=False, steering=False, road_name="I 15",
            acc_braking=False, acc_avail=True, set_speed=None,
            icbm_hold=0.0, icbm_manual=False):
  # Being stuck behind a car means matching its speed, not still closing on it: vEgo tracks vLead
  # and the gap to the SET speed is what makes passing worth suggesting. Tests that need a genuine
  # approach pass v_ego explicitly.
  if v_ego is None:
    v_ego = v_lead
  v_rel = v_lead - v_ego
  return FakeSubMaster({
    'carState': NS(vEgo=v_ego, brakePressed=brake, steeringPressed=steering,
                   leftBlinker=blinker, rightBlinker=False,
                   leftBlindspot=left_bs, rightBlindspot=right_bs,
                   cruiseState=NS(speedCluster=set_speed if set_speed is not None else CRUISE_MS)),
    'radarState': NS(leadOne=NS(status=status, dRel=d_rel, vRel=v_rel, vLead=v_lead, dPath=d_path)),
    'modelV2': NS(laneLines=[xyz(v) for v in ll], laneLineProbs=list(probs),
                  roadEdges=[xyz(edges[0]), xyz(edges[1], widen=right_edge_widen)],
                  roadEdgeStds=list(edge_stds)),
    'carStateBP': NS(brakeLightStatus=NS(accDataAvailable=acc_avail, accDecelRequest=acc_braking,
                                         accPrechargeRequest=False),
                     blisLeft=NS(dataAvailable=blis_avail), blisRight=NS(dataAvailable=blis_avail),
                     trafficSignData=NS(dataAvailable=tsr_avail, overtakeMsg=ovtk_msg,
                                        overtakeStatus=ovtk_status)),
    'liveMapDataSP': NS(roadName=road_name),
    'selfdriveStateSP': NS(intelligentCruiseButtonManagement=NS(
        vBaseline=icbm_hold, overrideState=1 if icbm_manual else 0)),
  })


def run(det, frames, **kw):
  for _ in range(frames):
    det.update(make_sm(**kw), CRUISE_MS, True)
  return det


# Enough frames to clear the confirmation window with margin.
STUCK_FRAMES = int(4.0 / DT_MDL)  # persistence is 2 s now


class TestPassingAssistGeometry:
  def test_clear_left_lane_is_suggested(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES)
    assert det.suggestion == Side.left
    assert det.blocked_by == Blocked.none

  def test_left_lane_requires_both_evidence_channels(self):
    # Painted line present but no drivable width to the edge: we are already in the left lane and
    # what the model sees beyond it is the far side of a barrier, not a lane.
    det = run(PassingAssistDetector(), STUCK_FRAMES, edges=(-2.3, 2.4))
    assert det.left_edge_gap < MIN_LANE_WIDTH_M
    assert not det.left_geometry_ok
    assert det.suggestion == Side.none
    assert det.blocked_by == Blocked.noLaneAvailable

  def test_low_line_probability_blocks_the_side(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, probs=(0.1, 0.99, 0.99, 0.2))
    assert not det.left_geometry_ok
    assert det.blocked_by == Blocked.noLaneAvailable

  def test_unreliable_road_edge_blocks_the_side(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, edge_stds=(2.0, 0.1))
    assert not det.left_geometry_ok
    assert det.blocked_by == Blocked.noLaneAvailable

  def test_edge_gap_sign_convention(self):
    # y is negative left, positive right. Both gaps must come out positive, or one side would be
    # silently unavailable forever and nothing in a log would say why.
    det = run(PassingAssistDetector(), 1)
    assert det.left_edge_gap > 0
    assert det.right_edge_gap > 0
    # left line -1.85 to left edge -5.6 is 3.75 m
    assert abs(det.left_edge_gap - 3.75) < 0.01

  def test_undivided_road_still_reports_a_lane(self):
    """The known false positive, asserted deliberately rather than left implicit.

    An oncoming lane is geometrically identical to a passing lane. This test documents that phase 1
    DOES fire here -- that is the measurement being taken. If a future change makes this pass by
    accident, the dataset silently stops answering the question it exists to answer.
    """
    det = run(PassingAssistDetector(), STUCK_FRAMES)
    assert det.suggestion == Side.left
    assert not det.overtake_restricted


class TestPassingAssistGates:
  def test_brief_confirmation_before_suggesting(self):
    """The timer is now only long enough to reject a bad frame of lead tracking -- it is NOT a
    waiting period, which is the behaviour this whole design exists to remove."""
    det = run(PassingAssistDetector(), int(1.0 / DT_MDL))
    assert det.suggestion == Side.none
    assert det.blocked_by == Blocked.notStuck
    assert 0.5 < det.approach_seconds < 1.5

  def test_confirmation_resets_when_lead_drops(self):
    det = PassingAssistDetector()
    run(det, int(20.0 / DT_MDL))
    assert det.approach_seconds > 19.0
    run(det, 1, status=False)
    assert det.approach_seconds == 0.0

  def test_closing_and_pacing_are_the_same_situation(self):
    """There is one trigger now. Whether we are still catching the slower car or already sitting
    behind it, the answer is the same: pass it."""
    closing = run(PassingAssistDetector(), STUCK_FRAMES,
                  v_lead=CRUISE_MS - 10.0, v_ego=CRUISE_MS, d_rel=55.)
    pacing = run(PassingAssistDetector(), STUCK_FRAMES)
    assert closing.suggestion == Side.left
    assert pacing.suggestion == Side.left

  def test_deficit_below_the_threshold_is_not_worth_passing(self):
    # 0.4 m/s is under 1 mph. The threshold is 2 mph now, so this has to be smaller than the
    # 1.0 m/s this test used when the threshold was 8.
    det = run(PassingAssistDetector(), STUCK_FRAMES, v_lead=CRUISE_MS - 0.4)
    assert det.blocked_by == Blocked.notStuck

  def test_below_min_speed_blocks(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, v_ego=MIN_V_EGO_MS - 1.0)
    assert det.blocked_by == Blocked.tooSlow

  def test_driver_input_blocks_and_resets(self):
    det = PassingAssistDetector()
    run(det, int(20.0 / DT_MDL))
    run(det, 1, blinker=True)
    assert det.blocked_by == Blocked.driverActive
    assert det.approach_seconds == 0.0

  def test_not_engaged_blocks(self):
    det = PassingAssistDetector()
    for _ in range(STUCK_FRAMES):
      det.update(make_sm(), CRUISE_MS, False)
    assert det.blocked_by == Blocked.notEngaged


class TestPassingAssistBlindspot:
  def test_occupied_blindspot_blocks_left(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, left_bs=True, probs=(0.9, 0.99, 0.99, 0.2))
    assert det.suggestion == Side.none
    assert det.blocked_by == Blocked.blindspotOccupied

  def test_falls_through_to_right_when_left_occupied(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, left_bs=True,
              probs=(0.9, 0.99, 0.99, 0.9), edges=(-5.6, 5.7))
    assert det.suggestion == Side.right

  def test_unavailable_blindspot_is_recorded(self):
    # The dangerous case: BLIS absent reads as "clear" in carState. The suggestion is still made
    # (this is phase 1 and the point is to collect data), but blindspotAvailable must say so or
    # every pre-canbox log entry looks blind-spot-checked when nothing checked it.
    det = run(PassingAssistDetector(), STUCK_FRAMES, blis_avail=False)
    assert det.suggestion == Side.left
    assert not det.blindspot_available

  def test_available_when_data_present(self):
    det = run(PassingAssistDetector(), 1)
    assert det.blindspot_available


class TestPassingAssistTsr:
  def test_reliable_restriction_vetoes(self):
    # 2 = LimAllWithoutRestriction (a limitation IS in force), 2 = LimitReliable
    det = run(PassingAssistDetector(), STUCK_FRAMES, ovtk_msg=2, ovtk_status=2)
    assert det.overtake_restricted
    assert det.blocked_by == Blocked.overtakeRestricted

  def test_cancelled_zone_does_not_veto(self):
    # 4 = LimAllCancelled -- the zone ended. Reading this as "Lim* therefore restricted" would
    # leave a veto latched for the rest of the drive.
    det = run(PassingAssistDetector(), STUCK_FRAMES, ovtk_msg=4, ovtk_status=2)
    assert not det.overtake_restricted
    assert det.suggestion == Side.left

  def test_unreliable_status_does_not_veto(self):
    # 3 = LimitOutdated. A stale reading must not block passing indefinitely.
    det = run(PassingAssistDetector(), STUCK_FRAMES, ovtk_msg=2, ovtk_status=3)
    assert not det.overtake_restricted
    assert det.suggestion == Side.left

  def test_absent_tsr_is_recorded_not_assumed(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, tsr_avail=False)
    assert not det.tsr_available
    assert not det.overtake_restricted
    assert det.suggestion == Side.left

  def test_truck_restriction_vetoes_too(self):
    # 5 = LimForTrucksWithoutRstrc. Conservative: we do not know we are not the restricted class.
    det = run(PassingAssistDetector(), STUCK_FRAMES, ovtk_msg=5, ovtk_status=2)
    assert det.overtake_restricted


class TestPassingAssistIsObservationOnly:
  def test_update_returns_nothing(self):
    det = PassingAssistDetector()
    assert det.update(make_sm(), CRUISE_MS, True) is None

  def test_no_event_or_target_attributes(self):
    """Guard against phase 2 arriving by accident.

    If someone later wires an alert or a speed target in here, this fails and forces the change to
    be deliberate rather than a quiet escalation from 'log only'.
    """
    det = PassingAssistDetector()
    for attr in ('v_target', 'a_target', 'events_sp', 'output_v_target'):
      assert not hasattr(det, attr), f"passing assist gained {attr}: it is supposed to be log-only"


# Three lanes each way, sitting in the middle: a lane to the right, and another beyond it. This is
# the geometry keep-right is scoped to now -- see PassingAssistAvoidOutermost.
IN_LEFT_LANE = dict(probs=(0.1, 0.99, 0.99, 0.9), edges=(-2.2, 9.3))

# Two lanes each way, sitting in the left: the lane to our right IS the outermost, so it could be
# an exit-only lane and keep-right must stay silent.
TWO_LANE_ROAD = dict(probs=(0.1, 0.99, 0.99, 0.9), edges=(-2.2, 5.7))
KEEP_RIGHT_FRAMES = int(11.0 / DT_MDL)



class _KeepRightOnParams:
  """Keep right is DEFAULT OFF in params_keys.h -- the exit-lane ambiguity makes it unsafe to
  lean on. These tests opt in explicitly so the default stays honest and a future flip of that
  default cannot silently make them pass for the wrong reason."""

  def get(self, key, block=False, return_default=False):
    return {"PassingAssistMinDeficit": 4, "PassingAssistStuckTime": 2,
            "PassingAssistKeepRightDelay": 10, "PassingAssistSettleTime": 20,
            "PassingAssistMaxDistance": 220, "PassingAssistSuspendMinutes": 15}[key]

  def __init__(self, avoid_outermost=True):
    self.avoid_outermost = avoid_outermost

  def get_bool(self, key, block=False):
    if key == "PassingAssistAvoidOutermost":
      return self.avoid_outermost
    return key != "PassingAssistSuspend"

  def put_bool(self, key, val):
    pass


def keep_right_det(avoid_outermost=True):
  det = PassingAssistDetector()
  det.params = _KeepRightOnParams(avoid_outermost)
  return det


class TestKeepRight:
  def test_suggests_returning_right_with_no_lead(self):
    det = run(keep_right_det(), KEEP_RIGHT_FRAMES, status=False, **IN_LEFT_LANE)
    assert det.suggestion == Side.right
    assert det.reason == Reason.keepRight

  def test_delay_must_elapse(self):
    det = run(keep_right_det(), int(4.0 / DT_MDL), status=False, **IN_LEFT_LANE)
    assert det.suggestion == Side.none
    assert 3.0 < det.keep_right_seconds < 5.0

  def test_no_lane_to_the_right_means_no_suggestion(self):
    # Already in the right lane: the gap to the road edge collapses to a shoulder.
    det = run(keep_right_det(), KEEP_RIGHT_FRAMES, status=False,
              probs=(0.9, 0.99, 0.99, 0.1), edges=(-5.6, 2.3))
    assert det.suggestion == Side.none
    assert det.keep_right_seconds == 0.0

  def test_right_blindspot_blocks_and_resets(self):
    det = keep_right_det()
    run(det, int(8.0 / DT_MDL), status=False, **IN_LEFT_LANE)
    assert det.keep_right_seconds > 7.0
    run(det, 1, status=False, right_bs=True, **IN_LEFT_LANE)
    assert det.keep_right_seconds == 0.0
    assert det.suggestion == Side.none

  def test_not_suggested_while_a_pass_is_warranted(self):
    """The ordering that matters: never told to move over mid-overtake."""
    det = run(PassingAssistDetector(), STUCK_FRAMES, **IN_LEFT_LANE)
    assert det.reason == Reason.passing
    assert det.suggestion == Side.right   # left unavailable in this geometry
    assert det.keep_right_seconds == 0.0

  def test_lead_present_but_not_holding_us_back_still_keeps_right(self):
    # A lead pacing us at our own set speed is not a reason to sit in the left lane.
    det = run(keep_right_det(), KEEP_RIGHT_FRAMES, v_lead=CRUISE_MS, **IN_LEFT_LANE)
    assert det.reason == Reason.keepRight

  def test_passing_suggestion_reports_reason_passing(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES)
    assert det.suggestion == Side.left
    assert det.reason == Reason.passing


class TestRoadNameLogging:
  def test_road_name_is_recorded_with_the_decision(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, road_name="I 15")
    assert det.road_name == "I 15"

  def test_missing_map_data_is_empty_not_an_error(self):
    det = PassingAssistDetector()
    sm = make_sm()
    del sm['liveMapDataSP']
    det.update(sm, CRUISE_MS, True)
    assert det.road_name == ""


class TestKeepRightIsOptIn:
  def test_disabled_by_default(self):
    """The default must stay off: an exit-only lane is geometrically a through lane."""
    det = run(PassingAssistDetector(), KEEP_RIGHT_FRAMES, status=False, **IN_LEFT_LANE)
    assert det.suggestion == Side.none
    assert det.keep_right_seconds == 0.0


class TestRearApproachGate:
  def test_no_rear_sensor_does_not_block_a_suggestion(self):
    """Today's behaviour: nothing is fitted, so the gate must not silently kill the feature."""
    det = run(PassingAssistDetector(), STUCK_FRAMES)
    assert det.suggestion == Side.left
    assert not det.rear.available

  def test_closing_vehicle_on_the_left_blocks_the_left_pass(self):
    det = PassingAssistDetector()
    for _ in range(STUCK_FRAMES):
      det.update(make_sm(), CRUISE_MS, True)
      det.rear.left.from_radar(d_rel=50.0, v_rel=12.0)   # applied after update(), as a source would
    det.update(make_sm(), CRUISE_MS, True)
    # update() resets rear each cycle while no source exists, so assert the gate logic directly
    det.rear.left.from_radar(d_rel=50.0, v_rel=12.0)
    assert det.rear.left.blocks_lane_change

  def test_left_threat_leaves_right_usable(self):
    det = PassingAssistDetector()
    det.rear.left.from_radar(d_rel=50.0, v_rel=12.0)
    det.rear.right.from_radar(d_rel=250.0, v_rel=0.0)
    assert det.rear.left.blocks_lane_change
    assert not det.rear.right.blocks_lane_change



class TestAvoidOutermostLane:
  def test_moves_right_when_another_lane_lies_beyond(self):
    det = run(keep_right_det(), KEEP_RIGHT_FRAMES, status=False, **IN_LEFT_LANE)
    assert det.lane_beyond_right
    assert det.suggestion == Side.right
    assert det.reason == Reason.keepRight

  def test_will_not_enter_the_outermost_lane(self):
    """Two lanes each way: the right lane IS the outermost, so it could be an exit. Stay put."""
    det = run(keep_right_det(), KEEP_RIGHT_FRAMES, status=False, **TWO_LANE_ROAD)
    assert not det.lane_beyond_right
    assert det.suggestion == Side.none
    assert det.keep_right_seconds == 0.0

  def test_passing_is_unaffected_by_the_outermost_rule(self):
    """The rule is scoped to keep-right. Overtaking on the right is a different decision and the
    driver is choosing to make it."""
    det = run(PassingAssistDetector(), STUCK_FRAMES, left_bs=True,
              probs=(0.9, 0.99, 0.99, 0.9), edges=(-5.6, 5.7))
    assert det.suggestion == Side.right
    assert det.reason == Reason.passing


class TestRoadWidening:
  def test_parallel_road_is_not_widening(self):
    det = run(PassingAssistDetector(), 1, **IN_LEFT_LANE)
    assert det.right_widening_m < 0.5
    assert not det.right_widening

  def test_exit_ahead_is_detected(self):
    # Right road edge peeling away by ~4 m at the 75 m mark.
    det = run(PassingAssistDetector(), 1, right_edge_widen=4.0, **IN_LEFT_LANE)
    assert det.right_widening_m > MAX_WIDENING_M
    assert det.right_widening

  def test_widening_blocks_keep_right_on_a_two_lane_road(self):
    """The case the outermost rule could only solve by giving up on two-lane roads entirely."""
    det = keep_right_det(avoid_outermost=False)
    run(det, KEEP_RIGHT_FRAMES, status=False, right_edge_widen=4.0, **TWO_LANE_ROAD)
    assert det.suggestion == Side.none
    assert det.keep_right_seconds == 0.0

  def test_two_lane_road_works_when_not_widening(self):
    """...and the point of it: with no exit ahead, a two-lane road now gets the suggestion."""
    det = keep_right_det(avoid_outermost=False)
    run(det, KEEP_RIGHT_FRAMES, status=False, **TWO_LANE_ROAD)
    assert not det.right_widening
    assert det.suggestion == Side.right
    assert det.reason == Reason.keepRight

  def test_untrusted_road_edge_reports_nothing(self):
    det = run(PassingAssistDetector(), 1, right_edge_widen=4.0, edge_stds=(0.1, 2.0),
              **{k: v for k, v in IN_LEFT_LANE.items() if k != 'edge_stds'})
    assert det.right_widening_m == 0.0
    assert not det.right_widening


class TestOneTrigger:
  def test_suggests_while_still_closing_before_any_speed_is_lost(self):
    det = PassingAssistDetector()
    for _ in range(int(3.0 / DT_MDL)):
      det.update(make_sm(v_ego=CRUISE_MS, v_lead=CRUISE_MS - 9.0, d_rel=150.), CRUISE_MS, True)
    assert det.suggestion == Side.left, "must not wait until we are close"

  def test_suggests_when_already_pacing_a_slower_car(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES)
    assert det.suggestion == Side.left

  def test_beyond_the_far_bound_is_ignored(self):
    det = PassingAssistDetector()
    for _ in range(int(3.0 / DT_MDL)):
      det.update(make_sm(v_ego=CRUISE_MS, v_lead=CRUISE_MS - 9.0, d_rel=400.), CRUISE_MS, True)
    assert det.suggestion == Side.none

  def test_below_the_deficit_is_not_worth_passing(self):
    """The deficit is the judgement. Everything else is a sanity bound."""
    det = PassingAssistDetector()
    for _ in range(int(3.0 / DT_MDL)):
      det.update(make_sm(v_ego=CRUISE_MS, v_lead=CRUISE_MS - 0.4, d_rel=60.), CRUISE_MS, True)
    assert det.suggestion == Side.none

  def test_a_gentle_deficit_reaches_just_as_far_as_a_large_one(self):
    """The reason TTC was removed. At 5 mph under, closing is 2.2 m/s, which a 60 s TTC bound
    turned into about 130 m -- the gentler the difference, the LATER it noticed. Distance must not
    depend on the speed difference at all."""
    MPH = 0.44704
    gentle = PassingAssistDetector()
    steep = PassingAssistDetector()
    for _ in range(int(3.0 / DT_MDL)):
      gentle.update(make_sm(v_ego=80 * MPH, v_lead=75 * MPH, d_rel=180.,
                            set_speed=80 * MPH), CRUISE_MS, True)
      steep.update(make_sm(v_ego=80 * MPH, v_lead=50 * MPH, d_rel=180.,
                           set_speed=80 * MPH), CRUISE_MS, True)
    assert gentle.suggestion == Side.left, "5 mph under at 180 m must still be noticed"
    assert steep.suggestion == Side.left


class TestBeatingAccBraking:
  def test_records_that_acc_was_not_yet_braking(self):
    """The quality metric, and what `trigger` now means: a suggestion made before ACC brakes could
    have avoided the deceleration entirely."""
    det = PassingAssistDetector()
    for _ in range(int(2.0 / DT_MDL)):
      det.update(make_sm(v_ego=CRUISE_MS, v_lead=CRUISE_MS - 9.0, d_rel=60., acc_braking=False),
                 CRUISE_MS, True)
    assert det.suggestion == Side.left
    assert det.acc_braking_available
    assert not det.acc_braking_at_decision
    assert det.trigger == Trigger.approaching, "beat ACC to it"

  def test_records_that_acc_had_already_started(self):
    det = PassingAssistDetector()
    for _ in range(int(2.0 / DT_MDL)):
      det.update(make_sm(v_ego=CRUISE_MS, v_lead=CRUISE_MS - 9.0, d_rel=60., acc_braking=True),
                 CRUISE_MS, True)
    assert det.acc_braking_at_decision
    assert det.trigger == Trigger.heldUp, "after ACC started braking, we did not beat it"

  def test_unavailable_is_not_reported_as_not_braking(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, acc_avail=False)
    assert not det.acc_braking_available
    assert not det.acc_braking_at_decision
    assert det.trigger == Trigger.approaching, "beat ACC to it"


class TestAntiWeave:
  def test_no_return_suggestion_right_after_a_pass(self):
    """Three-lane road, slow left lane: must not be told to move back the moment we move over."""
    det = keep_right_det()
    run(det, STUCK_FRAMES)                       # triggers a pass
    assert det.suggestion == Side.left
    run(det, int(5.0 / DT_MDL), status=False, **IN_LEFT_LANE)
    assert det.suggestion == Side.none
    assert det.keep_right_seconds == 0.0

  def test_return_allowed_once_settled(self):
    det = keep_right_det()
    run(det, STUCK_FRAMES)
    run(det, int(35.0 / DT_MDL), status=False, **IN_LEFT_LANE)
    assert det.suggestion == Side.right
    assert det.reason == Reason.keepRight


class _SuspendParams:
  """Params stub with a settable one-shot suspend request."""

  def __init__(self, minutes=15):
    self.suspend = False
    self.minutes = minutes

  def get(self, key, block=False, return_default=False):
    return {"PassingAssistMinDeficit": 4, "PassingAssistStuckTime": 2,
            "PassingAssistKeepRightDelay": 10, "PassingAssistSettleTime": 20,
            "PassingAssistMaxDistance": 220, "PassingAssistSuspendMinutes": self.minutes}[key]

  def get_bool(self, key, block=False):
    return self.suspend if key == "PassingAssistSuspend" else True

  def put_bool(self, key, val):
    if key == "PassingAssistSuspend":
      self.suspend = bool(val)


class TestSuspend:
  def _det(self, minutes=15):
    det = PassingAssistDetector()
    det.params = _SuspendParams(minutes)
    return det

  def test_tap_suspends_and_blocks_everything(self):
    det = self._det()
    run(det, STUCK_FRAMES)
    assert det.suggestion == Side.left
    det.params.suspend = True
    det.update(make_sm(), CRUISE_MS, True)
    assert det.suspended_seconds > 0
    assert det.blocked_by == Blocked.suspended
    assert det.suggestion == Side.none

  def test_request_is_consumed_so_it_cannot_retrigger(self):
    det = self._det()
    det.params.suspend = True
    det.update(make_sm(), CRUISE_MS, True)
    assert not det.params.suspend, "one-shot request was not cleared"

  def test_second_tap_resumes_immediately(self):
    det = self._det()
    det.params.suspend = True
    det.update(make_sm(), CRUISE_MS, True)
    assert det.suspended_seconds > 0
    det.params.suspend = True
    det.update(make_sm(), CRUISE_MS, True)
    assert det.suspended_seconds == 0.0

  def test_counts_down_and_resumes_on_its_own(self):
    """A pause you must remember to undo is one that disables the feature for a month."""
    det = self._det(minutes=0.02)   # ~1.2 s
    det.params.suspend = True
    det.update(make_sm(), CRUISE_MS, True)
    assert det.suspended_seconds > 0
    for _ in range(int(2.0 / DT_MDL)):
      det.update(make_sm(), CRUISE_MS, True)
    assert det.suspended_seconds == 0.0
    run(det, STUCK_FRAMES)
    assert det.suggestion == Side.left, "did not resume after the countdown"

  def test_timers_do_not_accumulate_while_paused(self):
    det = self._det()
    det.params.suspend = True
    det.update(make_sm(), CRUISE_MS, True)
    run(det, STUCK_FRAMES)
    assert det.approach_seconds == 0.0
    assert det.approach_seconds == 0.0


class TestSetSpeedIsTheClusterSpeed:
  """The reported failure, pinned exactly.

  Set 80, lead doing 65, and it reported "nothing slower ahead". The deficit was being measured
  against carState.vCruiseCluster -- VCruiseHelper's number, which depends on pcmCruise wiring that
  changes once ICBM manages the target -- instead of against the set speed on the dash.
  """

  MPH = 0.44704

  def _drive(self, set_mph, lead_mph, ego_mph=None, d_rel=120.):
    det = PassingAssistDetector()
    ego = (ego_mph if ego_mph is not None else set_mph) * self.MPH
    for _ in range(int(3.0 / DT_MDL)):
      det.update(make_sm(v_ego=ego, v_lead=lead_mph * self.MPH, d_rel=d_rel,
                         set_speed=set_mph * self.MPH), set_mph * self.MPH, True)
    return det

  def test_set_80_lead_65_suggests_a_pass(self):
    det = self._drive(80, 65, ego_mph=80, d_rel=150.)
    assert det.suggestion == Side.left, "the exact case that reported nothing slower ahead"

  def test_a_modest_deficit_is_enough(self):
    """Five mph under, which is the everyday case -- not a dramatically slower vehicle."""
    det = self._drive(80, 75, ego_mph=76)
    assert det.suggestion == Side.left

  def test_inside_traffic_variation_is_ignored(self):
    """Two mph is where another car's cruise hunting lives. Above the threshold by default so it
    does not fire on a car that is not really slower."""
    det = self._drive(80, 78, ego_mph=79)
    assert det.suggestion == Side.none

  def test_measured_against_the_set_speed_not_our_current_speed(self):
    """Already slowed to the lead's speed: current speed says no deficit, set speed says 15 mph."""
    det = self._drive(80, 65, ego_mph=65, d_rel=40.)
    assert det.suggestion == Side.left

  def test_a_lead_at_the_set_speed_is_not_passed(self):
    det = self._drive(80, 80, ego_mph=80)
    assert det.suggestion == Side.none

  def test_falls_back_when_the_cluster_reports_nothing(self):
    det = PassingAssistDetector()
    for _ in range(int(3.0 / DT_MDL)):
      det.update(make_sm(v_ego=CRUISE_MS, v_lead=CRUISE_MS - 7.0, d_rel=120., set_speed=0.0),
                 CRUISE_MS, True)
    assert det.suggestion == Side.left


class TestReferenceSpeedIsTheDriversIntent:
  """With ICBM running, the dash value is the CURRENT commanded set speed -- ICBM lowers it for
  curves, speed limits and the radar-blind lead. Differencing against it means every lead stops
  looking slow the moment anything slows the car, which is exactly when a pass is wanted.
  """

  MPH = 0.44704

  def _drive(self, frames=int(3.0 / DT_MDL), sl=0.0, **kw):
    det = PassingAssistDetector()
    for _ in range(frames):
      det.update(make_sm(**kw), CRUISE_MS, True, sl)
    return det

  def test_icbm_lowered_the_dash_but_the_hold_is_the_intent(self):
    """Driver holds 80, ICBM has dropped the dash to 68 for a curve, lead is doing 65."""
    det = self._drive(v_ego=68 * self.MPH, v_lead=65 * self.MPH, d_rel=60.,
                      set_speed=68 * self.MPH, icbm_hold=80 * self.MPH, icbm_manual=True)
    assert det.reference_source == RefSource.icbmHold
    assert det.suggestion == Side.left

  def test_speed_limit_plus_offset_is_the_intent_when_sla_drives(self):
    """SLA following 75+offset, ICBM momentarily showing 66, lead doing 64."""
    det = self._drive(sl=78 * self.MPH, v_ego=66 * self.MPH, v_lead=64 * self.MPH, d_rel=60.,
                      set_speed=66 * self.MPH)
    assert det.reference_source == RefSource.speedLimit
    assert det.suggestion == Side.left

  def test_dash_value_is_used_when_nothing_else_is_higher(self):
    det = self._drive(v_ego=80 * self.MPH, v_lead=65 * self.MPH, d_rel=120.,
                      set_speed=80 * self.MPH)
    assert det.reference_source == RefSource.cluster
    assert det.reference_speed > 79 * self.MPH

  def test_icbm_hold_ignored_while_in_auto(self):
    """A stale baseline must not raise the reference when ICBM is not actually holding it."""
    det = self._drive(v_ego=70 * self.MPH, v_lead=69 * self.MPH, d_rel=60.,
                      set_speed=70 * self.MPH, icbm_hold=95 * self.MPH, icbm_manual=False)
    assert det.reference_source == RefSource.cluster

  def test_reference_never_below_the_dash(self):
    det = self._drive(v_ego=80 * self.MPH, v_lead=78 * self.MPH, d_rel=60.,
                      set_speed=80 * self.MPH, icbm_hold=50 * self.MPH, icbm_manual=True)
    assert det.reference_speed >= 79 * self.MPH


class TestLookAheadDistance:
  MPH = 0.44704

  def test_a_deficit_above_the_threshold_reaches_full_distance(self):
    """The point of dropping TTC: reach must not shrink as the speed difference shrinks."""
    det = PassingAssistDetector()
    for _ in range(int(3.0 / DT_MDL)):
      det.update(make_sm(v_ego=80 * self.MPH, v_lead=75 * self.MPH, d_rel=200.,
                         set_speed=80 * self.MPH), CRUISE_MS, True)
    assert det.suggestion == Side.left

  def test_beyond_the_look_ahead_is_ignored(self):
    det = PassingAssistDetector()
    for _ in range(int(3.0 / DT_MDL)):
      det.update(make_sm(v_ego=80 * self.MPH, v_lead=75 * self.MPH, d_rel=300.,
                         set_speed=80 * self.MPH), CRUISE_MS, True)
    assert det.suggestion == Side.none
