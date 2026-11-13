#!/usr/bin/env python3
"""FusionPilot: of the passes he ACTUALLY MAKES, how many would radar-as-lane-proof unlock?

THE NUMBER THAT DECIDES WHETHER THE LEAD IS WORTH BUILDING, and every figure before it was in the
wrong unit. `bp_radar_lane_proof.py` says the candidate rule fires on 21% of the FRAMES where the
left geometry gate refuses. That is not 21% more passes and must never be read as one: most refused
frames are not pass opportunities at all -- no lead, wrong speed, driver already acting. The
denominator error this fork has made six times is exactly this shape.

So this counts DECISIONS. One per pass he makes.

  python tools/bp_pass_decisions.py <route-prefix> [<route-prefix> ...]

WHAT A PASS IS, taken from the detector rather than invented -- see `_record_driver_pass`:

  * the RISING EDGE of his left stalk, sampled on the frame BEFORE it goes up. The detector does
    this deliberately: the driver-active gate blanks the suggestion on the very next frame, so a
    moment later there is nothing left to compare against.
  * with a LEAD ahead. The detector counts on any lead rather than one already judged slow enough,
    and says why: "a metric that flatters itself by discarding its own misses is worth nothing".
  * moving. A stalk flick in a car park is not a pass.

THE FUNNEL, and each step is a strictly smaller number than the last:

    left stalk, moving, lead ahead        every pass he made
      PA already agreed                   it wanted the same thing -- no gain available
      PA refused for some OTHER reason    oncoming, blindspot, patience -- geometry was not the
                                          blocker, so lane proof cannot help
      PA refused on GEOMETRY              the addressable set. THIS is the ceiling.
        ...and the radar rule would fire  THE ANSWER

**IF THE LAST TWO NUMBERS ARE CLOSE, the rule recovers most of what geometry is costing him. If the
last one is near zero, the 21% was frames that never mattered** -- true, and irrelevant, which is
the outcome this tool exists to be able to report.

WHY IT MAY STILL OVERSTATE, said here rather than discovered later: firing at the instant he
signalled is necessary, not sufficient. A real maneuver also needs the rest of the gates to hold
through the crossing, and needs the lane to still be clear. This is an UPPER BOUND on the decisions
recoverable, and the honest framing of any number it produces.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

REALDATA = "/data/media/0/realdata"

# Matches bp_radar_lane_proof. See its note: overtakenVAbs is LATCHED and cannot serve as a speed
# test, so recency comes from overtakenSeconds with overtakenCount disambiguating its zero.
OVERTAKE_RECENT_S = 15.0

# The enum NAME, not its ordinal. Comparing by name survives anyone inserting a member above it in
# custom.capnp, which comparing by number does not -- and capnp enums cannot be int()ed on the
# device anyway.
BLOCKED_NO_LANE = "noLaneAvailable"

# Above this, the deficit is a BAD LEAD READ rather than a slow car. speedDeficit is set speed minus
# lead speed, so a lead being held at ~0 reports the whole set speed as the deficit. Nobody passes
# something 40 mph slower on the roads this car drives without it being stopped traffic, and a
# stopped-traffic lane change is not the calibration question this number exists to answer.
MAX_PLAUSIBLE_DEFICIT_MPH = 40.0


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("route", nargs="+")
  ap.add_argument("--segments", type=int, default=0)
  args = ap.parse_args()

  from openpilot.tools.lib.logreader import LogReader

  from cereal import custom
  pa_f = set(custom.LongitudinalPlanSP.PassingAssist.schema.fieldnames)
  for f in ("leftGeometryOk", "blockedBy", "hasLead", "suggestion", "adjacentLeft"):
    if f not in pa_f:
      sys.exit(f"passingAssist has no field {f!r} -- this tool would silently report zeros")
  adj_f = set(custom.LongitudinalPlanSP.PassingAssist.AdjacentLane.schema.fieldnames)
  for f in ("available", "oncomingAdjacent", "overtakenSeconds", "overtakenCount"):
    if f not in adj_f:
      sys.exit(f"adjacentLeft has no field {f!r}")

  all_segs = []
  for route in args.route:
    segs = sorted((d for d in os.listdir(REALDATA) if d.startswith(route)),
                  key=lambda d: int(d.rsplit("--", 1)[-1]) if d.rsplit("--", 1)[-1].isdigit() else -1)
    if args.segments:
      segs = segs[:args.segments]
    if not segs:
      print(f"  (no segments matching {route})")
      continue
    all_segs += segs
  if not all_segs:
    sys.exit(f"no segments matching any of {args.route}")

  out = Counter()
  blocked_hist = Counter()
  # THE DEFICIT HE ACTUALLY PASSES AT. `nothingSlower` means the feature was running, saw the lead,
  # and judged it fast enough not to bother -- and he passed anyway. That is not a sensing failure,
  # it is a THRESHOLD, and PassingAssistMinDeficit is his to set. The number he needs is what
  # deficit his own passes happen at, which nothing has ever reported.
  deficits = []
  deficits_nothing_slower = []
  speed = 0.0
  blinker = False
  prev_blinker = False
  # The PA state as of the last frame the stalk was DOWN. See the docstring: sampling after the
  # edge reads the driver-active gate's own blanking rather than what the feature had decided.
  last = None

  for seg in all_segs:
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
      w = m.which()
      if w == "carState":
        speed = float(m.carState.vEgo)
        blinker = bool(m.carState.leftBlinker)
        if blinker and not prev_blinker and last is not None and speed > 10.0:
          # A PASS, as the detector defines one: stalk up, moving, lead ahead.
          if last["hasLead"]:
            out["passes"] += 1
            blocked_hist[last["blockedBy"]] += 1
            if last["deficit"] is not None:
              if last["deficit"] > MAX_PLAUSIBLE_DEFICIT_MPH:
                out["  ...bad lead read (deficit implausible)"] += 1
              else:
                deficits.append(last["deficit"])
                if last["blockedBy"] == "nothingSlower":
                  deficits_nothing_slower.append(
                    (last["deficit"], last["need"], last["patience"], last["confirm"]))
            if last["agreed"]:
              out["PA already agreed"] += 1
            elif last["blockedBy"] == BLOCKED_NO_LANE:
              out["refused on GEOMETRY"] += 1
              if last["rule"]:
                out["  ...rule would fire"] += 1
              if last["radar_blind"]:
                out["  ...radar blind"] += 1
            else:
              out["refused for another reason"] += 1
        prev_blinker = blinker
        continue
      if w != "longitudinalPlanSP":
        continue
      if blinker:
        # Do not overwrite the pre-edge snapshot while the stalk is up.
        continue
      try:
        p_a = m.longitudinalPlanSP.passingAssist
      except Exception:  # noqa: BLE001
        continue
      rule = False
      blind = False
      try:
        a = p_a.adjacentLeft
        if not bool(a.available):
          blind = True
        else:
          rule = (int(a.overtakenCount) > 0
                  and float(a.overtakenSeconds) <= OVERTAKE_RECENT_S
                  and not bool(a.oncomingAdjacent))
      except Exception:  # noqa: BLE001
        blind = True
      last = {
        "hasLead": bool(p_a.hasLead),
        # BY NAME, NOT BY NUMBER. `int()` on a capnp enum raises TypeError on the device and cannot
        # fail offline, because every fixture builds messages from SimpleNamespace with plain ints
        # -- test_no_int_on_capnp_enums exists for exactly this and caught it here. The name is also
        # what makes the histogram below readable without counting enum members.
        "blockedBy": str(p_a.blockedBy),
        "agreed": str(p_a.suggestion) == "left",
        "rule": rule,
        "radar_blind": blind,
        # mph, and only when a lead is actually there -- a deficit against no lead is not a number.
        #
        # AND BOUNDED ABOVE, which the first version was not. `speedDeficit` is set speed minus LEAD
        # speed, so a lead the radar is holding with a speed of ~0 produces a deficit equal to the
        # whole set speed: the first run reported a median of 34 mph and a max of 106, which is not
        # a passing decision, it is a bad lead read. Unbounded p75 and median are then meaningless.
        #
        # THIRD TIME TODAY for this exact shape -- the narrowing measurement reported 99.95 m and
        # the oncoming flag 95 mph, both from bands with a floor and no ceiling. A quantity with a
        # physical range needs BOTH ends stated, every time, and it is always the end nobody thought
        # about that admits the garbage.
        "deficit": (float(p_a.speedDeficit) * 2.23694) if bool(p_a.hasLead) else None,
        # ALREADY IN MPH -- custom.capnp says so, and multiplying it again reported his 8 mph
        # setting as 17.9. Nor is it the "active" value its name suggests: it publishes the SETTING,
        # deliberately, because the summary uses it to recommend which number he should change and
        # scaling it would recommend one derived from a momentary road condition. The effective
        # threshold is this times patienceScale, computed here rather than assumed from the name.
        "need": float(p_a.minDeficitActive),
        "patience": float(p_a.patienceScale),
        # HOW LONG THE LEAD HAS BEEN CONTINUOUSLY CONFIRMED. `nothingSlower` covers TWO causes --
        # "no vehicle slow enough" and "slow enough but not yet confirmed" -- and passing_assist
        # line 2984 says so: before the confirmation completes, blocked_by reads nothingSlower.
        # Without this the tool reported a pass with a 14 mph deficit against a 5.2 threshold as a
        # threshold miss, which read as a contradiction and is really a TIMING one.
        "confirm": float(p_a.confirmSeconds),
      }

  n = out["passes"]
  if not n:
    sys.exit("no driver left-passes with a lead on these routes")

  print(f"routes {args.route}\n")
  print(f"  passes he made (left stalk, moving, lead ahead)   {n:5d}")
  for k in ("PA already agreed", "refused for another reason", "refused on GEOMETRY",
            "  ...rule would fire", "  ...radar blind"):
    v = out[k]
    print(f"  {k:<48} {v:5d}   {100.0 * v / n:5.1f}%")
  print()
  geo = out["refused on GEOMETRY"]
  fire = out["  ...rule would fire"]
  if geo:
    print(f"  OF THE GEOMETRY MISSES, the rule would fire on {fire} of {geo} "
          f"({100.0 * fire / geo:.0f}%)")
  print()
  if deficits:
    d = sorted(deficits)
    def dq(f):
      return d[min(len(d) - 1, int(f * len(d)))]
    print(f"  SPEED DEFICIT AT THE MOMENT HE SIGNALLED, mph   n={len(d)}")
    print(f"    p10 {dq(.1):5.1f}   p25 {dq(.25):5.1f}   median {dq(.5):5.1f}   "
          f"p75 {dq(.75):5.1f}   max {d[-1]:5.1f}")
    if deficits_nothing_slower:
      ns = sorted(deficits_nothing_slower)   # tuples: (deficit, needed, patience)
      print(f"    ...the {len(ns)} passes PA called 'nothingSlower', "
            f"as deficit vs what was NEEDED:")
      for dfc, need, pat, conf in ns:
        eff = need * pat
        # A NEGATIVE shortfall means the lead WAS slow enough, so the label is about CONFIRMATION,
        # not the threshold -- a different setting entirely and not one MinDeficit can fix.
        why = "NOT CONFIRMED YET" if dfc >= eff else "below threshold"
        print(f"      deficit {dfc:5.1f}   effective {eff:5.1f}   confirmed {conf:4.1f}s   -> {why}")
      print()
      print("      WHAT A SETTING WOULD HAVE CAUGHT (effective = setting x the patience in force):")
      thresh_misses = [t for t in ns if t[0] < t[1] * t[2]]
      if not thresh_misses:
        print("      NONE of these were threshold misses -- every one had a big enough deficit and")
        print("      was waiting on CONFIRMATION. MinDeficit cannot fix any of them.")
      for cand in (6.0, 5.0, 4.0, 3.0):
        got = sum(1 for dfc, _, pat, _ in ns if dfc >= cand * pat)
        got_nopat = sum(1 for dfc, _, _, _ in ns if dfc >= cand)
        print(f"        MinDeficit {cand:.0f}:  {got} of {len(ns)} with patience as it was,   "
              f"{got_nopat} of {len(ns)} with patience off (set to 10)")
    print("    PassingAssistMinDeficit is HIS setting, in mph. A pass he makes below it is a pass")
    print("    the feature was running for, saw, and declined -- which is a calibration question")
    print("    rather than a sensing one, and the only blocker here he can move without code.")
    print()
  print("  why PA was not suggesting, at the moment he signalled:")
  for code, c in blocked_hist.most_common(8):
    print(f"    {code:<22} {c:5d}")
  print()
  print("  'refused on GEOMETRY' IS THE CEILING -- no lane-proof rule can recover a pass that was")
  print("  refused for oncoming, a blind spot, or patience. 'rule would fire' is the answer, and it")
  print("  is an UPPER BOUND: firing when he signalled is necessary and not sufficient, since the")
  print("  other gates still have to hold through the crossing.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
