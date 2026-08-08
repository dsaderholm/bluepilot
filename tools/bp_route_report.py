#!/usr/bin/env python3
"""
BluePilot: read a drive's route log and answer the questions the panel would have.

    python tools/bp_route_report.py latest        # the drive you just finished
    python tools/bp_route_report.py <route-name-or-segment-dir-or-rlog>

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

# The gate's own thresholds, mirrored from passing_assist. Kept here so every term can be evaluated
# INDEPENDENTLY from the per-frame values the planner already publishes.
#
# _record_refusal on the device takes the FIRST failing term in a fixed order, and edge-std is
# checked first -- so a drive can only ever reveal one layer, and fixing it reveals the next. Three
# drives in, edge-std has been 100%, 98% and 98% of refusals and the other three terms have never
# been seen at all. Every input is in the log, so there is no reason to spend a drive per term.
MIN_ADJACENT_LINE_PROB = 0.5
MIN_LANE_WIDTH_M, MAX_LANE_WIDTH_M = 3.0, 5.0
MIN_EDGE_BEYOND_LINE_M = 0.8
MAX_ROAD_EDGE_STD = 1.2


def _fmt_s(seconds: float) -> str:
  m, s = divmod(int(seconds), 60)
  return f"{m}m{s:02d}s" if m else f"{s}s"


REALDATA = "/data/media/0/realdata"


def _segment_key(p: str) -> int:
  """Sort by segment NUMBER, not by name: --2 has to come before --10, and lexically it does not."""
  tail = os.path.basename(os.path.dirname(p)).rsplit("--", 1)[-1]
  return int(tail) if tail.isdigit() else 0


def newest_route() -> str | None:
  """The most recently written route on the device, by segment 0's mtime.

  Because "which one was that drive" is a question after every single drive, and answering it by
  reading an `ls` listing is a step that buys nothing -- the answer is almost always the last one.
  """
  segs = glob.glob(os.path.join(REALDATA, "*--0"))
  if not segs:
    return None
  newest = max(segs, key=os.path.getmtime)
  return os.path.basename(newest).rsplit("--", 1)[0]


def resolve(target: str) -> list[str] | str:
  """Turn what a person would actually type into something LogReader accepts.

  LogReader wants a SegmentRange -- a dongle-prefixed route name -- or explicit paths, and refuses
  a bare route name with "Segment range is not valid". On the device the route name is exactly what
  is on screen and in `ls`, so expand it here rather than making him type a path per segment.

  Returns a list of rlog paths when it can expand, or the original string to pass straight through.
  """
  # A directory: one segment.
  if target == "latest":
    found = newest_route()
    if found is None:
      return target
    target = found
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


def stream(target: str):
  """Yield every message of a route, ONE SEGMENT AT A TIME.

  Handing LogReader the whole segment list gets the process OOM-killed on the device -- bare
  "Killed", no traceback -- because a 35 minute route is far more than a comma 3X holds at once.
  Scoping the reader to a single segment keeps memory flat however long the drive was, and the
  caller's running state spans them regardless since this is one continuous sequence.
  """
  from openpilot.tools.lib.logreader import LogReader

  targets = resolve(target)
  if isinstance(targets, str):
    targets = [targets]

  for i, seg in enumerate(targets):
    if len(targets) > 1:
      print(f"  segment {i + 1}/{len(targets)}", end="\r", file=sys.stderr, flush=True)
    yield from LogReader(seg)

  if len(targets) > 1:
    print(" " * 30, end="\r", file=sys.stderr)


def resolved_name(target: str) -> str:
  """What was ACTUALLY read, for the report's own header.

  Printing back the word the user typed is worse than useless: `route: latest` told him nothing,
  and when `latest` picked a stale route the identical numbers were the only clue -- which he had
  to notice himself, against a report that looked perfectly well-formed.
  """
  paths = resolve(target)
  if isinstance(paths, list) and paths:
    return f"{os.path.basename(os.path.dirname(paths[0])).rsplit('--', 1)[0]}  ({len(paths)} segments)"
  return str(target)


def read(path: str) -> dict:
  """Walk the route once, accumulating everything. One pass -- these files are large."""

  out = {
    "pa_frames": 0, "engaged_s": 0.0, "suggested": Counter(), "blocked": Counter(),
    "geo": None, "maneuvers": Counter(), "duration_s": 0.0,
    "lane_changes": 0, "opposite_switch": 0, "signal_out": 0, "signal_out_steering": 0,
    # ...and the same question asked the OTHER way. See report(): the latched form said 22 of 23,
    # which would suppress the cancel on nearly every change.
    "signal_out_steering_at_drop": 0,
    "overtaken": 0, "route": resolved_name(path), "params": {},
    # Each term evaluated on its own, so one drive names every blocker instead of the first.
    "geo_each": [0, 0, 0, 0], "geo_frames": 0,
    # The panel's own crash. See hud_renderer_bp: one exception latches it off for the whole drive,
    # so this is the only record of WHY once the screen has gone quiet.
    "panel_error": None,
    # Every frame it actually suggested, with the geometry that let it through. Six frames on
    # 00000329 and one of them put him at a two-way center turn lane, so "what did the gate see"
    # is the whole question and a count cannot answer it.
    "suggestions": [],
    # Anything cloudlog said about the panel, whether or not it matched the latch message.
    "ui_log": [],
  }

  # Lane-change reconstruction. carState is 100 Hz, so the switch position is sampled far finer
  # than the counters in auto_lane_change can manage -- a nudge that shows for one gateway frame
  # is visible here even if the state machine never happened to look on that frame.
  prev_dir = 0            # -1 left, +1 right, 0 none: the signal the driver had on
  in_change = False
  pressed_now = False
  saw_opposite = False
  saw_pressed = False
  t0 = t1 = None

  for msg in stream(path):
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
        pressed_now = cs.steeringPressed
        if d == 0:
          out["lane_changes"] += 1
          if saw_opposite:
            out["opposite_switch"] += 1
          else:
            out["signal_out"] += 1
            if saw_pressed:
              out["signal_out_steering"] += 1
            if pressed_now:
              out["signal_out_steering_at_drop"] += 1
          in_change = False

    elif which in ("logMessage", "errorLogMessage") and out["panel_error"] is None:
      # cloudlog lands here as text. Only the panel's own latch is wanted -- the drive is full of
      # unrelated logging and dumping all of it would bury the one line that matters.
      try:
        text = str(msg.logMessage if which == "logMessage" else msg.errorLogMessage)
        if "passing assist panel failed" in text:
          out["panel_error"] = text[:4000]
        # Wider net, kept separate. The latch line did not appear in a route where the screen
        # definitely showed the error, so the filter itself is a suspect and this says what
        # cloudlog DID carry rather than only whether one exact phrase was present.
        # The MESSAGE, never the context. Every cloudlog line carries ctx with the branch name in
        # it -- "passing-assist-phase1" -- so matching the whole blob returned camerad and loggerd
        # noise on every drive and buried the thing being looked for.
        elif len(out["ui_log"]) < 20:
          body = text.split('"msg"', 1)[-1] if '"msg"' in text else ""
          if any(w in body.lower() for w in ("passing assist", "hudrenderer", "panel")):
            out["ui_log"].append(body[:300])
      except Exception:  # noqa: BLE001 - a malformed log line is not the report's problem
        pass

    elif which == "initData":
      # THE PARAMS AS THEY WERE AT BOOT. "I had all passing assist options on... there was
      # absolutely nothing on the screen" is answerable from the route rather than by asking him to
      # go read a settings screen and report it back -- and a setting he believes is on is exactly
      # the thing worth checking against the record rather than against memory.
      # .entries, NOT dict access. initData.params is a capnp Map, so `k in params` and `params[k]`
      # both raise -- and the bare except below swallowed it, so the whole section silently printed
      # nothing and looked like "this route has no params" rather than "the reader is wrong".
      want = ("ShowPassingAssist", "PassingAssistEnabled", "ShowPassingInCluster",
              "ShowAdjacentLanes", "ShowOncomingSpeeds", "PassingAssistMinSpeed")
      try:
        for entry in msg.initData.params.entries:
          if entry.key in want:
            out["params"][entry.key] = bytes(entry.value).decode(errors="replace").strip()
      except Exception as e:  # noqa: BLE001 - an older route without params is not a failure
        out["params_error"] = f"{type(e).__name__}: {e}"

    elif which == "longitudinalPlanSP":
      pa = msg.longitudinalPlanSP.passingAssist
      out["pa_frames"] += 1
      out["suggested"][str(pa.suggestion)] += 1
      out["blocked"][str(pa.blockedBy)] += 1
      out["maneuvers"][str(pa.maneuver)] += 1
      # Last wins: these are cumulative over the drive, not per-frame.
      out["geo"] = (int(pa.geoRefusedBy), float(pa.geoRefusedValue),
                    float(pa.geoRefusedShare), float(pa.geoLoosenTo))
      # INDEPENDENT per-term evaluation, counted ONLY on frames where geometry was the thing that
      # refused the pass.
      #
      # `not leftGeometryOk` was the wrong filter and produced a nonsense denominator -- 41091 of
      # 41091 frames on one route, because most of any drive has no lane to the left and the gate is
      # correctly False throughout. Percentages off that base describe the road, not the gate.
      # noLaneAvailable is the frames where everything ELSE was ready and this is what stopped it.
      if str(pa.blockedBy) == "noLaneAvailable":
        out["geo_frames"] += 1
        if float(pa.leftEdgeStd) > MAX_ROAD_EDGE_STD:
          out["geo_each"][0] += 1
        if float(pa.leftLineProb) < MIN_ADJACENT_LINE_PROB:
          out["geo_each"][1] += 1
        if not (MIN_LANE_WIDTH_M <= float(pa.leftLaneWidth) <= MAX_LANE_WIDTH_M):
          out["geo_each"][2] += 1
        if float(pa.leftEdgeBeyond) < MIN_EDGE_BEYOND_LINE_M:
          out["geo_each"][3] += 1

      side = str(pa.suggestion)
      if side != "none" and len(out["suggestions"]) < 40:
        # THE SIDE IT SUGGESTED, not always the left. The first version printed left* for every
        # row, so three right-side suggestions were reported against the left lane's geometry --
        # numbers that were real, attached to the wrong lane, and impossible to tell apart from the
        # correct ones.
        r = side == "right"
        out["suggestions"].append({
          "t": t, "side": side,
          "edge_std": float(pa.rightEdgeStd if r else pa.leftEdgeStd),
          "prob": float(pa.rightLineProb if r else pa.leftLineProb),
          "width": float(pa.rightLaneWidth if r else pa.leftLaneWidth),
          "beyond": float(pa.rightEdgeBeyond if r else pa.leftEdgeBeyond),
        })

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

  if d["panel_error"]:
    L.append("THE PANEL CRASHED, and this is why:")
    for line in d["panel_error"].splitlines():
      L.append(f"  {line}")
    L.append("  -> it latched off for the rest of the drive from this point")
    L.append("")

  if d.get("params_error"):
    L.append(f"SETTINGS: could not be read from this route -- {d['params_error']}")
    L.append("")
  if d["params"]:
    L.append("SETTINGS AS THIS DRIVE BOOTED:")
    for k, v in sorted(d["params"].items()):
      shown = {"0": "OFF", "1": "on"}.get(v, v)
      flag = "   <-- the panel draws nothing with this off" if (
        k == "ShowPassingAssist" and v == "0") else ""
      L.append(f"  {k:<24} {shown}{flag}")
    L.append("")

  if d["suggestions"]:
    L.append(f"IT SUGGESTED, and this is what the gate saw ({len(d['suggestions'])} frames):")
    L.append("    t       side    edge   paint   width   beyond")
    t0 = d["suggestions"][0]["t"]
    for g in d["suggestions"]:
      L.append(f"  {g['t'] - t0:6.1f}s  {g['side']:<6} {g['edge_std']:5.2f}  {g['prob']:5.2f}  "
               f"{g['width']:5.2f}  {g['beyond']:5.2f}")
    L.append("  a two-way center turn lane is correctly painted and correctly sized, so width and")
    L.append("  paint cannot refuse it -- the road edge is the only term that could.")
    L.append("")

  if d["ui_log"] and not d["panel_error"]:
    L.append("cloudlog mentioned the panel but not the latch message:")
    for line in d["ui_log"][:6]:
      L.append(f"  {line}")
    L.append("")

  # SAY THE DENOMINATOR. These are shares of EVERY planner frame, including the ones where no pass
  # was wanted at all. The panel's "mostly: <reason> N%" is a share of WANTED time -- see
  # top_blocked, which divides by wanted_seconds -- so the same reason legitimately carries two
  # different numbers, and unlabelled they read as a disagreement rather than as two questions.
  L.append(f"WHY IT REFUSED, most often first (share of all {total} planner frames --")
  L.append(" the panel's percentage is a share of WANTED time and will be larger):")
  for reason, n in blocked.most_common(6):
    if reason == "none":
      continue
    L.append(f"  {reason:<22} {100 * n / total:5.1f}%   ({n} frames)")
  L.append("")

  if d["geo"]:
    term, value, share, loosen = d["geo"]
    name = GEO_TERMS[term] if 0 <= term < len(GEO_TERMS) else f"term {term}"
    L.append("THE GEOMETRY GATE:")
    L.append(f"  refused by:  {name}")
    L.append(f"  it measured: {value:.2f}")
    L.append(f"  that term carried {100 * share:.0f}% of the refusals")
    # A ZERO IS A MISSING VALUE, NOT A RECOMMENDATION. The histogram behind this arrived after some
    # of the routes on the device, so an older drive reports the term and the mean correctly and
    # has nothing to compute a percentile from. Printed as a number it read "SET IT TO: 0.00",
    # which is a confident instruction to close the gate completely.
    if loosen <= 0.0:
      L.append("  SET IT TO:   -- not recorded on the build this drive ran; the term and the")
      L.append("               measurement above are still good, the recommendation is absent")
    else:
      L.append(f"  SET IT TO:   {loosen:.2f}   (admits four fifths of them)")
      # GEO_SPAN caps the histogram. Past it every refusal lands in the top bucket and the
      # percentile can only answer "the ceiling", which is a floor on the truth rather than a value.
      if value > loosen:
        L.append(f"               NOTE: the mean ({value:.2f}) is above this, so the histogram")
        L.append("               likely saturated -- treat it as a lower bound, not the answer")
    L.append("")

  if d["geo_frames"]:
    n = d["geo_frames"]
    L.append(f"EVERY TERM, EVALUATED ON ITS OWN ({n} frames where geometry was the blocker):")
    L.append("  the device only records the FIRST to fail, so this is the part that says whether")
    L.append("  fixing one reveals another or clears the way.")
    for i, term in enumerate(GEO_TERMS):
      c = d["geo_each"][i]
      note = "   <-- would still refuse" if c > n * 0.5 else ("" if c else "   never")
      L.append(f"  {term:<14} {100 * c / n:5.1f}%   ({c}){note}")
    only_edge = d["geo_each"][0] and not any(d["geo_each"][1:])
    if only_edge:
      L.append("  -> edge-std ALONE. The other three pass; loosening it opens the gate outright.")
    L.append("")

  L.append("HIS CANCEL GESTURE:")
  if not d["lane_changes"]:
    L.append("  no lane changes in this route")
  else:
    L.append(f"  {d['lane_changes']} signalled lane changes")
    L.append(f"  {d['opposite_switch']} reached the OPPOSITE switch position")
    L.append(f"  {d['signal_out']} just went out")
    L.append(f"     of those, steering AT ANY POINT in the change: {d['signal_out_steering']}")
    L.append(f"     of those, steering AT THE MOMENT it dropped:   "
             f"{d['signal_out_steering_at_drop']}")
    latched, at_drop = d["signal_out_steering"], d["signal_out_steering_at_drop"]
    if d["signal_out"] and latched > at_drop:
      L.append(f"  -> the two disagree by {latched - at_drop}. should_cancel latches torque across")
      L.append("     the whole change, so it would suppress the cancel on all of them; sampling at")
      L.append("     the drop would suppress only the smaller number.")
    if d["opposite_switch"] == 0 and d["signal_out"]:
      L.append("  -> the nudge never reaches position 2. His cancel is invisible to reversed_side,")
      L.append("     so the hands-off signal-out path is the one that has to catch it.")
      # signalOutSteering was unreachable and signal_out counted the WRONG changes before
      # 2026-08-08 -- should_cancel tested blinker_held_s == 0.0, which selects changes where the
      # signal was never on, so this counted NUDGELESS lane changes. A route recorded before that
      # fix satisfies this branch for a reason that has nothing to do with his stalk, and the
      # conclusion above does not hold for it.
      L.append("     CAVEAT: on a route recorded before 2026-08-08 this count is nudgeless lane")
      L.append("     changes, not his gesture -- re-measure before acting on it.")
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
  ap.add_argument("route", help="route name, 'latest', a segment directory, or an rlog file")
  args = ap.parse_args()

  try:
    print(report(read(args.route)))
  except Exception as e:  # noqa: BLE001 - a bad path should say so, not traceback at him
    print(f"could not read {args.route}: {type(e).__name__}: {e}", file=sys.stderr)
    return 1
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
