#!/usr/bin/env python3
"""FusionPilot: does the feeder's NEAREST rule ever hide the target that would refuse a pass?

THE QUESTION. `bp_rear_digest_sim.reduce_to_sides` -- the reference the Teensy firmware mirrors --
picks `min(closing, key=d_rel)`, under a docstring arguing "the target that matters is the one
arriving first". Those are two different targets:

    80 m back, closing 15 m/s   ->  TTC  5.3 s   RearApproachSide.blocks_lane_change REFUSES
    20 m back, closing  2 m/s   ->  TTC 10.0 s   allows it

Nearest reports the second. The first is discarded AT THE FEEDER, where nothing downstream can see
it existed -- the digest carries one target per side and `targetCount` is the only tell. So this is
the one place in the whole rear chain where evidence is destroyed rather than weighed, and whether
that matters is a measurement, not an argument.

WHAT IT MEASURES, and the honest caveat first: THERE IS NO REAR RADAR, so this runs against the
FRONT MRR on recorded drives. The decode, the binning and the thresholds are the real ones -- it
imports them from bp_rear_digest_sim rather than restating them, so a change there changes this.
What differs is the POPULATION: forward closing targets are mostly traffic we are overtaking and
oncoming cars, where rear closing targets are faster followers. Read the disagreement RATE as
"how often does a multi-target scene on his roads separate the two rules", not as a rear-radar
frequency.

  python tools/bp_digest_pick_rule.py <route>            # on the device
  python tools/bp_digest_pick_rule.py <route> --segments 12

The number that decides it is CROSSINGS: scans where nearest reports a target above UNSAFE_TTC_S
while some other closing target on that side is below it. That is the feeder answering "clear"
about a side that a rear radar would have refused.
"""
from __future__ import annotations

import argparse
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from openpilot.tools.bp_rear_digest_sim import (  # noqa: E402
  EMPTY_PREFIX, LONG_RANGE_SCANS, MAX_RANGE_M, MIN_CLOSING_MS, MIN_LONG_RANGE_DIST_M,
  MRR_END, MRR_HEADER, MRR_START, OWN_LANE_HALF_WIDTH_M, Detection,
  mrr_layout, sig,   # ONE copy of the decoder, in the reference module
)

# THE CONSUMER'S threshold, imported rather than restated -- this whole tool exists to ask whether
# the feeder can hide a target that would cross it.
from openpilot.sunnypilot.selfdrive.controls.lib.rear_approach import UNSAFE_TTC_S  # noqa: E402


def ttc(det: Detection) -> float:
  return det.d_rel / det.v_rel if det.v_rel >= MIN_CLOSING_MS else float("inf")


