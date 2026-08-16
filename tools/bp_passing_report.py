#!/usr/bin/env python3
"""FusionPilot: dump everything passing assist knows, from the device, in one paste.

RUN THIS ON THE CAR over SSH:

    cd /data/openpilot && python tools/bp_passing_report.py            # the stored drives
    cd /data/openpilot && python tools/bp_passing_report.py --live 60  # watch it decide, 60 s

WHY IT EXISTS
Every diagnostic in this feature has been squeezed into three lines on a panel, because the
standing assumption was that the panel was the only channel -- no SSH, no logs. That assumption is
half retired: "I can do SSH as much as you want... I just won't be able to SSH and do stuff like
that after each drive."

So this is NOT the per-drive workflow. The panel line he reads at a stop is still that. This is for
settling a question that the panel cannot answer in three lines and that is currently costing whole
drives -- run it once, get the answer, go back to driving.

It matters more than it sounds. The panel can say WHICH term refused; it cannot say what the four
numbers were doing over an hour, and it cannot be read at 70 mph anyway. Five drives produced
twenty-one passes, zero suggestions, and no idea which of four constants was responsible. --live
answers that in a couple of minutes of ordinary driving, and --history hands over a fortnight.

Neither mode writes anything or touches the car.
"""

import argparse
import json
import time
from collections import Counter


# The left gate's terms, in the order passing_assist.py evaluates them. Mirrored rather than
# imported: this runs on the device where importing the planner drags in the whole stack.
GEO_TERMS = ("edge unsure", "paint", "lane width", "room past it")


def _params():
  from openpilot.common.params import Params
  return Params()


def _already_archived(hist, last) -> bool:
  """Is PassingAssistLastDrive the same drive as the newest history entry?

  It is, for most of a drive's life. _archive_drive copies the record into history and stamps a
  build onto the copy, but LastDrive keeps being written until the NEXT drive starts -- so between
  those two moments the same drive exists in both places, and appending it unconditionally listed
  it twice. Seen 2026-08-14: drives 44 and 45 were byte-identical, same 591.6 s, same histogram,
  same lifetime counters, and read as two drives that never happened.

  Compared on the whole record minus `build`, because build is exactly what the archive adds.
  """
  if not hist:
    return False
  newest = {k: v for k, v in hist[-1].items() if k != "build"}
  return newest == {k: v for k, v in last.items() if k != "build"}


