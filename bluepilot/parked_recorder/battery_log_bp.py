"""Parked battery log: CSV rows and a summary of the drain, with no openpilot imports.

The point is to measure, in the real car, what the comma costs the 12 V battery while parked, before the 4 h
window and the 12.3 V floor are trusted. A run is labelled (for example "stock", "recorder on", "modem+vpn")
so the configurations can be compared.
"""
import csv
import os
from collections import defaultdict
from dataclasses import dataclass

LOG_PATH = "/data/media/0/parked_recorder/battery_log.csv"
MAX_LOG_BYTES = 5 * 1024 * 1024
SAMPLE_INTERVAL_S = 30.0

COLUMNS = ["wallTime", "monoTime", "label", "voltageMv", "filteredMv", "powerDrawW", "somPowerDrawW", "offroadUwh",
           "maxTempC", "cpuPercent", "network", "screenPercent", "recorderState", "capture"]


def append_row(path: str, row: dict, max_bytes: int = MAX_LOG_BYTES) -> None:
  """Appends one row (writing the header for a new file). When the file passes max_bytes it is moved to
  <path>.1 (replacing the previous one), so the log never grows without bound."""
  os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
  try:
    if os.path.getsize(path) >= max_bytes:
      os.replace(path, path + ".1")
  except OSError:
    pass
  new = not os.path.exists(path) or os.path.getsize(path) == 0
  with open(path, "a", newline="") as f:
    w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
    if new:
      w.writeheader()
    w.writerow({c: row.get(c, "") for c in COLUMNS})


@dataclass(frozen=True)
class RunSummary:
  label: str
  samples: int
  hours: float
  start_v: float
  end_v: float
  slope_v_per_h: float     # least-squares slope of the filtered voltage; negative means draining
  mean_power_w: float
  max_power_w: float


def _f(x) -> float | None:
  try:
    return float(x)
  except (TypeError, ValueError):
    return None


def _slope(xs: list[float], ys: list[float]) -> float:
  n = len(xs)
  if n < 2:
    return 0.0
  mx, my = sum(xs) / n, sum(ys) / n
  den = sum((x - mx) ** 2 for x in xs)
  return 0.0 if den == 0 else sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / den


def summarize(path: str) -> list[RunSummary]:
  """One summary per label, in the order labels first appear. Rows with no usable voltage are skipped."""
  groups: dict[str, list[dict]] = defaultdict(list)
  try:
    with open(path, newline="") as f:
      for row in csv.DictReader(f):
        groups[row.get("label", "")].append(row)
  except OSError:
    return []

  out = []
  for label, rows in groups.items():
    pts = [(_f(r["monoTime"]), _f(r["filteredMv"]), _f(r["powerDrawW"])) for r in rows]
    pts = [(t, v, p) for t, v, p in pts if t is not None and v is not None and v > 0]
    if len(pts) < 2:
      continue
    ts = [t for t, _, _ in pts]
    vs = [v / 1000.0 for _, v, _ in pts]
    ps = [p for _, _, p in pts if p is not None]
    hours = (max(ts) - min(ts)) / 3600.0
    out.append(RunSummary(label=label, samples=len(pts), hours=hours, start_v=vs[0], end_v=vs[-1],
                          slope_v_per_h=_slope([t / 3600.0 for t in ts], vs),
                          mean_power_w=sum(ps) / len(ps) if ps else 0.0, max_power_w=max(ps) if ps else 0.0))
  return out


if __name__ == "__main__":
  import sys
  for r in summarize(sys.argv[1] if len(sys.argv) > 1 else LOG_PATH):
    parts = [f"{r.label or '(no label)':<20}", f"{r.hours:5.2f} h", f"{r.start_v:5.2f} V -> {r.end_v:5.2f} V",
             f"{r.slope_v_per_h * 1000:+7.1f} mV/h", f"mean {r.mean_power_w:4.1f} W", f"max {r.max_power_w:4.1f} W",
             f"({r.samples} samples)"]
    print("  ".join(parts))
