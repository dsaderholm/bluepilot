#!/usr/bin/env python3
"""FusionPilot: does Ford ACC close the follow gap when the blinker is on?

His question, 2026-08-17. If the answer is yes it is the cheapest feature on the list, because
**passing assist already turns the blinker on 2 s before the lane change** -- so the gap would close
on its own, with Ford still owning the braking at the shorter distance, and no button press, no
lease, and no `IcbmGapControl` involved at all.

WHY IT IS EVEN PLAUSIBLE: `TurnLghtSwtch_D_Stat` lives in `Steering_Data_FD1` and the DBC lists
**IPMA_ADAS** as a receiver. The camera is told which way the stalk is. Plenty of ACC systems use
that to allow a closer approach during an overtake. Whether THIS camera does is firmware, and
nobody in this fork knows.

**AND IT DOES NOT NEED A NEW DRIVE.** He signals constantly on the highway, and every ingredient is
already logged: the blinker state, Ford's own accel request straight off the camera, and the radar
lead. So the experiment is a query over drives already on the device.

WHAT IS COMPARED
----------------
Only steady following counts -- a lead present, gap roughly held, the set speed not binding. Within
that, each blinker assertion of at least MIN_BLINK_S is scored as three windows:

    BEFORE   the 5 s of steady following leading up to the stalk going on
    DURING   the blinker on, from 1 s in (the camera needs a moment to react)
    AFTER    the 5 s once it goes off again

If Ford closes the gap for a signalled overtake, DURING shows a shorter headway and a less negative
`accAccelRequest` -- it stops braking to hold station. If the three windows agree, the camera does
not care and the gap button stays the only mechanism.

WHAT WOULD FOOL IT, and is excluded
-----------------------------------
The obvious confound is that signalling PRECEDES a lane change, and after a lane change the lead is
gone -- headway goes to infinity for reasons that have nothing to do with ACC policy. So a window
is discarded the moment the lead identity looks broken: a jump in dRel larger than LEAD_JUMP_M, or
the lead disappearing. What is being measured is the approach BEFORE the car moves over, which is
also the only part passing assist could use.

USAGE, on the device:

    cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 tools/bp_blinker_gap.py
    python tools/bp_blinker_gap.py --routes 8
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

REALDATA = "/data/media/0/realdata"
MS_TO_MPH = 2.23694

MIN_SPEED = 13.0        # m/s, ~30 mph; below this following is not steady
MIN_BLINK_S = 1.5       # a real signal, not a lane-keep flick
WINDOW_S = 5.0          # how much before/after to average
SETTLE_S = 1.0          # ignore the first second of the blinker; the camera needs a moment
LEAD_JUMP_M = 12.0      # a step this large means a different car, not a closing gap
MAX_VREL = 2.5          # m/s; steady following, not an active approach or a fast drop-back


def seg_index(name: str) -> int:
  tail = name.rsplit("--", 1)[-1]
  return int(tail) if tail.isdigit() else -1


def newest_routes(count: int):
  if not os.path.isdir(REALDATA):
    sys.exit(f"no {REALDATA} -- run this on the device")
  routes: dict[str, list[str]] = defaultdict(list)
  for d in os.listdir(REALDATA):
    if "--" in d and seg_index(d) >= 0:
      routes[d.rsplit("--", 1)[0]].append(d)
  if not routes:
    sys.exit("no route segments")

  def when(r: str) -> float:
    return max(os.path.getmtime(os.path.join(REALDATA, d)) for d in routes[r])
  return sorted(routes, key=when)[-count:], routes


def mean(xs):
  return sum(xs) / len(xs) if xs else None


def headway(sample):
  """Seconds of following distance, which is the number the gap setting actually names."""
  _, v_ego, d_rel, _, _, _ = sample
  return d_rel / v_ego if v_ego > 1.0 else None


def score(samples, t0, t1):
  """Average headway and Ford's own accel request across a time window, or None if unusable."""
  window = [s for s in samples if t0 <= s[0] <= t1]
  if len(window) < 10:
    return None
  d_rels = [s[2] for s in window]
  if max(d_rels) - min(d_rels) > LEAD_JUMP_M:
    return None                      # the lead changed identity inside the window
  if any(abs(s[3]) > MAX_VREL for s in window):
    return None                      # not steady following
  gaps = [g for g in (headway(s) for s in window) if g is not None]
  if not gaps:
    return None
  return mean(gaps), mean([s[4] for s in window]), mean([s[1] for s in window])


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--routes", type=int, default=6)
  ap.add_argument("--max-segments", type=int, default=12)
  args = ap.parse_args()

  try:
    from openpilot.tools.lib.logreader import LogReader
  except ImportError as e:
    sys.exit(f"no LogReader ({e}); see the docstring for the interpreter to use")

  picked, routes = newest_routes(args.routes)
  print(f"# routes: {', '.join(picked)}")

  events = []
  skipped_no_lead = 0

  for route in picked:
    for seg in sorted(routes[route], key=seg_index)[:args.max_segments]:
      path = os.path.join(REALDATA, seg, "rlog")
      if not os.path.exists(path):
        path += ".zst"
      if not os.path.exists(path):
        continue
      try:
        lr = LogReader(path)
      except Exception:  # noqa: BLE001
        continue

      samples = []          # (t, v_ego, d_rel, v_rel, ford_accel, blinker)
      d_rel = v_rel = 0.0
      lead_ok = False
      ford_accel = 0.0

      for msg in lr:
        try:
          w = msg.which()
          if w == "radarState":
            lead = msg.radarState.leadOne
            lead_ok = bool(lead.status)
            d_rel, v_rel = float(lead.dRel), float(lead.vRel)
            continue
          if w == "carStateBP":
            ford_accel = float(msg.carStateBP.brakeLightStatus.accAccelRequest)
            continue
          if w != "carState":
            continue
          cs = msg.carState
          if not cs.cruiseState.enabled or cs.vEgo < MIN_SPEED:
            continue
          if not lead_ok:
            skipped_no_lead += 1
            continue
          blink = 1 if cs.leftBlinker else (2 if cs.rightBlinker else 0)
          samples.append((msg.logMonoTime / 1e9, float(cs.vEgo), d_rel, v_rel, ford_accel, blink))
        except Exception:  # noqa: BLE001
          continue

      # Find each contiguous run of blinker-on and score the three windows around it.
      i = 0
      while i < len(samples):
        if not samples[i][5]:
          i += 1
          continue
        j = i
        while j + 1 < len(samples) and samples[j + 1][5] == samples[i][5]:
          j += 1
        t_on, t_off = samples[i][0], samples[j][0]
        if t_off - t_on >= MIN_BLINK_S:
          before = score(samples, t_on - WINDOW_S, t_on)
          during = score(samples, t_on + SETTLE_S, t_off)
          after = score(samples, t_off, t_off + WINDOW_S)
          if before and during:
            events.append((before, during, after, t_off - t_on, samples[i][5]))
        i = j + 1

  print(f"\n=== {len(events)} signalled events with steady following on both sides ===")
  if not events:
    print("  none. Either these drives had no lead while signalling, or the thresholds are wrong.")
    print(f"  ({skipped_no_lead:,} engaged frames were dropped for having no radar lead at all.)")
    return 0

  d_gap = mean([d[0] - b[0] for b, d, _, _, _ in events])
  d_accel = mean([d[1] - b[1] for b, d, _, _, _ in events])
  closed = sum(1 for b, d, _, _, _ in events if d[0] < b[0] - 0.05)

  print(f"  mean headway BEFORE the blinker   {mean([b[0] for b, _, _, _, _ in events]):5.2f} s")
  print(f"  mean headway DURING               {mean([d[0] for _, d, _, _, _ in events]):5.2f} s"
        f"   ({d_gap:+.2f} s)")
  restored = [a[0] for _, _, a, _, _ in events if a]
  if restored:
    print(f"  mean headway AFTER                {mean(restored):5.2f} s")
  print(f"\n  Ford's own accel request moved    {d_accel:+.3f} m/s^2 while signalling")
  print(f"  events where the gap CLOSED       {closed} of {len(events)}")

  print("\n  READ IT THIS WAY:")
  if d_gap < -0.15 and closed > len(events) * 0.6:
    print("    The camera closes the gap for a signalled overtake. That is the feature, free --")
    print("    passing assist already puts the blinker on 2 s ahead of the change, so it would")
    print("    happen with no button press and Ford still owning the braking at the shorter gap.")
  elif abs(d_gap) < 0.10:
    print("    No effect. The camera reads TurnLghtSwtch_D_Stat but does not act on it for gap,")
    print("    so the gap BUTTON stays the only mechanism and IcbmGapControl is still the answer.")
  else:
    print("    Ambiguous -- the shift is real but small or inconsistent. Before believing it, check")
    print("    that these events are not mostly the approach to a lane change, where the headway")
    print("    shortens because HE closed on the lead, not because the camera allowed it.")

  print("\n  NOTE: this measures signalling the driver did for his own reasons. It does NOT show")
  print("  what happens when the blinker is asserted with no lane change behind it -- and that")
  print("  case is ruled out anyway: signalling a maneuver the car is not making is the same")
  print("  objection that killed the stalk-tap gesture. This is only worth having because the")
  print("  blinker is ALREADY on during a pass.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
