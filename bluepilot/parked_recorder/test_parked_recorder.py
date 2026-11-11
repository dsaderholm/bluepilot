import json
import math
import re
from datetime import UTC, datetime

from bluepilot.parked_recorder import storage_bp
from bluepilot.parked_recorder.capture_bp import ClipWriter, EventRateLimiter, clamp_clip_seconds
from bluepilot.parked_recorder.warning_bp import (DEFAULT_STYLE, MAX_HZ, SEQUENCES, STYLES, WarningTracker,
                                                  clamp_warning_seconds, flash_frame)
from bluepilot.parked_recorder.detector_bp import MotionDetectorBP, ParkedTrigger, clamp_sensitivity
from bluepilot.parked_recorder.guards_bp import (LOW_VOLTAGE_HOLD_S, ParkedGuard, VoltageLPF, clamp_max_hours,
                                                 clamp_min_voltage, effective_max_time_offroad_s)

DT = 1 / 104  # accelerometer rate
GRAVITY = (0.0, 0.0, 9.81)


def feed(det, start_t, seconds, sample=GRAVITY, offset=None):
  """Feeds constant samples (optionally with a per-sample offset function); returns (events, end_t)."""
  events = []
  t = start_t
  n = int(seconds / DT)
  for i in range(n):
    t += DT
    s = sample if offset is None else tuple(a + b for a, b in zip(sample, offset(i), strict=True))
    e = det.update(t, s)
    if e is not None:
      events.append(e)
  return events, t


class TestDetector:
  def test_still_car_never_triggers(self):
    det = MotionDetectorBP(sensitivity=3.0)
    events, _ = feed(det, 0.0, 30.0)
    assert events == []

  def test_noise_below_threshold_never_triggers(self):
    det = MotionDetectorBP(sensitivity=3.0)  # threshold 0.1 m/s^2
    events, _ = feed(det, 0.0, 30.0, offset=lambda i: (0.02 * math.sin(i), 0.02 * math.cos(i), 0.0))
    assert events == []

  def test_slow_tilt_is_not_a_bump(self):
    # a 0.5 m/s^2 shift spread over 60 s (wind, temperature, settling) is absorbed by the baseline
    det = MotionDetectorBP(sensitivity=3.0)
    events, _ = feed(det, 0.0, 60.0, offset=lambda i: (0.5 * i * DT / 60.0, 0.0, 0.0))
    assert events == []

  def test_single_strong_bump_triggers(self):
    det = MotionDetectorBP(sensitivity=3.0)
    _, t = feed(det, 0.0, 5.0)
    event = det.update(t + DT, (0.0, 0.0, 9.81 + 0.6))  # 0.6 >= 4 * 0.1
    assert event is not None
    assert event.score >= 4.0

  def test_repeated_small_hits_trigger(self):
    det = MotionDetectorBP(sensitivity=3.0)
    _, t = feed(det, 0.0, 5.0)
    got = None
    for k in range(3):  # three hits of 0.2 (> 0.1, < 0.4) inside the 2 s window
      t += 0.1
      got = det.update(t, (0.0, 0.0, 9.81 + (0.2 if k % 2 == 0 else -0.2))) or got
    assert got is not None
    assert got.hits >= 3

  def test_single_small_hit_does_not_trigger(self):
    det = MotionDetectorBP(sensitivity=3.0)
    _, t = feed(det, 0.0, 5.0)
    assert det.update(t + DT, (0.0, 0.0, 9.81 + 0.2)) is None

  def test_hits_outside_window_do_not_add_up(self):
    det = MotionDetectorBP(sensitivity=3.0)
    _, t = feed(det, 0.0, 5.0)
    for _ in range(3):
      t += 3.0  # gaps above max_gap_s reset the detector each time, and hits are older than the window anyway
      assert det.update(t, (0.0, 0.0, 9.81 + 0.2)) is None

  def test_gap_relearns_baseline(self):
    det = MotionDetectorBP(sensitivity=3.0)
    _, t = feed(det, 0.0, 5.0)
    # process stalled for 10 s and the car is now tilted: not a bump
    assert det.update(t + 10.0, (1.0, 0.0, 9.76)) is None
    events, _ = feed(det, t + 10.0, 5.0, sample=(1.0, 0.0, 9.76))
    assert events == []

  def test_bad_samples_ignored(self):
    det = MotionDetectorBP()
    assert det.update(0.0, None) is None
    assert det.update(0.0, (1.0, 2.0)) is None
    assert det.update(0.0, (float("nan"), 0.0, 9.8)) is None
    assert det.update(float("inf"), (0.0, 0.0, 9.8)) is None

  def test_higher_sensitivity_triggers_on_smaller_bump(self):
    bump = (0.0, 0.0, 9.81 + 0.15)
    results = {}
    for s in (1.0, 6.0):
      det = MotionDetectorBP(sensitivity=s, min_hits=1)
      _, t = feed(det, 0.0, 5.0)
      results[s] = det.update(t + DT, bump) is not None
    assert results == {1.0: False, 6.0: True}

  def test_clamp_sensitivity(self):
    assert clamp_sensitivity(0.0) == 0.5
    assert clamp_sensitivity(99) == 10.0
    assert clamp_sensitivity(float("nan")) == 3.0


