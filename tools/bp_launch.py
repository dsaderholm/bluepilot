#!/usr/bin/env python3
"""FusionPilot: what does the car get told when it pulls away from a stop?

His report, 2026-08-25: *"Sometimes when it resumes from stop it stays in first gear for a really
long time."* Volunteered as a SECOND transmission symptom beside the freeway one, and it is the
better test of the `AccVeh_V_Trg` finding -- at a standstill the gap between "what we told the
powertrain the car wants" and "what the car is doing" is at its widest.

BUT THERE IS A FORK THAT HAS TO BE RESOLVED BEFORE ANY OF THAT APPLIES, and this tool exists to
resolve it rather than assume it.

`AlphaLongitudinalEnabled` reads 0, written one minute before route 000003c0 -- so every recent
drive is stock Ford ACC with ICBM pressing buttons, and **openpilot authors no ACCDATA at all**.
`AccVeh_V_Trg` on those drives is FORD'S OWN. So if the first-gear pull is happening on recent
drives, the constant-145 bug cannot be its cause, and the real candidate is something else entirely:
under ICBM the SET SPEED is the only lever, it sits at Ford's 20 mph floor while stopped, and
whatever it does on release is what Ford accelerates toward.

So the two regimes predict different things and this prints both:

  OPENPILOT AUTHORING   AccVeh_V_Trg pinned at 145 kph while stopped -> the transmission is told
                        the car wants 90 mph from rest. Holding first is the obvious response.
  FORD AUTHORING        Ford's own V_Trg tracks the car. Then the suspect is the SET SPEED
                        trajectory: a fast climb after release is a hard launch by request.

WHAT IT PRINTS, per pull-away from a standstill:

    who authored ACCDATA in that window (src 0/2 = received, 128/130 = our own TX echo)
    a one-row-per-second trace of vEgo, the dash set speed, AccVeh_V_Trg and AccPrpl_A_Rq

    python tools/bp_launch.py --route 000003c3--124d7bae03
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

from openpilot.tools.bp_logtime import DriveClock

REALDATA = "/data/media/0/realdata"
MS_TO_MPH = 2.23694
ACCDATA = 390

STOPPED_MPH = 1.0        # below this the car is stopped
LAUNCH_MPH = 3.0         # crossing this upward is the launch
TRACE_S = 18.0           # how much of the pull-away to show
MIN_STOP_S = 3.0         # ignore a momentary dip; a real stop is held

ONROAD_PARAM = "/data/params/d/IsOnroad"


def is_onroad() -> bool:
  try:
    with open(ONROAD_PARAM, "rb") as f:
      return f.read(1) == b"1"
  except OSError:
    return False


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


def be(data: bytes, start: int, nbits: int) -> int:
  v = int.from_bytes(data, "big")
  idx = (start // 8) * 8 + (7 - (start % 8))
  return (v >> (len(data) * 8 - idx - nbits)) & ((1 << nbits) - 1)


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--route", default=None, action="append")
  ap.add_argument("--max-segments", type=int, default=60)
  ap.add_argument("--max-events", type=int, default=6)
  args = ap.parse_args()

  entries = sorted((d for d in os.listdir(REALDATA) if "--" in d), key=seg_index)
  routes = args.route or [entries[-1].rsplit("--", 1)[0]]
  segs = []
  for r in routes:
    segs += [d for d in entries if d.startswith(r + "--")][:args.max_segments]
  if not segs:
    sys.exit(f"no segments for {routes}")
  print(f"# {', '.join(routes)} -- {len(segs)} segments\n")

  clock = DriveClock()
  st = {"v": 0.0, "dash": 0.0, "enab": False, "vtrg": 0.0, "prpl": 0.0, "src": 0}
  src_all: Counter = Counter()
  op_long = None

  stopped_since = None
  events = []          # [t_launch, rows]
  cur = None

  for seg in segs:
    if is_onroad():
      print(f"\n!!! THE CAR STARTED DRIVING at {seg}. STOPPING -- results are PARTIAL.\n")
      break
    lr = _open(seg)
    if lr is None:
      continue
    for msg in lr:
      w = msg.which()
      ts = clock.seconds(msg.logMonoTime)
      try:
        if w == "carParams":
          op_long = bool(msg.carParams.openpilotLongitudinalControl)
          continue
        if w == "carState":
          cs = msg.carState
          st["v"] = cs.vEgo * MS_TO_MPH
          st["dash"] = cs.cruiseState.speedCluster * MS_TO_MPH
          st["enab"] = bool(cs.cruiseState.enabled)
        elif w == "can":
          for c in msg.can:
            if c.address != ACCDATA or len(c.dat) != 8:
              continue
            raw = bytes(c.dat)
            src_all[int(c.src)] += 1
            # Only the frame the CAR acts on: bus 0. src 0 is received there, 128 is our TX echo.
            if int(c.src) in (0, 128):
              st["vtrg"] = be(raw, 32, 9) * 0.5
              st["prpl"] = be(raw, 49, 10) * 0.01 - 5.0
              st["src"] = int(c.src)
          continue
        else:
          continue
      except Exception:  # noqa: BLE001
        continue

      # ---- stop / launch detection
      if st["v"] < STOPPED_MPH:
        if stopped_since is None:
          stopped_since = ts
      else:
        # CRUISE MUST BE ENGAGED. His symptom is about the car RESUMING -- a pull-away he did with
        # his own foot says nothing about what ACC asked the powertrain for, and the first version
        # of this filtered on neither, so every trace it printed was a coasting stop with the dash
        # reading 0. That is this repo's most-repeated measurement error: restrict to the frames
        # where the feature is LIVE before reading anything into them.
        if (stopped_since is not None and st["v"] >= LAUNCH_MPH and st["enab"]
            and ts - stopped_since >= MIN_STOP_S and len(events) < args.max_events):
          cur = [ts, []]
          events.append(cur)
        if st["v"] >= LAUNCH_MPH:
          stopped_since = None

      if cur is not None:
        if ts - cur[0] <= TRACE_S:
          cur[1].append((ts, dict(st)))
        else:
          cur = None

  print("=== WHO PUT ACCDATA ON BUS 0 ===")
  if not src_all:
    print("  no ACCDATA seen at all")
  for s, n in sorted(src_all.items()):
    what = {0: "bus 0, received", 2: "bus 2, the camera",
            128: "bus 0, TX echo", 130: "bus 2, TX echo",
            192: "bus 0, TX REJECTED by panda", 194: "bus 2, TX REJECTED"}.get(s, "?")
    print(f"  src {s:<4} {n:>8} frames   {what}")

  # A TX ECHO IS NOT PROOF OPENPILOT AUTHORED ANYTHING, and reading it that way produced a false
  # alarm on 2026-08-25: 35,737 src-128 frames on a route where `openpilotLongitudinalControl` was
  # FALSE. With op long off the relay is closed and PANDA ITSELF forwards the camera's ACCDATA from
  # bus 2 onto bus 0 -- a transmission, so it echoes, but not ours. The tell is that the two counts
  # are nearly equal (36,061 on bus 2 against 35,737 on bus 0), which is forwarding, not authoring.
  #
  # THE AUTHORITATIVE ANSWER IS `carParams`, not the bus. It is what card decided at car init and it
  # is in every route's own header.
  echo, cam = src_all[128], src_all[2]
  fwd = cam > 0 and abs(echo - cam) < max(0.1 * cam, 50)
  print()
  print(f"  carParams.openpilotLongitudinalControl = {op_long}")
  if not op_long:
    print("  OPENPILOT AUTHORED NOTHING HERE" + ("; the bus-0 echo is panda FORWARDING the camera."
                                                 if fwd else "."))
    print("  Every ACCDATA field below is FORD'S OWN, so the constant-145 AccVeh_V_Trg bug cannot")
    print("  be acting on this drive. If the first-gear pull happens here it is Ford's, and the")
    print("  suspect is the SET SPEED, not what we told the powertrain.")
  else:
    print("  op long was ON -- the transmission fields below are partly openpilot's.")
  print()
  print("  NOTE: AccVeh_V_Trg of ~251.5 kph (156 mph) is the TOP of the 9-bit field and is Ford's")
  print("  own idle marker, the way -5.0 is the propulsion sentinel. It is not a 156 mph target.")
  print()

  print(f"=== {len(events)} PULL-AWAY(S) FROM A STOP ===")
  for i, (t0, rows) in enumerate(events, 1):
    print(f"\n----- launch {i}, t+{t0:.0f}s -----")
    print(f"  {'t':>6} {'mph':>5} {'dash':>5} {'V_Trg':>7} {'V_Trg-v':>8} {'AccPrpl':>8}  src")
    seen = set()
    for ts, s in rows:
      k = int((ts - t0) * 2)      # 2 Hz is enough to see a launch
      if k in seen:
        continue
      seen.add(k)
      vtrg_mph = s["vtrg"] / 1.609344
      print(f"  {ts - t0:6.1f} {s['v']:5.0f} {s['dash']:5.0f} {vtrg_mph:7.0f}"
            f" {vtrg_mph - s['v']:+8.0f} {s['prpl']:8.2f}  {s['src']}")
  if not events:
    print("  none -- the car never came to a held stop and pulled away on this route")
  else:
    print()
    print("  V_Trg-v is the whole point: it is how much FASTER the powertrain was told the car")
    print("  wants to be than it actually is. Ford's own averages about +4 kph (+3 mph) in cruise.")
    print("  A large positive number from a standstill is a transmission being asked to deliver a")
    print("  speed it can only reach by holding a low gear.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
