#!/usr/bin/env python3
"""FusionPilot: how good is mapd's guess at which way he is going?

RUN THIS ON THE CAR:

    cd /data/openpilot && python tools/bp_route_intent_score.py                 # newest route
    cd /data/openpilot && python tools/bp_route_intent_score.py --route 0000038a

WHY IT EXISTS
`bluepilot/ROUTE-INTENT.md` step 2: score `predicted` before building anything. Android Auto cannot
supply route intent -- four links in the chain and the first is outside his control -- so the
question became whether the guess already running is good enough to matter. Nobody has looked.

WHAT IT MEASURES, and the reason this needs no labelling work at all:

  `waySelectionType == predicted` means mapd attached to a way from the PREDICTED PATH rather than
  from certainty. Some frames later it resolves to `current`, which is mapd sure of where it is. So
  the later `wayId` GRADES the earlier guess. The route labels itself.

TWO NUMBERS, AND THE SECOND ONE DECIDES IT:

  accuracy   of the predictions that resolved, how many named the way he actually took.
  LEAD TIME  how long before resolution the guess was available. A prediction that is right one
             second out is worthless against the eight-second set-speed budget that the exit
             section of CLAUDE.md establishes. Accuracy without lead time is a vanity number.

RAMPS ARE THE SUBSET THAT MATTERS. For passing assist the question is never "which way" in general,
it is "is he leaving the freeway" -- so predictions resolving onto `motorwayLink` are scored on
their own. That is the population a "do not offer a pass approaching an exit" gate would run on.

The blinker is printed beside it as corroboration and NOT as the label. It arrives at the gore
point, seconds after the decision had to be made; see ROUTE-INTENT 5c. It says what he did, which
is useful for reading the ramp rows, and it cannot say what mapd should have known earlier.

Read-only. Writes nothing and touches no setting.
"""

import argparse
import glob
import os
import sys
from collections import Counter


# Below this, "which way is he going" is not a question anyone is asking, and a car creeping in a
# parking lot produces way changes that are not forks.
MIN_SPEED_MS = 5.0

# mapdOut is 20 Hz. Segments are a minute, so an episode that has not resolved within one is not a
# fork being approached -- it is mapd lost, which `fail` reports separately.
DT_MAPD = 0.05


def find_segments(route):
  base = "/data/media/0/realdata"
  if route:
    segs = sorted(glob.glob(os.path.join(base, f"{route}--*")))
    if not segs:
      sys.exit(f"no segments for {route}")
    return segs
  routes = sorted({os.path.basename(p).split("--")[0] for p in glob.glob(os.path.join(base, "*--*"))})
  if not routes:
    sys.exit("no routes on this device")
  return sorted(glob.glob(os.path.join(base, f"{routes[-1]}--*")))


def spread(segs, cap):
  """EVENLY SPACED, never the first N.

  A front cap answers "did this happen" and silently misreports "what did the whole drive look
  like" -- it put a parked-car figure into CLAUDE.md twice in one day, from two sessions
  independently. See the withdrawn 50x discrepancy in bluepilot/MAPD-V2-PLAN.md.
  """
  if len(segs) <= cap:
    return segs, len(segs)
  step = len(segs) / cap
  return [segs[int(i * step)] for i in range(cap)], len(segs)


