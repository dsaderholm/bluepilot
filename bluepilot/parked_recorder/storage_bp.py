"""Event storage for the parked recorder.

Events live in their own directory, outside Paths.log_root(), so loggerd's deleter and the stock uploader
never see them. Each event is a directory holding event.json (the catalog entry) plus its media files.
Pure file operations, no openpilot imports.
"""
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

EVENTS_ROOT = "/data/media/0/parked_recorder"
EVENT_FILE = "event.json"
UPLOADED_MARK = ".uploaded"   # written by the uploader once every file of the event is on the server
MIN_STORAGE_MB, MAX_STORAGE_MB = 256, 8192


def clamp_storage_mb(value) -> int:
  try:
    mb = int(value)
  except (TypeError, ValueError):
    return 2048
  return min(MAX_STORAGE_MB, max(MIN_STORAGE_MB, mb))


def _write_json_atomic(path: Path, data: dict) -> None:
  tmp = path.with_suffix(".tmp")
  tmp.write_text(json.dumps(data, separators=(",", ":")))
  tmp.chmod(0o600)
  tmp.replace(path)


def new_event_dir(root: str, when: datetime | None = None) -> Path:
  """Creates <root>/<id>/ with an id made of [A-Za-z0-9-] only, so it is a valid opconnect upload key."""
  when = when or datetime.now(UTC)
  base = when.strftime("%Y%m%dT%H%M%SZ")
  Path(root).mkdir(parents=True, exist_ok=True)
  for n in range(100):
    event_id = base if n == 0 else f"{base}-{n}"
    path = Path(root) / event_id
    try:
      path.mkdir()
      return path
    except FileExistsError:
      continue
  raise FileExistsError(f"no free event id under {root} for {base}")


def write_event(event_dir: Path, info: dict) -> dict:
  entry = {"eventId": event_dir.name, **info}
  _write_json_atomic(event_dir / EVENT_FILE, entry)
  return entry


def _dir_size(path: Path) -> int:
  total = 0
  for p in path.rglob("*"):
    try:
      if p.is_file():
        total += p.stat().st_size
    except OSError:
      pass
  return total


def list_events(root: str) -> list[Path]:
  """Event directories, oldest first (ids sort by time)."""
  r = Path(root)
  if not r.is_dir():
    return []
  return sorted(p for p in r.iterdir() if p.is_dir() and not p.name.startswith("."))


def enforce_quota(root: str, max_mb: int, keep: Path | None = None) -> list[str]:
  """Deletes oldest events until the total is under max_mb. Never deletes `keep` (the event being written).
  Returns the ids it removed."""
  limit = clamp_storage_mb(max_mb) * 1024 * 1024
  events = list_events(root)
  sizes = {e: _dir_size(e) for e in events}
  total = sum(sizes.values())
  removed = []
  # events already safe on the server go first (oldest first), then the oldest that are not
  for e in sorted(events, key=lambda ev: not (ev / UPLOADED_MARK).exists()):
    if total <= limit:
      break
    if keep is not None and e == keep:
      continue
    shutil.rmtree(e, ignore_errors=True)
    total -= sizes[e]
    removed.append(e.name)
  return removed


def free_bytes(root: str) -> int | None:
  try:
    path = root
    while not os.path.exists(path):
      path = os.path.dirname(path) or "/"
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize
  except OSError:
    return None


def now_iso() -> str:
  return datetime.now(UTC).isoformat()
