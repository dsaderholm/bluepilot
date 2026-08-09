"""
FusionPilot: recording radar detector encounters so the strength threshold can be FITTED.

RadarDetectorMinBars ships at a number with no evidence behind it, and no amount of reasoning will
produce a better one -- signal strength is not distance, and how far ahead a given bar count sits
depends on terrain, the road, and what the source is doing. The only way to pick it is to drive
with the readout live and the set-speed override off, then look at what actually happened.

So this file exists to make one specific question answerable after the fact:

    How many seconds elapse between a Ka alert first reaching N bars and it peaking?

That is the warning budget at threshold N. Compare it against how long the car needs to coast from
your usual cruising speed down to the limit and the choice of N stops being a judgment call. It is
also why a single "an alert happened at this GPS point" record would be useless -- the shape over
time IS the measurement.

WHAT A RECORD IS
----------------
One JSON object per ENCOUNTER, not per frame. An encounter opens on the first frame the detector
shows any band and closes after ENCOUNTER_GAP_S with nothing. Between those it carries a series of
samples, each with a monotonic offset from the encounter start, the bar count, the band bits, the
arrow bits, and the car's speed.

SAMPLING IS ON CHANGE, PLUS A HEARTBEAT
---------------------------------------
Logging every frame would be 20 samples a second for a metric whose interesting feature is where
the bar count steps. Recording only when something changes, with a slow heartbeat so a long steady
alert still shows its duration, is lossless for the question above and roughly two orders of
magnitude smaller.

FILE I/O IS INJECTED
--------------------
The writer is a callable handed in, so the whole of this is testable without touching a disk. On
the device it appends a line to a file under /data; in tests it appends to a list. Nothing here
knows which.

PRIVACY, SUCH AS IT IS
----------------------
This records where you were driving and when. It stays on the device, it is not uploaded anywhere,
and it exists to be read at home over SSH and then deleted. It is worth being deliberate about that
rather than discovering later that a position log rode along with something else.
"""

import json

# A gap this long with no bands showing ends the encounter. Generous on purpose: fringe alerts
# flicker, and splitting one approach into six records would destroy the very measurement this
# exists for -- the time from first detection to peak.
ENCOUNTER_GAP_S = 10.0

# Never record a sample more often than this, however fast things change.
MIN_SAMPLE_INTERVAL_S = 0.2
# ...and always record one at least this often, so a long steady alert still shows its duration.
HEARTBEAT_S = 2.0

# Bound on samples per encounter. At the heartbeat rate this is over half an hour of continuous
# alerting; anything longer is a stuck detector or a parked car next to a door opener, and the tail
# of that is not worth unbounded memory in a control-loop process.
MAX_SAMPLES = 1000

# Encounters with fewer than this many samples are dropped rather than written. A single-frame blip
# has no time series, so it cannot answer the question this log exists for, and thousands of them
# would bury the encounters that can.
MIN_SAMPLES_TO_KEEP = 2


