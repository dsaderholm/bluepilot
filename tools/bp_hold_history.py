#!/usr/bin/env python3
"""FusionPilot: every change to the HOLD, and what caused it.

Two reports on 2026-08-11 that guessing cannot separate: "at some point my hold dropped by 5 mph,
which was strange", and "when I resumed, my hold went away". Both are changes to v_baseline, and
selfdriveStateSP already logs vBaseline alongside baselineSource -- which names the mechanism that
set it, so a pin re-applying itself is distinguishable from a button press without inference.

Prints one line per CHANGE rather than a timeline, because a hold is stable for minutes at a time and
the interesting frames are the handful where it moves. Cruise engage/disengage and the button events
are interleaved, since the resume path decides whether to keep a hold by comparing the set speed
before disengage against the one that lands after -- so the disengage and the landing have to be
visible together to tell a correct decision from an unlucky one.

USAGE, on the device:

    cd /data/openpilot && python tools/bp_hold_history.py
    python tools/bp_hold_history.py --route 00000042--aa11bb22cc
"""
from __future__ import annotations

import argparse
import os
import sys

REALDATA = "/data/media/0/realdata"
MS_TO_MPH = 2.23694

# What each baselineSource means in one line, so the output explains itself.
WHY = {
  "none": "no hold",
  "press": "a real button press -- the driver asked for this",
  "fallbackIdle": "set speed moved while ICBM was silent, so it was read as the driver",
  "fallbackCounter": "set speed moved AGAINST ICBM's own button",
  "pinned": "A PIN RE-APPLIED ITSELF -- a number saved on an earlier drive",
}


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--route", default=None)
  ap.add_argument("--max-segments", type=int, default=14)
  args = ap.parse_args()

  try:
    from openpilot.tools.lib.logreader import LogReader
  except ImportError as e:
    sys.exit(f"no LogReader ({e}); run from /data/openpilot")

  if not os.path.isdir(REALDATA):
    sys.exit(f"no {REALDATA} -- run this on the device")
  entries = sorted(d for d in os.listdir(REALDATA) if "--" in d)
  route = args.route or entries[-1].rsplit("--", 1)[0]
  segs = [d for d in entries if d.startswith(route + "--")][:args.max_segments]
  print(f"# route {route}, {len(segs)} segments\n")

  t0 = None
  baseline = None
  source = "none"
  enabled = None
  dash = 0.0
  v = 0.0
  print("   time    hold  source            dash   mph  event")
  print("  " + "-" * 74)

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
      t = msg.logMonoTime / 1e9
      if t0 is None:
        t0 = t
      ts = t - t0
      try:
        if w == "carState":
          cs = msg.carState
          v = cs.vEgo * MS_TO_MPH
          dash = cs.cruiseState.speedCluster * MS_TO_MPH
          en = bool(cs.cruiseState.enabled)
          if enabled is not None and en != enabled:
            print(f"  t+{ts:6.0f} {'':7s} {'':17s} {dash:5.0f} {v:5.0f}  "
                  f"CRUISE {'ENGAGED' if en else 'OFF'}")
          enabled = en
          for be in cs.buttonEvents:
            if be.pressed:
              print(f"  t+{ts:6.0f} {'':7s} {'':17s} {dash:5.0f} {v:5.0f}  "
                    f"button {be.type} pressed")
        elif w == "selfdriveStateSP":
          icbm = msg.selfdriveStateSP.intelligentCruiseButtonManagement
          b = round(float(icbm.vBaseline))
          s = str(icbm.baselineSource)
          if baseline is None:
            baseline, source = b, s
            continue
          if b != baseline or s != source:
            if b == 0 and baseline > 0:
              note = f"HOLD CLEARED (was {baseline})"
            elif baseline == 0:
              note = f"hold created: {WHY.get(s, s)}"
            else:
              note = f"hold moved {baseline} -> {b}: {WHY.get(s, s)}"
            print(f"  t+{ts:6.0f} {b:6d}  {s:<16} {dash:5.0f} {v:5.0f}  {note}")
            baseline, source = b, s
      except Exception:  # noqa: BLE001
        continue

  print()
  print("  A hold moving with source `pinned` was not the driver. A hold clearing right after a")
  print("  CRUISE ENGAGED line is the resume path deciding the re-engage was a SET, not a RESUME.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
