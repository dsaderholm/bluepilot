"""FusionPilot: the synthesized GPS must not tell the camera the fix is unknowable.

Route 000003ba, 2026-08-24, driven with `FordSynthesizeApimGps` ON. 0x463 and 0x464 went out on
893 frames each -- the feature worked -- and the camera still reported `NoNavDataAvailable` on 96%
of frames and stayed in `Available_CameraOnly`. It accepted 4%, so it was reading them.

What it was being told, across all 894 gpsLocation samples with hasFix true on every one:

    horizontalAccuracy   0.0 on 894/894      -> GPS_Pdop and GPS_Hdop both DOP_UNKNOWN
    satelliteCount       0   on 894/894      -> GPS_Sat_num_in_view literally 0
    verticalAccuracy     1.06 .. 1.37 m      -> the one real quality figure, and unused for H

A position with unknown precision and zero satellites is a contradiction. These tests pin the two
substitutions: fall back to the accuracy the receiver DOES report, and say "not reported" rather
than "zero" for satellites.

NOT A PROVEN FIX for the read rate. It removes a well-evidenced reason for the camera to decline;
whether it moves TsrStat to Available_FusionMode is the next drive's measurement.
"""
from types import SimpleNamespace as NS

from opendbc.sunnypilot.car.ford import apim_gps
from opendbc.sunnypilot.car.ford.apim_gps import (
  DOP_UNKNOWN, SATS_UNKNOWN, _best_accuracy, nav2_signals, nav3_signals,
)

# His device, exactly as measured.
HIS = NS(hasFix=True, horizontalAccuracy=0.0, verticalAccuracy=1.2, satelliteCount=0,
         bearingDeg=90.0, bearingAccuracyDeg=1.0, altitude=1350.0, speed=20.0,
         unixTimestampMillis=1788000000000, flags=0)


def test_it_falls_back_to_the_accuracy_the_receiver_reports():
  assert _best_accuracy(HIS) == 1.2


def test_a_real_horizontal_accuracy_still_wins():
  """The fallback must not displace a receiver that does report horizontal accuracy."""
  gps = NS(horizontalAccuracy=0.8, verticalAccuracy=1.2)
  assert _best_accuracy(gps) == 0.8


def test_neither_reported_is_still_unknown():
  """No invented number when there is no measurement at all."""
  assert _best_accuracy(NS(horizontalAccuracy=0.0, verticalAccuracy=0.0)) == 0.0


def test_pdop_is_no_longer_unknown_on_his_device():
  """THE REPORTED CONDITION: 894/894 frames carried DOP_UNKNOWN."""
  pdop = nav2_signals(HIS)["GPS_Pdop"]
  assert pdop != DOP_UNKNOWN, "still telling the camera the fix quality is unknown"
  assert 0 < pdop < DOP_UNKNOWN


def test_hdop_is_no_longer_unknown_on_his_device():
  hdop = nav3_signals(HIS)["GPS_Hdop"]
  assert hdop != DOP_UNKNOWN
  assert 0 < hdop < DOP_UNKNOWN


def test_the_substitution_is_conservative():
  """Vertical accuracy is worse than horizontal, so the reported DOP must not be optimistic."""
  optimistic = apim_gps._dop_from_accuracy(0.5)
  ours = nav3_signals(HIS)["GPS_Hdop"]
  assert ours > optimistic, "the fallback is claiming a better fix than a sub-metre receiver"


def test_zero_satellites_is_reported_as_not_reported():
  """A valid fix with zero satellites is a contradiction; SATS_UNKNOWN is the DBC's own code."""
  assert nav3_signals(HIS)["GPS_Sat_num_in_view"] == SATS_UNKNOWN


def test_a_real_satellite_count_is_passed_through():
  gps = NS(hasFix=True, horizontalAccuracy=1.0, verticalAccuracy=1.2, satelliteCount=11,
           bearingDeg=90.0, altitude=1350.0, speed=20.0, unixTimestampMillis=1788000000000)
  assert nav3_signals(gps)["GPS_Sat_num_in_view"] == 11


def test_no_fix_still_reports_unknown_everything():
  """The fallback must not manufacture confidence out of a receiver with no fix."""
  gps = NS(hasFix=False, horizontalAccuracy=0.0, verticalAccuracy=1.2, satelliteCount=0)
  assert nav2_signals(gps)["GPS_Pdop"] == DOP_UNKNOWN
  assert nav2_signals(gps)["Gps_B_Falt"] == 1
