"""Clip writer for the parked recorder.

The stream encoder publishes H.264 frames as cereal messages (header + data per frame). The recorder writes
them straight to <camera>.h264 inside the event directory: an Annex-B elementary stream that ffmpeg or VLC
plays, and that the server can remux later. Nothing is transcoded on the comma, which keeps CPU and power low.

Pure logic: no openpilot imports, time is passed in.
"""
import contextlib
from pathlib import Path

CLIP_SECONDS_RANGE = (5, 60)
STARTUP_TIMEOUT_S = 20.0           # camerad and the encoder take a few seconds to start producing frames
MAX_CLIP_BYTES = 120 * 1024 * 1024  # hard cap per event across all cameras, whatever the settings say


def clamp_clip_seconds(value) -> int:
  try:
    seconds = int(value)
  except (TypeError, ValueError):
    return 20
  return min(CLIP_SECONDS_RANGE[1], max(CLIP_SECONDS_RANGE[0], seconds))


class ClipWriter:
  """Collects frames for `clip_seconds` counted from the first frame received (not from the trigger,
  because the cameras need a few seconds to start). Gives up if no frame arrives within the startup timeout."""

  def __init__(self, event_dir: Path, cameras, clip_seconds: int, t_start: float,
               startup_timeout_s: float = STARTUP_TIMEOUT_S, max_bytes: int = MAX_CLIP_BYTES):
    self.event_dir = Path(event_dir)
    self.clip_seconds = clamp_clip_seconds(clip_seconds)
    self.t_start = t_start
    self.startup_timeout_s = startup_timeout_s
    self.max_bytes = max_bytes
    self.first_frame_t: float | None = None
    self.total_bytes = 0
    self._files = {cam: open(self.event_dir / f"{cam}.h264", "wb") for cam in cameras}
    self._frames = dict.fromkeys(cameras, 0)
    self._bytes = dict.fromkeys(cameras, 0)
    self._closed = False

  def add_frame(self, camera: str, header: bytes, data: bytes, now: float) -> None:
    f = self._files.get(camera)
    if f is None or self._closed or not data:
      return
    if self.first_frame_t is None:
      self.first_frame_t = now
    chunk = bytes(header) + bytes(data)   # header (SPS/PPS) on every frame keeps the stream decodable from any point
    f.write(chunk)
    self._frames[camera] += 1
    self._bytes[camera] += len(chunk)
    self.total_bytes += len(chunk)

  def state(self, now: float) -> str:
    """"waiting" (no frame yet), "recording", "done" or "failed" (no frame within the startup timeout)."""
    if self.first_frame_t is None:
      return "failed" if now - self.t_start >= self.startup_timeout_s else "waiting"
    if self.total_bytes >= self.max_bytes or now - self.first_frame_t >= self.clip_seconds:
      return "done"
    return "recording"

  def close(self) -> list[dict]:
    """Closes the files, deletes empty ones, and returns the catalog entries for what was kept."""
    self._closed = True
    kept = []
    for cam, f in self._files.items():
      with contextlib.suppress(OSError):
        f.close()
      path = self.event_dir / f"{cam}.h264"
      if self._frames[cam] == 0:
        with contextlib.suppress(OSError):
          path.unlink()
        continue
      kept.append({"name": path.name, "camera": cam, "bytes": self._bytes[cam], "frames": self._frames[cam]})
    return kept


class EventRateLimiter:
  """At most `max_events` captures in any `window_s` seconds, so a windy night cannot keep the cameras on."""

  def __init__(self, max_events: int = 12, window_s: float = 3600.0):
    self.max_events = max_events
    self.window_s = window_s
    self._times: list[float] = []

  def allow(self, now: float) -> bool:
    self._times = [t for t in self._times if now - t < self.window_s]
    return len(self._times) < self.max_events

  def record(self, now: float) -> None:
    self._times.append(now)
