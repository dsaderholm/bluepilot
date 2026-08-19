#!/usr/bin/env python3
"""FusionPilot: one pass over a route, answering six road reports at once.

Reading a 26-segment route takes minutes, so asking six questions one tool at a time is most of an
hour of the device's CPU for data that is all in the same frames. This walks it ONCE.

  1. THERMAL          "I still feel like my fans are running pretty hard." Peak was 95 C with the fan
                      at 100% on 00000393, against 81-82 C on the two drives before it. Prints the
                      temperature over time next to CPU and memory, because a peak alone cannot tell
                      a hot day from a process spinning -- and `intakeTempC` reads 0.0 on this
                      hardware, so the ambient proxy that check used is worthless.
  2. THE 45 MPH LIMIT "it said the speed limit was 45, even though the speed limit was 70". Every
                      speed-limit change with its source, so a wrong value can be traced to the map
                      or to TSR rather than guessed at.
  3. STOP OVERRIDE    Which of the four arming conditions was missing, on the frames where the model
                      asked for a stop. Ford held 12,056 standstill frames on this drive -- the
                      first time that has ever been non-zero -- so the stops exist now.
  4. +/- ROUTING      The presses that moved the dash and not the MAX, with both numbers before and
                      after, so the complaint has a frame number rather than an impression.
  5. OP LONG SLOW     "it switched to OP long for acceleration and it went ridiculously slow." Runs
                      of `fallback` authority with the speed through them.
  6. LOW SPEED CURVES "Low speed curves, it isn't slowing down enough." Every SCC activation under
                      40 mph with what it asked for against what the car was doing.

NO DATA IS NOT ZERO. Every section says explicitly when the message it needs never arrived, because
three separate findings tonight were a missing field printing as a confident zero.

USAGE, on the device:

    cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 \\
        tools/bp_six_complaints.py 00000393--b8349cc881
"""
from __future__ import annotations

import os
import sys

REALDATA = "/data/media/0/realdata"
MS_TO_MPH = 2.23694
KPH_TO_MPH = 0.621371


def seg_index(name: str) -> int:
  tail = name.rsplit("--", 1)[-1]
  return int(tail) if tail.isdigit() else -1