class TestParkedTrigger:
  def bump(self, trigger, t):
    return trigger.update(t, (0.0, 0.0, 9.81 + 1.0))

  def test_arm_delay_suppresses_early_bump(self):
    trig = ParkedTrigger(t_start=0.0, arm_delay_s=120.0)
    _, t = feed(trig.detector, 0.0, 10.0)
    assert self.bump(trig, t + DT) is None  # door slam right after parking

  def test_triggers_once_armed(self):
    trig = ParkedTrigger(t_start=0.0, arm_delay_s=120.0)
    _, t = feed(trig.detector, 0.0, 125.0)
    assert not trig.armed(0.0) and trig.armed(t)
    assert self.bump(trig, t + DT) is not None

  def test_cooldown_makes_one_event_per_incident(self):
    trig = ParkedTrigger(t_start=0.0, arm_delay_s=1.0, cooldown_s=30.0)
    _, t = feed(trig.detector, 0.0, 5.0)
    assert self.bump(trig, t + DT) is not None
    _, t2 = feed(trig.detector, t + DT, 5.0)
    assert self.bump(trig, t2 + DT) is None       # 5 s later, still cooling down
    _, t3 = feed(trig.detector, t2 + DT, 30.0)
    assert self.bump(trig, t3 + DT) is not None   # after the cooldown


