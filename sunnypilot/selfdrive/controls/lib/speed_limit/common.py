"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

from openpilot.sunnypilot import IntEnumBase


class Policy(IntEnumBase):
  car_state_only = 0
  map_data_only = 1
  car_state_priority = 2
  map_data_priority = 3
  combined = 4


class OffsetType(IntEnumBase):
  off = 0
  fixed = 1
  percentage = 2
  # BluePilot: one offset per speed band, because that is how people actually drive. A single
  # number is wrong at both ends -- +10 is reckless in a 25 and pointless on a freeway -- and the
  # percentage option has the same problem in a less obvious form, which is why nobody uses it.
  bySpeed = 3


class Mode(IntEnumBase):
  off = 0
  information = 1
  warning = 2
  assist = 3
