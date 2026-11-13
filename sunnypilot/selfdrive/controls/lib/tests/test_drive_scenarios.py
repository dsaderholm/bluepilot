"""
FusionPilot: whole drives, start to finish.

Every other test here checks one thing in isolation, and every bug found on the road tonight was an
interaction between two things that were each correct. A green PASS LEFT shown seconds after the
car backed out of that pass. A slow-pass warning hiding the abort readout. A confirmation timer
that survived a dropped radar frame while the verdict it fed did not.

So these walk a plausible piece of driving frame by frame and assert what the DRIVER would see at
each stage -- the same fields the panel reads, in the order they would appear. They are deliberately
about sequence rather than about any single gate.
"""

from cereal import custom
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot.selfdrive.controls.lib.tests.test_passing_assist import (
  CRUISE_MS, SLOW_LEAD_MS, IN_LEFT_LANE, make_sm, keep_right_det, track,
)

Side = custom.LongitudinalPlanSP.PassingAssist.Side
Blocked = custom.LongitudinalPlanSP.PassingAssist.Blocked
Reason = custom.LongitudinalPlanSP.PassingAssist.Reason
Phase = custom.LongitudinalPlanSP.PassingAssist.Maneuver


def drive(det, seconds, **kw):
  for _ in range(max(1, int(round(seconds / DT_MDL)))):
    det.update(make_sm(**kw), CRUISE_MS, True)
  return det


class TestAnOrdinaryInterstatePass:
  """Empty road, a lorry appears, you go round it, you come back. The whole loop."""

  def test_the_sequence_a_driver_would_see(self):
    det = keep_right_det()

    # 1. Nothing ahead. The panel should say so rather than saying nothing.
    drive(det, 3.0, status=False)
    assert det.blocked_by == Blocked.noLead

    # 2. A lorry, and it is slower. Building toward a suggestion, not yet suggesting.
    # HALF the confirmation, not a hardcoded second -- this step is "part way through", and writing
    # that as an absolute time silently became "all the way through" when the default dropped.
    drive(det, det.persistence_s / 2, v_lead=SLOW_LEAD_MS, d_rel=180.0)
    assert det.blocked_by == Blocked.nothingSlower
    assert 0.0 < det.approach_seconds < det.persistence_s
    assert det.suggestion == Side.none

    # 3. The blinker goes on the moment it spots the car, BEFORE the confirmation finishes -- the
    #    two clocks overlap, which is the whole reason it can beat ACC to the brakes.
    assert det.maneuver.phase == Phase.signaling
    assert det.maneuver.blinker_on
    assert det.suggestion == Side.none, "signaling, not yet committed"

    # 4. Confirmation lands and the signal lead has elapsed, so it would begin crossing.
    drive(det, 2.0, v_lead=SLOW_LEAD_MS, d_rel=150.0)
    assert det.suggestion == Side.left
    assert det.reason == Reason.passing
    drive(det, 1.5, v_lead=SLOW_LEAD_MS, d_rel=120.0)
    assert det.maneuver.phase == Phase.changing
    assert det.maneuver.blinker_on and det.maneuver.steering_active

    # 5. The driver does it for real. Counted as an agreement, and the lead time is the warning.
    drive(det, 0.5, v_lead=SLOW_LEAD_MS, d_rel=100.0, blinker=True)
    assert det.driver_passes == 1
    assert det.driver_passes_agreed == 1
    assert det.driver_pass_lead_s > 1.0
    assert det.suggestions_taken == 1

    # 6. Stalk off, past the lorry, road clear. A short settle, then quiet.
    drive(det, 5.0, status=False, **IN_LEFT_LANE)
    assert det.driver_change_standdown == 0.0
    assert det.suggestion == Side.none

    # 7. Sitting in the left lane with nothing to pass: it wants us back right -- but not
    #    immediately. The 20 s settle after suggesting a pass is what stops a three-lane road
    #    turning into a weave, and only once it expires does the clear-lane delay start.
    #
    #    25 s TOTAL as of 2026-08-09, down from 30: PassingAssistKeepRightDelay went 10 -> 5,
    #    because "I want the left lane to be like the floor is lava". This block therefore has to
    #    stop SHORT of the total rather than land on it -- it used to drive to exactly 25 s and
    #    assert nothing had happened, which was true only while the delay was 10.
    drive(det, 15.0, status=False, **IN_LEFT_LANE)
    assert det.suggestion == Side.none, "still settling from the pass"
    # Stepped one frame at a time to the MOMENT it asks, rather than driving a fixed block and
    # looking afterwards. A fixed block used to work and no longer does, for a good reason: the
    # sequence now runs through and stands down instead of re-arming forever, so a 12 s window
    # ends with the machine idle after a completed run. Asserting on the moment it asks is what
    # this step was always trying to say.
    for _ in range(int(round(20.0 / DT_MDL))):
      drive(det, DT_MDL, status=False, **IN_LEFT_LANE)
      if det.suggestion == Side.right:
        break
    assert det.suggestion == Side.right
    assert det.reason == Reason.keepRight
    assert det.keep_right_maneuver.phase == Phase.signaling
    assert det.maneuver.phase == Phase.idle, "the passing machine must stay out of a keep-right"

    # ...and it says it ONCE. This is the loop from the road -- "would be changing right, would be
    # done, over and over again" -- and the reason it happened is that a dry run changes nothing,
    # so the sequence that just finished is still just as justified as when it started.
    drive(det, 8.0, status=False, **IN_LEFT_LANE)
    assert not det.keep_right_maneuver.blinker_on, "the run should have completed"
    assert det.keep_right_maneuver.standdown_remaining > 0.0
    assert det.keep_right_maneuver.standdown_after_completion, "not a reversal -- it ran through"
    drive(det, 2.0, status=False, **IN_LEFT_LANE)
    assert not det.keep_right_maneuver.blinker_on, "and it started straight over again"

  def test_the_drive_leaves_a_usable_record(self):
    det = keep_right_det()
    drive(det, 8.0, v_lead=SLOW_LEAD_MS)
    drive(det, 0.5, v_lead=SLOW_LEAD_MS, blinker=True)
    drive(det, 3.0, status=False)
    assert det.lifetime == (1, 1, 1)
    assert det.wanted_seconds > 0.0
    assert det.clear_share > 0.0, "a drive where it mostly agreed should say so"