class TestGuard:
  def test_time_window(self):
    g = ParkedGuard(t_start=1000.0, max_hours=4.0)
    assert g.update(1000.0 + 4 * 3600 - 1, 12600) is None
    assert g.update(1000.0 + 4 * 3600, 12600) == "time"

  def test_low_voltage_needs_to_hold(self):
    g = ParkedGuard(t_start=0.0, min_voltage_v=12.3)
    assert g.update(100.0, 12200) is None
    assert g.update(100.0 + LOW_VOLTAGE_HOLD_S - 1, 12200) is None
    assert g.update(100.0 + LOW_VOLTAGE_HOLD_S, 12200) == "voltage"

  def test_low_voltage_blip_resets(self):
    g = ParkedGuard(t_start=0.0, min_voltage_v=12.3)
    assert g.update(100.0, 12200) is None
    assert g.update(120.0, 12500) is None          # recovered: timer resets
    assert g.update(140.0, 12200) is None
    assert g.update(140.0 + LOW_VOLTAGE_HOLD_S - 1, 12200) is None

  def test_missing_voltage_fails_closed_after_the_grace_period(self):
    for bad in (None, float("nan"), 0):
      g = ParkedGuard(t_start=0.0, no_voltage_grace_s=15.0)
      assert g.update(14.9, bad) is None            # still waiting for the first reading
      assert g.update(15.0, bad) == "no_voltage"

  def test_first_reading_inside_grace_is_fine(self):
    g = ParkedGuard(t_start=0.0)
    assert g.update(0.1, None) is None              # the real device: the message arrives about 0.25 s in
    assert g.update(0.3, 12600) is None
    assert g.update(100.0, 12600) is None

  def test_power_down_disabled_refuses_to_run(self):
    g = ParkedGuard(t_start=0.0)
    assert g.update(1.0, 12600, power_down_disabled=True) == "power_down_disabled"
    assert g.update(2.0, 12600, power_down_disabled=False) == "power_down_disabled"   # latched for this ignition cycle

  def test_power_down_enabled_does_not_trip(self):
    g = ParkedGuard(t_start=0.0)
    assert g.update(1.0, 12600, power_down_disabled=False) is None

  def test_trip_is_latched(self):
    g = ParkedGuard(t_start=0.0, min_voltage_v=12.3, low_voltage_hold_s=0.0)
    g.update(1.0, 12000)
    assert g.update(2.0, 12000) == "voltage"
    assert g.update(3.0, 13000) == "voltage"        # voltage recovering does not re-enable it

  def test_configure_clamps(self):
    g = ParkedGuard(t_start=0.0)
    g.configure(max_hours=999, min_voltage_v=5)
    assert g.max_hours == 12.0 and g.min_voltage_v == 12.0
    assert clamp_max_hours("garbage") == 4.0
    assert clamp_min_voltage(None) == 12.3


class TestVoltageLPF:
  def test_starts_at_first_reading(self):
    f = VoltageLPF()
    assert f.update(0.0, 12700) == 12700

  def test_filters_a_dip(self):
    f = VoltageLPF(tau_s=45.0)
    f.update(0.0, 12700)
    v = f.update(1.0, 11000)  # one bad second moves the filter by about 2%
    assert 12600 < v < 12700

  def test_ignores_invalid(self):
    f = VoltageLPF()
    assert f.update(0.0, None) is None
    f.update(1.0, 12500)
    assert f.update(2.0, float("nan")) == 12500


class TestEffectiveOffroadLimit:
  STOCK = 1800.0

  def test_disabled_is_stock(self):
    assert effective_max_time_offroad_s(self.STOCK, False, 4, 12600, 12.3) == self.STOCK

  def test_enabled_and_healthy_extends_to_window(self):
    assert effective_max_time_offroad_s(self.STOCK, True, 4, 12600, 12.3) == 4 * 3600

  def test_low_voltage_falls_back_to_stock(self):
    assert effective_max_time_offroad_s(self.STOCK, True, 4, 12200, 12.3) == self.STOCK

  def test_unknown_voltage_falls_back_to_stock(self):
    assert effective_max_time_offroad_s(self.STOCK, True, 4, None, 12.3) == self.STOCK

  def test_owner_no_limit_is_left_alone(self):
    assert effective_max_time_offroad_s(0, True, 4, 12600, 12.3) == 0

  def test_never_shortens_a_longer_stock_limit(self):
    assert effective_max_time_offroad_s(10 * 3600, True, 4, 12600, 12.3) == 10 * 3600

  def test_window_is_clamped(self):
    assert effective_max_time_offroad_s(self.STOCK, True, 999, 12600, 12.3) == 12 * 3600


