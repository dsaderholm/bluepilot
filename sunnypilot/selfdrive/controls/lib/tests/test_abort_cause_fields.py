"""`rawWantedSide` and `blockedByDecided`: the two inputs a back-out cannot be explained without.

Route 0000046f, 2026-09-15: 13 aborts in 17 moving minutes, in bursts, every one a single frame of
`wantedSide none` while the suggestion stood and `blockedBy` read none. Two mechanisms produce that
frame and the recorded fields cannot separate them -- the debounce releasing, or a gate hard-clearing
wanted_side with the hold rewriting blockedBy to none in the same frame. See custom.capnp.

Each test asserts on the PUBLISHED message, not the attribute, because publishing the wrong one of
a near-identical pair is exactly the mistake here: `wantedSide` and `rawWantedSide` agree on most
frames, and a field fed from its neighbour reads perfectly plausibly in a drive.
"""
from cereal import custom
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot.selfdrive.controls.lib.passing_assist import (
  WANTED_RISE_S, PassingAssistDetector,
)
from openpilot.sunnypilot.selfdrive.controls.lib.tests.test_passing_assist import (
  CRUISE_MS, SLOW_LEAD_MS, STUCK_FRAMES, make_sm, run,
)

Side = custom.LongitudinalPlanSP.PassingAssist.Side
Blocked = custom.LongitudinalPlanSP.PassingAssist.Blocked

# The far-left line is not believed, so the geometry refuses and raw wanted is none.
NO_LANE = dict(probs=(0.1, 0.99, 0.99, 0.2))


def published(det):
  msg = custom.LongitudinalPlanSP.new_message()
  det.publish(msg.passingAssist)
  return msg.passingAssist


def test_the_raw_side_leads_the_debounced_one_through_the_rise():
  """The observed abort frame: the geometry wants a side, the debounce has not granted it yet."""
  det = run(PassingAssistDetector(), STUCK_FRAMES, **NO_LANE)
  assert published(det).rawWantedSide == Side.none

  # The rise costs WANTED_RISE_S of AGREEMENT, and the timer starts at zero on the frame the raw
  # answer changes -- so it is one frame longer than the window divided by the step.
  seen = []
  for _ in range(int(WANTED_RISE_S / DT_MDL) + 1):
    det.update(make_sm(), CRUISE_MS, True)
    pa = published(det)
    seen.append((pa.rawWantedSide, pa.wantedSide))
  assert (Side.left, Side.none) in seen, (
    "during the rise the raw side must read left while wantedSide is still none -- if these are "
    "equal on every frame, rawWantedSide is being fed from wanted_side")
  det.update(make_sm(), CRUISE_MS, True)
  assert published(det).wantedSide == Side.left, "the rise must still complete"


def test_the_decided_reason_survives_the_hold_that_rewrites_blocked_by():
  """A held frame: the screen keeps the suggestion, and the gate that objected is still recorded."""
  det = run(PassingAssistDetector(), STUCK_FRAMES)
  assert published(det).suggestion == Side.left

  # The lead stops being slow: nothingSlower, which is held through.
  det.update(make_sm(v_lead=CRUISE_MS, v_ego=SLOW_LEAD_MS), CRUISE_MS, True)
  pa = published(det)
  assert pa.suggestion == Side.left, "the hold must still be showing the suggestion"
  assert pa.blockedBy == Blocked.none, "blockedBy under the hold reads none -- that is the trap"
  assert pa.blockedByDecided == Blocked.nothingSlower, (
    "the reason _decide reached was overwritten before it was recorded -- blockedByDecided is fed "
    "from the post-hold value")


def test_both_agree_with_blocked_by_when_nothing_is_holding():
  """The ordinary case. A diagnostic that disagreed with the live fields on quiet frames would be
  read as a defect rather than as the pair working."""
  det = run(PassingAssistDetector(), STUCK_FRAMES, **NO_LANE)
  pa = published(det)
  assert pa.blockedBy == Blocked.noLaneAvailable
  assert pa.blockedByDecided == pa.blockedBy

  det = run(PassingAssistDetector(), STUCK_FRAMES)
  pa = published(det)
  assert pa.wantedSide == Side.left and pa.rawWantedSide == Side.left
  assert pa.blockedBy == Blocked.none and pa.blockedByDecided == Blocked.none


def test_an_early_return_publishes_no_raw_side():
  """Every early return leaves the geometry unasked, and none is the honest answer there -- not the
  previous frame's value, which is what a field left unset between frames would report."""
  det = run(PassingAssistDetector(), STUCK_FRAMES)
  assert published(det).rawWantedSide == Side.left
  run(det, 4, status=False, v_lead=CRUISE_MS)
  run(det, int(2.0 / DT_MDL), status=False, v_lead=CRUISE_MS)
  pa = published(det)
  assert pa.rawWantedSide == Side.none, "a stale raw side survived a frame that never computed one"
  assert pa.blockedByDecided == pa.blockedBy == Blocked.noLead
