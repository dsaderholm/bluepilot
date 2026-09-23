"""A dropped radar return inside the grace window must not retract the signal.

THE MEASURED FAILURE, build 0bde6ff361 across 21 routes and 154.6 moving minutes: 33 aborts, an
unchanged 12.8 per moving hour. Pulled at full rate, seven of the seventeen aborts examined carry
one signature -- a single 20 Hz frame of `noLead` while the raw geometry still wanted the lane:

    route 0000048e seg 18, 74 mph
      raw      L x23  - x1
      decided  nothingSlower x23  noLead x1      <- one frame ends a 1.15 s signal
    then re-arms and is killed again at +0.4 s, +0.6 s and +10 s. Four aborts in twelve seconds.

`_lead_gap()` exists so that "a single missed radar return is the same car, not a different
situation". But the early return ORed the gap test with `approach_seconds == 0.0`, so with no
confirmation yet established a one-frame dropout skipped the window entirely and hard-cleared the
debounced side. The verdict refusal is right and is kept -- an unestablished approach must not open
anything. Zeroing the SIDE along with it is what aborted the maneuver.

WHAT THIS IS NOT. The first attempt fed the debounce `none` and set keep_wanted on the whole branch,
the shape cc9b910b0a used for nothingSlower. It FAILED
`TestTheSignalDoesNotFlicker::test_no_pass_warranted_clears_it_immediately`, which states the
property directly: hysteresis is for geometry wobbling, and a lead that has genuinely gone must not
wait out WANTED_FALL_S. That test was right and the change was wrong, so the hard clear past the
grace is untouched here and only the window the grace already owns is corrected.
"""
from cereal import custom
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot.selfdrive.controls.lib.passing_assist import LEAD_GAP_GRACE_S, WANTED_FALL_S
from openpilot.sunnypilot.selfdrive.controls.lib.tests.test_passing_assist import (
  CRUISE_MS, SLOW_LEAD_MS, make_sm, keep_right_det,
)

Side = custom.LongitudinalPlanSP.PassingAssist.Side
Blocked = custom.LongitudinalPlanSP.PassingAssist.Blocked
Phase = custom.LongitudinalPlanSP.PassingAssist.Maneuver


def drive(det, seconds, **kw):
  for _ in range(max(1, int(round(seconds / DT_MDL)))):
    det.update(make_sm(**kw), CRUISE_MS, True)
  return det


def to_signaling(det):
  drive(det, 3.0, status=False)
  for _ in range(int(round(12.0 / DT_MDL))):
    drive(det, DT_MDL, v_lead=SLOW_LEAD_MS, d_rel=150.0)
    if det.maneuver.phase == Phase.signaling:
      return det
  raise AssertionError("never reached signaling -- the scenario is not exercising the maneuver")


def test_a_dropout_with_no_confirmation_keeps_the_side_inside_the_grace():
  """The measured event, reproduced through the term it actually took: approach_seconds == 0."""
  det = to_signaling(keep_right_det())
  assert det.wanted_side != Side.none, "the fixture never wanted a pass"

  det.approach_seconds = 0.0
  drive(det, DT_MDL, status=False)

  assert det.blocked_by == Blocked.noLead, "the frame must reach the noLead branch"
  assert det.wanted_side != Side.none, (
    "one dropped radar frame with no established confirmation zeroed the debounced side -- the "
    "shape of 7 of 17 aborts measured at 20 Hz on build 0bde6ff361")


def test_a_lead_gone_past_the_grace_still_clears_at_once():
  """TestTheSignalDoesNotFlicker's property, asserted here too so this file cannot drift into
  contradicting it: past the window a departed lead must NOT wait out WANTED_FALL_S."""
  det = to_signaling(keep_right_det())
  det.approach_seconds = 0.0
  drive(det, LEAD_GAP_GRACE_S + 0.1, status=False)
  assert det.wanted_side == Side.none, "a lead gone past the grace must clear immediately"
  assert LEAD_GAP_GRACE_S + 0.1 < WANTED_FALL_S, (
    "this test only means something while the grace is shorter than the debounce fall")


def test_the_carry_never_opens_anything():
  """The verdict refusal is the half that must survive: no suggestion on an unestablished approach."""
  det = to_signaling(keep_right_det())
  det.approach_seconds = 0.0
  drive(det, DT_MDL, status=False)
  assert det.suggestion == Side.none, "a carried side must not carry a suggestion with it"
  assert det.clear_side == Side.none
