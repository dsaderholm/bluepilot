import json
import os

from bluepilot.parked_recorder import storage_bp
from bluepilot.parked_recorder.webhook_bp import (BACKOFF_FIRST_S, BACKOFF_MAX_S, Outbox, Sender, build_payload, post_json,
                                                  valid_url)

URL = "https://ha.example/api/webhook/abc123"


def test_valid_url():
  assert valid_url("http://10.0.0.5:8123/api/webhook/x") and valid_url(URL)
  for bad in ("", None, "ftp://host/x", "file:///etc/passwd", "javascript:alert(1)", "http://", "not a url", 5):
    assert valid_url(bad) is False


def test_payload_is_small_and_has_no_secret_or_identifiers():
  p = build_payload("bump", eventId="20261111T210000Z", score=9.5, hits=1)
  assert p["source"] == "parked_recorder" and p["kind"] == "bump" and p["eventId"] == "20261111T210000Z" and "sentAt" in p
  assert "url" not in json.dumps(p).lower() and len(json.dumps(p)) < 400


class TestOutbox:
  def test_enqueue_orders_and_survives_a_new_instance(self, tmp_path):
    a = Outbox(tmp_path / ".outbox")
    for i in range(3):
      a.enqueue({"i": i})
    b = Outbox(tmp_path / ".outbox")                       # a restart: the files are what remain
    assert [b.read(p)["i"] for p in b.pending()] == [0, 1, 2]
    assert not list((tmp_path / ".outbox").glob("*.tmp"))

  def test_oldest_dropped_beyond_the_cap(self, tmp_path):
    o = Outbox(tmp_path / ".outbox", max_items=3)
    for i in range(6):
      o.enqueue({"i": i})
    assert [o.read(p)["i"] for p in o.pending()] == [3, 4, 5]

  def test_old_items_are_dropped(self, tmp_path):
    o = Outbox(tmp_path / ".outbox", max_age_s=100)
    keep = o.enqueue({"i": 0})
    drop = o.enqueue({"i": 1})
    os.utime(drop, (1000, 1000))                           # a very old file
    o.prune(now=keep.stat().st_mtime + 10)                 # `keep` is 10 s old, `drop` is far beyond 100 s
    assert keep.exists() and not drop.exists()

  def test_is_invisible_to_the_event_list_and_quota(self, tmp_path):
    ev = storage_bp.new_event_dir(str(tmp_path))
    storage_bp.write_event(ev, {"kind": "bump"})
    Outbox(tmp_path / ".outbox").enqueue({"i": 0})
    assert storage_bp.list_events(str(tmp_path)) == [ev]


class FakePost:
  def __init__(self, *statuses):
    self.statuses = list(statuses)
    self.calls = []

  def __call__(self, url, payload):
    self.calls.append((url, payload))
    return self.statuses.pop(0) if self.statuses else 200


class TestSender:
  def box(self, tmp_path, n=3):
    o = Outbox(tmp_path / ".outbox")
    for i in range(n):
      o.enqueue({"i": i})
    return o

  def test_sends_all_in_order_and_empties_the_box(self, tmp_path):
    o = self.box(tmp_path)
    post = FakePost()
    assert Sender(o, post).pump(URL, 0.0) == (3, 0)
    assert [c[1]["i"] for c in post.calls] == [0, 1, 2] and o.pending() == [] and all(c[0] == URL for c in post.calls)

  def test_no_network_keeps_everything_and_backs_off(self, tmp_path):
    o = self.box(tmp_path)
    s = Sender(o, FakePost(None))
    assert s.pump(URL, 100.0) == (0, 0)
    assert len(o.pending()) == 3
    assert s.pump(URL, 100.0 + BACKOFF_FIRST_S - 1) == (0, 0)            # still backing off: no attempt
    assert s.post.calls.__len__() == 1
    assert s.pump(URL, 100.0 + BACKOFF_FIRST_S + 1) == (3, 0)            # network is back
    assert o.pending() == []

  def test_backoff_doubles_up_to_the_cap(self, tmp_path):
    s = Sender(self.box(tmp_path), FakePost(*[None] * 20))
    t, waits = 0.0, []
    for _ in range(8):
      s.pump(URL, t)
      waits.append(s.backoff_s())
      t += s.backoff_s() + 1
    assert waits[:3] == [BACKOFF_FIRST_S, BACKOFF_FIRST_S * 2, BACKOFF_FIRST_S * 4] and waits[-1] == BACKOFF_MAX_S

  def test_server_errors_retry_but_client_errors_drop_the_item(self, tmp_path):
    o = self.box(tmp_path, 2)
    s = Sender(o, FakePost(500))
    assert s.pump(URL, 0.0) == (0, 0) and len(o.pending()) == 2            # 5xx: keep and retry
    o2 = self.box(tmp_path / "x", 2)
    assert Sender(o2, FakePost(404)).pump(URL, 0.0) == (1, 1) and o2.pending() == []   # 404 drops the first, the second sends
    o3 = self.box(tmp_path / "y", 1)
    assert Sender(o3, FakePost(429)).pump(URL, 0.0) == (0, 0) and len(o3.pending()) == 1   # 429: retry

  def test_unreadable_file_is_dropped_not_stuck(self, tmp_path):
    o = self.box(tmp_path, 1)
    (tmp_path / ".outbox" / "00000000T000000000000-000000.json").write_text("{not json")
    assert Sender(o, FakePost()).pump(URL, 0.0) == (1, 1) and o.pending() == []

  def test_bad_url_does_nothing(self, tmp_path):
    o = self.box(tmp_path)
    post = FakePost()
    assert Sender(o, post).pump("file:///etc/passwd", 0.0) == (0, 0) and post.calls == [] and len(o.pending()) == 3


def test_post_json_refuses_non_http_urls_without_touching_the_network():
  assert post_json("file:///etc/passwd", {"a": 1}) is None
  assert post_json("", {"a": 1}) is None
