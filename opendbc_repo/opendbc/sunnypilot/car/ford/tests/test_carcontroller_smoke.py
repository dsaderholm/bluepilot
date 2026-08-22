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
    # The camera's own ACC command, plus its freshness. Both are what the stock-ACC passthrough
    # reads, and a CarController that cannot survive their ABSENCE is the failure this file exists
    # for -- see the 2026-08-15 crash in the module docstring.
    self.acc_stock_values = dict.fromkeys(_dbc_signals("ACCDATA"), 0)
    self.acc_cam_valid = True
    # Has the APIM ever sent the GPS messages the IPMA waits on? False on this car -- measured
    # zero frames across a whole drive -- which is why the synthesizer exists.
    self.apim_gps_nav_seen = False


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


def _car_control(structs, *, enabled, send_button, gap_target, long_active=False, accel=None):
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
  msg.longActive = long_active
  msg.hudControl.leadVisible = True
  msg.hudControl.leftLaneVisible = True
  msg.hudControl.rightLaneVisible = True
  msg.hudControl.leadDistanceBars = 3
  # `accel` lets a test ask openpilot for real braking. Without it `actuators.accel` is 0.0, so any
  # test about openpilot's OWN deceleration is asserting against a plan that requests nothing --
  # the fixture-more-orderly-than-reality trap, caught here by a test that failed for the fixture's
  # reason rather than the code's.
  if accel is not None:
    msg.actuators.accel = accel
    msg.actuators.longControlState = capnp_car.CarControl.Actuators.LongControlState.pid

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


@pytest.mark.parametrize("passthrough", [False, True])
@pytest.mark.parametrize("cam_valid", [True, False])
def test_the_acc_passthrough_never_raises_and_falls_back_when_the_camera_is_stale(
    carcontroller_parts, passthrough, cam_valid):
  """The stock-ACC passthrough, driven through a real CarController.

  `cam_valid=False` is the case that matters. A CANParser's `vl` dict keeps its last value forever,
  so a dead camera bus looks identical to a live one from the values alone -- and forwarding a
  FROZEN brake or throttle request is the worst thing this feature could do. The carcontroller must
  fall back to its own computed ACCDATA, which it can only do if it is told about the staleness.
  """
  CarController, dbc_names, CP, CP_SP, structs = carcontroller_parts
  # The ACCDATA block only runs under op long, and the shared fixture's CarParams does not have it.
  # Without this the passthrough branch is unreachable and the test asserts against a path that
  # never executes -- which is exactly how it passed while proving nothing.
  from opendbc.car.ford.interface import CarInterface
  from opendbc.car.ford.values import CAR
  CP_long = CarInterface.get_non_essential_params(CAR.FORD_FUSION_MK5)
  CP_long.openpilotLongitudinalControl = True

  cc = CarController(dbc_names, CP_long, CP_SP)
  cc.stock_acc_passthrough = passthrough

  out = structs.CarState()
  out.vEgo = 20.0
  out.vEgoRaw = 20.0
  out.cruiseState.enabled = True
  out.cruiseState.available = True
  CS = FakeCarState(out)
  CS.acc_cam_valid = cam_valid

  # A distinctive brake request, so the frame on the wire says which path produced it.
  CS.acc_stock_values["AccBrkTot_A_Rq"] = -1.75

  CC, CC_SP = _car_control(structs, enabled=True,
                           send_button=structs.IntelligentCruiseButtonManagement.SendButtonState.none,
                           gap_target=0, long_active=True)
  sent = []
  for frame in range(200):
    _, can_sends = cc.update(CC, CC_SP, CS, frame * 10_000_000)
    sent.extend(m for m in can_sends if m[0] == 390)

  assert sent, "no ACCDATA was sent at all"
  from opendbc.sunnypilot.car.ford.fordcan_ext import create_acc_msg_passthrough

  class _P:
    def __init__(self, real): self.real = real
    def make_can_msg(self, *a): return self.real.make_can_msg(*a)
  expected = create_acc_msg_passthrough(_P(cc.packer), cc.CAN, CS.acc_stock_values)[1]
  forwarded = any(m[1] == expected for m in sent)

  if passthrough and cam_valid:
    assert forwarded, "passthrough was on with a live camera and Ford's own frame never went out"
  else:
    assert not forwarded, (
      "the camera's command was forwarded when it should not have been -- with a STALE camera that "
      "is a frozen brake request held indefinitely")