class TestATwoLaneHighwayWithOncomingTraffic:
  """US-6 or US-89: a slow vehicle, a lane to the left, and it is not ours."""

  ONCOMING = [track(90, 3.7, -27.0 - CRUISE_MS)]

  def test_it_wants_the_pass_and_refuses_it_for_the_right_reason(self):
    det = keep_right_det()

    # Slow lead confirmed, and it would go -- until traffic comes the other way.
    drive(det, 4.0, v_lead=SLOW_LEAD_MS)
    assert det.suggestion == Side.left

    drive(det, 0.5, v_lead=SLOW_LEAD_MS, tracks=self.ONCOMING)
    assert det.blocked_by == Blocked.oncomingLane
    assert det.suggestion == Side.none
    assert det.maneuver.phase in (Phase.aborting, Phase.waiting, Phase.idle),       "must not still be crossing into a head-on lane"

    # And it stays refused after they have gone by, which is the whole point of the memory.
    drive(det, 20.0, v_lead=SLOW_LEAD_MS)
    assert det.blocked_by == Blocked.oncomingLane
    assert det.adjacent.oncoming_seconds_left > 0.0

  def test_the_refusal_is_counted_against_a_pass_the_driver_makes_anyway(self):
    """The driver can see it is clear; the system cannot. That disagreement is the measurement."""
    det = keep_right_det()
    drive(det, 4.0, v_lead=SLOW_LEAD_MS, tracks=self.ONCOMING)
    drive(det, 0.5, v_lead=SLOW_LEAD_MS, tracks=self.ONCOMING, blinker=True)
    assert det.driver_passes == 1
    assert det.driver_passes_agreed == 0
    assert det.driver_pass_miss_reason == int(Blocked.oncomingLane)