def history() -> int:
  p = _params()
  hist = p.get("PassingAssistHistory") or []
  last = p.get("PassingAssistLastDrive")
  if last and not _already_archived(hist, last):
    hist = list(hist) + [dict(last, build="(in progress)")]
  if not hist:
    print("no drives recorded yet")
    return 0
  print(f"{len(hist)} drives\n")
  for i, d in enumerate(hist, 1):
    build = d.get("build", "?")
    share = float(d.get("geoRefusedShare", 0))
    why = ""
    if share > 0:
      why = (f"  [left refused by {GEO_TERMS[int(d.get('geoRefusedBy', 0))]} = "
             f"{d.get('geoRefusedValue')}, {share * 100:.0f}%]")
    print(f"--- drive {i}  build {build}{why}")
    # EACH TERM INDEPENDENTLY, which the line above cannot say. geoRefusedBy is the first failing
    # term in an if/elif chain, so it names A binding term rather than THE one -- and the question
    # that decides whether the geometry gate is fixable is whether paint and the road edge fail
    # TOGETHER or ALTERNATELY. Shares sum past 100% exactly when they overlap, which is the answer.
    fails = d.get("geoTermFails")
    if fails:
      print("    each term, independently: " +
            ", ".join(f"{n} {v * 100:.0f}%" for n, v in zip(GEO_TERMS, fails, strict=False)))

    # WHERE OPPOSING TRAFFIC ACTUALLY SAT, which is the whole reason the histogram exists and was
    # being written to the param and read by nobody -- a bare sixteen-element array in a JSON dump
    # is not a readout. Printed as a profile because the SHAPE is the answer: a peak past a median
    # means the search has to reach it, and mass spread evenly across the band means the returns are
    # noise rather than traffic and no distance setting will separate them.
    # THE ONE THAT DECIDES THE CALIFORNIA RUN. -1 means no frames in that band on this drive.
    bands = d.get("edgeFailBySpeed")
    if bands:
      names = ("<40", "40-55", "55-70", "70+")
      shown = ", ".join(f"{nm} {v * 100:.0f}%" if v >= 0 else f"{nm} -"
                        for nm, v in zip(names, bands, strict=False))
      print(f"    road edge refused, by mph: {shown}")

    # IS THERE A LANE THERE AT ALL -- the one question no camera term can answer, because "no lane
    # line" and "a line the camera missed" refuse identically. The radar is the independent witness:
    # a vehicle tracked to our left proves a left lane exists no matter what the paint says.
    #
    # Printed with an explicit verdict rather than a bare share, because the whole failure this
    # prevents was reading a number and inferring the wrong cause from it twice in one day.
    proven = d.get("geoLeftProven")
    if proven is not None and proven >= 0:
      if proven > 0:
        verdict = "a left lane EXISTS -- these refusals are the camera, not the road"
      else:
        verdict = "no traffic ever seen left; consistent with already being in the left lane"
      print(f"    radar saw a vehicle left on {proven * 100:.0f}% of refused frames: {verdict}")
      # AND THE SAME SHARE WITH THE SPEED TEST APPLIED. The gap between the two lines is traffic
      # that was over there but SLOWING -- a car entering a center turn lane, which is the exact
      # thing that reverted the road-edge waiver on 2026-08-09. On a freeway the two should be
      # nearly equal; a wide gap says the road had a turn lane in it and the top line is not the
      # evidence it appears to be.
      travel = d.get("geoLeftTravelProven")
      if travel is not None and travel >= 0:
        gap = proven - travel
        print(f"    ...of which {travel * 100:.0f}% were TRAVELLING, not slowing"
              f"{'  <- turn-lane risk, do not read the line above as a lane' if gap > 0.15 else ''}")
    elif proven is not None:
      # -1, and it must not read as 0%. See geoLeftProven.
      print("    radar could not answer whether a left lane exists on any refused frame")

    # WHAT THE EXTRA FUSSINESS COST. Printed even at zero, because zero is the answer to "is this
    # doing anything" and a line that only appears when it fired cannot be told from one that is
    # broken -- the same reason the brake lamp pill is drawn in both states.
    missed, refused = d.get("patienceMissed"), d.get("patienceRefused")
    if missed is not None:
      print(f"    fussier-at-the-limit refused {refused:.0f} s of slow-enough leads, and "
            f"{missed} pass{'' if missed == 1 else 'es'} you made yourself")

    # WHICH EXIT TEST DID THE WORK, and how many exits none of them caught. Printed as a verdict
    # for the same reason as the line above: the useful reading is the last bucket, and a bare
    # four-element list invites reading the first.
    exits = d.get("exitsBy")
    if exits and sum(exits):
      caught = ", ".join(f"{nm} {n}" for nm, n in
                         zip(("widening", "outermost", "slowed after"), exits[:3], strict=False) if n)
      print(f"    right-hand driver changes: {sum(exits)}, treated as exits by {caught or 'nothing'}"
            f" -- {exits[3]} not recognized")

    hist = d.get("oncomingLatHist")
    if hist and sum(hist):
      total = sum(hist)
      peak = max(hist)
      bars = "".join("#" if n > peak * 0.66 else "+" if n > peak * 0.33 else
                     "." if n else " " for n in hist)
      top = max(range(len(hist)), key=lambda i: hist[i])
      print(f"    oncoming by meters 0-15: [{bars}]  {total} returns, peak at {top} m")
    print(json.dumps({k: v for k, v in d.items() if k != "build"}, sort_keys=True))
  return 0


SIDES = ("none", "left", "right")
PHASES = ("idle", "confirming", "waiting", "signaling", "changing", "finishing", "aborting")


def _name(names, i):
  return names[i] if 0 <= i < len(names) else str(i)


def timeline() -> int:
  """The drive as a sequence, so a spoken report can be lined up against it.

  The stored numbers are all aggregates, and his report is ordered -- "first it did this, then I
  waited, then it did that". Without this, "it kept saying would be changing right over and over"
  and the drive summary are two facts that cannot be put beside each other.
  """
  from cereal import custom
  blocked_names = [e for e in custom.LongitudinalPlanSP.PassingAssist.Blocked.schema.enumerants]

  d = _params().get("PassingAssistLastDrive") or {}
  rows = d.get("timeline") or []
  if not rows:
    print("no timeline stored -- the drive has to have wanted a pass at least once")
    return 0
  print(f"{len(rows)} state changes over {d.get('elapsed', 0):.0f}s")
  # The headline, printed FIRST because it is the answer to why nothing was ever suggested.
  share = float(d.get("geoRefusedShare", 0))
  if share > 0:
    term = GEO_TERMS[int(d.get("geoRefusedBy", 0))]
    print(f"left side refused by: {term} = {d.get('geoRefusedValue')}  ({share * 100:.0f}% of refusals)")
    print(f"  would need to be {d.get('geoLoosenTo')} to admit four fifths of them")
  print()
  print(f"{'time':>8}  {'decided':<7} {'blocked by':<18} {'pass':<11} keep-right")
  for t, sug, blk, mv, kr in rows:
    mins, secs = divmod(float(t), 60)
    print(f"{int(mins):>5}:{secs:04.1f}  {_name(SIDES, sug):<7} "
          f"{_name(blocked_names, blk):<18} {_name(PHASES, mv):<11} {_name(PHASES, kr)}")
  return 0