def test_a_carcontroller_with_no_camera_acc_at_all_still_drives(carcontroller_parts):
  """Old CarState, a merge that drops the field, a platform that never sets it -- the passthrough
  must degrade to the normal path rather than taking the control loop down with it."""
  CarController, dbc_names, CP, CP_SP, structs = carcontroller_parts
  cc = CarController(dbc_names, CP, CP_SP)
  cc.stock_acc_passthrough = True

  out = structs.CarState()
  out.vEgo = 20.0
  out.vEgoRaw = 20.0
  out.cruiseState.enabled = True
  out.cruiseState.available = True
  CS = FakeCarState(out)
  del CS.acc_stock_values
  del CS.acc_cam_valid

  CC, CC_SP = _car_control(structs, enabled=True,
                           send_button=structs.IntelligentCruiseButtonManagement.SendButtonState.none,
                           gap_target=0)
  for frame in range(200):
    cc.update(CC, CC_SP, CS, frame * 10_000_000)


def test_the_gap_button_still_goes_out_under_the_passthrough(carcontroller_parts):
  """The configuration he will actually drive: op long ON, passthrough ON, ICBM alive, a gap asked for.

  Nothing covered this. The gap cases above run with the default CarParams (no op long), and the
  passthrough case runs with `gap_target=0` -- so the one combination that matters had no test at
  all, and it is the one where three separate gates had to be taught a new state before ICBM would
  even run.

  Asserts the PRESS REACHES THE WIRE, not merely that nothing raised. `create_button_msg` is called
  for the camera bus and the main bus, so a gap press is two Steering_Data_FD1 sends carrying one of
  the three gap signals.
  """
  from opendbc.car.ford.interface import CarInterface
  from opendbc.car.ford.values import CAR
  from opendbc.sunnypilot.car.ford.gap_control import GAP_MIN, SETTLE_FRAMES

  CarController, dbc_names, CP, CP_SP, structs = carcontroller_parts
  CP_long = CarInterface.get_non_essential_params(CAR.FORD_FUSION_MK5)
  CP_long.openpilotLongitudinalControl = True

  cc = CarController(dbc_names, CP_long, CP_SP)
  cc.stock_acc_passthrough = True

  out = structs.CarState()
  out.vEgo = 25.0
  out.vEgoRaw = 25.0
  out.cruiseState.enabled = True
  out.cruiseState.available = True
  CS = FakeCarState(out)
  CS.acc_cam_valid = True
  # The camera reports gap 3; the requester wants 1. The readback is what the controller closes on,
  # and drive B proved it tracks real presses (seven presses, seven changes, same timestamps).
  CS.acc_tja_status_stock_values["AccTGap_D_Dsply"] = 3

  CC, CC_SP = _car_control(structs, enabled=True,
                           send_button=structs.IntelligentCruiseButtonManagement.SendButtonState.none,
                           gap_target=GAP_MIN, long_active=True)

  gap_signals = {"AccButtnGapIncPress", "AccButtnGapDecPress", "AccButtnGapTogglePress"}
  pressed = 0
  for frame in range(SETTLE_FRAMES + 200):
    _, can_sends = cc.update(CC, CC_SP, CS, frame * 10_000_000)
    for addr, dat, _bus in can_sends:
      if addr == 0x083:
        pressed += 1

  assert not cc.icbm_gap_failed, "the gap path latched off under the passthrough"
  assert cc.icbm_gap.active, "no lease opened for a gap request the camera reported differently"
  assert pressed > 0, (
    "no Steering_Data_FD1 went out -- the gap request never reached the wire under the passthrough, "
    "which is the configuration the three ICBM gates were fixed for")


