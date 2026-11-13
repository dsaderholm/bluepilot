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
from collections import Counter, deque

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

# Mirrored from passing_assist.HOLD_THROUGH. These three were measured OSCILLATING on 2026-08-09,
# so _hold_suggestion rides them out and a sequence must not die on one; seeing one here means the
# hold is not long enough. Everything else -- noLead, driverActive, tooSlow -- means no pass is
# warranted AT ALL, and those are required to clear the side in one frame without waiting out
# WANTED_FALL_S (test_no_pass_warranted_clears_it_immediately states the property).
HOLD_THROUGH = ("noLaneAvailable", "adjacentSlow", "nothingSlower")


# _debounce_wanted needs WANTED_FALL_S of continuous disagreement to drop the side, so the gate
# that actually ended a sequence is the one that held for that long -- NOT whichever gate happened
# to be refusing on the single abort frame. Mirrored from passing_assist.WANTED_FALL_S / DT_MDL.
FALL_FRAMES = 15


def _sustained_held(causes) -> int:
  """How many of the last FALL_FRAMES the sustained cause occupied.

  THE THIRD CATEGORY, and without it the verdict is wrong on every real event. A HOLD_THROUGH gate
  that flickers for a frame is the cc9b910b0a defect; the SAME gate refusing for the whole window
  is _debounce_wanted working exactly as designed, because WANTED_FALL_S of continuous refusal is
  precisely what it is built to wait for. Measured 2026-09-25: all twelve post-fix aborts are
  HOLD_THROUGH causes, and labelling all twelve a defect would point the next session at raising
  WANTED_FALL_S -- the permissive direction, on evidence that says the gates were right.
  """
  ranked = Counter(c for c in causes if c != "none").most_common(1)
  return ranked[0][1] if ranked else 0


def _sustained(causes) -> str:
  """The gate that ran the debounce down, as opposed to the one refusing at the instant it expired.

  MEASURED, route 0000049f seg 5 t+33.35. The abort frame reads `noLaneAvailable` because
  leftEdgeStd ticked 1.19 -> 1.31 for two frames. What actually ended it was `adjacentSlow`, which
  had held for the fifteen frames before -- exactly WANTED_FALL_S -- while width, beyond and paint
  sat rock steady. Reading the abort frame alone names a coincidence and sends the work at the
  wrong gate; it is also why a 2 Hz qlog scan and a 20 Hz rlog scan disagreed about the same event.
  """
  ranked = Counter(c for c in causes if c != "none").most_common(1)
  return ranked[0][0] if ranked else "none"


