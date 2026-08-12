"""FusionPilot: both SCC controllers must leave `disabled` when their gates are open.

Measured on route 0000035a, 2026-08-12: SCC-Vision and SCC-Map were BOTH stuck in `disabled` for an
entire drive -- 0.0% occupancy each, zero real targets in 9,503 frames -- while their toggles read
True and `carControl.enabled` was True for 5,126 of the paired frames. Curves pulling 2.3-2.9 m/s^2
went completely unmanaged, including the exit.

The transition out of `disabled` needs only `long_enabled and enabled`. That is the whole condition,
so if it holds and the state does not move, something upstream of the state machine is wrong.

This drives the state machine directly with both gates open, which is the smallest thing that can
fail. It is deliberately NOT a test of curve targets -- it asks only whether the controller wakes up.
"""
from types import SimpleNamespace as NS

import numpy as np

from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.vision_controller import (
  SmartCruiseControlVision, VisionState,
)
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.map_controller import (
  SmartCruiseControlMap, MapState,
)


def _sm(curvature: float = 0.0, n: int = 33):
  """Minimum modelV2/controlsState the vision controller reads."""
  return {
    "modelV2": NS(orientationRate=NS(z=np.full(n, curvature)),
                  velocity=NS(x=np.full(n, 30.0))),
    "controlsState": NS(curvature=curvature),
  }


def test_vision_leaves_disabled_when_enabled_and_long_enabled():
  scc = SmartCruiseControlVision()
  scc.enabled = True
  for _ in range(5):
    scc.update(_sm(), long_enabled=True, long_override=False, v_ego=30.0, a_ego=0.0,
               v_cruise_setpoint=35.0)
  assert scc.state != VisionState.disabled, (
    "SCC-Vision never left `disabled` with its toggle on and carControl.enabled true -- this is what "
    "was measured on route 0000035a for a whole drive")


def test_map_leaves_disabled_when_enabled_and_long_enabled():
  scc = SmartCruiseControlMap()
  scc.enabled = True
  for _ in range(5):
    scc.update(long_enabled=True, long_override=False, v_ego=30.0, a_ego=0.0, v_cruise=35.0,
               model_lat_acc=0.0)
  assert scc.state != MapState.disabled, (
    "SCC-Map never left `disabled` with its toggle on and carControl.enabled true")


def test_vision_stays_disabled_when_its_toggle_is_off():
  """The counterweight: the gate must still work in the direction it is meant to.

  _update_params re-reads the param every few frames and would clobber `enabled`, so it is stubbed
  out. Without that this test passes for the wrong reason -- the stubbed Params returns the shipped
  default, which is on.
  """
  scc = SmartCruiseControlVision()
  scc.enabled = False
  scc._update_params = lambda: None
  for _ in range(5):
    scc.update(_sm(), long_enabled=True, long_override=False, v_ego=30.0, a_ego=0.0,
               v_cruise_setpoint=35.0)
  assert scc.state == VisionState.disabled, "the feature toggle stopped disabling the controller"
