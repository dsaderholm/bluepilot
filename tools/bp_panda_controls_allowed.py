#!/usr/bin/env python3
"""Rebuild panda's controls_allowed from raw CAN, beside what panda actually reported.

WHY IT EXISTS. Resuming cruise at a near-stop force-disengages with controlsMismatch: openpilot goes
enabled, panda's controls_allowed never comes back, and two seconds of that is ET.IMMEDIATE_DISABLE.
panda's internal state is NOT logged, so the mechanism cannot be read off a route -- but every INPUT
to it is on the bus, so it can be reconstructed and compared against the reported value.

WHAT IT HAS ALREADY RULED OUT, on route 0000036b at t+542 (2026-08-13). All three were plausible,
all three are wrong, and each was believed before this existed:

  * The brake clear. generic_rx_checks drops controls_allowed on
    `brake_pressed && (!brake_pressed_prev || vehicle_moving)`. At the cruise edge the brake was
    HELD, so brake_pressed_prev is true, and every speed sample was under 0.1 m/s, so vehicle_moving
    is false. It does not fire. This was the leading theory and it is dead.
  * speed_mismatch_check. PCM and ABS speeds agree to 0.09 m/s across the whole event, against a
    2.0 m/s threshold.
  * safetyRxChecksInvalid, panda faults, wrong safety model. All clean straight through.

So modelling every path in ford.h and safety.h that touches cruise, brake or speed, panda SHOULD
have set controls_allowed TRUE on the rising edge at t+542.24 -- and the log says it stayed FALSE
for the full two seconds. **The documented logic does not explain the observed behaviour.** That is
the finding, and it is what an upstream report needs.

MECHANISM FOUND 2026-08-13, and this tool's own first answer was wrong in an instructive way.

It recomputed `vehicle_moving` inline in the 0x165 handler. The real panda does NOT: vehicle_moving
is LATCHED when 0x213 arrives, and generic_rx_checks runs after EVERY message using that latched
value. So the brake clear fires with whatever vehicle_moving was at the last 0x213, not with the
speed samples as they stand when the cruise edge lands.

The numbers, from route 0000036b:

    t+542.03   6-sample speed max 0.175 m/s   -> vehicle_moving TRUE
    t+542.24   6-sample speed max 0.097 m/s   -> vehicle_moving FALSE
    t+542.243  cruise rising edge on bus 0    <- lands ON that boundary

If the last 0x213 preceded the samples clearing, vehicle_moving is stale-TRUE when the edge fires,
generic_rx_checks clears controls_allowed on the same frame that pcm_cruise_check set it, and
nothing restores it because panda only allows on an EDGE. Two seconds later selfdrived raises
controlsMismatch, which is ET.IMMEDIATE_DISABLE.

**IT IS A RACE, WHICH IS WHY IT IS INTERMITTENT.** The owner's resume that worked was at 0.0 mph,
with the sample window long since clear; the two that failed were at 0.2 mph, right at the crossing.
Any fix has to close the race rather than move a threshold -- moving one only shifts the speed at
which the crossing happens.

Note also that 0x165 arrives at 50 Hz while panda's rx check declares it at 10 Hz. That is not the
fault (the check is a minimum, and rx_checks_invalid was false throughout) but it does mean the
cruise edge is sampled five times more often than the declared rate, which narrows the window in
which a stale vehicle_moving can catch it.

WHAT IS LEFT for whoever picks this up: `cruise_engaged_prev` is panda-internal and persists across
the whole drive, so an edge this reconstruction sees is only real if panda's view of the bus matches
ours. The next step is whether ford_rx_hook processes 0x165 at that moment at all -- bus routing
rather than logic.

DO NOT change panda's Ford safety before that is answered. The carstate comment says so, and three
confident mechanisms have already been wrong here.

USAGE, on the device:

    cd /data/openpilot && python tools/bp_panda_controls_allowed.py <route> <from_s> <to_s>
"""
from __future__ import annotations

import os
import sys
from collections import deque

REALDATA = "/data/media/0/realdata"
KPH_TO_MS = 1 / 3.6

# panda's own message addresses, from opendbc/safety/modes/ford.h
ENG_BRAKE_DATA = 0x165          # brake pedal + cruise state
DESIRED_TORQ_BRK = 0x213        # standstill status
BRAKE_SYS_FEATURES = 0x415      # ABS vehicle speed
ENG_VEHICLE_SP_THROTTLE2 = 0x202  # PCM vehicle speed, the second source