class TestStorage:
  def make_event(self, root, when, size):
    d = storage_bp.new_event_dir(str(root), when)
    (d / "clip.bin").write_bytes(b"x" * size)
    storage_bp.write_event(d, {"trigger": "bump"})
    return d

  def test_event_id_is_a_valid_upload_key(self, tmp_path):
    d = storage_bp.new_event_dir(str(tmp_path), datetime(2026, 11, 10, 15, 30, 0, tzinfo=UTC))
    assert d.name == "20261110T153000Z"
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", d.name)

  def test_same_second_gets_a_suffix(self, tmp_path):
    when = datetime(2026, 11, 10, 15, 30, 0, tzinfo=UTC)
    a = storage_bp.new_event_dir(str(tmp_path), when)
    b = storage_bp.new_event_dir(str(tmp_path), when)
    assert a != b and b.name == "20261110T153000Z-1"

  def test_write_event_is_json_with_id(self, tmp_path):
    d = storage_bp.new_event_dir(str(tmp_path))
    entry = storage_bp.write_event(d, {"trigger": "bump", "score": 5.0})
    on_disk = json.loads((d / storage_bp.EVENT_FILE).read_text())
    assert on_disk == entry and on_disk["eventId"] == d.name
    assert not list(d.glob("*.tmp"))

  def test_quota_removes_oldest_first(self, tmp_path):
    mb = 1024 * 1024
    old = self.make_event(tmp_path, datetime(2026, 1, 1, tzinfo=UTC), 200 * mb)
    mid = self.make_event(tmp_path, datetime(2026, 1, 2, tzinfo=UTC), 100 * mb)
    new = self.make_event(tmp_path, datetime(2026, 1, 3, tzinfo=UTC), 100 * mb)
    removed = storage_bp.enforce_quota(str(tmp_path), 256, keep=new)  # 400 MB on disk, limit 256
    assert removed == [old.name]
    assert not old.exists() and mid.exists() and new.exists()

  def test_quota_never_deletes_the_event_being_written(self, tmp_path):
    mb = 1024 * 1024
    only = self.make_event(tmp_path, datetime(2026, 1, 1, tzinfo=UTC), 300 * mb)
    assert storage_bp.enforce_quota(str(tmp_path), 256, keep=only) == []
    assert only.exists()

  def test_quota_clamped_and_missing_root_ok(self, tmp_path):
    assert storage_bp.clamp_storage_mb(1) == 256
    assert storage_bp.clamp_storage_mb("x") == 2048
    assert storage_bp.enforce_quota(str(tmp_path / "nope"), 256) == []
    assert storage_bp.list_events(str(tmp_path / "nope")) == []


class TestClipWriter:
  def writer(self, tmp_path, **kw):
    return ClipWriter(tmp_path, ["wideRoad", "driver"], clip_seconds=20, t_start=100.0, **kw)

  def test_waits_then_records_then_done(self, tmp_path):
    w = self.writer(tmp_path)
    assert w.state(105.0) == "waiting"
    w.add_frame("wideRoad", b"H", b"d1", now=106.0)
    assert w.state(106.0) == "recording"
    assert w.state(125.9) == "recording"
    assert w.state(126.0) == "done"                 # 20 s counted from the first frame, not from the trigger

  def test_fails_without_frames(self, tmp_path):
    w = self.writer(tmp_path)
    assert w.state(100.0 + 19.9) == "waiting"
    assert w.state(100.0 + 20.0) == "failed"

  def test_size_cap_ends_the_clip(self, tmp_path):
    w = self.writer(tmp_path, max_bytes=10)
    w.add_frame("wideRoad", b"H", b"x" * 20, now=101.0)
    assert w.state(101.0) == "done"

  def test_close_keeps_only_cameras_with_frames(self, tmp_path):
    w = self.writer(tmp_path)
    w.add_frame("wideRoad", b"HH", b"dd", now=101.0)
    w.add_frame("wideRoad", b"HH", b"ee", now=101.1)
    w.add_frame("bogus", b"H", b"x", now=101.2)      # unknown camera ignored
    kept = w.close()
    assert kept == [{"name": "wideRoad.h264", "camera": "wideRoad", "bytes": 8, "frames": 2}]
    assert (tmp_path / "wideRoad.h264").read_bytes() == b"HHddHHee"
    assert not (tmp_path / "driver.h264").exists()

  def test_frames_after_close_are_dropped_and_empty_data_ignored(self, tmp_path):
    w = self.writer(tmp_path)
    w.add_frame("wideRoad", b"H", b"", now=101.0)    # empty frame: not counted, clock not started
    assert w.state(101.0) == "waiting"
    w.close()
    w.add_frame("wideRoad", b"H", b"late", now=102.0)
    assert not (tmp_path / "wideRoad.h264").exists()

  def test_clip_seconds_clamped(self):
    assert clamp_clip_seconds(1) == 5 and clamp_clip_seconds(999) == 60 and clamp_clip_seconds("x") == 20


