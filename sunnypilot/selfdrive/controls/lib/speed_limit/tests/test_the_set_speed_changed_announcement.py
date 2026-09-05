"""FusionPilot: "Set speed changed" must only be said when the set speed is going to change.

His report, 2026-09-05: *"It's still telling me set speed changed to the speed limit all the time
now, even when the set speed didn't change at all"* and *"I think we were on SLA and not a hold and
it just kept telling me it changed even though it didn't."*

**THIS IS A DIFFERENT ALERT FROM THE ONE `test_the_announcement_defers_to_a_hold.py` COVERS, WHICH
IS WHY HE SAID "STILL".** That one is `speedLimitAutoSet`. This one is `speedLimitChanged` /
`speedLimitActive`, fired from `update_active_event` on the ENTRY EDGE into an active state -- a
trigger that never consulted the set speed at all. Measured on route 00000427: 12 announcements in
13 minutes, SIX with the dash not moving, three of them inside 1.5 s off one flicker.

Both guards are driven through the REAL `update_events`, because the entry-edge condition lives
there and a test that called `update_active_event` directly would pass against a build that never
reaches it.
"""
from __future__ import annotations

from cereal import custom
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.speed_limit_assist import (
  ANNOUNCE_COOLDOWN_FRAMES,
  SpeedLimitAssist,
)
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP

EventNameSP = custom.OnroadEventSP.EventName
State = custom.LongitudinalPlanSP.SpeedLimit.AssistState

ANNOUNCEMENTS = (EventNameSP.speedLimitChanged, EventNameSP.speedLimitActive)


def _sla(dash_conv: int = 22, target_conv: int = 35) -> SpeedLimitAssist:
  """An SLA parked one frame BEFORE an entry edge into active.

  `__new__` skips `__init__`, so every field the path reads is seeded by hand -- including
  `_frames_since_announce`, which `__init__` seeds high so the first announcement is never gagged.

  The `speedLimitAutoSet` block above the entry edge is deliberately silenced by holding the two
  limit-conv fields EQUAL: this file is about the other announcement, and letting both fire would
  make a failure ambiguous about which one moved.
  """
  s = SpeedLimitAssist.__new__(SpeedLimitAssist)
  s.auto_follow = True
  s.is_metric = False
  s.is_active = True
  s.state = State.active
  s._state_prev = State.inactive          # the entry edge: not in ACTIVE_STATES
  s.speed_limit_final_last_conv = 35
  s.prev_speed_limit_final_last_conv = 35  # equal -> speedLimitAutoSet cannot fire
  s._speed_limit = 20.0
  s.speed_limit_prev = 20.0
  s.v_cruise_cluster_conv = dash_conv
  s.target_set_speed_conv = target_conv
  s._frames_since_announce = 1 << 30
  return s


def _announced(sla: SpeedLimitAssist) -> bool:
  """One frame at the entry edge. Returns whether SLA said anything about taking the set speed."""
  events = EventsSP()
  sla.update_events(events)
  return any(name in events.names for name in ANNOUNCEMENTS)


def _idle(sla: SpeedLimitAssist, frames: int) -> None:
  """Run `frames` frames with no entry edge, which is what lets the cooldown expire."""
  was_active, sla.is_active = sla.is_active, False
  for _ in range(frames):
    sla.update_events(EventsSP())
  sla.is_active = was_active


def _reenter(sla: SpeedLimitAssist) -> bool:
  """Re-arm the entry edge and take another frame -- route 00000427's active/inactive flicker."""
  sla._state_prev = State.inactive
  return _announced(sla)


def test_an_entry_that_will_move_the_set_speed_still_announces():
  """The control case. Dash at 22, target 35 -- the set speed really is about to change."""
  assert _announced(_sla(dash_conv=22, target_conv=35))


def test_an_entry_where_the_set_speed_ALREADY_EQUALS_the_target_says_nothing():
  """His report. Re-engaging cruise on a road the dash already matches is not a change.

  Four of route 00000427's twelve were exactly this -- dash pinned at 22 with the limit at 22.
  """
  assert not _announced(_sla(dash_conv=22, target_conv=22))


def test_a_second_entry_inside_the_alert_s_own_duration_is_silent():
  """The first announcement is still ON SCREEN, so a second one can only be redundant."""
  sla = _sla()
  assert _announced(sla)
  _idle(sla, ANNOUNCE_COOLDOWN_FRAMES - 2)
  assert not _reenter(sla)


def test_the_1_5_SECOND_FLICKER_ON_ROUTE_00000427_PRODUCES_ONE_CHIME():
  """active -> inactive -> active three times in 1.5 s fired three chimes for one real change."""
  sla = _sla()
  chimes = sum(_reenter(sla) for _ in range(3))
  assert chimes == 1


def test_it_speaks_again_once_the_cooldown_HAS_EXPIRED():
  """The guard is a cooldown, not a mute. A genuinely later entry must still be announced."""
  sla = _sla()
  assert _announced(sla)
  _idle(sla, ANNOUNCE_COOLDOWN_FRAMES + 1)
  assert _reenter(sla)


def test_the_FIRST_announcement_of_a_drive_is_never_gagged():
  """`__init__` seeds the counter high for this. A drive that opens inside the window is wrong."""
  sla = _sla()
  sla._frames_since_announce = 1 << 30
  assert _announced(sla)


def test_the_confirmed_guard_does_not_spend_the_cooldown():
  """A suppressed announcement must not reset the timer, or one no-op entry mutes a real one.

  This is the ordering that makes the two guards independent: `target_set_speed_confirmed`
  returns BEFORE the counter is zeroed.
  """
  sla = _sla(dash_conv=22, target_conv=22)
  assert not _announced(sla)
  sla.target_set_speed_conv = 35   # now it really would move
  assert _reenter(sla)


def test_the_wording_still_follows_the_confirm_threshold():
  """Below 50 mph is "Set speed changed"; above it is "Auto adjusting to speed limit"."""
  low, high = EventsSP(), EventsSP()
  _sla(dash_conv=22, target_conv=35).update_events(low)
  _sla(dash_conv=70, target_conv=75).update_events(high)
  assert EventNameSP.speedLimitChanged in low.names
  assert EventNameSP.speedLimitActive in high.names


def test_the_cooldown_IS_the_alert_s_own_duration_and_is_not_a_number_anyone_picked():
  """The one thing that justifies this guard: a re-announce inside the window is still on screen.

  Read off the SHIPPED alert rather than hardcoded, so nobody can retune the alert and silently
  leave the cooldown describing a duration the alert no longer has. Mutation testing is why this
  exists -- moving `ANNOUNCE_COOLDOWN_S` 5.0 -> 0.5 left every other test in this file green.
  """
  from openpilot.common.realtime import DT_CTRL
  from openpilot.sunnypilot.selfdrive.selfdrived.events import ET, EVENTS_SP

  from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.speed_limit_assist import ANNOUNCE_COOLDOWN_S

  # `Alert.duration` is stored as `int(duration / DT_CTRL)` -- CONTROL frames, while the cooldown
  # counts MODEL frames. Two fields both called a duration, on different clocks; comparing them
  # raw reads 500 == 5.0 and would have been the units half of this fork's "compare endpoints"
  # rule. Convert before comparing.
  for event in ANNOUNCEMENTS:
    assert EVENTS_SP[event][ET.WARNING].duration * DT_CTRL == ANNOUNCE_COOLDOWN_S
