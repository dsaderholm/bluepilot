"""Why did the cancel recovery never run? Times the override against the cancel run.

Routes 000003ae and 000003af, 2026-08-23: the stop override fired, the camera latched, the
passthrough went inert for thousands of frames, and he lost Ford ACC for the drive. The recovery
built on 2026-08-22 for exactly this never ran -- no RECOVERY line anywhere in swaglog.

`inert` is not a guess: it is set at `passthrough_cancel_frames >= 250`, i.e. five straight seconds
of the camera asserting cancel. So the counter DID reach its threshold and the recovery was BLOCKED
rather than never triggered. Its four gates are `cancel_is_ours`, `CC.longActive`,
`not stop_override_stopped_us`, and the 30 s bound.

ATTRIBUTION IS THE ONE THAT IS TIMED, and it is testable from `accAuthority` alone:

    cancel_is_ours = frames_since_override <= 3 s, evaluated on the FIRST frame of a cancel run

A run's first frame is the first refusal whose reason is cancel. `inert` follows 5 s later. So:

    (first inert frame) - (last opStop frame)  >  5 s + 3 s   =>  attribution REFUSED it

and anything at or under 8 s means attribution passed and a different gate blocked it. Any frame
whose refusal reason is NOT cancel resets the counter to 0, so a band refusal in between restarts
the clock -- which is why the gap can be much larger than the camera's own behaviour suggests.

    python tools/bp_cancel_attribution.py 000003ae
"""
import os
import sys

OVERRIDE_HZ = 50.0
ATTRIBUTION_S = 3.0
INERT_S = 5.0
REALDATA = os.environ.get("REALDATA", "/data/media/0/realdata")
sys.path.insert(0, "/data/openpilot")
from openpilot.tools.lib.logreader import LogReader  # noqa: E402


def seg_index(name):
  try:
    return int(name.rsplit("--", 1)[1])
  except Exception:
    return -1


def main():
  route = sys.argv[1]
  segs = sorted([d for d in os.listdir(REALDATA) if d.startswith(route)], key=seg_index)

  t0 = None
  holes = []
  runs = []          # (authority, t_start, t_end)
  cur = None
  start = None
  last = None

  for s in segs:
    p = os.path.join(REALDATA, s, "rlog")
    if not os.path.exists(p):
      p += ".zst"
    # A HOLE MUST BREAK THE RUN. The gap between the last opStop frame and the first inert one is
    # the number every conclusion here rests on; stitching an authority run across an unreadable
    # segment inflates it silently by the length of the hole. `cur = None` closes the open run so
    # the next segment starts a fresh one.
    lr = None
    if os.path.exists(p):
      try:
        lr = LogReader(p)
      except Exception:
        lr = None
    if lr is None:
      holes.append(s)
      if cur is not None:
        runs.append((cur, start, last))
      cur = None
      continue
    for m in lr:
      t = m.logMonoTime / 1e9
      # ANCHOR ON EVERY MESSAGE, not just controllerStateBP. bp_recovery_blocked.py anchors on all
      # of them, and anchoring differently made the two tools report the SAME inert window 6.5 s
      # apart -- internally consistent, mutually useless. This file already has a section on t+
      # values that only one tool can produce.
      if t0 is None or t < t0:
        t0 = t
      try:
        if m.which() != "controllerStateBP":
          continue
        a = str(m.controllerStateBP.accAuthority).split(".")[-1]
      except Exception:
        continue
      if a != cur:
        if cur is not None:
          runs.append((cur, start, last))
        cur, start = a, t
      last = t
  if cur is not None:
    runs.append((cur, start, last))

  if not runs:
    print("no controllerStateBP on this route")
    return

  rel = lambda x: x - t0  # noqa: E731
  if holes:
    print("WARNING: {} segment(s) unreadable, runs were broken there rather than stitched: {}".format(
      len(holes), ", ".join(holes)))
    print()

  # Only the transitions that matter, and their durations. A 2-frame blip is noise; the runs that
  # decide this are seconds long.
  print("     from      to      secs  authority")
  for a, s, e in runs:
    if e - s < 0.20 and a not in ("opStop", "inert"):
      continue
    print("  {:8.1f} {:8.1f}  {:6.2f}  {}".format(rel(s), rel(e), e - s, a))

  print()
  budget = INERT_S + ATTRIBUTION_S
  verdicts = []
  for i, (a, s, e) in enumerate(runs):
    if a != "inert":
      continue
    # The last opStop that ENDED before this inert run began.
    prior = [(aa, ss, ee) for aa, ss, ee in runs[:i] if aa == "opStop"]
    if not prior:
      verdicts.append("inert at t+{:.1f} with NO override before it -- not ours, correct".format(rel(s)))
      continue
    gap = s - prior[-1][2]
    verdicts.append(
      "inert at t+{:.1f}; override ended t+{:.1f}; gap {:.2f} s -- {}".format(
        rel(s), rel(prior[-1][2]), gap,
        "ATTRIBUTION REFUSED IT (>{:.0f} s)".format(budget) if gap > budget
        else "attribution PASSED, another gate blocked recovery"))
  for v in verdicts:
    print(v)
  if not verdicts:
    print("never went inert on this route")


if __name__ == "__main__":
  main()
