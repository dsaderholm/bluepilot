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

REALDATA = "/data/media/0/realdata"
CRASH_DIRS = ("/data/community/crashes", "/data/crashes")
LOG_DIR = "/data/log"
MPH = 2.23694

# Frames closer together than this belong to the same episode.
EPISODE_GAP_S = 1.0
# Mismatch episodes closer than this are one stall, not separate faults.
CLUSTER_GAP_S = 10.0


def sh(cmd: str, timeout: int = 20) -> str:
  try:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout).stdout.strip()
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
  # BOUNDED. This scanned every file in /data/log and timed out twice on a real device, taking the
  # drive section below with it -- the one part actually worth reading. Newest three files only,
  # capped output, short timeout, and a failure here must never cost the rest of the report.
  newest = sh("ls -t /data/log/* 2>/dev/null | head -3 | tr '\n' ' '")
  if newest:
    print(sh(f"grep -hoiE 'error|exception|not valid|mismatch|lagging' {newest} 2>/dev/null "
             "| sort | uniq -c | sort -rn | head -8", timeout=10) or "(none)")
  else:
    print("(no daemon logs)")

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

  # EPISODES, NOT FRAMES. onroadEvents republishes an event on EVERY frame it stays true, so a raw
  # count says nothing about how often something happened -- 277 frames of wrongGear is a few
  # seconds sitting in Park, not 277 gear faults. That number was misread exactly that way once and
  # a whole platform theory was built on it. So group contiguous frames into episodes and report
  # how many, how long, and when.
  from openpilot.tools.bp_logtime import DriveClock

  clock = DriveClock()
  episodes: dict = {}
  hits = []
  st = {"v": 0.0, "cruise": False, "standstill": False, "allowed": None, "brake": False}
  drive_end = 0.0
  for seg in segs:
    path = next((os.path.join(REALDATA, seg, n) for n in ("rlog", "rlog.zst", "rlog.bz2")
                 if os.path.exists(os.path.join(REALDATA, seg, n))), None)
    if path is None:
      continue
    for m in LogReader(path):
      w = m.which()
      ts = clock.seconds(m.logMonoTime)
      drive_end = max(drive_end, ts)
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
            runs = episodes.setdefault(name, [])
            if runs and ts - runs[-1][1] <= EPISODE_GAP_S:
              runs[-1][1] = ts
            else:
              runs.append([ts, ts])
              if "ismatch" in name and len(hits) < 15:
                hits.append((name, ts, dict(st)))
      except Exception:  # noqa: BLE001
        continue

  print()
  print("drive length: %.0f s" % drive_end)
  print()
  print("EVENTS AS EPISODES -- times it happened, not frames it was true for:")
  print("  %-26s %7s %9s %9s %8s %8s" % ("event", "times", "total s", "longest", "first", "last"))
  for name, runs in sorted(episodes.items(), key=lambda kv: -sum(b2 - a2 for a2, b2 in kv[1]))[:14]:
    total = sum(b2 - a2 for a2, b2 in runs)
    longest = max((b2 - a2 for a2, b2 in runs), default=0.0)
    print("  %-26s %7d %9.1f %9.1f %8.0f %8.0f"
          % (name, len(runs), total, longest, runs[0][0], runs[-1][1]))

  print()
  print("EVERY MISMATCH, when it fired and the state at that moment:")
  if not hits:
    print("  none on this drive")
  for name, ts, s2 in hits:
    print("  t+%7.1f  %-20s %5.1f mph  cruise=%-5s standstill=%-5s brake=%-5s pandaAllowed=%s"
          % (ts, name, s2["v"], s2["cruise"], s2["standstill"], s2["brake"], s2["allowed"]))

  if len(hits) > 1:
    gaps = [hits[i + 1][1] - hits[i][1] for i in range(len(hits) - 1)]
    close = sum(1 for g in gaps if g <= CLUSTER_GAP_S)
    print()
    print("  %d mismatch episodes; %d of the %d gaps between them are under %.0fs"
          % (len(hits), close, len(gaps), CLUSTER_GAP_S))
    print("  Clustered means ONE stall producing several, not several independent faults.")

  print("\n--- paste everything above ---")
  return 0


if __name__ == "__main__":
  sys.exit(main())