def _decode_acc_brake(data: bytes) -> float:
  """AccBrkTot_A_Rq out of a raw ACCDATA frame: start bit 4, 13 bits, 0.0039, -20, big-endian."""
  v = int.from_bytes(data, "big")
  total = len(data) * 8
  idx = (4 // 8) * 8 + (7 - (4 % 8))
  return ((v >> (total - idx - 13)) & ((1 << 13) - 1)) * 0.0039 - 20.0


def test_the_override_never_brakes_softer_than_ford_was_asking(carcontroller_parts):
  """THE NEAR-MISS, 2026-08-20. Taking authority must never mean taking braking AWAY.

  Measured on his own drives, the override braked softer than the camera it displaced on three of
  four episodes -- worst case Ford asking -1.14 m/s^2 and the override sending -0.10 for 8.9
  seconds, on an approach to a stopped car he then had to brake hard to avoid. Nothing in the code
  had ever compared the two.

  So: Ford asking for real braking, openpilot asking for almost none, override forced on. Every
  ACCDATA frame that goes out must carry AT LEAST Ford's deceleration.
  """
  from opendbc.car.ford.interface import CarInterface
  from opendbc.car.ford.values import CAR

  CarController, dbc_names, CP, CP_SP, structs = carcontroller_parts
  CP_long = CarInterface.get_non_essential_params(CAR.FORD_FUSION_MK5)
  CP_long.openpilotLongitudinalControl = True

  cc = CarController(dbc_names, CP_long, CP_SP)
  cc.stock_acc_passthrough = True
  cc.stop_override_enabled = True
  # Force the override on rather than driving it through a whole arming sequence: this test is
  # about what goes on the wire WHILE it is active, not about when it arms.
  cc.stop_override.update = lambda **kw: True

  out = structs.CarState()
  out.vEgo = 14.0
  out.vEgoRaw = 14.0
  out.cruiseState.enabled = True
  out.cruiseState.available = True
  CS = FakeCarState(out)
  CS.acc_cam_valid = True
  FORD_BRAKE = -1.75
  CS.acc_stock_values["AccBrkTot_A_Rq"] = FORD_BRAKE

  CC, CC_SP = _car_control(structs, enabled=True,
                           send_button=structs.IntelligentCruiseButtonManagement.SendButtonState.none,
                           gap_target=0, long_active=True)

  softest = None
  for frame in range(200):
    _, can_sends = cc.update(CC, CC_SP, CS, frame * 10_000_000)
    for m in can_sends:
      if m[0] == 390:
        b = _decode_acc_brake(bytes(m[1]))
        softest = b if softest is None else max(softest, b)

  assert softest is not None, "no ACCDATA went out at all"
  assert softest <= FORD_BRAKE + 0.02, (
    f"the override sent {softest:.2f} m/s^2 while Ford was asking {FORD_BRAKE:.2f} -- taking "
    f"authority took braking away, which is the near-miss of 2026-08-20")


def test_the_override_keeps_its_own_braking_when_ford_asks_for_none(carcontroller_parts):
  """The other half, and the whole point of the feature.

  Below Ford's floor the camera has given up and asks for nothing. The floor must not clamp our
  braking back toward zero there -- if it did, `min` would have turned the feature off entirely on
  exactly the approach it exists for.
  """
  from opendbc.car.ford.interface import CarInterface
  from opendbc.car.ford.values import CAR

  CarController, dbc_names, CP, CP_SP, structs = carcontroller_parts
  CP_long = CarInterface.get_non_essential_params(CAR.FORD_FUSION_MK5)
  CP_long.openpilotLongitudinalControl = True

  cc = CarController(dbc_names, CP_long, CP_SP)
  cc.stock_acc_passthrough = True
  cc.stop_override_enabled = True
  cc.stop_override.update = lambda **kw: True

  out = structs.CarState()
  out.vEgo = 4.0
  out.vEgoRaw = 4.0
  out.cruiseState.enabled = True
  out.cruiseState.available = True
  CS = FakeCarState(out)
  CS.acc_cam_valid = True
  CS.acc_stock_values["AccBrkTot_A_Rq"] = 0.0      # Ford has given up

  CC, CC_SP = _car_control(structs, enabled=True,
                           send_button=structs.IntelligentCruiseButtonManagement.SendButtonState.none,
                           gap_target=0, long_active=True, accel=-1.5)

  hardest = 0.0
  for frame in range(200):
    _, can_sends = cc.update(CC, CC_SP, CS, frame * 10_000_000)
    for m in can_sends:
      if m[0] == 390:
        hardest = min(hardest, _decode_acc_brake(bytes(m[1])))

  assert hardest < -0.05, (
    f"with Ford asking 0.0 the override only ever sent {hardest:.2f} m/s^2 -- the floor clamped our "
    f"own braking away and the feature does nothing below Ford's floor")


def test_a_stopped_car_is_never_asked_for_ford_scale_braking(carcontroller_parts):
  """THE WIND-UP, 2026-08-20. openpilot must not pin a stationary car with a huge brake request.

  openpilot's stopping state ramps toward `stopAccel` to hold a stopped car -- normal upstream, and
  wildly outside what this ACC system ever sees. Measured across every standstill frame of routes
  0000039d and 0000039f: Ford's own `AccBrkTot_A_Rq` spans -0.25 to +0.47, while ours reached -2.61
  on 5% of stopped frames. Ford holds a stop with `AccStopStat_B_Rq`, not with a deceleration.

  On 0000039d the request ramped -1.03 -> -2.61 over four seconds against a dead-stopped car and
  held; the next ignition came up with cruise `Denied`.

  So: car stopped, openpilot asking for hard braking, and the wire must stay inside Ford's envelope.
  """
  from opendbc.car.ford.interface import CarInterface
  from opendbc.car.ford.values import CAR
  from opendbc.car.ford.carcontroller import _STANDSTILL_ACCEL_FLOOR

  CarController, dbc_names, CP, CP_SP, structs = carcontroller_parts
  CP_long = CarInterface.get_non_essential_params(CAR.FORD_FUSION_MK5)
  CP_long.openpilotLongitudinalControl = True

  cc = CarController(dbc_names, CP_long, CP_SP)
  cc.stock_acc_passthrough = True

  out = structs.CarState()
  out.vEgo = 0.0
  out.vEgoRaw = 0.0
  out.standstill = True
  out.cruiseState.enabled = True
  out.cruiseState.available = True
  CS = FakeCarState(out)
  CS.acc_cam_valid = True
  CS.acc_stock_values["AccBrkTot_A_Rq"] = -0.02        # what Ford actually asks while holding

  CC, CC_SP = _car_control(structs, enabled=True,
                           send_button=structs.IntelligentCruiseButtonManagement.SendButtonState.none,
                           gap_target=0, long_active=True, accel=-2.61)

  hardest = 0.0
  for frame in range(300):
    _, can_sends = cc.update(CC, CC_SP, CS, frame * 10_000_000)
    for m in can_sends:
      if m[0] == 390:
        hardest = min(hardest, _decode_acc_brake(bytes(m[1])))

  assert hardest >= _STANDSTILL_ACCEL_FLOOR - 0.02, (
    f"a stopped car was asked for {hardest:.2f} m/s^2 -- Ford's deepest standstill request across "
    f"7,168 measured frames was -0.25, and this is the shape that preceded cruise coming up Denied")


def test_the_standstill_floor_does_not_touch_a_moving_car(carcontroller_parts):
  """The clamp is about holding, not stopping. If it bled into the approach it would cap the
  braking that brings the car to rest -- turning a fix for the standstill into a much worse bug on
  every deceleration."""
  from opendbc.car.ford.interface import CarInterface
  from opendbc.car.ford.values import CAR

  CarController, dbc_names, CP, CP_SP, structs = carcontroller_parts
  CP_long = CarInterface.get_non_essential_params(CAR.FORD_FUSION_MK5)
  CP_long.openpilotLongitudinalControl = True

  cc = CarController(dbc_names, CP_long, CP_SP)
  cc.stock_acc_passthrough = False        # author our own, no camera frame to floor against

  out = structs.CarState()
  out.vEgo = 13.0
  out.vEgoRaw = 13.0
  out.standstill = False
  out.cruiseState.enabled = True
  out.cruiseState.available = True
  CS = FakeCarState(out)

  CC, CC_SP = _car_control(structs, enabled=True,
                           send_button=structs.IntelligentCruiseButtonManagement.SendButtonState.none,
                           gap_target=0, long_active=True, accel=-2.2)

  hardest = 0.0
  for frame in range(300):
    _, can_sends = cc.update(CC, CC_SP, CS, frame * 10_000_000)
    for m in can_sends:
      if m[0] == 390:
        hardest = min(hardest, _decode_acc_brake(bytes(m[1])))

  assert hardest < -1.0, (
    f"a MOVING car asking for -2.2 m/s^2 only got {hardest:.2f} -- the standstill floor leaked into "
    f"the approach and is now capping real braking")


@pytest.mark.parametrize("stop_override", [False, True])
def test_the_stop_override_never_takes_the_car_with_it(carcontroller_parts, stop_override):
  """The 2026-08-15 rule applied to the newest addition: anything that adds state or a call to the
  carcontroller path gets a case here, in the same commit.

  Drives the REAL CarController with the override on and off, through the full stop sequence --
  moving, slowing, stopped -- and requires it to survive. `longitudinalPlanSP` is a new subscription
  on the carcontroller's SubMaster, so this is also the only thing that would catch that message
  being absent or shaped differently than assumed.
  """
  from opendbc.car.ford.interface import CarInterface
  from opendbc.car.ford.values import CAR

  CarController, dbc_names, CP, CP_SP, structs = carcontroller_parts
  CP_long = CarInterface.get_non_essential_params(CAR.FORD_FUSION_MK5)
  CP_long.openpilotLongitudinalControl = True

  cc = CarController(dbc_names, CP_long, CP_SP)
  cc.stock_acc_passthrough = True
  cc.stop_override_enabled = stop_override

  out = structs.CarState()
  out.cruiseState.enabled = True
  out.cruiseState.available = True
  CS = FakeCarState(out)
  CS.acc_cam_valid = True

  CC, CC_SP = _car_control(structs, enabled=True,
                           send_button=structs.IntelligentCruiseButtonManagement.SendButtonState.none,
                           gap_target=0, long_active=True)

  # Walk the whole approach: highway speed, into the override's regime, down to a standstill.
  profile = [40] * 60 + [20] * 60 + [10] * 200 + [0] * 60
  accdata = 0
  for frame, mph in enumerate(profile):
    out.vEgo = mph * 0.44704
    out.vEgoRaw = out.vEgo
    out.standstill = mph == 0
    _, can_sends = cc.update(CC, CC_SP, CS, frame * 10_000_000)
    accdata += sum(1 for m in can_sends if m[0] == 390)

  assert not cc.stop_override_failed, "the stop override latched off -- it raised on the car path"
  # ACCDATA is on its own frame divider, so assert it kept flowing across the run rather than
  # guessing the divider -- an earlier version of this line guessed it and was wrong.
  assert accdata > len(profile) // 4, f"ACCDATA nearly stopped: {accdata} frames over {len(profile)}"


# --- FusionPilot: synthesized APIM GPS (0x463 / 0x464) toward the IPMA -------------------------
#
# Measured on this car: the APIM sends 0x462 3494 times a drive and these two ZERO times, which is
# the U0253 "Missing Message" the camera raises. See opendbc/sunnypilot/car/ford/apim_gps.py.

_NAV2_ADDR = 0x463
_NAV3_ADDR = 0x464


class _FakeGps:
  """Strict, like FakeCarState -- a Mock would satisfy every getattr and prove nothing."""
  hasFix = True
  latitude, longitude, altitude = 40.7608, -111.8910, 1288.0
  speed, bearingDeg = 31.3, 87.0
  horizontalAccuracy, verticalAccuracy = 3.0, 5.0
  satelliteCount = 14
  unixTimestampMillis = 1787270400000


def _run(cc, CC, CC_SP, CS, frames=250):
  sent = []
  for frame in range(frames):
    _, can_sends = cc.update(CC, CC_SP, CS, frame * 10_000_000)
    sent.extend(can_sends)
  return sent


def _addrs(can_sends):
  return [m[0] for m in can_sends]


def _gps_case(carcontroller_parts, *, enabled=True):
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
                           send_button=SendButtonState.none, gap_target=3)
  return cc, CC, CC_SP, CS