class Score:
  """The episode walk, kept out of main() so it can be exercised without a route.

  This tool runs ONCE, on data that does not exist until he drives, and its verdict decides whether
  route prediction gets built at all. A miscount here is not a wrong number on a screen -- it is a
  wasted drive and a decision made on it. So the logic lives here and `feed_segment` is fed synthetic
  frames by the tests.
  """

  def __init__(self):
    self.hits = self.misses = self.unresolved = self.spanning = 0
    self.ramp_hits = self.ramp_misses = 0
    self.lead_times = []
    self.ramp_leads = []
    self.sel_counts = Counter()
    self.blinker_at_resolve = Counter()
    self.mapd_frames = 0

  def feed_segment(self, messages):
    """One segment's messages, in order.

    PER SEGMENT, DELIBERATELY. Sampled segments are not contiguous and every segment replays the
    boot-time header messages, so absolute time across them is meaningless -- see the "every t+NNNN
    is inflated" section of CLAUDE.md. Within one segment logMonoTime is monotonic, so an episode
    that starts and resolves inside it can be timed exactly. Episodes crossing a boundary are
    COUNTED AND DROPPED rather than guessed at.
    """
    open_pred = None          # (wayId, monotime) of the guess currently outstanding
    blinker = None
    speed = 0.0

    for m in messages:
      w = m.which()
      if w == "carState":
        speed = float(m.carState.vEgo)
        blinker = ("left" if m.carState.leftBlinker else
                   "right" if m.carState.rightBlinker else None)
        continue
      if w != "mapdOut":
        continue

      self.mapd_frames += 1
      o = m.mapdOut
      sel = str(o.waySelectionType)
      self.sel_counts[sel] += 1
      if speed < MIN_SPEED_MS:
        open_pred = None
        continue

      if sel == "predicted":
        # Only the FIRST frame of a run opens an episode; a guess held for two seconds is one guess.
        # A CHANGED wayId is a NEW guess -- mapd changed its mind, and timing the second one from
        # the first would credit it with lead time it never had.
        if open_pred is None or open_pred[0] != int(o.wayId):
          open_pred = (int(o.wayId), int(m.logMonoTime))
        continue

      if open_pred is None:
        continue

      if sel == "current":
        pred_way, t0 = open_pred
        lead = (int(m.logMonoTime) - t0) / 1e9
        correct = int(o.wayId) == pred_way
        # Resolution onto a RAMP is the population a passing gate would run on.
        on_ramp = str(o.highwayClass) == "motorwayLink"
        if correct:
          self.hits += 1
          if on_ramp:
            self.ramp_hits += 1
            self.ramp_leads.append(lead)
        else:
          self.misses += 1
          if on_ramp:
            self.ramp_misses += 1
        self.lead_times.append(lead)
        if on_ramp:
          self.blinker_at_resolve[blinker or "none"] += 1
        open_pred = None
      elif sel == "fail":
        self.unresolved += 1
        open_pred = None

    if open_pred is not None:
      self.spanning += 1


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--route", default=None, help="route id; default is the newest on the device")
  ap.add_argument("--max-segments", type=int, default=40)
  args = ap.parse_args()

  try:
    from openpilot.tools.lib.logreader import LogReader
  except Exception as e:
    sys.exit(f"no LogReader ({e}); run this from /data/openpilot")

  segs, total = spread(find_segments(args.route), args.max_segments)
  if len(segs) < total:
    print(f"# sampling {len(segs)} of {total} segments, spread evenly across the route")

  sc = Score()
  for seg in segs:
    f = os.path.join(seg, "rlog.zst")
    if os.path.exists(f):
      sc.feed_segment(LogReader(f))

  hits, misses, unresolved, spanning = sc.hits, sc.misses, sc.unresolved, sc.spanning
  lead_times, ramp_leads = sc.lead_times, sc.ramp_leads
  ramp_hits, ramp_misses = sc.ramp_hits, sc.ramp_misses
  sel_counts, blinker_at_resolve, mapd_frames = sc.sel_counts, sc.blinker_at_resolve, sc.mapd_frames

  if not mapd_frames:
    print("NO mapdOut IN THIS ROUTE AT ALL.")
    print("Before concluding observe mode is broken, check `uptime` against the segment mtimes --")
    print("a route recorded before the running build predates the feature, and v2 publishes nothing")
    print("offroad by design, so a parked device supports whichever conclusion you arrive with.")
    return 0

  resolved = hits + misses
  print(f"mapdOut frames: {mapd_frames}   selection types: {dict(sel_counts)}")
  print()
  if not resolved:
    print("no prediction ever resolved -- mapd was never uncertain then certain on this route")
    return 0

  lead_times.sort()
  med = lead_times[len(lead_times) // 2]
  print(f"PREDICTIONS THAT RESOLVED: {resolved}   correct {hits} ({100.0 * hits / resolved:.0f}%)")
  print(f"  lead time: median {med:.1f}s   best {lead_times[-1]:.1f}s   worst {lead_times[0]:.1f}s")
  print(f"  gave up (fail): {unresolved}   dropped at a segment boundary: {spanning}")
  print()

  ramp_resolved = ramp_hits + ramp_misses
  if ramp_resolved:
    ramp_leads.sort()
    rmed = ramp_leads[len(ramp_leads) // 2] if ramp_leads else 0.0
    print(f"RESOLVED ONTO A RAMP (motorwayLink): {ramp_resolved}   "
          f"correct {ramp_hits} ({100.0 * ramp_hits / ramp_resolved:.0f}%)")
    print(f"  lead time on the ones it got right: median {rmed:.1f}s")
    print(f"  blinker at the moment of resolution: {dict(blinker_at_resolve)}")
    print()
    # THE VERDICT, stated rather than left as arithmetic. Eight seconds is the set-speed travel a
    # 65->38 exit needs at the 3.3 mph/s the cluster moves; see CLAUDE.md's exit section.
    if ramp_hits and rmed >= 8.0:
      print("  -> USABLE: right, and early enough to act on against the 8s budget.")
    elif ramp_hits and rmed >= 3.0:
      print("  -> MARGINAL: right, but arriving inside the 8s budget. Good for a REFUSAL that costs")
      print("     only a missed pass; not enough to plan a set-speed descent around.")
    else:
      print("  -> NOT USABLE AS A PREDICTION on this evidence. It resolves about when the road does.")
  else:
    print("no prediction resolved onto a ramp -- no freeway exits in this route, or none predicted")
  return 0


if __name__ == "__main__":
  sys.exit(main())
