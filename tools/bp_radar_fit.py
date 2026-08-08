#!/usr/bin/env python3
"""BluePilot: pick RadarDetectorMinBars from your own driving instead of from a guess.

    python tools/bp_radar_fit.py radar_alerts.jsonl

RadarDetectorMinBars ships at 6 of 8 with NO EVIDENCE BEHIND IT. This is the thing that replaces the
guess: it reads the encounter log written on the road and answers the only question that matters.

    Would this threshold have given me enough warning to actually reach the limit, and how often
    would it have fired for nothing?

Note "enough", not "how much". Seconds of notice mean nothing on their own -- they only mean
something next to how many seconds of slowing the situation needed, and that is what changes with
speed. Sixteen seconds is generous at 45 mph and may be nothing at 85, where there is four times as
much to bleed off. So this reports BY SPEED BAND rather than lumping every encounter together: a
threshold fitted from town driving can look excellent and be useless on the freeway, which is
exactly where it matters.

THE COAST RATE IS MEASURED, NOT ASSUMED
---------------------------------------
How fast this car actually sheds speed with Ford's ACC coasting depends on the car, the grade and
the wind, and nobody can supply it in advance. It is read back from the encounters where the car
acted -- which are the same encounters excluded from the threshold fit below. The data that cannot
answer one question turns out to be the only thing that answers the other.

ENCOUNTERS WHERE THE CAR ACTED ARE EXCLUDED FROM THE FIT, and this is not fastidiousness.

Slowing changes the rate you close on the source, which changes how fast the bar count climbs --
which is the exact quantity being measured. Including those encounters would fit the threshold to
data the threshold itself produced, and the bias runs the flattering way: warning times look longer
than they were, so any threshold looks better than it is. Third instance of the same trap in this
feature; the other two are in locations.py.

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

MS_TO_MPH = 2.23694

# Mirrored from esp_protocol rather than imported: this runs on a laptop against a log file,
# and should not need the openpilot tree on the path to be useful.
BAND_LASER, BAND_KA, BAND_K, BAND_X, BAND_KU = 1, 2, 4, 8, 16

# Speed bands, in mph. Split where the DRIVING changes rather than on round numbers: town, the
# arterials and slower interstates, and the fast interstate where there is most speed to shed and
# least time to shed it.
BANDS = ((0, 50, "town"), (50, 70, "arterial"), (70, 999, "freeway"))


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


def crossing(rec: dict, threshold: int) -> dict | None:
  """The sample where this encounter first reached `threshold` bars, or None."""
  for smp in rec["samples"]:
    if smp["bars"] >= threshold:
      return smp
  return None


def band_of(mph: float) -> str:
  for lo, hi, name in BANDS:
    if lo <= mph < hi:
      return name
  return BANDS[-1][2]


def coast_rate(records) -> float | None:
  """How fast this car sheds speed, in mph/s, measured from encounters where it acted.

  Median rather than mean: one encounter on a downgrade, or one where the driver braked, would drag
  an average somewhere the car cannot actually go on its own.
  """
  rates = []
  for r in records:
    smp = [s for s in r["samples"] if s.get("v_ego", 0) > 0]
    if len(smp) < 2:
      continue
    dt = smp[-1]["t"] - smp[0]["t"]
    dv = (smp[0]["v_ego"] - smp[-1]["v_ego"]) * MS_TO_MPH
    if dt > 2.0 and dv > 0.5:            # actually slowed, over long enough to measure
      rates.append(dv / dt)
  if not rates:
    return None
  rates.sort()
  return rates[len(rates) // 2]


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


def seconds_needed(hits, rate) -> list[float]:
  """Seconds of coasting each encounter would have needed to reach the posted limit.

  Empty when the log predates the `limit` field or no limit was known, which is why the caller
  treats a missing value as "cannot judge" rather than as "fine".
  """
  out = []
  if not rate:
    return out
  for _r, c in hits:
    limit_mph = c.get("limit", 0) * MS_TO_MPH
    over = c.get("v_ego", 0) * MS_TO_MPH - limit_mph
    if limit_mph > 1 and over > 0:
      out.append(over / rate)
  return out


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

  print()
  print(f"  {len(records)} encounters: {len(ka)} clean Ka, {len(acted)} Ka where the car acted, "
        f"{len(muted_ka)} muted Ka, {len(non_ka)} non-Ka")
  print("  Non-Ka is context only -- the set-speed path gates on Ka before strength is consulted.")
  if acted:
    print("  The acted-on ones are excluded from the fit: slowing changes how fast the bars climb,")
    print("  so they would measure the threshold's own effect. They set the coast rate instead.")

  bands = collections.Counter()
  for r in records:
    for bit, name in ((BAND_LASER, "laser"), (BAND_KA, "Ka"), (BAND_K, "K"),
                      (BAND_X, "X"), (BAND_KU, "Ku")):
      if r.get("bands_seen", 0) & bit:
        bands[name] += 1
  print(f"  bands: {dict(bands)}")

  if not ka:
    print()
    if acted:
      print(f"  All {len(acted)} Ka encounters had the car acting, so none can be fitted against.")
      print("  Switch RadarDetectorSlowdownEnabled off for a fortnight to collect clean data.")
      return 0
    print("  No unmuted Ka encounters yet, so there is nothing to fit. That is a real answer: the")
    print("  set-speed change would not have fired at all on this data.")
    return 0

  rate = coast_rate(acted) if acted else None
  print()
  if rate:
    print(f"  Coast rate: {rate:.2f} mph/s, measured from the {len(acted)} encounters where the car")
    print("  acted. Nobody can supply that in advance -- it depends on the car, grade and wind.")
  else:
    print("  Coast rate not measurable yet, so the 'need' column below is blank. It needs")
    print("  encounters where the car actually slowed, which only happen once this is switched on.")

  by_band = collections.defaultdict(list)
  for r in ka:
    first = crossing(r, 1)
    if first:
      by_band[band_of(first.get("v_ego", 0) * MS_TO_MPH)].append(r)

  best = {}
  for _lo, _hi, name in BANDS:
    group = by_band.get(name, [])
    if not group:
      continue
    print()
    print(f"  === {name} ({len(group)} encounters) ===")
    print("  bars   fires   median    worst     need    muted")
    print("                warn s   warn s        s   Ka hit")
    print("  " + "-" * 50)
    for t in range(1, 9):
      hits = [(r, c) for r, c in ((r, crossing(r, t)) for r in group) if c is not None]
      warns = sorted(b for b in (budget(r, t) for r, _ in hits) if b is not None)
      falses = sum(1 for r in muted_ka if budget(r, t) is not None)
      if not warns:
        print(f"  {t:>4}  {0:>6}  {'-':>7}  {'-':>7}  {'-':>7}  {falses:>6}")
        continue

      # The column that turns a warning time into a verdict. Keyed on the WORST case in the band --
      # the encounter that needed the most slowing is the one that decides whether a threshold is
      # usable, not the typical one.
      needs = seconds_needed(hits, rate)
      need = f"{max(needs):.1f}" if needs else "-"
      median, worst = warns[len(warns) // 2], warns[0]
      late = bool(needs) and worst < max(needs)
      print(f"  {t:>4}  {len(warns):>6}  {median:>7.1f}  {worst:>7.1f}  {need:>7}  {falses:>6}"
            f"{'   <- arrives too late' if late else ''}")

      if (len(warns) >= max(2, len(group) // 2) and not late
          and falses <= max(1, len(muted_ka) * 0.2)):
        best[name] = t

  print()
  if not best:
    print("  No threshold clears the bar in any band. Read that carefully before assuming it means")
    print("  'not enough data' -- if every row says 'arrives too late', the problem is not the")
    print("  threshold. It means COASTING CANNOT SHED THE SPEED IN THE TIME AVAILABLE, and no bar")
    print("  count fixes that, because even 1 bar fires as early as this detector can see.")
    print()
    print("  In that case the answer is a harder drop rather than an earlier one: let the request")
    print("  bypass ICBM's target-drop limiter the way the radar-blind lead path does, instead of")
    print("  coasting. That is a real change with a real cost -- Ford brakes rather than coasts, and")
    print("  the brake lights come on, which is the thing the rear-radar case was trying to avoid.")
    print("  Worth knowing that this data is what decides it rather than anyone's judgement.")
  elif len(set(best.values())) == 1:
    only = next(iter(best.values()))
    print(f"  Suggested RadarDetectorMinBars: {only}, and it is the SAME in every band.")
    print("  That is a real finding rather than a non-answer: speed does not need its own scaling,")
    print("  one number is enough, and the graded response is complexity you do not have to carry.")
  else:
    print("  Speed MATTERS -- the right threshold differs by band:")
    for _lo, _hi, name in BANDS:
      if name in best:
        print(f"    {name:>9}: {best[name]} bars")
    print()
    print(f"  A single setting has to take the lowest, {min(best.values())}, or the fast roads get a")
    print("  warning too late to use. That is the argument for scaling the threshold by speed.")
  print(f"  (aiming {args.margin} mph under the limit; set it on the Speed Limit screen)")
  print()
  print("  These are YOUR roads. Nothing here is a default anyone else should inherit.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
