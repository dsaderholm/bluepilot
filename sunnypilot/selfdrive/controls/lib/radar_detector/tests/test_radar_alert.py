"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FusionPilot: behavioral tests for the radar-detector speed limit offset override.

The cases that earn their keep here are the ones asserting this stays OUT of the way: muted alerts,
rear-only alerts, K and X band, and the shipped configuration where the override is switched off
entirely. A detector feature that fires when it should not is worse than one that never fires,
because the driver switches the whole thing off after the second time.

The mute case is load-bearing rather than incidental -- it is how the driver's own GPS lockouts and
manual mutes reach this module, and there is no second lockout store anywhere. If it regresses,
every automatic door the detector has already been taught to ignore starts slowing the car.
"""

from openpilot.common.constants import CV
from openpilot.sunnypilot.selfdrive.controls.lib.radar_detector.esp_protocol import (
  ARROW_FRONT, ARROW_REAR, BAND_K, BAND_KA, DisplayData,
)
from openpilot.sunnypilot.selfdrive.controls.lib.radar_detector.radar_alert import (
  MIN_PERSISTENCE_S, MIN_V_EGO_MS, RELEASE_S, RadarAlertDetector,
)

V_EGO = 70 * CV.MPH_TO_MS


class FakeParams:
  """Only the keys this module reads. Values chosen to be ACTIVE by default so each test states the
  one thing it is turning off, rather than every test restating the whole configuration."""

  def __init__(self, **over):
    self.vals = {
      "RadarDetectorEnabled": True,
      "RadarDetectorSlowdownEnabled": True,
      "RadarDetectorMinBars": 6,
      "RadarDetectorMargin": 1,
    }
    self.vals.update(over)

  def get_bool(self, k):
    return bool(self.vals[k])

  def get(self, k, return_default=False):
    return self.vals[k]


def display(bars=7, bands=BAND_KA, arrows=ARROW_FRONT, muted=False, searching=True):
  return DisplayData(bogey_count=1, bars=bars, bands=bands, arrows=arrows,
                     muted=muted, searching=searching, display_on=True)


def run(det, d, seconds, v_ego=V_EGO, long_enabled=True):
  """Feed the same frame for `seconds`, at the model rate the detector accumulates against."""
  for _ in range(int(seconds / 0.05) + 1):
    det.update(d, v_ego, long_enabled)


def armed(**params):
  return RadarAlertDetector(FakeParams(**params))


class TestTrigger:
  def test_strong_ka_ahead_overrides_the_offset(self):
    det = armed()
    run(det, display(), MIN_PERSISTENCE_S)
    assert det.active
    assert det.offset_override(False) == -1 * CV.MPH_TO_MS

  def test_single_frame_does_not_trigger(self):
    """One packet at threshold is a blind-spot monitor clipping the antenna, not an approach."""
    det = armed()
    det.update(display(), V_EGO, True)
    assert not det.active

  def test_rear_ka_triggers_too(self):
    """Requested deliberately (2026-08-06). A rear hit means you have already been measured, so the
    recorded speed will not change -- but on this car stock ACC bleeds speed off without lighting
    the brake lights, so the car ends up at the limit having shown no braking behavior at all. That
    is the driver's call about his own car; the action itself is "drive at the speed limit"."""
    det = armed()
    run(det, display(arrows=ARROW_REAR), MIN_PERSISTENCE_S)
    assert det.active

  def test_below_threshold_bars_never_triggers(self):
    det = armed()
    run(det, display(bars=5), MIN_PERSISTENCE_S * 3)
    assert not det.active

  def test_margin_is_in_display_units(self):
    det = armed(RadarDetectorMargin=5)
    run(det, display(), MIN_PERSISTENCE_S)
    assert det.offset_override(False) == -5 * CV.MPH_TO_MS

  def test_margin_converts_for_a_metric_display(self):
    det = armed(RadarDetectorMargin=5)
    run(det, display(), MIN_PERSISTENCE_S)
    assert det.offset_override(True) == -5 * CV.KPH_TO_MS

  def test_override_is_always_negative(self):
    """It aims UNDER the limit by construction, so it can never raise a target however it is
    configured -- including replacing a driver's positive offset."""
    det = armed(RadarDetectorMargin=0)
    run(det, display(), MIN_PERSISTENCE_S)
    assert det.offset_override(False) <= 0

  def test_no_override_while_inactive(self):
    """None, not zero. Zero would be an offset of its own and would silently discard the driver's."""
    det = armed()
    assert det.offset_override(False) is None

  def test_does_not_apply_a_speed_floor(self):
    """Ford's 20 mph ACC minimum is ICBM's floor, not this module's -- it is a property of the
    cruise-button layer and disappears under alpha longitudinal. A clamp here would be wrong the
    day that lands, and wrong in the direction of not slowing down."""
    det = armed(RadarDetectorMargin=30)
    run(det, display(), MIN_PERSISTENCE_S)
    assert det.offset_override(False) == -30 * CV.MPH_TO_MS