def _verdict(reason: str, held: int) -> str:
  """Is this back-out a defect, or the design working?

  WITHOUT THIS THE TWO READ IDENTICALLY, and on 2026-09-25 that cost a wrong report: three noLead
  aborts at 76-79 mph were about to be written up as the radar-dropout fix having failed. They are
  the LEAD_GAP_GRACE_S window genuinely expiring on a lead that had been gone 0.4 s -- the hard
  clear that branch is supposed to do. A one-frame clear is only a defect when the cause is one the
  hold owns.
  """
  if reason in HOLD_THROUGH:
    if held >= FALL_FRAMES:
      return (f"by design -- {reason} refused for the whole WANTED_FALL_S window, "
              "which is what the debounce waits for")
    return (f"DEFECT -- {reason} is on HOLD_THROUGH and only held {held}/{FALL_FRAMES} frames, "
            "so the hold let go of a gate it owns")
  if reason == "none":
    return "unattributed -- no cause recorded on either frame"
  return f"by design -- {reason} means no pass is warranted at all"


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("route", nargs="+")
  ap.add_argument("--segments", type=int, default=0)
  args = ap.parse_args()

  from openpilot.tools.lib.logreader import LogReader

  from cereal import custom
  f = set(custom.LongitudinalPlanSP.PassingAssist.schema.fieldnames)
  for name in ("maneuver", "maneuverSeconds", "maneuverSide", "blockedBy", "maneuverAborts",
               "suggestion", "reason", "wantedSide", "blockedByDecided", "rawWantedSide"):
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
  # Deliberately NOT reset per segment: segments are contiguous 60 s slices of one drive, so an
  # abort in the first frames of a segment was run down by frames at the end of the previous one.
  # Clearing here would blind the window on exactly the events that straddle a boundary.
  recent: deque[str] = deque(maxlen=FALL_FRAMES)
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
          # THE CAUSE. `blockedBy` is rewritten to `none` by _hold_suggestion on any frame a
          # HOLD_THROUGH gate is being held through, so on exactly the frames this tool cares
          # about it names nothing -- a cause that erases its own log entry. blockedByDecided is
          # captured BEFORE the hold and was added 2026-09-15 for this; the tool kept reading
          # blockedBy for ten days after it existed, which is a diagnostic outliving the thing it
          # measures. Reads `none` on routes older than that field, hence the fallback below.
          "decided": str(pa.blockedByDecided),
          # THE GEOMETRY'S OWN ANSWER -- and it is NOT usable on every frame. `raw_wanted_side` is
          # cleared immediately before _decide (passing_assist ~line 2935), so ANY early return in
          # _decide leaves it `none` without the geometry ever having been asked. So `raw none`
          # means "geometry said no" OR "the frame returned early", and those are indistinguishable
          # from this field alone. Read it WITH `decided`: a no-pass-warranted cause means the
          # early-return reading is the likely one.
          "raw": str(pa.rawWantedSide),
          # The DETECTOR's own output, which is what `wanted` is built from. blockedBy `none`
          # means a suggestion IS being made, so on a back-out it cannot name the cause -- but the
          # suggestion's SIDE can, because passing_maneuver leaves `signaling` on
          # `wanted != self.side` just as readily as on `wanted == none`. Those are different
          # defects: the first is one gate flipping the answer over, the second is the reason
          # evaporating. Without this column they are indistinguishable and both print as "none".
          "sugg": str(pa.suggestion),
          # THE DECIDING TERM. passing_maneuver leaves `signaling` on `wanted == none or
          # wanted != side`, and `wanted` is wantedSide -- NOT `suggestion`, which additionally
          # requires every safety gate and a completed confirmation. Published 2026-08-26 for
          # exactly this; on routes recorded before that it reads `none` on every frame, which
          # would look like "the reason evaporated" on every event. Hence the guard below.
          "want": str(pa.wantedSide),
          "why": str(pa.reason),
          "deficit": float(pa.speedDeficit) * 2.23694,
          "d_rel": float(pa.leadDRel),
          "lead": bool(pa.hasLead),
          "route": route,
        }
      except Exception:  # noqa: BLE001
        continue
      phases_seen[phase] += 1
      recent.append(cur["decided"])
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
          # blockedByDecided where it exists, blockedBy only as the pre-2026-09-15 fallback. Taking
          # blockedBy first is what made three of four events read "none" before that field landed.
          src = prev if window else cur
          instant = src["decided"] if src["decided"] != "none" else src["blocked"]
          # The gate that held for WANTED_FALL_S, which is what actually released the side.
          held = _sustained(recent)
          reason = held if held != "none" else instant
          # A route older than the wantedSide field reads `none` forever, which is
          # indistinguishable from the real "it evaporated" answer. Say so rather than print it.
          stale = prev["want"] == "none" and cur["want"] == "none"
          flipped = cur["want"] not in ("none", prev["side"])
          dropped = cur["want"] == "none"
          events.append({**prev, "went_to": phase, "after": cur["blocked"],
                         "verdict": _verdict(reason, _sustained_held(recent)),
                         "instant": instant,
                         "path": "window expired" if window
                                 else "wantedSide NOT LOGGED" if stale
                                 else f"wanted FLIPPED to {cur['want']}" if flipped
                                 else "wanted went to none" if dropped
                                 else "gate refused",
                         "reason": ("re-run on a route recorded after 2026-08-26" if stale
                                    else f"{cur['why']} on the {cur['want']}" if flipped
                                    else reason)})
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
  print(f"  {'to':<10} {'side':<6} {'held':>6}  {'why it left':<20} {'refused by':<22} {'lead'}")
  for e in events:
    lead = (f"{e['deficit']:.0f} mph slower at {e['d_rel']:.0f} m" if e["lead"] else "LEAD GONE")
    print(f"  {e['went_to']:<10} {e['side']:<6} {e['secs']:5.1f}s  {e['path']:<20} "
          f"{e['reason']:<22} {lead}")
    # RENDERED, not merely computed. A verdict held in the dict and never printed is this fork's
    # oldest bug, and the whole point of the column is that a reader cannot tell a flickering gate
    # from the grace expiring by looking at the reason alone.
    print(f"  {'':<10} {'':<6} {'':>6}  -> {e['verdict']}")
    # Only when they disagree, and then loudly: a sustained cause that differs from the abort
    # frame means the obvious reading of that frame is a coincidence. Printing it every time would
    # be noise; hiding it entirely is how the wrong gate gets worked on.
    if e["instant"] != e["reason"]:
      print(f"  {'':<10} {'':<6} {'':>6}     (the abort FRAME said {e['instant']} -- "
            f"a coincidence; {e['reason']} is what held for WANTED_FALL_S)")
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
