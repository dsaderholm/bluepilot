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
  assert CarControllerParams.ACCEL_MIN < fordcan_ext._PANDA_ACCEL_MIN, (
    "ACCEL_MIN is no longer below the panda floor; if these were reconciled upstream, this test "
    "and the clamp it defends should be revisited together")


def test_a_full_braking_request_stays_inside_the_band():
  """THE REPORTED FAILURE: openpilot asks for its own ACCEL_MIN and panda must accept it."""
  out = _accel_on_the_wire(CarControllerParams.ACCEL_MIN)
  assert out >= fordcan_ext._PANDA_ACCEL_MIN, (
    f"a full-braking frame went out at {out:.4f}, below panda's {fordcan_ext._PANDA_ACCEL_MIN} "
    "-- panda drops these, which is 15 lost frames per stop")


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