class TestStaysOutOfTheWay:
  def test_muted_alert_never_triggers(self):
    """The driver's own lockouts and manual mutes arrive as the Soft Mute bit. This is the only
    lockout mechanism in the feature -- see the radar_alert module docstring."""
    det = armed()
    run(det, display(muted=True), MIN_PERSISTENCE_S * 3)
    assert not det.active

  def test_a_band_with_no_direction_does_not_trigger(self):
    """A partial frame, not an alert. Every real alert lights an arrow."""
    det = armed()
    run(det, display(arrows=0), MIN_PERSISTENCE_S * 3)
    assert not det.active

  def test_k_band_never_triggers(self):
    det = armed()
    run(det, display(bands=BAND_K), MIN_PERSISTENCE_S * 3)
    assert not det.active

  def test_shipped_configuration_does_not_change_the_offset(self):
    """RadarDetectorSlowdownEnabled ships OFF. Detection and logging run; the car does not change."""
    det = armed(RadarDetectorSlowdownEnabled=False)
    run(det, display(), MIN_PERSISTENCE_S * 3)
    assert not det.active
    assert det.offset_override(False) is None
    # ...but the readout still has to work, or there is nothing to fit the threshold from.
    assert det.bars == 7
    assert det.ka_ahead

  def test_disabled_outright(self):
    det = armed(RadarDetectorEnabled=False)
    run(det, display(), MIN_PERSISTENCE_S * 3)
    assert not det.active

  def test_not_engaged(self):
    det = armed()
    run(det, display(), MIN_PERSISTENCE_S * 3, long_enabled=False)
    assert not det.active

  def test_too_slow_to_matter(self):
    det = armed()
    run(det, display(), MIN_PERSISTENCE_S * 3, v_ego=MIN_V_EGO_MS - 1.0)
    assert not det.active

  def test_detector_not_searching(self):
    """Powered but not signed on. Its indicators mean nothing yet."""
    det = armed()
    run(det, display(searching=False), MIN_PERSISTENCE_S * 3)
    assert not det.active


class TestRelease:
  def test_holds_through_a_short_dropout(self):
    """Fringe alerts flicker. Releasing on every gap would walk the target up and down."""
    det = armed()
    run(det, display(), MIN_PERSISTENCE_S)
    run(det, display(bands=0, arrows=0), RELEASE_S / 2)
    assert det.active

  def test_releases_after_the_alert_is_gone(self):
    det = armed()
    run(det, display(), MIN_PERSISTENCE_S)
    run(det, display(bands=0, arrows=0), RELEASE_S + 0.5)
    assert not det.active
    assert det.offset_override(False) is None

  def test_hysteresis_holds_at_one_bar_below_the_trigger(self):
    """An alert sitting exactly at the threshold is the common case at range, not an edge one."""
    det = armed()
    run(det, display(), MIN_PERSISTENCE_S)
    run(det, display(bars=5), RELEASE_S + 0.5)
    assert det.active

  def test_muting_an_active_alert_releases_it(self):
    """Mute mid-encounter is the driver saying "I see it, ignore it" -- the same instruction as a
    lockout, given in the moment."""
    det = armed()
    run(det, display(), MIN_PERSISTENCE_S)
    run(det, display(muted=True), RELEASE_S + 0.5)
    assert not det.active

  def test_disengaging_drops_the_override_immediately(self):
    det = armed()
    run(det, display(), MIN_PERSISTENCE_S)
    det.update(display(), V_EGO, False)
    assert not det.active


class TestLinkHealth:
  def test_lost_link_releases_and_does_not_show_stale_indicators(self):
    """A dead link must look dead. The failure that matters is the driver reading "no alerts" off a
    link that stopped ten minutes ago."""
    det = armed()
    run(det, display(), MIN_PERSISTENCE_S)
    assert det.link_ok
    run(det, None, RELEASE_S + 0.5)
    assert not det.active
    assert not det.link_ok
    assert det.bars == 0
    assert not det.ka_present
    assert det.bogey_count == 0

  def test_link_ok_is_reported_even_when_the_feature_cannot_act(self):
    det = armed(RadarDetectorSlowdownEnabled=False)
    run(det, display(), 0.2)
    assert det.link_ok
