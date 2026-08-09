"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

BluePilot: behavioral tests for the phase-1 passing-assist observer.

The detector cannot actuate anything, so these are not safety tests. They cover the two ways a
log-only observer can still waste a drive's worth of data:

  - recording a suggestion that was never actually gated (blind spot unavailable being silently
    read as "clear", geometry passing on one evidence channel alone)
  - blockedBy reporting the wrong gate, which would make the whole dataset misleading about which
    filter is doing the work

Plus the sign convention on lane geometry, which is easy to get backwards and impossible to spot
in a log after the fact.
"""

import inspect
import re
from types import SimpleNamespace as NS

from cereal import custom
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot.selfdrive.controls.lib.passing_maneuver import CHANGE_DURATION_S
from openpilot.sunnypilot.selfdrive.controls.lib.passing_assist import (
  PassingAssistDetector, MIN_LANE_WIDTH_M, DEFAULT_MIN_SPEED_MPH, MAX_WIDENING_M,
  SUGGESTION_HOLD_S,
  DEFAULT_PERSISTENCE_S, DRIVE_HISTORY_MAX, CHIME_MIN_INTERVAL_S,
  TIMELINE_MAX,
)

Side = custom.LongitudinalPlanSP.PassingAssist.Side
Blocked = custom.LongitudinalPlanSP.PassingAssist.Blocked
Reason = custom.LongitudinalPlanSP.PassingAssist.Reason
Trigger = custom.LongitudinalPlanSP.PassingAssist.Trigger
RefSource = custom.LongitudinalPlanSP.PassingAssist.ReferenceSource
Phase = custom.LongitudinalPlanSP.PassingAssist.Maneuver

CRUISE_MS = 31.0            # ~70 mph set speed
SLOW_LEAD_MS = 24.0         # ~54 mph lead -> ~7 m/s deficit, over the 8 mph default


X_IDXS = [192.0 * (i / 32.0) ** 2 for i in range(33)]


def xyz(y, widen=0.0):
  """33 points along X_IDXS. `widen` grows the value with distance, which is what an exit or
  on-ramp looks like on the right road edge."""
  return NS(x=list(X_IDXS), y=[y + widen * (i / 32.0) ** 2 * (32 / 20.0) ** 2 for i in range(33)])


def path(curve_radius_m=0.0):
  """The model's predicted path. Straight by default.

  `curve_radius_m` bends it right by the usual small-angle displacement d^2 / 2R (negative radius
  bends left). This is the geometry that put our own lead in the next lane: at 70 m on a 500 m
  bend the path is 4.9 m off the car's straight-ahead axis, which is the middle of the
  adjacent-lane band.
  """
  if not curve_radius_m:
    return NS(x=list(X_IDXS), y=[0.0] * 33)
  return NS(x=list(X_IDXS), y=[(x * x) / (2.0 * curve_radius_m) for x in X_IDXS])


class FakeSubMaster:
  """Mimics cereal SubMaster, deliberately NOT a dict.

  SubMaster exposes __getitem__ and no __contains__, so `'x' in sm` falls back to sequence
  iteration and calls sm[0] -- KeyError: 0. A dict fixture makes that work perfectly in tests and
  crash-loop plannerd on the car, which is what happened. Anything sm-shaped here must reproduce
  SubMaster's actual protocol, not a convenient superset.

  alive/valid/updated are plain dicts keyed by service, as in the real thing, and a missing service
  raises KeyError rather than defaulting -- the same reason. Code that reads them must handle the
  absent case explicitly instead of inheriting a convenient True.
  """

  def __init__(self, data: dict, updated: dict | None = None):
    self.data = data
    self.alive = dict.fromkeys(data, True)
    self.valid = dict.fromkeys(data, True)
    self.updated = dict.fromkeys(data, True) if updated is None else updated

  def __getitem__(self, s):
    return self.data[s]

  def __delitem__(self, s):
    del self.data[s]
    self.alive.pop(s, None)
    self.valid.pop(s, None)
    self.updated.pop(s, None)


def make_sm(*, v_lead=SLOW_LEAD_MS, v_ego=None, d_rel=40., lead_y=0.0, status=True,
            left_bs=False, right_bs=False, blis_avail=True,
            # geometry: ego lane lines at -1.85/+1.85, road edges default to one clear lane left
            #
            # The left edge is 1.5 m PAST the far-left line, not 0.1 m short of it as it was. The
            # old numbers put the road edge and the outermost lane line on top of each other, which
            # is what the model does when there is NO lane out there -- so the fixture that stood
            # for "one clear lane left" was drawing the shoulder case, and every gate written
            # against it inherited that. The right side keeps the shoulder geometry deliberately:
            # edge at 2.4 is barely past ego's own line, so the default road is one lane plus a
            # shoulder, which is the road this gets driven on.
            ll=(-5.5, -1.85, 1.85, 5.5), probs=(0.9, 0.99, 0.99, 0.2),
            edges=(-7.0, 2.4), edge_stds=(0.1, 0.1), right_edge_widen=0.0,
            tsr_avail=True, ovtk_msg=1, ovtk_status=2,
            blinker=False, blinker_right=False, brake=False, steering=False, road_name="I 15", curve=0.0,
            acc_braking=False, acc_precharge=False, acc_propulsion=0.0,
            acc_avail=True, set_speed=None,
            icbm_hold=0.0, icbm_manual=False, lka=False, tracks=(),
            lead_accel=0.0, lead_radar=True):
  # Being stuck behind a car means matching its speed, not still closing on it: vEgo tracks vLead
  # and the gap to the SET speed is what makes passing worth suggesting. Tests that need a genuine
  # approach pass v_ego explicitly.
  if v_ego is None:
    v_ego = v_lead
  v_rel = v_lead - v_ego
  return FakeSubMaster({
    'carState': NS(vEgo=v_ego, brakePressed=brake, steeringPressed=steering,
                   leftBlinker=blinker, rightBlinker=blinker_right,
                   leftBlindspot=left_bs, rightBlindspot=right_bs,
                   cruiseState=NS(speedCluster=set_speed if set_speed is not None else CRUISE_MS)),
    # leadOne yRel is LEFT-POSITIVE, like the radar's. dPath is deliberately absent: nothing
    # in openpilot populates it, and a fixture that supplied it let a dead gate look alive.
    'radarState': NS(leadOne=NS(status=status, dRel=d_rel, yRel=lead_y, vRel=v_rel, vLead=v_lead,
                              aLeadK=lead_accel, radar=lead_radar, modelProb=0.9)),
    'modelV2': NS(laneLines=[xyz(v) for v in ll], laneLineProbs=list(probs),
                  roadEdges=[xyz(edges[0]), xyz(edges[1], widen=right_edge_widen)],
                  roadEdgeStds=list(edge_stds), position=path(curve)),
    'carStateBP': NS(lkaButtonPressed=lka,
                     brakeLightStatus=NS(accDataAvailable=acc_avail, accDecelRequest=acc_braking,
                                         accPrechargeRequest=acc_precharge,
                                         accPropulsionRequest=acc_propulsion),
                     blisLeft=NS(dataAvailable=blis_avail), blisRight=NS(dataAvailable=blis_avail),
                     trafficSignData=NS(dataAvailable=tsr_avail, overtakeMsg=ovtk_msg,
                                        overtakeStatus=ovtk_status)),
    'liveMapDataSP': NS(roadName=road_name),
    'selfdriveStateSP': NS(intelligentCruiseButtonManagement=NS(
        # The ENUMERANT NAME, which is what str() on a live capnp _DynamicEnum gives. It was 1/0
        # here, and an int is exactly what the device never sends -- a fixture laxer than the real
        # message, which is the shape of most of this file's real bugs.
        vBaseline=icbm_hold, overrideState="manual" if icbm_manual else "auto")),
    # Front-radar object list. Empty by default: alive and reporting nothing beside us, which is
    # "the next lane is clear" -- NOT the same as the unavailable case, which tests remove the
    # service entirely to produce.
    'liveTracks': NS(points=list(tracks)),
  })


def track(d_rel, y_rel, v_rel):
  """One liveTracks point. yRel is LEFT-POSITIVE here, matching the radar and NOT the lane
  geometry above -- the two frames are opposite and that is the point of stating it twice."""
  return NS(dRel=d_rel, yRel=y_rel, vRel=v_rel)


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
    # left line -1.85 out to left edge -7.0 is 5.15 m -- the next lane AND its shoulder, which is
    # the reason this number is published and no longer gated on.
    assert abs(det.left_edge_gap - 5.15) < 0.01
    # ...and the two halves it is made of, each positive on both sides. If either came out negative
    # a side would be silently unavailable forever with nothing in a log to say why.
    assert abs(det.left_lane_width - 3.65) < 0.01    # -1.85 to the far-left line at -5.5
    assert abs(det.left_edge_beyond - 1.5) < 0.01    # -5.5 out to the edge at -7.0
    assert det.right_lane_width > 0
    assert det.right_edge_beyond < 0, "the default right side is a shoulder, and reads as one"

  def test_two_way_road_still_reports_a_lane(self):
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
    waiting period, which is the behavior this whole design exists to remove.

    Measured as a FRACTION of the configured time rather than a hardcoded second. This test used to
    run for exactly 1.0 s against a 2 s default, so it broke the moment the default became 1 -- it
    was asserting the old number, not the behavior.
    """
    det = PassingAssistDetector()
    half = int((DEFAULT_PERSISTENCE_S / 2) / DT_MDL)
    run(det, half)
    assert det.suggestion == Side.none
    assert det.blocked_by == Blocked.nothingSlower
    assert 0.0 < det.approach_seconds < DEFAULT_PERSISTENCE_S
    # ...and it does complete, so the case above is "not yet" rather than "never".
    run(det, half + 4)
    assert det.suggestion == Side.left

  def test_an_intermittent_radar_track_still_confirms(self):
    """The reported failure, and the one a decay rate alone did NOT fix: "it would go in and out of
    range and so the pass would keep resetting."

    Decaying three times faster than it accumulates means a track has to survive 75% of frames just
    to break even -- below that the timer never reaches the threshold however long the car sits
    there. A grace window is what makes a dropped return genuinely free. At 50% here, which is far
    worse than any real radar.
    """
    det = PassingAssistDetector()
    for i in range(STUCK_FRAMES * 2):
      det.update(make_sm(status=(i % 2 == 0)), CRUISE_MS, True)
    assert det.suggestion == Side.left
    assert det.reason == Reason.passing

  def test_a_lead_hovering_at_the_look_ahead_distance_still_confirms(self):
    """Same failure at the range boundary rather than the tracking one."""
    det = PassingAssistDetector()
    for i in range(STUCK_FRAMES * 2):
      det.update(make_sm(d_rel=215.0 if i % 2 == 0 else 225.0), CRUISE_MS, True)
    assert det.suggestion == Side.left

  def test_a_lead_that_is_really_gone_still_clears_it(self):
    """The other half. The grace window is not a memory: sustained absence must reach zero, or a
    car that left would keep a stale confirmation alive behind it."""
    det = PassingAssistDetector()
    run(det, STUCK_FRAMES)
    assert det.approach_seconds > 0
    run(det, int(2.0 / DT_MDL), status=False)
    assert det.approach_seconds == 0.0
    assert not det.lead_is_slow

  def test_a_brief_dropout_costs_nothing_at_all(self):
    det = PassingAssistDetector()
    run(det, STUCK_FRAMES)
    before = det.approach_seconds
    run(det, 3, status=False)
    assert det.approach_seconds == before, "inside the grace window a lost return must be free"



class TestSpeedBoundary:
  """Reported from the car: "when a car is going in between the speed I want to pass at and the
  speed I don't. Same with it coming in and out of radar range."

  Raised as a complaint about the visualisation, and half of it was. The other half was a genuine
  refusal to pass -- every frame below the threshold zeroed the confirmation timer, so a vehicle
  sitting on the line never accumulated two seconds and never produced a suggestion. Not late:
  never. And a car only slightly slower than you is the case this feature most obviously exists
  for.
  """

  # Deficit either side of the 4 mph default, by well under the 1 mph hysteresis band.
  JUST_OVER = CRUISE_MS - 4.3 * CV.MPH_TO_MS
  JUST_UNDER = CRUISE_MS - 3.8 * CV.MPH_TO_MS

  def test_a_vehicle_hovering_on_the_threshold_is_still_passed(self):
    det = PassingAssistDetector()
    for i in range(STUCK_FRAMES):
      v = self.JUST_OVER if i % 2 == 0 else self.JUST_UNDER
      det.update(make_sm(v_lead=v), CRUISE_MS, True)
    assert det.lead_is_slow
    assert det.suggestion == Side.left
    assert det.reason == Reason.passing

  def test_a_vehicle_that_is_genuinely_not_slower_is_never_passed(self):
    """The hysteresis must not become a licence to pass anything. Below the band, and it never
    latches in the first place."""
    det = run(PassingAssistDetector(), STUCK_FRAMES, v_lead=self.JUST_UNDER)
    assert not det.lead_is_slow
    assert det.suggestion == Side.none
    assert det.blocked_by == Blocked.nothingSlower

  def test_it_takes_a_clear_margin_to_release(self):
    """Once judged slow it stays judged slow until meaningfully faster -- that is the whole point
    of latching it. Two mph clear of the threshold is meaningfully faster."""
    det = run(PassingAssistDetector(), STUCK_FRAMES)
    assert det.lead_is_slow
    run(det, 3, v_lead=self.JUST_UNDER)
    assert det.lead_is_slow, "released inside the noise band -- the chatter is back"
    run(det, 3, v_lead=CRUISE_MS - 2.0 * CV.MPH_TO_MS)
    assert not det.lead_is_slow
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
    assert det.blocked_by == Blocked.nothingSlower

  def test_below_min_speed_blocks(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, v_ego=DEFAULT_MIN_SPEED_MPH * CV.MPH_TO_MS - 1.0)
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
              probs=(0.9, 0.99, 0.99, 0.9), edges=(-7.0, 7.0))
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

# Two lanes each way, sitting in the left. Keep-right works here now: nothing about the geometry
# says exit, so the widening and lane-age gates carry it instead of a blanket refusal.
# The right edge is 3 m past the far-right line, not 0.2 m past it. A road edge sitting on the
# outermost lane line is what the model gives when there is NO lane out there, so the old
# numbers described a shoulder while claiming to describe a two-lane road.
TWO_LANE_ROAD = dict(probs=(0.1, 0.99, 0.99, 0.9), edges=(-2.2, 8.5))

# No lane to our right at all: painted line gone and no drivable width to the edge. Used to age a
# lane up from zero.
NO_RIGHT_LANE = dict(probs=(0.1, 0.99, 0.99, 0.1), edges=(-2.2, 2.4))

# Long enough to clear BOTH keep-right clocks: the 10 s clear-lane delay and the 15 s lane age.
# They are separate, and a fixture that only cleared one would make the other untestable.
KEEP_RIGHT_FRAMES = int(16.0 / DT_MDL)
DELAY_FRAMES = int(11.0 / DT_MDL)   # clears the delay alone, NOT the age



# Every params stub reads from this one dict. They used to each carry their own literal, so adding
# a single new param raised KeyError in nine unrelated tests at once, none of which were about the
# new param. Defaults live in params_keys.h; these mirror them.
_STUB_PARAM_DEFAULTS = {
  "PassingAssistMinDeficit": 4, "PassingAssistConfirmTime": 1,
  "PassingAssistKeepRightDelay": 10, "PassingAssistSettleTime": 20,
  "PassingAssistMaxDistance": 220,
  "PassingAssistOncomingMemory": 90, "PassingAssistBlinkerLead": 1, "PassingAssistMinApproach": 0, "PassingAssistMinSpeed": 30, "PassingAssistExitStandDown": 45, "PassingAssistCrawlTime": 8, "PassingAssistMinLaneAge": 15,
}


class _KeepRightOnParams:
  """Keep right is DEFAULT OFF in params_keys.h -- the exit-lane ambiguity makes it unsafe to
  lean on. These tests opt in explicitly so the default stays honest and a future flip of that
  default cannot silently make them pass for the wrong reason."""

  def __init__(self, **overrides):
    self.values = dict(_STUB_PARAM_DEFAULTS, **overrides)

  def get(self, key, block=False, return_default=False):
    return self.values[key]

  def get_bool(self, key, block=False):
    # Overrides first, so a BOOL param can actually be switched off in a test. It could not before:
    # this answered True for everything except the suspend one-shot, whatever was passed in, so any
    # test of a toggle's OFF state quietly asserted nothing.
    if key in self.values:
      return bool(self.values[key])
    return True

  def put_bool(self, key, val, block=False):
    pass


