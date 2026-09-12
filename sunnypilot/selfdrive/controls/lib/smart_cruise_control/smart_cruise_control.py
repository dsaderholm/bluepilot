"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.mapd import MAPD_V2_ON
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.mapd_v2_path import path_from_mapd
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.vision_controller import SmartCruiseControlVision
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.map_controller import SmartCruiseControlMap


# FusionPilot 2026-09-07: HOW OLD A CACHED MAP PATH MAY BE BEFORE IT IS THROWN AWAY.
#
# He reported "the speed kept lowering every time on cruise so I couldn't use it" on I-215.
# SCC-Map published a CONSTANT 17.2 mph -- a 27 m radius -- in thirteen windows at 70+ mph, with
# peak lateral acceleration 0.00. Across that whole 1,900 s drive the plan carried exactly TWO
# vTarget values (255.0 unset, and 7.690868377685547) and exactly TWO finite targetDistances
# (645.3853759765625 and 602.384033203125), unchanged over seven miles and two freeways. A live
# distance to a corner cannot do that.
#
# Replaying the shipped controller over the recorded messages settles what it was. With the live
# path, from full state history, it never produces 17.2 -- it produces 223.7 with no corner
# selected. FREEZING the path at the message received at t+1290.9 reproduces all three floats
# exactly, and no other freeze point does. So the walk spent the last SEVEN MINUTES of that drive
# on a path from a road it had already left. The 7.69 m/s corner is real map geometry at
# 40.637489, -111.808718 -- it was simply nowhere near him any more.
#
# WHY IT WENT STALE WAS FOUND 2026-09-12, AND IT WAS NOT MAPD: see `_new_mapd_message` below. The
# rebuild was keyed on `sm.updated`, and in plannerd that flag almost never reaches this code.
#
# THE AGE STAYS ANYWAY, as the backstop it was built to be. A cache invalidated only by somebody else's liveness
# rule inherits that rule's blind spots; one that knows how old it is does not care what caused the
# gap. 5.0 s is FIVE TIMES the worst inter-arrival ever observed on this service (p50 1.00 s,
# p90 1.00 s, max 1.02 s across 1889 messages on this drive), so it cannot fire on a hiccup, and it
# is two orders of magnitude short of the 400 s that happened.
#
# ONE-DIRECTIONAL: dropping the cache falls back to v1, which at MapdV2 = 2 does not run at all, so
# the worst this can do is leave SCC-Map idle. Not slowing for a mapped corner is the cost; acting
# on a corner seven minutes behind you is what it replaces.
MAPD_V2_PATH_MAX_AGE_S = 5.0
MAPD_V2_PATH_MAX_AGE_FRAMES = int(MAPD_V2_PATH_MAX_AGE_S / DT_MDL)


def _new_mapd_message(sm, last_mono: int) -> bool:
  """FusionPilot 2026-09-12: has a NEW mapdExtendedOut arrived since the path was last built?

  NOT `sm.updated`, and this is the whole I-215 phantom. plannerd is

      sm = SubMaster([...], poll='carState')
      while True:
        sm.update()                     # every carState -- 100 Hz -- and it resets EVERY updated flag
        if sm.updated['modelV2']:       # 20 Hz
          longitudinal_planner.update(sm)

  so a 1 Hz message's `updated` flag is visible here only if it was received in the SAME 10 ms poll
  cycle as a modelV2. Everywhere else it was set and cleared on a poll the planner never ran on. And
  `alive` stays True the whole time, because the messages ARE being received -- which is exactly the
  "stale while alive" that looked impossible.

  PREDICTED FROM TIMESTAMPS ALONE ON ROUTE 00000430, and it matches the drive to the message: only
  233 of 1,891 mapdExtendedOut (12.3%) landed in a modelV2 cycle, and between t+1172.9 and t+1888.9
  exactly ONE did -- t+1290.9, the very message a separate replay had already pinned as the frozen
  path. mapd publishes on a steady 1.00 Hz clock and modelV2 on the camera's 20 Hz one, so the phase
  between them drifts slowly: minutes of normal refresh, then minutes of none.

  The per-service `logMonoTime` is set on RECEIPT, in whichever poll cycle that happens, and it
  survives until the next message. So a changed value means a new message whatever cycle it landed
  in. Both start at 0, so nothing is built before the first message -- and this branch is behind
  `alive` anyway. (An explicit `mono != 0` clause was tried and mutation testing showed it could
  never change the answer, so it is not here.)
  """
  return sm.logMonoTime['mapdExtendedOut'] != last_mono


