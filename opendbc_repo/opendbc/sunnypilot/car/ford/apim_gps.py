"""FusionPilot: synthesize the two APIM GPS messages the IPMA never receives.

WHY THIS EXISTS
---------------
The IPMA is a listed receiver of three APIM GPS messages:

    0x462  APIMGPS_Data_Nav_1_FD1   latitude / longitude
    0x463  APIMGPS_Data_Nav_2_FD1   UTC date+time, PDOP, compass, GPS fault flag
    0x464  APIMGPS_Data_Nav_3_FD1   heading, HDOP, VDOP, altitude, satellites, speed

Measured on this car over a full 27-segment drive (2026-08-21):

    0x462   3494 frames    bus0:1747  bus2:12  bus130:1735
    0x463      0 frames    NEVER SEEN ON ANY BUS
    0x464      0 frames    NEVER SEEN ON ANY BUS

One of three arrives. That is exactly what the camera reports as
`U0253 - Lost Communication With Accessory Protocol Interface Module`, additional
fault symptom "Missing Message", raised BY the IPMA.

Without them the camera never leaves `Available_CameraOnly` / `NoNavDataAvailable`
and never enters `Available_FusionMode` -- and Fusion mode is what two independent
owners report as the state in which signs are actually read (see section 4f of
bluepilot/TSR-INVESTIGATION.md). `TsrVLim1MsgTxt_D_Rq` has been 255 ("NoLimit") on
every frame of every route ever captured on this car.

The comma has its own GPS. Every field these two messages carry is already in
`gpsLocationExternal`. So we synthesize them and put them on the camera bus.

WHAT THIS IS NOT
----------------
This is telemetry to a sensor. It commands no actuator and cannot move the car.
The camera either believes it and starts reading signs, or it does not.

HONESTY ABOUT DOP
-----------------
The comma reports accuracy in metres; the Ford signals want DOP, which is unitless
geometry. There is no exact conversion -- DOP would have to come from the receiver's
satellite geometry, which we do not have. The mapping below is a documented
approximation from reported accuracy, deliberately pessimistic, and it is the one
part of this module that is a guess rather than a measurement. If the camera turns
out to gate on DOP we will see it as a persistent NoNavDataAvailable despite the
messages arriving.
"""

import datetime
import math

# Raw sentinel values, from the DBC VAL_ tables. Expressed as the PHYSICAL value the
# packer must be handed to produce that raw code, i.e. raw * scale.
DOP_SCALE = 0.2
DOP_INVALID = 31 * DOP_SCALE   # 6.2
DOP_UNKNOWN = 30 * DOP_SCALE   # 6.0
DOP_MAX_VALID = 29 * DOP_SCALE  # 5.8

SPEED_INVALID = 255
SPEED_UNKNOWN = 254
SPEED_MAX_VALID = 253

SATS_INVALID = 31
SATS_UNKNOWN = 30
SATS_MAX_VALID = 29

HEADING_SCALE = 0.01
HEADING_FAULT = 65535 * HEADING_SCALE
HEADING_UNKNOWN = 65534 * HEADING_SCALE

ALT_SCALE = 10.0
ALT_OFFSET = -20460.0
ALT_FAULT = 4095 * ALT_SCALE + ALT_OFFSET
ALT_UNKNOWN = 4094 * ALT_SCALE + ALT_OFFSET
ALT_MIN_VALID = ALT_OFFSET
ALT_MAX_VALID = 4093 * ALT_SCALE + ALT_OFFSET

# GPS_dimension
DIM_NO_FIX = 0
DIM_2D = 1
DIM_3D = 2

# UTC sentinels (raw == physical, scale 1)
UTC_YEAR_FAULT = 2010 + 31   # GpsUtcYr_No_Actl offset is 2010
UTC_MONTH_FAULT = 15
UTC_SEC_FAULT, UTC_SEC_UNKNOWN = 63, 62
UTC_MIN_FAULT, UTC_MIN_UNKNOWN = 63, 62
UTC_HOUR_INVALID, UTC_HOUR_UNKNOWN = 31, 30

METERS_TO_FEET = 3.280839895
MS_TO_MPH = 2.23693629


def _dop_from_accuracy(accuracy_m: float) -> float:
  """Approximate a dilution-of-precision figure from a reported accuracy in metres.

  Deliberately conservative: a receiver reporting sub-metre accuracy is given DOP 1.0
  (good but not implausibly perfect), and anything at or beyond 15 m saturates at the
  largest *valid* code rather than spilling into the Invalid sentinel -- a large DOP is
  a usable "poor fix" signal, whereas Invalid may make the camera discard the message
  outright.
  """
  if not math.isfinite(accuracy_m) or accuracy_m <= 0.0:
    return DOP_UNKNOWN
  dop = 1.0 + (accuracy_m / 3.0)
  return float(min(max(dop, DOP_SCALE), DOP_MAX_VALID))


