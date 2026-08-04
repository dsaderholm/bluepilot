"""BluePilot: the message lists Ford's CarState hands to CANParser must not repeat an address.

CANParser raises RuntimeError("Duplicate Message Check: %d") when it does, at car init, which
takes card down and leaves the device stuck on "waiting to start" with no way to drive.

That happened for real: an upstream merge added a Traffic_RecognitnData registration inside the
non-CANFD branch while this fork already had a TSR-flag-gated one below it. Both fired on a
non-CANFD car carrying the TSR flag and the car would not start.

Nothing caught it. The existing platform test calls CarInterface.get_params, which never builds
the parsers, and get_can_parsers itself could not be exercised offline because CANParser needs the
compiled extension and the real DBC. So stub both and assert on the lists it asked for -- the
duplicate is visible in the arguments, well before anything has to parse CAN.
"""
import itertools
from types import SimpleNamespace as NS
from unittest import mock

import pytest

from opendbc.car import structs
from opendbc.car.ford.values import FordFlags

TransmissionType = structs.CarParams.TransmissionType


ALL_FLAGS = [FordFlags.CANFD, FordFlags.ALT_STEER_ANGLE, FordFlags.HEV_CLUSTER_DATA,
             FordFlags.HEV_BATTERY_DATA, FordFlags.TSR]


def _flag_combinations():
  """Every subset. There are only 5 flags, so 32 cases is cheap and leaves no gap for the next
  conditional message block to hide in."""
  for size in range(len(ALL_FLAGS) + 1):
    for combo in itertools.combinations(ALL_FLAGS, size):
      value = 0
      for flag in combo:
        value |= flag
      yield value


def _collect_messages(flags, enable_bsm, transmission=None):
  """Call the real get_can_parsers with CANParser and DBC stubbed, and return what it asked for."""
  from opendbc.car.ford import carstate

  captured = []

  def fake_can_parser(_dbc, messages, _bus):
    captured.append(list(messages))
    return object()

  CP = NS(carFingerprint="FORD_FUSION_MK5", flags=flags, enableBsm=enable_bsm,
          transmissionType=transmission or TransmissionType.automatic)
  CP_SP = NS()

  with mock.patch.object(carstate, "CANParser", fake_can_parser), \
       mock.patch.object(carstate, "DBC", {"FORD_FUSION_MK5": {"pt": "ford_lincoln_base_pt"}}), \
       mock.patch.object(carstate, "CanBus", lambda _cp: NS(main=0, camera=2)):
    carstate.CarState.get_can_parsers(CP, CP_SP)

  assert len(captured) == 2, "expected a pt parser and a cam parser"
  return captured


@pytest.mark.parametrize("flags", list(_flag_combinations()))
@pytest.mark.parametrize("enable_bsm", [False, True])
@pytest.mark.parametrize("transmission", [TransmissionType.automatic, TransmissionType.manual])
def test_no_duplicate_messages(flags, enable_bsm, transmission):
  for messages in _collect_messages(flags, enable_bsm, transmission):
    names = [name for name, _freq in messages]
    duplicates = {n for n in names if names.count(n) > 1}
    assert not duplicates, (
      f"flags={int(flags)} enableBsm={enable_bsm}: {sorted(duplicates)} registered more than once "
      f"-- CANParser raises RuntimeError and the car will not start"
    )


def test_tsr_message_present_when_the_flag_is_set():
  """The flag comes from the camera fingerprint, not the platform, so it has to be honored on
  CANFD too -- deleting the flag-gated branch to fix the duplicate would have broken that."""
  for flags in (FordFlags.TSR, FordFlags.TSR | FordFlags.CANFD):
    _pt, cam = _collect_messages(flags, enable_bsm=False)
    assert "Traffic_RecognitnData" in [name for name, _ in cam], f"missing for flags={int(flags)}"


def test_tsr_message_still_present_on_plain_non_canfd():
  """Upstream registers it for every non-CANFD Ford regardless of the flag; keep that."""
  _pt, cam = _collect_messages(FordFlags.ALT_STEER_ANGLE, enable_bsm=False)
  assert "Traffic_RecognitnData" in [name for name, _ in cam]


def test_canfd_without_tsr_does_not_get_it():
  _pt, cam = _collect_messages(FordFlags.CANFD, enable_bsm=False)
  assert "Traffic_RecognitnData" not in [name for name, _ in cam]
