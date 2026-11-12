import json

from bluepilot.parked_recorder import storage_bp
from bluepilot.parked_recorder.upload_bp import (EventUploader, clamp_daily_mb, event_files, is_uploaded, pending_events)

TODAY = "2026-11-12"
MB = 1024 * 1024


def make_event(root, name, size=1000, captured=True, cams=("wideRoad", "driver")):
  d = root / name
  d.mkdir(parents=True)
  for c in cams:
    (d / f"{c}.h264").write_bytes(b"x" * size)
  (d / "event.json").write_text(json.dumps({"eventId": name, "captured": captured}))
  return d


class Net:
  """Records calls; statuses are consumed in order, then 200."""

  def __init__(self, *statuses, url_ok=True):
    self.statuses = list(statuses)
    self.url_ok = url_ok
    self.keys, self.puts = [], []

  def get_url(self, key):
    self.keys.append(key)
    return ("https://srv/put/" + key, {"X-Sig": "s"}) if self.url_ok else None

  def put(self, url, headers, path):
    self.puts.append((url, headers, path.name))
    return self.statuses.pop(0) if self.statuses else 200


def run(up, n=20, t=0.0, today=TODAY, cap=1000):
  results = []
  for _ in range(n):
    r = up.step(t, today, cap)
    results.append(r)
    if r in ("idle", "failed", "capped", "backoff"):
      break
  return results


def test_files_media_first_event_json_last_and_markers_ignored(tmp_path):
  d = make_event(tmp_path, "20261112T010000Z")
  (d / ".uploaded").write_text("ok")
  (d / "wideRoad.h264.sent").write_text("ok")
  (d / "x.tmp").write_text("")
  (d / "bad name.h264").write_text("x")
  assert [f.name for f in event_files(d)] == ["driver.h264", "wideRoad.h264", "event.json"]


def test_pending_skips_uploaded_uncaptured_and_unreadable(tmp_path):
  a = make_event(tmp_path, "20261112T010000Z")
  done = make_event(tmp_path, "20261112T020000Z")
  (done / ".uploaded").write_text("ok")
  make_event(tmp_path, "20261112T030000Z", captured=False)
  broken = make_event(tmp_path, "20261112T040000Z")
  (broken / "event.json").write_text("{nope")
  b = make_event(tmp_path, "20261112T050000Z")
  assert pending_events(str(tmp_path)) == [a, b] and is_uploaded(done)


def test_uploads_each_file_with_the_event_key_then_marks_the_event(tmp_path):
  d = make_event(tmp_path, "20261112T010000Z")
  net = Net()
  run(EventUploader(str(tmp_path), net.get_url, net.put))
  assert net.keys == ["20261112T010000Z/driver.h264", "20261112T010000Z/wideRoad.h264", "20261112T010000Z/event.json"]
  assert [p[1] for p in net.puts] == [{"X-Sig": "s"}] * 3 and is_uploaded(d)


def test_sequence_of_results(tmp_path):
  make_event(tmp_path, "20261112T010000Z")
  net = Net()
  up = EventUploader(str(tmp_path), net.get_url, net.put)
  assert run(up) == ["progress", "progress", "progress", "uploaded", "idle"]


def test_no_connection_keeps_the_event_and_backs_off_then_resumes(tmp_path):
  d = make_event(tmp_path, "20261112T010000Z")
  net = Net(None)
  up = EventUploader(str(tmp_path), net.get_url, net.put)
  assert up.step(100.0, TODAY, 1000) == "failed" and not is_uploaded(d)
  assert up.step(105.0, TODAY, 1000) == "backoff"
  assert run(up, t=200.0)[-1] == "idle" and is_uploaded(d)


def test_a_restart_does_not_resend_files_already_on_the_server(tmp_path):
  make_event(tmp_path, "20261112T010000Z")
  net = Net()
  up = EventUploader(str(tmp_path), net.get_url, net.put)
  assert up.step(0.0, TODAY, 1000) == "progress"                # driver.h264 is up
  net2 = Net()
  up2 = EventUploader(str(tmp_path), net2.get_url, net2.put)    # the process restarted
  run(up2)
  assert [k.split("/")[1] for k in net2.keys] == ["wideRoad.h264", "event.json"]


def test_url_request_failure_and_server_errors_retry(tmp_path):
  d = make_event(tmp_path, "20261112T010000Z")
  assert EventUploader(str(tmp_path), Net(url_ok=False).get_url, Net().put).step(0.0, TODAY, 1000) == "failed"
  for status in (500, 503, 408, 429, 401, 403):
    net = Net(status)
    assert EventUploader(str(tmp_path), net.get_url, net.put).step(0.0, TODAY, 1000) == "failed", status
  assert not is_uploaded(d)


def test_a_file_the_server_will_never_accept_is_skipped_not_stuck(tmp_path):
  d = make_event(tmp_path, "20261112T010000Z")
  net = Net(413)                                                # the first file is rejected for good
  up = EventUploader(str(tmp_path), net.get_url, net.put)
  assert run(up)[-2:] == ["uploaded", "idle"] and is_uploaded(d)
  assert (d / "driver.h264.sent").read_text() == "rejected"


def test_daily_cap_stops_uploads_and_resets_the_next_day(tmp_path):
  make_event(tmp_path, "20261112T010000Z", size=40 * MB // 1)   # two 40 MB files
  net = Net()
  up = EventUploader(str(tmp_path), net.get_url, net.put)
  assert up.step(0.0, TODAY, 50) == "progress"                  # 40 MB of the 50 MB cap used
  assert up.step(0.0, TODAY, 50) == "capped"                    # the next 40 MB file would pass it
  assert EventUploader(str(tmp_path), net.get_url, net.put).step(0.0, TODAY, 50) == "capped"   # the cap survives a restart
  assert up.step(0.0, "2026-11-13", 50) == "progress"           # a new day


def test_cap_setting_is_clamped():
  assert clamp_daily_mb(1) == 50 and clamp_daily_mb(10**9) == 10000 and clamp_daily_mb("x") == 1000


def sparse_event(root, name, mb):
  d = root / name
  d.mkdir(parents=True)
  with open(d / "clip.h264", "wb") as f:
    f.truncate(mb * MB)                                      # apparent size only, no real disk used
  (d / "event.json").write_text(json.dumps({"eventId": name, "captured": True}))
  return d


def test_quota_removes_uploaded_events_before_unuploaded_ones(tmp_path):
  old_unsent = sparse_event(tmp_path, "20261101T000000Z", 100)
  newer_sent = sparse_event(tmp_path, "20261102T000000Z", 100)
  (newer_sent / ".uploaded").write_text("ok")
  newest = sparse_event(tmp_path, "20261103T000000Z", 100)
  removed = storage_bp.enforce_quota(str(tmp_path), 256, keep=newest)      # 300 MB against a 256 MB limit: one must go
  assert removed == ["20261102T000000Z"]                                   # the already-uploaded one, not the oldest unsent
  assert old_unsent.exists() and newest.exists() and not newer_sent.exists()
