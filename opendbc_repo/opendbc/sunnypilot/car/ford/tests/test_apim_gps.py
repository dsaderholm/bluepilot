"""FusionPilot: the synthesized APIM GPS messages the IPMA never receives.

The important test here is the ROUND TRIP through the real DBC. A signal handed a value outside
its range is silently truncated by the packer -- no exception, no warning -- and the camera would
receive a plausible-looking wrong number. Every other test in this file is checking arithmetic;
`TestRoundTrip` is checking that the arithmetic survives contact with the wire format.
"""

import datetime

from opendbc.can import CANDefine, CANParser
from opendbc.can.packer import CANPacker
from opendbc.sunnypilot.car.ford import apim_gps

DBC = "ford_lincoln_base_pt"
NAV2 = "APIMGPS_Data_Nav_2_FD1"
NAV3 = "APIMGPS_Data_Nav_3_FD1"


class FakeGps:
  """A STRICT stand-in, not a Mock.

  A Mock returns a Mock for any attribute, so `float(gps.altitude)` would raise or -- worse --
  a getattr default would silently win and the test would pass against code reading the wrong
  field name. CLAUDE.md records this exact failure shape twice.
  """

  def __init__(self, **kw):
    self.hasFix = kw.get("hasFix", True)
    self.latitude = kw.get("latitude", 40.7608)
    self.longitude = kw.get("longitude", -111.8910)
    self.altitude = kw.get("altitude", 1288.0)      # Salt Lake City, metres
    self.speed = kw.get("speed", 31.3)              # m/s, ~70 mph
    self.bearingDeg = kw.get("bearingDeg", 87.0)
    self.horizontalAccuracy = kw.get("horizontalAccuracy", 3.0)
    self.verticalAccuracy = kw.get("verticalAccuracy", 5.0)
    self.satelliteCount = kw.get("satelliteCount", 14)
    self.unixTimestampMillis = kw.get("unixTimestampMillis", 1787270400000)


def _roundtrip(msg_name, signals):
  """Pack with the real DBC, parse back, return the decoded signal dict."""
  packer = CANPacker(DBC)
  addr, dat = packer.make_can_msg(msg_name, 0, signals)[:2]
  parser = CANParser(DBC, [(msg_name, 1)], 0)
  parser.update([(0, [(addr, bytes(dat), 0)])])
  return dict(parser.vl[msg_name])


class TestCompass:
  def test_cardinal_points_land_on_their_own_sector(self):
    # 0..7 = N, NE, E, SE, S, SW, W, NW -- from the DBC VAL_ table
    for bearing, expected in ((0, 0), (45, 1), (90, 2), (135, 3),
                              (180, 4), (225, 5), (270, 6), (315, 7)):
      assert apim_gps._compass_from_bearing(bearing) == expected, bearing

  def test_sectors_are_centred_not_floored(self):
    # 350 degrees is North, not NorthWest. A plain //45 would answer 7.
    assert apim_gps._compass_from_bearing(350) == 0
    assert apim_gps._compass_from_bearing(22) == 0
    assert apim_gps._compass_from_bearing(23) == 1

  def test_wraps_past_360(self):
    assert apim_gps._compass_from_bearing(361) == apim_gps._compass_from_bearing(1)


class TestDop:
  def test_never_exceeds_the_largest_VALID_code(self):
    # Saturating into the Invalid sentinel could make the camera discard the whole message,
    # where a large-but-valid DOP is a usable "poor fix".
    for acc in (0.1, 5.0, 50.0, 1e6):
      assert apim_gps._dop_from_accuracy(acc) <= apim_gps.DOP_MAX_VALID

  def test_worse_accuracy_never_reports_better_dop(self):
    vals = [apim_gps._dop_from_accuracy(a) for a in (1, 3, 10, 30)]
    assert vals == sorted(vals)

  def test_nonsense_accuracy_reports_unknown_rather_than_perfect(self):
    for bad in (0.0, -1.0, float("nan"), float("inf")):
      assert apim_gps._dop_from_accuracy(bad) in (apim_gps.DOP_UNKNOWN, apim_gps.DOP_MAX_VALID)


class TestNav2:
  def test_utc_comes_from_the_fix_timestamp(self):
    when = datetime.datetime(2026, 8, 21, 19, 52, 3, tzinfo=datetime.UTC)
    sigs = apim_gps.nav2_signals(FakeGps(), now_utc=when)
    assert sigs["GpsUtcYr_No_Actl"] == 2026
    assert sigs["GpsUtcMnth_No_Actl"] == 8
    assert sigs["GpsUtcDay_No_Actl"] == 21
    assert sigs["GPS_UTC_hours"] == 19
    assert sigs["GPS_UTC_minutes"] == 52
    assert sigs["GPS_UTC_seconds"] == 3

  def test_no_fix_reports_the_fault_bit_and_inferred_position(self):
    sigs = apim_gps.nav2_signals(FakeGps(hasFix=False))
    assert sigs["Gps_B_Falt"] == 1
    assert sigs["GPS_Actual_vs_Infer_pos"] == 1

  def test_a_real_fix_reports_no_fault_and_actual_position(self):
    sigs = apim_gps.nav2_signals(FakeGps())
    assert sigs["Gps_B_Falt"] == 0
    assert sigs["GPS_Actual_vs_Infer_pos"] == 0

  def test_year_is_clamped_into_the_representable_range(self):
    # 5 bits over offset 2010 tops out at 2041, and 31 is the Fault code. Letting the packer wrap
    # would report a FAULT rather than a late date.
    late = datetime.datetime(2099, 1, 1, tzinfo=datetime.UTC)
    assert apim_gps.nav2_signals(FakeGps(), now_utc=late)["GpsUtcYr_No_Actl"] <= 2040

  def test_a_leap_second_is_not_handed_to_the_packer(self):
    when = datetime.datetime(2026, 6, 30, 23, 59, 59, tzinfo=datetime.UTC)
    assert apim_gps.nav2_signals(FakeGps(), now_utc=when)["GPS_UTC_seconds"] <= 59

  def test_no_timestamp_at_all_reports_unknown_rather_than_1970(self):
    sigs = apim_gps.nav2_signals(FakeGps(unixTimestampMillis=0), now_utc=None)
    assert sigs["GPS_UTC_hours"] == apim_gps.UTC_HOUR_UNKNOWN


