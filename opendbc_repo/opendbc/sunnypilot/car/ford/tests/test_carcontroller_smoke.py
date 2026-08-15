"""FusionPilot: run the REAL Ford CarController for a few thousand frames and require it not to raise.

WHY THIS EXISTS
---------------
On 2026-08-15 the car became undrivable on the first control frame:

    File "opendbc/sunnypilot/car/ford/icbm.py", line 111, in _update_gap
      was_mode, was_result = self.gap.mode, self.gap.last_result
    AttributeError: 'CarController' object has no attribute 'gap'

`IntelligentCruiseButtonManagementInterface.update` is called CLASS-STYLE from
ford/carcontroller.py with `self` = the CarController, and the line that would construct a real
instance is commented out. So an `__init__` added to that class never runs, and the attribute it
sets does not exist. The code read as obviously correct, 607 tests were green, and ruff had nothing
to say -- because NOTHING OFFLINE HAD EVER CALLED CarController.update.

That is the gap this closes. Every existing test here exercises pure logic (`gap_control.py`,
`controller.py`) or asserts on arguments at a stubbed boundary. Pure logic cannot catch a wiring
mistake between two objects, and that is the category that takes the car off the road.

WHAT IS STUBBED, AND THE RULE FOR ADDING TO IT
----------------------------------------------
Exactly one thing: `cereal.messaging.SubMaster`, because it opens real sockets. Everything else --
CarController, the ICBM interface, the CAN packer, the real DBC, the real CarParams for his car --
is the shipped object.

**Stub the transport, never the code under test.** A stub that is laxer than the real thing hides
the bugs it was built to catch; that is written up in CLAUDE.md and it is why `CS` below is a strict
object with real DBC-derived dicts rather than a Mock. A Mock would have returned a Mock for
`self.gap` and this test would have passed while the car did not start.

WHAT IT COVERS
--------------
The matrix below is not decorative. Each axis is a branch in `CarController.update` or in the ICBM
path underneath it, and the failure above lived in the `sendButton == none` branch -- which is the
common case, not an edge case.
"""
from __future__ import annotations

import os
import re
import sys
import types

import pytest


def _stub_submaster():
  """Replace only the socket layer. Imported before CarController so its module-level import binds."""
  import cereal.messaging as messaging

  class FakeSubMaster:
    def __init__(self, services, *a, **k):
      from cereal import log
      self._services = list(services)
      self.alive = dict.fromkeys(self._services, True)
      self.valid = dict.fromkeys(self._services, True)
      self.updated = dict.fromkeys(self._services, False)
      self.frame = 0
      self._data = {s: getattr(log.Event.new_message(**{s: {}}), s) for s in self._services}

    def update(self, timeout=0):
      self.frame += 1

    def __getitem__(self, s):
      return self._data[s]

  messaging.SubMaster = FakeSubMaster
  return messaging


def _dbc_signals(message: str) -> list[str]:
  """Signal names for a message, read from the real DBC so this cannot drift from the car."""
  # Walk up to the opendbc package root rather than counting "..", which was miscounted once.
  d = os.path.dirname(os.path.abspath(__file__))
  while d != os.path.dirname(d) and not os.path.isdir(os.path.join(d, "dbc")):
    d = os.path.dirname(d)
  dbc = os.path.join(d, "dbc", "ford_lincoln_base_pt.dbc")
  names, inside = [], False
  with open(dbc, encoding="utf-8", errors="replace") as f:
    for line in f:
      if line.startswith("BO_ "):
        inside = re.match(rf"BO_ \d+ {re.escape(message)}\s*:", line) is not None
      elif inside and line.strip().startswith("SG_ "):
        names.append(line.strip().split()[1])
  assert names, f"no signals found for {message} -- did the DBC move?"
  return names


class FakeCarState:
  """Strict stand-in for Ford's CarState: real capnp `out`, real DBC-derived stock value dicts.

  Deliberately NOT a Mock. Missing attributes must raise here exactly as they would on the car --
  that is the entire point of the test.
  """

  def __init__(self, out):
    self.out = out
    self.buttons_stock_values = dict.fromkeys(_dbc_signals("Steering_Data_FD1"), 0)
    self.acc_tja_status_stock_values = dict.fromkeys(_dbc_signals("ACCDATA_3"), 0)
    self.lkas_status_stock_values = dict.fromkeys(_dbc_signals("IPMA_Data"), 0)
    # The ACC gap the camera reports. 3 is what the owner drives.
    self.acc_tja_status_stock_values["AccTGap_D_Dsply"] = 3


@pytest.fixture(scope="module")
def carcontroller_parts():
  _stub_submaster()
  from opendbc.car import Bus, structs
  from opendbc.car.ford.carcontroller import CarController
  from opendbc.car.ford.interface import CarInterface
  from opendbc.car.ford.values import CAR

  CP = CarInterface.get_non_essential_params(CAR.FORD_FUSION_MK5)
  CP_SP = structs.CarParamsSP()
  CP_SP.intelligentCruiseButtonManagementAvailable = True
  return CarController, {Bus.pt: "ford_lincoln_base_pt"}, CP, CP_SP, structs


