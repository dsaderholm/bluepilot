"""
BluePilot: whole drives, start to finish.

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

    # 7. Sitting in the left lane with nothing to pass: it wants us back right -- but not for
    #    THIRTY SECONDS, and that is deliberate rather than slow. The 20 s settle after suggesting
    #    a pass is what stops a three-lane road turning into a weave, and only once it expires does
    #    the 10 s clear-lane delay start. Worth stating here because 30 s feels wrong until you
    #    remember what it is buying.
    drive(det, 20.0, status=False, **IN_LEFT_LANE)
    assert det.suggestion == Side.none, "still settling from the pass"
    drive(det, 12.0, status=False, **IN_LEFT_LANE)
    assert det.suggestion == Side.right
    assert det.reason == Reason.keepRight
    assert det.keep_right_maneuver.phase == Phase.signaling
    assert det.maneuver.phase == Phase.idle, "the passing machine must stay out of a keep-right"

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