def compare_side(closing: list[Detection]) -> tuple[bool, bool, float, float]:
  """(rules_differ, crossing, ttc_of_nearest, ttc_of_soonest) for one side of one scan."""
  if len(closing) < 2:
    return False, False, float("inf"), float("inf")
  nearest = min(closing, key=lambda d: d.d_rel)
  soonest = min(closing, key=ttc)
  t_near, t_soon = ttc(nearest), ttc(soonest)
  differ = nearest is not soonest
  # THE ONE THAT MATTERS. The feeder said "nothing arriving inside the veto window" while a target
  # it threw away was inside it.
  crossing = differ and t_near >= UNSAFE_TTC_S > t_soon
  return differ, crossing, t_near, t_soon


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("route")
  ap.add_argument("--segments", type=int, default=0, help="0 = all")
  args = ap.parse_args()

  from openpilot.tools.lib.logreader import LogReader

  dbc_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                          "opendbc_repo", "opendbc", "dbc", "FORD_CADS.dbc")
  layout = mrr_layout(dbc_path)
  addr_to_index = {a: a - MRR_START + 1 for a in range(MRR_START, MRR_END + 1)}

  # SEGMENT DIRECTORIES, not a route name. LogReader's SegmentRange wants a dongle-qualified
  # identifier and rejects the bare local route name with "Segment range is not valid" --
  # bp_drive_checkup.py already walks the paths instead, so this does the same.
  realdata = "/data/media/0/realdata"
  segs = sorted((d for d in os.listdir(realdata) if d.startswith(args.route)),
                key=lambda d: int(d.rsplit("--", 1)[-1]) if d.rsplit("--", 1)[-1].isdigit() else -1)
  if args.segments:
    segs = segs[:args.segments]
  assert segs, f"no segments matching {args.route} in {realdata}"

  scans = both_sides = differ_n = cross_n = 0
  worst = None
  cycle: list[tuple[int, bytes]] = []

  def flush():
    nonlocal scans, both_sides, differ_n, cross_n, worst
    if not cycle:
      return
    dets = []
    for addr, raw in cycle:
      i = addr_to_index.get(addr)
      if i is None or raw[:2] == EMPTY_PREFIX:
        continue
      if len(raw) < 8 or not sig(raw, layout["VALID_LEVEL"]):
        continue
      scan = int(sig(raw, layout["SCAN_INDEX_2LSB"]))
      rng = sig(raw, layout["RANGE"])
      if rng <= 0.0 or rng > MAX_RANGE_M:
        continue
      if scan in LONG_RANGE_SCANS and rng < MIN_LONG_RANGE_DIST_M:
        continue
      az = sig(raw, layout["AZIMUTH"])
      dets.append(Detection(d_rel=math.cos(az) * rng, y_rel=-math.sin(az) * rng,
                            v_rel=-sig(raw, layout["RANGE_RATE"]),
                            amplitude=sig(raw, layout["AMPLITUDE"])))
    cycle.clear()
    scans += 1
    for keep in (lambda d: d.y_rel > OWN_LANE_HALF_WIDTH_M,
                 lambda d: d.y_rel < -OWN_LANE_HALF_WIDTH_M):
      closing = [d for d in dets if keep(d) and d.v_rel >= MIN_CLOSING_MS]
      if len(closing) < 2:
        continue
      both_sides += 1
      differ, crossing, t_near, t_soon = compare_side(closing)
      differ_n += differ
      cross_n += crossing
      if crossing and (worst is None or t_soon < worst[1]):
        worst = (t_near, t_soon, len(closing))

  for seg in segs:
    # The rlog FILE inside the segment, not the directory -- same as bp_drive_checkup.py. Handing
    # LogReader the directory raises IsADirectoryError.
    path = os.path.join(realdata, seg, "rlog")
    if not os.path.exists(path):
      path += ".zst"
    if not os.path.exists(path):
      continue
    try:
      lr = LogReader(path)
    except Exception as e:  # noqa: BLE001 - one unreadable segment is not a reason to lose the rest
      print(f"  (skipped {seg}: {e})")
      continue
    for msg in lr:
      if msg.which() != "can":
        continue
      for m in msg.can:
        if m.address == MRR_HEADER:
          flush()
        elif MRR_START <= m.address <= MRR_END:
          cycle.append((m.address, bytes(m.dat)))
    flush()

  print(f"route {args.route}")
  print(f"  radar scans                              {scans:8d}")
  print(f"  side-scans with 2+ CLOSING targets       {both_sides:8d}")
  if both_sides:
    print(f"  ...nearest and soonest DISAGREE          {differ_n:8d}  {100*differ_n/both_sides:5.1f}%")
    print(f"  ...and the disagreement CROSSES {UNSAFE_TTC_S:.0f} s     {cross_n:8d}  {100*cross_n/both_sides:5.1f}%")
  if worst:
    print(f"  worst crossing: nearest TTC {worst[0]:5.1f} s while a discarded target was at "
          f"{worst[1]:.1f} s ({worst[2]} closing targets that side)")
  print()
  print("  CROSSINGS are the decision number: the feeder would have reported the side clear while")
  print("  a target it threw away was inside the veto window. Zero means the nearest rule is safe")
  print("  on these roads and the firmware should be left alone.")
  print("  Front radar as a proxy for rear -- see the module docstring before quoting any of this.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
