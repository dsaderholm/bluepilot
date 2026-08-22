#!/usr/bin/env python3
"""FusionPilot: the numbers passing assist has been logging that nothing has ever read.

WHY THIS EXISTS. An audit on 2026-08-21 compared every top-level field of `PassingAssist` against
every consumer -- the two renderers, the car-side readers, the desire path and every tool -- and
found 25 fields whose only three references in the whole repo are a comment, a comment, and the
line that publishes them. They are computed correctly, tested, written into every route, and
consulted by nobody.

That is this fork's oldest bug one level out. The recorded version is "a value computed and never
rendered"; this is "a value recorded and never READ", and it is worse in one specific way: the
unrendered value at least stops being written when someone deletes it, while these accumulate in
every drive looking like evidence nobody has bothered to want.

THE ONE THAT BLOCKS A DESIGN CONSTANT. CLAUDE.md, on the drive summary:

    ACC braked by -> the furthest back Ford's ACC ever lost patience. The close-in hold has to
    stay clear of this, and until it is measured that hold has no safe value.

`accBrakingOnsetDRel` IS that measurement. It has been logging since it was written and this is
the first thing to read it.

  python tools/bp_passing_unread.py <route-prefix>          # on the device
  python tools/bp_passing_unread.py <route-prefix> --segments 6

DENOMINATOR DISCIPLINE, because this file's own notes record getting it wrong three times: every
rate below states what it is a rate OF, and the decision-time fields are counted over DECISIONS
rather than over frames. A field sampled on 90,000 frames and meaningful on 7 of them is a
property of those 7.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

REALDATA = "/data/media/0/realdata"


def pct(n, d):
  return f"{100.0 * n / d:5.1f}%" if d else "    --"


def quantiles(vals):
  if not vals:
    return None
  v = sorted(vals)
  def q(f):
    return v[min(len(v) - 1, int(f * len(v)))]
  return v[0], q(0.5), q(0.9), v[-1]


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("route")
  ap.add_argument("--segments", type=int, default=0, help="0 = all")
  args = ap.parse_args()

  from openpilot.tools.lib.logreader import LogReader

  segs = sorted((d for d in os.listdir(REALDATA) if d.startswith(args.route)),
                key=lambda d: int(d.rsplit("--", 1)[-1]) if d.rsplit("--", 1)[-1].isdigit() else -1)
  if args.segments:
    segs = segs[:args.segments]
  if not segs:
    sys.exit(f"no segments matching {args.route}")

  frames = 0
  acc_avail = 0
  onset = []                       # accBrakingOnsetDRel, non-zero only
  clear_share = []
  onc_seen = onc_remem = 0.0
  lead_ttc = []
  approach = []
  lead_accel = []
  suspended = 0.0
  crawl = Counter()
  triggers = Counter()
  decisions = 0
  dec_acc_braking = dec_acc_precharge = 0
  driver_change = Counter()
  steering_active = 0
  # THE TWO FACTS A DISPUTED ONCOMING REFUSAL COMES DOWN TO. custom.capnp says they are "logged
  # because they are what a disputed decision comes down to and neither is visible from the road"
  # -- and an audit on 2026-08-21 found nothing in the tree had ever read either one. So the fields
  # recorded to settle the I-15 false fire have been sitting unread since they were added.
  adj = {"left": Counter(), "right": Counter()}
  overtaken = {"left": [], "right": []}

  prev_sug = 0
  prev_dc = False
  for seg in segs:
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
      except Exception:  # noqa: BLE001
        continue
      frames += 1
      acc_avail += bool(pa.accBrakingAvailable)
      steering_active += bool(pa.steeringWouldBeActive)
      suspended = max(suspended, float(pa.suspendedSeconds))
      onc_seen = max(onc_seen, float(pa.oncomingSeenSeconds))
      onc_remem = max(onc_remem, float(pa.oncomingRememberedSeconds))
      if float(pa.clearShare) > 0:
        clear_share.append(float(pa.clearShare))
      if float(pa.accBrakingOnsetDRel) > 0:
        onset.append(float(pa.accBrakingOnsetDRel))
      if float(pa.leadTtc) > 0:
        lead_ttc.append(float(pa.leadTtc))
      if float(pa.approachSeconds) > 0:
        approach.append(float(pa.approachSeconds))
      if abs(float(pa.leadAccel)) > 0.01:
        lead_accel.append(float(pa.leadAccel))
      for side_name, a in (("left", pa.adjacentLeft), ("right", pa.adjacentRight)):
        if not bool(a.available):
          adj[side_name]["unavailable"] += 1
          continue
        adj[side_name]["available"] += 1
        # ADJACENT means the lane RIGHT NEXT to us is theirs, and no setting overrides it. A
        # sighting further out only says the ROAD is two-way, which sameDirectionRecent then has
        # to disambiguate -- a center turn lane and an ordinary passing lane are geometrically
        # identical.
        if bool(a.oncomingAdjacent):
          adj[side_name]["oncomingADJACENT"] += 1
        if bool(a.sameDirectionRecent):
          adj[side_name]["sameDirectionRecent"] += 1
        if float(a.overtakenVAbs) > 0:
          overtaken[side_name].append(float(a.overtakenVAbs))
      cs = str(pa.crawlSide)
      if cs != "none":
        crawl[cs] += 1
      # A DECISION is the rising edge of a suggestion, not a frame with one -- a suggestion held
      # for 200 frames is one decision, and counting frames would weight long refusals as many.
      sug = getattr(pa.suggestion, "raw", pa.suggestion)
      if sug and not prev_sug:
        decisions += 1
        dec_acc_braking += bool(pa.accBrakingAtDecision)
        dec_acc_precharge += bool(pa.accPrechargeAtDecision)
        triggers[str(pa.trigger)] += 1
      prev_sug = sug
      # A RISING EDGE, like the decisions above. driverChangeStandDown is a LEVEL held for the
      # whole stand-down, so counting frames reports one lane change as two thousand -- which is
      # exactly the denominator mistake this file's docstring warns about, made in this file.
      cur_dc = bool(pa.driverChangeStandDown)
      if cur_dc and not prev_dc:
        driver_change["exit" if bool(pa.driverChangeWasExit) else "lane change"] += 1
      prev_dc = cur_dc

  print(f"route {args.route}   {len(segs)} segments   {frames} passingAssist frames\n")

  print("THE ONE THAT BLOCKS A CONSTANT -- how far back Ford's ACC lost patience")
  q = quantiles(onset)
  if q:
    print(f"  accBrakingOnsetDRel   n={len(onset)}   min {q[0]:.0f} m   p50 {q[1]:.0f} m   "
          f"p90 {q[2]:.0f} m   MAX {q[3]:.0f} m")
    print(f"  The close-in hold must stay clear of the MAX ({q[3]:.0f} m): inside that distance ACC")
    print("  is already braking, so holding off to 'let ACC deal with it' is holding off forever.")
  else:
    print("  accBrakingOnsetDRel   never non-zero on this drive -- ACC never braked for a lead")
    print("  while passing assist was watching, so this drive says nothing about the margin.")
  print(f"  accBrakingAvailable   {pct(acc_avail, frames)} of frames (the camera was reporting at all)")
  print()

  print(f"DECISIONS: {decisions}")
  if decisions:
    print(f"  ACC was already braking when we decided   {dec_acc_braking:4d}  {pct(dec_acc_braking, decisions)}")
    print(f"  ACC was merely pre-charging               {dec_acc_precharge:4d}  {pct(dec_acc_precharge, decisions)}")
    print("  Pre-charge is NOT braking -- no deceleration, no lamps, no pad wear -- so those count")
    print("  as beating ACC to the decision rather than reacting to it.")
    for t, n in triggers.most_common():
      print(f"    trigger {t:<14} {n:4d}")
  print()

  print("THE TWO FACTS A DISPUTED ONCOMING REFUSAL TURNS ON -- first read, 2026-08-21")
  for side_name in ("left", "right"):
    c = adj[side_name]
    live = c.get("available", 0)
    if not live:
      print(f"  {side_name:5s}  never available")
      continue
    ov = overtaken[side_name]
    ovs = (f"   overtaken n={len(ov)} p50 {sorted(ov)[len(ov)//2] * 2.23694:.0f} mph"
           if ov else "   overtaken never")
    print(f"  {side_name:5s}  available {live:6d}   oncomingADJACENT {c.get('oncomingADJACENT', 0):5d} "
          f"({pct(c.get('oncomingADJACENT', 0), live)})   sameDirectionRecent "
          f"{c.get('sameDirectionRecent', 0):5d} ({pct(c.get('sameDirectionRecent', 0), live)})")
    print(f"         {ovs}")
  print("  oncomingADJACENT is the one no setting overrides -- opposing traffic in the lane RIGHT")
  print("  NEXT to us. A sighting further out only says the road is two-way, and sameDirectionRecent")
  print("  is what then separates a center turn lane from an ordinary passing lane.")
  print()

  print("THE ONCOMING VETO, live sighting vs memory")
  tot = onc_seen + onc_remem
  if tot > 0:
    print(f"  seen {onc_seen:7.1f} s ({pct(onc_seen, tot)})   remembered {onc_remem:7.1f} s ({pct(onc_remem, tot)})")
    print("  Mostly-remembered means the 90 s window is doing the work, which is what a false fire")
    print("  on a divided highway looks like from the inside.")
  else:
    print("  never fired on this drive")
  print()

  print("THE REST, all first reads")
  for label, vals, unit in (("leadTtc", lead_ttc, "s"), ("approachSeconds", approach, "s"),
                            ("leadAccel", lead_accel, "m/s2"), ("clearShare", clear_share, "")):
    q = quantiles(vals)
    print(f"  {label:18s} " + (f"n={len(vals):6d}  min {q[0]:7.2f}  p50 {q[1]:7.2f}  "
                               f"p90 {q[2]:7.2f}  max {q[3]:7.2f} {unit}" if q else "never set"))
  print(f"  {'suspendedSeconds':18s} max {suspended:.1f} s")
  print(f"  {'steeringWouldBeActive':18s} {pct(steering_active, frames)} of frames")
  print(f"  {'crawl':18s} " + (", ".join(f"{k}={v}" for k, v in crawl.items()) or "never"))
  print(f"  {'driverChange':18s} " + (", ".join(f"{k}={v}" for k, v in driver_change.items()) or "never") + "   (events, not frames)")
  return 0


if __name__ == "__main__":
  sys.exit(main())