SAMPLE_VALS = 6                 # MAX_SAMPLE_VALS in opendbc/safety/declarations.h
STOPPED_MS = 0.1                # ford.h's stopped_by_speed threshold
MAX_SPEED_DELTA = 2.0           # safety.h's speed_mismatch_check threshold


def main() -> int:
  if len(sys.argv) < 4:
    sys.exit(__doc__.strip().splitlines()[-1].strip())
  route, lo, hi = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])

  from openpilot.tools.bp_logtime import DriveClock
  from openpilot.tools.lib.logreader import LogReader

  segs = sorted((d for d in os.listdir(REALDATA) if d.startswith(route + "--")),
                key=lambda d: int(d.rsplit("--", 1)[-1]))
  if not segs:
    sys.exit(f"no segments for {route}")

  clock = DriveClock()
  spd: deque = deque(maxlen=SAMPLE_VALS)
  vstop, pcm = 2, 0.0
  brake = brake_prev = cruise = cruise_prev = False
  sim_allowed = False
  logged_allowed = None
  last = -9.0

  print("  time    ABS    PCM  delta   max6  stopped moving brake cruise SIM LOG  note")
  for seg in segs:
    paths = [os.path.join(REALDATA, seg, n) for n in ("rlog", "rlog.zst", "rlog.bz2")
             if os.path.exists(os.path.join(REALDATA, seg, n))]
    if not paths:
      continue
    for msg in LogReader(paths[0]):
      w = msg.which()
      ts = clock.seconds(msg.logMonoTime)
      if w == "pandaStates":
        for ps in msg.pandaStates:
          logged_allowed = ps.controlsAllowed
          break
        continue
      if w != "can":
        continue

      note = ""
      for c in msg.can:
        if c.src != 0:
          continue
        d = c.dat
        if c.address == BRAKE_SYS_FEATURES and len(d) >= 2:
          spd.append(((d[0] << 8) | d[1]) * 0.01 * KPH_TO_MS)
        elif c.address == DESIRED_TORQ_BRK and len(d) >= 4:
          vstop = (d[3] >> 3) & 0x3
        elif c.address == ENG_VEHICLE_SP_THROTTLE2 and len(d) >= 8:
          pcm = ((d[6] << 8) | d[7]) * 0.01 * KPH_TO_MS
          if abs(pcm - (spd[-1] if spd else 0.0)) > MAX_SPEED_DELTA:
            if sim_allowed:
              note = (note + " ; " if note else "") + "SPEED MISMATCH CLEAR"
            sim_allowed = False
        elif c.address == ENG_BRAKE_DATA and len(d) >= 2:
          brake_prev, brake = brake, ((d[0] >> 4) & 0x3) == 2
          cruise_prev, cruise = cruise, (d[1] & 0x07) in (4, 5)
          # pcm_cruise_check: rising edge allows, disengage clears.
          if cruise and not cruise_prev:
            sim_allowed = True
            note = (note + " ; " if note else "") + "cruise EDGE -> allow"
          elif not cruise:
            sim_allowed = False
          # generic_rx_checks runs AFTER the mode's rx hook, on the same message.
          mx = max(spd) if spd else 0.0
          moving = (vstop != 1) and not (mx < STOPPED_MS)
          if brake and ((not brake_prev) or moving):
            if sim_allowed:
              note = (note + " ; " if note else "") + "BRAKE CLEAR"
            sim_allowed = False

      if not (lo <= ts <= hi) or (ts - last < 0.25 and not note):
        continue
      last = ts
      a = spd[-1] if spd else 0.0
      mx = max(spd) if spd else 0.0
      moving = (vstop != 1) and not (mx < STOPPED_MS)
      print("  t+%6.2f %6.3f %6.3f %6.2f %6.3f %7s %6s %5s %6s %3s %3s  %s"
            % (ts, a, pcm, abs(pcm - a), mx, mx < STOPPED_MS, moving, brake, cruise,
               sim_allowed, logged_allowed, note))

  print()
  print("  SIM is what ford.h + safety.h say controls_allowed SHOULD be. LOG is what panda")
  print("  reported. Where they disagree, the documented logic does not explain the car.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
