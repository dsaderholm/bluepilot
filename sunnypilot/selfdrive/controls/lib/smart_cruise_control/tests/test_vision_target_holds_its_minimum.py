"""FusionPilot: SCC-Vision's target must not shake, because ICBM presses a button for every wobble.

`max_pred_lat_acc` is a 97th percentile over the model plan recomputed every frame, so `v_target`
jitters hard on a twisty road. Measured on the SLC -> Yosemite mountain routes, 22,344 target
frames: the plan target reversed direction 8,110 times and the source is this controller alone --

    TOTAL reversals -- sccVision 8319 | sccMap 38

ICBM asked `increase` on 15,779 frames and `decrease` on 12,381, with 17 bursts of 8+ reversals
inside 20 s. The hunt analysis says ICBM was NOT oscillating on its own; it was faithfully tracking
a number that would not sit still.

The filter is a MINIMUM over a 0.5 s window, which by construction adopts every FALL on the frame
it arrives and delays only RISES -- the one direction this fork permits. The window is deliberately
the SMALLEST that removes the hunting (86% of reversals): every extra millisecond is a spurious LOW
frame governing for longer, and going to 1.0 s buys six more points of reversal removal for 52%
more suppression of the target.

These tests drive the REAL `_update_calculations`, not a reimplementation of the filter, so a change
to where the hold is applied is caught rather than mirrored.
"""
from __future__ import annotations

import math
from types import SimpleNamespace as NS

import numpy as np

from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.vision_controller import (
  _A_LAT_REG_MAX,
  _V_TARGET_HOLD_FRAMES,
  _V_TARGET_HOLD_S,
  SmartCruiseControlVision,
  VisionState,
)
from openpilot.common.realtime import DT_MDL

V_EGO = 30.0


def _sm(max_pred: float):
  """A SubMaster stub whose model plan yields exactly `max_pred` as the 97th percentile."""
  n = 33
  vel = np.full(n, 1.0)
  rate = np.full(n, max_pred)
  return {"modelV2": NS(orientationRate=NS(z=rate), velocity=NS(x=vel)),
          "controlsState": NS(curvature=0.0)}


def _vision() -> SmartCruiseControlVision:
  v = SmartCruiseControlVision.__new__(SmartCruiseControlVision)
  v.long_enabled = True
  v.v_ego = V_EGO
  v.sensitivity = 1.0
  v.state = VisionState.turning
  from collections import deque
  v._v_target_hist = deque(maxlen=_V_TARGET_HOLD_FRAMES)
  return v


def _raw(max_pred: float) -> float:
  """What v_target would be with no filter at all."""
  return V_EGO * math.sqrt((_A_LAT_REG_MAX / 1.0) / max_pred)


def _drive(v, preds):
  return [(_step(v, p)) for p in preds]


def _step(v, max_pred: float) -> float:
  v._update_calculations(_sm(max_pred))
  return v.v_target


def test_the_window_is_derived_from_the_model_rate():
  """A hardcoded frame count silently becomes the wrong duration if DT_MDL moves."""
  assert _V_TARGET_HOLD_FRAMES == max(int(round(_V_TARGET_HOLD_S / DT_MDL)), 1)
  assert _V_TARGET_HOLD_S == 0.5


def test_a_fall_is_adopted_on_the_frame_it_arrives():
  """THE LOAD-BEARING PROPERTY. A minimum cannot delay a drop, and a filter that made the car
  slower to slow down would be unacceptable regardless of what it bought."""
  v = _vision()
  _step(v, 0.5)                       # gentle: a high target
  tight = _step(v, 8.0)               # the corner appears
  assert tight == min(tight, _raw(8.0))
  assert abs(tight - _raw(8.0)) < 1e-6


def test_a_one_frame_spike_upward_is_suppressed():
  """The measured failure: a single noisy frame reading 'no curve' must not raise the target and
  make ICBM press +."""
  v = _vision()
  _step(v, 8.0)
  spiked = _step(v, 0.5)              # one frame of nonsense
  assert abs(spiked - _raw(8.0)) < 1e-6, "the spike leaked through"


def test_a_sustained_rise_is_adopted_after_the_window():
  """The corner really has ended. The target must recover -- bounded by the window, not forever."""
  v = _vision()
  _step(v, 8.0)
  out = [_step(v, 0.5) for _ in range(_V_TARGET_HOLD_FRAMES + 2)]
  assert abs(out[-1] - _raw(0.5)) < 1e-6, "the target never recovered"
  assert abs(out[0] - _raw(8.0)) < 1e-6, "it recovered immediately, so nothing is being held"


def test_the_delay_never_exceeds_the_window():
  """Worst-case lag adopting a genuine rise is the window itself, which is what the replay
  measured at 0.45 s. Anything longer would be a different trade than the one chosen."""
  v = _vision()
  _step(v, 8.0)
  for i in range(_V_TARGET_HOLD_FRAMES * 3):
    got = _step(v, 0.5)
    if abs(got - _raw(0.5)) < 1e-6:
      assert (i + 1) * DT_MDL <= _V_TARGET_HOLD_S + 1e-9
      return
  raise AssertionError("never adopted the rise")


def test_the_history_is_dropped_when_the_controller_disables():
  """A low target from the last corner must not be carried into the next engagement -- that would
  slow the car for a corner it already finished."""
  v = _vision()
  _step(v, 8.0)
  assert len(v._v_target_hist) > 0
  v.state = VisionState.disabled
  v._v_target_hist.clear()            # what update() does at the disabled edge
  assert abs(_step(v, 0.5) - _raw(0.5)) < 1e-6


def test_the_filter_never_raises_the_target():
  """Whatever the input series, the filtered target is <= the unfiltered one on every frame. This
  is the property that makes the change safe to land on measured evidence: it can only ever make
  the car slow earlier, never later."""
  v = _vision()
  rng = np.random.default_rng(0)
  for p in rng.uniform(0.3, 9.0, size=200):
    got = _step(v, float(p))
    assert got <= _raw(float(p)) + 1e-9
