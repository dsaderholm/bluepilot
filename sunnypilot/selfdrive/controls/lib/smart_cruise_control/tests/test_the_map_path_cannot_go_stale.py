"""FusionPilot: SCC-Map must never walk a map path it cannot date.

His report, 2026-09-07: *"On the way back on I-215 the speed kept lowering every time on cruise so
I couldn't use it."* SCC-Map published a CONSTANT 17.2 mph -- a 27 m radius -- in thirteen windows
at 70+ mph with peak lateral acceleration 0.00.

WHAT THE DRIVE PROVES, and it is arithmetic rather than judgement. Route 00000430 carried exactly
two vTarget values across 1,900 seconds -- 255.0 (unset) and 7.690868377685547 -- and exactly two
finite targetDistances, 645.3853759765625 and 602.384033203125, unchanged over seven miles and two
freeways. Replaying the SHIPPED controller over the recorded `mapdExtendedOut` with full state
history never reproduces them: the live path gives 223.7 mph with no corner selected. FREEZING the
path at the message received at t+1290.9 reproduces all three floats exactly, and no other freeze
point does.

So the cache in `SmartCruiseControl.update` served a seven-minute-old path, and its only
invalidation was somebody else's liveness rule. These tests are about the cache having an age of
its own -- they drive the REAL `SmartCruiseControl.update`, because a test that called the guard
directly would pass against a build that never reaches it.
"""
from __future__ import annotations

import pytest

from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot.selfdrive.controls.lib import smart_cruise_control as scc_mod
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.smart_cruise_control import (
  MAPD_V2_PATH_MAX_AGE_FRAMES,
  MAPD_V2_PATH_MAX_AGE_S,
  SmartCruiseControl,
)

# The real thing is (Coordinate, list[dict]); nothing under test looks inside it.
A_PATH = ("position", [{"latitude": 40.6, "longitude": -111.8, "velocity": 7.69}])
ANOTHER_PATH = ("elsewhere", [{"latitude": 40.7, "longitude": -111.9, "velocity": 30.0}])


class _Recorder:
  """Stands in for the two controllers and remembers the path it was handed."""
  max_pred_lat_acc = 0.0

  def __init__(self):
    self.seen: list = []

  def update(self, *a, **k):
    self.seen.append(k.get("mapd_v2_path", "not passed"))


class _SM:
  """What plannerd's SubMaster looks like ON A PLANNER FRAME.

  `updated` defaults to FALSE even when a message has just arrived, because that is what plannerd
  shows the planner roughly 88% of the time: the flag was set and cleared on a 100 Hz carState poll
  the planner did not run on. `logMonoTime` is the receipt stamp that survives that.
  """
  def __init__(self, mono, alive=True, valid=True, updated=False):
    self.alive = {"mapdExtendedOut": alive}
    self.valid = {"mapdExtendedOut": valid}
    self.updated = {"mapdExtendedOut": updated}
    self.logMonoTime = {"mapdExtendedOut": mono}

  def __getitem__(self, k):
    return None


@pytest.fixture
def scc(monkeypatch):
  """A real SmartCruiseControl with the two controllers replaced and v2 selected.

  `__new__` skips `__init__` because it needs real Params, so every field the cache path reads is
  seeded by hand -- including `_frames_since_path`, which `__init__` seeds HIGH so a path that
  never arrives is stale rather than fresh.
  """
  s = SmartCruiseControl.__new__(SmartCruiseControl)
  s.vision = _Recorder()
  s.map = _Recorder()
  s.use_mapd_v2 = True
  s._mapd_v2_path = None
  s._frames_since_path = 1 << 30
  s._mapd_v2_path_mono = 0
  s._test_mono = 0   # the fake publisher's clock, not a controller field
  # The build is under test, not the translation. Returning a fixed object keeps a rebuild
  # observable as "the SAME path came back", which is what the age question is about.
  monkeypatch.setattr(scc_mod.smart_cruise_control, "path_from_mapd", lambda sm: A_PATH)
  return s


def _run(s, frames: int, updated: bool) -> None:
  """`updated=True` means a NEW message arrived before these frames -- told to the controller the
  way plannerd actually tells it, by a new logMonoTime, with the `updated` flag already cleared."""
  if updated:
    s._test_mono += 1_000_000_000
  sm = _SM(s._test_mono)
  for _ in range(frames):
    s.update(sm, True, False, 30.0, 0.0, 30.0)


def _handed(s):
  return s.map.seen[-1]


def test_a_path_that_just_arrived_is_used(scc):
  _run(scc, 1, updated=True)
  assert _handed(scc) == A_PATH


def test_it_survives_a_missed_message(scc):
  """mapdExtendedOut is a 1 Hz service. One late frame is not a reason to stop slowing for a bend."""
  _run(scc, 1, updated=True)
  _run(scc, MAPD_V2_PATH_MAX_AGE_FRAMES - 2, updated=False)
  assert _handed(scc) == A_PATH


def test_a_PATH_OLDER_THAN_THE_MAX_AGE_IS_THROWN_AWAY(scc):
  """The I-215 defect. Seven minutes of walking a road he had already left."""
  _run(scc, 1, updated=True)
  _run(scc, MAPD_V2_PATH_MAX_AGE_FRAMES, updated=False)
  assert _handed(scc) is None


def test_it_stays_dropped_rather_than_reviving(scc):
  _run(scc, 1, updated=True)
  _run(scc, MAPD_V2_PATH_MAX_AGE_FRAMES * 3, updated=False)
  assert _handed(scc) is None


