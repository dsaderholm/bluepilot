"""
FusionPilot: passing assist's lane-change request, as desire_helper sees it.

THE LAST PIECE. Everything else exists: the decision, the maneuver sequence, the commanded signal.
What was missing is that `desire_helper` keys the entire lane-change state machine off
`carstate.leftBlinker != carstate.rightBlinker`, decoded from the SCCM's copy of
Steering_Data_FD1 -- and openpilot cannot see its own transmissions, since panda returns them at
`bus | 0x80` and the parser drops them. So a commanded signal lights the lamp and leaves
`carState.leftBlinker` reading the driver's stalk, which is off. The lamp is right and the state
machine simply never starts.

This is that request, arriving from the planner instead of from a stalk that will never move.

WHY IT LIVES HERE rather than in desire_helper. That file is openpilot's, and every line this fork
adds to it is a merge conflict paid on every future update. The logic is ours, so it sits in a
sunnypilot module and desire_helper asks it two questions.

THE ONE THAT WOULD PUT THE CAR ON THE SHOULDER
`DesireHelper.get_lane_change_direction` is

    return LaneChangeDirection.left if CS.leftBlinker else LaneChangeDirection.right

-- RIGHT whenever the left blinker is not set. Feed it a left request without touching that and it
arms preLaneChange for the left, then reads the direction as RIGHT and changes into whatever is
there, which on the outside lane is the shoulder. Stated plainly: *"I don't want to end up on the
shoulder."* So the direction has to honour the request wherever it is derived, not just the arming.

WHAT THIS DELIBERATELY DOES NOT DO

  * It does not time anything. The blinker lead is the maneuver's, the nudgeless wait is
    AutoLaneChangeController's, and they are both one second by the owner's choice -- see the
    2026-08-09 decision, "for automatic lane changes, I want it to be the same." Adding a third
    clock here would be the two-timer problem again with an extra participant.
  * It does not touch the blind-spot or torque checks in preLaneChange. Those still apply exactly
    as they do to a stalk change, which is the point: the request is a stalk press, not a bypass.
  * It does not survive the maneuver ending. `blinkerWouldBeOn` is signaling and changing ONLY --
    not aborting, not finishing -- which closes the trap desire_helper's own note warns about, where
    a signal still flashing at the end of a revert re-arms preLaneChange and launches a second lane
    change the same way. The request is already gone by then.
"""

NONE, LEFT, RIGHT = 0, 1, 2


def request_side(longitudinal_plan_sp) -> int:
  """Which side passing assist is asking for, or NONE.

  THREE FIELDS, AND `actuating` CARRIES THE WEIGHT. blinkerWouldBeOn and maneuverSide have been
  published on every drive since the feature was written -- they are the dry run's whole output --
  so reading them alone would have driven lane changes on his commute for weeks. `actuating` is
  false whenever the rear sensor on the side being entered is unavailable, which today is always.

  Broad except on purpose: modeld must not be stoppable by a planner that is absent or malformed,
  and this runs in modeld's hot loop. Same shape as hud_ext.py and passing_assist_blinker.py, which
  read this message from the car side for the same reason.
  """
  try:
    pa = longitudinal_plan_sp.passingAssist
    # desireOk, NOT blinkerWouldBeOn. The lamp may be lit while the gates are still deciding --
    # that is what signal-first means -- but the DESIRE may not, because desire_helper advances on
    # its own timer without consulting them. See PassingManeuver.desire_ok.
    if not (bool(pa.actuating) and bool(pa.desireOk)):
      return NONE
    # .raw, NOT int(): a capnp enum read off a live message is a _DynamicEnum and int() raises
    # TypeError on the device. getattr keeps the plain ints test fixtures build working.
    side = getattr(pa.maneuverSide, 'raw', pa.maneuverSide)
    return side if side in (LEFT, RIGHT) else NONE
  except Exception:  # noqa: BLE001 - no planner is not a reason to break the model loop
    return NONE
