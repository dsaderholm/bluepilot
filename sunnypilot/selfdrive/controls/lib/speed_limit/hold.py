"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

# FusionPilot: THE HOLD. The driver's own number, and the policy for what it does to a plan.
#
# He has asked for this twice: *"holds shouldn't even be a part of ICBM, they are a part of SLA"*
# (2026-08-18) and *"The next thing we definitely need to do across the board on all branches is
# move holds and pinned holds to SLA. It is not just for ICBM! I wish you had never done that."*
# (2026-08-26).
#
# WHY IT IS NOT MERELY NAMING. A hold is "against THIS posted limit I want a different number".
# That is a statement about speed POLICY and says nothing about how the speed is achieved, so it is
# actuator-independent by construction and has to outlive ICBM. Filed under the button layer it is
# deleted with the scaffolding, or kept for the wrong reason -- and under op long the button layer
# goes away and holds go with it, which is the bug this removes.
#
# WHY IT IS A MODULE AND NOT A MOVE INTO SpeedLimitAssist. ICBM is constructed in **selfdrived**
# and SpeedLimitAssist in **plannerd**. Putting the hold inside SLA would place a message round
# trip between the driver's press and the hold taking effect -- in exactly the press-settle timing
# where this file's history records three separate failed attempts. So the hold lives in its own
# unit under speed_limit/, owned by neither process: ICBM drives it today, and the planner can
# drive the same object the day ICBM is gone. Nothing crosses a process boundary and no timing
# relationship moves.
#
# WHAT IS DELIBERATELY NOT HERE YET: the persistent param key `IcbmBaselineResetDelta` and the
# capnp fields `vBaseline` and `baselineSource`. Renaming a PERSISTENT key discards his stored
# value, and the capnp fields have WIRE HISTORY in every recorded route -- renumbering makes every
# drive on disk decode as garbage. Those are a separate change, through the
# `_BP_LATERAL_SCHEME_PARAM_RENAMES` machinery that exists for exactly this, and renaming the field
# while keeping the ordinal.
#
# PINNED HOLDS WENT WITH THIS MIGRATION RATHER THAN THROUGH IT, 2026-09-25. He had asked three
# times not to have them -- *"I doubt I am going to use pinned holds at all"*, *"I just want to be
# able to override the speed when I want and it to not be remembered"*, *"Remember, I don't like
# the concept of pinned holds"* -- and `IcbmPinnedHolds` had read `[]` since 2026-08-11, so not one
# had ever been created on the car. Deleting was cheaper than migrating and is what he asked for.
# `pinSuggestion @7` and `BaselineSource.pinned @4` are retired in place; the ordinals cannot be
# reused.

from cereal import custom

LongitudinalPlanSource = custom.LongitudinalPlanSP.LongitudinalPlanSource
OverrideState = custom.IntelligentCruiseButtonManagement.OverrideState
BaselineSource = custom.IntelligentCruiseButtonManagement.BaselineSource

# The plan components a hold REPLACES outright rather than merely capping. These are the ones that
# represent "what speed should this road be driven at", which is precisely the judgement the driver
# overrode. A curve, map point or lead target is a physics limit and is never raised by a hold.
HELD_SOURCES = (LongitudinalPlanSource.cruise, LongitudinalPlanSource.speedLimitAssist)


class SpeedHold:
  """The driver's chosen speed, and what it does to a planned target.

  Five pieces of state that have always moved together and were set by hand at four different
  sites, each writing a slightly different subset. That is the same shape as the
  `cluster_moved_since_press` pair bug -- a latch and its anchor reset in one place and not
  another -- so `capture` and `clear` are the only ways to move them.
  """

  def __init__(self):
    self.value = 0                 # the driver's speed, cluster units. 0 = no hold, follow the plan
    self.source = BaselineSource.none
    self.override_state = OverrideState.auto
    self.target_at_capture = 0     # the plan's own number when the hold was taken
    self.diverged = False          # has this hold ever actually differed from the limit?

  @property
  def active(self) -> bool:
    return self.value > 0

  @property
  def manual(self) -> bool:
    return self.override_state == OverrideState.manual

  def aim(self, v_target: int, plan_source) -> int:
    """Substitute the driver's speed for the component they overrode.

    With no hold this is the identity. With one:

      cruise / speed limit  -> the hold outright. Above or below the posted limit; the hold wins
                               either way and SLA does not pull them back.
      curve / map / lead    -> min(planned, hold). A physics limit is honored as-is and the hold
                               only ever caps it, which is what keeps curve slowing working while
                               the driver is overridden.

    A VALUE, not a mode -- which is why everything downstream (the state machine, the rate
    limiters, the hazard path) needs no knowledge of holds at all.
    """
    if self.value <= 0:
      return v_target
    if plan_source in HELD_SOURCES:
      return self.value
    return min(v_target, self.value)

  def capture(self, value: int, source, target_raw: int) -> None:
    """Take a hold. The ONLY way the five fields move together.

    The plan's number is anchored on the FIRST capture only: the reset rule measures how far the
    posted limit has moved since the driver made their statement, so re-anchoring on every
    capture would make that rule unreachable wherever the limit drifts.
    """
    if not self.manual:
      self.target_at_capture = target_raw
      self.diverged = False
    self.override_state = OverrideState.manual
    self.value = value
    self.source = source

  def clear(self) -> None:
    self.override_state = OverrideState.auto
    self.value = 0
    self.target_at_capture = 0
    self.diverged = False

  def limit_moved_away(self, v_target_raw: int, reset_delta: int) -> bool:
    """The posted limit has moved far enough that this hold is about a road already passed."""
    return abs(v_target_raw - self.target_at_capture) >= reset_delta
