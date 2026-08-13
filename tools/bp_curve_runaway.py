#!/usr/bin/env python3
"""FusionPilot: find curve slowdowns where SCC-Vision walked the car down instead of settling.

Reported 2026-08-12, with a map: on one drive at the I-80/I-215 Parley's interchange the car slowed
far too much in two places and not enough in a third. All three are freeway curves and SCC-Vision
owned all three.

WHY THIS SHAPE OF BUG EXISTS. Vision's target is proportional to the CURRENT speed:

    v_target = v_ego * sqrt(a_lat_reg_max / max_pred_lat_acc)          vision_controller.py

Rewrite it in terms of the curvature the model implies, kappa = max_pred_lat_acc / v_ego^2, and the
v_ego cancels:

    v_target = sqrt(a_lat_reg_max / kappa)

So while the model's idea of the ROAD holds still, the target is a fixed corner speed and the
controller settles on it -- correct, and the usual case. The runaway happens only when that implied
curvature RISES as the car slows: each frame then re-derives a lower target from the lower speed, and
nothing in `_update_state_machine` bounds it. SCC-Map got three defenses built from measured events.
Vision has none.

So the signature this looks for is not "slowed a lot" -- it is a descent during which the implied
RADIUS SHRANK. A genuine approach to a corner resolves to a roughly stable radius; a runaway keeps
finding a tighter corner the slower it goes. On route 00000365 the implied radius went 382 m -> 147 m
while the car came down from 61 to 44 mph, and 147 m is not a radius that exists on I-215.

IT ALSO SETTLES A MEASUREMENT DISPUTE, which is why the steering columns are here. Two tools
disagreed 30x about the lateral acceleration at one instant: `currentLateralAccel` (which is
`v_ego^2 * controlsState.curvature`) read 4.53 where a steering-angle derivation read 0.13. 4.53 m/s2
at 34 mph is a 51 m radius, and the owner's map says that road is I-215. Every flagged event prints
the RAW steering angle beside both derivations, because the angle cannot be argued with: a freeway
curve at 34 mph needs a few degrees, a 51 m radius needs about 55.

The steering derivation here is the simple bicycle model and ignores tire slip, so it reads slightly
LOW at highway speed. That is worth a few percent, not 30x -- do not explain away an order of
magnitude with it.

READ-ONLY. Nothing here writes, sets a param, or restarts anything.

USAGE, on the device:

    cd /data/openpilot && python tools/bp_curve_runaway.py --route 00000365--0be21ea565
"""
from __future__ import annotations

import argparse
import math
import os
import sys

REALDATA = "/data/media/0/realdata"
MS_TO_MPH = 2.23694
NO_TARGET_MPH = 500.0

# Fallbacks only. Both are read from carParams when the route carries it.
DEFAULT_STEER_RATIO = 17.07
DEFAULT_WHEELBASE = 2.85


def seg_index(name: str) -> int:
  tail = name.rsplit("--", 1)[-1]
  return int(tail) if tail.isdigit() else -1


def implied_radius(v_ego: float, max_pred: float) -> float:
  """Radius of the corner the MODEL is claiming, in metres. inf when it claims none."""
  if max_pred <= 1e-3 or v_ego <= 0.1:
    return float("inf")
  return v_ego * v_ego / max_pred


