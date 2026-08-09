#!/usr/bin/env python3
"""FusionPilot: dump what was true around a "Controls Mismatch", on the device.

Reported 2026-08-06: controlsMismatch appears when resuming cruise at a complete stop, never just
from stopping. It is ET.IMMEDIATE_DISABLE, so it takes everything down with it.

selfdrived raises that event from exactly three places (selfdrived.py, ~line 361):

  1. safety_mismatch      -- pandaState.safetyModel / safetyParam / alternativeExperience differs
                             from CarParams
  2. safetyRxChecksInvalid -- a message in ford_rx_checks stopped arriving at its declared rate
  3. mismatch_counter >= 200 -- two seconds of openpilot ENABLED while panda says controls are not
                             allowed

THE FIELD THAT DECIDES IT is selfdriveState.ACTIVE, which the first version of this script collected
and then forgot to print. State.preEnabled counts as ENABLED (state.py: ENABLED_STATES includes it)
but not as ACTIVE. preEnableStandstill fires on `brakePressed and standstill`, and standstill only
started reading true on this car on 2026-08-05 -- so openpilot can now sit in preEnabled at a stop,
reporting enabled=True with active=False, for as long as the brake is held. Panda has no reason to
allow controls there, and two seconds of that is the mismatch.

  enabled=True, active=False  -> preEnabled. Ours, from the standstill change. No upstream bug.
  enabled=True, active=True   -> genuinely engaged while panda disallows. Upstream.

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
    "altExperience": None, "events": "", "cruiseAvail": None, "latActive": None,
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
        # available is (3,4,5); enabled is (4,5). A dip to standby shows here as avail=True with
        # cruiseEn=False -- the signature of a momentary dropout, and the thing a rising edge needs.
        state["cruiseAvail"] = cs.cruiseState.available
        # Per-frame, not sticky. Carrying the last-seen value forward made a single SET- release
        # look like a button held down for the whole window on the first run of this.
        state["buttons"] = ",".join(f"{b.type}{'+' if b.pressed else '-'}"
                                    for b in cs.buttonEvents) if cs.buttonEvents else ""
      elif w == "carControl":
        cc = msg.carControl
        state["ccEnabled"] = cc.enabled
        state["ccResume"] = cc.cruiseControl.resume
        state["latActive"] = cc.latActive
      elif w == "selfdriveState":
        ss = msg.selfdriveState
        state["sdEnabled"] = ss.enabled
        state["sdActive"] = ss.active
        state["alert"] = ss.alertText1
      elif w == "onroadEvents":
        state["events"] = ",".join(str(e.name) for e in msg.onroadEvents)
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
        # EngBrakeData -- which carries CcStat_D_Actl, the signal BOTH openpilot and panda derive
        # cruise-engaged from -- is a 10 Hz message. So a single-frame dropout lasts exactly 100 ms
        # and decimating carState 10:1 makes it invisible while still being visible to both
        # consumers. That is very likely why the first pass showed cruiseEn=True throughout and no
        # edge to explain the enable. Full rate near the transition, decimated further out.
        fine_lo = (t - t0) - min(args.window, 1.0)
        prev_sd = None
        shown = 0
        for ts, kind, snap in history:
          if ts < lo or kind != "carState":
            continue
          shown += 1
          edge = snap["sdEnabled"] != prev_sd
          prev_sd = snap["sdEnabled"]
          if not edge and ts < fine_lo and shown % 10:
            continue
          if not edge and ts >= fine_lo and shown % 2:
            continue
          print(f"  t+{ts:7.2f} v={snap['vEgo']:>5} standstill={snap['standstill']!s:<5} "
                f"cruiseEn={snap['cruiseEnabled']!s:<5} cruiseAvail={snap['cruiseAvail']!s:<5} "
                f"| ccEn={snap['ccEnabled']!s:<5} latAct={snap['latActive']!s:<5} "
                f"| sdEn={snap['sdEnabled']!s:<5} sdActive={snap['sdActive']!s:<5} "
                f"| cA={snap['controlsAllowed']!s:<5} rxInvalid={snap['rxChecksInvalid']!s:<5} "
                f"| btn={snap['buttons']:<16} ev={snap['events']}")
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
