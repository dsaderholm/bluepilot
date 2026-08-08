#!/usr/bin/env python3
"""BluePilot: pick RadarDetectorMinBars from your own driving instead of from a guess.

    python tools/bp_radar_fit.py radar_alerts.jsonl

RadarDetectorMinBars ships at 6 of 8 with NO EVIDENCE BEHIND IT. This is the thing that replaces the
guess: it reads the encounter log written on the road and answers, for each candidate threshold, the
only question that matters.

    How many seconds of warning would this threshold have given me, and how often would it have
    fired for nothing?

Both halves are needed. A low threshold buys warning time and costs false triggers; a high one is
quiet and may fire too late to be worth anything. Neither number can be reasoned out in advance --
signal strength is not distance, and how far ahead a given bar count sits depends on the terrain and
the road, which is exactly why this waits for real drives.

ENCOUNTERS WHERE THE CAR ACTED ARE EXCLUDED FROM THE FIT, and this is not fastidiousness.

Slowing changes the rate you close on the source, which changes how fast the bar count climbs --
which is the exact quantity being measured. Including those encounters would fit the threshold to
data the threshold itself produced, and the bias runs the flattering way: warning times look longer
than they were, so any threshold looks better than it is. Third instance of the same trap in this
feature; the other two are in locations.py.

They are still counted and reported, because "how often did it fire" is worth knowing. They just do
not get a vote on the number.

WHAT COUNTS AS A FALSE TRIGGER: a Ka encounter you MUTED that still reached the threshold. Those are
the ones where the car would have slowed for something you had already dismissed.

Non-Ka encounters are shown for context and deliberately NOT counted. The set-speed path gates on Ka
before strength is ever consulted, so a K-band supermarket door at 8 bars cannot fire it however
loud it gets. An earlier version of this tool counted them, which made every threshold look
hopeless and refused to suggest anything -- a wrong answer built from evidence that does not apply.
"""
import argparse
import collections
import json

# Mirrored from esp_protocol rather than imported: this runs on a laptop against a log file,
# and should not need the openpilot tree on the path to be useful.
BAND_LASER, BAND_KA, BAND_K, BAND_X, BAND_KU = 1, 2, 4, 8, 16


def load(path: str) -> list[dict]:
  out = []
  with open(path) as f:
    for line in f:
      line = line.strip()
      if not line:
        continue
      try:
        rec = json.loads(line)
      except ValueError:
        continue          # a torn last line after a power cut is expected, not an error
      if isinstance(rec, dict) and rec.get("samples"):
        out.append(rec)
  return out


def budget(rec: dict, threshold: int) -> float | None:
  """Seconds between first reaching `threshold` bars and the encounter's peak.

  None when it never got there. This is the warning time that threshold would have bought on this
  encounter -- the whole reason the log records a time series rather than a single peak.
  """
  samples = rec["samples"]
  peak = max(s["bars"] for s in samples)
  if peak < threshold:
    return None
  first = next(s["t"] for s in samples if s["bars"] >= threshold)
  at_peak = next(s["t"] for s in samples if s["bars"] == peak)
  return max(at_peak - first, 0.0)


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("log")
  ap.add_argument("--margin", type=int, default=1, help="mph under the limit, for context only")
  args = ap.parse_args()

  try:
    records = load(args.log)
  except OSError as e:
    print(f"cannot read {args.log}: {e}")
    return 1

  if not records:
    print(f"no usable encounters in {args.log}.")
    print("Drive with RadarDetectorEnabled on for a couple of weeks and come back.")
    return 1

  ka_all = [r for r in records if (r.get("bands_seen", 0) & BAND_KA) and not r.get("ever_muted")]
  acted = [r for r in ka_all if r.get("ever_acted")]
  ka = [r for r in ka_all if not r.get("ever_acted")]
  muted_ka = [r for r in records if (r.get("bands_seen", 0) & BAND_KA) and r.get("ever_muted")]
  non_ka = [r for r in records if not (r.get("bands_seen", 0) & BAND_KA)]
  print(f"\n  {len(records)} encounters logged: {len(ka)} unmuted Ka, {len(muted_ka)} muted Ka, "
        f"{len(non_ka)} non-Ka")
  print("  Non-Ka is context only -- the set-speed path gates on Ka before strength is consulted,")
  print("  so a K-band supermarket door at 8 bars cannot fire it however loud it gets.")
  if acted:
    print(f"  {len(acted)} Ka encounters EXCLUDED from the fit because the car slowed during them.")
    print("  Slowing changes how fast the bars climb, so those measure the threshold's own effect.")

  bands = collections.Counter()
  for r in records:
    for bit, name in ((BAND_LASER, "laser"), (BAND_KA, "Ka"), (BAND_K, "K"),
                      (BAND_X, "X"), (BAND_KU, "Ku")):
      if r.get("bands_seen", 0) & bit:
        bands[name] += 1
  print(f"  bands: {dict(bands)}")

  if not ka:
    print("\n  No unmuted Ka encounters yet, so there is nothing to fit. That is a real answer:")
    print("  it means the set-speed change would not have fired at all on this data.")
    return 0

  print(f"\n  {'bars':>4}  {'fires':>6}  {'median':>7}  {'worst':>7}  {'false':>6}")
  print(f"  {'':>4}  {'':>6}  {'warn s':>7}  {'warn s':>7}  {'fires':>6}")
  print("  " + "-" * 40)

  best = None
  for t in range(1, 9):
    warns = [b for b in (budget(r, t) for r in ka) if b is not None]
    falses = sum(1 for r in muted_ka if budget(r, t) is not None)
    if not warns:
      print(f"  {t:>4}  {0:>6}  {'-':>7}  {'-':>7}  {falses:>6}")
      continue
    warns.sort()
    median = warns[len(warns) // 2]
    worst = warns[0]
    print(f"  {t:>4}  {len(warns):>6}  {median:>7.1f}  {worst:>7.1f}  {falses:>6}")
    # A defensible pick: fires on most real Ka, and the WORST case still leaves time to coast off
    # a few mph. Deliberately keyed on the worst warning rather than the median -- the encounter
    # that matters is the one that gave you the least notice, not the typical one.
    if len(warns) >= max(3, len(ka) // 2) and worst >= 4.0 and falses <= max(1, len(muted_ka) * 0.2):
      best = t

  print()
  if best is None:
    print("  No threshold clears the bar on this data: either too few encounters, or every one")
    print("  that fires also fires on things you did not want. Keep driving, or accept a lower")
    print("  bar knowing what it costs -- the columns above are the whole trade.")
  else:
    print(f"  Suggested RadarDetectorMinBars: {best}")
    print(f"  (aiming {args.margin} mph under the limit; set it on the Speed Limit screen)")
  print("\n  These are YOUR roads. Nothing here is a default anyone else should inherit.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
