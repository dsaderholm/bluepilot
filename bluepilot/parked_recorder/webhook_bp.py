"""Store-and-forward webhook for the parked recorder (Home Assistant, or anything that takes a JSON POST).

Events are written to an outbox directory first and sent later, so nothing is lost while the comma has no network and nothing
blocks the recorder. Pure logic around the file queue and one stdlib HTTP call that can be replaced in tests.
The URL is a secret (a Home Assistant webhook id is a password): it is never logged and never put in a payload.
"""
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

OUTBOX_MAX_ITEMS = 200
OUTBOX_MAX_AGE_S = 7 * 24 * 3600
POST_TIMEOUT_S = 5.0
BACKOFF_FIRST_S = 10.0
BACKOFF_MAX_S = 300.0


def valid_url(url) -> bool:
  if not isinstance(url, str) or not url.strip():
    return False
  p = urlparse(url.strip())
  return p.scheme in ("http", "https") and bool(p.netloc)


def build_payload(kind: str, **fields) -> dict:
  """kind is "bump", "warning" or "clip". Only small facts go in: no images, no GPS, no device identifiers."""
  return {"source": "parked_recorder", "kind": kind, "sentAt": datetime.now(UTC).isoformat(), **fields}


class Outbox:
  def __init__(self, path, max_items: int = OUTBOX_MAX_ITEMS, max_age_s: float = OUTBOX_MAX_AGE_S):
    self.path = Path(path)
    self.max_items = max_items
    self.max_age_s = max_age_s
    self._seq = 0

  def enqueue(self, payload: dict) -> Path:
    self.path.mkdir(parents=True, exist_ok=True)
    self._seq += 1
    name = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}-{self._seq:06d}.json"
    tmp = self.path / (name + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")))
    tmp.chmod(0o600)
    final = self.path / name
    tmp.replace(final)
    self.prune()
    return final

  def pending(self) -> list[Path]:
    if not self.path.is_dir():
      return []
    return sorted(p for p in self.path.iterdir() if p.suffix == ".json")

  def read(self, path: Path) -> dict | None:
    try:
      data = json.loads(path.read_text())
    except (OSError, ValueError):
      return None
    return data if isinstance(data, dict) else None

  def remove(self, path: Path) -> None:
    try:
      path.unlink()
    except OSError:
      pass

  def prune(self, now: float | None = None) -> None:
    """Drops items older than max_age_s, then the oldest beyond max_items."""
    now = datetime.now(UTC).timestamp() if now is None else now
    items = self.pending()
    for p in items:
      try:
        if now - p.stat().st_mtime > self.max_age_s:
          self.remove(p)
      except OSError:
        pass
    items = self.pending()
    for p in items[:max(0, len(items) - self.max_items)]:
      self.remove(p)


def post_json(url: str, payload: dict, timeout: float = POST_TIMEOUT_S) -> int | None:
  """Returns the HTTP status, or None when the server could not be reached. Only http(s) URLs are accepted."""
  if not valid_url(url):
    return None
  req = urllib.request.Request(url.strip(), data=json.dumps(payload).encode(), method="POST",
                               headers={"Content-Type": "application/json"})
  try:
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # scheme checked above
      return resp.status
  except urllib.error.HTTPError as e:
    return e.code
  except (urllib.error.URLError, OSError, ValueError):
    return None


class Sender:
  """Sends the oldest items first. A success removes the item. A 4xx other than 408/429 means the receiver will never accept it, so the
  item is dropped instead of blocking the queue. Anything else (no network, 5xx, 408, 429) stops this round and backs off."""

  def __init__(self, outbox: Outbox, post=post_json):
    self.outbox = outbox
    self.post = post
    self._failures = 0
    self._not_before = 0.0

  def backoff_s(self) -> float:
    return 0.0 if self._failures == 0 else min(BACKOFF_MAX_S, BACKOFF_FIRST_S * 2 ** (self._failures - 1))

  def pump(self, url: str, now: float) -> tuple[int, int]:
    """Returns (sent, dropped). Does nothing while backing off or if the URL is not valid."""
    if now < self._not_before or not valid_url(url):
      return 0, 0
    sent = dropped = 0
    for path in self.outbox.pending():
      payload = self.outbox.read(path)
      if payload is None:                      # unreadable file: drop it, it can never be sent
        self.outbox.remove(path)
        dropped += 1
        continue
      status = self.post(url, payload)
      if status is not None and 200 <= status < 300:
        self.outbox.remove(path)
        sent += 1
        self._failures = 0
      elif status is not None and 400 <= status < 500 and status not in (408, 429):
        self.outbox.remove(path)
        dropped += 1
      else:
        self._failures += 1
        self._not_before = now + self.backoff_s()
        break
    if sent and self._failures == 0:
      self._not_before = 0.0
    return sent, dropped
