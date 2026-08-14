#!/usr/bin/env python3
"""One command for an owner to run and paste back. NO ARGUMENTS, READ-ONLY.

Written 2026-08-13 because diagnosing a second owner's car was going through two people relaying
prose -- "it said something about data speed", "the error log is blank" -- and a day was spent on
three wrong mechanisms as a result. This prints the facts that were repeatedly guessed at instead.

Deliberately argument-free and self-locating: it finds the newest route itself. Anything that needs
a route id, a timestamp or a second step gets typed wrong, or not run at all.

    cd /data/openpilot && python tools/bp_help_report.py

Everything here is read-only. It does not change a setting, write a param, or restart a process.
"""
from __future__ import annotations

import os
import subprocess
import sys
from collections import Counter

REALDATA = "/data/media/0/realdata"
CRASH_DIRS = ("/data/community/crashes", "/data/crashes")
LOG_DIR = "/data/log"
MPH = 2.23694


def sh(cmd: str) -> str:
  try:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30).stdout.strip()
  except Exception as e:  # noqa: BLE001
    return f"(failed: {e})"


def head(title: str) -> None:
  print(f"\n===== {title} =====")


def main() -> int:
  print("FusionPilot help report")

  head("BUILD")
  print(sh("cd /data/openpilot && git log --oneline -1"))
  print("branch:", sh("cd /data/openpilot && git rev-parse --abbrev-ref HEAD"))
  print("dirty files:", sh("cd /data/openpilot && git status --porcelain | wc -l"))

  head("CRASHES")
  found = False
  for d in CRASH_DIRS:
    if os.path.isdir(d):
      listing = sh(f"ls -lt {d} 2>/dev/null | head -6")
      if listing:
        found = True
        print(f"{d}:\n{listing}")
        log = os.path.join(d, "error.log")
        if os.path.exists(log):
          print(f"\nlast 30 lines of {log}:")
          print(sh(f"tail -30 {log}") or "(empty)")
  if not found:
    print("no crash directory found")

  head("PROCESSES THAT KEEP RESTARTING")
  # A daemon dying and being restarted is what a comms fault usually turns out to be.
  print(sh("grep -ho \"Restarting [a-z_]*\" /data/log/* 2>/dev/null | sort | uniq -c | sort -rn | head -8")
        or "(no restarts logged)")
  print("\nsoundd stream reopens (the comma 4 fix working looks like this):")
  print(sh("grep -hc \"audio stream died\" /data/log/* 2>/dev/null | paste -sd+ | bc") or "0")

  head("RECENT ERRORS IN THE DAEMON LOG")
  print(sh("grep -ho \"\\\"msg[^,]*\\\"\" /data/log/* 2>/dev/null | grep -i \"error\\|exception\\|invalid\\|not valid\\|mismatch\" "
           "| sort | uniq -c | sort -rn | head -12") or "(none)")

  head("LATEST DRIVE")
  if not os.path.isdir(REALDATA):
    print("no route data on this device")
    return 0
  entries = [d for d in os.listdir(REALDATA) if "--" in d and d.rsplit("--", 1)[-1].isdigit()]
  if not entries:
    print("no routes recorded")
    return 0
  route = sorted(entries, key=lambda d: (d.rsplit("--", 1)[0], int(d.rsplit("--", 1)[-1])))[-1]
  route = route.rsplit("--", 1)[0]
  segs = sorted((d for d in entries if d.startswith(route + "--")),
                key=lambda d: int(d.rsplit("--", 1)[-1]))
  print(f"route {route}, {len(segs)} segments")

  try:
    from openpilot.tools.lib.logreader import LogReader
  except ImportError as e:
    print(f"cannot read the route here ({e}) -- run this from /data/openpilot")
    return 0

  events: Counter = Counter()
  hits = []
  st = {"v": 0.0, "cruise": False, "standstill": False, "allowed": None, "brake": False}
  for seg in segs:
    path = next((os.path.join(REALDATA, seg, n) for n in ("rlog", "rlog.zst", "rlog.bz2")
                 if os.path.exists(os.path.join(REALDATA, seg, n))), None)
    if path is None:
      continue
    for m in LogReader(path):
      w = m.which()
      try:
        if w == "carState":
          cs = m.carState
          st.update(v=cs.vEgo * MPH, cruise=cs.cruiseState.enabled,
                    standstill=cs.standstill, brake=cs.brakePressed)
        elif w == "pandaStates":
          for ps in m.pandaStates:
            st["allowed"] = ps.controlsAllowed
            break
        elif w == "onroadEvents":
          for e in m.onroadEvents:
            name = str(e.name)
            events[name] += 1
            if "ismatch" in name and len(hits) < 12:
              hits.append((name, dict(st)))
      except Exception:  # noqa: BLE001
        continue

  print("\nmost common events:")
  for name, n in events.most_common(12):
    print(f"  {n:6d}  {name}")

  print("\nEVERY MISMATCH, with the state when it fired:")
  if not hits:
    print("  none on this drive")
  for name, s in hits:
    print(f"  {name:22s} {s['v']:5.1f} mph  cruise={s['cruise']!s:5s} "
          f"standstill={s['standstill']!s:5s} brake={s['brake']!s:5s} pandaAllowed={s['allowed']}")

  print("\n--- paste everything above ---")
  return 0


if __name__ == "__main__":
  sys.exit(main())
