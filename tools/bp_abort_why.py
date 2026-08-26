#!/usr/bin/env python3
"""FusionPilot: which gate backed a maneuver out after the blinker was already on?

THE NUMBER THIS ANSWERS IS THE ONE THE WHOLE DRY RUN EXISTS FOR. custom.capnp, on maneuverAborts:

    "Sequences that reached `signaling` and then backed out -- a blinker shown to traffic behind for
     a maneuver that did not happen. Near zero on a drive means the gates are stable enough to act
     on. ANYTHING ELSE NAMES AN UNSTABLE GATE THAT NO AMOUNT OF READING THE CODE WOULD HAVE FOUND."

Route 000003c1 recorded **3** where every neighbouring drive recorded 0. Nothing actuates yet, so
this costs nothing today -- but when the rear radar lands, an abort is the car putting its indicator
on, beginning to move, and changing its mind. That is the single worst thing this feature can do,
and it is the one failure a dry run can measure BEFORE it can happen.

    python tools/bp_abort_why.py <route-prefix> [<route-prefix> ...]

HOW IT FINDS THEM. `maneuver` is a phase enum. An abort is leaving `signaling` or `changing` for
anything that is not the next phase along -- so the sequence is walked and every backwards
transition is caught, rather than trusting the counter, which says how many but never which.

WHAT IT REPORTS AT THE MOMENT OF THE ABORT:

    blockedBy        the gate that refused. THE ANSWER.
    seconds held     how long the maneuver had been in that phase. A gate that refuses 0.2 s after
                     the blinker is a different defect from one that refuses after 3 s.
    side             which way it had committed
    the lead         deficit and distance, so a lead that simply vanished is distinguishable from a
                     gate that changed its mind about a lead still sitting there

A GATE THAT REFUSES AFTER SIGNALLING IS NOT NECESSARILY WRONG. Something genuinely arriving in the
target lane SHOULD stop the maneuver, and that is `rearApproaching` or `blindspotOccupied` doing its
job. What would be a defect is a gate that oscillates -- `noLaneAvailable` or `nothingSlower`
flickering -- because those say the pass was never warranted, and they had already passed once to
get this far. The three HOLD_THROUGH reasons exist precisely because those three were measured
oscillating on 2026-08-09; seeing one here means the hold is not long enough.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

REALDATA = "/data/media/0/realdata"

# Mirrored from passing_maneuver so the two abort paths can be told apart. A sequence that held this
# long left because a gate stayed unhappy; anything shorter left because the SUGGESTION dropped, and
# those want the blockedBy from different frames.
SIGNAL_WINDOW_S = 5.0

# The phases a maneuver may be in when it backs out. `changing` is COMMITTED -- per custom.capnp the
# gates can no longer call it off there, only the driver -- so an abort out of `changing` is a much
# louder finding than one out of `signaling`.
COMMITTED = "changing"
SIGNALLING = "signaling"

# Leaving one of these for anything other than the forward phase is a back-out.
FORWARD = {SIGNALLING: {COMMITTED, "aborting"}, COMMITTED: {"finishing", "aborting"}}


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("route", nargs="+")
  ap.add_argument("--segments", type=int, default=0)
  args = ap.parse_args()

  from openpilot.tools.lib.logreader import LogReader

  from cereal import custom
  f = set(custom.LongitudinalPlanSP.PassingAssist.schema.fieldnames)
  for name in ("maneuver", "maneuverSeconds", "maneuverSide", "blockedBy", "maneuverAborts"):
    if name not in f:
      sys.exit(f"passingAssist has no field {name!r} -- this tool would silently report zeros")

  all_segs = []
  for route in args.route:
    segs = sorted((d for d in os.listdir(REALDATA) if d.startswith(route)),
                  key=lambda d: int(d.rsplit("--", 1)[-1]) if d.rsplit("--", 1)[-1].isdigit() else -1)
    if args.segments:
      segs = segs[:args.segments]
    if not segs:
      print(f"  (no segments matching {route})")
      continue
    all_segs += [(route, s) for s in segs]
  if not all_segs:
    sys.exit(f"no segments matching any of {args.route}")

  prev = None
  events = []
  by_reason = Counter()
  phases_seen = Counter()

  for route, seg in all_segs:
    p = os.path.join(REALDATA, seg, "rlog")
    if not os.path.exists(p):
      p += ".zst"
    if not os.path.exists(p):
      continue
    try:
      lr = LogReader(p)
    except Exception as e:  # noqa: BLE001
      print(f"  (skipped {seg}: {e})")
      continue
    for m in lr:
      if m.which() != "longitudinalPlanSP":
        continue
      try:
        pa = m.longitudinalPlanSP.passingAssist
        phase = str(pa.maneuver)
        cur = {
          "phase": phase,
          "secs": float(pa.maneuverSeconds),
          "side": str(pa.maneuverSide),
          "blocked": str(pa.blockedBy),
          "deficit": float(pa.speedDeficit) * 2.23694,
          "d_rel": float(pa.leadDRel),
          "lead": bool(pa.hasLead),
          "route": route,
        }
      except Exception:  # noqa: BLE001
        continue
      phases_seen[phase] += 1
      if prev is not None and prev["phase"] in FORWARD and phase != prev["phase"]:
        if phase not in FORWARD[prev["phase"]]:
          # BOTH FRAMES, and the first version got this backwards. It read only the PREVIOUS
          # frame on the reasoning that the maneuver is torn down by the next one -- but blockedBy
          # is the DETECTOR's verdict, recomputed every frame whatever the maneuver is doing. While
          # signaling it reads `none` because a suggestion is live, so the before-frame can only
          # ever say "nothing was wrong", which is exactly what it said on 3 of 4 events.
          #
          # passing_maneuver has two ways out of `signaling`, and they want different frames:
          #   the window expired      a gate stayed unhappy for SIGNAL_WINDOW_S -- BEFORE is right
          #   the reason went away    `wanted` became none this frame -- AFTER is the answer
          # Held time separates them, so both are printed and the path is named.
          window = prev["secs"] >= SIGNAL_WINDOW_S - 0.2
          reason = prev["blocked"] if window else cur["blocked"]
          events.append({**prev, "went_to": phase, "after": cur["blocked"],
                         "path": "window expired" if window else "reason went away",
                         "reason": reason})
          by_reason[reason] += 1
      prev = cur

  print(f"routes {args.route}")
  print(f"  maneuver phases seen: " + ", ".join(f"{k}={v}" for k, v in phases_seen.most_common()))
  print()
  if not events:
    print("  NO BACK-OUTS. Every sequence that reached signaling either completed or was still")
    print("  running at the end of a segment. That is what a stable set of gates looks like.")
    return 0

  print(f"  {len(events)} BACK-OUT(S) -- a blinker shown for a maneuver that did not happen\n")
  print(f"  {'to':<10} {'held':>6}  {'why it left':<16} {'refused by':<20} {'lead'}")
  for e in events:
    lead = (f"{e['deficit']:.0f} mph slower at {e['d_rel']:.0f} m" if e["lead"] else "LEAD GONE")
    print(f"  {e['went_to']:<10} {e['secs']:5.1f}s  {e['path']:<16} {e['reason']:<20} {lead}")
  print()
  print("  by gate:")
  for k, v in by_reason.most_common():
    print(f"    {k:<22} {v}")
  print()
  print("  A GATE THAT REFUSES AFTER SIGNALLING IS NOT AUTOMATICALLY WRONG. Something genuinely")
  print("  arriving in the target lane SHOULD stop it -- that is rearApproaching or")
  print("  blindspotOccupied working. What is a defect is an OSCILLATING gate: noLaneAvailable,")
  print("  adjacentSlow or nothingSlower here means the pass was never warranted, and each of those")
  print("  had already passed once to get this far. Those three are exactly the ones measured")
  print("  oscillating on 2026-08-09, which is why HOLD_THROUGH exists -- seeing one means the")
  print("  hold is not long enough.")
  print()
  print("  'LEAD GONE' is the benign case: the car being passed left, so there was nothing to pass.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
