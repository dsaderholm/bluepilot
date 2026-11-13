#!/usr/bin/env python3
"""WHERE does the slow-pass conjunction fire? It jumped 16% -> 51% between drives.

The warning is about hogging a freeway lane. The drive that produced 51% included surface streets
for the first time, and the anchor now answers on `primary` at 90%. If the conjunction is firing on
city roads it is not a tuning question, it is the warning appearing somewhere it has no business.

Splits the conjunction by road class, and reports the third term of the gate too -- the real gate is
lead_is_slow AND leftmost AND right_geometry_ok, and only the first two are scored elsewhere.
"""
import glob, os, re, sys
from collections import Counter

def segments_in_order(route):
  segs = glob.glob(f"/data/media/0/realdata/{route}--*")
  def idx(p):
    m = re.search(r"--(\d+)$", p)
    return int(m.group(1)) if m else -1
  return sorted(segs, key=idx)


# THE UNITS DO NOT MATCH. `speedDeficit` is published in m/s (custom.capnp @6) and
# `minDeficitActive` in MPH (passing_assist.py: `min_deficit_ms * MS_TO_MPH`). Comparing them
# directly demands a lead 2.237x slower than the real gate does, and every slow-lead count this
# tool published was over that far too strict population.
#
# `minDeficitActive` is also the SETTING, not the live threshold -- the gate tests
# `min_deficit_active_ms`, the setting times `patience_scale`. Patience only ever RAISES the bar,
# so the setting over-counts wherever patience was active. That part is not on the wire, so it is
# stated here rather than silently absorbed.
DEFICIT_MPH_TO_MS = 0.44704


def main():
  sys.path.insert(0, "/data/openpilot")
  from openpilot.tools.lib.logreader import LogReader
  from openpilot.sunnypilot.selfdrive.controls.lib.lane_anchor import LaneAnchor
  from cereal import custom
  for f in ("speedDeficit", "minDeficitActive", "hasLead", "rightGeometryOk"):
    if f not in set(custom.LongitudinalPlanSP.PassingAssist.schema.fieldnames):
      sys.exit(f"passingAssist has no field {f!r}")

  slow_by = Counter(); hog_by = Counter(); full_by = Counter(); speed_by = {}
  speed = 0.0; lanes = 0; one_way = False; hwy = ""
  lead_slow = False; right_ok = False
  anchor = LaneAnchor()

  for route in sys.argv[1:]:
    for s in segments_in_order(route):
      p = os.path.join(s, "rlog.zst")
      if not os.path.exists(p):
        continue
      for m in LogReader(p):
        w = m.which()
        if w == "carState":
          speed = float(m.carState.vEgo)
        elif w == "mapdOut":
          try:
            lanes = int(m.mapdOut.lanes); one_way = bool(m.mapdOut.oneWay)
            hwy = str(m.mapdOut.highwayClass)
          except Exception:
            lanes, one_way, hwy = 0, False, "?"
        elif w == "longitudinalPlanSP":
          pa = m.longitudinalPlanSP.passingAssist
          # See DEFICIT_MPH_TO_MS: speedDeficit is m/s, minDeficitActive is mph.
          d, t = float(pa.speedDeficit), float(pa.minDeficitActive) * DEFICIT_MPH_TO_MS
          lead_slow = bool(pa.hasLead) and t > 0 and d >= t
          right_ok = bool(pa.rightGeometryOk)
        elif w == "modelV2":
          if speed < 15.0:
            continue
          try:
            probs = m.modelV2.laneLineProbs
            fl, fr = float(probs[0]), float(probs[3])
            std = float(m.modelV2.roadEdgeStds[1]); d = float(m.modelV2.roadEdges[1].y[0])
          except Exception:
            continue
          anchor.update(0.05, d, std, lanes, one_way, fl, fr)
          if not lead_slow:
            continue
          slow_by[hwy] += 1
          if anchor.in_leftmost_lane():
            hog_by[hwy] += 1
            if right_ok:
              full_by[hwy] += 1
              speed_by.setdefault(hwy, []).append(speed)

  print(f"{'road class':<16} {'slow lead':>10} {'+leftmost':>10} {'+right geo':>11}   (the real gate)")
  for c in sorted(slow_by, key=lambda x: -slow_by[x]):
    print(f"  {c:<14} {slow_by[c]:>10} {hog_by[c]:>10} {full_by[c]:>11}")
  print()
  tot_s, tot_f = sum(slow_by.values()), sum(full_by.values())
  print(f"ALL: slow lead {tot_s}, full gate {tot_f} ({100.0*tot_f/max(tot_s,1):.1f}%)")
  for c, v in speed_by.items():
    v = sorted(v)
    print(f"  {c}: speed at the warning  p10 {v[len(v)//10]*2.237:.0f}  p50 {v[len(v)//2]*2.237:.0f} mph")

main()