def _car_control(structs, *, enabled, send_button, gap_target):
  """Match card's call convention EXACTLY: CC is a capnp READER, CC_SP is the opendbc dataclass.

  `selfdrive/car/card.py` does `self.CI.apply(CC, convert_carControlSP(CC_SP), now_nanos)` with CC
  taken straight off the carControl message. Getting this wrong shows up immediately --
  `actuators.as_builder()` at the end of CarController.update only exists on a reader -- and getting
  it right is what makes this test stand in for the real call rather than resemble it.
  """
  from cereal import car as capnp_car

  msg = capnp_car.CarControl.new_message()
  msg.enabled = enabled
  msg.latActive = enabled
  msg.longActive = False
  msg.hudControl.leadVisible = True
  msg.hudControl.leftLaneVisible = True
  msg.hudControl.rightLaneVisible = True
  msg.hudControl.leadDistanceBars = 3

  CC_SP = structs.CarControlSP()
  CC_SP.intelligentCruiseButtonManagement.sendButton = send_button
  CC_SP.intelligentCruiseButtonManagement.gapTarget = gap_target
  return msg.as_reader(), CC_SP


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("send_button", ["none", "increase", "decrease"])
@pytest.mark.parametrize("gap_target", [0, 1, 3, 5])
def test_update_never_raises(carcontroller_parts, enabled, send_button, gap_target):
  """The whole matrix, 400 frames each. This is what would have caught the 2026-08-15 crash.

  `send_button == "none"` with any gap target is the branch that took the car off the road, and it
  is also the branch the car spends nearly all of its time in.
  """
  CarController, dbc_names, CP, CP_SP, structs = carcontroller_parts
  SendButtonState = structs.IntelligentCruiseButtonManagement.SendButtonState

  cc = CarController(dbc_names, CP, CP_SP)
  out = structs.CarState()
  out.vEgo = 20.0
  out.vEgoRaw = 20.0
  out.cruiseState.enabled = enabled
  out.cruiseState.available = True
  out.cruiseState.speedCluster = 29.0
  CS = FakeCarState(out)

  CC, CC_SP = _car_control(structs, enabled=enabled,
                           send_button=getattr(SendButtonState, send_button), gap_target=gap_target)

  for frame in range(400):
    # Nothing here asserts on the CAN produced -- other tests do that. The claim is only that a
    # real CarController survives being driven, which is the claim that was false.
    cc.update(CC, CC_SP, CS, frame * 10_000_000)


def test_carcontroller_owns_the_icbm_gap_state(carcontroller_parts):
  """The gap controller must be created by CarController.__init__, not by the interface class.

  ford/carcontroller.py calls `IntelligentCruiseButtonManagementInterface.update(self, ...)`
  class-style, so that class is never instantiated and its `__init__` never runs. Putting the state
  there looks right and fails on the first frame.
  """
  CarController, dbc_names, CP, CP_SP, _ = carcontroller_parts
  cc = CarController(dbc_names, CP, CP_SP)
  assert hasattr(cc, "icbm_gap"), "CarController.__init__ must create the gap controller"
  assert cc.icbm_gap_failed is False

  from opendbc.sunnypilot.car.ford.icbm import IntelligentCruiseButtonManagementInterface
  assert "__init__" not in IntelligentCruiseButtonManagementInterface.__dict__, (
    "This class is never instantiated -- an __init__ here is dead code whose attributes do not "
    "exist at runtime. Put per-drive state on the Ford CarController instead."
  )


def test_a_broken_gap_controller_does_not_take_the_car_with_it(carcontroller_parts):
  """A follow-distance convenience must degrade to doing nothing, never to an undrivable car.

  The 2026-08-15 failure was not just a missing attribute -- it was that ANY exception in this path
  propagates out of CarController.update, through card, and stops the car. So the path is latched
  off on failure, and this proves it with a controller rigged to throw.
  """
  CarController, dbc_names, CP, CP_SP, structs = carcontroller_parts
  cc = CarController(dbc_names, CP, CP_SP)

  class Exploding:
    mode = "unknown"
    last_result = ""
    inverted = False

    def update(self, *a, **k):
      raise RuntimeError("boom")

  cc.icbm_gap = Exploding()

  out = structs.CarState()
  out.vEgo = 20.0
  out.vEgoRaw = 20.0
  out.cruiseState.enabled = True
  out.cruiseState.available = True
  CS = FakeCarState(out)
  CC, CC_SP = _car_control(structs, enabled=True,
                           send_button=structs.IntelligentCruiseButtonManagement.SendButtonState.none,
                           gap_target=1)

  for frame in range(200):
    cc.update(CC, CC_SP, CS, frame * 10_000_000)

  assert cc.icbm_gap_failed, "the failure must latch off rather than being retried every frame"


if __name__ == "__main__":
  sys.exit(pytest.main([__file__, "-q"]))