def test_a_REBUILD_MAKES_IT_FRESH_AGAIN(scc):
  """The guard is an age, not a mute. v2 coming back must be used immediately."""
  _run(scc, 1, updated=True)
  _run(scc, MAPD_V2_PATH_MAX_AGE_FRAMES * 2, updated=False)
  assert _handed(scc) is None
  _run(scc, 1, updated=True)
  assert _handed(scc) == A_PATH


def test_a_path_built_THIS_FRAME_is_never_dropped(scc):
  """After any outage, however long, the first new message is used at once.

  MUTATION TESTING CORRECTED THIS TEST'S OWN CLAIM. It said the guard must sit AFTER the rebuild or
  a returning path would be dropped on arrival -- and moving it above the rebuild kills no test,
  because the rebuild then simply puts the path back. That mutant is EQUIVALENT in behaviour and
  costs only a misleading log line. What is NOT equivalent is moving the guard BELOW
  `mapd_v2_path = self._mapd_v2_path`, which hands the stale path on for one more frame; the
  expiry test catches that one.
  """
  _run(scc, 1, updated=True)
  _run(scc, MAPD_V2_PATH_MAX_AGE_FRAMES * 5, updated=False)
  _run(scc, 1, updated=True)
  assert _handed(scc) == A_PATH


def test_the_FIRST_frames_of_a_drive_hand_nothing(scc):
  """Before any message there is no path, and no age can make one appear."""
  _run(scc, 3, updated=False)
  assert _handed(scc) is None


def test_a_dead_service_still_clears_the_cache(scc):
  """The original alive/valid invalidation is not replaced by the age -- both are wanted."""
  _run(scc, 1, updated=True)
  sm = _SM(scc._test_mono, alive=False)
  scc.update(sm, True, False, 30.0, 0.0, 30.0)
  assert _handed(scc) is None


def test_the_max_age_is_FIVE_TIMES_THE_WORST_OBSERVED_GAP_not_a_number_anyone_picked():
  """1889 messages on route 00000430: p50 1.00 s, p90 1.00 s, max 1.02 s inter-arrival.

  Pinned so nobody tightens it toward the service period and starts dropping live paths on a
  hiccup, and so nobody loosens it back toward the SubMaster's own 10 s -- which is the rule that
  failed to catch this in the first place.
  """
  assert MAPD_V2_PATH_MAX_AGE_S == 5.0
  assert MAPD_V2_PATH_MAX_AGE_FRAMES == int(5.0 / DT_MDL)


def test_THE_I215_RACE_a_new_message_whose_updated_flag_the_planner_never_saw_is_still_used(scc):
  """The root cause, 2026-09-12. plannerd polls carState at 100 Hz and runs the planner only on a
  modelV2 frame, and `SubMaster.update()` clears every `updated` flag on every poll. On route
  00000430 only 12.3% of mapdExtendedOut landed in a modelV2 poll, and for 597 s straight none did --
  so a controller keyed on `updated` walked the path from t+1290.9 for ten minutes.

  Here the flag is False on every frame the controller runs, exactly as on the car, and the second
  message must still replace the first.
  """
  scc_mod.smart_cruise_control.path_from_mapd, first = (lambda sm: ANOTHER_PATH), None
  _run(scc, 1, updated=True)
  first = _handed(scc)
  scc_mod.smart_cruise_control.path_from_mapd = lambda sm: A_PATH
  _run(scc, 1, updated=True)
  assert first == ANOTHER_PATH
  assert _handed(scc) == A_PATH, "a new message was ignored because its `updated` flag was cleared"


def test_a_stale_updated_flag_with_NO_new_message_does_not_rebuild(scc):
  """The other half: the flag is not the signal in either direction. Same logMonoTime, flag True,
  and the path must not be rebuilt -- it would be rebuilt from the same message, which is the 17.6 ms
  per-frame cost the cache exists to avoid."""
  calls = []
  scc_mod.smart_cruise_control.path_from_mapd = lambda sm: (calls.append(1), A_PATH)[1]
  _run(scc, 1, updated=True)
  sm = _SM(scc._test_mono, updated=True)
  for _ in range(10):
    scc.update(sm, True, False, 30.0, 0.0, 30.0)
  assert len(calls) == 1


def test_the_counter_EXISTS_BEFORE_ANY_FRAME_RUNS():
  """The 2026-08-15 undrivable-car shape: an attribute that only exists once a method has run.

  The fixture hand-seeds it because `__init__` needs real Params, so nothing else in this file can
  see the seed being deleted -- and without it the first `+= 1` is an AttributeError inside
  plannerd, which is a dead planner rather than a missed corner.
  """
  import inspect

  from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control import smart_cruise_control as m
  body = inspect.getsource(m.SmartCruiseControl).split("def __init__", 1)[1].split("def ", 1)[0]
  assert "self._frames_since_path" in body, "_frames_since_path is not initialised in __init__"
  assert "self._mapd_v2_path_mono" in body, "_mapd_v2_path_mono is not initialised in __init__"


def test_v2_being_switched_off_still_hands_nothing(scc):
  """State 0 and state 1 read v1, and the age must not resurrect a v2 path there."""
  scc.use_mapd_v2 = False
  scc._mapd_v2_path = ANOTHER_PATH
  _run(scc, 1, updated=True)
  assert _handed(scc) is None
