"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.sunnypilot.mapd import MAPD_V2_ON
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.mapd_v2_path import path_from_mapd
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.vision_controller import SmartCruiseControlVision
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.map_controller import SmartCruiseControlMap


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
    # The alive/valid clear is NOT optional: without it a v2 that dies leaves `updated` False
    # forever and the last path would be served indefinitely, which is the one way caching could
    # turn a fallback into a stale answer.
    if not self.use_mapd_v2:
      mapd_v2_path = None
    else:
      if not (sm.alive['mapdExtendedOut'] and sm.valid['mapdExtendedOut']):
        self._mapd_v2_path = None
      elif sm.updated['mapdExtendedOut']:
        self._mapd_v2_path = path_from_mapd(sm)
      mapd_v2_path = self._mapd_v2_path
    self.map.update(long_enabled, long_override, v_ego, a_ego, v_cruise,
                    model_lat_acc=self.vision.max_pred_lat_acc, mapd_v2_path=mapd_v2_path)