def _compass_from_bearing(bearing_deg: float) -> int:
  """0..7 = N, NE, E, SE, S, SW, W, NW. 45-degree sectors centred on each point."""
  if not math.isfinite(bearing_deg):
    return 0
  return int(((bearing_deg % 360.0) + 22.5) // 45.0) % 8


def nav2_signals(gps, now_utc: datetime.datetime | None = None) -> dict:
  """Build the APIMGPS_Data_Nav_2_FD1 (0x463) signal dict.

  `gps` is a cereal GpsLocationData (gpsLocationExternal). `now_utc` overrides the
  clock derived from the fix, for tests.
  """
  has_fix = bool(getattr(gps, "hasFix", False))

  when = now_utc
  if when is None:
    ts_ms = int(getattr(gps, "unixTimestampMillis", 0) or 0)
    if ts_ms > 0:
      when = datetime.datetime.fromtimestamp(ts_ms / 1000.0, tz=datetime.UTC)

  if when is None:
    time_sigs = {
      "GpsUtcYr_No_Actl": UTC_YEAR_FAULT,
      "GpsUtcMnth_No_Actl": UTC_MONTH_FAULT,
      "GpsUtcDay_No_Actl": 1,
      "GPS_UTC_hours": UTC_HOUR_UNKNOWN,
      "GPS_UTC_minutes": UTC_MIN_UNKNOWN,
      "GPS_UTC_seconds": UTC_SEC_UNKNOWN,
    }
  else:
    # The signal is offset 2010 over 5 bits, so it cannot represent past 2040. Clamp
    # into range rather than letting the packer wrap into the Fault code.
    year = min(max(when.year, 2010), 2040)
    time_sigs = {
      "GpsUtcYr_No_Actl": year,
      "GpsUtcMnth_No_Actl": when.month,
      "GpsUtcDay_No_Actl": when.day,
      "GPS_UTC_hours": when.hour,
      "GPS_UTC_minutes": when.minute,
      "GPS_UTC_seconds": min(when.second, 59),  # never hand the packer a leap second
    }

  return {
    **time_sigs,
    "GPS_Pdop": _dop_from_accuracy(float(getattr(gps, "horizontalAccuracy", 0.0))) if has_fix else DOP_UNKNOWN,
    "GPS_Compass_direction": _compass_from_bearing(float(getattr(gps, "bearingDeg", 0.0))) if has_fix else 0,
    "GPS_Actual_vs_Infer_pos": 0 if has_fix else 1,  # 0 = Actual_Postition (sic, per DBC)
    "Gps_B_Falt": 0 if has_fix else 1,
  }


def nav3_signals(gps) -> dict:
  """Build the APIMGPS_Data_Nav_3_FD1 (0x464) signal dict."""
  has_fix = bool(getattr(gps, "hasFix", False))

  if not has_fix:
    return {
      "GPS_Vdop": DOP_UNKNOWN,
      "GPS_Hdop": DOP_UNKNOWN,
      "GPS_Speed": SPEED_UNKNOWN,
      "GPS_Sat_num_in_view": min(int(getattr(gps, "satelliteCount", 0) or 0), SATS_MAX_VALID),
      "GPS_MSL_altitude": ALT_UNKNOWN,
      "GPS_Heading": HEADING_UNKNOWN,
      "GPS_dimension": DIM_NO_FIX,
    }

  alt_ft = float(getattr(gps, "altitude", 0.0)) * METERS_TO_FEET
  if not math.isfinite(alt_ft):
    alt_ft = ALT_UNKNOWN
  else:
    alt_ft = min(max(alt_ft, ALT_MIN_VALID), ALT_MAX_VALID)

  speed_mph = float(getattr(gps, "speed", 0.0)) * MS_TO_MPH
  if not math.isfinite(speed_mph) or speed_mph < 0.0:
    speed_mph = SPEED_UNKNOWN
  else:
    speed_mph = min(speed_mph, SPEED_MAX_VALID)

  bearing = float(getattr(gps, "bearingDeg", 0.0))
  heading = (bearing % 360.0) if math.isfinite(bearing) else HEADING_UNKNOWN

  v_acc = float(getattr(gps, "verticalAccuracy", 0.0))
  h_acc = float(getattr(gps, "horizontalAccuracy", 0.0))

  # A vertical accuracy of zero means the receiver did not report one, not that the
  # fix is perfect -- fall back to 2D rather than claiming a 3D fix we cannot support.
  is_3d = math.isfinite(v_acc) and v_acc > 0.0

  return {
    "GPS_Vdop": _dop_from_accuracy(v_acc) if is_3d else DOP_UNKNOWN,
    "GPS_Hdop": _dop_from_accuracy(h_acc),
    "GPS_Speed": speed_mph,
    "GPS_Sat_num_in_view": min(int(getattr(gps, "satelliteCount", 0) or 0), SATS_MAX_VALID),
    "GPS_MSL_altitude": alt_ft,
    "GPS_Heading": heading,
    "GPS_dimension": DIM_3D if is_3d else DIM_2D,
  }
