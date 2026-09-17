"""The crossing waits for a REAL brake request, never for engine braking. His call, 2026-09-16:

  "Real brake requests only. It's fine if it has to brake to lane change, I just want it to brake
   the least amount possible."

The crossing had reused acc_braking_at_decision, a METRIC flag that deliberately counts engine
braking. Following a slower lead at highway speed, Ford holds AccPrpl_A_Rq in the engine band the
whole time with aEgo ~0, so across routes 046a-046f and 471-476 the gate held 96 % of ready-to-cross
time at 60+ mph: 12 sequences, 2 crossed, 5 expired their window, zero brake requests.

Both halves are pinned, plus the metric staying as it was -- narrowing the crossing must not quietly
rewrite what "ACC was braking when it decided" means in every recorded drive.
"""
from openpilot.sunnypilot.selfdrive.controls.lib.passing_assist import PassingAssistDetector
from openpilot.sunnypilot.selfdrive.controls.lib.passing_maneuver import Phase
from openpilot.sunnypilot.selfdrive.controls.lib.tests.test_passing_assist import (
  CRUISE_MS, STUCK_FRAMES, Side, make_sm,
)


def _phases(**kw):
  det = PassingAssistDetector()
  seen = []
  for _ in range(STUCK_FRAMES):
    det.update(make_sm(**kw), CRUISE_MS, True)
    seen.append(det.maneuver.phase)
  return det, seen


def test_engine_braking_behind_a_slower_lead_does_not_hold_the_crossing():
  det, seen = _phases(acc_propulsion=-1.2)
  assert det.acc_braking_at_decision, "fixture is not in the engine-braking band"
  assert not det.acc_brake_requested
  assert Phase.signaling in seen, "never signalled -- the fixture no longer reaches the crossing"
  assert Phase.changing in seen, "engine braking held the crossing"


def test_a_real_brake_request_still_holds_it():
  det, seen = _phases(acc_braking=True)
  assert det.acc_brake_requested
  assert Phase.signaling in seen and det.suggestion == Side.left, \
    "the hold must be what stops it, not a missing signal or suggestion"
  assert Phase.changing not in seen, "crossed while ACC was requesting the brakes"


def test_the_crossing_goes_as_soon_as_the_brake_request_ends():
  det, _ = _phases(acc_braking=True)
  assert det.maneuver.phase == Phase.signaling
  for _ in range(3):
    det.update(make_sm(acc_braking=False, acc_propulsion=-1.2), CRUISE_MS, True)
  assert det.maneuver.phase == Phase.changing, "kept waiting after the brake request ended"


def test_the_decision_metric_still_counts_engine_braking():
  det, _ = _phases(acc_propulsion=-1.2)
  assert det.acc_braking_at_decision, "the crossing change rewrote the metric"
