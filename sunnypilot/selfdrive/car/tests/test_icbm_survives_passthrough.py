"""FusionPilot: op long as PERMISSION does not mean openpilot is driving.

Both ICBM gates in `interfaces.py` keyed on `CP.openpilotLongitudinalControl` alone, encoding the
assumption that op long implies openpilot authors the longitudinal command. Under the stock-ACC
passthrough the numbers on the wire are Ford's, the set speed still governs the car, and panda still
carries `Steering_Data_FD1` -- so holds, Speed Limit Assist, both curve controllers, pinned holds and
the gap button are all still meaningful and must survive.

The second gate is the one that actually bit on drive A, and it bit for a reason unrelated to the
passthrough: it REMOVES the param rather than ignoring it, and the key has no default in
params_keys.h, so it returns as off. He re-enabled ICBM mid-drive, the gate deleted it again, and
the device still read `unset` afterwards.
"""
from __future__ import annotations

from opendbc.car import structs
from openpilot.sunnypilot.selfdrive.car.interfaces import (
  _cleanup_unsupported_params,
  _initialize_intelligent_cruise_button_management,
)


class FakeParams:
  def __init__(self, **vals):
    self.vals = dict(vals)
    self.removed: list[str] = []

  def get_bool(self, k):
    return bool(self.vals.get(k, False))

  def remove(self, k):
    self.removed.append(k)
    self.vals.pop(k, None)


def _cp(op_long: bool):
  cp = structs.CarParams()
  cp.openpilotLongitudinalControl = op_long
  cp.steerControlType = structs.CarParams.SteerControlType.torque
  cp_sp = structs.CarParamsSP()
  cp_sp.intelligentCruiseButtonManagementAvailable = True
  cp_sp.pcmCruiseSpeed = True
  return cp, cp_sp


def test_op_long_without_the_passthrough_still_retires_icbm():
  """The original behaviour, unchanged: openpilot really is driving, so the buttons mean nothing."""
  cp, cp_sp = _cp(op_long=True)
  p = FakeParams(IntelligentCruiseButtonManagement=True, StockAccPassthrough=False)
  _initialize_intelligent_cruise_button_management(cp, cp_sp, p)
  assert cp_sp.pcmCruiseSpeed, "ICBM took the set speed while openpilot was authoring the command"
  _cleanup_unsupported_params(cp, cp_sp, p)
  assert "IntelligentCruiseButtonManagement" in p.removed


def test_op_long_WITH_the_passthrough_keeps_icbm():
  """Ford is still computing and the set speed still governs, so every ICBM-layer feature survives."""
  cp, cp_sp = _cp(op_long=True)
  p = FakeParams(IntelligentCruiseButtonManagement=True, StockAccPassthrough=True)
  _initialize_intelligent_cruise_button_management(cp, cp_sp, p)
  assert not cp_sp.pcmCruiseSpeed, "ICBM did not take the set speed under the passthrough"
  _cleanup_unsupported_params(cp, cp_sp, p)
  assert "IntelligentCruiseButtonManagement" not in p.removed, (
    "the passthrough drive deleted his ICBM toggle, and it has no default so it comes back OFF")


def test_no_op_long_is_untouched():
  cp, cp_sp = _cp(op_long=False)
  p = FakeParams(IntelligentCruiseButtonManagement=True, StockAccPassthrough=True)
  _initialize_intelligent_cruise_button_management(cp, cp_sp, p)
  assert not cp_sp.pcmCruiseSpeed
  _cleanup_unsupported_params(cp, cp_sp, p)
  assert "IntelligentCruiseButtonManagement" not in p.removed


def test_the_param_is_never_removed_when_icbm_is_unavailable_but_passthrough_is_on():
  """Availability still governs. The third state is about op long, not about the platform."""
  cp, cp_sp = _cp(op_long=True)
  cp_sp.intelligentCruiseButtonManagementAvailable = False
  p = FakeParams(IntelligentCruiseButtonManagement=True, StockAccPassthrough=True)
  _cleanup_unsupported_params(cp, cp_sp, p)
  assert "IntelligentCruiseButtonManagement" in p.removed