class TestTheOutermostLaneWithABarrier:
  """I-15, and the report that came back twice: "it just keeps trying to go into the shoulder",
  from the furthest right lane, with a red line on screen where the barrier wall is.

  test_passing_assist.py covers right_geometry_ok directly. That is the unit, not the outcome --
  what he saw was the whole detector arriving at MOVE RIGHT, and there was no test standing between
  the gate and that. Every path that can suggest a side is walked here instead of the one that
  happened to be wrong.

  The geometry is what the model produces when there is no lane out there: laneLines always has
  four entries, so the outermost one lands on the strongest feature left, which is the road edge
  itself. Shoulder width is AASHTO's widest, 12 ft, so nothing about width can refuse it.
  """

  # ego's right line at +1.85, "far right line" on the barrier 12 ft beyond it, road edge there too
  RIGHTMOST_LANE = dict(ll=(-5.5, -1.85, 1.85, 1.85 + 3.66),
                        probs=(0.9, 0.99, 0.99, 0.9),
                        edges=(-7.0, 1.85 + 3.66))

  # ...and the mirror image: leftmost lane, median barrier just past the fog line. A 4 ft inside
  # shoulder, which is the narrowest case the degeneracy test has to survive without refusing
  # honest left lanes.
  LEFTMOST_LANE = dict(ll=(-1.85 - 3.66, -1.85, 1.85, 5.5),
                       probs=(0.9, 0.99, 0.99, 0.9),
                       edges=(-1.85 - 3.66, 7.0))

  def test_a_slow_car_ahead_never_sends_us_into_the_shoulder(self):
    det = keep_right_det()
    drive(det, 30.0, v_lead=SLOW_LEAD_MS, **self.RIGHTMOST_LANE)
    assert not det.right_geometry_ok
    assert det.suggestion != Side.right, "suggested moving into the shoulder"
    assert not det.keep_right_maneuver.blinker_on, "signalled into the shoulder"
    assert det.maneuver.side != Side.right

  def test_and_neither_does_keep_right_with_no_lead_at_all(self):
    """The path that actually fired on the road -- keep-right, not a pass. It has its own gate and
    its own maneuver, and reading one does not tell you about the other."""
    det = keep_right_det()
    drive(det, 60.0, status=False, **self.RIGHTMOST_LANE)
    assert det.suggestion != Side.right
    assert det.reason != Reason.keepRight
    assert not det.keep_right_maneuver.blinker_on

  def test_the_left_side_is_still_offered_from_that_lane(self):
    """The refusal has to be about the shoulder, not about the road. If this also goes quiet the
    fix is just a feature that never speaks, which is the trade that produced the shoulder in the
    first place."""
    det = keep_right_det()
    drive(det, 30.0, v_lead=SLOW_LEAD_MS, **self.RIGHTMOST_LANE)
    assert det.left_geometry_ok, "refused a genuine lane to the left"
    assert det.suggestion == Side.left

  def test_the_median_is_not_a_passing_lane_either(self):
    """Same fault, opposite side, and worse: a pass to the left is the suggestion this thing exists
    to make, so nothing downstream would look twice at it."""
    det = keep_right_det()
    drive(det, 30.0, v_lead=SLOW_LEAD_MS, **self.LEFTMOST_LANE)
    assert not det.left_geometry_ok, "offered the median as a passing lane"
    assert det.suggestion != Side.left
    assert not det.maneuver.blinker_on

  def test_a_missing_road_edge_refuses_rather_than_assumes(self):
    """modelV2 does not always give two road edges. With nothing to measure past the far line,
    the honest answer is no -- an unmeasured shoulder must not read as an empty lane."""
    det = keep_right_det()
    scene = dict(self.RIGHTMOST_LANE)
    drive(det, 20.0, v_lead=SLOW_LEAD_MS, **scene)
    before = det.left_geometry_ok
    assert before
    det2 = keep_right_det()
    for _ in range(int(20.0 / DT_MDL)):
      sm = make_sm(v_lead=SLOW_LEAD_MS, **scene)
      sm.data['modelV2'].roadEdges = sm.data['modelV2'].roadEdges[:1]   # right edge gone
      det2.update(sm, CRUISE_MS, True)
    assert not det2.right_geometry_ok
    assert det2.suggestion != Side.right
