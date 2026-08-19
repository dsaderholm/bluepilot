#!/usr/bin/env python3
"""FusionPilot: WHY did the passthrough hand the car to openpilot, and can the stop override ever arm?

Two questions from the 2026-08-19 road report, in one pass, because both need every segment.

1. *"it switched to OP long for acceleration and it went ridiculously slow"* -- route 00000393 had
   1,986 `fallback` frames and 1,246 of them were UNDER 15 MPH, median 10. So the passthrough is
   handing Ford's job to openpilot exactly at launch, where openpilot is weakest and Ford is best.
   This replays the REAL `passthrough_admissible` against the REAL camera frames and counts the
   reason it returned, overall and restricted to low speed.

   The suspect is `AccAutoResum_D_Rq`. Ford asserts auto-resume when pulling away from a stop-hold,
   which is precisely 0-15 mph -- and it sits on the unpoliced-bit refusal list, which is exactly the
   shape of the `AccStopStat_B_Rq` mistake already corrected once: a bit that went on the list by
   guilt-by-association after drive A's park brake, and cost the feature the case it exists for.

2. THE STOP OVERRIDE'S FOUR CONDITIONS ARE NEVER ALL TRUE, and the funnel cannot say why. On
   00000393: <=20 mph on 78.2% of stop frames, plan `stopping` on 23.3%, no lead on 40.8% -- and
   ALL FOUR on 0.0%. Independent, that intersection would be ~7.5%. Exactly zero means two of them
   are ANTI-CORRELATED, and the likely pair is damning: if openpilot's plan only commits to
   `stopping` when there IS a lead, then "plan stopping" and "no lead within 60 m" are mutually
   exclusive and the override can NEVER fire, by construction rather than by tuning.

   So this prints the pairwise intersections the funnel omitted.

USAGE, on the device:

    cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 \\
        tools/bp_fallback_reason.py 00000393--b8349cc881
"""
from __future__ import annotations

import os
import sys
from collections import Counter

REALDATA = "/data/media/0/realdata"
MS_TO_MPH = 2.23694
ACCDATA = 0x186
CAM_BUS = 2
SLOW_MPH = 15.0


def seg_index(name: str) -> int:
  tail = name.rsplit("--", 1)[-1]
  return int(tail) if tail.isdigit() else -1


def main() -> int:
  if len(sys.argv) < 2:
    sys.exit("usage: bp_fallback_reason.py <route>")
  route = sys.argv[1]
  sys.path.insert(0, "/data/openpilot")
  from openpilot.tools.lib.logreader import LogReader
  from opendbc.sunnypilot.car.ford import fordcan_ext as fe
  from opendbc.car.ford.values import DBC
  from opendbc.can.parser import CANParser

  segs = sorted([d for d in os.listdir(REALDATA) if d.startswith(route)], key=seg_index)
  print("# route {}  ({} segments)".format(route, len(segs)))

  cp = CANParser(DBC["FORD_FUSION_MK5"]["pt"], [("ACCDATA", 50)], CAM_BUS)

  reasons: Counter[str] = Counter()
  reasons_slow: Counter[str] = Counter()
  seen = 0

  # Pairwise, over frames where the model asked for a stop.
  pair = Counter()
  slow = stopping = nolead = 0
  fn_total = 0

  v_mph = 0.0
  auth = "?"
  op_stopping = False
  lead_m = 0.0

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
      try:
        if w == "carState":
          v_mph = float(m.carState.vEgo) * MS_TO_MPH
        elif w == "controllerStateBP":
          auth = str(m.controllerStateBP.accAuthority)
        elif w == "carControl":
          op_stopping = str(m.carControl.actuators.longControlState) == "stopping"
        elif w == "radarState":
          ld = m.radarState.leadOne
          lead_m = float(ld.dRel) if bool(ld.status) else 0.0
        elif w == "longitudinalPlanSP":
          if bool(m.longitudinalPlanSP.dec.hasSlowDown):
            fn_total += 1
            a = v_mph <= 20.0
            b = op_stopping
            c = not (0.0 < lead_m < 60.0)
            slow += a
            stopping += b
            nolead += c
            pair["speed+stopping"] += a and b
            pair["speed+nolead"] += a and c
            pair["stopping+nolead"] += b and c
            pair["all three"] += a and b and c
        elif w == "can":
          cp.update_strings([m.as_builder().to_bytes()])
          if auth != "fallback":
            continue
          for c_ in m.can:
            if c_.address == ACCDATA and c_.src == CAM_BUS:
              sv = cp.vl["ACCDATA"]
              seen += 1
              r = fe.passthrough_admissible(sv, True) or "(admissible -- fallback had another cause)"
              reasons[r] += 1
              if v_mph < SLOW_MPH:
                reasons_slow[r] += 1
      except Exception:
        continue

  print("")
  print("=== 1. WHY THE PASSTHROUGH FELL BACK ({} camera frames while falling back) ===".format(seen))
  if not seen:
    print("  NO DATA -- no fallback frame carried a camera ACCDATA")
  else:
    print("  ALL fallback frames:")
    for r, n in reasons.most_common(8):
      print("    {:6d}  {:5.1f}%  {}".format(n, 100.0 * n / seen, r))
    tot_slow = sum(reasons_slow.values())
    print("  UNDER {:.0f} MPH ({} frames) -- the launch case he reported:".format(SLOW_MPH, tot_slow))
    for r, n in reasons_slow.most_common(8):
      print("    {:6d}  {:5.1f}%  {}".format(n, 100.0 * n / max(tot_slow, 1), r))

  print("")
  print("=== 2. CAN THE STOP OVERRIDE EVER ARM? (frames where the model asked for a stop) ===")
  if not fn_total:
    print("  NO DATA -- the model never asked for a stop")
  else:
    def row(label: str, n: int) -> None:
      print("  {:<26} {:6d}  {:5.1f}%".format(label, n, 100.0 * n / fn_total))
    row("model asked for a stop", fn_total)
    row("<= 20 mph", slow)
    row("plan STOPPING", stopping)
    row("no lead in 60 m", nolead)
    print("  -- pairwise --")
    for k in ("speed+stopping", "speed+nolead", "stopping+nolead", "all three"):
      row(k, pair[k])
    if pair["stopping+nolead"] == 0 and stopping and nolead:
      print("")
      print("  STOPPING AND NO-LEAD NEVER CO-OCCUR. The plan commits to stopping only when there is")
      print("  a lead, so the override's own no-lead requirement makes it UNABLE TO FIRE. That is a")
      print("  design contradiction, not a threshold to move.")

  return 0


if __name__ == "__main__":
  raise SystemExit(main())
