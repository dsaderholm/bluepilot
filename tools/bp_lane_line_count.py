#!/usr/bin/env python3
"""Can the model tell us there is a lane to our LEFT? The last candidate for a lane-count gate.

WHY THIS IS THE LAST ONE. Passing assist must refuse a move into a California express lane, and the
rule that would do it needs only a COUNT, not a position: if there is no general-purpose lane to our
left, anything detected over there is not part of our roadway. Two signals were tried and both are
measured dead --

    left road edge          trusted 0.0% of frames on multi-lane motorway
    distanceFromWayCenter   24.2% physically impossible on motorway

`modelV2` carries FOUR lane lines, not two, each with a probability. If `laneLineProbs` on the
outermost LEFT line is high, the model is asserting a line beyond our immediate left boundary, which
means a lane beyond it. That is a count, and a count is enough.

WHAT WOULD MAKE IT USABLE. The gate only ever REFUSES, so it must not be confidently wrong in the
permissive direction. The test is therefore not "does it correlate" but:

    on a road with ONE lane in our direction, is the outer-left probability reliably LOW?

A motorwayLink with `lanes = 1` is exactly that road, and route 00000383 has 463 frames of it. If
the model says "there is a lane to your left" on a single-lane ramp, the signal cannot carry a
refusal and this line of attack is finished too.

INDEX ORDER IS VERIFIED, NOT ASSUMED. The four lines are conventionally
[far-left, left, right, far-right] and openpilot's y axis is positive to the LEFT, but that is
exactly the kind of assumption that has been wrong here before, so the tool prints the median lateral
position of each line and the caller can see the ordering rather than trust it.
"""
import glob
import os
import re
import sys
from collections import defaultdict


def segments_in_order(route):
  """Segment dirs for a route, in DRIVE order.

  sorted(glob(...)) is a STRING sort, so --10 lands before --2 and the drive is walked out of
  order. Harmless for whole-drive percentages, fatal for "what happened at the start", which is
  exactly the question a road report asks. Sort on the trailing integer instead.
  """
  segs = glob.glob(f"/data/media/0/realdata/{route}--*")
  def idx(p):
    m = re.search(r"--(\d+)$", p)
    return int(m.group(1)) if m else -1
  return sorted(segs, key=idx)


MIN_SPEED = 15.0          # m/s
LANE_M = 3.7


def q(vals, p):
  if not vals:
    return float("nan")
  s = sorted(vals)
  return s[min(len(s) - 1, int(p * len(s)))]


def y_at_zero(line):
  """Lateral offset of a lane line at the car, in metres. None if the polyline is unusable."""
  try:
    ys = list(line.y)
    return float(ys[0]) if ys else None
  except (AttributeError, TypeError, ValueError, IndexError):
    return None


def main():
  route = sys.argv[1] if len(sys.argv) > 1 else "00000383"
  sys.path.insert(0, "/data/openpilot")
  from openpilot.tools.lib.logreader import LogReader

  segs = segments_in_order(route)
  if not segs:
    sys.exit(f"no segments for {route}")

  speed = 0.0
  hwy = ""
  lanes = 0
  buckets = defaultdict(lambda: {"p": [[], [], [], []], "y": [[], [], [], []], "n": 0})

  for s in segs:
    f = os.path.join(s, "rlog.zst")
    if not os.path.exists(f):
      continue
    for m in LogReader(f):
      w = m.which()
      if w == "carState":
        speed = float(m.carState.vEgo)
      elif w == "mapdOut":
        hwy = str(m.mapdOut.highwayClass)
        try:
          lanes = int(m.mapdOut.lanes)
        except (AttributeError, TypeError, ValueError):
          lanes = 0
      elif w == "modelV2":
        if speed < MIN_SPEED:
          continue
        try:
          probs = list(m.modelV2.laneLineProbs)
          lines = list(m.modelV2.laneLines)
        except (AttributeError, TypeError, ValueError):
          continue
        if len(probs) < 4 or len(lines) < 4:
          continue
        b = buckets[f"{hwy or 'unknown'}, {lanes if lanes else '?'} lanes"]
        b["n"] += 1
        for i in range(4):
          b["p"][i].append(float(probs[i]))
          yv = y_at_zero(lines[i])
          if yv is not None:
            b["y"][i].append(yv)

  if not buckets:
    sys.exit("no qualifying frames")

  print(f"route {route}, above {MIN_SPEED:.0f} m/s ({MIN_SPEED * 2.237:.0f} mph)")
  print("laneLineProbs / lateral position of each of the FOUR lines.")
  print("Ordering is printed rather than assumed -- read the median y row to confirm which")
  print("index is the outer LEFT line before believing any probability below it.")

  for key in sorted(buckets, key=lambda k: -buckets[k]["n"]):
    b = buckets[key]
    if b["n"] < 100:
      continue
    print(f"\n=== {key} ===   {b['n']} frames")
    print(f"  {'idx':>4}{'median y (m)':>14}{'prob p10':>10}{'prob p50':>10}{'prob p90':>10}"
          f"{'prob>0.5':>10}")
    for i in range(4):
      ys, ps = b["y"][i], b["p"][i]
      hi = sum(1 for v in ps if v > 0.5)
      print(f"  {i:>4}{q(ys, .50):>14.2f}{q(ps, .10):>10.2f}{q(ps, .50):>10.2f}"
            f"{q(ps, .90):>10.2f}{100.0 * hi / max(len(ps), 1):>9.0f}%")

  print()
  print("THE DECIDING ROW is the single-lane ramp (motorwayLink, 1 lanes). The outer-left line")
  print("must be reliably LOW there, because a refusal gate that fires on a road with no lane to")
  print("the left would be confidently wrong in the permissive direction.")


if __name__ == "__main__":
  main()
