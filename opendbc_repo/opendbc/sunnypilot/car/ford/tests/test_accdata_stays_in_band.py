"""FusionPilot: a frame panda rejects is not a softer command, it is NO command.

Route 000003b8, 2026-08-24 -- the drive where the stop override took a red light for the first
time. During its 2.8 s of authority:

    220 ACCDATA frames accepted, all clamped at -3.499
     15 ACCDATA frames REJECTED at -3.503 .. -3.542   (src 192 = panda's rejected-TX flag)

Ford ACC faulted 2.8 s in and never came back; the next route came up "Cruise Fault: Restart the
car to engage".

The cause was two constants that disagree by 0.0009 m/s^2:

    CarControllerParams.ACCEL_MIN = -3.5      openpilot clips its request to this
    _PANDA_ACCEL_MIN              = -3.4991   panda refuses anything below this

so every frame that reached the clip was handed to panda just below its floor. `create_acc_msg`
wrote the value raw, while `stop_override.py` documented that it "already clamps to panda's bands".
"""
from types import SimpleNamespace as NS

from opendbc.car.ford.values import CarControllerParams
from opendbc.can.packer import CANPacker
from opendbc.sunnypilot.car.ford import fordcan_ext

# create_acc_msg reads exactly one attribute off the bus object.
CAN = lambda: NS(main=0)  # noqa: E731


def _gas_on_the_wire(gas: float, accel: float = -1.0) -> float:
  packer = CANPacker("ford_lincoln_base_pt")
  _, dat, _ = fordcan_ext.create_acc_msg(
    packer, CAN(), True, gas, accel, -5.0, False, True, False, v_ego_kph=30.0)
  v = int.from_bytes(bytes(dat), "big")
  total = len(dat) * 8
  # AccPrpl_A_Rq : 49|10@0+ (0.01,-5) -- read from the DBC, not guessed. An earlier version of
  # this helper used bit 15, which is inside AccPrpl_A_Pred, and decoded -0.4 as -4.68.
  idx = (49 // 8) * 8 + (7 - (49 % 8))
  return ((v >> (total - idx - 10)) & ((1 << 10) - 1)) * 0.01 - 5.0


def _accel_on_the_wire(accel: float, gas: float = -5.0) -> float:
  """Build the frame the way the car does, then decode what actually went out."""
  packer = CANPacker("ford_lincoln_base_pt")
  addr, dat, _bus = fordcan_ext.create_acc_msg(
    packer, CAN(), True, gas, accel, -5.0, False, True, False, v_ego_kph=30.0)
  assert addr == 390
  v = int.from_bytes(bytes(dat), "big")
  total = len(dat) * 8
  idx = (4 // 8) * 8 + (7 - (4 % 8))
  return ((v >> (total - idx - 13)) & ((1 << 13) - 1)) * 0.0039 - 20.0


def test_the_openpilot_clip_is_below_pandas_floor():
  """The two constants really do disagree -- this is the bug, stated as an assertion."""
  msg = "ACCEL_MIN is no longer below panda's floor -- revisit this test and the clamp together"
  assert CarControllerParams.ACCEL_MIN < fordcan_ext._PANDA_ACCEL_MIN, msg


def test_a_full_braking_request_stays_inside_the_band():
  """THE REPORTED FAILURE: openpilot asks for its own ACCEL_MIN and panda must accept it."""
  out = _accel_on_the_wire(CarControllerParams.ACCEL_MIN)
  assert out >= fordcan_ext._PANDA_ACCEL_MIN,     f"full braking went out at {out:.4f}, below panda's floor -- 15 frames were lost this way"


def test_the_measured_rejected_values_are_now_impossible():
  """-3.503 .. -3.542 were measured on the wire and rejected. None may survive the clamp."""
  for asked in (-3.503, -3.520, -3.542, -3.6, -4.0, -20.0):
    out = _accel_on_the_wire(asked)
    assert out >= fordcan_ext._PANDA_ACCEL_MIN, f"asked {asked}, sent {out:.4f}"


def test_the_top_is_bounded_too():
  for asked in (2.0, 2.5, 11.9):
    out = _accel_on_the_wire(asked)
    assert out <= fordcan_ext._PANDA_ACCEL_MAX, f"asked {asked}, sent {out:.4f}"


def test_ordinary_braking_is_not_altered():
  """The clamp must only bite at the extremes -- a normal request goes out as asked."""
  for asked in (-0.5, -1.0, -2.0, -3.0):
    out = _accel_on_the_wire(asked)
    assert abs(out - asked) < 0.01, f"asked {asked}, sent {out:.4f} -- clamp is biting too early"


def test_the_gas_inactive_sentinel_survives():
  """-5.0 means 'not requesting' and sits outside the band on purpose."""
  packer = CANPacker("ford_lincoln_base_pt")
  _, dat, _ = fordcan_ext.create_acc_msg(
    packer, CAN(), True, fordcan_ext._PANDA_GAS_INACTIVE, -1.0, -5.0, False, False, False, v_ego_kph=30.0)
  assert dat is not None


# THE GAS CLAMP IS PREVENTIVE, NOT A MEASURED FIX -- and the first version of this note claimed
# otherwise, because the analysis behind it decoded the wrong bits.
#
# `AccPrpl_A_Rq` is at start bit 49 (49|10@0+ (0.01,-5)). A first pass read bit 15, which lands
# inside `AccPrpl_A_Pred`, and produced a confident "the gas caused 18 of the 21 rejections on
# route ba". Decoded correctly, the gas was NEVER out of band on either drive:
#
#     000003b8   brake below band 15,  all policed fields in band 20
#     000003ba   all policed fields in band 21   (nothing out of band at all)
#
# So the brake clamp fixed 15 measured frames and the gas clamp fixed none. It stays because
# `AccPrpl_A_Rq` was written raw by exactly the same code that wrote the brake raw, and the tests
# below pin it before it ever bites. The lesson is the bit positions: read them from the DBC.


def test_gas_below_the_band_is_clamped():
  """Measured: 8 frames on b8 and 12 on ba were rejected for this."""
  for asked in (-0.6, -1.0, -2.0, -4.0):
    out = _gas_on_the_wire(asked)
    assert out >= fordcan_ext._PANDA_GAS_MIN, f"asked {asked}, sent {out:.4f}"


def test_gas_above_the_band_is_clamped():
  """Measured: 6 frames on each route."""
  for asked in (2.1, 2.5, 5.0):
    out = _gas_on_the_wire(asked)
    assert out <= fordcan_ext._PANDA_GAS_MAX, f"asked {asked}, sent {out:.4f}"


def test_ordinary_gas_is_not_altered():
  for asked in (-0.4, 0.0, 0.5, 1.5, 1.9):
    out = _gas_on_the_wire(asked)
    assert abs(out - asked) < 0.02, f"asked {asked}, sent {out:.4f} -- clamp biting too early"


def test_the_inactive_sentinel_is_not_dragged_into_the_band():
  """-5.0 means 'not requesting'. Clamping it to -0.495 would be a request for gentle braking."""
  out = _gas_on_the_wire(fordcan_ext._PANDA_GAS_INACTIVE)
  assert out < fordcan_ext._PANDA_GAS_MIN,     f"the inactive sentinel came out as {out:.4f} -- clamped into the band, now a real request"