def fmt_radius(r: float) -> str:
  if r == float("inf") or r > 9999:
    return "   --"
  return f"{r:5.0f}"


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--route", default=None)
  ap.add_argument("--drop", type=float, default=10.0,
                  help="mph of speed loss for a descent to be worth reporting")
  ap.add_argument("--shrink", type=float, default=0.6,
                  help="flag when the implied radius ends below this fraction of where it started")
  ap.add_argument("--max-segments", type=int, default=40)
  args = ap.parse_args()

  try:
    from openpilot.tools.lib.logreader import LogReader
  except ImportError as e:
    sys.exit(f"no LogReader ({e}); run from /data/openpilot")

  entries = sorted((d for d in os.listdir(REALDATA) if "--" in d), key=seg_index)
  if not entries:
    sys.exit(f"no routes under {REALDATA}")
  route = args.route or entries[-1].rsplit("--", 1)[0]
  segs = [d for d in entries if d.startswith(route + "--")][:args.max_segments]
  if not segs:
    sys.exit(f"no segments for {route}")

  steer_ratio, wheelbase = DEFAULT_STEER_RATIO, DEFAULT_WHEELBASE
  geometry_from_log = False

  print(f"# route {route}, {len(segs)} segments")
  print(f"# reporting descents >= {args.drop:.0f} mph; FLAGGING those whose implied radius fell "
        f"below {args.shrink:.0%} of its starting value\n")

  st = {"v": 0.0, "dash": 0.0, "angle": 0.0, "cs_curv": 0.0, "state": "?", "src": "?",
        "vis_t": 0.0, "vis_lat": 0.0, "vis_pred": 0.0, "vis_active": False, "map_t": 0.0}
  t0 = t_prev = None
  t_shift = 0.0
  run: list = []          # frames of the current descent
  peak_v = None
  found = flagged = 0

  def close_run() -> None:
    nonlocal run, found, flagged
    if len(run) >= 2:
      v_start = run[0][1]["v"]
      v_end = min(f[1]["v"] for f in run)
      if (v_start - v_end) * MS_TO_MPH >= args.drop and any(f[1]["vis_active"] for f in run):
        r_first = r_last = None
        for _, s in run:
          r = implied_radius(s["v"], s["vis_pred"])
          if r != float("inf"):
            if r_first is None:
              r_first = r
            r_last = r
        shrank = (r_first is not None and r_last is not None
                  and r_first > 0 and r_last / r_first < args.shrink)
        found += 1
        if shrank:
          flagged += 1
        tag = "RUNAWAY" if shrank else "descent"
        head = (f"===== {tag} #{found}: {v_start * MS_TO_MPH:.0f} -> {v_end * MS_TO_MPH:.0f} mph "
                f"starting t+{run[0][0]:.0f}s =====")
        print(head)
        if r_first is not None and r_last is not None:
          print(f"  model's implied corner radius: {r_first:.0f} m -> {r_last:.0f} m"
                + ("   <-- the model found a TIGHTER corner the slower it went" if shrank else ""))
        print("   time    mph   dash  visState    visTgt  maxPred  impliedR   "
              "latAcc(field)  steerDeg  latAcc(steer)")
        for ts, s in run:
          vt = "   -- " if s["vis_t"] <= 0 or s["vis_t"] > NO_TARGET_MPH else f"{s['vis_t']:6.0f}"
          curv_angle = math.tan(math.radians(s["angle"] / steer_ratio)) / wheelbase
          lat_steer = abs(s["v"] * s["v"] * curv_angle)
          print(f"  t+{ts:6.0f} {s['v'] * MS_TO_MPH:6.0f} {s['dash']:6.0f}  {s['state']:<11}"
                f" {vt} {s['vis_pred']:8.2f}  {fmt_radius(implied_radius(s['v'], s['vis_pred']))}"
                f"   {s['vis_lat']:12.2f}  {s['angle']:8.1f}  {lat_steer:12.2f}")
        print()
    run = []

  for seg in segs:
    path = None
    for name in ("rlog", "rlog.zst", "rlog.bz2"):
      p = os.path.join(REALDATA, seg, name)
      if os.path.exists(p):
        path = p
        break
    if path is None:
      continue
    for msg in LogReader(path):
      w = msg.which()
      t_raw = msg.logMonoTime / 1e9
      if t_prev is not None and t_raw < t_prev - 1.0:
        t_shift += t_prev - t_raw          # segments restart the clock; keep one timeline
      t_prev = t_raw
      t = t_raw + t_shift
      if t0 is None:
        t0 = t
      ts = t - t0
      try:
        if w == "carParams" and not geometry_from_log:
          cp = msg.carParams
          if cp.steerRatio > 0 and cp.wheelbase > 0:
            steer_ratio, wheelbase = float(cp.steerRatio), float(cp.wheelbase)
            geometry_from_log = True
            print(f"# vehicle geometry from carParams: steerRatio {steer_ratio:.2f}, "
                  f"wheelbase {wheelbase:.2f} m\n")
          continue
        if w == "carState":
          cs = msg.carState
          st["v"] = cs.vEgo
          st["dash"] = cs.cruiseState.speedCluster * MS_TO_MPH
          st["angle"] = float(cs.steeringAngleDeg)
        elif w == "controlsState":
          st["cs_curv"] = float(msg.controlsState.curvature)
        elif w == "longitudinalPlanSP":
          lp = msg.longitudinalPlanSP
          st["src"] = str(lp.longitudinalPlanSource)
          vis = lp.smartCruiseControl.vision
          st["state"] = str(vis.state)
          st["vis_t"] = float(vis.vTarget) * MS_TO_MPH
          st["vis_lat"] = float(vis.currentLateralAccel)
          st["vis_pred"] = float(vis.maxPredictedLateralAccel)
          st["vis_active"] = bool(vis.active)
          st["map_t"] = float(lp.smartCruiseControl.map.vTarget) * MS_TO_MPH
        else:
          continue
      except Exception:  # noqa: BLE001 -- a short or partial segment must not kill the run
        continue

      if w != "longitudinalPlanSP":
        continue                       # sample the descent on the planner's cadence, once per frame

      if peak_v is None:
        peak_v = st["v"]
        continue
      if st["v"] > peak_v - 0.3:       # still at or above the high-water mark: no descent underway
        close_run()
        peak_v = st["v"]
        continue
      peak_v = max(peak_v, st["v"])
      if not run or ts - run[-1][0] >= 1.0:
        run.append((ts, dict(st)))

  close_run()

  print(f"=== {found} descents >= {args.drop:.0f} mph with vision active, {flagged} flagged as "
        f"runaways ===")
  print("  impliedR is the corner the MODEL claims: v_ego^2 / maxPredictedLateralAccel. Compare it")
  print("  against the road on a map -- a freeway does not have a 150 m radius, and a ramp does.")
  print("  A descent whose radius holds steady is vision doing its job. One whose radius collapses")
  print("  is vision chasing its own output down, and nothing in the state machine stops that.")
  print()
  print("  latAcc(field) vs latAcc(steer): these are supposed to agree. Where they do not, believe")
  print("  steerDeg -- 55 degrees is a 51 m radius, 3 degrees is a freeway sweeper.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
