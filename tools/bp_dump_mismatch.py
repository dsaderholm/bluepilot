#!/usr/bin/env python3
"""BluePilot: dump what was true around a "Controls Mismatch", on the device.

Reported 2026-08-06: controlsMismatch appears when resuming cruise at a complete stop, never just
from stopping. It is ET.IMMEDIATE_DISABLE, so it takes everything down with it.

selfdrived raises that event from exactly three places (selfdrived.py, ~line 361):

  1. safety_mismatch      -- pandaState.safetyModel / safetyParam / alternativeExperience differs
                             from CarParams
  2. safetyRxChecksInvalid -- a message in ford_rx_checks stopped arriving at its declared rate
  3. mismatch_counter >= 200 -- two seconds of openpilot ENABLED while panda says controls are not
                             allowed

"Only when I press resume" already argues for 3: a lapsing CAN message does not care whether a
button was pressed, and a safety-config mismatch would fire constantly rather than at stops. This
script exists to confirm that rather than believe it, and to show WHICH side moved first -- whether
panda dropped controls_allowed while openpilot stayed enabled, or openpilot enabled while panda had
not yet allowed.

USAGE, on the device:

    cd /data/openpilot && python tools/bp_dump_mismatch.py

    python tools/bp_dump_mismatch.py --route 00000042--aa11bb22cc   # a specific route
    python tools/bp_dump_mismatch.py --window 5                     # seconds either side

It prints one block per occurrence. Paste the whole thing back.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import deque

REALDATA = "/data/media/0/realdata"
FIELDS_HELP = "cA=controlsAllowed rxOK=not safetyRxChecksInvalid | enab=selfdriveState.enabled"


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


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--route", default=None)
  ap.add_argument("--window", type=float, default=3.0, help="seconds either side of the event")
  args = ap.parse_args()

  try:
    from openpilot.tools.lib.logreader import LogReader
  except ImportError as e:
    sys.exit(f"could not import LogReader ({e}); run this from /data/openpilot on the device")

  # Latest value of everything interesting, sampled whenever a controlsMismatch shows up.
  state = {
    "vEgo": None, "standstill": None, "cruiseEnabled": None, "cruiseStandstill": None,
    "buttons": "", "ccEnabled": None, "ccResume": None,
    "sdEnabled": None, "sdActive": None, "alert": "",
    "controlsAllowed": None, "rxChecksInvalid": None, "safetyModel": None, "safetyParam": None,
    "altExperience": None,
  }
  history: deque = deque(maxlen=4000)   # ~40 s at 100 Hz of carState
  hits = 0
  t0 = None

  for seg in find_segments(args.route):
    path = log_path(seg)
    if path is None:
      continue
    for msg in LogReader(path):
      w = msg.which()
      t = msg.logMonoTime / 1e9
      if t0 is None:
        t0 = t

      if w == "carState":
        cs = msg.carState
        state["vEgo"] = round(cs.vEgo, 2)
        state["standstill"] = cs.standstill
        state["cruiseEnabled"] = cs.cruiseState.enabled
        state["cruiseStandstill"] = cs.cruiseState.standstill
        if cs.buttonEvents:
          state["buttons"] = ",".join(f"{b.type}{'+' if b.pressed else '-'}" for b in cs.buttonEvents)
      elif w == "carControl":
        cc = msg.carControl
        state["ccEnabled"] = cc.enabled
        state["ccResume"] = cc.cruiseControl.resume
      elif w == "selfdriveState":
        ss = msg.selfdriveState
        state["sdEnabled"] = ss.enabled
        state["sdActive"] = ss.active
        state["alert"] = ss.alertText1
      elif w == "pandaStates":
        for ps in msg.pandaStates:
          state["controlsAllowed"] = ps.controlsAllowed
          state["rxChecksInvalid"] = ps.safetyRxChecksInvalid
          state["safetyModel"] = str(ps.safetyModel)
          state["safetyParam"] = ps.safetyParam
          state["altExperience"] = ps.alternativeExperience
          break

      history.append((t - t0, w, dict(state)))

      mismatch = (w == "onroadEvents" and any(str(e.name) == "controlsMismatch" for e in msg.onroadEvents)) \
          or (w == "selfdriveState" and "Controls Mismatch" in (msg.selfdriveState.alertText1 or ""))
      if mismatch:
        hits += 1
        print(f"\n===== controlsMismatch #{hits} at t+{t - t0:7.2f}s  ({os.path.basename(seg)}) =====")
        print(f"# {FIELDS_HELP}")
        lo = (t - t0) - args.window
        shown = 0
        for ts, kind, snap in history:
          if ts < lo or kind != "carState":
            continue
          shown += 1
          if shown % 10:        # carState is 100 Hz; 10 Hz is plenty to read
            continue
          print(f"  t+{ts:7.2f} v={snap['vEgo']:>5} standstill={snap['standstill']!s:<5} "
                f"cruiseEn={snap['cruiseEnabled']!s:<5} cruiseStand={snap['cruiseStandstill']!s:<5} "
                f"| ccEn={snap['ccEnabled']!s:<5} ccResume={snap['ccResume']!s:<5} "
                f"| sdEn={snap['sdEnabled']!s:<5} "
                f"| cA={snap['controlsAllowed']!s:<5} rxInvalid={snap['rxChecksInvalid']!s:<5} "
                f"| btn={snap['buttons']}")
        s = state
        print(f"  SAFETY CONFIG  model={s['safetyModel']} param={s['safetyParam']} "
              f"altExperience={s['altExperience']}")
        print(f"  ALERT          {s['alert']!r}")
        history.clear()

  if hits == 0:
    print("no controlsMismatch found in this route.")
    print("Re-run with --route <name> for the drive it happened on; routes are directory names")
    print(f"under {REALDATA} with the trailing --<segment> removed.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