class RadarAlertLog:
  """Accumulate one encounter at a time; hand a finished one to the writer."""

  def __init__(self, writer=None):
    """writer: called with one dict per completed encounter. None disables writing entirely."""
    self.writer = writer
    self._open: dict | None = None
    self._start_t = 0.0
    self._last_seen_t = 0.0
    self._last_sample_t = 0.0
    self._last_key: tuple | None = None
    self._last_obs: dict | None = None
    self.written = 0
    self.dropped_short = 0

  def update(self, display, lat: float, lon: float, v_ego: float, acting: bool, now: float,
             speed_limit: float = 0.0) -> None:
    """One frame.

    Args:
      display: decoded front panel, or None when the link is down. None closes any open encounter
        -- we genuinely do not know what the detector is showing, and guessing would put a fake
        quiet tail on the end of a real approach.
      lat, lon: position. Zeros mean no fix and are recorded as such rather than as the Gulf of
        Guinea, matching pinned_holds.
      v_ego: speed in m/s. SI here; the analysis converts.
      acting: whether the set-speed override was active this frame. The point of comparison for
        "would this threshold have fired, and how early".
      speed_limit: the posted limit (m/s), 0 when unknown. Recorded because without it the log
        cannot answer the question it exists for at any given speed: the warning budget only matters
        relative to how much speed there was to shed, and that is v_ego minus the limit. Added after
        the first version could tell you how many seconds of notice you got and not whether those
        seconds were enough.
      now: monotonic seconds.
    """
    alerting = display is not None and display.searching and bool(display.bands)

    if not alerting:
      if self._open is not None and now - self._last_seen_t >= ENCOUNTER_GAP_S:
        self._close(now)
      return

    self._last_seen_t = now

    if self._open is None:
      self._open = {
        "start_lat": round(lat, 6),
        "start_lon": round(lon, 6),
        "start_v_ego": round(v_ego, 2),
        "peak_bars": 0,
        "bands_seen": 0,
        "ever_muted": False,
        "ever_acted": False,
        "samples": [],
      }
      self._start_t = now
      self._last_sample_t = -1e9
      self._last_key = None

    enc = self._open
    enc["peak_bars"] = max(enc["peak_bars"], display.bars)
    enc["bands_seen"] |= display.bands
    enc["ever_muted"] = enc["ever_muted"] or display.muted
    enc["ever_acted"] = enc["ever_acted"] or acting

    # Sample when something meaningful changed, or on the heartbeat -- but never faster than the
    # floor. See the module docstring.
    key = (display.bars, display.bands, display.arrows, display.muted)
    changed = key != self._last_key
    due = now - self._last_sample_t >= HEARTBEAT_S
    # The most recent state, kept so the encounter can be closed with a final sample at the moment
    # it actually ended. Without it a steady alert produces one sample at the open and nothing else,
    # which has no duration and gets dropped as too short -- and a changing one ends at whenever the
    # last change happened rather than at the end of the encounter.
    self._last_obs = {
      "t": round(now - self._start_t, 2),
      "bars": display.bars,
      "bands": display.bands,
      "arrows": display.arrows,
      "muted": display.muted,
      "v_ego": round(v_ego, 2),
      "lat": round(lat, 6),
      "lon": round(lon, 6),
      "limit": round(speed_limit, 2),
    }

    if (changed or due) and now - self._last_sample_t >= MIN_SAMPLE_INTERVAL_S:
      self._append(dict(self._last_obs))
      self._last_sample_t = now
      self._last_key = key

  def _append(self, sample: dict) -> None:
    if self._open is not None and len(self._open["samples"]) < MAX_SAMPLES:
      self._open["samples"].append(sample)

  def flush(self, now: float) -> None:
    """Close any open encounter. For shutdown, so the last one of a drive is not lost."""
    if self._open is not None:
      self._close(now)

  def _close(self, now: float) -> None:
    enc = self._open
    if enc is None:
      return

    # Close with a sample at the moment the alert actually stopped, unless one already sits there.
    #
    # Two things depend on this. A steady alert otherwise produces exactly one sample -- recorded at
    # the open, never changing, never reaching a heartbeat if it is short -- which has no duration
    # and gets thrown away as too short despite being a perfectly real encounter. And an encounter
    # that did change ends at whenever its last change happened, so the tail of every approach is
    # silently missing.
    if self._last_obs is not None and self._last_obs["t"] > 0 and \
       (not enc["samples"] or enc["samples"][-1]["t"] < self._last_obs["t"]):
      self._append(dict(self._last_obs))

    self._open = None
    self._last_obs = None
    if len(enc["samples"]) < MIN_SAMPLES_TO_KEEP:
      self.dropped_short += 1
      return
    enc["duration"] = round(self._last_seen_t - self._start_t, 2)
    if self.writer is not None:
      self.writer(enc)
      self.written += 1


def file_writer(path: str):
  """Append-one-JSON-line-per-encounter writer.

  Opens and closes per record rather than holding a handle. Encounters are rare -- a handful an
  hour at most -- so the cost is irrelevant, and it means a power cut cannot lose buffered records
  or leave a half-written file behind.

  Never raises. A full or read-only filesystem must not take down the process that is driving the
  car for the sake of a diagnostic log.
  """
  def write(record: dict) -> None:
    try:
      with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")
    except Exception:  # noqa: BLE001 - see docstring
      pass
  return write
