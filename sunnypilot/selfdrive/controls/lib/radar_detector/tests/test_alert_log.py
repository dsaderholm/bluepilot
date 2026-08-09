"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FusionPilot: tests for the radar encounter log.

The test that matters most here is test_the_warning_budget_is_computable. This log exists for
exactly one purpose -- to answer "how many seconds pass between a Ka alert first reaching N bars
and it peaking", which is the warning budget at threshold N and the only honest way to choose
RadarDetectorMinBars. A log that records encounters beautifully but cannot answer that question is
a log that wasted a month of driving, and that failure would be completely invisible until someone
sat down with the file and tried.

The other load-bearing case is test_flicker_does_not_split_an_encounter. Fringe alerts come and go
by nature; splitting one approach into six records would destroy the measurement while producing a
file that looks entirely reasonable.
"""

from openpilot.sunnypilot.selfdrive.controls.lib.radar_detector.alert_log import (
  ENCOUNTER_GAP_S, HEARTBEAT_S, MAX_SAMPLES, MIN_SAMPLE_INTERVAL_S, RadarAlertLog,
)
from openpilot.sunnypilot.selfdrive.controls.lib.radar_detector.esp_protocol import (
  ARROW_FRONT, BAND_K, BAND_KA, DisplayData,
)

LAT, LON = 40.7608, -111.8910
V_EGO = 31.0  # ~70 mph


def d(bars=4, bands=BAND_KA, arrows=ARROW_FRONT, muted=False, searching=True):
  return DisplayData(bars=bars, bands=bands, arrows=arrows, muted=muted, searching=searching)


class Sink:
  def __init__(self):
    self.records = []

  def __call__(self, rec):
    self.records.append(rec)


def feed(log, display, t0, seconds, step=0.05, acting=False, v_ego=V_EGO):
  t = t0
  while t < t0 + seconds:
    log.update(display, LAT, LON, v_ego, acting, t)
    t += step
  return t


class TestEncounters:
  def test_an_encounter_is_written_when_it_ends(self):
    sink = Sink()
    log = RadarAlertLog(sink)
    t = feed(log, d(), 100.0, 3.0)
    assert sink.records == []          # nothing written while it is still open
    feed(log, None, t, ENCOUNTER_GAP_S + 1.0)
    assert len(sink.records) == 1
    assert sink.records[0]["peak_bars"] == 4

  def test_a_single_frame_blip_is_dropped(self):
    """Thousands of these would bury the encounters that can actually answer anything."""
    sink = Sink()
    log = RadarAlertLog(sink)
    log.update(d(), LAT, LON, V_EGO, False, 100.0)
    feed(log, None, 100.05, ENCOUNTER_GAP_S + 1.0)
    assert sink.records == []
    assert log.dropped_short == 1

  def test_flicker_does_not_split_an_encounter(self):
    """Fringe alerts have, in the ESP spec's own words, an on-again off-again quality. Splitting one
    approach into six records would destroy the time-to-peak measurement while producing a file that
    looks perfectly reasonable."""
    sink = Sink()
    log = RadarAlertLog(sink)
    t = 100.0
    for _ in range(4):
      t = feed(log, d(bars=3), t, 1.0)
      t = feed(log, None, t, ENCOUNTER_GAP_S - 2.0)
    feed(log, None, t, ENCOUNTER_GAP_S + 1.0)
    assert len(sink.records) == 1

  def test_a_real_gap_does_split_them(self):
    sink = Sink()
    log = RadarAlertLog(sink)
    t = feed(log, d(), 100.0, 2.0)
    t = feed(log, None, t, ENCOUNTER_GAP_S + 1.0)
    t = feed(log, d(), t, 2.0)
    feed(log, None, t, ENCOUNTER_GAP_S + 1.0)
    assert len(sink.records) == 2

  def test_link_loss_does_not_fake_a_quiet_tail(self):
    """None means we cannot tell what the detector is showing. Treating it as "no alert" would
    append a fabricated quiet stretch to the end of a real approach."""
    sink = Sink()
    log = RadarAlertLog(sink)
    t = feed(log, d(bars=6), 100.0, 2.0)
    feed(log, None, t, ENCOUNTER_GAP_S + 1.0)
    rec = sink.records[0]
    assert all(s["bars"] > 0 for s in rec["samples"])

  def test_flush_does_not_lose_the_last_encounter_of_a_drive(self):
    sink = Sink()
    log = RadarAlertLog(sink)
    t = feed(log, d(), 100.0, 2.0)
    log.flush(t)
    assert len(sink.records) == 1

  def test_records_what_it_promised(self):
    sink = Sink()
    log = RadarAlertLog(sink)
    t = feed(log, d(bars=2, bands=BAND_K), 100.0, 1.0)
    t = feed(log, d(bars=7, bands=BAND_KA, muted=True), t, 1.0, acting=True)
    feed(log, None, t, ENCOUNTER_GAP_S + 1.0)
    rec = sink.records[0]
    assert rec["peak_bars"] == 7
    assert rec["bands_seen"] & BAND_K and rec["bands_seen"] & BAND_KA
    assert rec["ever_muted"]
    assert rec["ever_acted"]
    assert rec["start_lat"] == round(LAT, 6)


class TestSampling:
  def test_samples_on_change_not_every_frame(self):
    """20 Hz for a metric whose interesting feature is where the bar count steps would be two orders
    of magnitude more data for no more information."""
    sink = Sink()
    log = RadarAlertLog(sink)
    t = feed(log, d(bars=4), 100.0, HEARTBEAT_S - 0.5)   # steady, well under a heartbeat
    feed(log, None, t, ENCOUNTER_GAP_S + 1.0)
    # Two samples: one at the open, one at the close. Thirty frames went in, and a steady alert has
    # nothing to say in between -- but it must still carry its duration, which is why the closing
    # sample exists at all.
    samples = sink.records[0]["samples"]
    assert len(samples) == 2
    assert samples[0]["t"] == 0.0
    assert samples[-1]["t"] > 0.0

  def test_a_steady_alert_still_shows_its_duration(self):
    sink = Sink()
    log = RadarAlertLog(sink)
    t = feed(log, d(bars=4), 100.0, HEARTBEAT_S * 3)
    feed(log, None, t, ENCOUNTER_GAP_S + 1.0)
    assert len(sink.records[0]["samples"]) >= 3

  def test_rapid_change_is_rate_limited(self):
    sink = Sink()
    log = RadarAlertLog(sink)
    t = 100.0
    for i in range(40):                      # a different bar count every single frame
      log.update(d(bars=1 + i % 8), LAT, LON, V_EGO, False, t)
      t += 0.05
    feed(log, None, t, ENCOUNTER_GAP_S + 1.0)
    span = 40 * 0.05
    assert len(sink.records[0]["samples"]) <= span / MIN_SAMPLE_INTERVAL_S + 1

  def test_samples_are_bounded(self):
    sink = Sink()
    log = RadarAlertLog(sink)
    t = 100.0
    for i in range(MAX_SAMPLES * 2 + 100):
      log.update(d(bars=1 + i % 8), LAT, LON, V_EGO, False, t)
      t += MIN_SAMPLE_INTERVAL_S
    feed(log, None, t, ENCOUNTER_GAP_S + 1.0)
    assert len(sink.records[0]["samples"]) == MAX_SAMPLES


class TestItAnswersTheQuestionItExistsFor:
  def test_the_warning_budget_is_computable(self):
    """The whole point of this file.

    Simulate an approach where the bar count climbs 1 -> 8 over sixteen seconds, then compute from
    the written record how much warning a threshold of 6 bars would have given before the peak. If
    this cannot be done from the record, the log is useless however tidy it looks -- and nobody
    would find out until after a month of driving.
    """
    sink = Sink()
    log = RadarAlertLog(sink)
    t = 100.0
    for bars in range(1, 9):
      t = feed(log, d(bars=bars), t, 2.0)
    feed(log, None, t, ENCOUNTER_GAP_S + 1.0)

    samples = sink.records[0]["samples"]
    first_at_6 = next(s["t"] for s in samples if s["bars"] >= 6)
    peak_at = max(s["t"] for s in samples if s["bars"] == 8)

    warning = peak_at - first_at_6
    # 6 bars starts at t=10 s, 8 bars runs 14-16 s. Six seconds of warning at this threshold.
    assert warning == pytest_approx(6.0, 0.3)

  def test_a_lower_threshold_buys_more_warning(self):
    """The comparison the threshold choice actually rests on."""
    sink = Sink()
    log = RadarAlertLog(sink)
    t = 100.0
    for bars in range(1, 9):
      t = feed(log, d(bars=bars), t, 2.0)
    feed(log, None, t, ENCOUNTER_GAP_S + 1.0)

    samples = sink.records[0]["samples"]
    peak_at = max(s["t"] for s in samples if s["bars"] == 8)
    at_4 = peak_at - next(s["t"] for s in samples if s["bars"] >= 4)
    at_7 = peak_at - next(s["t"] for s in samples if s["bars"] >= 7)
    assert at_4 > at_7


def pytest_approx(value, tol):
  class _Approx:
    def __eq__(self, other):
      return abs(other - value) <= tol

    def __repr__(self):
      return f"{value} +/- {tol}"
  return _Approx()