class SmartCruiseControl:
  def __init__(self):
    self.vision = SmartCruiseControlVision()
    self.map = SmartCruiseControlMap()
    # FusionPilot: read ONCE, as mapd_manager does and for the same reason -- swapping the curve
    # source mid-drive would hand SCC-Map a different path with no transition. A reboot follows the
    # setting anyway. Note this is state 2 only: in observe mode v2 is logged and nothing reads it.
    self.use_mapd_v2 = Params().get("MapdV2", return_default=True) == MAPD_V2_ON
    # See the cache in update(). None means "v2 has nothing to say", which is also its value
    # before the first mapdExtendedOut arrives.
    self._mapd_v2_path: tuple | None = None
    # Frames since the cache was last REBUILT, not since a message arrived. Seeded high so a path
    # that never arrives is stale rather than fresh -- the same reason the announcement cooldowns
    # seed high, and the opposite of the bug this guard exists for.
    self._frames_since_path: int = 1 << 30
    # logMonoTime of the message the cached path was built from. 0 = never built, which matches
    # SubMaster's own value for a service that has not been received.
    self._mapd_v2_path_mono: int = 0

  def update(self, sm: messaging.SubMaster, long_enabled: bool, long_override: bool, v_ego: float, a_ego: float, v_cruise: float) -> None:
    # BluePilot: vision FIRST. The map controller cross-checks its own curve against what the
    # camera can see, and running it second would hand it last frame's model data. Vision does not
    # read anything the map controller produces, so the swap costs nothing.
    self.vision.update(sm, long_enabled, long_override, v_ego, a_ego, v_cruise)
    # FusionPilot: None means "read v1", which is also what happens if v2 is selected but silent.
    # Falling back is right HERE and wrong in the SLA reader, and the asymmetry is deliberate: there,
    # a quiet fallback would hide a broken install behind plausible speed limits, while here v1 is
    # still the shipped curve source and the failure being avoided is not slowing for a corner.
    # REBUILT ONLY WHEN THE MESSAGE CHANGES, and this is a fix for a disengagement, not a tidy-up.
    #
    # `mapdExtendedOut` publishes at ~1 Hz and this runs at 20, so the old unconditional call redid
    # identical work nineteen times per message. Measured against real path sizes: 0.001 ms on a
    # straight road, 7.6 ms at 285 points, and **17.6 ms at the 652 points a mountain road
    # produces** -- against a 50 ms frame budget, on a desktop CPU faster than the device's.
    #
    # WHAT THAT COST HIM. plannerd polls `carState` at 100 Hz, so it must service that socket every
    # 10 ms. Stalling tens of ms inside this call starves it, `carState` falls under the 80 Hz floor
    # of its frequency band AS PLANNERD SEES IT, and every all_checks in this process fails --
    # which invalidates longitudinalPlan, longitudinalPlanSP and driverAssistance together, because
    # `carState` is the one service in all three of their check lists. selfdrived reads those as
    # invalid and raises commIssue, which is ET.SOFT_DISABLE. Measured on his device: 96 of 103
    # events had all three invalid at once, with alive and freq passing on everything else.
    #
    # SAFE BECAUSE IT IS NOT AN APPROXIMATION. Between updates `sm['mapdExtendedOut']` returns the
    # same message, so the old code recomputed a pure function of unchanged input -- the cached
    # value is identical, not a stale stand-in. `Coordinate` copies floats and the targets are
    # dicts of floats, which is what this file already builds them as, for exactly this reason.
    #
    # The alive/valid clear is NOT optional: without it a v2 that dies leaves no new message forever
    # and the last path would be served indefinitely, which is the one way caching could turn a
    # fallback into a stale answer.
    #
    # AND "WHEN THE MESSAGE CHANGES" MUST NOT BE READ FROM `sm.updated` -- that is the bug this
    # caching introduced on 2026-08-28 and that the I-215 drive paid for. See `_new_mapd_message`.
    if not self.use_mapd_v2:
      mapd_v2_path = None
    else:
      self._frames_since_path += 1
      if not (sm.alive['mapdExtendedOut'] and sm.valid['mapdExtendedOut']):
        self._mapd_v2_path = None
      elif _new_mapd_message(sm, self._mapd_v2_path_mono):
        self._mapd_v2_path_mono = sm.logMonoTime['mapdExtendedOut']
        self._mapd_v2_path = path_from_mapd(sm)
        self._frames_since_path = 0
      # AND THE CACHE'S OWN AGE, which is what the I-215 drive needed and did not have. Checked
      # after the rebuild so a path built this frame is never dropped, and logged once on the edge
      # rather than every frame -- a rule that fires 20 times a second cannot be read off a drive.
      if self._mapd_v2_path is not None and self._frames_since_path >= MAPD_V2_PATH_MAX_AGE_FRAMES:
        cloudlog.error("SCC-Map: DROPPED A STALE MAPD PATH after %.1f s" %
                       (self._frames_since_path * DT_MDL))
        self._mapd_v2_path = None
      mapd_v2_path = self._mapd_v2_path
    self.map.update(long_enabled, long_override, v_ego, a_ego, v_cruise,
                    model_lat_acc=self.vision.max_pred_lat_acc, mapd_v2_path=mapd_v2_path)
