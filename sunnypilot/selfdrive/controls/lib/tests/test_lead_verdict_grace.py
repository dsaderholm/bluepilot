"""The grace window carries the slow VERDICT through a lead that briefly stops qualifying, not only the timer.

Route 0000046a (drive 61): blockedBy flipped nothingSlower <-> noLaneAvailable every 0.1-0.3 s. Two causes,
both measured on the car -- see LEAD_SWAP_D_M in passing_assist.py for the numbers:

  - the radar's lateral noise on the SAME car pushing d_path across the 1.5 m bound. The bound failure was
    free in the timer and still returned False, so the verdict blinked while the confirmation survived.
  - leadOne jumping to another vehicle for a few frames. That car's vLead failed the deficit and
    _clear_confirmation started the whole confirmation over.

And the guard that keeps this from being an opening: only a verdict built by at least the grace's worth of
evidence is carried, because WANTED_RISE_S is shorter than the grace and one radar frame carried for 0.4 s
would light the blinker.
"""
from cereal import custom
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot.selfdrive.controls.lib.passing_assist import (
  LEAD_GAP_GRACE_S, LEAD_SWAP_D_M, WANTED_RISE_S, PassingAssistDetector,
)
from openpilot.sunnypilot.selfdrive.controls.lib.tests.test_passing_assist import (
  CRUISE_MS, SLOW_LEAD_MS, STUCK_FRAMES, make_sm, run,
)

Side = custom.LongitudinalPlanSP.PassingAssist.Side
Blocked = custom.LongitudinalPlanSP.PassingAssist.Blocked

# The far-left line is not believed, so a spotted lead reads noLaneAvailable and an unspotted one reads
# nothingSlower -- the two values the drive flipped between. Nothing holds a suggestion here to mask a flip.
NO_LANE = dict(probs=(0.1, 0.99, 0.99, 0.2))
# Our own speed held fixed, so a swap to a faster car changes the lead and nothing else about the scene.
SAME_EGO = dict(v_ego=SLOW_LEAD_MS)
# Another vehicle far enough from the tracked one to be a swap, and fast enough to fail the deficit.
OTHER_CAR = dict(d_rel=40.0 + LEAD_SWAP_D_M * 5, v_lead=CRUISE_MS)
GRACE_FRAMES = int(round(LEAD_GAP_GRACE_S / DT_MDL))


def test_the_constants_leave_room_for_the_guard():
  """The whole reason for the establishment guard. If the rise ever outlasts the grace it is redundant; if
  someone shortens the grace below the rise this still holds -- but check before deleting the guard."""
  assert WANTED_RISE_S < LEAD_GAP_GRACE_S


class TestTheRadarsLateralNoise:
  def test_a_lead_straddling_the_path_bound_does_not_flicker_blocked_by(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, **NO_LANE)
    assert det.blocked_by == Blocked.noLaneAvailable
    seen = set()
    for i in range(int(2.0 / DT_MDL)):
      # 0.3 m and 2.5 m off the path, frame about -- the p90-p99 yRel swing measured on one car.
      det.update(make_sm(lead_y=0.3 if i % 2 == 0 else 2.5, **NO_LANE), CRUISE_MS, True)
      seen.add(det.blocked_by)
    assert seen == {Blocked.noLaneAvailable}, "an in-path lead's radar noise flickered the verdict"

  def test_a_lead_that_really_leaves_the_path_still_ends_it(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES)
    assert det.suggestion == Side.left
    run(det, int(1.5 / DT_MDL), lead_y=3.7)
    assert det.suggestion == Side.none
    assert det.blocked_by == Blocked.nothingSlower

  def test_one_frame_of_evidence_is_not_carried_past_the_path_bound(self):
    det = run(PassingAssistDetector(), int(1.0 / DT_MDL), status=False)
    run(det, 1)
    wanted = []
    for _ in range(GRACE_FRAMES):
      det.update(make_sm(lead_y=3.7), CRUISE_MS, True)
      wanted.append(det.wanted_side)
    assert set(wanted) == {Side.none}, "one in-path radar frame lit the blinker through the grace"


class TestLeadOneSwappingVehicles:
  def test_a_brief_swap_to_a_faster_car_keeps_the_confirmation(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, **SAME_EGO)
    before = det.approach_seconds
    assert before > 0.0 and det.suggestion == Side.left
    run(det, GRACE_FRAMES // 2, **OTHER_CAR, **SAME_EGO)
    assert det.lead_is_slow
    run(det, 1, **SAME_EGO)
    assert abs(det.approach_seconds - before) < 1e-9, "a swap and back started the confirmation over"
    assert det.suggestion == Side.left

  def test_a_swap_that_persists_is_judged_on_the_new_car(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, **SAME_EGO)
    run(det, GRACE_FRAMES + 2, **OTHER_CAR, **SAME_EGO)
    assert not det.lead_is_slow
    assert det.approach_seconds == 0.0

  def test_the_carry_ends_with_the_grace_not_after_it(self):
    det = run(PassingAssistDetector(), STUCK_FRAMES, **SAME_EGO)
    run(det, GRACE_FRAMES, **OTHER_CAR, **SAME_EGO)
    assert det.lead_is_slow, "the grace must still be carrying on its last frame"
    run(det, 1, **OTHER_CAR, **SAME_EGO)
    assert not det.lead_is_slow, "the frame after the grace must judge the new car"

  def test_the_swap_check_projects_the_lead_forward_through_a_dropout(self):
    """A car we close on at 20 m/s moves 6 m in 0.3 s of lost returns. Where it reappears is the same car;
    where it WAS is not. Without the projection both answers invert, and without the aging the anchor
    never moves at all."""
    closing = 20.0
    det = run(PassingAssistDetector(), STUCK_FRAMES, d_rel=120.0, v_lead=SLOW_LEAD_MS, v_ego=SLOW_LEAD_MS + closing)
    assert det._lead_anchor_d == 120.0, "the scenario must leave a tracked lead or this proves nothing"
    run(det, 6, status=False)
    projected = 120.0 - closing * 6 * DT_MDL
    assert not det._lead_swapped(projected)
    assert det._lead_swapped(projected + LEAD_SWAP_D_M + 1.0)
    assert det._lead_swapped(120.0 + LEAD_SWAP_D_M - 1.0), "a return where the car used to be is another car"

  def test_one_frame_of_evidence_is_not_carried_through_a_swap(self):
    det = run(PassingAssistDetector(), int(1.0 / DT_MDL), **OTHER_CAR, **SAME_EGO)
    assert not det.lead_is_slow
    run(det, 1, **SAME_EGO)
    wanted = []
    for _ in range(GRACE_FRAMES):
      det.update(make_sm(**OTHER_CAR, **SAME_EGO), CRUISE_MS, True)
      wanted.append(det.wanted_side)
    assert set(wanted) == {Side.none}, "one slow radar frame lit the blinker through a swap"
    assert not det.lead_is_slow