def test_the_synthesized_gps_goes_out_on_the_camera_bus(carcontroller_parts):
  """Both messages, at 1Hz, addressed to the bus the IPMA is on."""
  cc, CC, CC_SP, CS = _gps_case(carcontroller_parts)
  cc.apim_gps_enabled = True
  cc.gps = _FakeGps()

  sent = _run(cc, CC, CC_SP, CS)
  addrs = _addrs(sent)
  assert _NAV2_ADDR in addrs, "APIMGPS_Data_Nav_2 was never transmitted"
  assert _NAV3_ADDR in addrs, "APIMGPS_Data_Nav_3 was never transmitted"

  # The camera bus, not the vehicle bus. Putting these on bus 0 would talk to the gateway instead
  # of the camera, and would be a message the car does not expect from us.
  buses = {m[2] for m in sent if m[0] in (_NAV2_ADDR, _NAV3_ADDR)}
  assert buses == {cc.CAN.camera}, f"expected camera bus only, got {buses}"

  # 250 frames at 100Hz is 2.5 s, so 1Hz means 2 or 3 of each -- never one per frame.
  assert 2 <= addrs.count(_NAV2_ADDR) <= 3, addrs.count(_NAV2_ADDR)
  assert addrs.count(_NAV2_ADDR) == addrs.count(_NAV3_ADDR)


