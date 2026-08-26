#!/usr/bin/env python3
"""FusionPilot: find where the SET SPEED ran away, and print who was asking.

His report, 2026-08-26: *"at one point at the beginning my speed went to 80 while it was at a stop
and then went back down to 35."*

`bp_hold_history.py` cannot answer this and that is worth stating rather than discovering: it prints
a row when the HOLD changes, so a set speed that climbs on its own -- SLA raising it, ICBM chasing a
target, a limit arriving -- moves the dash without ever touching the hold and leaves no line at all.
On route 000003c4 it showed the dash at 78 with no preceding row explaining how it got there.

WHAT THIS LOOKS FOR: a window where the dash set speed is HIGH while the car is SLOW or stopped.
That combination is the report, and it is also the dangerous shape in general -- a high set speed on
a stopped car is what pulls away hard the moment the brake is released, and it is how the 2026-08-24
exit incident began (set speed still unwinding through 57 into a ramp).

FOR EACH EPISODE it prints a one-row-per-second trace of every number that could be responsible:

    dash      the car's own set speed, `cruiseState.speedCluster` -- the one ICBM's buttons move
    MAX       openpilot's `vCruiseCluster` -- his number, and NOT the same thing as the dash
    vTgt/raw  what ICBM is aiming at, and the planner's target BEFORE the hold is applied
    hold      the baseline, with the SOURCE that captured it
    SLA       the speed-limit target, and the plan source that won

Both speed columns are printed because this fork has produced three wrong conclusions from reading
one as the other. See "There are two set speeds and they legitimately disagree" in CLAUDE.md.

    python tools/bp_dash_excursion.py --route 000003c4--3ea7141fb2
"""
from __future__ import annotations

import argparse
import os
import sys

from openpilot.tools.bp_logtime import DriveClock

REALDATA = "/data/media/0/realdata"
MS_TO_MPH = 2.23694
KPH_TO_MPH = 0.621371
NO_TARGET_MPH = 500.0

SLOW_MPH = 8.0          # "at a stop" with room for creep
HIGH_DASH_MPH = 55.0    # a set speed this far above a stopped car is the report
CONTEXT_S = 20.0

ONROAD_PARAM = "/data/params/d/IsOnroad"


def is_onroad() -> bool:
  try:
    with open(ONROAD_PARAM, "rb") as f:
      return f.read(1) == b"1"
  except OSError:
    return False


def seg_index(name: str) -> int:
  tail = name.rsplit("--", 1)[-1]
  return int(tail) if tail.isdigit() else -1