def keep_right_det(**overrides):
  det = PassingAssistDetector()
  det.params = _KeepRightOnParams(**overrides)
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


class TestKeepRightIsOnForMeasurement:
  """It shipped OFF, because an exit-only lane is geometrically a through lane and a suggestion
  there can mean take the exit. Two things guard that now -- the road opening up ahead, and the
  lane having been continuously present -- and both are unproven on real roads.

  On, deliberately, for exactly that reason. Nothing actuates, so a wrong suggestion costs a wrong
  line on a screen and buys the measurement that would settle it. Off, it is the one built feature
  that can never produce any data at all.
  """

  def test_it_is_on_by_default_now(self):
    det = run(PassingAssistDetector(), KEEP_RIGHT_FRAMES, status=False, **IN_LEFT_LANE)
    assert det.suggestion == Side.right
    assert det.reason == Reason.keepRight

  def test_and_the_gates_still_hold(self):
    """On is not the same as unguarded. A lane that just appeared is still refused."""
    det = run(PassingAssistDetector(), KEEP_RIGHT_FRAMES, status=False, **NO_RIGHT_LANE)
    run(det, int(11.0 / DT_MDL), status=False, **IN_LEFT_LANE)
    assert det.suggestion == Side.none


class TestRearApproachGate:
  def test_no_rear_sensor_does_not_block_a_suggestion(self):
    """Today's behavior: nothing is fitted, so the gate must not silently kill the feature."""
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



class TestAccBrakingMetric:
  """accBrakingAtDecision answers one question: had Ford's ACC already started slowing the car
  when we decided? It is the measure of whether passing assist beats ACC to a lead, so getting it
  wrong does not break a feature -- it silently makes the drive data say nothing.

  Two errors were fixed here at once, in opposite directions, which is why neither showed up as an
  obviously wrong number."""

  def test_precharge_is_not_braking(self):
    # Pressurising the brakes produces no deceleration, no stop lamps and no pad wear. A suggestion
    # made here beat ACC to the decision and must be recorded as preemptive.
    det = run(PassingAssistDetector(), STUCK_FRAMES, acc_precharge=True)
    assert not det.acc_braking_at_decision
    assert det.acc_precharge_at_decision
    assert det.trigger == Trigger.approaching

  def test_a_decel_request_is_braking(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, acc_braking=True)
    assert det.acc_braking_at_decision
    assert det.trigger == Trigger.heldUp

  def test_engine_braking_counts_as_braking(self):
    # Ford slows by downshifting to spare the pads. No lamps, no wear -- but the car is losing
    # speed for this lead, which is exactly what the metric asks about.
    det = run(PassingAssistDetector(), STUCK_FRAMES, acc_propulsion=-1.2)
    assert det.acc_braking_at_decision
    assert det.trigger == Trigger.heldUp

  def test_the_inactive_sentinel_is_not_engine_braking(self):
    # AccPrpl_A_Rq's floor means "nothing requested", not a -5 m/s2 demand. Reading it as engine
    # braking would mark every decision reactive.
    det = run(PassingAssistDetector(), STUCK_FRAMES, acc_propulsion=-5.0)
    assert not det.acc_braking_at_decision
    assert det.trigger == Trigger.approaching

  def test_trim_around_zero_is_not_engine_braking(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, acc_propulsion=-0.05)
    assert not det.acc_braking_at_decision

  def test_unavailable_acc_data_reports_neither(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, acc_avail=False, acc_braking=True)
    assert not det.acc_braking_available
    assert not det.acc_braking_at_decision


class TestDrivingBelowTheLimit:
  """Setting a speed below the posted limit is a choice, not a condition to be talked out of.

  The reference speed used to be max(dash, limit + offset, ICBM baseline). On its own that is
  sound -- ICBM only ever moves the dash DOWN, so a maximum recovers the driver's baseline. It
  breaks the moment the limit is ABOVE anything the driver asked for, which is an ordinary Tuesday:
  set 60 where the limit plus offset is 70, and a car ahead doing 62 -- genuinely faster than you
  chose to travel -- measured as 8 under and produced a suggestion to pass it.
  """

  MPH = CV.MPH_TO_MS

  def _drive(self, **kw):
    det = PassingAssistDetector()
    for _ in range(STUCK_FRAMES):
      det.update(make_sm(**kw), CRUISE_MS, True, 70 * self.MPH)   # limit + offset = 70
    return det

  def test_a_manual_hold_below_the_limit_is_respected(self):
    det = self._drive(v_ego=60 * self.MPH, v_lead=62 * self.MPH, d_rel=60.,
                      set_speed=60 * self.MPH, icbm_hold=60 * self.MPH, icbm_manual=True)
    assert abs(det.reference_speed - 60 * self.MPH) < 0.1
    assert det.reference_source == RefSource.icbmHold
    # 62 is faster than we asked to go, so there is nothing to pass.
    assert det.suggestion == Side.none

  def test_the_limit_still_applies_without_an_override(self):
    # No override: the dash may have been lowered by ICBM for a curve or a limit, so the higher of
    # the two really is the driver's baseline.
    det = self._drive(v_ego=60 * self.MPH, v_lead=60 * self.MPH, d_rel=60.,
                      set_speed=60 * self.MPH, icbm_manual=False)
    assert abs(det.reference_speed - 70 * self.MPH) < 0.1
    assert det.reference_source == RefSource.speedLimit

  def test_a_manual_hold_above_the_limit_is_also_respected(self):
    det = self._drive(v_ego=80 * self.MPH, v_lead=70 * self.MPH, d_rel=60.,
                      set_speed=80 * self.MPH, icbm_hold=80 * self.MPH, icbm_manual=True)
    assert abs(det.reference_speed - 80 * self.MPH) < 0.1
    assert det.suggestion == Side.left

  def test_the_dash_still_floors_it_under_an_override(self):
    # ICBM sets its baseline from the cluster when the override latches, so the two agree in
    # normal operation. If they ever drift, the dash is the number the car is actually driving to.
    det = self._drive(v_ego=80 * self.MPH, v_lead=78 * self.MPH, d_rel=60.,
                      set_speed=80 * self.MPH, icbm_hold=50 * self.MPH, icbm_manual=True)
    assert det.reference_speed >= 79 * self.MPH


class TestLeadInLaneGate:
  """The lead must be in OUR lane, measured against the model path.

  This gate used to read lead.dPath, which nothing in openpilot populates -- it arrived as 0.0, so
  `abs(0.0) > 1.5` rejected nothing and the gate only looked like a filter. The fixture supplied a
  dPath, which is exactly how a dead gate passes for a live one.
  """

  def test_a_lead_in_our_lane_still_passes_the_gate(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, lead_y=0.3)
    assert det.suggestion == Side.left
    assert det.lead_d_path < 1.0

  def test_an_off_path_return_is_rejected(self):
    # A radar return a lane over is not a reason to pass. Before the fix this suggested anyway.
    det = run(PassingAssistDetector(), STUCK_FRAMES, lead_y=3.7)
    assert det.blocked_by == Blocked.nothingSlower
    assert det.suggestion == Side.none

  def test_a_lead_on_a_curve_is_still_in_our_lane(self):
    # Measured from the car's axis, a lead at 70 m on a 500 m bend is 4.9 m off and would look
    # like it was two lanes over. Measured from the path, it is where it actually is: in front.
    det = run(PassingAssistDetector(), STUCK_FRAMES, d_rel=70., lead_y=-4.9, curve=500.0)
    assert det.lead_d_path < 1.0
    assert det.reason == Reason.passing


class TestAdjacentLaneGate:
  """The front radar's off-path tracks, wired into the decision.

  Distinct from every other gate here in what it is protecting against: not safety, but a
  suggestion that would be immediately regretted. So it must be the LAST thing consulted and must
  never take the blame in blockedBy from a gate that is about safety.
  """

  def test_clear_next_lane_still_suggests(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES)
    assert det.suggestion == Side.left
    assert det.adjacent.available
    assert not det.adjacent.left.occupied

  def test_left_lane_full_of_traffic_no_faster_blocks_the_pass(self):
    # The maneuver this exists to prevent: pull out to pass a car doing 24 m/s and land behind
    # one doing the same. yRel is LEFT-POSITIVE.
    det = run(PassingAssistDetector(), STUCK_FRAMES, tracks=[track(70, 3.7, 0.0)])
    assert det.adjacent.left.occupied
    assert det.suggestion == Side.none
    assert det.blocked_by == Blocked.adjacentSlow

  def test_faster_traffic_in_the_left_lane_does_not_block(self):
    # Occupied is not the test. A lane moving 5 m/s faster than the car we are stuck behind is
    # exactly the lane we want.
    det = run(PassingAssistDetector(), STUCK_FRAMES, tracks=[track(70, 3.7, 5.0)])
    assert det.adjacent.left.occupied
    assert det.suggestion == Side.left

  def test_comparison_is_against_the_lead_not_the_set_speed(self):
    # Crawling traffic: everything is well under the 31 m/s set speed, but the left lane is still
    # 4 m/s better than what we are behind. Measuring against the set speed here would refuse
    # every pass in exactly the conditions where passing matters most.
    det = run(PassingAssistDetector(), STUCK_FRAMES, v_lead=20.0, tracks=[track(70, 3.7, 4.0)])
    assert det.suggestion == Side.left

  def test_right_lane_traffic_does_not_block_a_left_pass(self):
    """edges widened deliberately. The default fixture puts the right road edge at 2.4 m, so a
    "vehicle" at 3.7 m to the right is off the drivable surface -- and this test used to assert it
    was occupying a lane, which is the bug he reported: "it kept seeing curbs as other cars, even
    though I could see a red line on the curb too." A right lane needs a right edge beyond it."""
    det = run(PassingAssistDetector(), STUCK_FRAMES, edges=(-7.0, 7.0),
              tracks=[track(60, -3.7, 0.0)])
    assert det.adjacent.right.occupied
    assert det.suggestion == Side.left

  def test_nothing_beyond_the_road_edge_is_traffic(self):
    """The elevated sidewalk past the right shoulder, and the fifty vehicles that overtook him in a
    few minutes. Same geometry as above but with the default edge at 2.4 m, so the track sits
    outside it -- and outside our drivable surface is not our road, whatever its range rate says."""
    det = run(PassingAssistDetector(), STUCK_FRAMES, tracks=[track(60, -3.7, 0.0)])
    assert not det.adjacent.right.occupied, "scenery past the road edge counted as a vehicle"
    assert det.adjacent.right.overtaken_count == 0, "scenery counted as an overtake"

  def test_no_radar_data_does_not_block(self):
    det = PassingAssistDetector()
    for _ in range(STUCK_FRAMES):
      sm = make_sm()
      del sm['liveTracks']
      det.update(sm, CRUISE_MS, True)
    assert not det.adjacent.available
    assert det.suggestion == Side.left

  def test_blind_spot_is_reported_over_slow_traffic(self):
    # Ordering: a side stopped by both must report the safety reason, not the pointless one.
    det = run(PassingAssistDetector(), STUCK_FRAMES, left_bs=True, tracks=[track(70, 3.7, 0.0)])
    assert det.blocked_by == Blocked.blindspotOccupied

  def test_disabling_the_gate_leaves_it_unavailable(self):
    class _Off(_KeepRightOnParams):
      def get_bool(self, key, block=False):
        if key == "PassingAssistAdjacentLane":
          return False
        return super().get_bool(key, block)

    det = PassingAssistDetector()
    det.params = _Off()
    for _ in range(STUCK_FRAMES):
      det.update(make_sm(tracks=[track(70, 3.7, 0.0)]), CRUISE_MS, True)
    assert not det.adjacent.available
    assert det.suggestion == Side.left


class TestOncomingVeto:
  """A two-lane two-way road passes every geometry test as though the oncoming lane were a
  passing lane. This is the gate that stops it, and it is the only one here guarding against a
  dangerous suggestion rather than a merely wasted one."""

  # 27 m/s the other way, one lane left. Default geometry already reports a clear lane there.
  ONCOMING = [track(90, 3.7, -27.0 - SLOW_LEAD_MS)]

  def test_oncoming_traffic_stops_the_pass(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, tracks=self.ONCOMING)
    assert det.adjacent.oncoming_any_side
    assert det.suggestion == Side.none
    assert det.blocked_by == Blocked.oncomingLane

  def test_it_outlasts_the_car(self):
    # The dangerous window: the oncoming car has gone by, the left lane looks clear and inviting,
    # and it is still the lane they are using.
    det = PassingAssistDetector()
    # Three sightings, not one: the veto now needs corroboration. See ONCOMING_FRAMES -- it latched
    # on a single return until a drive on I-15 showed what that costs on a divided road.
    run(det, 3, tracks=self.ONCOMING)
    run(det, STUCK_FRAMES)
    assert det.suggestion == Side.none
    assert det.blocked_by == Blocked.oncomingLane

  def test_it_outranks_the_sign_veto(self):
    # Both apply constantly on a two-lane road. The road fact is reported, because it explains a
    # sustained silence where a no-passing zone explains a passing one.
    det = run(PassingAssistDetector(), STUCK_FRAMES, tracks=self.ONCOMING, ovtk_msg=2, ovtk_status=2)
    assert det.blocked_by == Blocked.oncomingLane

  def test_a_four_lane_two_way_road_keeps_the_other_side(self):
    """The case the per-side veto exists for, and the reason it is not a whole-road one.

    Left lane of a four-lane two-way arterial: the oncoming lane is one over to the LEFT, and an
    ordinary through lane is one over to the RIGHT. Giving up on both would throw away every
    arterial in the state to protect against a lane that is only on one side.
    """
    det = run(PassingAssistDetector(), STUCK_FRAMES, tracks=self.ONCOMING,
              probs=(0.9, 0.99, 0.99, 0.9), edges=(-7.0, 7.0))
    assert det.adjacent.left.blocks_oncoming
    assert not det.adjacent.right.blocks_oncoming
    assert det.suggestion == Side.right
    assert det.reason == Reason.passing

  def test_a_two_lane_road_has_no_other_side_to_keep(self):
    # Same veto, default geometry: the shoulder is not a lane, so nothing is suggested and the
    # reason names the road rather than the missing lane.
    det = run(PassingAssistDetector(), STUCK_FRAMES, tracks=self.ONCOMING)
    assert det.suggestion == Side.none
    assert det.blocked_by == Blocked.oncomingLane

  def test_a_divided_highway_is_unaffected(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES)
    assert not det.adjacent.oncoming_any_side
    assert det.suggestion == Side.left

  def test_the_veto_can_be_turned_off(self):
    class _Off(_KeepRightOnParams):
      def get_bool(self, key, block=False):
        if key == "PassingAssistOncomingVeto":
          return False
        return super().get_bool(key, block)

    det = PassingAssistDetector()
    det.params = _Off()
    for _ in range(STUCK_FRAMES):
      det.update(make_sm(tracks=self.ONCOMING), CRUISE_MS, True)
    assert det.adjacent.oncoming_any_side     # still measured and logged
    assert det.suggestion == Side.left  # but not acted on


class TestKeepRightOncoming:
  """Keep-right must respect the oncoming gate too. It did not, and the pass path did -- exactly
  the ordering bug the rear-approach interface was built early to avoid."""

  # Opposing traffic in the lane to our RIGHT. Rare, and Utah has the road: 5400 South runs three
  # reversible flex lanes, so which side is theirs changes by time of day.
  ONCOMING_RIGHT = [track(90, -3.7, -27.0 - CRUISE_MS)]

  def test_never_keep_right_into_opposing_traffic(self):
    det = run(keep_right_det(), KEEP_RIGHT_FRAMES, status=False, v_ego=CRUISE_MS,
              tracks=self.ONCOMING_RIGHT, **IN_LEFT_LANE)
    assert det.adjacent.right.blocks_oncoming
    assert det.suggestion == Side.none
    assert det.keep_right_seconds == 0.0

  def test_opposing_traffic_on_the_LEFT_does_not_stop_keep_right(self):
    # An ordinary two-way road: they are on the left, the lane to our right is ours, and moving
    # over is exactly the right thing to do.
    det = run(keep_right_det(), KEEP_RIGHT_FRAMES, status=False, v_ego=CRUISE_MS,
              tracks=[track(90, 3.7, -27.0 - CRUISE_MS)], **IN_LEFT_LANE)
    assert not det.adjacent.right.blocks_oncoming
    assert det.suggestion == Side.right
    assert det.reason == Reason.keepRight


