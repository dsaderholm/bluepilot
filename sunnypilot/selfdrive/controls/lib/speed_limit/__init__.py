"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
LIMIT_ADAPT_ACC = -1.  # m/s^2 Ideal acceleration for the adapting (braking) phase when approaching speed limits.
LIMIT_MAX_MAP_DATA_AGE = 10.  # s Maximum time to hold to map data, then consider it invalid inside limits controllers.
# BluePilot: cap on how far a GPS fix is extrapolated forward when working out how far the next
# speed-limit zone still is. Past this the fix is stale and dead-reckoning from it is guesswork, so
# the correction is dropped rather than trusted. Also the safety net for a bad device clock, which
# is what made this whole path dead: see _calculate_map_data_limits.
MAX_FIX_AGE_S = 2.0

# Speed Limit Assist constants
PCM_LONG_REQUIRED_MAX_SET_SPEED = {
  True: (33.3333, 36.1111),  # km/h, (120, 130)
  False: (31.2928, 35.7632),  # mph, (70, 80)
}

CONFIRM_SPEED_THRESHOLD = {
  True: 80,   # km/h
  False: 50,  # mph
}
