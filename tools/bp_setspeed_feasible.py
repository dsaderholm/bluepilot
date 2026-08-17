#!/usr/bin/env python3
"""FusionPilot: which slowdowns could the SET SPEED never have delivered?

The division of labour is settled -- Ford decides HOW to slow, we decide WHETHER and BY HOW MUCH --
but the BOUNDARY is not: how much of a maneuver the ACCDATA override should take. His words,
2026-08-17: *"It is difficult to know when to use what."*

**It should not be a judgment call. It is arithmetic, and every input is already logged.**

    the set speed falls at ~3.3 mph/s and cannot go below 20 mph

So for any slowdown there is one test: could the button path have arrived in time? If yes, ICBM is
strictly better -- Ford picks coast vs engine-brake vs friction and we inherit tuning nobody here
has to write. If no, buttons were never going to do it and the override covers the shortfall.

**And the point of this tool is that the test can be run BACKWARDS, on drives already recorded.**
That answers the question the boundary really turns on: how OFTEN would the override fire? A rule
that fires on 2% of slowdowns is a narrow supplement. One that fires on 40% is openpilot doing the
longitudinal driving with extra steps, which is the thing he rejected.

WHAT IT DOES NOT DO
-------------------
It does not ask whether the slowdown was CORRECT. `bp_why_slow.py` attributes the source and
`bp_curve_runaway.py` questions the target. This assumes the request was right and asks only whether
the ACTUATOR could serve it.

It also cannot see the counterfactual. What is measured is the deceleration the car ACTUALLY
achieved and what the set speed was doing at the time -- so "infeasible" means the observed slowdown
was steeper than the set speed alone could have produced, which on a stock-ACC drive means Ford's
own braking for a lead was doing the work. Those are exactly the events where the question "who
should own this" is live.

USAGE, on the device:

    cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 tools/bp_setspeed_feasible.py
    python tools/bp_setspeed_feasible.py --routes 6
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict, deque

REALDATA = "/data/media/0/realdata"
MS_TO_MPH = 2.23694

# The two hard limits the whole question turns on. Both measured, both Ford's, neither ours.
SET_SPEED_RATE_MPH_S = 3.3     # CLAUDE.md: 71 -> 38 took ten seconds, held button
SET_SPEED_FLOOR_MPH = 20.0     # get_minimum_set_speed(); he confirmed the car refuses lower

MIN_DROP_MPH = 6.0             # what counts as a slowdown worth attributing
WINDOW_S = 12.0                # over this long
MIN_SPEED_MPH = 12.0           # below this it is parking-lot noise


def seg_index(name: str) -> int:
  tail = name.rsplit("--", 1)[-1]
  return int(tail) if tail.isdigit() else -1


def newest_routes(count: int) -> list[str]:
  if not os.path.isdir(REALDATA):
    sys.exit(f"no {REALDATA} -- run this on the device")
  routes: dict[str, list[str]] = defaultdict(list)
  for d in os.listdir(REALDATA):
    if "--" in d and seg_index(d) >= 0:
      routes[d.rsplit("--", 1)[0]].append(d)
  if not routes:
    sys.exit("no route segments")

  def when(r: str) -> float:
    return max(os.path.getmtime(os.path.join(REALDATA, d)) for d in routes[r])
  return sorted(routes, key=when)[-count:], routes


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--routes", type=int, default=4)
  ap.add_argument("--max-segments", type=int, default=12)
  args = ap.parse_args()

  try:
    from openpilot.tools.lib.logreader import LogReader
  except ImportError as e:
    sys.exit(f"no LogReader ({e}); see the docstring for the interpreter to use")

  picked, routes = newest_routes(args.routes)
  print(f"# routes: {', '.join(picked)}")

  feasible = 0
  infeasible_rate = 0
  infeasible_floor = 0
  worst = []
  by_source: Counter = Counter()

  for route in picked:
    for seg in sorted(routes[route], key=seg_index)[:args.max_segments]:
      path = os.path.join(REALDATA, seg, "rlog")
      if not os.path.exists(path):
        path += ".zst"
      if not os.path.exists(path):
        continue
      try:
        lr = LogReader(path)
      except Exception:  # noqa: BLE001
        continue

      hist: deque = deque(maxlen=1500)
      src = "?"
      target_mph = 0.0
      for msg in lr:
        try:
          w = msg.which()
          if w == "longitudinalPlanSP":
            lp = msg.longitudinalPlanSP
            src = str(lp.longitudinalPlanSource)
            # The TARGET is what decides the floor question, not the speed the car ends up at.
            target_mph = float(lp.vTarget) * MS_TO_MPH
            continue
          if w != "carState":
            continue
          cs = msg.carState
          if not cs.cruiseState.enabled:
            hist.clear()
            continue
          ts = msg.logMonoTime / 1e9
          hist.append((ts, cs.vEgo * MS_TO_MPH, cs.cruiseState.speedCluster * MS_TO_MPH, src,
                       target_mph))
        except Exception:  # noqa: BLE001
          continue

        now_t, now_v, now_dash, now_src, now_target = hist[-1]
        if now_v < MIN_SPEED_MPH or len(hist) < 100:
          continue
        old = None
        for t, v, dash, s, tgt in hist:
          if now_t - t <= WINDOW_S:
            old = (t, v, dash, s, tgt)
            break
        if old is None:
          continue
        drop = old[1] - now_v
        if drop < MIN_DROP_MPH:
          continue

        elapsed = max(now_t - old[0], 0.1)
        required = drop / elapsed                    # mph per second the car actually lost

        # Could the SET SPEED alone have produced this? Two ways it could not -- and the FLOOR test
        # is about the TARGET, not the car.
        #
        # The first version of this asked `now_v < 20`, i.e. did the car end up below the floor.
        # That is wrong and it inflated the answer to 53%: the car drops below 20 constantly --
        # following a slow lead, or the driver braking -- with the set speed sitting at 25 the whole
        # time and perfectly able to express what was being asked. What matters is whether the
        # PLAN ASKED for something the set speed cannot say.
        #
        # A target of 0 or an absurd one means nothing was asking; skip rather than count it.
        asked = 0.0 < now_target < 200.0
        if not asked:
          hist.clear()
          continue
        hit_floor = now_target < SET_SPEED_FLOOR_MPH
        too_fast = required > SET_SPEED_RATE_MPH_S

        if hit_floor:
          infeasible_floor += 1
        elif too_fast:
          infeasible_rate += 1
        else:
          feasible += 1
        by_source[now_src] += 1
        if too_fast or hit_floor:
          worst.append((required, old[1], now_v, now_target, now_src,
                        "floor" if hit_floor else "rate"))
        hist.clear()

  total = feasible + infeasible_rate + infeasible_floor
  print(f"\n=== slowdowns of >={MIN_DROP_MPH:.0f} mph within {WINDOW_S:.0f}s, engaged, above {MIN_SPEED_MPH:.0f} mph ===")
  if not total:
    print("  none found. Either the thresholds are wrong for these routes or nothing qualified.")
    return 0

  def pct(n: int) -> str:
    return f"{100.0 * n / total:5.1f}%"

  print(f"  total                                   {total:6d}")
  print(f"  the SET SPEED could have delivered      {feasible:6d}  {pct(feasible)}   <- ICBM keeps these")
  print(f"  needed more than {SET_SPEED_RATE_MPH_S} mph/s            {infeasible_rate:6d}  {pct(infeasible_rate)}   <- override territory")
  print(f"  TARGET below the {SET_SPEED_FLOOR_MPH:.0f} mph floor         {infeasible_floor:6d}  {pct(infeasible_floor)}   <- override territory")

  share = 100.0 * (infeasible_rate + infeasible_floor) / total
  print(f"\n  SO THE OVERRIDE WOULD FIRE ON {share:.1f}% OF SLOWDOWNS.")
  print("  Low single digits means a narrow supplement to ICBM, which is the design.")
  print("  Anything large means the override is doing the longitudinal driving, which is op long")
  print("  wearing a different name -- and that is the thing he rejected, so it would be the signal")
  print("  to narrow the rule rather than to ship it.")

  if worst:
    worst.sort(reverse=True)
    print("\n  steepest events the buttons could not have served:")
    print("    mph/s   from    to  target  plan source        why")
    for req, v0, v1, tgt, s, why in worst[:12]:
      print(f"    {req:5.1f}  {v0:5.0f}  {v1:4.0f}  {tgt:5.0f}   {s:<17}  {why}")

  print(f"\n  who asked, across all qualifying slowdowns: {dict(by_source.most_common(6))}")
  print("\n  NOTE: this measures the deceleration ACHIEVED, not the one requested. On a stock-ACC")
  print("  drive an 'infeasible' event is usually Ford braking for a lead -- which is precisely the")
  print("  case the override should NOT take, since Ford saw it and handled it. Read the source")
  print("  column: sccMap and sccVision rows are the ones the override would actually be for.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
