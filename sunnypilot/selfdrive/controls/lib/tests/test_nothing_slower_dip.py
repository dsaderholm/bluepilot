"""A brief `nothingSlower` dip must not abort a maneuver that the suggestion hold is riding out.

THE BUG, found on build 8e979260 across 29 routes and 5.9 hours. `nothingSlower` is one of the three
HOLD_THROUGH gates, so `_hold_suggestion` restores the suggestion through a dip of up to
SUGGESTION_HOLD_S. But its early return in `_decide` called `_reset_outputs(Blocked.nothingSlower)`
WITHOUT keep_wanted, which zeroed `wanted_side` on the same frame. update() runs:

    _decide           -> wanted_side = none, blocked_by = nothingSlower
    _hold_suggestion  -> suggestion restored, blocked_by overwritten to none
    _run_maneuver     -> reads wanted_side = none -> ABORTS

So the maneuver backed out while the screen still showed the suggestion, and the abort frame read
`wantedSide none, blockedBy none` -- the largest abort bucket on those drives (31 of 73), which read as
unexplained because both fields that would have named the cause had been overwritten.

The other two HOLD_THROUGH gates never had this. `noLaneAvailable` feeds the debounce the raw answer
(`_debounce_wanted(Side.none)`) and then resets with keep_wanted=True, under a comment recording that
this exact early-return-before-the-debounce shape once made the whole debounce useless.

WHY NOT keep_wanted=True ALONE. Tried first, and the suite stayed green, which is the trap: the
debounce only runs further down `_decide`, past this early return, so keep_wanted alone means
wanted_side is never touched at all and a persistent nothingSlower FREEZES it at its last value. That
is the stale-wanted-into-keep-right bug the hard clear was added for. The existing guard in
test_drive_scenarios drives `status=False` -- the noLead path -- so it cannot see this branch.
Hence the third test below.
"""
from cereal import custom
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot.selfdrive.controls.lib.passing_assist import WANTED_FALL_S
from openpilot.sunnypilot.selfdrive.controls.lib.tests.test_passing_assist import (
  CRUISE_MS, SLOW_LEAD_MS, IN_LEFT_LANE, make_sm, keep_right_det,
)

Side = custom.LongitudinalPlanSP.PassingAssist.Side
Blocked = custom.LongitudinalPlanSP.PassingAssist.Blocked
Phase = custom.LongitudinalPlanSP.PassingAssist.Maneuver


def drive(det, seconds, **kw):
  for _ in range(max(1, int(round(seconds / DT_MDL)))):
    det.update(make_sm(**kw), CRUISE_MS, True)
  return det


def to_signaling(det):
  """Approach a slow lead until the passing maneuver is signaling, as a driver would see it."""
  drive(det, 3.0, status=False)
  for _ in range(int(round(12.0 / DT_MDL))):
    drive(det, DT_MDL, v_lead=SLOW_LEAD_MS, d_rel=150.0)
    if det.maneuver.phase == Phase.signaling:
      return det
  raise AssertionError("never reached signaling -- the scenario is not exercising the maneuver at all")


def test_a_nothing_slower_dip_shorter_than_the_fall_does_not_abort():
  det = to_signaling(keep_right_det())
  aborts_before = det.maneuver.aborts

  # The lead briefly stops qualifying: running at our cruise speed, so nothing is slower.
  dip = WANTED_FALL_S * 0.5
  drive(det, dip, v_lead=CRUISE_MS, d_rel=150.0)
  assert det.blocked_by in (Blocked.nothingSlower, Blocked.none), (
    "the dip must actually reach the nothingSlower branch or this test proves nothing")

  drive(det, 1.0, v_lead=SLOW_LEAD_MS, d_rel=150.0)
  assert det.maneuver.aborts == aborts_before, (
    "a %.2f s nothingSlower dip aborted the maneuver -- wanted_side was hard-cleared under the "
    "suggestion hold instead of riding the debounce" % dip)


def test_a_sustained_nothing_slower_still_ends_it():
  """The fix must not turn a real 'no longer worth passing' into a maneuver that never lets go."""
  det = to_signaling(keep_right_det())
  drive(det, WANTED_FALL_S + 1.0, v_lead=CRUISE_MS, d_rel=150.0)
  assert det.wanted_side == Side.none, "wanted_side must still fall when nothing is slower for real"
  assert det.maneuver.phase != Phase.signaling


def test_a_persistent_nothing_slower_cannot_freeze_wanted_into_a_keep_right():
  """The bug the hard clear existed for. keep_wanted=True ALONE reintroduces it with a green suite,
  because the debounce is only reached past this early return -- so wanted_side would freeze. This is
  the nothingSlower path specifically, which the noLead-based scenario guard never exercises."""
  det = to_signaling(keep_right_det())
  drive(det, 30.0, v_lead=CRUISE_MS, d_rel=150.0, **IN_LEFT_LANE)
  assert det.wanted_side == Side.none, "wanted_side froze through a persistent nothingSlower"
  assert det.maneuver.phase == Phase.idle, "the passing machine must stay out of a keep-right"
