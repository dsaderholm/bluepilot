"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

BluePilot: adding a car platform means registering it in several unrelated files, and missing one
does not fail at import -- it fails inside card at car init, on the vehicle.

FORD_FUSION_MK5 shipped without an entry in torque_data/override.toml. Everything imported, the
platform resolved, its fingerprint and CarSpecs were correct, and the tests passed. Then
get_std_params did get_torque_params()[candidate]['MAX_LAT_ACCEL_MEASURED'] and card died with a
KeyError at every boot.

So this exercises the real entry point, CarInterface.get_params, rather than checking a list of
files someone has to remember to update.
"""

import pytest

from opendbc.car.ford.interface import CarInterface
from opendbc.car.ford.values import CAR, FordFlags
from opendbc.car.interfaces import get_torque_params
from opendbc.car.structs import CarParams

Ecu = CarParams.Ecu

# A plausible Q3 Ford bus. Individual platforms may not match all of it; get_params must still
# complete rather than raise, which is the only thing under test here.
FINGERPRINT = {
  0: {0x083: 8, 0x091: 8, 0x165: 8, 0x213: 8, 0x357: 8, 0x3A6: 8, 0x3A7: 8, 0x5A: 8},
  1: {},
  2: {0x3CD: 8, 0x186: 8, 0x3B3: 8, 0x3D6: 8},
}

FW = [
  (Ecu.eps, 0x730, b'K2GC-14D003-AH'),
  (Ecu.abs, 0x760, b'KG9C-2D053-MD'),
  (Ecu.fwdRadar, 0x764, b'JX7T-14D049-AC'),
  (Ecu.fwdCamera, 0x706, b'KT4T-14F397-AE'),
]


def _car_fw():
  out = []
  for ecu, addr, ver in FW:
    f = CarParams.CarFw()
    f.ecu, f.address, f.fwVersion = ecu, addr, ver + b'\x00' * (24 - len(ver))
    f.brand, f.subAddress, f.logging = 'ford', 0, False
    out.append(f)
  return out


@pytest.mark.parametrize("platform", list(CAR), ids=lambda p: str(p))
class TestFordPlatformRegistration:
  def test_get_params_does_not_raise(self, platform):
    """THE REGRESSION. A platform missing any per-platform registration dies here, not at import."""
    CP = CarInterface.get_params(platform, FINGERPRINT, _car_fw(),
                                 alpha_long=False, is_release=False, docs=False)
    assert CP.carFingerprint == str(platform)
    assert CP.mass > 0 and CP.wheelbase > 0 and CP.steerRatio > 0

  def test_has_torque_params(self, platform):
    """The specific miss: get_std_params indexes this dict directly and KeyErrors without it."""
    assert platform in get_torque_params(), \
      f"{platform} missing from opendbc/car/torque_data/*.toml"

  def test_pinion_geometry_matches_alt_steer_angle(self, platform):
    """ALT_STEER_ANGLE platforms read a RELATIVE pinion angle and must have no geometry row;
    everything else needs one, or the safety param would select the wrong car's geometry."""
    from opendbc.sunnypilot.car.ford.values_ext import FORD_PINION_GEOMETRY_INDEX
    alt = bool(platform.config.flags & FordFlags.ALT_STEER_ANGLE)
    has_row = platform in FORD_PINION_GEOMETRY_INDEX
    assert alt != has_row, (
      f"{platform}: ALT_STEER_ANGLE={alt} but pinion geometry row present={has_row}. "
      "ALT_STEER_ANGLE platforms must be excluded; all others must be listed."
    )