class TestKeepRightAdjacentLane:
  def test_will_not_move_over_behind_slow_traffic(self):
    # "Keep right except to pass" assumes the right lane is moving. Dropping in behind a car slow
    # enough to trip the passing threshold buys two lane changes and no progress.
    # Cruising at the set speed with nothing ahead, which is the situation keep-right applies to.
    det = run(keep_right_det(), KEEP_RIGHT_FRAMES, status=False, v_ego=CRUISE_MS,
              tracks=[track(80, -3.7, -5.0)], **IN_LEFT_LANE)
    assert det.adjacent.right.occupied
    assert det.suggestion == Side.none
    assert det.keep_right_seconds == 0.0

  def test_moves_over_behind_traffic_at_our_own_speed(self):
    det = run(keep_right_det(), KEEP_RIGHT_FRAMES, status=False, v_ego=CRUISE_MS,
              tracks=[track(80, -3.7, 0.0)], **IN_LEFT_LANE)
    assert det.adjacent.right.occupied
    assert det.suggestion == Side.right
    assert det.reason == Reason.keepRight


class TestLaneAge:
  """The owner's own exit test: an exit lane did not exist a moment ago and now does, whereas a
  through lane has been beside us for miles. This replaced a blanket refusal to enter the outermost
  lane, which bought the same safety by giving up keep-right on every two-lane road.

  Every case here runs at least KEEP_RIGHT_FRAMES, so the 10 s clear-lane delay is always satisfied
  and age is the only thing that can be deciding.
  """

  def test_a_lane_that_has_always_been_there_is_suggested(self):
    det = run(keep_right_det(), KEEP_RIGHT_FRAMES, status=False, **IN_LEFT_LANE)
    assert det.right_lane_age_s >= det.min_lane_age_s
    assert det.suggestion == Side.right
    assert det.reason == Reason.keepRight

  def test_a_lane_that_just_appeared_is_refused(self):
    """The exit case. Nothing to our right, then a lane opens up -- that is a gore, not a lane to
    settle into. The clear-lane delay elapses inside this window and must not be enough alone."""
    det = run(keep_right_det(), KEEP_RIGHT_FRAMES, status=False, **NO_RIGHT_LANE)
    assert det.right_lane_age_s == 0.0
    run(det, DELAY_FRAMES, status=False, **IN_LEFT_LANE)
    assert det.keep_right_seconds >= det.keep_right_delay_s    # the OTHER clock is satisfied
    assert det.right_lane_age_s < det.min_lane_age_s
    assert det.suggestion == Side.none

  def test_and_is_accepted_once_it_has_lasted(self):
    """Same detector, same lane, five more seconds. If this did not flip, the case above would be
    passing on something other than age."""
    det = run(keep_right_det(), KEEP_RIGHT_FRAMES, status=False, **NO_RIGHT_LANE)
    run(det, DELAY_FRAMES, status=False, **IN_LEFT_LANE)
    assert det.suggestion == Side.none
    run(det, int(5.0 / DT_MDL), status=False, **IN_LEFT_LANE)
    assert det.suggestion == Side.right
    assert det.reason == Reason.keepRight

  def test_losing_the_lane_restarts_the_clock(self):
    """Fails safe: an occlusion or faded paint costs a few quiet seconds rather than risking a
    fresh lane being read as an old one."""
    det = run(keep_right_det(), KEEP_RIGHT_FRAMES, status=False, **IN_LEFT_LANE)
    assert det.suggestion == Side.right
    run(det, 1, status=False, **NO_RIGHT_LANE)
    assert det.right_lane_age_s == 0.0
    run(det, DELAY_FRAMES, status=False, **IN_LEFT_LANE)
    assert det.suggestion == Side.none

  def test_our_own_lane_change_restarts_the_clock(self):
    """The hole the age gate had: it aged the SIDE, not the LANE.

    Three lanes, sitting in the middle. Lane three has been beside us for a minute, so its age is
    well past the gate. The driver moves right into it -- and now the lane on our right is a
    different piece of road, which we have never looked at, and which on a highway is very often an
    exit-only lane. Nothing zeroed the counter, so that brand-new lane inherited the previous one's
    sixty seconds and sailed through the one test built to catch exactly it.

    The four second post-change settle does not cover this: it is shorter than the ten second
    clear-lane delay, so it expires with the stale age still standing.

    Resetting on a LEFT change too is deliberate. The lane to our right afterwards is the one we
    just left, so its age is genuinely known -- but re-proving it costs nothing (the 20 s anti-weave
    settle is longer than the 15 s age anyway) and a gate that has to reason about which direction
    we went is a gate with a second way to be wrong.

    A 30 s age rather than the default 15 so the timing is not a coincidence. The post-change
    stand-down holds the clear-lane clock at zero for its first four seconds, so at the default the
    delay is satisfied at 14 s and age at 15 -- a one second window in which age is provably the
    thing deciding. That is too narrow to tell a real gate from an arithmetic accident.
    """
    det = run(keep_right_det(PassingAssistMinLaneAge=30), int(35.0 / DT_MDL),
              status=False, **IN_LEFT_LANE)
    assert det.suggestion == Side.right                      # aged in, as before

    run(det, int(2.0 / DT_MDL), status=False, blinker_right=True, **IN_LEFT_LANE)
    run(det, 1, status=False, **IN_LEFT_LANE)                # stalk off: the change is done
    assert det.right_lane_age_s == 0.0, "a new lane inherited the old lane's age"

    # Both clocks run again from zero and CONCURRENTLY -- the wait is the 30 s age, not 30 + 10.
    run(det, int(20.0 / DT_MDL), status=False, **IN_LEFT_LANE)
    assert det.keep_right_seconds >= det.keep_right_delay_s   # the other clock is satisfied
    assert det.right_lane_age_s < det.min_lane_age_s          # this one is not
    assert det.suggestion == Side.none, "suggested moving into an unproven lane"
    run(det, int(12.0 / DT_MDL), status=False, **IN_LEFT_LANE)
    assert det.suggestion == Side.right                       # and it comes back once proven

  def test_zero_disables_the_gate(self):
    """The control goes down to 0 s, and there the widening test is on its own."""
    det = keep_right_det(PassingAssistMinLaneAge=0)
    run(det, KEEP_RIGHT_FRAMES, status=False, **NO_RIGHT_LANE)
    run(det, DELAY_FRAMES, status=False, **IN_LEFT_LANE)
    assert det.min_lane_age_s == 0.0
    assert det.suggestion == Side.right

  def test_passing_is_unaffected_by_the_keep_right_gates(self):
    """They are scoped to keep-right. Overtaking on the right is a different decision and the
    driver is choosing to make it."""
    det = run(PassingAssistDetector(), STUCK_FRAMES, left_bs=True,
              probs=(0.9, 0.99, 0.99, 0.9), edges=(-7.0, 7.0))
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
    det = keep_right_det()
    run(det, KEEP_RIGHT_FRAMES, status=False, right_edge_widen=4.0, **TWO_LANE_ROAD)
    assert det.suggestion == Side.none
    assert det.keep_right_seconds == 0.0

  def test_two_lane_road_works_when_not_widening(self):
    """...and the point of it: with no exit ahead, a two-lane road now gets the suggestion."""
    det = keep_right_det()
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
    """The deficit is the judgment. Everything else is a sanity bound."""
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
    if key == "PassingAssistSuspendMinutes":
      return self.minutes
    return _STUB_PARAM_DEFAULTS[key]

  def get_bool(self, key, block=False):
    return self.suspend if key == "PassingAssistSuspend" else True

  def put_bool(self, key, val, block=False):
    if key == "PassingAssistSuspend":
      self.suspend = bool(val)




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




class TestPublish:
  """publish() is forty lines of field copying into capnp and had no test at all.

  That is the one place a rename lands silently: the detector keeps its Python attribute, the
  schema gets the new field name, and the assignment in between raises only on the car. It also
  owns the confirmSeconds/approachSeconds duplicate, which nothing else can check.
  """

  @staticmethod
  def _published(det):
    msg = custom.LongitudinalPlanSP.new_message()
    det.publish(msg.passingAssist)
    return msg.passingAssist

  def test_every_field_assignment_survives_the_schema(self):
    """Catches a field renamed in custom.capnp without its publish() line, which is an
    AttributeError on the device and nothing at all offline."""
    det = run(PassingAssistDetector(), STUCK_FRAMES)
    pa = self._published(det)
    assert str(pa.suggestion) == 'left'
    assert str(pa.reason) == 'passing'
    assert str(pa.blockedBy) == 'none'

  def test_confirm_seconds_is_the_confirmation_timer(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES)
    # Float32 on the wire against a Float64 accumulator, so a tolerance rather than equality.
    assert abs(self._published(det).confirmSeconds - det.approach_seconds) < 1e-4

  def test_the_deprecated_alias_still_matches(self):
    """approachSeconds @29 duplicates confirmSeconds @2. The ordinal cannot be reclaimed, so the
    field stays and keeps being written -- but the two must never drift, or a log becomes
    ambiguous about which one meant anything."""
    det = run(PassingAssistDetector(), STUCK_FRAMES)
    pa = self._published(det)
    assert pa.approachSeconds == pa.confirmSeconds

  def test_nothing_slower_ahead_is_reported_as_such(self):
    """The blocked reason the driver sees most often, and the one whose name just changed."""
    det = run(PassingAssistDetector(), STUCK_FRAMES, v_lead=CRUISE_MS)
    assert str(self._published(det).blockedBy) == 'nothingSlower'


class TestClosingIn:
  """The owner: "I would like to get as close to the car as I can before making the lane change,
  as long as Ford ACC brakes the least amount."

  Two requirements pulling opposite ways. The resolution is the ACC override, not the distance --
  which is why the override is tested harder than the distance is.
  """

  @staticmethod
  def _det(approach_m):
    det = PassingAssistDetector()
    det.params = _KeepRightOnParams(PassingAssistMinApproach=approach_m)
    return det

  def test_off_by_default_nothing_changes(self):
    """0 must be genuinely off, not 'hold until 0 m away'."""
    det = run(PassingAssistDetector(), STUCK_FRAMES, d_rel=200.0)
    assert not det.closing_in
    assert det.suggestion == Side.left

  def test_it_waits_while_still_a_long_way_back(self):
    det = run(self._det(150), STUCK_FRAMES, d_rel=200.0)
    assert det.closing_in
    assert det.suggestion == Side.none
    assert det.blocked_by == Blocked.closingIn

  def test_and_goes_the_moment_it_is_close_enough(self):
    """The confirmation ran underneath the hold, so reaching the distance starts the maneuver at
    once rather than beginning a fresh two-second wait -- which would hand the time straight back."""
    det = self._det(150)
    run(det, STUCK_FRAMES, d_rel=200.0)
    assert det.suggestion == Side.none
    run(det, 2, d_rel=140.0)
    assert det.suggestion == Side.left, "should not restart the confirmation after the hold"

  def test_acc_braking_abandons_the_hold_at_any_distance(self):
    """The safety valve, and the reason 'as close as possible' is safe to ask for. Set the hold
    absurdly tight: the moment ACC asks for deceleration it goes anyway."""
    det = run(self._det(20), STUCK_FRAMES, d_rel=200.0, acc_braking=True)
    assert not det.closing_in
    assert det.suggestion == Side.left

  def test_precharge_counts_too(self):
    """Brakes pressurised for that lead is ACC deciding it will need them. Waiting past that point
    is waiting until braking is already committed."""
    det = run(self._det(20), STUCK_FRAMES, d_rel=200.0, acc_precharge=True)
    assert not det.closing_in
    assert det.suggestion == Side.left

  def test_the_hold_never_applies_without_a_slow_car(self):
    """closingIn must mean "waiting to pass this one", never leak into an empty road."""
    det = run(self._det(150), STUCK_FRAMES, v_lead=CRUISE_MS, d_rel=200.0)
    assert not det.closing_in
    assert det.blocked_by != Blocked.closingIn


class TestLeadBraking:
  """People do not pass a braking car. They are usually turning off -- so the pass was never
  needed -- or braking for something ahead you cannot see yet, which is the worst moment to pull
  out. Neither cause is visible to any sensor here; the braking stands in for both.
  """

  def test_a_hard_braking_lead_holds_the_pass(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, lead_accel=-4.0)
    assert det.lead_braking_hold
    assert det.suggestion == Side.none
    assert det.blocked_by == Blocked.leadBraking

  def test_ordinary_slowing_is_not_slamming_on(self):
    """The line that makes this useful rather than crippling, and the owner moved it: "we can pass
    a car that is slowing down a little, just not if they are slamming on their brakes."

    -2.0 is ordinary traffic braking. A car shedding speed is the single best reason to go round
    it -- it is about to cost you more, not less -- so this must NOT hold."""
    for accel in (-0.3, -1.0, -2.0):
      det = run(PassingAssistDetector(), STUCK_FRAMES, lead_accel=accel)
      assert not det.lead_braking_hold, f"held for {accel} m/s^2, which is not slamming on"
      assert det.suggestion == Side.left

  def test_the_hold_outlives_the_braking(self):
    """A driver braking for a turn lifts off, coasts, brakes again. The pause is not an
    invitation, and it also absorbs one noisy acceleration estimate."""
    det = run(PassingAssistDetector(), STUCK_FRAMES, lead_accel=-4.0)
    run(det, int(1.0 / DT_MDL), lead_accel=0.0)
    assert det.lead_braking_hold, "released the instant the number crossed back"
    run(det, int(1.0 / DT_MDL), lead_accel=0.0)
    assert not det.lead_braking_hold
    assert det.suggestion == Side.left

  def test_it_can_be_turned_off(self):
    class _Off(_KeepRightOnParams):
      def get_bool(self, key, block=False):
        return False if key == "PassingAssistLeadBrakingHold" else super().get_bool(key)
    det = PassingAssistDetector()
    det.params = _Off()
    run(det, STUCK_FRAMES, lead_accel=-4.0)
    assert not det.lead_braking_hold
    assert det.suggestion == Side.left

  def test_no_lead_never_reports_a_braking_hold(self):
    """leadBraking must mean "that car is stopping", never leak onto an empty road."""
    det = run(PassingAssistDetector(), STUCK_FRAMES, status=False)
    assert not det.lead_braking_hold
    assert det.blocked_by != Blocked.leadBraking


class TestWhereTheTimeWent:
  """blockedBy says what is stopping a pass right now. This says where the drive's time actually
  went, which is the question that decides what to build next -- and it is invisible from the
  panel, where every reason looks equally common because each is only on screen for a moment.
  """

  def test_an_empty_road_is_not_evidence_about_anything(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, status=False)
    assert det.wanted_seconds == 0.0
    assert det.top_blocked == (int(Blocked.none), 0.0)

  def test_a_car_going_our_speed_is_not_evidence_either(self):
    """A pass was never wanted, so nothing that happened counts toward why one did not happen."""
    det = run(PassingAssistDetector(), STUCK_FRAMES, v_lead=CRUISE_MS)
    assert det.wanted_seconds == 0.0

  def test_the_binding_gate_is_named(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, left_bs=True, edges=(-2.3, 2.4))
    key, share = det.top_blocked
    assert det.wanted_seconds > 0
    assert key == int(Blocked.noLaneAvailable)
    assert share > 0.9

  def test_a_clean_drive_reports_clear_rather_than_a_gate(self):
    """Nothing stopping it must NOT win the "what stopped it" question -- that would hide the real
    answer behind good news on any drive that mostly worked."""
    det = run(PassingAssistDetector(), STUCK_FRAMES)
    assert det.clear_share > 0.5
    assert det.top_blocked[0] == int(Blocked.none), "no gate should be named when none was binding"

  def test_the_dominant_gate_wins_over_a_brief_one(self):
    det = PassingAssistDetector()
    # The confirmation is not attributed to any gate, so only the time AFTER it lands in the
    # histogram. Written relative to the setting: with it hardcoded, changing the confirmation
    # silently changed the ratio this asserts and the test failed for a reason it was not about.
    brief = int((DEFAULT_PERSISTENCE_S + 2.0) / DT_MDL)      # 2 s of blind spot
    dominant = int(6.0 / DT_MDL)                             # 6 s of no lane
    run(det, brief, left_bs=True, right_bs=True)
    run(det, dominant, left_bs=True, edges=(-2.3, 2.4))
    key, share = det.top_blocked
    assert key == int(Blocked.noLaneAvailable)
    assert 0.6 < share < 1.0


