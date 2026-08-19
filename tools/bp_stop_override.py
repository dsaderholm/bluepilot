#!/usr/bin/env python3
"""FusionPilot: did the stop override fire, and what happened when it did?

THE FIRST DRIVE IS AN EXPERIMENT AND THIS IS ITS READOUT. Five questions, and every one of them is
unanswered as of 2026-08-18:

  1. DID IT ARM AT ALL? It needs `dec.hasSlowDown`, openpilot's plan committed to stopping, below
     20 mph, and no radar lead within 60 m. Any one of those missing and it never fires -- and from
     the seat that is indistinguishable from the car simply stopping at 20 as it always did.
  2. DID IT COMPLETE, OR HIT THE TIME BOUND? 8 s is a guess sized against drive A's 40 s latch. A
     stop that keeps hitting the bound is a number to change, not a feature that failed.
  3. DID FORD HOLD THE STOP? `cruiseState.standstill` is `EngBrakeData.AccStopMde_D_Rq == 3`.
     **This has never once been true on this car** -- three drives checked, zero stopped-and-engaged
     frames -- because stock ACC cannot hold a stop without a lead. Everything downstream depends on
     it: the set-speed restore, the resume, whether he has to re-engage by hand.
  4. DID THE CAMERA REACT? The unknown the whole design is bounded around. Drive A latched cancel
     after ~40 s of sustained contradiction; drive B saw 1.3 s and nothing. A stop is 5-8 s, which
     is between them and untested.
  5. WAS THE SET SPEED PREPARED? It should climb back while stopped and held, so the getaway is not
     a crawl to 20.

HOW THE OVERRIDE IS DETECTED, since it is not published as a flag: our transmitted ACCDATA differs
from the camera's while openpilot longitudinal is active. That is the same test the HUD uses for the
violet OP STOP pill, and it is honest -- what we put on the wire disagreeing with what Ford asked
for IS openpilot having taken the command. It does not separate the override from a passthrough
FALLBACK, so the report splits them by speed and duration, which is what actually distinguishes
them: a fallback is milliseconds anywhere, an override is seconds below 20 mph.

USAGE, on the device:

    cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 tools/bp_stop_override.py
    python tools/bp_stop_override.py --route 00000388--abcdef0123
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

REALDATA = "/data/media/0/realdata"
MS_TO_MPH = 2.23694
ACCDATA = 0x186
CAM_BUS = 2

# An override lives below this and lasts seconds; a passthrough fallback is milliseconds anywhere.
OVERRIDE_SPEED_MPH = 30.0
OVERRIDE_MIN_S = 0.5


def seg_index(name: str) -> int:
  tail = name.rsplit("--", 1)[-1]
  return int(tail) if tail.isdigit() else -1


def newest_route() -> str:
  routes: dict[str, list[str]] = defaultdict(list)
  for d in os.listdir(REALDATA):
    if "--" in d and seg_index(d) >= 0:
      routes[d.rsplit("--", 1)[0]].append(d)
  if not routes:
    sys.exit("no route segments")

  def when(r: str) -> float:
    return max(os.path.getmtime(os.path.join(REALDATA, d)) for d in routes[r])
  return sorted(routes, key=when)[-1]


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--route", default=None)
  ap.add_argument("--max-segments", type=int, default=40)
  args = ap.parse_args()

  if not os.path.isdir(REALDATA):
    sys.exit(f"no {REALDATA} -- run this on the device")
  try:
    from openpilot.tools.lib.logreader import LogReader
  except ImportError as e:
    sys.exit(f"no LogReader ({e}); see the docstring for the interpreter to use")

  route = args.route or newest_route()
  segs = sorted([d for d in os.listdir(REALDATA) if d.startswith(route)], key=seg_index)
  print(f"# route: {route}  ({len(segs)} segments)")

  t0 = None
  runs = []          # [start, end, min_mph, ended_stopped]
  cur = None
  last_cam = None
  oe = False
  v_mph = 0.0
  standstill = False
  standstill_frames = 0
  cs_frames = 0
  slowdown_frames = 0
  cam_cancel_after = []
  prev_cancel = 0
  setspeed_while_stopped = []
  resume_events = 0
  # WHICH CONDITION BLOCKED IT. Its own output used to name two candidates -- "the plan never
  # committed or a lead was inside 60 m" -- and had no way to separate them, which is the same
  # "prints the same thing for two different causes" failure recorded twice in CLAUDE.md. These
  # mirror stop_override.py exactly: has_slow_down, op_stopping, v <= ENTER_SPEED, no lead in 60 m.
  fn_slow = fn_speed = fn_stopping = fn_nolead = fn_all = 0
  op_stopping = False
  lead_m = 0.0

  for seg in segs[:args.max_segments]:
    p = os.path.join(REALDATA, seg, "rlog")
    if not os.path.exists(p):
      p += ".zst"
    if not os.path.exists(p):
      continue
    try:
      lr = LogReader(p)
    except Exception:
      continue

    for m in lr:
      try:
        w = m.which()
      except Exception:
        continue
      t = m.logMonoTime / 1e9
      if t0 is None or t < t0:
        t0 = t

      if w == "carState":
        cs = m.carState
        v_mph = float(cs.vEgo) * MS_TO_MPH
        standstill = bool(cs.cruiseState.standstill)
        cs_frames += 1
        if standstill:
          standstill_frames += 1
          setspeed_while_stopped.append(round(float(cs.cruiseState.speedCluster) * MS_TO_MPH))
      elif w == "carControl":
        oe = bool(m.carControl.longActive)
        if m.carControl.cruiseControl.resume:
          resume_events += 1
      elif w == "longitudinalPlan":
        try:
          op_stopping = str(m.longitudinalPlan.longControlState) == "stopping"
        except Exception:
          op_stopping = False
      elif w == "radarState":
        try:
          lead = m.radarState.leadOne
          lead_m = float(lead.dRel) if bool(lead.status) else 0.0
        except Exception:
          lead_m = 0.0
      elif w == "longitudinalPlanSP":
        try:
          if m.longitudinalPlanSP.dec.hasSlowDown:
            slowdown_frames += 1
            fn_slow += 1
            slow_enough = v_mph <= 20.0
            no_lead = not (0.0 < lead_m < 60.0)
            if slow_enough:
              fn_speed += 1
            if op_stopping:
              fn_stopping += 1
            if no_lead:
              fn_nolead += 1
            if slow_enough and op_stopping and no_lead:
              fn_all += 1
        except Exception:
          pass
      elif w == "can":
        for c in m.can:
          if c.address == ACCDATA and c.src == CAM_BUS:
            d = bytes(c.dat)
            last_cam = d
            cancel = (d[4] >> 7) & 1
            if cancel and not prev_cancel and oe:
              cam_cancel_after.append(t - t0)
            prev_cancel = cancel
      elif w == "sendcan":
        for c in m.sendcan:
          if c.address != ACCDATA or last_cam is None:
            continue
          ours = bytes(c.dat)
          # Identical except AccPrpl_A_Pred, which the passthrough deliberately pins.
          same = all(ours[i] == last_cam[i] for i in (0, 1, 4, 5, 6, 7))
          if oe and not same:
            if cur is None:
              cur = [t, t, v_mph, False]
            cur[1] = t
            cur[2] = min(cur[2], v_mph)
            cur[3] = v_mph < 0.5
          elif cur is not None:
            runs.append(cur)
            cur = None
  if cur is not None:
    runs.append(cur)

  overrides = [r for r in runs if (r[1] - r[0]) >= OVERRIDE_MIN_S and r[2] < OVERRIDE_SPEED_MPH]
  fallbacks = [r for r in runs if r not in overrides]

  print("\n=== 0. WHICH CONDITION BLOCKED IT? (frames where the model asked for a stop) ===")
  if fn_slow == 0:
    print("  the model never asked for a stop at all -- nothing downstream could have fired")
  else:
    def _row(label: str, n: int) -> None:
      print("  {:<44} {:6d}   {:5.1f}%".format(label, n, 100.0 * n / fn_slow))
    _row("model asked for a stop (hasSlowDown)", fn_slow)
    _row("...and at or below 20 mph", fn_speed)
    _row("...and openpilot's plan was STOPPING", fn_stopping)
    _row("...and no radar lead inside 60 m", fn_nolead)
    _row("ALL FOUR -- the override could arm here", fn_all)
    if fn_all == 0:
      worst = min((fn_speed, "speed: never reached 20 mph while the model wanted a stop"),
                  (fn_stopping, "plan: longControlState never reached `stopping`"),
                  (fn_nolead, "lead: a car was always inside 60 m"))[1]
      print("  BLOCKER: " + worst)

  print("\n=== 1. DID IT ARM? ===")
  print(f"  model asked for a stop (hasSlowDown) on {slowdown_frames} frames")
  print(f"  openpilot authored for >= {OVERRIDE_MIN_S}s below {OVERRIDE_SPEED_MPH:.0f} mph: "
        f"{len(overrides)} times")
  print(f"  brief fallbacks (not overrides): {len(fallbacks)}")
  if not overrides:
    print("  NOTHING FIRED. If the car still stopped at 20 and sat there, the override never armed --")
    print("  check hasSlowDown above: zero means the model never asked and the trigger is the issue,")
    print("  nonzero means the plan never committed or a lead was inside 60 m.")

  if overrides:
    print("\n=== 2. DID IT COMPLETE? ===")
    for r in overrides:
      dur = r[1] - r[0]
      how = "reached a STOP" if r[3] else "handed back while still moving"
      flag = "  <- TIME BOUND?" if dur >= 7.5 else ""
      print(f"  t+{r[0] - t0:7.1f}  {dur:4.1f}s  from {r[2]:4.1f} mph min  {how}{flag}")
    completed = sum(1 for r in overrides if r[3])
    print(f"  {completed} of {len(overrides)} came to a full stop")

  print("\n=== 3. DID FORD HOLD THE STOP? ===")
  pct = 100.0 * standstill_frames / max(cs_frames, 1)
  print(f"  cruiseState.standstill true on {standstill_frames} of {cs_frames} frames ({pct:.2f}%)")
  if standstill_frames == 0:
    print("  NO. Ford never entered its hold, so the set-speed restore and the automatic resume")
    print("  never became reachable -- he re-engages by hand. That is the first thing to solve.")
  else:
    print("  YES -- and this is the first time it has ever been true on this car.")

  print("\n=== 4. DID THE CAMERA REACT? ===")
  print(f"  camera raised cancel while openpilot was active: {len(cam_cancel_after)} times")
  if cam_cancel_after:
    print(f"    first at t+{cam_cancel_after[0]:.1f}")
    print("    Compare against the override windows above. A cancel that STARTS during one and")
    print("    never clears is drive A's latch, and the time bound needs to come down.")
  else:
    print("  No reaction. If an override ran for seconds and the camera said nothing, that is the")
    print("  answer the whole feature was bounded around.")

  print("\n=== 5. WAS THE SET SPEED PREPARED? ===")
  if setspeed_while_stopped:
    lo, hi = min(setspeed_while_stopped), max(setspeed_while_stopped)
    print(f"  while stopped the set speed ran {lo} -> {hi} mph")
    if hi > lo:
      print("  YES -- it climbed while held, so the getaway is not a crawl to 20.")
    else:
      print("  NO -- it never moved. Either nothing was held long enough, or the restore did not run.")
  else:
    print("  never stopped with cruise engaged, so there was nothing to prepare")
  print(f"\n  openpilot requested resume {resume_events} times "
        f"(should be 0 for a stop it authored -- that one waits for him)")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