def dump() -> int:
  """Every field of the live passingAssist message, once.

  A GENERIC dump rather than a curated list, and that is the point. Thirty-five of the eighty-nine
  published fields had no way to reach them off the car -- they were in the log, which is not a
  channel anyone here uses. Curating them into this file would have fixed thirty-five and left the
  next one to be discovered the same way. This one cannot go stale.
  """
  import cereal.messaging as messaging

  sm = messaging.SubMaster(['longitudinalPlanSP'])
  for _ in range(200):
    sm.update(100)
    if sm.updated['longitudinalPlanSP']:
      d = sm['longitudinalPlanSP'].passingAssist.to_dict()
      for k in sorted(d):
        v = d[k]
        print(f"  {k:28} {json.dumps(v) if isinstance(v, dict | list) else v}")
      return 0
  print("nothing published -- is the car on and openpilot running?")
  return 1


def live(seconds: float) -> int:
  import cereal.messaging as messaging

  sm = messaging.SubMaster(['longitudinalPlanSP', 'carState'])
  refusals: Counter = Counter()
  sums: dict[str, float] = {}
  blocked: Counter = Counter()
  frames = 0
  wanted = 0.0
  seen_left_ok = 0

  print(f"watching for {seconds:.0f}s -- drive normally, then paste everything below\n")
  end = time.monotonic() + seconds
  while time.monotonic() < end:
    sm.update(100)
    if not sm.updated['longitudinalPlanSP']:
      continue
    pa = sm['longitudinalPlanSP'].passingAssist
    frames += 1
    blocked[str(pa.blockedBy)] += 1
    if pa.leftGeometryOk:
      seen_left_ok += 1
      continue
    # Same order as the gate. Whichever it hits first is the one to act on.
    if pa.leftEdgeStd > 1.2:
      term, val = GEO_TERMS[0], pa.leftEdgeStd
    elif pa.leftLineProb < 0.5:
      term, val = GEO_TERMS[1], pa.leftLineProb
    elif not (3.0 <= pa.leftLaneWidth <= 5.0):
      term, val = GEO_TERMS[2], pa.leftLaneWidth
    else:
      term, val = GEO_TERMS[3], pa.leftEdgeBeyond
    refusals[term] += 1
    sums[term] = sums.get(term, 0.0) + val
    wanted = pa.wantedSeconds

  if not frames:
    print("nothing published -- is the car on and openpilot running?")
    return 1

  print(f"frames        {frames}")
  print(f"wantedSeconds {wanted:.0f}")
  print(f"left lane OK  {seen_left_ok} frames ({seen_left_ok / frames * 100:.0f}%)")
  print("\nwhat blocked it:")
  for name, n in blocked.most_common():
    print(f"  {name:20} {n / frames * 100:5.1f}%")
  if refusals:
    print("\nwhen the LEFT side was refused, by which term:")
    for term, n in refusals.most_common():
      print(f"  {term:14} {n / sum(refusals.values()) * 100:5.1f}%   mean {sums[term] / n:.2f}")
  return 0


def main() -> int:
  ap = argparse.ArgumentParser(description=__doc__,
                               formatter_class=argparse.RawDescriptionHelpFormatter)
  ap.add_argument("--live", type=float, metavar="SECONDS", default=None,
                  help="watch the live decision instead of the stored drives")
  ap.add_argument("--timeline", action="store_true",
                  help="the last drive as an ordered list of state changes")
  ap.add_argument("--dump", action="store_true",
                  help="every field of the live message, once -- nothing published is unreachable")
  args = ap.parse_args()
  if args.live:
    return live(args.live)
  if args.dump:
    return dump()
  return timeline() if args.timeline else history()


if __name__ == "__main__":
  raise SystemExit(main())