class TestMinimumSpeed:
  """The 40 mph floor failed in the case it most mattered: stuck behind something slow, ACC drags
  you below it, and the system goes quiet exactly when a pass is most obviously wanted."""

  def test_a_tractor_on_a_back_road_is_now_passed(self):
    """35 mph behind something doing 25 -- the case the old floor threw away."""
    det = run(PassingAssistDetector(), STUCK_FRAMES,
              v_lead=11.0, v_ego=15.6, set_speed=24.6)   # ~25 / ~35 / ~55 mph
    assert det.suggestion == Side.left
    assert det.reason == Reason.passing

  def test_still_silent_at_walking_pace(self):
    """The floor still has a job: below it the geometry stops meaning what it says, because a turn
    pocket or a driveway looks exactly like a lane."""
    det = run(PassingAssistDetector(), STUCK_FRAMES, v_lead=5.0, v_ego=8.0, set_speed=24.6)
    assert det.suggestion == Side.none
    assert det.blocked_by == Blocked.tooSlow

  def test_the_floor_is_adjustable(self):
    class _Slow(_KeepRightOnParams):
      def get(self, key, block=False, return_default=False):
        return 20 if key == "PassingAssistMinSpeed" else super().get(key)
    det = PassingAssistDetector()
    det.params = _Slow()
    run(det, STUCK_FRAMES, v_lead=5.0, v_ego=10.0, set_speed=24.6)
    assert det.blocked_by != Blocked.tooSlow


class TestPublishTheNewFields:
  """publish() is the one place in this feature that can take the car down.

  A capnp assignment that the schema rejects raises inside plannerd, and plannerd dying is a device
  stuck on "waiting to start" -- the exact failure a duplicate CAN registration caused once
  already. The behavioral suite never reaches these lines, because every one of them is a plain
  assignment that only fails against the real schema.

  The enum fields are the sharp edge: they are assigned from ints here, and whether capnp accepts
  an int for an enum field is not something the Python side reveals until it runs.
  """

  @staticmethod
  def _published(det):
    msg = custom.LongitudinalPlanSP.new_message()
    det.publish(msg.passingAssist)
    return msg.passingAssist

  def test_enum_fields_assigned_from_ints_survive_the_schema(self):
    det = PassingAssistDetector()
    run(det, int(4.0 / DT_MDL), left_bs=True, edges=(-2.3, 2.4))
    pa = self._published(det)
    assert str(pa.topBlockedBy) == 'noLaneAvailable'
    assert str(pa.maneuver) in ('idle', 'confirming', 'waiting', 'signaling', 'changing', 'finishing')
    assert str(pa.maneuverSide) in ('none', 'left', 'right')
    assert str(pa.crawlSide) in ('none', 'left', 'right')

  def test_every_field_in_the_schema_is_actually_published(self):
    """EVERY field, read out of the schema -- not a list somebody has to remember to extend.

    This was named test_every_new_field_round_trips and checked a hardcoded seventeen. The schema
    has ninety-four. So the sixty-odd added since it was written were covered by its name and by
    nothing else, and the next field would have been too: a reviewer reading the title would
    reasonably conclude the case was handled.

    A field declared and never assigned in publish() is the failure that matters, and it is silent
    in the worst way -- capnp hands back the type's zero, so the reader gets 0.0 or `none` and every
    consumer treats it as a real measurement. That is the same fault as the three drive-summary
    numbers that were saved and never drawn, one layer further down.
    """
    det = run(PassingAssistDetector(), STUCK_FRAMES)
    pa = self._published(det)
    declared = set(pa.schema.fieldnames)
    assert len(declared) > 80, f"schema reflection returned only {len(declared)} fields"

    src = inspect.getsource(PassingAssistDetector.publish)
    # Any REFERENCE, not just an assignment: the nested groups (adjacentLeft, rearRight and
    # friends) are filled in place by iterating over them, so they are never on the left of an `=`
    # and a stricter pattern reports all four as missing.
    assigned = set(re.findall(r'passingAssist\.(\w+)', src))
    missing = sorted(declared - assigned)
    assert not missing, (
      f"declared in custom.capnp but never assigned in publish(): {missing}. Each one reads as the "
      f"type's zero on the car -- a measurement that is always 0.0 or 'none' and looks real.")

    # ...and the other direction, which is an AttributeError on the device and nothing offline.
    for name in sorted(assigned):
      getattr(pa, name)

  def test_counters_saturate_rather_than_wrap(self):
    """UInt16 rolling over to 0 would read as a clean drive, which is the opposite of what a huge
    count means."""
    det = PassingAssistDetector()
    det.maneuver.aborts = 99999
    det.overtake.crawl_events = 99999
    pa = self._published(det)
    assert pa.maneuverAborts == 65535
    assert pa.crawlEvents == 65535


class TestKeepRightManeuver:
  """Moving back over is half of what the finished system does and had no dry run at all -- the
  readout went straight from "MOVE RIGHT" to nothing."""

  def test_deciding_to_move_right_starts_its_own_sequence(self):
    det = run(keep_right_det(), KEEP_RIGHT_FRAMES, status=False, **IN_LEFT_LANE)
    assert det.suggestion == Side.right
    live, reason = det.live_maneuver
    assert reason == Reason.keepRight
    assert live is det.keep_right_maneuver
    assert live.blinker_on

  def test_the_passing_machine_stays_out_of_it(self):
    """Separate machines so the abort counts stay separate -- that number is the readiness metric
    for each maneuver, and one combined figure would not say which was unstable."""
    det = run(keep_right_det(), KEEP_RIGHT_FRAMES, status=False, **IN_LEFT_LANE)
    assert det.maneuver.phase == Phase.idle
    assert det.maneuver.aborts == 0

  def test_it_signals_when_decided_rather_than_early(self):
    """Unlike passing, where signaling early beats ACC to the brakes. Nothing is being raced when
    moving back over, and a blinker lit through the whole keep-right delay would be several seconds
    of announcing a maneuver that may not happen."""
    det = keep_right_det()
    run(det, int(4.0 / DT_MDL), status=False, **IN_LEFT_LANE)
    assert det.suggestion == Side.none, "delay has not elapsed"
    assert not det.keep_right_maneuver.blinker_on
    assert det.keep_right_maneuver.phase == Phase.idle

  def test_a_pass_takes_the_screen_back(self):
    """Only one can run: keep-right is evaluated solely on frames where no pass is warranted."""
    det = run(keep_right_det(), KEEP_RIGHT_FRAMES, status=False, **IN_LEFT_LANE)
    assert det.live_maneuver[1] == Reason.keepRight
    run(det, STUCK_FRAMES, **IN_LEFT_LANE)     # a slow lead appears
    assert det.reason == Reason.passing
    assert det.live_maneuver[1] == Reason.passing

  def test_the_driver_taking_over_ends_it(self):
    det = run(keep_right_det(), KEEP_RIGHT_FRAMES, status=False, **IN_LEFT_LANE)
    assert det.keep_right_maneuver.blinker_on
    run(det, 2, status=False, blinker=True, **IN_LEFT_LANE)
    assert det.keep_right_maneuver.phase == Phase.idle
    assert det.keep_right_maneuver.aborts == 0, "a takeover is the right outcome, not an abort"


class TestDriverOwnLaneChange:
  """Asked directly: "what if I do a sunnypilot, nudgeless lane change into an exit lane? Will it
  try to pull me out of that?"

  It would have. The driver-active gate silences this only WHILE the blinker is on; the moment it
  goes out the system re-evaluates from scratch, and an exit lane is geometrically a slow lane with
  somewhere to go.
  """

  EXIT = dict(right_edge_widen=4.0, **IN_LEFT_LANE)

  def test_taking_an_exit_buys_a_long_silence(self):
    det = keep_right_det()
    run(det, int(2.0 / DT_MDL), blinker_right=True, **self.EXIT)
    run(det, 2, **self.EXIT)                              # stalk off: the change is done
    assert det.driver_change_was_exit
    assert det.driver_change_standdown > 30.0
    assert det.blocked_by == Blocked.driverChangedLanes

  def test_an_ordinary_lane_change_hands_control_straight_back(self):
    """"Sometimes I'll do a nudgeless lane change if passing assist doesn't pass, but I still want
    it to take over." So a manual pass costs seconds, not the better part of a minute."""
    det = keep_right_det()
    run(det, int(2.0 / DT_MDL), blinker=True, **IN_LEFT_LANE)
    run(det, 2, **IN_LEFT_LANE)
    assert not det.driver_change_was_exit
    assert 0.0 < det.driver_change_standdown <= 4.0

  def test_and_it_does_come_back(self):
    det = keep_right_det()
    run(det, int(2.0 / DT_MDL), blinker=True, **IN_LEFT_LANE)
    run(det, int(5.0 / DT_MDL), status=False, **IN_LEFT_LANE)
    assert det.driver_change_standdown == 0.0
    assert det.blocked_by != Blocked.driverChangedLanes

  def test_signaling_left_over_a_widening_road_is_not_an_exit(self):
    """The road opening up on the LEFT is a lane being added, not an exit being taken."""
    det = keep_right_det()
    run(det, int(2.0 / DT_MDL), blinker=True, **self.EXIT)
    run(det, 2, **self.EXIT)
    assert not det.driver_change_was_exit
    assert det.driver_change_standdown <= 4.0

  def test_the_exit_evidence_is_latched_while_signaling(self):
    """Once the car is in the ramp lane the road edge belongs to the ramp and the widening that
    identified it has gone -- so the only moment the evidence exists is during the maneuver."""
    det = keep_right_det()
    run(det, int(2.0 / DT_MDL), blinker_right=True, **self.EXIT)
    run(det, 2, **IN_LEFT_LANE)     # no widening visible any more
    assert det.driver_change_was_exit

  def test_it_never_fires_without_the_driver_touching_the_stalk(self):
    det = run(keep_right_det(), STUCK_FRAMES, **IN_LEFT_LANE)
    assert det.driver_change_standdown == 0.0
    assert det.blocked_by != Blocked.driverChangedLanes

  def test_the_stand_down_expires_while_slowing_for_the_ramp(self):
    """It used to be tracked after the speed gate, so dropping below the minimum froze it -- and
    slowing down is exactly what taking an exit involves. The pause would have been waiting on the
    driveway instead of expiring on the ramp."""
    det = keep_right_det()
    run(det, int(2.0 / DT_MDL), blinker_right=True, **TestDriverOwnLaneChange.EXIT)
    run(det, 2, **TestDriverOwnLaneChange.EXIT)
    started = det.driver_change_standdown
    run(det, int(10.0 / DT_MDL), v_lead=8.0, v_ego=9.0, **TestDriverOwnLaneChange.EXIT)
    assert det.driver_change_standdown < started - 9.0, "frozen while below the minimum speed"


class TestDriverSteeringTakeover:
  """"I usually use sunnypilot nudgeless changes, but I also will just fully takeover and do my own
  steering." Watching only the stalk would have missed that entirely, and steering onto an off-ramp
  without signaling is about as common as driving gets."""

  def test_a_sustained_takeover_stands_the_system_down(self):
    det = keep_right_det()
    run(det, int(1.5 / DT_MDL), steering=True, **IN_LEFT_LANE)
    run(det, 2, **IN_LEFT_LANE)
    assert det.driver_change_standdown > 0.0
    assert det.blocked_by == Blocked.driverChangedLanes

  def test_a_takeover_over_a_widening_road_reads_as_an_exit(self):
    det = keep_right_det()
    run(det, int(1.5 / DT_MDL), steering=True, **TestDriverOwnLaneChange.EXIT)
    run(det, 2, **IN_LEFT_LANE)
    assert det.driver_change_was_exit
    assert det.driver_change_standdown > 30.0

  def test_ordinary_corrections_are_not_takeovers(self):
    """The whole difficulty. steeringPressed fires constantly on the small corrections of normal
    driving, and a stand-down every time a hand tightens on the wheel would be worse than not
    having this at all."""
    det = keep_right_det()
    for _ in range(6):
      run(det, int(0.3 / DT_MDL), steering=True, **IN_LEFT_LANE)
      run(det, int(0.5 / DT_MDL), **IN_LEFT_LANE)
    assert det.driver_change_standdown == 0.0
    assert det.blocked_by != Blocked.driverChangedLanes

  def test_stalk_and_wheel_together_is_one_maneuver(self):
    """Doing both at once is normal. It must not double-count into two stand-downs, and the exit
    reading has to survive the wheel being released before the stalk."""
    det = keep_right_det()
    run(det, int(1.5 / DT_MDL), steering=True, blinker_right=True, **TestDriverOwnLaneChange.EXIT)
    run(det, int(0.5 / DT_MDL), blinker_right=True, **TestDriverOwnLaneChange.EXIT)
    run(det, 2, **IN_LEFT_LANE)
    assert det.driver_change_was_exit
    assert det.driver_change_standdown > 30.0


class TestAutoCloseIn:
  """The close-in hold had no safe default because the distance Ford's ACC starts braking at was
  unknown, and guessing it wrong means braking -- the one thing this all exists to avoid. It is
  measured now, so Auto takes the number from the car."""

  @staticmethod
  def _det(setting=-1, onset=0.0, last=None):
    class _P(_KeepRightOnParams):
      def __init__(self):
        super().__init__(PassingAssistMinApproach=setting)
        self.last = last

      def get(self, key, block=False, return_default=False):
        if key == "PassingAssistLastDrive":
          return self.last
        return super().get(key)

      def put(self, key, val, block=False):
        pass
    det = PassingAssistDetector()
    det.params = _P()
    det.acc_onset_max = onset
    return det

  def test_auto_holds_just_beyond_where_acc_gives_up(self):
    det = self._det(onset=137.0)
    run(det, STUCK_FRAMES, d_rel=200.0)
    assert 150.0 < det.min_approach_m < 165.0
    assert det.closing_in, "200 m is further out than the measured onset plus margin"

  def test_auto_does_nothing_until_something_has_been_measured(self):
    """No measurement is not a licence to guess. With nothing recorded there is no hold at all."""
    det = self._det(onset=0.0)
    run(det, STUCK_FRAMES, d_rel=200.0)
    assert det.min_approach_m == 0.0
    assert not det.closing_in
    assert det.suggestion == Side.left

  def test_a_manual_number_still_wins(self):
    det = self._det(setting=80, onset=137.0)
    run(det, STUCK_FRAMES, d_rel=200.0)
    assert det.min_approach_m == 80.0

  def test_off_means_off(self):
    det = self._det(setting=0, onset=137.0)
    run(det, STUCK_FRAMES, d_rel=200.0)
    assert det.min_approach_m == 0.0
    assert not det.closing_in

  def test_the_measurement_survives_the_ignition_cycle(self):
    """Otherwise Auto is inert for the first part of every drive -- until ACC happens to brake
    once -- which is exactly the part of a drive where it would do the most good."""
    det = self._det(onset=0.0, last={"accOnsetMax": 140.0})
    run(det, STUCK_FRAMES, d_rel=200.0)
    assert det.acc_onset_max == 140.0
    assert det.min_approach_m > 150.0


class TestGateOrder:
  """Which gate gets NAMED when several are true at once.

  Load-bearing twice over: it is what the driver reads on the panel, and it is what the drive
  summary's "mostly:" line counts. A gate inserted at the wrong point changes both silently -- the
  system still refuses correctly, it just blames the wrong thing, and every conclusion drawn from a
  drive afterwards is about the wrong gate.

  Sixteen reasons now, added over a dozen commits. Each pair below is a deliberate ordering, not an
  accident of where the code happened to grow.
  """

  def test_your_own_lane_change_outranks_you_are_driving(self):
    """The stand-down outlasts the stalk, so "you are driving" would read as not having noticed
    the stalk go off."""
    det = keep_right_det()
    run(det, int(2.0 / DT_MDL), blinker=True)
    run(det, 2)
    assert det.blocked_by == Blocked.driverChangedLanes

  def test_a_braking_lead_outranks_closing_in(self):
    """"That car is stopping" beats "still closing" when both are true -- it is the more specific
    reason and the one a driver would recognize out of the windscreen."""
    det = PassingAssistDetector()
    det.params = _KeepRightOnParams(PassingAssistMinApproach=50)
    run(det, STUCK_FRAMES, d_rel=200.0, lead_accel=-4.0)
    assert det.blocked_by == Blocked.leadBraking

  def test_no_lane_at_all_outranks_a_sign(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, edges=(-2.3, 2.4), ovtk_msg=2)
    assert det.blocked_by == Blocked.noLaneAvailable

  def test_oncoming_outranks_the_blind_spot(self):
    """Oncoming is the only gate here about a DANGEROUS maneuver rather than a wasted one, and it
    explains a sustained silence where the blind spot explains a passing one.

    Note for anyone changing this: oncoming is vetoed in TWO places -- an early return before the
    sign veto, and again in the per-side priority chain below it. The early one is what fires, so
    editing only the chain will not change this and will not show up here either.
    """
    det = run(PassingAssistDetector(), 3, tracks=[track(90, 3.7, -27.0 - CRUISE_MS)], left_bs=True)
    run(det, STUCK_FRAMES, tracks=[track(90, 3.7, -27.0 - CRUISE_MS)], left_bs=True, right_bs=True)
    assert det.blocked_by == Blocked.oncomingLane

  def test_the_blind_spot_outranks_a_slow_next_lane(self):
    """Reversed, a flickering blind spot would hide behind "the next lane is no faster", and the
    two mean opposite things about whether the maneuver was unsafe or merely pointless."""
    det = run(PassingAssistDetector(), STUCK_FRAMES, left_bs=True, right_bs=True,
              tracks=[track(80, 3.7, 0.0)])
    assert det.blocked_by == Blocked.blindspotOccupied

  def test_nothing_slower_never_masks_a_real_gate(self):
    """The confirmation timer reports nothingSlower while it runs. If that outranked the gates, a
    drive would look like it was mostly waiting to be sure when it was mostly being refused."""
    det = run(PassingAssistDetector(), STUCK_FRAMES, edges=(-2.3, 2.4))
    assert det.blocked_by == Blocked.noLaneAvailable