def _open(seg: str):
  from openpilot.tools.lib.logreader import LogReader
  for name in ("rlog", "rlog.zst", "rlog.bz2"):
    p = os.path.join(REALDATA, seg, name)
    if os.path.exists(p):
      return LogReader(p)
  return None


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--route", action="append", default=None)
  ap.add_argument("--max-segments", type=int, default=60)
  ap.add_argument("--high", type=float, default=HIGH_DASH_MPH)
  args = ap.parse_args()

  entries = sorted((d for d in os.listdir(REALDATA) if "--" in d), key=seg_index)
  routes = args.route or [entries[-1].rsplit("--", 1)[0]]
  segs = []
  for r in routes:
    segs += [d for d in entries if d.startswith(r + "--")][:args.max_segments]
  if not segs:
    sys.exit(f"no segments for {routes}")
  print(f"# {', '.join(routes)} -- {len(segs)} segments, "
        f"looking for dash >= {args.high:.0f} mph while under {SLOW_MPH:.0f} mph\n")

  clock = DriveClock()
  st = {"v": 0.0, "dash": 0.0, "max": 0.0, "enab": False, "tgt": 0.0, "raw": 0.0,
        "hold": 0.0, "bsrc": "?", "sla": 0.0, "src": "?", "btn": "none"}
  trail: list = []
  eps: list = []
  cur = None
  last_high = -1e9

  for seg in segs:
    if is_onroad():
      print(f"\n!!! THE CAR STARTED DRIVING at {seg}. STOPPING -- PARTIAL.\n")
      break
    lr = _open(seg)
    if lr is None:
      continue
    for msg in lr:
      w = msg.which()
      ts = clock.seconds(msg.logMonoTime)
      try:
        if w == "carState":
          cs = msg.carState
          st["v"] = cs.vEgo * MS_TO_MPH
          st["dash"] = cs.cruiseState.speedCluster * MS_TO_MPH
          st["max"] = cs.vCruiseCluster * KPH_TO_MPH
          st["enab"] = bool(cs.cruiseState.enabled)
        elif w == "longitudinalPlanSP":
          lp = msg.longitudinalPlanSP
          st["src"] = str(lp.longitudinalPlanSource)
          try:
            st["sla"] = lp.speedLimit.resolver.speedLimit * MS_TO_MPH
          except Exception:  # noqa: BLE001
            pass
        elif w == "selfdriveStateSP":
          icbm = msg.selfdriveStateSP.intelligentCruiseButtonManagement
          st["hold"] = float(icbm.vBaseline)
          st["tgt"] = float(icbm.vTarget)
          st["bsrc"] = str(icbm.baselineSource)
          st["btn"] = str(icbm.sendButton)
          try:
            st["raw"] = float(icbm.vTargetRaw)
          except Exception:  # noqa: BLE001
            pass
        else:
          continue
      except Exception:  # noqa: BLE001
        continue

      trail.append((ts, dict(st)))
      while trail and ts - trail[0][0] > CONTEXT_S:
        trail.pop(0)

      hit = st["dash"] >= args.high and st["v"] < SLOW_MPH
      if hit:
        if cur is None:
          cur = {"t0": ts, "rows": list(trail), "peak": st["dash"], "vmin": st["v"]}
          eps.append(cur)
        cur["rows"].append((ts, dict(st)))
        cur["peak"] = max(cur["peak"], st["dash"])
        cur["vmin"] = min(cur["vmin"], st["v"])
        last_high = ts
      elif cur is not None:
        cur["rows"].append((ts, dict(st)))
        if ts - last_high > CONTEXT_S:
          cur = None

  print(f"=== {len(eps)} EPISODE(S) of a high set speed on a slow car ===")
  for i, ep in enumerate(eps, 1):
    print(f"\n----- episode {i}: dash peaked at {ep['peak']:.0f} mph "
          f"with the car down to {ep['vmin']:.0f} mph, first at t+{ep['t0']:.0f}s -----")
    print(f"  {'time':>8} {'mph':>5} {'dash':>5} {'MAX':>5} {'vTgt':>5} {'raw':>5} {'hold':>5}"
          f"  {'bsrc':<12} {'SLA':>5}  {'source':<18} btn")
    seen = set()
    for ts, s in ep["rows"]:
      k = int(ts)
      if k in seen:
        continue
      seen.add(k)
      if len(seen) > 70:
        print("      ... truncated")
        break
      sla = "  -- " if s["sla"] <= 0 or s["sla"] > NO_TARGET_MPH else f"{s['sla']:5.0f}"
      print(f"  t+{ts:6.0f} {s['v']:5.0f} {s['dash']:5.0f} {s['max']:5.0f} {s['tgt']:5.0f}"
            f" {s['raw']:5.0f} {s['hold']:5.0f}  {s['bsrc']:<12} {sla}  {s['src']:<18} {s['btn']}")
  if not eps:
    print("  none -- the set speed never sat high on a slow car on this route")
  else:
    print()
    print("  dash vs MAX: `speedCluster` is the CAR's number, `vCruiseCluster` is openpilot's.")
    print("  They legitimately disagree under ICBM and reading one as the other has produced three")
    print("  wrong conclusions in this fork. Both are printed on purpose.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
