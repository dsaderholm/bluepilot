"""Upload of parked-recorder events to the self-hosted backend (opconnect), one event at a time.

Pure logic: the two network calls (ask for a signed URL, PUT the file) are passed in, so this is tested without a network.
An event is a directory <root>/<event-id>/ holding event.json and the media; each file is uploaded with the key <event-id>/<file>.
Footage never leaves the device unless the owner turned the upload on, the backend is the self-hosted one, and the link is
unmetered Wi-Fi (the process checks those; this module only does the transfer).
"""
import json
import re
from pathlib import Path

from bluepilot.parked_recorder import storage_bp
from bluepilot.parked_recorder.webhook_bp import BACKOFF_FIRST_S, BACKOFF_MAX_S

SENT_SUFFIX = ".sent"
STATE_FILE = ".upload_state.json"
SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
DAILY_MB_RANGE = (50, 10000)


def clamp_daily_mb(value) -> int:
  try:
    mb = int(value)
  except (TypeError, ValueError):
    return 1000
  return min(DAILY_MB_RANGE[1], max(DAILY_MB_RANGE[0], mb))


def event_files(event_dir: Path) -> list[Path]:
  """The files to send, media first and event.json last, so the catalog entry only arrives once its clips have."""
  files = [p for p in event_dir.iterdir()
           if p.is_file() and not p.name.startswith(".") and not p.name.endswith((SENT_SUFFIX, ".tmp")) and SAFE_KEY.match(p.name)]
  return sorted(files, key=lambda p: (p.name == storage_bp.EVENT_FILE, p.name))


def is_uploaded(event_dir: Path) -> bool:
  return (event_dir / storage_bp.UPLOADED_MARK).exists()


def pending_events(root: str) -> list[Path]:
  """Captured events not yet on the server, oldest first. An event with no media (rate limited, no frames) has nothing to send."""
  out = []
  for ev in storage_bp.list_events(root):
    if is_uploaded(ev):
      continue
    try:
      entry = json.loads((ev / storage_bp.EVENT_FILE).read_text())
    except (OSError, ValueError):
      continue
    if isinstance(entry, dict) and entry.get("captured") and SAFE_KEY.match(ev.name):
      out.append(ev)
  return out


class DailyCap:
  """Bytes uploaded today, kept in a small file so a restart does not reset the cap."""

  def __init__(self, root: str):
    self.path = Path(root) / STATE_FILE

  def used(self, today: str) -> int:
    try:
      data = json.loads(self.path.read_text())
      return int(data["bytes"]) if data.get("date") == today else 0
    except (OSError, ValueError, KeyError, TypeError):
      return 0

  def add(self, today: str, n: int) -> None:
    try:
      self.path.parent.mkdir(parents=True, exist_ok=True)
      self.path.write_text(json.dumps({"date": today, "bytes": self.used(today) + n}))
    except OSError:
      pass


class EventUploader:
  """get_url(key) -> (url, headers) or None; put(url, headers, path) -> HTTP status or None (None = no connection)."""

  def __init__(self, root: str, get_url, put):
    self.root = root
    self.get_url = get_url
    self.put = put
    self.cap = DailyCap(root)
    self._failures = 0
    self._not_before = 0.0

  def _backoff(self) -> float:
    return min(BACKOFF_MAX_S, BACKOFF_FIRST_S * 2 ** (self._failures - 1))

  def step(self, now: float, today: str, cap_mb: int) -> str:
    """One unit of work: returns "idle", "backoff", "capped", "failed", "progress" (a file went up) or "uploaded" (an event finished)."""
    if now < self._not_before:
      return "backoff"
    events = pending_events(self.root)
    if not events:
      return "idle"
    ev = events[0]
    cap = clamp_daily_mb(cap_mb) * 1024 * 1024
    for f in event_files(ev):
      mark = ev / (f.name + SENT_SUFFIX)
      if mark.exists():
        continue
      size = f.stat().st_size
      if self.cap.used(today) + size > cap:
        return "capped"
      target = self.get_url(f"{ev.name}/{f.name}")
      status = self.put(*target, f) if target else None
      if status is not None and 200 <= status < 300:
        mark.write_text("ok")
        self.cap.add(today, size)
        self._failures = 0
        return "progress"
      if status is not None and 400 <= status < 500 and status not in (408, 429, 401, 403):
        mark.write_text("rejected")      # the server will never accept this file: do not block the queue on it
        continue
      self._failures += 1
      self._not_before = now + self._backoff()
      return "failed"
    (ev / storage_bp.UPLOADED_MARK).write_text("ok")
    return "uploaded"