class TestNoRearSensingAtAll:
  """The owner's car has no BLIS routed and no rear radar. He believes that means "passes will
  never happen" -- it does not, and which of those two is true matters more than almost anything
  else here, because one of them means the whole feature is dead on his car today.

  The design is deliberate and documented in rear_approach.py: an UNAVAILABLE side does not block.
  Blocking would disable passing outright on a car with no rear radar and hide the real reason;
  answering "clear" would be a lie. So it neither blocks nor claims to have checked -- it suggests,
  and says on the panel that it could not look.
  """

  def test_a_pass_is_still_suggested_with_nothing_watching_behind(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, blis_avail=False)
    assert det.suggestion == Side.left
    assert det.reason == Reason.passing

  def test_and_it_is_marked_as_unchecked_rather_than_clear(self):
    """Level 2: the driver is responsible, which is exactly why a suggestion made with no rear
    sensing must be legible as one rather than passing for a checked one."""
    det = run(PassingAssistDetector(), STUCK_FRAMES, blis_avail=False)
    assert not det.blindspot_available
    assert not det.rear.left.available and not det.rear.right.available

  def test_an_occupied_blind_spot_still_blocks_when_it_IS_available(self):
    """The other half: unavailable must not become a blanket ignore."""
    det = run(PassingAssistDetector(), STUCK_FRAMES, left_bs=True, right_bs=True)
    assert det.suggestion == Side.none
    assert det.blocked_by == Blocked.blindspotOccupied

  def test_drifting_over_first_and_signaling_after_is_one_maneuver(self):
    """Found by reading rather than by a failure. The blinker's rising edge cleared the widening
    evidence, so a driver who drifts toward an exit and signals afterwards -- by which time the
    ramp's own road edge has replaced the widening -- got the four second pause instead of the
    full one. That is precisely the case the long stand-down exists for."""
    det = keep_right_det()
    run(det, int(1.5 / DT_MDL), steering=True, **TestDriverOwnLaneChange.EXIT)
    run(det, int(1.0 / DT_MDL), steering=True, blinker_right=True, **IN_LEFT_LANE)
    run(det, 2, **IN_LEFT_LANE)
    assert det.driver_change_was_exit
    assert det.driver_change_standdown > 30.0




class TestAgreementWithTheDriver:
  """The closest thing to a readiness score this phase can produce, and the most useful thing
  measurable before any sensor is fitted.

  Every gate here is checkable in isolation and none of that answers the only question that
  matters: when a real driver decided to pass a real car on a real road, had this system decided
  the same, and how long before? Agreeing on nine passes in ten and naming a gate for the tenth is
  ready to be trusted with a blinker; agreeing on half is not, whatever the unit tests say.
  """

  def test_a_pass_it_had_already_suggested_counts_as_agreement(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES)
    assert det.suggestion == Side.left
    run(det, 2, blinker=True)
    assert det.driver_passes == 1
    assert det.driver_passes_agreed == 1

  def test_the_lead_time_is_how_long_it_had_been_saying_so(self):
    """The whole benefit being claimed. If it only agrees at the moment the driver acts, it has
    added nothing over the driver's own eyes."""
    det = run(PassingAssistDetector(), STUCK_FRAMES + int(6.0 / DT_MDL))
    run(det, 2, blinker=True)
    assert det.driver_pass_lead_s > 5.0

  def test_a_pass_it_refused_is_counted_with_the_gate_that_refused_it(self):
    """The interesting half. "Missed two, both on oncoming" is a specific piece of work; "missed
    two" is not."""
    det = run(PassingAssistDetector(), STUCK_FRAMES, edges=(-2.3, 2.4))
    assert det.blocked_by == Blocked.noLaneAvailable
    run(det, 2, blinker=True, edges=(-2.3, 2.4))
    assert det.driver_passes == 1
    assert det.driver_passes_agreed == 0
    assert det.driver_pass_miss_reason == int(Blocked.noLaneAvailable)

  def test_it_is_sampled_on_the_rising_edge(self):
    """The driver-active gate blanks the suggestion on the very next frame, so sampled a moment
    later there would be nothing left to compare against and everything would read as a miss."""
    det = run(PassingAssistDetector(), STUCK_FRAMES)
    run(det, int(2.0 / DT_MDL), blinker=True)
    assert det.blocked_by == Blocked.driverActive
    assert det.driver_passes_agreed == 1, "sampled after the gate had already blanked it"

  def test_a_car_going_our_own_speed_counts_as_a_pass_it_disagreed_with(self):
    """It used to be excluded, on the reasoning that changing lanes past a car keeping pace is not
    really a pass. That reasoning hid the failure that matters most: a deficit threshold set too
    high would have discarded every pass it caused and reported a perfect score."""
    det = run(PassingAssistDetector(), STUCK_FRAMES, v_lead=CRUISE_MS)
    run(det, 2, blinker=True, v_lead=CRUISE_MS)
    assert det.driver_passes == 1
    assert det.driver_passes_agreed == 0

  def test_a_right_hand_signal_is_not_counted(self):
    """Ambiguous by nature -- an exit, a keep-right or a pass on the right all look identical, and
    a readiness score built on a guess is worse than a smaller honest one."""
    det = run(PassingAssistDetector(), STUCK_FRAMES)
    run(det, 2, blinker_right=True)
    assert det.driver_passes == 0

  def test_holding_the_stalk_is_one_pass(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES)
    run(det, int(4.0 / DT_MDL), blinker=True)
    assert det.driver_passes == 1


class TestSuggestionsNobodyTook:
  """The other error direction. TestAgreementWithTheDriver asks whether this system found the
  passes the driver made -- recall. It says nothing about passes this system offered that no sane
  driver would take, which is the error that matters once it is allowed to act on its own and is
  completely invisible in those numbers.
  """

  def test_a_suggestion_the_driver_acts_on_is_taken(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES)
    run(det, 2, blinker=True)
    assert det.suggestions_made == 1
    assert det.suggestions_taken == 1
    assert det.longest_ignored_s == 0.0

  def test_one_that_lapses_untaken_is_recorded_by_how_long_it_stood(self):
    """Not counted as WRONG -- an unacted suggestion is often just traffic changing its mind. What
    is recorded is duration, because three seconds and thirty seconds are different claims."""
    det = run(PassingAssistDetector(), STUCK_FRAMES + int(8.0 / DT_MDL))
    run(det, int(2.0 / DT_MDL), v_lead=CRUISE_MS)     # lead speeds up; nothing to pass
    assert det.suggestions_made == 1
    assert det.suggestions_taken == 0
    assert det.longest_ignored_s > 8.0

  def test_the_driver_acting_does_not_also_count_as_ignored(self):
    """The driver-active gate blanks the suggestion on the very next frame, which looks exactly
    like it lapsing -- so without closing the episode a taken pass would be counted both ways."""
    det = run(PassingAssistDetector(), STUCK_FRAMES + int(6.0 / DT_MDL))
    run(det, int(3.0 / DT_MDL), blinker=True)
    assert det.suggestions_taken == 1
    assert det.longest_ignored_s == 0.0

  def test_a_held_suggestion_is_one_episode(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES + int(20.0 / DT_MDL))
    assert det.suggestions_made == 1

  def test_the_longest_survives_a_later_shorter_one(self):
    """It is the worst case that says something, and a mean would bury it."""
    det = run(PassingAssistDetector(), STUCK_FRAMES + int(10.0 / DT_MDL))
    run(det, int(2.0 / DT_MDL), v_lead=CRUISE_MS)
    long_one = det.longest_ignored_s
    run(det, STUCK_FRAMES)
    run(det, int(2.0 / DT_MDL), v_lead=CRUISE_MS)
    assert det.suggestions_made == 2
    assert det.longest_ignored_s == long_one

  def test_a_car_it_never_judged_slow_still_counts_as_a_pass(self):
    """The hole this closes, and it was the worst one available: requiring the lead to be judged
    slow meant a pass the system never considered incremented nothing at all. A deficit threshold
    set too high would have shown up as a perfect score."""
    det = run(PassingAssistDetector(), STUCK_FRAMES, v_lead=CRUISE_MS - 1.0)   # ~2 mph, under 4
    assert not det.lead_is_slow
    run(det, 2, blinker=True, v_lead=CRUISE_MS - 1.0)
    assert det.driver_passes == 1
    assert det.driver_passes_agreed == 0
    assert det.driver_pass_miss_reason == int(Blocked.nothingSlower)

  def test_signaling_with_an_empty_road_is_still_not_a_pass(self):
    """The line has to be somewhere. No lead at all means no car was passed, whatever the stalk
    was for -- an exit, a junction, moving over for someone merging."""
    det = run(PassingAssistDetector(), STUCK_FRAMES, status=False)
    run(det, 2, blinker=True, status=False)
    assert det.driver_passes == 0

  def test_a_suggestion_still_standing_is_already_visible(self):
    """It used to be recorded only when the suggestion ENDED, so one still up when the drive ended
    was never counted. A system offering a single enormous unacted pass per drive would have
    reported a spotless record -- the exact opposite of what that behavior means."""
    det = run(PassingAssistDetector(), STUCK_FRAMES + int(25.0 / DT_MDL))
    assert det.suggestions_made == 1
    assert det.suggestions_taken == 0
    assert det.longest_ignored > 20.0
    assert det.longest_ignored_s == 0.0, "the episode has not ended, so nothing is banked yet"

  def test_and_a_taken_one_is_not_counted_while_it_runs(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES + int(10.0 / DT_MDL))
    run(det, 2, blinker=True)
    assert det.longest_ignored == 0.0


class TestLifetimeTotals:
  """One drive decides nothing. Seven passes swing by a third on a single odd stretch of road;
  eighty is what says whether this can be trusted with a blinker, and that is the question the
  whole phase exists to answer."""

  @staticmethod
  def _det(last=None):
    class _P(_KeepRightOnParams):
      def get(self, key, block=False, return_default=False):
        return last if key == "PassingAssistLastDrive" else super().get(key)

      def put(self, key, val, block=False):
        self.written = val
    det = PassingAssistDetector()
    det.params = _P()
    return det

  def test_this_drive_adds_to_what_came_before(self):
    det = self._det({"lifetimeDrives": 11, "lifetimePasses": 77, "lifetimeAgreed": 72})
    run(det, STUCK_FRAMES)
    run(det, 2, blinker=True)
    assert det.lifetime == (12, 78, 73)

  def test_a_drive_with_no_passes_is_not_counted_as_a_drive(self):
    """Otherwise a week of short trips inflates the denominator and the ratio looks worse than the
    driving was."""
    det = self._det({"lifetimeDrives": 11, "lifetimePasses": 77, "lifetimeAgreed": 72})
    run(det, STUCK_FRAMES, status=False)
    assert det.lifetime == (11, 77, 72)

  def test_the_periodic_save_cannot_count_the_drive_twice(self):
    """It is written every 30 s. Accumulating on write rather than computing would make the totals
    climb with the length of the drive rather than with what happened in it."""
    det = self._det({"lifetimeDrives": 3, "lifetimePasses": 20, "lifetimeAgreed": 18})
    run(det, STUCK_FRAMES)
    run(det, 2, blinker=True)
    first = det.lifetime
    run(det, int(120.0 / DT_MDL), status=False)      # several saves go by
    assert det.lifetime == first

  def test_it_starts_from_nothing_on_a_fresh_device(self):
    det = self._det(None)
    run(det, STUCK_FRAMES)
    run(det, 2, blinker=True)
    assert det.lifetime == (1, 1, 1)


class TestTheCrossingUsesTheCarsOwnDuration:
  """Settled with the owner: passing assist must not have its own steering. When it acts it drives
  the SAME lane change sunnypilot already performs -- same model, same lateral tuning, same
  lane_change_factor -- with the trigger coming from the system instead of the stalk.

  So the crossing duration is not a number chosen in passing_maneuver.py. It is whatever his own
  nudgeless changes take, which auto_lane_change.py measures.
  """

  @staticmethod
  def _det(stats):
    class _P(_KeepRightOnParams):
      def get(self, key, block=False, return_default=False):
        return stats if key == "LaneChangeStats" else super().get(key)
    det = PassingAssistDetector()
    det.params = _P()
    return det

  def test_the_measured_duration_replaces_the_guess(self):
    det = self._det({"changes": 12, "seconds": 3.1})
    run(det, 2)
    assert det.maneuver.change_duration_s == 3.1
    assert det.keep_right_maneuver.change_duration_s == 3.1

  def test_with_nothing_measured_the_fallback_stands(self):
    det = self._det(None)
    run(det, 2)
    assert det.maneuver.change_duration_s == CHANGE_DURATION_S

  def test_a_nonsense_measurement_is_ignored(self):
    """A stored zero or a fraction of a second is a bug somewhere, not a lane change, and taking
    it would make the dry run claim the crossing finished instantly."""
    det = self._det({"changes": 1, "seconds": 0.2})
    run(det, 2)
    assert det.maneuver.change_duration_s == CHANGE_DURATION_S

  def test_retuning_the_laterals_carries_through_without_anyone_remembering(self):
    """The point of taking it from a measurement rather than a constant."""
    det = self._det({"changes": 30, "seconds": 5.4})
    run(det, 2)
    assert det.maneuver.change_duration_s == 5.4


class TestHowWrongTheThresholdWas:
  """"Missed on nothing slower ahead" says the threshold rejected a car the driver went round. It
  does not say by how much, and that is the entire calibration question -- 4 mph is invented and
  the only thing that can settle it is what he actually passes.

  A car he passed that this called 3.6 mph slower argues for lowering the threshold. One at 0.5
  argues he simply wanted past, and no threshold would have agreed.
  """

  def test_it_records_what_the_deficit_really_was(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, v_lead=CRUISE_MS - 1.2)   # ~2.7 mph
    run(det, 2, blinker=True, v_lead=CRUISE_MS - 1.2)
    assert det.driver_passes_agreed == 0
    assert 2.0 < det.missed_deficit_mph < 3.5

  def test_it_is_a_mean_so_one_odd_pass_cannot_move_it(self):
    det = PassingAssistDetector()
    for v in (CRUISE_MS - 1.34, CRUISE_MS - 1.34, CRUISE_MS - 0.1):   # 3, 3, 0.2 mph
      run(det, STUCK_FRAMES, v_lead=v)
      run(det, 2, blinker=True, v_lead=v)
      run(det, int(3.0 / DT_MDL), status=False)
    assert det.driver_passes == 3
    assert 1.8 < det.missed_deficit_mph < 2.6, "one 0.2 mph pass should not dominate two at 3"

  def test_a_pass_refused_for_another_reason_is_not_averaged_in(self):
    """A blind spot says nothing about whether 4 mph is right, and averaging it would bury the
    cases that do."""
    det = run(PassingAssistDetector(), STUCK_FRAMES, left_bs=True, right_bs=True)
    run(det, 2, blinker=True, left_bs=True, right_bs=True)
    assert det.driver_passes == 1
    assert det.missed_deficit_mph == 0.0

  def test_agreed_passes_contribute_nothing(self):
    """It measures the threshold's mistakes. A pass it agreed with is not one."""
    det = run(PassingAssistDetector(), STUCK_FRAMES)
    run(det, 2, blinker=True)
    assert det.driver_passes_agreed == 1
    assert det.missed_deficit_mph == 0.0


