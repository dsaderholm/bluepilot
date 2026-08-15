#!/usr/bin/env python3
"""FusionPilot: what Ford's follow-gap settings 1-5 actually ARE, in seconds.

The owner set his gap by feel and believes 3 of 5 gives him about two seconds. Nobody had measured
it. Every decision about whether a closer gap buys enough room to pass rests on that number.

Both halves are already logged: the setting is `AccTGap_D_Dsply` in ACCDATA_3, and the lead distance
is in radarState. Headway is dRel / vEgo.

WHAT MAKES THIS HARDER THAN A DIVISION
--------------------------------------
Most frames with a lead say nothing about the gap SETTING, and the first version of this tool got
that wrong in a way worth recording, because the wrong version still produces a confident number.

  1. IF THE SET SPEED BINDS, THE GAP IS NOT WHAT IS HOLDING THE CAR BACK. Cruising at 70 behind a
     car doing 72, headway is whatever the geometry happens to be. Those frames are excluded by
     requiring vEgo to sit meaningfully BELOW the dash set speed -- that is the definition of ACC
     being gap-limited, and it is the single most important filter here. Measured on route 365 and
     neighbours it removes about a quarter of otherwise-qualifying frames.

  2. INSTANTANEOUS CLOSING RATE IS TOO NOISY TO GATE ON. |vRel| has a median of 0.9 m/s and a p75 of
     2.4 m/s during perfectly ordinary steady following, so "every frame under 0.5 m/s for two
     seconds" almost never happens -- the first version of this tool returned ZERO samples from
     46,000 radar frames for exactly that reason. Steadiness is a property of a WINDOW: the mean
     closing rate near zero, and the headway itself barely moving across it.

STEADY-STATE IS THE ONLY STATE THAT REPORTS THE SETTING. Everything above is in service of that.

READING THE SETTING
-------------------
Prefers `carStateBP.accGap`, added 2026-08-14. Falls back to decoding address 394 out of raw CAN,
so routes recorded before that field existed still measure -- which is every route on the device as
of 2026-08-15. Byte 4, low 3 bits: DBC start bit 34, length 3, big-endian, so bits 34/33/32 are
byte 4 bits 2/1/0.

USAGE, on the device:

    cd /data/openpilot && python tools/bp_gap_seconds.py
    python tools/bp_gap_seconds.py --routes 6           # pool the last 6 routes
    python tools/bp_gap_seconds.py --route 00000042--aa11bb22cc

Over a NON-INTERACTIVE ssh the PATH that makes `python` work is not set. Use:

    cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 tools/bp_gap_seconds.py
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
from collections import defaultdict, deque

REALDATA = "/data/media/0/realdata"
MS_TO_MPH = 2.23694
ACCDATA_3_ADDR = 394

MIN_SPEED_MS = 11.0        # ~25 mph; below this Ford ACC behaves differently and creeps
# How far below the set speed the car must be sitting before ACC counts as gap-limited rather than
# speed-limited. 0.9 m/s is ~2 mph -- wider than cluster rounding, narrower than a real slowdown.
SETSPEED_MARGIN_MS = 0.9
WINDOW_S = 3.0             # steadiness is judged over this much history
MIN_WINDOW_SAMPLES = 40    # radarState is 20 Hz, so a full window is ~60
MAX_MEAN_CLOSING_MS = 0.6  # mean, not instantaneous -- see the docstring
MAX_HEADWAY_SD_S = 0.20    # the headway must actually be holding still
MAX_HEADWAY_S = 4.0        # beyond this ACC is not gap-limited whatever else says
MIN_SAMPLES = 50           # a bucket below this is printed but flagged


def seg_index(name: str) -> int:
  tail = name.rsplit("--", 1)[-1]
  return int(tail) if tail.isdigit() else -1


def list_routes() -> dict[str, list[str]]:
  if not os.path.isdir(REALDATA):
    sys.exit(f"no {REALDATA} -- run this on the device")
  routes: dict[str, list[str]] = defaultdict(list)
  for d in os.listdir(REALDATA):
    if "--" in d and seg_index(d) >= 0:
      routes[d.rsplit("--", 1)[0]].append(d)
  if not routes:
    sys.exit("no route segments")
  return routes


def newest_routes(routes: dict[str, list[str]], count: int) -> list[str]:
  """Newest by segment MTIME, not by name and emphatically not by segment index.

  Sorting every segment directory by its index and taking the last one -- which is what this tool
  did on its first run -- picks the route with the MOST SEGMENTS, not the most recent. It reported
  route 348 as newest on a device whose newest was 370.
  """
  def when(route: str) -> float:
    return max(os.path.getmtime(os.path.join(REALDATA, d)) for d in routes[route])
  return sorted(routes, key=when)[-count:]


def rlog(seg: str) -> str | None:
  for name in ("rlog", "rlog.zst", "rlog.bz2"):
    p = os.path.join(REALDATA, seg, name)
    if os.path.exists(p):
      return p
  return None


def pct(values: list[float], q: float) -> float:
  if not values:
    return 0.0
  s = sorted(values)
  return s[min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))]


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--route", default=None, help="a single route id; default is the newest")
  ap.add_argument("--routes", type=int, default=1, help="pool this many of the newest routes")
  ap.add_argument("--max-segments", type=int, default=12, help="per route")
  args = ap.parse_args()

  try:
    from openpilot.tools.lib.logreader import LogReader
  except ImportError as e:
    sys.exit(f"no LogReader ({e}); see the docstring for the interpreter to use")

  routes = list_routes()
  wanted = [args.route] if args.route else newest_routes(routes, args.routes)
  for r in wanted:
    if r not in routes:
      sys.exit(f"no segments for {r}")
  print(f"# routes: {', '.join(wanted)}")

  samples: dict[int, list[float]] = defaultdict(list)
  by_speed: dict[tuple[int, int], list[float]] = defaultdict(list)
  seen_gaps: dict[int, int] = defaultdict(int)
  stage: dict[str, int] = defaultdict(int)

  for route in wanted:
    for seg in sorted(routes[route], key=seg_index)[:args.max_segments]:
      path = rlog(seg)
      if path is None:
        continue
      try:
        lr = LogReader(path)
      except Exception:  # noqa: BLE001 -- a truncated segment must not end the run
        continue

      # Per segment: monotime restarts, and following across a boundary is not one run anyway.
      window: deque = deque()
      gap = 0
      gap_from_msg = False
      v_ego = 0.0
      set_speed = 0.0
      engaged = False
      pedal = False

      for msg in lr:
        try:
          w = msg.which()
          if w == "carStateBP":
            if msg.carStateBP.accGap:
              gap, gap_from_msg = int(msg.carStateBP.accGap), True
            continue
          if w == "can":
            # Only consulted while carStateBP has never reported one. Two sources for one value is
            # how they end up quietly disagreeing.
            if not gap_from_msg:
              for c in msg.can:
                if c.address == ACCDATA_3_ADDR and len(c.dat) >= 5:
                  gap = c.dat[4] & 0x07
            continue
          if w == "carState":
            cs = msg.carState
            v_ego = float(cs.vEgo)
            engaged = bool(cs.cruiseState.enabled)
            pedal = bool(cs.gasPressed or cs.brakePressed)
            set_speed = float(cs.cruiseState.speedCluster)
            continue
          if w != "radarState":
            continue
          lead = msg.radarState.leadOne
          ts = msg.logMonoTime / 1e9
        except Exception:  # noqa: BLE001
          continue

        if gap:
          seen_gaps[gap] += 1

        if not (lead.status and engaged and not pedal and v_ego >= MIN_SPEED_MS and gap):
          window.clear()
          continue
        stage["lead, engaged, moving"] += 1

        # Gap-limited, or merely behind someone at the set speed? See the docstring.
        if set_speed > 0 and v_ego > set_speed - SETSPEED_MARGIN_MS:
          stage["...but at the set speed"] += 1
          window.clear()
          continue
        stage["gap-limited"] += 1

        headway = float(lead.dRel) / max(v_ego, 0.1)
        window.append((ts, headway, float(lead.vRel), v_ego))
        while window and ts - window[0][0] > WINDOW_S:
          window.popleft()
        # Note the 0.95: the pop above leaves the oldest sample just UNDER the window length, so
        # asking for a full WINDOW_S span here is a test that can never pass. The first version of
        # this tool did exactly that and reported zero steady samples from 46,000 radar frames.
        if ts - window[0][0] < WINDOW_S * 0.95 or len(window) < MIN_WINDOW_SAMPLES:
          continue

        headways = [a[1] for a in window]
        if abs(statistics.fmean(a[2] for a in window)) > MAX_MEAN_CLOSING_MS:
          stage["...but still closing"] += 1
          continue
        if statistics.pstdev(headways) > MAX_HEADWAY_SD_S:
          stage["...but headway unsteady"] += 1
          continue
        med = statistics.median(headways)
        if med > MAX_HEADWAY_S:
          continue
        stage["STEADY FOLLOWING"] += 1
        samples[gap].append(med)
        by_speed[(gap, int(v_ego * MS_TO_MPH) // 10 * 10)].append(med)

  print("\n=== how many frames survived each filter ===")
  for k, v in stage.items():
    print(f"  {k:28s} {v:7d}")

  print(f"\n=== gap settings seen: {dict(sorted(seen_gaps.items())) or 'none'} ===")
  if len(samples) < 2:
    print("  ONLY ONE SETTING APPEARS IN THESE ROUTES, so nothing here compares settings -- it")
    print("  measures the one he drives. Comparing them needs a drive that actually changes it.")

  print("\n=== steady-following headway by gap setting ===")
  print("  gap      n     p25   median    p75    median distance @ 65 mph")
  if not samples:
    print("  nothing qualified.")
  for g in sorted(samples):
    vals = samples[g]
    med = pct(vals, 0.5)
    flag = "" if len(vals) >= MIN_SAMPLES else "   (too few to trust)"
    print(f"   {g}    {len(vals):6d}  {pct(vals, 0.25):5.2f}s  {med:5.2f}s  {pct(vals, 0.75):5.2f}s"
          f"      {med * 65 / MS_TO_MPH:5.0f} m{flag}")

  if by_speed:
    print("\n=== does it hold across speeds? ===")
    print("  Ford may hold a constant TIME, a constant DISTANCE, or something between. A flat row")
    print("  is a time gap. A row that falls as speed rises is not, and 'seconds' is then the wrong")
    print("  unit to plan a pass in.")
    speeds = sorted({s for _, s in by_speed})
    print("  gap  " + "".join(f"{s:>5d}+" for s in speeds))
    for g in sorted(samples):
      row = "".join(f"{pct(by_speed[(g, s)], 0.5):6.2f}" if len(by_speed[(g, s)]) >= 30 else "     -"
                    for s in speeds)
      print(f"   {g}   {row}")
    print("  (a column needs 30+ samples to print; '-' means too little time spent there)")

  print(f"\n  Steady following = lead present, engaged, no pedal, above {MIN_SPEED_MS * MS_TO_MPH:.0f} mph, sitting at")
  print(f"  least {SETSPEED_MARGIN_MS * MS_TO_MPH:.0f} mph below the set speed, with mean closing rate under {MAX_MEAN_CLOSING_MS} m/s and")
  print(f"  headway varying by under {MAX_HEADWAY_SD_S}s across {WINDOW_S:.0f}s. Anything else says nothing about the setting.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