def main() -> int:
  if len(sys.argv) < 2:
    sys.exit("usage: bp_six_complaints.py <route>")
  route = sys.argv[1]
  sys.path.insert(0, "/data/openpilot")
  from openpilot.tools.lib.logreader import LogReader

  segs = sorted([d for d in os.listdir(REALDATA) if d.startswith(route)], key=seg_index)
  print("# route {}  ({} segments)".format(route, len(segs)))

  t0 = None
  # 1 thermal
  thermal = []          # (t, maxTemp, cpuPct, memPct, fan)
  # 2 limits
  limit_changes = []    # (t, mph, source)
  last_limit = None
  # 3 override funnel
  fn = dict(slow=0, speed=0, stopping=0, nolead=0, all=0)
  seen_lcs = False
  op_stopping = False
  lead_m = 0.0
  # 4 presses
  presses = []
  pending = []
  # 5 fallback runs
  auth_runs = []
  cur_auth = None
  last_auth = None
  # 6 low speed curves
  slow_curves = []

  v_mph = 0.0
  dash = 0.0
  mx = 0.0

  for seg in segs:
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
      rt = t - t0 if t0 is not None else 0.0

      try:
        if w == "deviceState":
          d = m.deviceState
          cpus = list(getattr(d, "cpuUsagePercent", []) or [])
          thermal.append((rt,
                          float(getattr(d, "maxTempC", 0.0)),
                          (sum(cpus) / len(cpus)) if cpus else -1.0,
                          float(getattr(d, "memoryUsagePercent", -1)),
                          float(getattr(d, "fanSpeedPercentDesired", -1))))

        elif w == "carState":
          cs = m.carState
          v_mph = float(cs.vEgo) * MS_TO_MPH
          dash = float(cs.cruiseState.speedCluster) * MS_TO_MPH
          mx = float(cs.vCruiseCluster) * KPH_TO_MPH
          for be in cs.buttonEvents or []:
            if be.pressed and str(be.type) in ("accelCruise", "decelCruise"):
              pending.append([rt, str(be.type), dash, mx, None, None])
          keep = []
          for pr in pending:
            if rt - pr[0] >= 1.5:
              pr[4], pr[5] = dash, mx
              presses.append(pr)
            else:
              keep.append(pr)
          pending = keep

        elif w == "carControl":
          try:
            op_stopping = str(m.carControl.actuators.longControlState) == "stopping"
            seen_lcs = True
          except Exception:
            op_stopping = False

        elif w == "radarState":
          ld = m.radarState.leadOne
          lead_m = float(ld.dRel) if bool(ld.status) else 0.0

        elif w == "controllerStateBP":
          a = str(m.controllerStateBP.accAuthority)
          if a != last_auth:
            if cur_auth is not None:
              cur_auth[2] = rt
              auth_runs.append(cur_auth)
            cur_auth = [a, rt, rt, v_mph, v_mph]
            last_auth = a
          elif cur_auth is not None:
            cur_auth[2] = rt
            cur_auth[3] = min(cur_auth[3], v_mph)
            cur_auth[4] = max(cur_auth[4], v_mph)

        elif w == "longitudinalPlanSP":
          lp = m.longitudinalPlanSP
          try:
            r = lp.speedLimit.resolver
            lim = float(r.speedLimit) * MS_TO_MPH
            src = str(r.source)
            key = (round(lim), src)
            if lim > 0 and key != last_limit:
              limit_changes.append((rt, round(lim, 1), src, round(v_mph)))
              last_limit = key
          except Exception:
            pass
          try:
            if lp.dec.hasSlowDown:
              fn["slow"] += 1
              se = v_mph <= 20.0
              nl = not (0.0 < lead_m < 60.0)
              if se:
                fn["speed"] += 1
              if op_stopping:
                fn["stopping"] += 1
              if nl:
                fn["nolead"] += 1
              if se and op_stopping and nl:
                fn["all"] += 1
          except Exception:
            pass
          for nm in ("map", "vision"):
            try:
              scc = getattr(lp.smartCruiseControl, nm)
              if bool(scc.active) and v_mph < 40.0:
                tgt = float(scc.vTarget) * MS_TO_MPH
                if 0 < tgt < 200:
                  slow_curves.append((rt, nm, round(v_mph), round(tgt)))
            except Exception:
              pass
      except Exception:
        continue

  if cur_auth is not None:
    auth_runs.append(cur_auth)

  # ---- 1 thermal -------------------------------------------------------------------------
  print("")
  print("=== 1. THERMAL ===")
  if not thermal:
    print("  NO DATA -- deviceState never arrived")
  else:
    peak = max(thermal, key=lambda r: r[1])
    print("  peak {:.0f} C at t+{:.0f}s   cpu {:.0f}%  mem {:.0f}%  fan {:.0f}%".format(
      peak[1], peak[0], peak[2], peak[3], peak[4]))
    print("  {:>7} {:>7} {:>6} {:>6} {:>6}".format("t+", "degC", "cpu%", "mem%", "fan%"))
    step = max(1, len(thermal) // 12)
    for r in thermal[::step]:
      print("  {:7.0f} {:7.0f} {:6.0f} {:6.0f} {:6.0f}".format(r[0], r[1], r[2], r[3], r[4]))

  # ---- 2 limits --------------------------------------------------------------------------
  print("")
  print("=== 2. SPEED LIMIT CHANGES (source-tagged) ===")
  if not limit_changes:
    print("  NO DATA -- no non-zero speed limit ever published")
  else:
    for rt, lim, src, v in limit_changes[:40]:
      flag = "  <-- LOW" if lim <= 50 and v >= 60 else ""
      print("  t+{:7.0f}s   {:3.0f} mph   src={:<5} while doing {:3d} mph{}".format(
        rt, lim, src, v, flag))
    if len(limit_changes) > 40:
      print("  ... {} more".format(len(limit_changes) - 40))

  # ---- 3 override ------------------------------------------------------------------------
  print("")
  print("=== 3. STOP OVERRIDE FUNNEL ===")
  if fn["slow"] == 0:
    print("  NO DATA -- the model never asked for a stop")
  elif not seen_lcs:
    print("  NO DATA -- longControlState never read; cannot rule `stopping` in or out")
  else:
    for k, lbl in (("slow", "model asked for a stop"), ("speed", "...and <= 20 mph"),
                   ("stopping", "...and plan was STOPPING"), ("nolead", "...and no lead in 60 m"),
                   ("all", "ALL FOUR -- could arm")):
      print("  {:<32} {:6d}  {:5.1f}%".format(lbl, fn[k], 100.0 * fn[k] / fn["slow"]))

  # ---- 4 presses -------------------------------------------------------------------------
  print("")
  print("=== 4. +/- PRESSES (dash = ICBM number, max = MAX) ===")
  if not presses:
    print("  NO DATA -- no driver set-speed presses")
  else:
    for rt, ty, d0, m0, d1, m1 in presses:
      dm = abs((m1 or 0) - m0) > 0.4
      dd = abs((d1 or 0) - d0) > 0.4
      verdict = "BOTH" if (dm and dd) else ("MAX only" if dm else ("DASH ONLY <-- BUG" if dd else "neither"))
      print("  t+{:7.0f}s {:<12} dash {:5.1f}->{:5.1f}   max {:5.1f}->{:5.1f}   {}".format(
        rt, ty, d0, d1 or 0, m0, m1 or 0, verdict))

  # ---- 5 fallback ------------------------------------------------------------------------
  print("")
  print("=== 5. WHO AUTHORED, run by run (runs >= 2 s) ===")
  longruns = [r for r in auth_runs if (r[2] - r[1]) >= 2.0 and r[0] not in ("stock",)]
  if not auth_runs:
    print("  NO DATA -- controllerStateBP never arrived")
  else:
    for a, s, e, vmin, vmax in longruns[:25]:
      print("  t+{:7.0f}s -> {:7.0f}s  {:>9}  {:5.1f}s   speed {:3.0f}-{:3.0f} mph".format(
        s, e, a, e - s, vmin, vmax))
    if not longruns:
      print("  no non-stock authority run lasted 2 s")

  # ---- 6 low speed curves ----------------------------------------------------------------
  print("")
  print("=== 6. SCC ACTIVE UNDER 40 MPH ===")
  if not slow_curves:
    print("  NO DATA -- neither curve controller was active under 40 mph")
  else:
    step = max(1, len(slow_curves) // 20)
    for rt, nm, v, tgt in slow_curves[::step]:
      print("  t+{:7.0f}s  {:<6} doing {:3d} mph, asking {:3d} mph  (delta {:+d})".format(
        rt, nm, v, tgt, tgt - v))
    print("  {} frames total".format(len(slow_curves)))

  return 0


if __name__ == "__main__":
  raise SystemExit(main())
