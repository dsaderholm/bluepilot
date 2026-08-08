#!/usr/bin/env python3
"""BluePilot: why the car does not slow enough for a freeway exit.

Standing report across several drives: "It still does not slow down anywhere close to enough on
freeway exits."

TWO RUNS IN, AND BOTH OF MY FIRST TWO READINGS WERE WRONG. Worth writing down, because both came
from the same mistake and the third reading depends on not repeating it.

  Run 1 said the set speed never moved while SCC-Map asked for 39 mph, so ICBM was ignoring the
  plan. Run 2 added ICBM's own state and showed the opposite: vTarget stepped 80 -> 68 -> 56 -> 44
  -> 32, in AUTO, not suppressed, and the car began decelerating on the frame the first step
  landed. ~0.94 m/s^2, no brake, no gas, no lead. ICBM works.

  The "set speed" column was the lie. carState.vCruiseCluster is NOT the dash number -- card.py
  sets it from VCruiseHelper, and with ICBM (pcmCruiseSpeed False) that tracks DRIVER button
  presses only. It sat at 80 because he never touched a button, exactly as designed. The car's real
  set speed is cruiseState.speedCluster, which run 1 never printed. Both columns are here now, and
  labeled so they cannot be confused again: `dash` is the car, `opSet` is openpilot's.

So the plan is right and the actuator is right, and what is left is WHEN. SCC-Map read 570 (its
no-target sentinel) until t+416.7 and then jumped straight to 39 mph. mapd handed over a target
with only a few hundred metres of ramp left. That is a lookahead question, and it needs the two
things this run adds:

  - DISTANCE. Metres travelled, accumulated from vEgo, printed relative to the start of the window.
    Subtract two rows to get the runway between them. At 0.8 m/s^2 an 80 -> 40 mph exit needs 600 m
    and at 1.5 it needs 320, so the gap between "where the target appeared" and "where the ramp is"
    is the entire question.
  - WHAT HAPPENS AFTER. The old version stopped printing at the 45 mph trigger, which cut off the
    part that matters -- whether it ever reached the target, and how late. There is now a window on
    both sides.

USAGE, on the device:

    cd /data/openpilot && python tools/bp_dump_exit.py
    python tools/bp_dump_exit.py --route 00000042--aa11bb22cc
    python tools/bp_dump_exit.py --post 25          # more of the ramp itself

Paste the whole thing back.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import deque

REALDATA = "/data/media/0/realdata"
MS_TO_MPH = 2.23694

# An "exit" for our purposes: was doing freeway speed, ended up well below it.
FAST_MPH = 55.0
SLOW_MPH = 45.0
# SCC publishes 255 m/s as "no target", which prints as 570 mph. Anything near it is not a request.
NO_TARGET_MPH = 500.0
# longitudinalPlanSource is a capnp ENUM, and str() on it gives the enumerant name directly --
# "sccMap", "sccVision", "cruise", "speedLimitAssist". The first version called int() on it and died
# with "int() argument must be ... not '_DynamicEnum'" after parsing a whole route. Verified against
# the schema rather than assumed: str() -> 'sccMap', .raw -> 2.


def find_segments(route: str | None) -> list[str]:
  if not os.path.isdir(REALDATA):
    sys.exit(f"no {REALDATA} -- is this running on the device?")
  entries = sorted(d for d in os.listdir(REALDATA) if "--" in d)
  if not entries:
    sys.exit(f"no route segments under {REALDATA}")
  if route is None:
    route = entries[-1].rsplit("--", 1)[0]
    print(f"# newest route: {route}\n")
  segs = [os.path.join(REALDATA, d) for d in entries if d.startswith(route + "--")]
  if not segs:
    sys.exit(f"no segments for route {route}")
  return segs


def log_path(seg: str) -> str | None:
  for name in ("rlog", "rlog.zst", "rlog.bz2"):
    p = os.path.join(seg, name)
    if os.path.exists(p):
      return p
  return None


def render(rows: list[tuple[float, dict]], label: str) -> None:
  print(label)
  print("   time     mph  dash opSet  source      sccMap   sccVis  icbmTgt  icbm/override "
        " sup lead  G B      m")
  d0 = rows[0][1]["dist"] if rows else 0.0
  shown = 0
  for ts, s in rows:
    shown += 1
    if shown % 20:      # ~1 Hz is plenty across a 40 s approach
      continue
    m = f"{s['mapV']:5.0f}{'*' if s['mapAct'] else ' '}"
    v = f"{s['visV']:5.0f}{'*' if s['visAct'] else ' '}"
    print(f"  t+{ts:7.1f} {s['v']:6.1f} {s['dash']:5.0f}{s['opSet']:6.0f}  {str(s['src']):<10} "
          f"{m}   {v}  {s['icbmV']:6.0f}  {s['icbmState']}/{s['ovr']:<10} "
          f"{'Y' if s['sup'] else '.'} {s['lead']:4.0f}  {'G' if s['gas'] else '.'} "
          f"{'B' if s['brake'] else '.'} {s['dist'] - d0:6.0f}")


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--route", default=None)
  ap.add_argument("--window", type=float, default=40.0, help="seconds of approach to show")
  ap.add_argument("--post", type=float, default=15.0, help="seconds after the trigger to show")
  args = ap.parse_args()

  try:
    from openpilot.tools.lib.logreader import LogReader
  except ImportError as e:
    sys.exit(f"could not import LogReader ({e}); run this from /data/openpilot on the device")

  # Live peek at mapd -- but ONLY meaningful while driving, and this is usually run parked.
  #
  # The first version printed "EMPTY, mapd is producing nothing" whenever the param was unset, which
  # parked is exactly what you would expect whether mapd works or not. That is a conclusion the
  # evidence does not support, and stating it would have sent the next hour after the wrong cause.
  # The log-based count in the summary is the reliable answer; this is a bonus that only speaks up
  # when it has something positive to say.
  try:
    from openpilot.common.params import Params
    onroad = bool(Params().get_bool("IsOnroad"))
    n = len(Params("/dev/shm/params").get("MapTargetVelocities") or b"")
    if n:
      print(f"# mapd is producing data right now: MapTargetVelocities = {n} bytes")
    elif onroad:
      print("# MapTargetVelocities is EMPTY while onroad -- mapd has nothing for this road")
    else:
      print("# parked, so the live mapd check proves nothing either way; see the summary instead")
  except Exception as e:  # noqa: BLE001
    print(f"# live mapd check unavailable ({e}); the summary below does not depend on it")
  print()

  st = {"v": 0.0, "src": None, "mapV": 0.0, "mapAct": False, "visV": 0.0, "visAct": False,
        "dash": 0.0, "opSet": 0.0, "icbmV": 0.0, "icbmState": "?", "ovr": "?", "sup": False,
        "gas": False, "brake": False, "lead": 0.0, "dist": 0.0}
  hist: deque = deque(maxlen=12000)
  map_active_frames = 0
  map_source_frames = 0
  total = 0
  events = 0
  bad = 0
  t0 = None
  t_prev = None
  armed = False
  pending: tuple[float, str] | None = None
  # Where SCC-Map's target last went from "no target" to a real request, so the summary can say how
  # much road was left when mapd finally spoke. This is the number the whole question turns on.
  map_onset: tuple[float, float] | None = None
  onsets: list[tuple[float, float, float]] = []

  for seg in find_segments(args.route):
    path = log_path(seg)
    if path is None:
      continue
    for msg in LogReader(path):
      w = msg.which()
      t = msg.logMonoTime / 1e9
      if t0 is None:
        t0 = t

      # Wrapped because a route takes minutes to parse and one unexpected field should not throw
      # all of it away -- which is exactly what happened on the first run.
      try:
        if w == "carState":
          st["v"] = msg.carState.vEgo * MS_TO_MPH
          # BOTH set speeds, because conflating them cost two wrong conclusions. `dash` is what the
          # car shows and what ICBM's buttons actually move; `opSet` is openpilot's internal
          # v_cruise, which under ICBM only ever moves when the DRIVER presses something.
          st["dash"] = msg.carState.cruiseState.speedCluster * MS_TO_MPH
          st["opSet"] = msg.carState.vCruiseCluster * 0.621371
          # What the DRIVER and the traffic were doing. Added because he said "no cars ahead, not on
          # the gas, I braked at some point" and then, correctly, "my memory isn't perfect here".
          # It never should have rested on recollection: every one of these is in the log.
          st["gas"] = msg.carState.gasPressed
          st["brake"] = msg.carState.brakePressed
          if t_prev is not None:
            dt = t - t_prev
            if 0.0 < dt < 0.5:                      # ignore segment seams
              st["dist"] += msg.carState.vEgo * dt
          t_prev = t
        elif w == "radarState":
          lead = msg.radarState.leadOne
          st["lead"] = lead.dRel if lead.status else 0.0
        elif w == "longitudinalPlanSP":
          lp = msg.longitudinalPlanSP
          st["src"] = str(lp.longitudinalPlanSource)
          was_asking = st["mapAct"] and st["mapV"] < NO_TARGET_MPH
          st["mapV"] = lp.smartCruiseControl.map.vTarget * MS_TO_MPH
          st["mapAct"] = lp.smartCruiseControl.map.active
          st["visV"] = lp.smartCruiseControl.vision.vTarget * MS_TO_MPH
          st["visAct"] = lp.smartCruiseControl.vision.active
          if st["mapAct"] and st["mapV"] < NO_TARGET_MPH and not was_asking:
            map_onset = (t - t0, st["dist"])
          total += 1
          map_active_frames += 1 if st["mapAct"] else 0
          map_source_frames += 1 if st["src"] == "sccMap" else 0
        elif w == "carControlSP":
          icbm = msg.carControlSP.intelligentCruiseButtonManagement
          st["icbmV"] = icbm.vTarget
          st["icbmState"] = str(icbm.state)
          st["ovr"] = str(icbm.overrideState)
          st["sup"] = icbm.holdSuppressed
        else:
          continue
      except Exception as e:  # noqa: BLE001
        if not bad:
          print(f"# skipping unreadable {w} frames: {e}")
        bad += 1
        continue

      ts = t - t0
      hist.append((ts, dict(st)))

      # Print only once the post-window has elapsed. The old version flushed on the trigger, which
      # cut the printout off at 45 mph -- exactly where the interesting part starts, because the
      # question is whether it ever reached the target and how late.
      if pending is not None and ts >= pending[0] + args.post:
        lo, hi = pending[0] - args.window, pending[0] + args.post
        rows = [(x, s) for x, s in hist if lo <= x <= hi]
        onset = f", SCC-Map target appeared at t+{map_onset[0]:.1f}s" if map_onset else ""
        render(rows, f"===== deceleration #{events}: {FAST_MPH:.0f} -> {SLOW_MPH:.0f} mph at "
                     f"t+{pending[0]:.1f}s  ({pending[1]}){onset} =====")
        if map_onset is not None:
          onsets.append((events, map_onset[0], st["dist"] - map_onset[1]))
        print()
        pending = None

      if st["v"] >= FAST_MPH:
        armed = True
      elif armed and st["v"] <= SLOW_MPH and pending is None:
        armed = False
        events += 1
        pending = (ts, os.path.basename(seg))

  if pending is not None:
    lo = pending[0] - args.window
    rows = [(x, s) for x, s in hist if x >= lo]
    render(rows, f"===== deceleration #{events}: ends with the route  ({pending[1]}) =====")
    print()

  print("===== summary =====")
  print(f"  longitudinalPlanSP frames : {total}")
  print(f"  SCC-Map ACTIVE            : {map_active_frames}")
  print(f"  SCC-Map was the SOURCE    : {map_source_frames}")
  print(f"  large decelerations found : {events}")
  if bad:
    print(f"  unreadable frames skipped : {bad}")
  for n, when, travelled in onsets:
    print(f"  #{n}: target appeared at t+{when:.1f}s, {travelled:.0f} m of road used since")
  print()
  if map_active_frames == 0:
    print("  -> SCC-Map never became active anywhere in this route, so no amount of tuning will")
    print("     change the exits. Look at whether mapd is running and has data:")
    print("       ls -la /data/media/0/osm  ;  grep -i mapd /data/log/* | tail")
  elif map_source_frames == 0:
    print("  -> SCC-Map was active but never won the min(). Something else was always asking for")
    print("     less, so its target was never the limiting one. Check the source column above.")
  else:
    print("  -> SCC-Map fires and wins, and ICBM acts on it -- run 2 settled both. What is left is")
    print("     runway. Read the `m` column between where sccMap stops reading 570 and where the")
    print("     speed finally arrives. At SmartCruiseControlMapDecel = 0.8 m/s^2 an 80 -> 40 exit")
    print("     needs 600 m; at 1.5 it needs 320. If the runway is short, the setting cannot fix")
    print("     what mapd did not see coming.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
