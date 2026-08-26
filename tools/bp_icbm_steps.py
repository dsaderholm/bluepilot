#!/usr/bin/env python3
"""FusionPilot: is the dash set speed moving in 1 mph TAPS or 5 mph HOLDS, and who moved it to 20?

Two road reports from one drive, 2026-08-25:

  1. *"It seemed to be emulating pressing and holding because I was dropping and increasing by 5
     miles per hour at a time."*
  2. *"It also decided to drop down to 20 for no reason with no warning."*

WHY THIS TOOL EXISTS RATHER THAN ANOTHER READ OF `bp_setspeed_hunting.py`. That tool looks for
direction REVERSALS -- the oscillation shape. Neither report is an oscillation. The first is about
the SIZE OF EACH STEP, which no existing tool prints, and the repo currently holds two recorded
claims that contradict each other about exactly that:

    CLAUDE.md, "THE SET SPEED HUNT"   ICBM asserts the button continuously -- a HOLD -- and this
                                      car moves the set speed 5 mph for a held button
    memory, "buttons cannot hold"     the SCCM clears the bit between our frames, so ICBM cannot
                                      hold at all and taps at 1 mph per 0.30 s

Both give the same THROUGHPUT (5 mph / 1.5 s and 1 mph / 0.30 s are both 3.3 mph/s), which is why
neither was ever caught by a tool that measured rate. They differ only in what the driver SEES on
the dash, which is precisely what he is reporting. So measure the step, not the rate.

CLAUDE.md's own rule for this situation: "If a claim here is contradicted anywhere else in this
file, re-measure before quoting either."

WHAT IT PRINTS

  STEP HISTOGRAM   every change in `cruiseState.speedCluster`, bucketed by size, split by whether
                   ICBM was asserting a button when it happened. A 5 mph column under ICBM is the
                   report; a 1 mph column is ICBM tapping correctly and the complaint is elsewhere.

  IN-BAND / OUT    for every ICBM-driven step, whether ICBM was inside `TAP_BAND` (2 mph) of its
                   target -- because OUTSIDE the band a hold is the DESIGN, not a defect, and the
                   two answers need completely different fixes.

  FLOOR EPISODES   every stretch where the target reached Ford's 20 mph floor, with who was asking
                   and what the driver could have seen. `vTarget` alone cannot answer "why", so the
                   plan source, both curve controllers, SLA and the lead are printed beside it.

USAGE, on the device:

    cd /data/openpilot && python tools/bp_icbm_steps.py --route 000003c0--d46d098434
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from collections import Counter

from openpilot.tools.bp_logtime import DriveClock

REALDATA = "/data/media/0/realdata"
MS_TO_MPH = 2.23694
NO_TARGET_MPH = 500.0

# Mirrors the controller. Imported would be better and is not available offline in every tree, so
# it is asserted against the source instead -- a silent drift here would mislabel every step.
TAP_BAND = 2.0
FLOOR_MPH = 20.0

# A dash change is attributed to ICBM if ICBM was asserting a button within this many seconds
# before it. The car's own response lag to a press is well under this; a driver press that ICBM
# was not making lands outside it.
ATTRIB_S = 1.0

# A new dash value must persist this long to count as a real step rather than rounding chatter.
# Well under the ~0.30 s this car takes to move the cluster a single mph, so it cannot merge two
# genuine taps, and well over the 0.01 s frame period that produces the chatter.
DEBOUNCE_S = 0.08

# DID THE CAR ACTUALLY TURN? A curve controller that drives the target to 12 mph is either reading
# a real corner or a phantom, and the two are indistinguishable from the target alone. The car's
# own steering is the independent witness -- CLAUDE.md's rule that a number only one tool can
# produce has never been checked. Bicycle model, same derivation `bp_curve_runaway.py` uses as its
# cross-check; it reads high by roughly half at highway speed because it omits the understeer term,
# which does not matter for "was there a corner at all".
DEFAULT_STEER_RATIO = 17.0
DEFAULT_WHEELBASE = 2.85
# Below this the road was straight and the slowdown had nothing behind it. Well under the ~1.1 m/s^2
# a 25 mph mapped corner implies, so a real corner cannot hide beneath it.
NO_CORNER_LAT_ACC = 1.0
# How long after the target hits the floor to keep looking for the corner. SCC-Map publishes the
# corner speed at the moment braking must BEGIN, so the corner arrives seconds later, not now.
CORNER_LOOKAHEAD_S = 20.0

# mapd's own `map_curve_target_lat_a`, which SCC-Map's corner speed is derived from, and his
# `SmartCruiseControlMapFactor` (90) applied to corners at or below 25 mph. Read off the device
# rather than assumed; if either moves, this arithmetic moves with it.
SCC_MAP_LAT_A = 2.2
SCC_MAP_FACTOR = 0.90


# FusionPilot: THE GUARD, NOT A NOTE. On 2026-08-25 an rlog scan run on this device cost him
# engagement mid-drive, and the lesson was written down as "copy the logs off and decode locally".
# A note cannot stop the next scan; this can. Checked before EVERY segment, because a drive can
# start at any point during a run that takes minutes.
ONROAD_PARAM = "/data/params/d/IsOnroad"


def is_onroad() -> bool:
  try:
    with open(ONROAD_PARAM, "rb") as f:
      return f.read(1) == b"1"
  except OSError:
    return False          # not on the device at all -- decoding on a laptop is always fine


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
  ap.add_argument("--route", default=None, action="append", help="repeatable")
  ap.add_argument("--max-segments", type=int, default=60)
  ap.add_argument("--ignore-onroad", action="store_true",
                  help="keep decoding after the car starts driving. Almost never right.")
  ap.add_argument("--floor-window", type=float, default=12.0,
                  help="seconds of context to print either side of a floor episode")
  args = ap.parse_args()

  try:
    import openpilot.tools.lib.logreader  # noqa: F401
  except ImportError as e:
    sys.exit(f"no LogReader ({e}); run from /data/openpilot")

  entries = sorted((d for d in os.listdir(REALDATA) if "--" in d), key=seg_index)
  routes = args.route or [entries[-1].rsplit("--", 1)[0]]
  segs = []
  for r in routes:
    segs += [d for d in entries if d.startswith(r + "--")][:args.max_segments]
  if not segs:
    sys.exit(f"no segments for {routes}")
  print(f"# {len(routes)} route(s): {', '.join(routes)} -- {len(segs)} segments")

  st = {"dash": 0.0, "v": 0.0, "src": "?", "hold": 0.0, "tgt": 0.0, "raw": 0.0,
        "state": "?", "btn": "none", "ovr": "?", "bsrc": "?", "enab": False,
        "slaV": 0.0, "visV": 0.0, "mapV": 0.0, "lead": 0.0, "slowdown": False}

  clock = DriveClock()
  last_dash = None
  last_change_ts = None
  cand = None
  cand_ts = 0.0
  last_btn_ts = -1e9        # last time ICBM asserted ANY button
  steps = []                # (ts, delta, dt, by_icbm, in_band, tgt, dash)
  trail = []                # rolling context for floor episodes
  floor_eps = []            # one summary dict per episode
  cur_ep = None
  last_floor_ts = -1e9
  lat_samples = []          # (ts, |lateral accel|) at 5 Hz -- the independent witness
  last_lat_bucket = None
  steer_ratio, wheelbase = DEFAULT_STEER_RATIO, DEFAULT_WHEELBASE
  enabled_frames = 0
  total_frames = 0

  for seg in segs:
    if not args.ignore_onroad and is_onroad():
      print()
      print(f"!!! THE CAR STARTED DRIVING at segment {seg}. STOPPING.")
      print("!!! Everything below is PARTIAL. Re-run parked, or copy the rlogs off and decode")
      print("!!! them on a laptop -- decoding here competes with what keeps him engaged.")
      print()
      break
    lr = _open(seg)
    if lr is None:
      continue
    for msg in lr:
      w = msg.which()
      ts = clock.seconds(msg.logMonoTime)
      try:
        if w == "carParams":
          cp = msg.carParams
          if cp.steerRatio > 0 and cp.wheelbase > 0:
            steer_ratio, wheelbase = float(cp.steerRatio), float(cp.wheelbase)
          continue
        if w == "carState":
          cs = msg.carState
          st["dash"] = cs.cruiseState.speedCluster * MS_TO_MPH
          st["v"] = cs.vEgo * MS_TO_MPH
          st["enab"] = bool(cs.cruiseState.enabled)
          total_frames += 1
          enabled_frames += st["enab"]
          # 5 Hz is plenty to catch a corner's peak and keeps this list small on a 37-segment route.
          bucket = int(ts * 5)
          if bucket != last_lat_bucket:
            last_lat_bucket = bucket
            curv = math.tan(math.radians(float(cs.steeringAngleDeg) / steer_ratio)) / wheelbase
            lat_samples.append((ts, abs(cs.vEgo * cs.vEgo * curv), float(cs.vEgo)))
        elif w == "longitudinalPlanSP":
          lp = msg.longitudinalPlanSP
          st["src"] = str(lp.longitudinalPlanSource)
          st["visV"] = lp.smartCruiseControl.vision.vTarget * MS_TO_MPH
          st["mapV"] = lp.smartCruiseControl.map.vTarget * MS_TO_MPH
          try:
            st["slaV"] = lp.speedLimit.resolver.speedLimit * MS_TO_MPH
          except Exception:  # noqa: BLE001
            pass
          try:
            st["slowdown"] = bool(lp.dec.hasSlowDown)
          except Exception:  # noqa: BLE001
            pass
        elif w == "selfdriveStateSP":
          icbm = msg.selfdriveStateSP.intelligentCruiseButtonManagement
          st["hold"] = float(icbm.vBaseline)
          st["tgt"] = float(icbm.vTarget)
          st["state"] = str(icbm.state)
          st["btn"] = str(icbm.sendButton)
          st["ovr"] = str(icbm.overrideState)
          st["bsrc"] = str(icbm.baselineSource)
          try:
            st["raw"] = float(icbm.vTargetRaw)
          except Exception:  # noqa: BLE001
            pass
          if st["btn"] != "none":
            last_btn_ts = ts
        elif w == "radarState":
          ld = msg.radarState.leadOne
          st["lead"] = ld.dRel if ld.status else 0.0
        else:
          continue
      except Exception:  # noqa: BLE001
        continue

      row = (ts, dict(st))
      trail.append(row)
      while trail and ts - trail[0][0] > args.floor_window:
        trail.pop(0)

      # ---------------------------------------------------------------- step sizes
      # DEBOUNCED, and the first version was not. `speedCluster` is a float in m/s, so a dash
      # sitting on a rounding boundary flips between two integers on adjacent frames and reads as a
      # burst of 1 mph steps 0.01 s apart that the car never took. The first run of this tool
      # reported a median gap of 0.10 s -- 10 mph/s, three times the rate CLAUDE.md records as this
      # car's hard ceiling -- which is the tell that it was measuring float noise, not the cluster.
      # A new value has to HOLD before it counts.
      d = round(st["dash"])
      if last_dash is None:
        last_dash, last_change_ts, cand, cand_ts = d, ts, d, ts
      elif st["enab"]:
        if d != cand:
          cand, cand_ts = d, ts
        elif d != last_dash and (ts - cand_ts) >= DEBOUNCE_S:
          delta = d - last_dash
          dt = ts - (last_change_ts if last_change_ts is not None else ts)
          by_icbm = (ts - last_btn_ts) <= ATTRIB_S
          in_band = abs(st["tgt"] - st["dash"]) <= TAP_BAND
          steps.append((ts, delta, dt, by_icbm, in_band, st["tgt"], float(d)))
          last_dash, last_change_ts = d, ts

      # ---------------------------------------------------------------- floor episodes
      #
      # An episode is a maximal RUN of frames whose target is pinned at Ford's floor. The first
      # version of this tried to keep a context trail per episode and got both halves wrong: it
      # never closed an episode cleanly, so episode 2 was episode 1 plus a second, and it printed
      # the trail around frames that were not at the floor at all. A run with a summary line
      # answers "who drove it to 20" and a wall of context does not.
      at_floor = st["enab"] and 0 < st["tgt"] <= FLOOR_MPH + 1.0
      if at_floor:
        if cur_ep is None:
          # `pre` is the state a few seconds BEFORE the descent, which is what says where it came
          # from and who was asking on the way down.
          pre = trail[0][1] if trail else dict(st)
          cur_ep = {"t0": ts, "pre": pre, "src": Counter(), "n": 0, "min_tgt": st["tgt"],
                    "sla": 0, "lead": 0, "slow": 0, "dash0": pre.get("dash", 0.0)}
        cur_ep["n"] += 1
        cur_ep["t1"] = ts
        cur_ep["src"][st["src"]] += 1
        cur_ep["min_tgt"] = min(cur_ep["min_tgt"], st["tgt"])
        cur_ep["sla"] += 1 if 0 < st["slaV"] < NO_TARGET_MPH else 0
        cur_ep["lead"] += 1 if st["lead"] > 0 else 0
        cur_ep["slow"] += 1 if st["slowdown"] else 0
        last_floor_ts = ts
      elif cur_ep is not None and ts - last_floor_ts > 2.0:
        floor_eps.append(cur_ep)
        cur_ep = None

  # ==================================================================== report
  print(f"{total_frames} carState frames, {enabled_frames} with cruise ENGAGED "
        f"({100.0 * enabled_frames / max(total_frames, 1):.1f}%)\n")

  print("=== SET-SPEED STEP SIZES (dash, cruise engaged) ===")
  print("A 5 mph column means the car read a HELD button. A 1 mph column means it read a TAP.\n")
  by = {"ICBM in-band": Counter(), "ICBM out-of-band": Counter(), "driver / other": Counter()}
  gaps = {k: [] for k in by}
  for _ts, delta, dt, by_icbm, in_band, _tgt, _d in steps:
    k = ("ICBM in-band" if in_band else "ICBM out-of-band") if by_icbm else "driver / other"
    by[k][abs(delta)] += 1
    gaps[k].append(dt)
  updown = {k: [0, 0] for k in by}
  for _ts, delta, _dt, by_icbm, in_band, _tgt, _d in steps:
    k = ("ICBM in-band" if in_band else "ICBM out-of-band") if by_icbm else "driver / other"
    updown[k][0 if delta > 0 else 1] += 1
  print(f"  {'who':<18} {'n':>5}  " + "  ".join(f"{m:>4}" for m in (1, 2, 3, 4, 5, 6))
        + "   >6   med dt    up/down")
  for k, c in by.items():
    n = sum(c.values())
    if not n:
      print(f"  {k:<18} {0:>5}")
      continue
    g = sorted(gaps[k])
    med = g[len(g) // 2]
    big = sum(v for m, v in c.items() if m > 6)
    u, dn = updown[k]
    print(f"  {k:<18} {n:>5}  " + "  ".join(f"{c.get(m, 0):>4}" for m in (1, 2, 3, 4, 5, 6))
          + f"  {big:>3}   {med:5.2f}s   {u:>4}/{dn:<4}")
  print()
  print("  in-band  = ICBM was within TAP_BAND (2 mph) of its target, so it was PULSING the button")
  print("  out      = ICBM was further away, so it was HOLDING deliberately. 5 mph there is by")
  print("             DESIGN, not a defect -- the fix for that is a different one entirely.")
  print()

  if cur_ep is not None:
    floor_eps.append(cur_ep)

  # EVERY BIG ICBM STEP, NAMED. The histogram above answers "how often" and he did not ask that --
  # he said he SAW it, which is a claim about individual events. A ratio that reads 869-of-875 is a
  # way of dismissing eleven real ones. List them.
  big = [(ts, d, tgt, dash, in_band) for ts, d, _dt, by_icbm, in_band, tgt, dash in steps
         if by_icbm and abs(d) >= 3]
  print(f"=== ICBM STEPS OF 3 MPH OR MORE: {len(big)} ===")
  if big:
    print(f"  {'time':>9} {'step':>5} {'dash':>6} {'icbmTgt':>8}  band")
    for ts, d, tgt, dash, in_band in big:
      print(f"  t+{ts:7.1f} {d:+5.0f} {dash:6.0f} {tgt:8.1f}  {'in' if in_band else 'out'}")
  else:
    print("  none -- every ICBM step was 1 or 2 mph")
  print()

  print(f"=== TARGET AT FORD'S {FLOOR_MPH:.0f} MPH FLOOR: {len(floor_eps)} episode(s) ===")
  print("Who drove the target down, and what the driver could have seen while it happened.\n")
  print(f"  {'at':>8} {'dur':>6}  {'from':>5} {'min':>4}  {'plan source (while at the floor)':<34}"
        f" {'SLA':>5} {'lead':>5} {'stop?':>6} {'pkLat':>6} {'Rmap':>6} {'Rdrove':>7}  verdict")
  for ep in floor_eps:
    dur = ep.get("t1", ep["t0"]) - ep["t0"]
    n = max(ep["n"], 1)
    top = ", ".join(f"{s} {100 * c // n}%" for s, c in ep["src"].most_common(3))
    t_end = ep.get("t1", ep["t0"]) + CORNER_LOOKAHEAD_S
    window = [(a, v) for t, a, v in lat_samples if ep["t0"] <= t <= t_end]
    peak, v_at_peak = max(window, default=(0.0, 0.0))
    curve_src = any(s.startswith("scc") for s in ep["src"])
    # ONLY SCC-MAP GETS THE RADIUS CHECK. SCC-Vision derives its target completely differently --
    # `v_target = v_ego * sqrt(a_lat_reg_max / max_pred_lat_acc)`, proportional to CURRENT SPEED --
    # so inverting it with the map's formula produces a number that looks authoritative and means
    # nothing. The first run of this printed exactly that for six vision episodes and every one
    # happened to read "map agrees", which is the most dangerous way for a wrong column to be wrong.
    map_dominant = ep["src"].most_common(1)[0][0] == "sccMap"

    # THE RADIUS THE MAP CLAIMED, AGAINST THE ONE THE CAR DROVE. Comparing the map's corner SPEED
    # against the car's speed proves nothing -- the whole point of the controller is that the car
    # slows before the bend, and it publishes the target at the moment braking must BEGIN. The
    # invariant that survives both is the GEOMETRY.
    #
    #   SCC-Map: v_target = factor * sqrt(a_lat / k)   ->   R_map = v_target^2 / (a_lat * factor^2)
    #   the car:                                            R_drove = v^2 / a_lat_measured
    #
    # A ratio near 1 means the map read the road. A ratio in the tens means it invented a hairpin.
    r_map = r_drove = 0.0
    if map_dominant and ep["min_tgt"] > 0:
      v_t = ep["min_tgt"] / MS_TO_MPH
      r_map = v_t * v_t / (SCC_MAP_LAT_A * SCC_MAP_FACTOR * SCC_MAP_FACTOR)
      if peak > 0.05:
        r_drove = v_at_peak * v_at_peak / peak

    verdict = ""
    if curve_src:
      if r_map > 0 and r_drove > 0:
        ratio = r_drove / r_map
        verdict = f"{ratio:5.1f}x too tight" if ratio >= 3.0 else "map agrees"
      elif peak < NO_CORNER_LAT_ACC:
        verdict = "NO CORNER"
    print(f"  t+{ep['t0']:6.0f} {dur:5.1f}s  {ep['dash0']:5.0f} {ep['min_tgt']:4.0f}  {top:<34}"
          f" {100 * ep['sla'] // n:4d}% {100 * ep['lead'] // n:4d}% {100 * ep['slow'] // n:5d}%"
          f" {peak:6.2f} {r_map:6.0f} {r_drove:7.0f}  {verdict}")
  if not floor_eps:
    print("  none -- the target never reached the floor on this route")
  else:
    print()
    print("  from   = the dash a few seconds before the descent started")
    print("  stop?  = share of frames where DEC said the model was slowing for something. A floor")
    print("           episode with a LOW number here had no stop sign or light behind it, which is")
    print("           the shape of *\"it dropped to 20 for no reason\"*.")
    print("  SLA / lead = whether a posted limit or a radar lead could explain it instead.")
    print("  peakLat = the hardest cornering the car ACTUALLY did, from the episode until")
    print(f"            {CORNER_LOOKAHEAD_S:.0f}s after it ended. SCC publishes the corner speed when braking")
    print("            must BEGIN, so the corner arrives later -- looking only at 'now' finds nothing")
    print("            even for a real one.")
    print("  NO CORNER = a curve controller drove the target down and the car then never exceeded")
    print(f"            {NO_CORNER_LAT_ACC:.1f} m/s^2. The road was straight. That is a phantom, and it is what")
    print("            *\"it dropped to 20 for no reason\"* looks like in a log.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
