"""FusionPilot: the PSCM marking its own steering angle invalid must stop openpilot steering.

Route 000004b7, 2026-09-28 5:26 PM MDT. openpilot was steering a left turn hands-off at 17 mph with
the wheel at 92 deg when the PSCM set StePinRelInit_An_Sns (0x085) to all-ones -- its own "this
angle is invalid" -- and logged C1B00 (steering angle sensor, signal compare failure). The ABS then
faulted on invalid PSCM data (U0420 / C0051) and cruise went with it.

Nothing on openpilot's side noticed. EPAS_Failure stayed 0, and on ALT_STEER_ANGLE cars the angle
openpilot uses is the CAMERA's copy (ParkAid_Data.ExtSteeringAngleReq2), which froze at 92.2 deg
instead of going invalid. Lateral stayed off only because the ABS fault took cruise down too.

These frames are his car's, byte for byte, fed through the REAL parser and the REAL CarState --
the parser's checksum and counter handling are part of what is being tested, not stubbed.
"""
from __future__ import annotations

import pytest

# Recorded on 000004b7 / 000004bc. Each is one SteeringPinion_Data_Alt (0x085) frame.
HEALTHY_MID_TURN = bytes.fromhex("8125800050007d8c")   # t+404.501, rel +106.1 deg, 50 ms before
FAULT_FIRST = bytes.fromhex("ffff8000f2607d8c")        # t+404.560, the frame it went invalid
FAULT_STEADY = bytes.fromhex("ffff8000fd70fffe")       # t+404.570 on, and both restarts after
CLEAN_BOOT = bytes.fromhex("7d0000007ba0fffe")         # 000004bc first frame: rel 0, offset unknown

ADDR = 0x085


@pytest.fixture(scope="module")
def parts():
  from opendbc.car import Bus, structs
  from opendbc.car.ford.carstate import CarState
  from opendbc.car.ford.interface import CarInterface
  from opendbc.car.ford.values import CAR
  CP = CarInterface.get_non_essential_params(CAR.FORD_FUSION_MK5)
  CP_SP = structs.CarParamsSP()
  return CarState, CP, CP_SP, Bus


def _run(parts, frame: bytes):
  CarState, CP, CP_SP, Bus = parts
  cs = CarState(CP, CP_SP)
  parsers = CarState.get_can_parsers(CP, CP_SP)
  pt = parsers[Bus.pt]
  updated = pt.update([(1_000_000_000, [(ADDR, frame, pt.bus)])])
  assert ADDR in updated, "the real parser rejected a frame his car actually sent"
  ret, _ = cs.update(parsers)
  return ret, pt


def test_his_car_has_the_flag_this_depends_on(parts):
  from opendbc.car.ford.values import FordFlags
  _, CP, _, _ = parts
  assert CP.flags & FordFlags.ALT_STEER_ANGLE


def test_a_healthy_mid_turn_frame_is_not_a_fault(parts):
  ret, pt = _run(parts, HEALTHY_MID_TURN)
  assert abs(pt.vl["SteeringPinion_Data_Alt"]["StePinRelInit_An_Sns"] - 106.1) < 0.05
  assert not ret.steerFaultPermanent


@pytest.mark.parametrize("frame", [FAULT_FIRST, FAULT_STEADY], ids=["first", "steady"])
def test_the_pscm_invalid_marker_is_a_permanent_steer_fault(parts, frame):
  ret, pt = _run(parts, frame)
  assert pt.vl["SteeringPinion_Data_Alt"]["StePinRelInit_An_Sns"] > 3353.4
  assert ret.steerFaultPermanent, "openpilot kept steering on an angle the PSCM had declared invalid"


def test_a_clean_startup_is_not_a_fault(parts):
  """Every clean boot on record starts with rel 0 and the offset bytes still FFFE -- that offset
  marker alone is normal and must not trip anything. Only the all-ones ANGLE is the fault."""
  ret, _ = _run(parts, CLEAN_BOOT)
  assert not ret.steerFaultPermanent


def test_the_threshold_sits_between_the_last_valid_count_and_the_marker():
  from opendbc.car.ford.carstate import PSCM_ANGLE_INVALID_DEG
  last_valid = 65533 * 0.1 - 3200    # the DBC's own maximum, 3353.3
  marker = 65535 * 0.1 - 3200        # 3353.5
  assert last_valid < PSCM_ANGLE_INVALID_DEG < marker
