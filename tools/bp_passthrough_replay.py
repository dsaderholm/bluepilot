#!/usr/bin/env python3
"""FusionPilot: replay a recorded drive through the CURRENT passthrough logic.

Drive A (00000383, 2026-08-18) was made with a version that forwarded the camera's cancel request,
which knocked openpilot out and then knocked it out again on every re-engagement -- seventy times.
`passthrough_admissible` has since been widened and `AccPrpl_A_Pred` is pinned rather than costing
the frame. **None of that has been on a road.**

So replay the drive. Every camera ACCDATA frame is in the log, the decision function is pure, and
the question "what would today's code have done" is therefore answerable exactly, offline, without
asking him to drive it again.

WHAT IT REPORTS, and why each number is the one to look at:

  FORWARD RATE. The share of engaged frames where Ford's own command would go out. This is the
  whole value of the feature -- at 50% the car is being driven by two controllers taking turns.

  HANDOVER COUNT. How often it would SWITCH between Ford's command and ours. This matters more than
  the rate and is easy to miss: 95% forwarding split into 400 handovers is worse than 80% in four,
  because every handover is a step change in commanded acceleration between two controllers that
  never agreed on anything. If this number is large, the fix is dwell time, not a better predicate.

  THE CANCEL CASCADE. Whether the t+234.44 loop would form again. It must not.

USAGE, on the device:

    cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 tools/bp_passthrough_replay.py
    python tools/bp_passthrough_replay.py --route 00000383--7934cf27c1
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict

REALDATA = "/data/media/0/realdata"
ACCDATA_ADDR = 0x186
CAM_BUS = 2


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


def decode(dat: bytes) -> dict:
  """The ACCDATA fields the decision turns on, extracted the way panda extracts them.

  Deliberately NOT via the DBC: panda reads raw bytes and bit offsets, and the question here is what
  PANDA would decide. Bit positions checked against the DBC's `start|len@0+` big-endian form, where
  byte = start // 8 and shift = start % 8 -- verified against ford.h's own CmbbDeny_B_Actl read
  (`data[4] >> 5`, DBC start bit 37).
  """
  return {
    "AccBrkTot_A_Rq": (((dat[0] & 0x1F) << 8) | dat[1]) * 0.0039 - 20.0,
    "AccPrpl_A_Pred": (((dat[2] & 0x3) << 8) | dat[3]) * 0.01 - 5.0,
    "AccPrpl_A_Rq": (((dat[6] & 0x3) << 8) | dat[7]) * 0.01 - 5.0,
    "CmbbDeny_B_Actl": (dat[4] >> 5) & 1,       # start 37
    "AccCancl_B_Rq": (dat[4] >> 7) & 1,         # start 39
    "AccBrkPrkEl_B_Rq": (dat[4] >> 6) & 1,      # start 38
    "AccStopStat_B_Rq": (dat[4] >> 2) & 1,      # start 34
    "AccBrkPulse_B_Rq": (dat[4] >> 4) & 1,      # start 36
    "AccDeny_B_Rq": (dat[6] >> 5) & 1,          # start 53
    "AccAutoResum_D_Rq": (dat[0] >> 6) & 0x3,   # start 7, length 2
  }


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
  sys.path.insert(0, "/data/openpilot")
  from opendbc.sunnypilot.car.ford.fordcan_ext import passthrough_admissible

  route = args.route or newest_route()
  segs = sorted([d for d in os.listdir(REALDATA) if d.startswith(route)], key=seg_index)
  print(f"# route: {route}  ({len(segs)} segments)")

  engaged = 0
  forwarded = 0
  handovers = 0
  last_decision = None
  reasons: Counter = Counter()
  # Longest unbroken stretch on each controller: a feature that alternates every few frames is a
  # different thing from one that hands over a handful of times.
  run = 0
  runs_fwd: list[int] = []
  runs_fb: list[int] = []

  for seg in segs[:args.max_segments]:
    p = os.path.join(REALDATA, seg, "rlog")
    if not os.path.exists(p):
      p += ".zst"
    if not os.path.exists(p):
      continue
    try:
      lr = LogReader(p)
    except Exception:  # noqa: BLE001
      continue

    oe = False
    for m in lr:
      try:
        w = m.which()
      except Exception:  # noqa: BLE001
        continue
      if w == "carControl":
        oe = bool(m.carControl.enabled)
      elif w == "can":
        for c in m.can:
          if c.address != ACCDATA_ADDR or c.src != CAM_BUS or not oe:
            continue
          engaged += 1
          reason = passthrough_admissible(decode(bytes(c.dat)), True)
          ok = not reason
          reasons[reason or "ADMISSIBLE"] += 1
          if ok:
            forwarded += 1
          if last_decision is None:
            last_decision = ok
            run = 1
          elif ok == last_decision:
            run += 1
          else:
            (runs_fwd if last_decision else runs_fb).append(run)
            handovers += 1
            last_decision = ok
            run = 1
  if last_decision is not None:
    (runs_fwd if last_decision else runs_fb).append(run)

  if not engaged:
    print("no engaged camera ACCDATA frames -- was openpilot longitudinal on for this route?")
    return 0

  print(f"\n=== {engaged:,} camera ACCDATA frames while openpilot was ENGAGED ===")
  for k, v in reasons.most_common():
    print(f"   {k[:52]:<54} {v:6d}  {100.0 * v / engaged:5.1f}%")

  print(f"\n  FORWARD RATE   {100.0 * forwarded / engaged:.1f}%   ({forwarded:,} of {engaged:,})")
  print(f"  HANDOVERS      {handovers}")
  if handovers:
    secs = engaged / 50.0
    print(f"                 one every {secs / handovers:.1f} s of engaged driving")

  def summarize(name, rs):
    if not rs:
      print(f"  {name}: none")
      return
    rs = sorted(rs)
    print(f"  {name}: {len(rs)} runs, median {rs[len(rs)//2]/50.0:.2f} s, "
          f"longest {rs[-1]/50.0:.1f} s, shortest {rs[0]/50.0:.2f} s")
  summarize("on FORD's command ", runs_fwd)
  summarize("on OPENPILOT's    ", runs_fb)

  cascade = reasons.get("camera asserted AccCancl_B_Rq -- unpoliced actuation, see drive A", 0)
  print(f"\n  cancel frames REFUSED rather than relayed: {cascade:,}")
  print("  Every one of those was a frame the old code forwarded to the car. Forwarding the first")
  print("  of them ended an engagement in 10 ms and started seventy re-engagement cycles.")

  print("\n  READ THE HANDOVER COUNT FIRST. A high forward rate split across hundreds of switches is")
  print("  two controllers taking turns, and each switch is a step change in commanded accel between")
  print("  two planners that never agreed. If that is what this shows, the answer is minimum dwell")
  print("  time on a decision -- not a cleverer predicate.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