class TestWhichHalfOfTheOncomingVetoWorks:
  """When oncoming refuses a pass, is it SEEING traffic or REMEMBERING it? The two need opposite
  fixes -- mostly-seen means the detection is right and the road really is two-way; mostly-
  remembered means one sighting is carrying the whole refusal.

  This is the measurement that turns the reported I-15 fault into a diagnosis rather than three
  mitigations and a hope.
  """

  ONCOMING = [track(90, 3.7, -27.0 - CRUISE_MS)]

  def test_watching_a_vehicle_counts_as_seen(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, tracks=self.ONCOMING)
    assert det.blocked_by == Blocked.oncomingLane
    assert det.oncoming_seen_seconds > 0.5
    assert det.oncoming_remembered_seconds == 0.0

  def test_the_tail_after_it_has_gone_counts_as_remembered(self):
    """The 90 s window outliving the vehicle is the intended behavior on a two-lane road, and is
    also exactly what one phantom detection would look like on a divided one."""
    det = run(PassingAssistDetector(), 3, tracks=self.ONCOMING)
    seen = det.oncoming_seen_seconds
    run(det, STUCK_FRAMES + int(5.0 / DT_MDL))
    assert det.blocked_by == Blocked.oncomingLane
    assert det.oncoming_seen_seconds == seen, "nothing in view should add no seen time"
    assert det.oncoming_remembered_seconds > 4.0

  def test_a_clear_road_counts_neither(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES)
    assert det.oncoming_seen_seconds == 0.0
    assert det.oncoming_remembered_seconds == 0.0

  def test_a_refusal_for_another_reason_counts_neither(self):
    """It measures the oncoming veto, not refusals in general."""
    det = run(PassingAssistDetector(), STUCK_FRAMES, edges=(-2.3, 2.4))
    assert det.blocked_by == Blocked.noLaneAvailable
    assert det.oncoming_seen_seconds == 0.0
    assert det.oncoming_remembered_seconds == 0.0


class TestItDoesNotAbortItsOwnPass:
  """The bug this system would have inflicted on itself the day it started actuating.

  The driver-override test was `leftBlinker or rightBlinker or brakePressed or steeringPressed`,
  which is exactly right while nothing here commands anything -- every blinker on the car is the
  driver's. The moment passing assist lights its OWN blinker, that test sees it, calls it driver
  input, and aborts the pass it just started. Every time.

  Found by researching how BlueCruise handles the same problem, not by any test: nothing could
  fail, because nothing actuates yet.
  """

  @staticmethod
  def _acting(side=Side.left):
    """A detector mid-maneuver with the blinker lit for `side`, as phase 2 will have it."""
    det = PassingAssistDetector()
    det.params = _KeepRightOnParams()
    det.actuating = True
    det.maneuver.side = side
    det.maneuver.phase = Phase.signaling
    return det

  def test_our_own_blinker_is_not_a_driver_override(self):
    det = self._acting(Side.left)
    assert det.maneuver.blinker_on                       # the dry run really is signaling
    assert not det._driver_override(NS(leftBlinker=True, rightBlinker=False,
                                       brakePressed=False, steeringPressed=False))

  def test_signaling_the_OTHER_way_calls_it_off(self):
    """The cancel gesture, and Ford uses the same one on BlueCruise. It falls out of subtracting our
    own signal rather than being bolted on: the other side is by definition not our side."""
    det = self._acting(Side.left)
    assert det._driver_override(NS(leftBlinker=False, rightBlinker=True,
                                   brakePressed=False, steeringPressed=False))

  def test_the_driver_agreeing_does_not_cancel(self):
    """Reaching for the stalk the way the car is already going is agreement, not a takeover.
    Treating it as one is how a system teaches you not to touch it."""
    det = self._acting(Side.right)
    assert not det._driver_override(NS(leftBlinker=False, rightBlinker=True,
                                       brakePressed=False, steeringPressed=False))

  def test_brake_and_steering_still_always_win(self):
    det = self._acting(Side.left)
    for kw in ({"brakePressed": True}, {"steeringPressed": True}):
      cs = dict(leftBlinker=True, rightBlinker=False, brakePressed=False, steeringPressed=False)
      cs.update(kw)
      assert det._driver_override(NS(**cs)), f"{kw} did not override"

  def test_with_the_blinker_out_our_side_stops_being_ours(self):
    """Only while we are actually signaling. Once the maneuver drops the signal, a blinker on that
    side is the driver's again -- otherwise one pass would deafen us to that stalk for the drive."""
    det = self._acting(Side.left)
    det.maneuver.phase = Phase.idle
    assert not det.maneuver.blinker_on
    assert det._driver_override(NS(leftBlinker=True, rightBlinker=False,
                                   brakePressed=False, steeringPressed=False))

  def test_and_none_of_this_applies_until_it_actuates(self):
    """Phase 1: every blinker on the car is the driver's, and the old behavior must be exact."""
    det = self._acting(Side.left)
    det.actuating = False
    assert det._driver_override(NS(leftBlinker=True, rightBlinker=False,
                                   brakePressed=False, steeringPressed=False))


class TestTheChime:
  """A sound when it decides. See CHIME_SETTLE_S in custom.capnp's neighbour.

  From the road: "it just kept beeping over and over." The chime fired on the RISING EDGE of a
  suggestion, which is right exactly once and useless the moment a gate flickers -- geometry
  sitting on a threshold toggles at 20 Hz, and every toggle was a fresh edge. The edge WAS the
  rate limit, and an edge is not a rate limit.
  """

  def test_a_flickering_gate_does_not_produce_a_flickering_chime(self):
    """The reported failure, reproduced: a suggestion that toggles every other frame used to fire
    the chime on every one of those frames."""
    det = PassingAssistDetector()
    run(det, STUCK_FRAMES)
    assert det.suggestion == Side.left
    chimes = 0
    for i in range(int(6.0 / DT_MDL)):
      # Alternate a gate open and shut as fast as the planner runs.
      det.update(make_sm(left_bs=(i % 2 == 0)), CRUISE_MS, True)
      chimes += bool(det.suggestion_started)
    assert chimes <= 1, f"a flickering gate produced {chimes} chimes in six seconds"

  def test_it_still_speaks_for_a_real_suggestion(self):
    det = PassingAssistDetector()
    chimes = 0
    for _ in range(STUCK_FRAMES * 2):
      det.update(make_sm(), CRUISE_MS, True)
      chimes += bool(det.suggestion_started)
    assert det.suggestion == Side.left
    assert chimes == 1, f"expected one chime for one settled pass, got {chimes}"

  def test_a_suggestion_too_brief_to_act_on_says_nothing(self):
    """Shorter than the settle time. If it was not up long enough to be worth doing, it was not
    worth a noise."""
    det = PassingAssistDetector()
    run(det, STUCK_FRAMES)
    det.suggestion_started = False
    chimes = 0
    for _ in range(int(0.2 / DT_MDL)):
      det.update(make_sm(status=False), CRUISE_MS, True)
      chimes += bool(det.suggestion_started)
    assert chimes == 0

  def test_a_second_pass_much_later_chimes_again(self):
    """It has to re-arm, or only the first pass of a drive is ever announced."""
    det = PassingAssistDetector()
    chimes = 0
    for _ in range(STUCK_FRAMES * 2):
      det.update(make_sm(), CRUISE_MS, True)
      chimes += bool(det.suggestion_started)
    for _ in range(int(CHIME_MIN_INTERVAL_S + 4.0)):
      for _ in range(int(1.0 / DT_MDL)):
        det.update(make_sm(status=False), CRUISE_MS, True)
    for _ in range(STUCK_FRAMES * 2):
      det.update(make_sm(), CRUISE_MS, True)
      chimes += bool(det.suggestion_started)
    assert chimes == 2, f"expected one chime per pass, got {chimes}"

  def test_the_toggle_is_read(self):
    det = PassingAssistDetector()
    det.params = _KeepRightOnParams(PassingAssistChime=False)
    run(det, 4)
    assert det.chime_enabled is False


class TestTheDriveHistory:
  """"I'm not going to look at that after each drive. That is cool for me to see, but I didn't want
  it to be what I have to tell you."

  PassingAssistLastDrive is overwritten every ignition cycle, so a week of driving left one drive's
  numbers and the only way to keep the rest was for the driver to read a panel and retype it. The
  car keeps its own history now. See DRIVE_HISTORY_MAX.
  """

  class _P(_KeepRightOnParams):
    """Params that actually store, so archiving across simulated ignition cycles is testable."""

    def __init__(self, **kw):
      super().__init__(**kw)
      self.store: dict = {}

    def get(self, key, block=False, return_default=False):
      if key in self.store:
        return self.store[key]
      return self.values[key] if key in self.values else None

    def put(self, key, value, block=False):
      self.store[key] = value

  @staticmethod
  def _drive(params, **summary):
    """One ignition cycle: a fresh detector reads params once, then parks with a summary."""
    det = PassingAssistDetector()
    det.params = params
    det.update_params()
    params.store["PassingAssistLastDrive"] = dict(summary)
    return det

  def test_each_drive_is_kept_not_overwritten(self):
    p = self._P()
    for n in range(4):
      self._drive(p, wantedSeconds=n * 10.0)
    # The fourth boot archives drives 1-3; the fourth is still "last" until the next start.
    hist = p.store["PassingAssistHistory"]
    assert len(hist) == 3, f"kept {len(hist)} of 3 finished drives"
    assert [h["wantedSeconds"] for h in hist] == [0.0, 10.0, 20.0]

  def test_starting_the_car_without_driving_does_not_duplicate(self):
    """The failure this would have had in the driveway: LastDrive is untouched by a boot with no
    driving, so without a guard the same drive is archived on every start."""
    p = self._P()
    self._drive(p, wantedSeconds=42.0)
    for _ in range(5):
      det = PassingAssistDetector()
      det.params = p
      det.update_params()          # boot, no driving, no new summary
    hist = p.store.get("PassingAssistHistory", [])
    assert len(hist) == 1, f"one drive archived {len(hist)} times"

  def test_it_stops_growing(self):
    p = self._P()
    for n in range(DRIVE_HISTORY_MAX + 12):
      self._drive(p, wantedSeconds=float(n))
    hist = p.store["PassingAssistHistory"]
    assert len(hist) == DRIVE_HISTORY_MAX
    # ...and it is the RECENT ones that survive. Keeping the oldest would be worse than useless: a
    # history frozen at the first twenty drives never shows anything the system learned since.
    #
    # Asserting hist[-1] > hist[0] does NOT pin this -- it holds for either end of the list, which a
    # mutation proved by passing. The first drive being GONE is the claim.
    assert hist[0]["wantedSeconds"] > 0.0, "kept the oldest drives instead of the newest"
    assert hist[-1]["wantedSeconds"] == float(DRIVE_HISTORY_MAX + 10), "newest drive missing"

  def test_a_throwing_param_store_does_not_reach_the_planner(self):
    class Broken(self._P):
      def put(self, key, value, block=False):
        if key == "PassingAssistHistory":
          raise RuntimeError("param store is unhappy")
        super().put(key, value, block)
    p = Broken()
    self._drive(p, wantedSeconds=1.0)
    det = PassingAssistDetector()
    det.params = p
    det.update_params()            # must not raise
    run(det, 4)

  def test_a_fresh_device_archives_nothing(self):
    p = self._P()
    det = PassingAssistDetector()
    det.params = p
    det.update_params()
    assert "PassingAssistHistory" not in p.store, "wrote an empty drive on a car that never drove"


class TestTheGeometryGateActuallyOpens:
  """From the road: "it just always says no lane to change into."

  Nothing in this suite pinned the three thresholds -- every fixture sat well clear of all of them,
  so 0.6/0.5/3.0 could be changed to anything and 1044 tests stayed green. That is the same blind
  spot that has produced every real bug this week: the fixtures held constant exactly the thing
  that was wrong.
  """

  def test_a_standard_shoulder_is_not_a_lane(self):
    """The one that mattered, from the road: "it said it would be changing right even though I was
    in the furthest right lane, which means it would have run me right into the shoulder."

    AASHTO gives interstate right shoulders as 10 ft, and 12 ft where truck volumes are high --
    3.05 to 3.66 m. The old 3.0 m bar was written believing "a wide shoulder is under 3", which is
    simply not true of the roads this drives on, so a standard shoulder read as a lane.

    ...AND THEN 3.5 DID NOT HOLD EITHER: "it just keeps trying to go into the shoulder."

    So this is now the WIDEST shoulder AASHTO gives, 12 ft, which is exactly as wide as the lane in
    the next test. Nothing about width separates those two and no threshold on it ever could -- the
    pair below differs in one number only, where the ROAD EDGE is, and that is the whole test.

    The geometry is what the model does with no lane out there: laneLines always has four entries,
    so the far one gets put on the strongest feature left, which is the road edge itself. On screen
    that is the red line on the barrier wall -- "so it's obvious that's a shoulder."
    """
    det = run(PassingAssistDetector(), STUCK_FRAMES, probs=(0.1, 0.99, 0.99, 0.9),
              ll=(-5.5, -1.85, 1.85, 1.85 + 3.66), edges=(-2.2, 1.85 + 3.66))  # 12 ft shoulder
    assert not det.right_geometry_ok, "a 12 ft shoulder counted as a lane to move into"
    assert det.right_lane_width > MIN_LANE_WIDTH_M, "width was never the thing that refused it"

  def test_but_a_real_lane_beside_it_still_is(self):
    """The same 12 ft of pavement with the road edge a shoulder further out. It is a lane."""
    det = run(PassingAssistDetector(), STUCK_FRAMES, probs=(0.1, 0.99, 0.99, 0.9),
              ll=(-5.5, -1.85, 1.85, 1.85 + 3.66), edges=(-2.2, 1.85 + 3.66 + 3.05))
    assert det.right_geometry_ok

  def test_a_genuinely_useless_edge_is_still_refused(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, edge_stds=(1.5, 1.5))
    assert not det.left_geometry_ok

  def test_paint_nobody_can_see_is_still_refused(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, probs=(0.1, 0.99, 0.99, 0.1),
              edges=(-2.2, 2.4))
    assert not det.left_geometry_ok
    assert det.blocked_by == Blocked.noLaneAvailable

  def test_traffic_in_a_lane_does_not_make_it_a_lane(self):
    """Reversed on road evidence, twice: "it just keeps trying to go into the shoulder."

    This used to OR `same_direction_recent` into the geometry gate, on the reasoning that a vehicle
    driving our way proves the lane exists. True, and still wrong here -- it made a side available
    with NO geometry at all, so any misplaced track opened the right-hand side from the rightmost
    lane. The adjacent band is 2.0-5.5 m from the path, the radar is mounted off-center with no
    correction, its lateral estimate degrades with range, and path_offset is a model output. Any of
    those can put a car from another lane into that band.
    """
    det = keep_right_det()
    scene = dict(probs=(0.05, 0.99, 0.99, 0.05), edges=(-2.2, 2.4), edge_stds=(1.5, 1.5))
    moving = dict(scene, tracks=(track(40.0, 3.7, v_rel=0.5),))
    run(det, int(3.0 / DT_MDL), **moving)
    assert det.adjacent.left.same_direction_recent, "the radar did not register the vehicle"
    assert not det.left_geometry_ok, "traffic alone opened a side with no geometry"