class TestRateLimiter:
  def test_limits_per_window(self):
    r = EventRateLimiter(max_events=2, window_s=100.0)
    assert r.allow(0.0)
    r.record(0.0)
    r.record(10.0)
    assert not r.allow(20.0)
    assert r.allow(101.0)                             # the first one has aged out


class TestWarning:
  def test_hard_hit_warns_at_once(self):
    assert WarningTracker(strong_score=8.0).add_event(0.0, 12.0) is True

  def test_light_single_hit_does_not_warn(self):
    assert WarningTracker(strong_score=8.0).add_event(0.0, 5.0) is False

  def test_repeated_hits_inside_the_window_warn(self):
    t = WarningTracker(repeat_count=3, window_s=120.0)
    assert [t.add_event(x, 5.0) for x in (0.0, 40.0, 80.0)] == [False, False, True]

  def test_old_hits_age_out(self):
    t = WarningTracker(repeat_count=3, window_s=120.0)
    t.add_event(0.0, 5.0)
    t.add_event(40.0, 5.0)
    assert t.add_event(200.0, 5.0) is False         # the first two are older than 120 s

  def test_flash_alternates_between_the_two_colours(self):
    a, b = STYLES["police"]
    seen = [flash_frame(i * 0.05, "police", hz=2.0)[0] for i in range(40)]   # 2 s at 20 fps
    assert set(seen) == {a, b}
    assert seen[0] == a and seen[5] == b             # 2 Hz: colour A for 0.25 s, then B for 0.25 s

  def test_flash_rate_is_capped(self):
    changes = sum(1 for i in range(1, 1000) if flash_frame(i * 0.001, "white", hz=50.0)[0] != flash_frame((i - 1) * 0.001, "white", hz=50.0)[0])
    assert changes <= int(MAX_HZ * 2) + 1            # at most 16 colour changes in 1 s, whatever hz is asked for

  def test_default_is_police_then_white_and_repeats(self):
    assert DEFAULT_STYLE == "police_white" and SEQUENCES[DEFAULT_STYLE] == [("police", 6.0), ("white", 4.0)]
    police, white = set(STYLES["police"]), set(STYLES["white"])
    first = {flash_frame(i * 0.01, DEFAULT_STYLE)[0] for i in range(600)}      # 0-6 s
    second = {flash_frame(6.0 + i * 0.01, DEFAULT_STYLE)[0] for i in range(400)}  # 6-10 s
    third = {flash_frame(10.0 + i * 0.01, DEFAULT_STYLE)[0] for i in range(600)}  # the cycle repeats
    assert first == police and second == white and third == police

  def test_default_rate_is_fast(self):
    seen = [flash_frame(i * 0.01, "police")[0] for i in range(100)]                 # 1 s
    flips = sum(1 for a, b in zip(seen, seen[1:], strict=False) if a != b)
    assert flips >= 9                                    # default 5 flashes/s means 10 colour changes per second

  def test_unknown_style_and_bad_input_fall_back(self):
    assert flash_frame(0.0, "nope")[0] == STYLES["police"][0]               # unknown style: the default sequence starts on police
    assert flash_frame(0.0, "amber", hz=float("nan"))[0] == STYLES["amber"][0]
    assert flash_frame(float("nan"), DEFAULT_STYLE)[0] == STYLES["police"][0]
    assert flash_frame(-5.0, DEFAULT_STYLE)[0] == STYLES["police"][0]

  def test_warning_seconds_clamped(self):
    assert clamp_warning_seconds(1) == 10 and clamp_warning_seconds(999) == 120 and clamp_warning_seconds("x") == 30
