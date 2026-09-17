"""A gate refusing must go through the wanted-side debounce like every other refusal.

Route 00000476, 2026-09-16, 76 mph behind a 72 mph lead with an 80 mph car in the left lane: two of
the drive's three aborts were a SINGLE frame of adjacentSlow. rawWantedSide and blockedByDecided --
published the day before for exactly this -- showed raw and wanted dropping on the same frame, which
the debounce cannot produce (WANTED_FALL_S is 0.75 s). The cause was a line in the gate-refusal
branch of _decide that reassigned wanted_side from the RAW term after _reset_outputs(keep_wanted=True)
had already preserved the debounced one. It predated keep_wanted by five days.

It cut both ways, so both are pinned: a one-frame refusal dropped a standing signal, and a refusal
on the first frame of geometry lit one without WANTED_RISE_S. Evidence that opens must never be
cheaper than evidence that refuses, and here the opening side was the cheap one.
"""
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot.selfdrive.controls.lib import adjacent_lane
from openpilot.sunnypilot.selfdrive.controls.lib.passing_assist import (
  WANTED_FALL_S, WANTED_RISE_S, PassingAssistDetector,
)
from openpilot.sunnypilot.selfdrive.controls.lib.passing_maneuver import Phase
from openpilot.sunnypilot.selfdrive.controls.lib.tests.test_passing_assist import (
  CRUISE_MS, STUCK_FRAMES, Blocked, Side, make_sm, run,
)

NO_LANE = dict(probs=(0.1, 0.99, 0.99, 0.2))


def _signaling(det):
  """Step until the dry run is showing the signal, then a few frames more. The signal comes up after
  WANTED_RISE_S and the crossing waits on the confirmation (2 s) and the blinker lead (1 s), so this
  lands mid-signal with over a second of margin. No blind spot: it outranks adjacentSlow in naming."""
  for _ in range(STUCK_FRAMES):
    det.update(make_sm(), CRUISE_MS, True)
    if det.maneuver.phase == Phase.signaling:
      break
  else:
    raise AssertionError("never reached signaling")
  run(det, 3)
  assert det.maneuver.phase == Phase.signaling, "fixture drifted past signaling before the refusal"
  return det


def _adjacent_slow(monkeypatch, on):
  """blocks_move is the term that went true for one frame on the drive. Forcing it rather than
  building a marginal track keeps the fixture about the wiring, not about radar corroboration."""
  real = adjacent_lane.AdjacentLaneSide.blocks_move
  monkeypatch.setattr(adjacent_lane.AdjacentLaneSide, "blocks_move",
                      lambda self, *a, **k: True if on() else real(self, *a, **k))


def test_one_frame_of_adjacent_slow_does_not_drop_a_standing_signal(monkeypatch):
  flag = {"on": False}
  _adjacent_slow(monkeypatch, lambda: flag["on"])
  det = _signaling(PassingAssistDetector())
  assert det.wanted_side == Side.left
  aborts = det.maneuver.aborts

  flag["on"] = True
  det.update(make_sm(), CRUISE_MS, True)
  flag["on"] = False
  assert det.blocked_by_decided == Blocked.adjacentSlow, "the fixture did not reach the gate branch"
  assert det.raw_wanted_side == Side.none
  assert det.wanted_side == Side.left, "a single frame of refusal bypassed WANTED_FALL_S"
  assert det.maneuver.aborts == aborts, "the dry run backed out on one frame"

  det.update(make_sm(), CRUISE_MS, True)
  assert det.maneuver.phase == Phase.signaling and det.maneuver.aborts == aborts


def test_a_sustained_refusal_still_ends_the_signal_after_the_fall_time(monkeypatch):
  """The other half. Riding out a flicker must not become holding a signal against a lane that
  really is full -- that is the situation the adjacent gate exists for."""
  flag = {"on": False}
  _adjacent_slow(monkeypatch, lambda: flag["on"])
  det = _signaling(PassingAssistDetector())
  aborts = det.maneuver.aborts

  flag["on"] = True
  fall = int(WANTED_FALL_S / DT_MDL)
  for i in range(fall):
    det.update(make_sm(), CRUISE_MS, True)
    assert det.wanted_side == Side.left, f"released after {i + 1} frames, before WANTED_FALL_S"
  for _ in range(2):
    det.update(make_sm(), CRUISE_MS, True)
  assert det.wanted_side == Side.none, "a sustained refusal never released the signal"
  assert det.maneuver.aborts == aborts + 1


def test_a_refused_lane_does_not_light_the_signal_before_the_rise_time():
  """The opening direction, which is the one that matters more. Geometry appears with somebody in
  the blind spot: the gate branch runs on the very first frame, and the signal must still wait out
  WANTED_RISE_S like any other new side."""
  det = run(PassingAssistDetector(), STUCK_FRAMES, **NO_LANE)
  assert det.wanted_side == Side.none

  rise = int(WANTED_RISE_S / DT_MDL)
  for i in range(rise):
    det.update(make_sm(left_bs=True), CRUISE_MS, True)
    assert det.blocked_by_decided == Blocked.blindspotOccupied, "fixture left the gate branch"
    assert det.wanted_side == Side.none, f"signal lit after {i + 1} frames, before WANTED_RISE_S"
  for _ in range(2):
    det.update(make_sm(left_bs=True), CRUISE_MS, True)
  assert det.wanted_side == Side.left, "a blind-spot wait must still end up signalling"