class TestTheBackedOutChime:
  """"I'll keep reporting back to you instances where it messed up. That's why I like that it makes
  a sound. That helps me to know what it is doing without always looking at it."

  The sound is the reporting channel, and it covered exactly one event -- a decision, the case that
  went right. A sequence that lit the blinker and then withdrew it was silent, so `aborts`, the one
  number this whole dry run exists to produce, was the one thing he could not notice without
  staring at the screen and therefore could not report.

  Every test here has to catch the machine DURING `signaling`, which is about a second wide. Once
  the crossing starts a gate can no longer stop it -- a car cannot un-change lanes on a change of
  mind -- so blocking a second too late provokes nothing at all.
  """

  @staticmethod
  def _to_signaling(det, **kw):
    for _ in range(int(10.0 / DT_MDL)):
      det.update(make_sm(v_lead=SLOW_LEAD_MS, **kw), CRUISE_MS, True)
      if det.maneuver.phase == Phase.signaling:
        return True
    return False

  def test_a_reversal_makes_a_sound(self):
    det = keep_right_det()
    assert self._to_signaling(det), "never got as far as showing a blinker"
    heard = False
    for _ in range(int(3.0 / DT_MDL)):        # the lane fills while the blinker is up
      det.update(make_sm(v_lead=SLOW_LEAD_MS, left_bs=True), CRUISE_MS, True)
      heard = heard or det.abort_started
    assert det.maneuver.aborts >= 1, "did not actually back out"
    assert heard, "backed out silently"

  def test_it_sounds_once_per_reversal_not_once_per_frame(self):
    det = keep_right_det()
    assert self._to_signaling(det)
    n = 0
    for _ in range(int(6.0 / DT_MDL)):
      det.update(make_sm(v_lead=SLOW_LEAD_MS, left_bs=True), CRUISE_MS, True)
      n += det.abort_started
    assert n == 1, f"sounded {n} times for one reversal"

  def test_a_strobing_gate_cannot_produce_a_strobing_tone(self):
    """"It just kept beeping over and over" is a failure this feature has already had once. A gate
    flickering signal-abort-signal is worth hearing about, at twelve seconds apart rather than at
    whatever rate the gate manages."""
    det = keep_right_det()
    n = 0
    for i in range(int(30.0 / DT_MDL)):
      blocked = (i // int(0.6 / DT_MDL)) % 2 == 1     # the lane fills and clears, on and on
      det.update(make_sm(v_lead=SLOW_LEAD_MS, left_bs=blocked), CRUISE_MS, True)
      n += det.abort_started
    assert det.maneuver.aborts > n, "the test did not actually provoke repeated reversals"
    assert n <= 3, f"sounded {n} times in 30 s"

  def test_a_clean_drive_stays_quiet(self):
    det = keep_right_det()
    n = 0
    for _ in range(int(20.0 / DT_MDL)):
      det.update(make_sm(v_lead=SLOW_LEAD_MS), CRUISE_MS, True)
      n += det.abort_started
    assert det.maneuver.aborts == 0
    assert n == 0, "made a backed-out noise on a drive that never backed out"

  def test_it_goes_quiet_again_after_the_reversal_is_over(self):
    """An EDGE, not a level. Testing the count against zero instead of against what was already
    reported passes every test above -- the interval hides it -- and then chimes every twelve
    seconds for the rest of the drive because one reversal happened at the start of it.
    """
    det = keep_right_det()
    assert self._to_signaling(det)
    for _ in range(int(3.0 / DT_MDL)):
      det.update(make_sm(v_lead=SLOW_LEAD_MS, left_bs=True), CRUISE_MS, True)
    assert det.maneuver.aborts >= 1
    n = 0
    for _ in range(int(40.0 / DT_MDL)):        # a long clean stretch after it
      det.update(make_sm(v_lead=SLOW_LEAD_MS), CRUISE_MS, True)
      n += det.abort_started
    assert n == 0, f"kept announcing an old reversal {n} times"

  def test_a_suppressed_reversal_is_dropped_rather_than_queued(self):
    """A tone that arrives twelve seconds after the thing it describes is worse than no tone -- he
    would look up at a screen showing something else entirely and report the wrong event.
    """
    det = keep_right_det()
    assert self._to_signaling(det)
    n = 0
    for i in range(int(12.0 / DT_MDL)):        # two reversals inside one interval
      blocked = (i // int(0.6 / DT_MDL)) % 2 == 1
      det.update(make_sm(v_lead=SLOW_LEAD_MS, left_bs=blocked), CRUISE_MS, True)
      n += det.abort_started
    assert det.maneuver.aborts >= 2, "the test did not provoke a second reversal"
    assert n == 1
    for _ in range(int(30.0 / DT_MDL)):        # nothing further happens
      det.update(make_sm(v_lead=SLOW_LEAD_MS), CRUISE_MS, True)
      n += det.abort_started
    assert n == 1, "the suppressed reversal was saved up and played later"

  def test_a_keep_right_that_backs_out_sounds_the_same(self):
    """Two machines run side by side and each keeps its own abort count. To a driver they are one
    event -- it showed a blinker and then withdrew it -- so reporting only the passing machine's
    reversals would be silence for half the cases with nothing saying why.
    """
    det = keep_right_det()
    reached = False
    for _ in range(int(60.0 / DT_MDL)):
      det.update(make_sm(status=False, **IN_LEFT_LANE), CRUISE_MS, True)
      if det.keep_right_maneuver.phase == Phase.signaling:
        reached = True
        break
    assert reached, "keep-right never got as far as showing a blinker"
    heard = False
    for _ in range(int(3.0 / DT_MDL)):
      det.update(make_sm(status=False, right_bs=True, **IN_LEFT_LANE), CRUISE_MS, True)
      heard = heard or det.abort_started
    assert det.keep_right_maneuver.aborts >= 1, "keep-right did not back out"
    assert det.maneuver.aborts == 0, "this has to be the keep-right machine, not the other one"
    assert heard, "a keep-right reversal was silent"


class TestWhyItNeverFired:
  """Five drives, twenty-one passes, zero suggestions -- and no way to tell which of four constants
  was responsible without reading numbers off a screen at 70 mph, which he does not do and has said
  so: "and you expect me to read all of that while driving?"

  So the drive keeps a tally and reports the answer as one sentence at a stop.
  """

  def test_it_names_the_paint_threshold_when_that_is_what_refuses(self):
    det = keep_right_det()
    run(det, int(20.0 / DT_MDL), v_lead=SLOW_LEAD_MS, probs=(0.31, 0.99, 0.99, 0.2))
    term, value, share = det.geo_refusal
    assert term == det.GEO_PAINT
    assert abs(value - 0.31) < 0.01, "reported a number that is not the one that refused"
    assert share > 0.9

  def test_it_names_the_shoulder_when_that_is_what_refuses(self):
    """Paint fine, width fine, no road left past the far line -- the shoulder case."""
    det = keep_right_det()
    run(det, int(20.0 / DT_MDL), v_lead=SLOW_LEAD_MS,
        ll=(-5.5, -1.85, 1.85, 5.5), probs=(0.9, 0.99, 0.99, 0.2), edges=(-5.5, 2.4))
    term, value, share = det.geo_refusal
    assert term == det.GEO_BEYOND
    assert share > 0.9

  def test_the_first_failing_term_wins_not_the_last(self):
    """Two terms failing at once has to report the one the gate reaches first, or the number named
    is not the number to change."""
    det = keep_right_det()
    run(det, int(20.0 / DT_MDL), v_lead=SLOW_LEAD_MS,
        probs=(0.1, 0.99, 0.99, 0.2), edge_stds=(1.5, 0.1))
    term, _, _ = det.geo_refusal
    assert term == det.GEO_EDGE_STD, "paint was blamed for a road edge nobody could measure"

  def test_a_drive_where_the_left_lane_was_fine_reports_nothing(self):
    det = keep_right_det()
    run(det, int(20.0 / DT_MDL), v_lead=SLOW_LEAD_MS)
    assert det.left_geometry_ok
    _, _, share = det.geo_refusal
    assert share == 0.0, "counted refusals on a drive that had none"


class TestTheHistoryKnowsWhichBuildMadeIt:
  """Asked directly: "are we keeping logs from previous versions or wiping them with each commit
  since you keep changing things?"

  Kept -- the key is PERSISTENT and nothing clears it. Which is the problem: the thresholds this
  measures move between drives, so a run from before the geometry was rewritten is not comparable
  to one from after, and nothing in the record said which was which. Twenty drives of that mixture
  reads as noise in the gates rather than as two different gates.
  """

  @staticmethod
  def _det_with(store):
    det = keep_right_det()
    det.params = store
    return det

  def test_each_archived_drive_carries_the_build(self):
    class P:
      def __init__(s): s.store = {"GitCommit": "abcdef1234567890"}
      def get(s, k, *a, **kw): return s.store.get(k)
      def get_bool(s, k, *a, **kw): return bool(s.store.get(k))
      def put(s, k, v, block=False): s.store[k] = v
    p = P()
    det = self._det_with(p)
    det._archive_drive({"driverPasses": 3})
    assert p.store["PassingAssistHistory"][-1]["build"] == "abcdef12"

  def test_a_drive_is_still_kept_when_the_build_cannot_be_read(self):
    class P:
      def __init__(s): s.store = {}
      def get(s, k, *a, **kw):
        if k == "GitCommit":
          raise RuntimeError("unreadable")
        return s.store.get(k)
      def get_bool(s, k, *a, **kw): return False
      def put(s, k, v, block=False): s.store[k] = v
    p = P()
    det = self._det_with(p)
    det._archive_drive({"driverPasses": 3})
    assert p.store["PassingAssistHistory"][-1]["driverPasses"] == 3

  def test_stamping_does_not_break_the_repeat_check(self):
    """A boot with no driving must not archive the same drive again -- the comparison is against
    the unstamped value that was passed in."""
    class P:
      def __init__(s): s.store = {"GitCommit": "abcdef1234567890"}
      def get(s, k, *a, **kw): return s.store.get(k)
      def get_bool(s, k, *a, **kw): return bool(s.store.get(k))
      def put(s, k, v, block=False): s.store[k] = v
    p = P()
    det = self._det_with(p)
    drive = {"driverPasses": 3}
    det._archive_drive(drive)
    det._archive_drive(drive)
    assert len(p.store["PassingAssistHistory"]) == 1, (
      "the stamp changed the stored shape, so a re-boot archives the same drive again")

  def test_town_driving_does_not_pollute_the_tally(self):
    """The tally used to run on every frame of every road. A commute of residential streets -- no
    passing lane, no pass wanted -- would swamp the highway frames the question is about, and the
    summary would have named whichever term refuses a side street.
    """
    det = keep_right_det()
    # miles of road with no lead at all and no lane either side
    run(det, int(120.0 / DT_MDL), status=False, probs=(0.05, 0.99, 0.99, 0.05), edges=(-2.2, 2.4))
    _, _, share = det.geo_refusal
    assert share == 0.0, "counted refusals on a road where no pass was ever wanted"

  def test_a_drive_like_his_produces_the_line(self):
    """End to end: the values the summary needs must actually arrive together. wantedSeconds is the
    gate on showing it, and it only accumulates once a slow lead is CONFIRMED -- so a tally with no
    wanted time attached would publish a diagnosis the panel never shows.
    """
    det = keep_right_det()
    run(det, int(60.0 / DT_MDL), v_lead=SLOW_LEAD_MS, probs=(0.31, 0.99, 0.99, 0.2))
    term, value, share = det.geo_refusal
    assert det.blocked_by == Blocked.noLaneAvailable
    assert det.wanted_seconds > 30.0, "the panel gate would never open"
    assert share > 0.5, "no single term dominates, so there is nothing to report"
    assert term == det.GEO_PAINT and abs(value - 0.31) < 0.01


class TestTheTimelineIsARing:
  """His report is ordered -- "first it did this, then I waited, then it did that" -- and everything
  stored until now was an aggregate, which throws the order away. So the drive keeps a list of state
  changes, and a list on a device needs a bound that is actually enforced.

  The soak test cannot prove this on its own: a thirty-minute drive produces fewer changes than the
  cap, so removing the cap passes it. This drives long enough to overflow.
  """

  @staticmethod
  def _churn(det, seconds):
    """Force a state change roughly twice a second by making and unmaking a slow lead."""
    for i in range(int(seconds / DT_MDL)):
      slow = (i // int(0.5 / DT_MDL)) % 2 == 0
      det.update(make_sm(v_lead=SLOW_LEAD_MS if slow else CRUISE_MS, status=slow), CRUISE_MS, True)

  def test_it_stops_at_the_cap(self):
    det = keep_right_det()
    self._churn(det, TIMELINE_MAX * 1.5)
    assert len(det._timeline) <= TIMELINE_MAX, (
      f"grew to {len(det._timeline)}; a list on the device with no bound")

  def test_it_keeps_the_NEWEST_entries(self):
    """The tail is the interesting part -- a drive that overflowed was eventful, and what he is
    describing is most likely the end of it. Dropping the newest would keep the boring start.
    """
    det = keep_right_det()
    self._churn(det, TIMELINE_MAX * 1.5)
    if len(det._timeline) < TIMELINE_MAX:
      return
    assert det._timeline[0][0] > 0.0, "kept the oldest entries and dropped the recent ones"
    assert det._timeline[-1][0] == max(r[0] for r in det._timeline)

  def test_a_steady_state_records_nothing(self):
    """One entry per CHANGE. Per frame would be twenty a second and unreadable.

    An EMPTY road, deliberately. With a slow lead in front the state is not steady at all -- the dry
    run completes, stands down thirty seconds, and runs again, which is COMPLETE_STANDDOWN_S working
    and shows up here as a handful of entries a minute. The first version of this test used that
    scene and was simply wrong about what it was watching.
    """
    det = keep_right_det()
    run(det, int(30.0 / DT_MDL), status=False)
    n = len(det._timeline)
    assert n > 0, "recorded nothing at all -- this would pass on a dead timeline"
    run(det, int(60.0 / DT_MDL), status=False)
    assert len(det._timeline) == n, "a state that never changed still wrote entries"

  def test_the_timeline_does_not_go_into_the_history(self):
    """It rides in LastDrive and is read once. Archiving it puts three hundred entries into each of
    twenty drives -- 129 KB in one PERSISTENT | BACKUP param, rewritten whenever a drive ends, to
    hold a sequence nobody reads a fortnight later.
    """
    class P:
      def __init__(s): s.store = {"GitCommit": "abcdef1234567890"}
      def get(s, k, *a, **kw): return s.store.get(k)
      def get_bool(s, k, *a, **kw): return bool(s.store.get(k))
      def put(s, k, v, block=False): s.store[k] = v
    p = P()
    det = keep_right_det()
    det.params = p
    det._archive_drive({"driverPasses": 3, "timeline": [[1.0, 0, 0, 0, 0]] * 300})
    stored = p.store["PassingAssistHistory"][-1]
    assert "timeline" not in stored
    assert stored["driverPasses"] == 3 and stored["build"] == "abcdef12"

  def test_stripping_the_timeline_does_not_break_the_repeat_check(self):
    """Two things are now removed before comparing. Getting either wrong re-archives the same drive
    on every start of the car."""
    class P:
      def __init__(s): s.store = {"GitCommit": "abcdef1234567890"}
      def get(s, k, *a, **kw): return s.store.get(k)
      def get_bool(s, k, *a, **kw): return bool(s.store.get(k))
      def put(s, k, v, block=False): s.store[k] = v
    p = P()
    det = keep_right_det()
    det.params = p
    drive = {"driverPasses": 3, "timeline": [[1.0, 0, 0, 0, 0]]}
    det._archive_drive(drive)
    det._archive_drive(drive)
    assert len(p.store["PassingAssistHistory"]) == 1

  def test_the_refusal_reaches_the_stored_summary(self):
    """It was published live and never stored, so the one number explaining a drive with sixty
    refusals and zero suggestions could only be read off a RUNNING car. "I guess I need to go back
    to my car?" was the cost of that omission, and the answer should have been no.
    """
    # WRAPS the real stub rather than replacing it -- every setting still reads its own default,
    # which a bare fake returning None turns into `None * float` several gates later.
    det = keep_right_det()
    real = det.params

    class P:
      def __init__(s): s.store = {}
      def get(s, k, *a, **kw): return s.store[k] if k in s.store else real.get(k, *a, **kw)
      def get_bool(s, k, *a, **kw): return real.get_bool(k, *a, **kw)
      def put(s, k, v, block=False): s.store[k] = v
      def remove(s, k): s.store.pop(k, None)
    p = P()
    det.params = p
    run(det, int(60.0 / DT_MDL), v_lead=SLOW_LEAD_MS, probs=(0.31, 0.99, 0.99, 0.2))
    saved = p.store.get("PassingAssistLastDrive")
    assert saved, "nothing was stored at all"
    assert saved["geoRefusedBy"] == det.GEO_PAINT
    assert abs(saved["geoRefusedValue"] - 0.31) < 0.01
    assert saved["geoRefusedShare"] > 0.5

  def test_it_reports_where_the_threshold_would_have_to_sit(self):
    """A mean names the constant; it does not say what to set it to. Refusals clustered at 0.31
    are fixed by 0.30; refusals split between 0.45 and 0.17 are not, and the mean is the same.
    """
    det = keep_right_det()
    run(det, int(30.0 / DT_MDL), v_lead=SLOW_LEAD_MS, probs=(0.31, 0.99, 0.99, 0.2))
    term, _, _ = det.geo_refusal
    assert term == det.GEO_PAINT
    loosen = det.geo_refusal_loosen_to
    assert 0.0 < loosen <= 0.31, f"reported {loosen}, which would not admit the refused frames"

  def test_a_split_distribution_reports_the_LOW_end_not_the_middle(self):
    """The case a mean cannot express. Half the refusals well below the mean means the mean fixes
    half of them, so the number to report is the one that admits four fifths."""
    # THREE TO ONE, deliberately. With an even split the median and the twentieth percentile land
    # close enough that reporting either passes -- which is a test that cannot tell the difference
    # between the number to set and the middle of the range.
    det = keep_right_det()
    run(det, int(30.0 / DT_MDL), v_lead=SLOW_LEAD_MS, probs=(0.45, 0.99, 0.99, 0.2))
    run(det, int(10.0 / DT_MDL), v_lead=SLOW_LEAD_MS, probs=(0.15, 0.99, 0.99, 0.2))
    _, mean, _ = det.geo_refusal
    loosen = det.geo_refusal_loosen_to
    assert loosen < mean, f"reported the mean ({mean:.2f}) rather than the low end ({loosen:.2f})"
    assert loosen <= 0.2, (
      f"{loosen:.2f} is the middle of the spread, not the value that admits four fifths of it")

  def test_nothing_refused_reports_nothing(self):
    det = keep_right_det()
    run(det, int(20.0 / DT_MDL), v_lead=SLOW_LEAD_MS)
    assert det.left_geometry_ok
    assert det.geo_refusal_loosen_to == 0.0


class TestLkaButtonTurnsItOnAndOff:
  """"I want this fully turned on and off with the LKA button. Not pause. But have it automatically
  turn on at speed or whatever, but then when I turn it off, leave it off until I turn it on."

  It used to start a fifteen minute countdown. A countdown always comes back on its own, which is
  the one thing he said he did not want -- so the button writes the same key the settings toggle
  writes, and OFF stays off across ignition cycles until he presses it again.

  "Automatically turn on at speed" needs nothing extra: enabled is not active. Once it is on, the
  speed gate decides when it does anything, exactly as before.
  """

  @staticmethod
  def _det():
    """Wraps the real stub so every other setting keeps its default -- a bare fake returning None
    turns into `None * float` several gates later, which is a harness bug wearing a real one's
    costume."""
    det = keep_right_det()
    real = det.params

    class P:
      def __init__(s): s.store = {}
      def get(s, k, *a, **kw): return s.store[k] if k in s.store else real.get(k, *a, **kw)
      def get_bool(s, k, *a, **kw):
        return s.store[k] if k in s.store else real.get_bool(k, *a, **kw)
      def put(s, k, v, block=False): s.store[k] = v
      def put_bool(s, k, v, block=False): s.store[k] = bool(v)
      def remove(s, k): s.store.pop(k, None)
    det.params = P()
    return det

  def test_a_press_turns_it_off(self):
    det = self._det()
    run(det, STUCK_FRAMES, v_lead=SLOW_LEAD_MS)
    assert det.suggestion == Side.left
    run(det, 3, v_lead=SLOW_LEAD_MS, lka=True)
    assert not det.enabled
    assert det.blocked_by == Blocked.disabled

  def test_and_it_STAYS_off(self):
    """The whole request. A pause expires; this must not."""
    det = self._det()
    run(det, 3, v_lead=SLOW_LEAD_MS, lka=True)
    run(det, int(60.0 / DT_MDL), v_lead=SLOW_LEAD_MS)
    assert not det.enabled, "it came back on by itself"
    assert det.blocked_by == Blocked.disabled

  def test_it_writes_where_the_settings_screen_reads(self):
    """One state, two ways to reach it. A separate flag would let the menu and the panel disagree
    about whether the feature is on."""
    det = self._det()
    run(det, 3, v_lead=SLOW_LEAD_MS, lka=True)
    assert det.params.store["PassingAssistLogEnabled"] is False

  def test_a_second_press_turns_it_back_on_and_it_works(self):
    det = self._det()
    run(det, 3, v_lead=SLOW_LEAD_MS, lka=True)
    assert not det.enabled
    run(det, 3, v_lead=SLOW_LEAD_MS)
    run(det, 3, v_lead=SLOW_LEAD_MS, lka=True)
    assert det.enabled
    assert det.params.store["PassingAssistLogEnabled"] is True
    run(det, STUCK_FRAMES, v_lead=SLOW_LEAD_MS)
    assert det.suggestion == Side.left, "back on, but not working"

  def test_holding_the_button_is_one_press_not_many(self):
    """The signal reads Pressed for as long as it is held. Level-triggered, a held button would
    toggle at 20 Hz and land on whichever state the frame count happened to end on."""
    det = self._det()
    run(det, int(3.0 / DT_MDL), v_lead=SLOW_LEAD_MS, lka=True)
    assert not det.enabled, "a held button toggled it back on again"

  def test_not_pressed_changes_nothing(self):
    det = self._det()
    run(det, STUCK_FRAMES, v_lead=SLOW_LEAD_MS)
    assert det.enabled and det.suggestion == Side.left

  def test_the_toggle_write_blocks(self):
    """block=True, and a stub cannot prove it -- put_bool defaults to putNonBlocking, and the
    periodic param refresh re-reads this key, so a non-blocking write can lose to its own read-back
    and the button appears dead. That is the same race that made the +/- settings controls look
    broken. Asserted from source because no fake here reproduces the timing.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "passing_assist.py").read_text(encoding="utf-8")
    assert 'put_bool("PassingAssistLogEnabled", self.enabled, block=True)' in src, (
      "the LKA toggle no longer writes blocking; the button will intermittently do nothing")


class TestTheExitWithNoExitLane:
  """From the road 2026-08-09, with a map of the junction: an I-80 exit he takes every day where
  the ramp simply leaves the rightmost through lane. There is no exit lane at all.

      "Notice how there is no exit lane at all, you just exit. Here I always get into the far right
       lane early to prepare."

  Every exit test that existed misses this, and each for a structural reason rather than a tuning
  one:

    road widening   nothing widens. There is no ramp lane opening alongside to be seen.
    lane age        the rightmost lane is an ordinary through lane, beside him for miles. It passes
                    the age gate comfortably.
    lane speed      "sometimes they aren't slower" -- his own correction, and right.

  So he moved over early, collected the four second pause, and the system was free to suggest
  moving back left just as he was about to leave the freeway.

  What is left is where he ENDS UP: the outermost lane, no lane to the right. Already computed.
  """

  @staticmethod
  def _change(det, **road):
    """Signal, hold it through the change, then let the stalk go."""
    run(det, int(2.0 / DT_MDL), **road)
    run(det, 2, **{k: v for k, v in road.items() if not k.startswith("blinker")})
    return det

  def test_moving_right_into_the_outermost_lane_reads_as_exit_preparation(self):
    """The case. Default fixture geometry is one lane plus a shoulder -- the far right lane."""
    det = self._change(keep_right_det(), blinker_right=True)
    assert det.driver_change_was_exit
    assert det.driver_change_standdown > 30.0

  def test_moving_right_with_a_lane_still_out_there_is_ordinary_lane_discipline(self):
    """The other half, and what stops this swallowing every rightward change. If there is still a
    lane beyond the one he moved into, he has not run out of road and is not leaving."""
    det = self._change(keep_right_det(), blinker_right=True, **IN_LEFT_LANE)
    assert not det.driver_change_was_exit
    assert 0.0 < det.driver_change_standdown <= 4.0

  def test_a_left_change_is_never_an_exit_however_the_road_looks(self):
    """Direction carries the claim. Moving LEFT out of the outermost lane is the opposite of
    leaving, and the same geometry is present -- so a rule that only looked at the road would get
    this backwards."""
    det = self._change(keep_right_det(), blinker=True)
    assert not det.driver_change_was_exit
    assert det.driver_change_standdown <= 4.0

  def test_the_widening_test_still_fires_where_it_always_did(self):
    """Kept as an OR rather than replaced. The ordinary kind of exit -- a ramp lane that really
    does open up, moved into while a lane remains to the right -- has to keep working, and it is
    the case the outermost test cannot see."""
    det = self._change(keep_right_det(), blinker_right=True, right_edge_widen=4.0, **IN_LEFT_LANE)
    assert det.driver_change_was_exit
    assert det.driver_change_standdown > 30.0


class TestTrafficCanStandInForTheRoadEdge:
  """Measured on the 2026-08-09 freeway drive, and the fix that measurement asked for.

  leftEdgeStd had a MEDIAN of 2.12 and sat over the 1.2 limit for 61 % of the drive -- the limit is
  around the thirtieth percentile of that road, tight by a factor of two rather than marginally. On
  2865 frames the edge was the ONLY objection, and on 94.1 % of those the radar had already seen
  same-direction traffic in that very lane.

  The two edge-derived terms exist to tell a lane from a shoulder. A car driving down it our way
  answers that question directly, and without the model having to find a road edge at all -- so it
  may stand in for them. What it may NOT do is stand in for paint or width, and most of these tests
  are about that boundary rather than about the case it enables.
  """

  # Bad enough to fail MAX_ROAD_EDGE_STD on the left, fine on the right.
  BAD_EDGE = dict(edge_stds=(2.5, 0.1))
  # Same-direction, and FASTER, so it vouches for the lane without blocking the pass on speed.
  TRAFFIC = dict(tracks=[track(70, 3.7, 5.0)])

  def test_the_edge_alone_still_refuses_with_nothing_in_the_lane(self):
    """The behavior before this change, kept: an untrusted edge and no other evidence is a refusal.
    If this ever passes, the substitution has become unconditional."""
    det = run(PassingAssistDetector(), STUCK_FRAMES, **self.BAD_EDGE)
    assert not det.left_geometry_ok

  def test_a_car_travelling_our_way_down_it_makes_it_a_lane(self):
    """The case. Same untrusted edge, one corroborated vehicle going our way."""
    det = run(PassingAssistDetector(), STUCK_FRAMES, **self.BAD_EDGE, **self.TRAFFIC)
    assert det.adjacent.left.same_direction_recent, "the fixture never established the traffic"
    assert det.left_geometry_ok

  def test_it_does_not_override_paint(self):
    """Traffic says the lane is DRIVEABLE. It says nothing about whether the model can see a lane
    line there, and a lane the model cannot see is one it cannot steer into."""
    det = run(PassingAssistDetector(), STUCK_FRAMES, probs=(0.2, 0.99, 0.99, 0.2),
              **self.BAD_EDGE, **self.TRAFFIC)
    assert not det.left_geometry_ok

  def test_it_does_not_override_lane_width(self):
    """Same reasoning. Something driving down a 2 m gap does not make it a lane to move into."""
    det = run(PassingAssistDetector(), STUCK_FRAMES, ll=(-3.85, -1.85, 1.85, 5.5),
              **self.BAD_EDGE, **self.TRAFFIC)
    assert not det.left_geometry_ok

  def test_the_room_past_the_line_is_waived_with_it_not_separately(self):
    """Both edge-derived terms go together. edge_beyond is measured FROM the edge, so an untrusted
    edge makes it meaningless rather than merely false -- refusing on it would be refusing on a
    number derived from the thing we just agreed not to trust."""
    tight = dict(edges=(-5.8, 2.4))   # only 0.3 m past the far line, under MIN_EDGE_BEYOND_LINE_M
    assert not run(PassingAssistDetector(), STUCK_FRAMES, **tight).left_geometry_ok
    assert run(PassingAssistDetector(), STUCK_FRAMES, **tight, **self.TRAFFIC).left_geometry_ok

  def test_a_single_stray_return_cannot_vouch(self):
    """The ordering that matters: evidence which OPENS a maneuver must not be cheaper than evidence
    that refuses one. same_direction_recent needs SAME_DIRECTION_FRAMES corroborating messages, so
    one frame of traffic does not unlock an untrusted edge."""
    det = run(PassingAssistDetector(), 1, **self.BAD_EDGE, **self.TRAFFIC)
    assert not det.adjacent.left.same_direction_recent
    assert not det.left_geometry_ok


class TestLeftLaneHogs:
  """Asked for by name on 2026-08-09, at the end of a list that also included the horn, the high
  beams, a brake check and "a little love tap". This is the one that tells the next drive
  something: it is the deficit threshold's own evidence, counted on the road.

  Three terms, and the third is what makes it a hog rather than traffic -- a lane free to their
  right. Without it they are not hogging anything, they are the front of the queue.
  """

  HOG_FRAMES = int(14.0 / DT_MDL)          # comfortably past HOG_MIN_S
  # Leftmost lane with a real lane to the right: no far-left paint, road edge well out on the right.
  PASSING_LANE = IN_LEFT_LANE
  # Leftmost, but only a shoulder to the right -- an ordinary two-lane road.
  NOWHERE_TO_GO = dict(probs=(0.1, 0.99, 0.99, 0.2), edges=(-2.2, 2.4))

  def test_a_car_camped_in_the_passing_lane_is_counted(self):
    det = run(PassingAssistDetector(), self.HOG_FRAMES, **self.PASSING_LANE)
    assert det.hog_count == 1
    assert det.hog_seconds > 3.0

  def test_not_counted_when_there_is_still_a_lane_further_left(self):
    """The default road puts us in the right-hand lane with a lane to our left. Being behind
    something slow there is ordinary traffic, and counting it would drown the number."""
    det = run(PassingAssistDetector(), self.HOG_FRAMES)
    assert det.hog_count == 0

  def test_not_counted_when_they_have_nowhere_to_go(self):
    """The term that makes this mean something. On a two-lane road the car ahead is not hogging the
    passing lane, it IS the road -- and a counter that fired here would say nothing about anyone's
    behavior."""
    det = run(PassingAssistDetector(), self.HOG_FRAMES, **self.NOWHERE_TO_GO)
    assert det.hog_count == 0

  def test_a_brief_slowdown_is_not_a_hog(self):
    """Everybody slows for a moment. See HOG_MIN_S -- without it the count is dominated by ordinary
    bunching and stops being about anyone camping."""
    det = run(PassingAssistDetector(), int(5.0 / DT_MDL), **self.PASSING_LANE)
    assert det.hog_count == 0
    assert det.hog_seconds == 0.0

  def test_one_hog_is_counted_once_however_long_he_sits_there(self):
    """The seconds keep climbing; the count does not. A driver reads them as two different facts and
    a count that ticked per frame would be meaningless."""
    det = run(PassingAssistDetector(), int(60.0 / DT_MDL), **self.PASSING_LANE)
    assert det.hog_count == 1
    assert det.hog_seconds > 45.0

  def test_a_second_one_later_counts_again(self):
    det = run(PassingAssistDetector(), self.HOG_FRAMES, **self.PASSING_LANE)
    run(det, int(6.0 / DT_MDL), status=False, **self.PASSING_LANE)   # road clears
    run(det, self.HOG_FRAMES, **self.PASSING_LANE)
    assert det.hog_count == 2


class TestASuggestionSurvivesAOneFrameDip:
  """59 suggestions in 62.7 s of wanted time, median length 0.10 s -- two frames.

  Measured on the 2026-08-09 drive. The decision was right and unusable: four gates each sat near
  their threshold and any one dipping for a single frame withdrew the whole suggestion. It never
  appeared long enough to see, which is why he took none of the 59.

  No single term was at fault -- a non-geometry gate 42 %, paint 31 %, width 14 %, road edge 8 % --
  so the hysteresis goes where all of them converge, on the output.
  """

  def test_a_momentary_geometry_dip_does_not_withdraw_it(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES)
    assert det.suggestion == Side.left, "the fixture never suggested"
    run(det, 2, probs=(0.2, 0.99, 0.99, 0.2))     # paint drops out for two frames
    assert det.suggestion == Side.left
    assert det.blocked_by == Blocked.none, "a held frame must not record a block"

  def test_but_a_sustained_one_does(self):
    """The hold is a bridge, not a latch. Past SUGGESTION_HOLD_S the gate is simply right."""
    det = run(PassingAssistDetector(), STUCK_FRAMES)
    run(det, int((SUGGESTION_HOLD_S + 0.4) / DT_MDL), probs=(0.2, 0.99, 0.99, 0.2))
    assert det.suggestion == Side.none
    assert det.blocked_by == Blocked.noLaneAvailable

  def test_the_driver_taking_over_ends_it_immediately(self):
    """Not on the allow list, and the first draft of this held through it -- which would have kept
    suggesting at a driver who had just grabbed the wheel."""
    det = run(PassingAssistDetector(), STUCK_FRAMES)
    run(det, 1, blinker=True)
    assert det.suggestion == Side.none
    assert det.blocked_by == Blocked.driverActive

  def test_oncoming_traffic_ends_it_immediately(self):
    """The one the whole module is built around. Holding a suggestion through opposing traffic
    would be inventing evidence, and half a second is a long way at a closing speed of 120 mph.

    Asserted at the exact frame the veto lands rather than after a fixed wait, so this cannot pass
    merely because the hold happened to expire first."""
    det = run(PassingAssistDetector(), STUCK_FRAMES)
    assert det.suggestion == Side.left
    oncoming = [track(90, 3.7, -27.0 - SLOW_LEAD_MS)]
    for _ in range(12):
      run(det, 1, tracks=oncoming)
      if det.blocked_by == Blocked.oncomingLane:
        break
    assert det.blocked_by == Blocked.oncomingLane, "the veto never fired; the test proves nothing"
    assert det.suggestion == Side.none, "a suggestion was held through opposing traffic"

  def test_it_cannot_create_a_suggestion_of_its_own(self):
    """It only ever extends one that already stood. If it could start one it would be a second
    trigger, hidden behind the first, and the confirmation window would stop meaning anything."""
    det = run(PassingAssistDetector(), 1)          # nowhere near the confirmation window
    assert det.suggestion == Side.none
    run(det, 4)
    assert det.suggestion == Side.none or det._held_side == Side.none
