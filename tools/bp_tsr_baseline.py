"""TSR baseline: how often does the camera read a sign, and does it ever CHANGE its mind.

The open question this answers is not "does it read" -- it demonstrably does -- but whether a read
is followed by more reads, or LATCHES for the rest of the drive. Those need different fixes and an
as-built change can only be judged against the right one.

Prints, per route: distinct sign-read events (rising edges out of the 0/255 sentinel), every value
seen with its frame count, and whether the field ever returned to the sentinel after a read.
"""
import os
import sys
from collections import Counter

REALDATA = os.environ.get("REALDATA", "/data/media/0/realdata")
sys.path.insert(0, "/data/openpilot")
from openpilot.tools.lib.logreader import LogReader  # noqa: E402


def seg_index(name):
  try:
    return int(name.rsplit("--", 1)[1])
  except Exception:
    return -1


def scan(route, segs):
  events = 0
  returns = 0          # transitions back to the sentinel AFTER a read -- proves it is not stuck
  values = Counter()
  frames = 0
  reading = False
  seen_read = False
  for s in segs:
    p = os.path.join(REALDATA, s, "rlog")
    if not os.path.exists(p):
      p += ".zst"
    if not os.path.exists(p):
      continue
    try:
      lr = LogReader(p)
    except Exception:
      continue
    for m in lr:
      try:
        if m.which() != "carStateBP":
          continue
        v = int(m.carStateBP.trafficSignData.vLimit1)
      except Exception:
        continue
      frames += 1
      now = v not in (0, 255)
      if now:
        values[v] += 1
        if not reading:
          events += 1
          seen_read = True
      elif reading and seen_read:
        returns += 1
      reading = now
  return events, returns, values, frames


def main():
  routes = {}
  for d in os.listdir(REALDATA):
    if "--" not in d:
      continue
    routes.setdefault(d.rsplit("--", 1)[0], []).append(d)

  def when(r):
    return max(os.path.getmtime(os.path.join(REALDATA, s)) for s in routes[r])

  n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
  for r in sorted(routes, key=when, reverse=True)[:n]:
    segs = sorted(routes[r], key=seg_index)
    events, returns, values, frames = scan(r, segs)
    vs = ", ".join("{} x{}".format(v, c) for v, c in sorted(values.items())) or "none"
    print("{}  {:>2} seg  {:>6} frames  {:>2} read(s)  {:>2} return(s) to sentinel  [{}]".format(
      r, len(segs), frames, events, returns, vs))
    sys.stdout.flush()


if __name__ == "__main__":
  main()
