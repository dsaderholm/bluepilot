#!/usr/bin/env python3
"""
BluePilot: read a drive's route log and answer the questions the panel would have.

    python tools/bp_route_report.py <route-or-segment-dir-or-rlog>

WHY THIS EXISTS
---------------
2026-08-06: *"I haven't been paying attention to anything on the screen. I hope logs will tell a
lot."* They do -- and better than the screen, because everything the panel draws is published to
`longitudinalPlanSP.passingAssist` first, at 20 Hz, for the whole drive rather than whatever was on
screen at the moment he happened to look. bp_passing_report.py reads the LIVE feed and the stored
drive summary; this reads a route after the fact, which is the only one of the two that works when
nobody was watching.

WHAT IT CANNOT ANSWER, and this is the honest limit: nothing here records what his INSTRUMENT
CLUSTER drew. We transmit a CAN value and the cluster renders whatever it renders. The standstill
lane-display walk still needs his eyes, and no amount of logging replaces it.

WHAT IT ANSWERS
---------------
  * the geometry gate -- which term refuses, how often, and the number to set. This is the one that
    has been blocking every suggestion.
  * the cancel gesture -- whether his nudge back toward center ever reaches the opposite SWITCH
    position, reconstructed from carState at 100 Hz rather than from a counter. See
    OPPOSITE_SWITCH_WINDOW_S in auto_lane_change: if it never does, the nudge is invisible.
  * adjacent-lane sanity -- how much of what the radar called traffic sat beyond the road edge.
    "It kept seeing curbs as other cars."
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# The gate's own order. Mirrors GEO_EDGE_STD/PAINT/WIDTH/BEYOND in passing_assist.
GEO_TERMS = ("edge unsure", "paint", "lane width", "room past it")


def _fmt_s(seconds: float) -> str:
  m, s = divmod(int(seconds), 60)
  return f"{m}m{s:02d}s" if m else f"{s}s"


REALDATA = "/data/media/0/realdata"


def _segment_key(p: str) -> int:
  """Sort by segment NUMBER, not by name: --2 has to come before --10, and lexically it does not."""
  tail = os.path.basename(os.path.dirname(p)).rsplit("--", 1)[-1]
  return int(tail) if tail.isdigit() else 0


def resolve(target: str) -> list[str] | str:
  """Turn what a person would actually type into something LogReader accepts.

  LogReader wants a SegmentRange -- a dongle-prefixed route name -- or explicit paths, and refuses
  a bare route name with "Segment range is not valid". On the device the route name is exactly what
  is on screen and in `ls`, so expand it here rather than making him type a path per segment.

  Returns a list of rlog paths when it can expand, or the original string to pass straight through.
  """
  # A directory: one segment.
  if os.path.isdir(target):
    found = sorted(glob.glob(os.path.join(target, "rlog*")), key=_segment_key)
    return found or target
  # A bare route name, with or without the realdata prefix.
  name = os.path.basename(target.rstrip("/"))
  if re.fullmatch(r"[0-9a-f]{8}--[0-9a-f]+", name):
    found = sorted(glob.glob(os.path.join(REALDATA, name + "--*", "rlog*")), key=_segment_key)
    if found:
      return found
  return target


def read(path: str) -> dict:
  """Walk the route once, accumulating everything. One pass -- these files are large."""
  from openpilot.tools.lib.logreader import LogReader

  out = {
    "pa_frames": 0, "engaged_s": 0.0, "suggested": Counter(), "blocked": Counter(),
    "geo": None, "maneuvers": Counter(), "duration_s": 0.0,
    "lane_changes": 0, "opposite_switch": 0, "signal_out": 0, "signal_out_steering": 0,
    "overtaken": 0, "route": path,
  }

  # Lane-change reconstruction. carState is 100 Hz, so the switch position is sampled far finer
  # than the counters in auto_lane_change can manage -- a nudge that shows for one gateway frame
  # is visible here even if the state machine never happened to look on that frame.
  prev_dir = 0            # -1 left, +1 right, 0 none: the signal the driver had on
  in_change = False
  saw_opposite = False
  saw_pressed = False
  t0 = t1 = None

  for msg in LogReader(resolve(path)):
    which = msg.which()
    t = msg.logMonoTime * 1e-9
    if t0 is None:
      t0 = t
    t1 = t

    if which == "carState":
      cs = msg.carState
      d = -1 if cs.leftBlinker else (1 if cs.rightBlinker else 0)
      if d and not in_change:
        in_change, prev_dir, saw_opposite, saw_pressed = True, d, False, False
      elif in_change:
        if d and d == -prev_dir:
          saw_opposite = True
        if cs.steeringPressed:
          saw_pressed = True
        if d == 0:
          out["lane_changes"] += 1
          if saw_opposite:
            out["opposite_switch"] += 1
          else:
            out["signal_out"] += 1
            if saw_pressed:
              out["signal_out_steering"] += 1
          in_change = False

    elif which == "longitudinalPlanSP":
      pa = msg.longitudinalPlanSP.passingAssist
      out["pa_frames"] += 1
      out["suggested"][str(pa.suggestion)] += 1
      out["blocked"][str(pa.blockedBy)] += 1
      out["maneuvers"][str(pa.maneuver)] += 1
      # Last wins: these are cumulative over the drive, not per-frame.
      out["geo"] = (int(pa.geoRefusedBy), float(pa.geoRefusedValue),
                    float(pa.geoRefusedShare), float(pa.geoLoosenTo))
      out["overtaken"] = max(out["overtaken"],
                             int(pa.adjacentLeft.overtakenCount) + int(pa.adjacentRight.overtakenCount))

  if t0 is not None:
    out["duration_s"] = t1 - t0
  return out


def report(d: dict) -> str:
  L = [f"route: {d['route']}", f"length: {_fmt_s(d['duration_s'])}", ""]

  blocked = d["blocked"]
  total = sum(blocked.values()) or 1
  suggested = sum(v for k, v in d["suggested"].items() if k != "none")

  L.append(f"SUGGESTIONS: {suggested} frames "
           f"({100 * suggested / total:.1f}% of {total} planner frames)")
  if not suggested:
    L.append("  none at all -- which is the thing to explain, not a quiet drive")
  L.append("")

  L.append("WHY IT REFUSED, most often first:")
  for reason, n in blocked.most_common(6):
    if reason == "none":
      continue
    L.append(f"  {reason:<22} {100 * n / total:5.1f}%   ({n} frames)")
  L.append("")

  if d["geo"]:
    term, value, share, loosen = d["geo"]
    name = GEO_TERMS[term] if 0 <= term < len(GEO_TERMS) else f"term {term}"
    L.append("THE GEOMETRY GATE -- the number to change:")
    L.append(f"  refused by:  {name}")
    L.append(f"  it measured: {value:.2f}")
    L.append(f"  that term carried {100 * share:.0f}% of the refusals")
    L.append(f"  SET IT TO:   {loosen:.2f}   (admits four fifths of them)")
    L.append("")

  L.append("HIS CANCEL GESTURE:")
  if not d["lane_changes"]:
    L.append("  no lane changes in this route")
  else:
    L.append(f"  {d['lane_changes']} signalled lane changes")
    L.append(f"  {d['opposite_switch']} reached the OPPOSITE switch position")
    L.append(f"  {d['signal_out']} just went out ({d['signal_out_steering']} of those while steering)")
    if d["opposite_switch"] == 0 and d["signal_out"]:
      L.append("  -> the nudge never reaches position 2. His cancel is invisible to reversed_side,")
      L.append("     so the hands-off signal-out path is the one that has to catch it.")
    elif d["opposite_switch"]:
      L.append("  -> the nudge DOES reach the other side. reversed_side fires; the open question is")
      L.append("     only whether the 4 s window is long enough.")
  L.append("")

  L.append(f"ADJACENT LANE: {d['overtaken']} vehicles counted as having overtaken him")
  if d["duration_s"] > 0:
    per_min = d["overtaken"] / (d["duration_s"] / 60)
    L.append(f"  {per_min:.1f} per minute")
    if per_min > 4:
      L.append("  -> still implausible. Scenery is being counted; check the road-edge filter.")
  return "\n".join(L)


def main() -> int:
  ap = argparse.ArgumentParser(description=__doc__,
                               formatter_class=argparse.RawDescriptionHelpFormatter)
  ap.add_argument("route", help="route name, segment directory, or an rlog file")
  args = ap.parse_args()

  try:
    print(report(read(args.route)))
  except Exception as e:  # noqa: BLE001 - a bad path should say so, not traceback at him
    print(f"could not read {args.route}: {type(e).__name__}: {e}", file=sys.stderr)
    return 1
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
