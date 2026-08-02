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
  PassingAssistDetector, MIN_LANE_WIDTH_M, MIN_V_EGO_MS,
)

Side = custom.LongitudinalPlanSP.PassingAssist.Side
Blocked = custom.LongitudinalPlanSP.PassingAssist.Blocked
Reason = custom.LongitudinalPlanSP.PassingAssist.Reason

CRUISE_MS = 31.0            # ~70 mph set speed
SLOW_LEAD_MS = 24.0         # ~54 mph lead -> ~7 m/s deficit, over the 8 mph default


def xyz(y):
  return NS(y=[y] * 33)


def make_sm(*, v_lead=SLOW_LEAD_MS, v_ego=None, d_rel=40., d_path=0.2, status=True,
            left_bs=False, right_bs=False, blis_avail=True,
            # geometry: ego lane lines at -1.85/+1.85, road edges default to one clear lane left
            ll=(-5.5, -1.85, 1.85, 5.5), probs=(0.9, 0.99, 0.99, 0.2),
            edges=(-5.6, 2.4), edge_stds=(0.1, 0.1),
            tsr_avail=True, ovtk_msg=1, ovtk_status=2,
            blinker=False, brake=False, steering=False, road_name="I 15"):
  # Being stuck behind a car means matching its speed, not still closing on it: vEgo tracks vLead
  # and the gap to the SET speed is what makes passing worth suggesting. Tests that need a genuine
  # approach pass v_ego explicitly.
  if v_ego is None:
    v_ego = v_lead
  v_rel = v_lead - v_ego
  return {
    'carState': NS(vEgo=v_ego, brakePressed=brake, steeringPressed=steering,
                   leftBlinker=blinker, rightBlinker=False,
                   leftBlindspot=left_bs, rightBlindspot=right_bs),
    'radarState': NS(leadOne=NS(status=status, dRel=d_rel, vRel=v_rel, vLead=v_lead, dPath=d_path)),
    'modelV2': NS(laneLines=[xyz(v) for v in ll], laneLineProbs=list(probs),
                  roadEdges=[xyz(v) for v in edges], roadEdgeStds=list(edge_stds)),
    'carStateBP': NS(blisLeft=NS(dataAvailable=blis_avail), blisRight=NS(dataAvailable=blis_avail),
                     trafficSignData=NS(dataAvailable=tsr_avail, overtakeMsg=ovtk_msg,
                                        overtakeStatus=ovtk_status)),
    'liveMapDataSP': NS(roadName=road_name),
  }


def run(det, frames, **kw):
  for _ in range(frames):
    det.update(make_sm(**kw), CRUISE_MS, True)
  return det


# Enough frames to clear the default 25 s stuck timer with margin.
STUCK_FRAMES = int(26.0 / DT_MDL)


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
  def test_not_stuck_until_the_timer_elapses(self):
    det = run(PassingAssistDetector(), int(5.0 / DT_MDL))
    assert det.suggestion == Side.none
    assert det.blocked_by == Blocked.notStuck
    assert 4.0 < det.stuck_seconds < 6.0

  def test_stuck_timer_resets_when_lead_drops(self):
    det = PassingAssistDetector()
    run(det, int(20.0 / DT_MDL))
    assert det.stuck_seconds > 19.0
    run(det, 1, status=False)
    assert det.stuck_seconds == 0.0

  def test_still_closing_is_not_stuck(self):
    # Approaching a slower car is not the same as being held behind it: still at the set speed,
    # closing at 10 m/s. The timer must not start until we have actually been slowed down.
    det = run(PassingAssistDetector(), STUCK_FRAMES,
              v_lead=CRUISE_MS - 10.0, v_ego=CRUISE_MS, d_rel=55.)
    assert det.blocked_by == Blocked.notStuck

  def test_small_deficit_is_not_worth_passing(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, v_lead=CRUISE_MS - 1.0)
    assert det.blocked_by == Blocked.notStuck

  def test_below_min_speed_blocks(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, v_ego=MIN_V_EGO_MS - 1.0)
    assert det.blocked_by == Blocked.tooSlow

  def test_driver_input_blocks_and_resets(self):
    det = PassingAssistDetector()
    run(det, int(20.0 / DT_MDL))
    run(det, 1, blinker=True)
    assert det.blocked_by == Blocked.driverActive
    assert det.stuck_seconds == 0.0

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


# Geometry with a clear lane on the RIGHT and nothing usable on the left (i.e. we are sitting in
# the left lane of a divided highway).
IN_LEFT_LANE = dict(probs=(0.1, 0.99, 0.99, 0.9), edges=(-2.2, 5.7))
KEEP_RIGHT_FRAMES = int(11.0 / DT_MDL)



class _KeepRightOnParams:
  """Keep right is DEFAULT OFF in params_keys.h -- the exit-lane ambiguity makes it unsafe to
  lean on. These tests opt in explicitly so the default stays honest and a future flip of that
  default cannot silently make them pass for the wrong reason."""

  def get(self, key, block=False, return_default=False):
    return {"PassingAssistMinDeficit": 8, "PassingAssistStuckTime": 25,
            "PassingAssistKeepRightDelay": 10}[key]

  def get_bool(self, key, block=False):
    return True


def keep_right_det():
  det = PassingAssistDetector()
  det.params = _KeepRightOnParams()
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
