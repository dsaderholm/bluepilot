"""FusionPilot: do not announce a speed-limit change a HOLD is going to outrank.

His report, 2026-08-27, after the SLC -> Yosemite trip:

  *"I also noticed it said setting speed to speed limit when it was actually setting it to a hold
  or seemingly setting it to the hold even though it was already set to a hold and the speed limit
  must've updated in OSM or something."*

`speed_limit_auto_set_alert` renders `f"{direction} set speed to {target} {unit} speed limit"` where
`target` is SLA's own number. Under ICBM the car is driven to `apply_baseline(...)` -- the driver's
hold -- so with a hold in force the alert names a speed the set speed will never reach, and it fires
again on every map limit change while nothing on the car moves.

The value was right and the SENTENCE was wrong, which is the inverse of this fork's usual
"computed correctly and never rendered" bug and just as invisible from a green suite.

THE GATE IS ON THE HOLD DIFFERING FROM THE NEW LIMIT, not on a hold existing. A hold that EQUALS the
limit is about to be cleared by the hold-clearing rule, the set speed genuinely does end up at the
limit, and that announcement is true -- so it is kept.
"""
from __future__ import annotations

from cereal import custom
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.speed_limit_assist import SpeedLimitAssist
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP

EventNameSP = custom.OnroadEventSP.EventName
State = custom.LongitudinalPlanSP.SpeedLimit.AssistState


def _sla(limit_conv: int, prev_conv: int) -> SpeedLimitAssist:
  """An SLA that is active, auto-following, and has just seen the posted limit change."""
  s = SpeedLimitAssist.__new__(SpeedLimitAssist)
  s.auto_follow = True
  s.is_active = True
  s.speed_limit_final_last_conv = limit_conv
  s.prev_speed_limit_final_last_conv = prev_conv
  # The rest of update_events fires its own unrelated events. Park it in a steady ACTIVE state so
  # only the announcement under test can change -- `_state_prev` in ACTIVE_STATES and an unchanged
  # speed limit together mean update_active_event is never reached.
  s.state = State.active
  s._state_prev = State.active
  s._speed_limit = 20.0
  s.speed_limit_prev = 20.0
  # __new__ skips __init__, so per-drive state has to be seeded by hand. Seeded HIGH for the same
  # reason __init__ does: the re-announce cooldown must never gag the first announcement.
  s._frames_since_announce = 1 << 30
  s._frames_since_auto_set = 1 << 30
  return s


def _fired(sla: SpeedLimitAssist, v_baseline_conv: float) -> bool:
  events = EventsSP()
  sla.update_events(events, v_baseline_conv)
  return EventNameSP.speedLimitAutoSet in events.names


def test_no_hold_still_announces():
  """The ordinary case is untouched: no hold, the set speed really does go to the limit."""
  assert _fired(_sla(40, 35), 0.0)


def test_a_hold_that_differs_suppresses_the_announcement():
  """His report. A 55 hold against a limit moving 35 -> 40: the car stays at 55, so saying
  'Raising set speed to 40 mph speed limit' describes something that does not happen."""
  assert not _fired(_sla(40, 35), 55.0)


def test_a_hold_below_the_limit_also_suppresses_it():
  """Direction is irrelevant -- the hold outranks the limit either way."""
  assert not _fired(_sla(65, 55), 45.0)


def test_a_hold_equal_to_the_new_limit_still_announces():
  """The carve-out. This hold is about to be cleared by the hold-clearing rule, so the set speed
  DOES end up at the limit and the sentence is true. Suppressing here would lose a real
  announcement -- the exact over-correction this gate is written narrowly to avoid."""
  assert _fired(_sla(40, 35), 40.0)


def test_rounding_does_not_reopen_it():
  """`v_baseline` is a cluster integer and `speed_limit_final_last_conv` is rounded from m/s, so
  the comparison is on rounded values. 39.6 IS the 40 hold, not a different number."""
  assert _fired(_sla(40, 35), 39.6)


def test_an_unchanged_limit_never_announces_whatever_the_hold_is():
  """The pre-existing trigger is untouched: no limit change, no event."""
  assert not _fired(_sla(40, 40), 0.0)
  assert not _fired(_sla(40, 40), 55.0)


def test_the_default_argument_preserves_the_old_behavior():
  """On any caller that does not pass the baseline -- a build where selfdriveStateSP is not up --
  the alert must behave exactly as it did before, rather than going silent."""
  events = EventsSP()
  _sla(40, 35).update_events(events)
  assert EventNameSP.speedLimitAutoSet in events.names
