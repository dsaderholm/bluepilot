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

  def update(self, sm: messaging.SubMaster, long_enabled: bool, long_override: bool, v_ego: float, a_ego: float, v_cruise: float) -> None:
    # BluePilot: vision FIRST. The map controller cross-checks its own curve against what the
    # camera can see, and running it second would hand it last frame's model data. Vision does not
    # read anything the map controller produces, so the swap costs nothing.
    self.vision.update(sm, long_enabled, long_override, v_ego, a_ego, v_cruise)
    # FusionPilot: None means "read v1", which is also what happens if v2 is selected but silent.
    # Falling back is right HERE and wrong in the SLA reader, and the asymmetry is deliberate: there,
    # a quiet fallback would hide a broken install behind plausible speed limits, while here v1 is
    # still the shipped curve source and the failure being avoided is not slowing for a corner.
    mapd_v2_path = path_from_mapd(sm) if self.use_mapd_v2 else None
    self.map.update(long_enabled, long_override, v_ego, a_ego, v_cruise,
                    model_lat_acc=self.vision.max_pred_lat_acc, mapd_v2_path=mapd_v2_path)
