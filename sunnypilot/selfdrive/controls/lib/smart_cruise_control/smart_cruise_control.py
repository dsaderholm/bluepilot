"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import cereal.messaging as messaging
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.vision_controller import SmartCruiseControlVision
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.map_controller import SmartCruiseControlMap


class SmartCruiseControl:
  def __init__(self):
    self.vision = SmartCruiseControlVision()
    self.map = SmartCruiseControlMap()

  def update(self, sm: messaging.SubMaster, long_enabled: bool, long_override: bool, v_ego: float, a_ego: float, v_cruise: float) -> None:
    # BluePilot: vision FIRST. The map controller cross-checks its own curve against what the
    # camera can see, and running it second would hand it last frame's model data. Vision does not
    # read anything the map controller produces, so the swap costs nothing.
    self.vision.update(sm, long_enabled, long_override, v_ego, a_ego, v_cruise)
    self.map.update(long_enabled, long_override, v_ego, a_ego, v_cruise,
                    model_lat_acc=self.vision.max_pred_lat_acc)
