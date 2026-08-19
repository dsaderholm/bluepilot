#!/usr/bin/env python3
"""For every passing-assist suggestion, what did the map and the radar actually say?

WHY. He reported passes suggested INTO ONCOMING TRAFFIC on a two-lane road. Two gates exist to stop
exactly that -- `oncomingLane` (Blocked @13, the radar oncoming veto) and `noRoomInMap` (@19, the map
saying the road has no same-direction lane to the left) -- and NEITHER appears anywhere in the drive's
state-change timeline. A gate that never fires is either correct-and-quiet or structurally dead, and
the summary cannot tell those apart.

The specific thing to rule out first: `_map_usable` returns None whenever `sm.alive['mapdOut']` is
False, and EVERY map gate is built on it, so one dead subscription silently disables all of them at
once with no log line. mapdOut is declared at 20 Hz and was measured delivering 14.6 Hz with stalls
to 1037 ms (`bp_left_edge_profile`/`bp_mapd_restarts`, 2026-08-18). If SubMaster's liveness window is
tighter than that jitter, the map layer is off on the road while every offline test passes.

So this reports, per suggestion frame:
  - what mapdOut held (oneWay, highwayClass, lanes, waySelectionType, tileLoaded)
  - how stale that mapdOut was, which is the alive question stated as a number
  - what the radar had to the left, and whether anything qualified as oncoming

Read-only. Run on the device from /data/openpilot.
"""
import glob
import os
import sys
from collections import Counter

SUGGESTION = "none"     # Blocked.none, BY NAME -- int() on a capnp enum raises on the device
STALE_WARN_S = 0.5


def main():
  route = sys.argv[1] if len(sys.argv) > 1 else "0000038e"
  sys.path.insert(0, "/data/openpilot")
  from openpilot.tools.lib.logreader import LogReader

  segs = sorted(glob.glob(f"/data/media/0/realdata/{route}--*"))
  if not segs:
    sys.exit(f"no segments for {route}")

  t0 = None
  last_map = None          # (t, oneWay, highwayClass, lanes, waySel, tileLoaded)
  map_frames = 0
  suggestions = []
  prev_blocked = None
  speed = 0.0

  for s in segs:
    f = os.path.join(s, "rlog.zst")
    if not os.path.exists(f):
      continue
    for m in LogReader(f):
      mt = m.logMonoTime * 1e-9
      if t0 is None or mt < t0:
        t0 = mt
      w = m.which()
      if w == "carState":
        speed = float(m.carState.vEgo)
      elif w == "mapdOut":
        map_frames += 1
        o = m.mapdOut
        try:
          last_map = (mt, bool(o.oneWay), str(o.highwayClass), int(o.lanes),
                      str(o.waySelectionType), bool(o.tileLoaded))
        except (AttributeError, TypeError, ValueError):
          last_map = (mt, None, "?", 0, "?", False)
      elif w == "longitudinalPlanSP":
        # str() on the enum, never int(): int() raises TypeError on the device and cannot fail
        # offline, which is what test_no_int_on_capnp_enums guards. A broad except around this
        # turned that raise into "0 suggestions found" on the first run of this tool.
        try:
          pa = m.longitudinalPlanSP.passingAssist
          blocked = str(pa.blockedBy)
          side = str(pa.suggestion)
        except (AttributeError, ValueError):
          continue
        # Only the TRANSITION into a suggestion, so a held state is one row not thousands.
        if blocked == SUGGESTION and prev_blocked != SUGGESTION:
          age = (mt - last_map[0]) if last_map else None
          suggestions.append((mt, side, speed, last_map, age))
        prev_blocked = blocked

  if t0 is None:
    sys.exit("no messages read")

  print(f"route {route}: {len(segs)} segments, {map_frames} mapdOut frames, "
        f"{len(suggestions)} suggestion onsets")
  if not map_frames:
    print("  *** mapdOut NEVER PUBLISHED. Every map gate was inert for the whole drive. ***")
  if not suggestions:
    return

  print()
  print(f"  {'t+':>8} {'side':>5} {'mph':>5} {'oneWay':>7} {'class':>13} {'lanes':>6} "
        f"{'waySel':>10} {'tile':>5} {'map age':>8}")
  stale = 0
  twoway = 0
  for t, side, v, mp, age in suggestions:
    if mp is None:
      print(f"  {t - t0:>8.1f} {side:>5} {v * 2.237:>5.0f}   <no mapdOut ever>")
      continue
    _, one, cls, lanes, waysel, tile = mp
    flag = ""
    if age is not None and age > STALE_WARN_S:
      flag = f"  <- STALE {age:.1f}s"
      stale += 1
    if one is False:
      twoway += 1
      flag += "  <- TWO-WAY ROAD"
    print(f"  {t - t0:>8.1f} {side:>5} {v * 2.237:>5.0f} {str(one):>7} {cls:>13} {lanes:>6} "
          f"{waysel:>10} {str(tile):>5} {age if age is None else round(age, 2):>8}{flag}")

  print()
  print("=== verdict ===")
  print(f"  suggestions on a way mapd says is NOT one-way: {twoway} of {len(suggestions)}")
  print(f"  suggestions where mapdOut was over {STALE_WARN_S}s stale: {stale}")
  cls_counter = Counter(mp[2] for _, _, _, mp, _ in suggestions if mp)
  print(f"  highwayClass at suggestion time: {dict(cls_counter)}")
  if twoway:
    print("  A suggestion on a two-way way is the reported failure. Either noRoomInMap did not")
    print("  evaluate (map unusable) or its threshold let it through -- check `lanes` above.")


if __name__ == "__main__":
  main()