def test_it_stands_down_when_the_car_sends_the_real_ones(carcontroller_parts):
  """The whole point of the carstate watcher: never compete with a working APIM.

  If Android Auto turns out to be what suppresses them, unplugging the phone makes the car send
  them for real -- and two transmitters for one address is a worse bug than the one being fixed.
  """
  cc, CC, CC_SP, CS = _gps_case(carcontroller_parts)
  cc.apim_gps_enabled = True
  cc.gps = _FakeGps()
  CS.apim_gps_nav_seen = True

  addrs = _addrs(_run(cc, CC, CC_SP, CS))
  assert _NAV2_ADDR not in addrs
  assert _NAV3_ADDR not in addrs


def test_the_toggle_off_sends_nothing(carcontroller_parts):
  cc, CC, CC_SP, CS = _gps_case(carcontroller_parts)
  cc.apim_gps_enabled = False
  cc.gps = _FakeGps()

  addrs = _addrs(_run(cc, CC, CC_SP, CS))
  assert _NAV2_ADDR not in addrs
  assert _NAV3_ADDR not in addrs


def test_no_gps_fix_yet_sends_nothing_rather_than_raising(carcontroller_parts):
  """gpsLocationExternal has not arrived. self.gps is None and must simply mean 'not yet'."""
  cc, CC, CC_SP, CS = _gps_case(carcontroller_parts)
  cc.apim_gps_enabled = True
  cc.gps = None

  addrs = _addrs(_run(cc, CC, CC_SP, CS))
  assert _NAV2_ADDR not in addrs
  assert cc.apim_gps_failed is False, "a missing fix is not a failure and must not latch the path off"


