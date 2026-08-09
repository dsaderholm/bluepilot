"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FusionPilot: the radar detector's speed limit offset override, inside the resolver.

These exist because the resolver's own 47 tests were green before this branch was added and would
have stayed green if it had been wired backwards -- none of them reach the new code path. A test
that cannot fail for the reason you care about is not coverage.

What is actually being asserted: the override REPLACES the driver's offset rather than adding to
it, and it does so for every OffsetType including `off`. Both halves matter. Adding would leave a
driver who runs +5 still over the limit on exactly the roads where they run over it most, and
skipping `off` would mean the one setting that reads as "I don't want an offset" silently disabled
a safety-adjacent feature that has nothing to do with it.
"""

import pytest

from openpilot.common.constants import CV
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit import speed_limit_resolver as slr
from openpilot.sunnypilot.selfdrive.controls.lib.radar_detector.esp_protocol import BAND_KA
from openpilot.sunnypilot.selfdrive.controls.lib.radar_detector.locations import MIN_OBSERVATIONS_TO_MUTE
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.common import OffsetType
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.speed_limit_resolver import SpeedLimitResolver

MARGIN = 1
LIMIT_MS = 65 * CV.MPH_TO_MS
LAT, LON = 40.7608, -111.8910
FAR_LAT, FAR_LON = 40.9000, -111.7000


def resolver(offset_type=OffsetType.fixed, offset_value=5, is_metric=False, active=False):
  """A resolver with its offset settings pinned, so these tests state their own configuration
  rather than inheriting whatever the param defaults happen to be this week."""
  r = SpeedLimitResolver()
  r.is_metric = is_metric
  r.offset_type = offset_type
  r.offset_value = offset_value
  r.offset_low = r.offset_mid = r.offset_high = offset_value
  r.radar_alert.margin = MARGIN
  r.radar_alert.active = active
  return r


def final_speed(r):
  r.speed_limit = LIMIT_MS
  r.speed_limit_offset = r._get_speed_limit_offset()
  r.update_speed_limit_states()
  return r.speed_limit_final


class TestOverrideReplacesTheDriversOffset:
  def test_inactive_leaves_a_fixed_offset_alone(self):
    assert resolver()._get_speed_limit_offset() == pytest.approx(5 * CV.MPH_TO_MS)

  def test_active_replaces_a_fixed_offset(self):
    assert resolver(active=True)._get_speed_limit_offset() == pytest.approx(-MARGIN * CV.MPH_TO_MS)

  @pytest.mark.parametrize("offset_type", [OffsetType.off, OffsetType.fixed,
                                           OffsetType.percentage, OffsetType.bySpeed])
  def test_active_wins_over_every_offset_type(self, offset_type):
    """Including `off`. "I don't use an offset" is a statement about how you normally drive, not a
    refusal to respond to a radar alert -- and it is the default, so getting this wrong would mean
    the feature did nothing on a stock configuration."""
    r = resolver(offset_type=offset_type, active=True)
    assert r._get_speed_limit_offset() == pytest.approx(-MARGIN * CV.MPH_TO_MS)

  def test_it_replaces_rather_than_adds(self):
    """The whole point. A driver running +5 over a 65 gets a 6 mph change from a 1 mph margin,
    because the reason to slow is that their usual number is not the number they want right now.
    Adding would produce 1 mph and leave them at 69 in a 65."""
    normal = final_speed(resolver(offset_value=5))
    alerted = final_speed(resolver(offset_value=5, active=True))
    assert normal - alerted == pytest.approx(6 * CV.MPH_TO_MS)
    assert alerted == pytest.approx(LIMIT_MS - MARGIN * CV.MPH_TO_MS)

  def test_it_aims_under_the_limit_not_merely_at_it(self):
    assert final_speed(resolver(active=True)) < LIMIT_MS

  def test_metric_margin_converts(self):
    r = resolver(is_metric=True, active=True)
    assert r._get_speed_limit_offset() == pytest.approx(-MARGIN * CV.KPH_TO_MS)


class FakeReader:
  """Stands in for EspSerialReader so no test opens a port or spawns a thread."""
  port: str | None = "/dev/serial/by-id/fake"
  built: list = []

  def __init__(self):
    self.started = False
    self.stopped = False
    self.value = "a display"
    FakeReader.built.append(self)

  @staticmethod
  def find_port():
    return FakeReader.port

  def start(self):
    self.started = True

  def stop(self, timeout=1.0):
    self.stopped = True

  def display(self):
    return self.value


@pytest.fixture
def fake_reader(monkeypatch):
  FakeReader.built = []
  FakeReader.port = "/dev/serial/by-id/fake"
  monkeypatch.setattr(slr, "EspSerialReader", FakeReader)
  return FakeReader


class TestSerialLinkManagement:
  """The link is brought up lazily and only when there is something to talk to.

  The gate that matters is the port check. Without it, every resolver ever constructed -- including
  in each of these tests, on a machine with no serial devices at all -- would spawn a thread whose
  entire job is to fail to open a port and sleep.
  """

  def test_no_adapter_means_no_reader(self, fake_reader):
    fake_reader.port = None
    r = resolver()
    r.frame = 0
    r._update_radar_link()
    assert fake_reader.built == []
    assert r.radar_display is None

  def test_adapter_present_starts_the_reader(self, fake_reader):
    r = resolver()
    r.frame = 0
    r._update_radar_link()
    assert len(fake_reader.built) == 1
    assert fake_reader.built[0].started

  def test_only_one_reader_however_often_it_is_checked(self, fake_reader):
    r = resolver()
    for frame in range(0, slr._RADAR_LINK_CHECK_FRAMES * 4):
      r.frame = frame
      r._update_radar_link()
    assert len(fake_reader.built) == 1

  def test_disabling_the_feature_closes_the_port(self, fake_reader):
    r = resolver()
    r.frame = 0
    r._update_radar_link()
    reader = fake_reader.built[0]

    # The offline Params stub reports params_keys.h defaults and ignores writes, so the setting is
    # turned off by swapping the object rather than by storing a value.
    r.params = type("OffParams", (), {"get_bool": staticmethod(lambda *a, **k: False)})()
    r.frame = slr._RADAR_LINK_CHECK_FRAMES
    r._update_radar_link()
    assert reader.stopped
    assert r.radar_display is None

  def test_display_is_refreshed_every_frame_not_on_the_slow_cadence(self, fake_reader):
    """Staleness is measured in tenths of a second. Refreshing on the two-second link check would
    make a perfectly live link look intermittent."""
    r = resolver()
    r.frame = 0
    r._update_radar_link()
    reader = fake_reader.built[0]

    reader.value = "fresh"
    r.frame = 1  # not a multiple of the link-check period
    r._update_radar_link()
    assert r.radar_display == "fresh"

  def test_an_adapter_plugged_in_later_is_picked_up(self, fake_reader):
    """No reboot required -- the periodic check is what makes that true."""
    fake_reader.port = None
    r = resolver()
    r.frame = 0
    r._update_radar_link()
    assert fake_reader.built == []

    fake_reader.port = "/dev/serial/by-id/fake"
    r.frame = slr._RADAR_LINK_CHECK_FRAMES
    r._update_radar_link()
    assert len(fake_reader.built) == 1


class TestEngagementGate:
  def test_missing_car_control_reads_as_not_engaged(self):
    """A radar alert must never override the offset on the strength of a message that is not there.
    The stubbed SubMaster in these tests has no carControl at all, which is exactly the case."""
    class NoCarControl:
      valid: dict = {}

      def __getitem__(self, k):
        raise KeyError(k)

    assert SpeedLimitResolver._long_enabled(NoCarControl()) is False

  def test_present_but_disengaged(self):
    class Disengaged:
      valid = {'carControl': True}

      def __getitem__(self, k):
        return type("CC", (), {"enabled": False})()

    assert SpeedLimitResolver._long_enabled(Disengaged()) is False

  def test_engaged(self):
    class Engaged:
      valid = {'carControl': True}

      def __getitem__(self, k):
        return type("CC", (), {"enabled": True})()

    assert SpeedLimitResolver._long_enabled(Engaged()) is True


class TestMutingLearnedFalseAlarms:
  """The V1 Gen2 has no GPS, so openpilot is the only thing that can give it lockouts at all -- and
  a lockout it cannot act on is worthless, which is why this is the one path that transmits."""

  class FakeDisplay:
    searching = True
    muted = False
    bands = BAND_KA
    arrows = 0
    bars = 8
    bogey_count = 1

  class Sender:
    def __init__(self, accept=True):
      self.sent = []
      self.accept = accept

    def send(self, data):
      self.sent.append(data)
      return self.accept

    def display(self):
      return None

  def _at_a_false_alarm(self, monkeypatch):
    monkeypatch.setattr(slr, "EspSerialReader", FakeReader)
    r = resolver()
    r._radar_reader = self.Sender()
    for _ in range(MIN_OBSERVATIONS_TO_MUTE):
      r._radar_places.observe(LAT, LON, True)
    return r

  def test_mutes_on_the_way_in_and_unmutes_on_the_way_out(self, monkeypatch):
    """Edge triggered -- one packet each way, not a stream."""
    r = self._at_a_false_alarm(monkeypatch)
    r._update_radar_mute(LAT, LON)
    assert r._radar_muting
    assert len(r._radar_reader.sent) == 1

    r._update_radar_mute(LAT, LON)                 # still there; nothing more to say
    assert len(r._radar_reader.sent) == 1

    r._update_radar_mute(FAR_LAT, FAR_LON)         # left
    assert not r._radar_muting
    assert len(r._radar_reader.sent) == 2

  def test_a_refused_send_does_not_claim_to_be_muting(self, monkeypatch):
    """The transport refuses to transmit on a port that has never spoken ESP. If we recorded the
    mute anyway, every pass afterwards would be marked suppressed and the store would stop learning
    from a detector that was never actually silenced."""
    r = self._at_a_false_alarm(monkeypatch)
    r._radar_reader = self.Sender(accept=False)
    r._update_radar_mute(LAT, LON)
    assert not r._radar_muting

  def test_no_link_means_no_muting(self, monkeypatch):
    r = self._at_a_false_alarm(monkeypatch)
    r._radar_reader = None
    r._update_radar_mute(LAT, LON)
    assert not r._radar_muting

  def test_the_setting_switches_off_the_only_thing_that_transmits(self, monkeypatch):
    r = self._at_a_false_alarm(monkeypatch)
    r.params = type("Off", (), {"get_bool": staticmethod(lambda *a, **k: False)})()
    r._update_radar_mute(LAT, LON)
    assert not r._radar_muting
    assert r._radar_reader.sent == []


class TestAnnouncingAPlaceAhead:
  """Once per approach, not once a second.

  This alert has the cry-wolf problem by construction -- a marked place fires every single time you
  drive past it. The ICBM alerts were dialled back to prompts after two false positives in one
  drive; repeating this one at 1 Hz for twenty seconds would be twenty.
  """
  NORTH = 0.0
  V_EGO = 31.0

  def _with_a_trap_ahead(self):
    r = resolver()
    r.v_ego = self.V_EGO
    store = r._radar_places
    store.observe(LAT + 0.004, LON, True)
    for _ in range(16):
      store.observe(LAT + 0.004, LON, False)
    for _ in range(3):
      store.observe(LAT + 0.004, LON, True)
    return r

  def test_announces_once_while_approaching(self):
    r = self._with_a_trap_ahead()
    r._announce_radar_place(LAT, LON, self.NORTH)
    assert r.radar_place_ahead is not None
    for _ in range(20):                       # twenty more seconds of approach
      r._announce_radar_place(LAT, LON, self.NORTH)
      assert r.radar_place_ahead is None

  def test_announces_again_on_a_later_approach(self):
    r = self._with_a_trap_ahead()
    r._announce_radar_place(LAT, LON, self.NORTH)
    assert r.radar_place_ahead is not None
    r._announce_radar_place(FAR_LAT, FAR_LON, self.NORTH)     # away, nothing ahead
    assert r.radar_place_ahead is None
    r._announce_radar_place(LAT, LON, self.NORTH)             # back again
    assert r.radar_place_ahead is not None

  def test_silent_with_nothing_ahead(self):
    r = resolver()
    r.v_ego = self.V_EGO
    r._announce_radar_place(LAT, LON, self.NORTH)
    assert r.radar_place_ahead is None