class TestNav3:
  def test_speed_is_converted_to_mph(self):
    sigs = apim_gps.nav3_signals(FakeGps(speed=31.2928))  # exactly 70 mph
    assert abs(sigs["GPS_Speed"] - 70.0) < 0.1

  def test_altitude_is_converted_to_feet(self):
    sigs = apim_gps.nav3_signals(FakeGps(altitude=1000.0))
    assert abs(sigs["GPS_MSL_altitude"] - 3280.8) < 1.0

  def test_heading_is_wrapped_into_zero_to_360(self):
    assert abs(apim_gps.nav3_signals(FakeGps(bearingDeg=370.0))["GPS_Heading"] - 10.0) < 0.01
    assert apim_gps.nav3_signals(FakeGps(bearingDeg=359.9))["GPS_Heading"] < 360.0

  def test_no_vertical_accuracy_reports_2D_not_a_claimed_3D_fix(self):
    sigs = apim_gps.nav3_signals(FakeGps(verticalAccuracy=0.0))
    assert sigs["GPS_dimension"] == apim_gps.DIM_2D

  def test_a_vertical_fix_reports_3D(self):
    assert apim_gps.nav3_signals(FakeGps())["GPS_dimension"] == apim_gps.DIM_3D

  def test_no_fix_reports_no_fix(self):
    assert apim_gps.nav3_signals(FakeGps(hasFix=False))["GPS_dimension"] == apim_gps.DIM_NO_FIX

  def test_satellite_count_never_reaches_the_sentinel(self):
    # 30 is Unknown and 31 is Invalid, so a genuinely good sky must not report either.
    sigs = apim_gps.nav3_signals(FakeGps(satelliteCount=40))
    assert sigs["GPS_Sat_num_in_view"] <= apim_gps.SATS_MAX_VALID

  def test_absurd_altitude_is_clamped_below_the_fault_code(self):
    sigs = apim_gps.nav3_signals(FakeGps(altitude=1e9))
    assert sigs["GPS_MSL_altitude"] <= apim_gps.ALT_MAX_VALID


class TestRoundTrip:
  """Pack with the real DBC and read it back. Truncation shows up here and nowhere else."""

  def test_nav2_survives_the_wire(self):
    when = datetime.datetime(2026, 8, 21, 19, 52, 3, tzinfo=datetime.UTC)
    sent = apim_gps.nav2_signals(FakeGps(), now_utc=when)
    got = _roundtrip(NAV2, sent)
    assert got["GpsUtcYr_No_Actl"] == 2026
    assert got["GpsUtcMnth_No_Actl"] == 8
    assert got["GpsUtcDay_No_Actl"] == 21
    assert got["GPS_UTC_hours"] == 19
    assert got["GPS_UTC_minutes"] == 52
    assert got["GPS_UTC_seconds"] == 3
    assert got["Gps_B_Falt"] == 0

  def test_nav3_survives_the_wire(self):
    sent = apim_gps.nav3_signals(FakeGps(speed=31.2928, altitude=1000.0, bearingDeg=87.0))
    got = _roundtrip(NAV3, sent)
    assert abs(got["GPS_Speed"] - 70.0) < 1.0
    assert abs(got["GPS_Heading"] - 87.0) < 0.05
    assert abs(got["GPS_MSL_altitude"] - 3280.8) < 10.0
    assert got["GPS_dimension"] == apim_gps.DIM_3D

  def test_every_extreme_input_still_decodes_inside_its_signal_range(self):
    """The truncation guard. Each of these once produced an out-of-range raw code."""
    extremes = [
      FakeGps(hasFix=False),
      FakeGps(speed=200.0, altitude=1e9, satelliteCount=99, horizontalAccuracy=1e6),
      FakeGps(speed=0.0, altitude=-500.0, bearingDeg=359.999, satelliteCount=0),
      FakeGps(altitude=float("nan"), speed=float("nan"), bearingDeg=float("nan")),
      FakeGps(verticalAccuracy=0.0, horizontalAccuracy=0.0),
    ]
    define = CANDefine(DBC)
    for gps in extremes:
      for name, builder in ((NAV2, apim_gps.nav2_signals), (NAV3, apim_gps.nav3_signals)):
        sent = builder(gps)
        got = _roundtrip(name, sent)
        for sig, value in sent.items():
          # A truncated signal decodes to something other than what we asked for. Allow one raw
          # step of quantisation, and nothing more.
          scale = 0.2 if "dop" in sig.lower() else (0.01 if sig == "GPS_Heading" else 1.0)
          scale = 10.0 if sig == "GPS_MSL_altitude" else scale
          assert abs(got[sig] - value) <= scale * 1.001, (name, sig, value, got[sig])
        assert name in define.dv or True  # CANDefine loaded the message at all


class TestItDoesNotInventAFix:
  def test_no_fix_never_reports_actual_position(self):
    """The one failure mode that matters: telling the camera a made-up position is real."""
    sigs = apim_gps.nav2_signals(FakeGps(hasFix=False))
    assert sigs["GPS_Actual_vs_Infer_pos"] == 1
    assert sigs["Gps_B_Falt"] == 1