def test_a_broken_gps_does_not_take_the_car_with_it(carcontroller_parts):
  """Same contract as the gap controller: a telemetry convenience degrades to doing nothing.

  An unhandled exception here propagates out of CarController.update, through card's control loop,
  and stops the car -- the 2026-08-15 failure, in a new place.
  """
  cc, CC, CC_SP, CS = _gps_case(carcontroller_parts)
  cc.apim_gps_enabled = True

  class ExplodingGps:
    def __getattr__(self, name):
      raise RuntimeError("boom")

  cc.gps = ExplodingGps()

  addrs = _addrs(_run(cc, CC, CC_SP, CS))   # must not raise
  assert cc.apim_gps_failed is True, "the path must latch OFF after a failure, not retry forever"
  assert _NAV2_ADDR not in addrs


def test_a_missing_attribute_disables_the_feature_not_the_car(carcontroller_parts):
  """`getattr(self, 'apim_gps_failed', True)` defaults to True on purpose.

  A merge that drops the __init__ line must silently disable the synthesizer, exactly the shape
  that took the car off the road when `self.gap` went missing.
  """
  cc, CC, CC_SP, CS = _gps_case(carcontroller_parts)
  cc.apim_gps_enabled = True
  cc.gps = _FakeGps()
  del cc.apim_gps_failed

  addrs = _addrs(_run(cc, CC, CC_SP, CS))   # must not raise
  assert _NAV2_ADDR not in addrs
