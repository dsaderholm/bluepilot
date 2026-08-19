#!/usr/bin/env python3
"""Did mapd_v2 die and restart during a drive, and does that explain the SCC-Map fallback?

WHY THIS EXISTS. mapd issue 88 (closed 2026-08-12) reports that mapd v2's SHADOW subscriber can
panic on a torn read -- `panic("Invalid Msgq message size")` in gomsgq -- when the writer overwrites
the region being read under CPU load, most often during model inference. The process dies. `card` is
unaffected because a shadow subscriber never registers a reader slot, so nothing else on the device
looks wrong.

We cannot see the panic. `process_config.py` launches mapd_v2 as
`bash -c "<binary> > /dev/null 2>&1"`, so its stderr -- and therefore the panic text -- is discarded
before it reaches manager's output or swaglog. **The only evidence a drive can carry is the PID
changing in managerState.**

The measurement this is really for: on route 00000383 SCC-Map fell back to v1 on 9.0% of moving
frames, in 5 discrete runs, the longest 38 s. Eight of nine sampled points were traced to our own
`path_from_mapd` returning None for a straight path and fixed. A handful of multi-second gaps is the
shape a process restart makes; a map coverage gap is scattered and single-frame. This tool decides
between those two by putting the PID timeline and the mapdOut silence on the same axis.

Read-only. Run on the device from /data/openpilot.
"""
import glob
import os
import re
import sys


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


PROC = "mapd_v2"
# A gap longer than this is worth printing on its own. mapdOut is 20 Hz, so anything past a second
# is two orders of magnitude out and cannot be scheduling jitter.
GAP_S = 1.0


def route_segments(route):
  segs = segments_in_order(route)
  if not segs:
    sys.exit(f"no segments for {route}")
  return segs


def count_restarts(timeline):
  """Restarts implied by a PID timeline of (t, pid, running, exit_code) rows.

  A restart is a RESPAWN EVENT, which is not the same as a new PID value, and the difference is
  the whole reason this is a function. Counting distinct PIDs -- or, identically, counting
  transitions to a PID never seen before -- misses the case where the kernel hands the respawned
  process the SAME pid it just used. `4321 -> 0 -> 4321` is a death and a restart, and both of
  those formulations score it zero.

  So the state that matters is whether the process has been observed DOWN since the last live
  PID. A restart is a live PID that either differs from the previous live one, or follows a
  down observation at all.

  pid 0 is 'no process' and is never itself a restart -- it is what makes the NEXT live PID one.
  """
  restarts = 0
  prev_live = None
  died_since = False
  for _, pid, running, _ in timeline:
    down = pid <= 0 or not running
    if down:
      if prev_live is not None:
        died_since = True
      continue
    if prev_live is not None and (died_since or pid != prev_live):
      restarts += 1
    prev_live = pid
    died_since = False
  return restarts


def attribute_gaps(gaps, timeline):
  """Pair each (start, duration) silence with any process state change inside it.

  The window is CLOSED at both ends. A restart is detected by managerState, which publishes far
  slower than mapdOut, so the state change that explains a gap routinely lands on the same
  timestamp as its first or last silent frame; a half-open window drops exactly those.
  """
  out = []
  for start, dur in gaps:
    inside = [t for t, _, _, _ in timeline if start <= t <= start + dur]
    out.append((start, dur, inside))
  return out


def main():
  # Imported here, not at module scope: LogReader pulls in the device's hardware layer, and the
  # pure judgments above are unit tested off the device. A module-level import makes the whole
  # file unimportable in the offline suite.
  sys.path.insert(0, "/data/openpilot")
  from openpilot.tools.lib.logreader import LogReader

  route = sys.argv[1] if len(sys.argv) > 1 else "00000383"
  segs = route_segments(route)

  t0 = None
  pid_timeline = []       # (t, pid, running, exit_code)
  last = None
  mapd_times = []
  manager_frames = 0

  for s in segs:
    f = os.path.join(s, "rlog.zst")
    if not os.path.exists(f):
      continue
    for m in LogReader(f):
      # Anchor on the running minimum, never on the first message: every segment replays the
      # boot-time header block, so the smallest monotime can arrive at any point in the walk.
      # See CLAUDE.md, "EVERY t+NNNN PRINTED BEFORE 2026-08-12 IS INFLATED".
      mt = m.logMonoTime * 1e-9
      if t0 is None or mt < t0:
        t0 = mt
      w = m.which()
      if w == "managerState":
        manager_frames += 1
        for p in m.managerState.processes:
          if p.name != PROC:
            continue
          cur = (int(p.pid), bool(p.running), int(p.exitCode))
          if cur != last:
            pid_timeline.append((mt, *cur))
            last = cur
      elif w == "mapdOut":
        mapd_times.append(mt)

  if t0 is None:
    sys.exit("no messages read")

  print(f"route {route}: {len(segs)} segments, {manager_frames} managerState frames")
  if not manager_frames:
    print("  managerState never appeared -- this route cannot answer the question.")
    return

  print()
  print(f"=== {PROC} state changes ===")
  if not pid_timeline:
    print(f"  {PROC} never appeared in managerState at all.")
    print("  That means the process was not configured to run, not that it ran cleanly.")
  else:
    print(f"  {'t+':>9}  {'pid':>7}  {'running':>7}  {'exit':>5}")
    for t, pid, running, ec in pid_timeline:
      print(f"  {t - t0:>9.1f}  {pid:>7}  {str(running):>7}  {ec:>5}")

    distinct = sorted({p for _, p, _, _ in pid_timeline if p > 0})
    restarts = count_restarts(pid_timeline)
    print()
    print(f"  distinct PIDs: {len(distinct)}  ->  {restarts} restart(s)")
    if restarts:
      print("  A PID change IS a death and respawn. The panic text is discarded by the")
      print("  `> /dev/null 2>&1` in process_config.py, so this is the whole visible trace.")
    else:
      print("  One PID for the whole drive: the process never died. Issue 88 did not fire here,")
      print("  and any mapdOut silence below has some other cause.")

  print()
  print("=== mapdOut silence ===")
  if not mapd_times:
    print("  mapdOut never published. Check MapdV2 and that the route postdates enabling it.")
    return
  mapd_times.sort()
  span = mapd_times[-1] - mapd_times[0]
  gaps = []
  for a, b in zip(mapd_times, mapd_times[1:]):
    if b - a > GAP_S:
      gaps.append((a, b - a))
  print(f"  {len(mapd_times)} frames over {span:.0f} s "
        f"({len(mapd_times) / max(span, 1e-9):.1f} Hz average)")

  # An average rate below nominal says nothing about WHERE the frames went, and the two
  # possibilities have different causes: a steady slower cadence is mapd publishing on something
  # other than its timer, while a nominal cadence pocked with short dropouts is contention. The
  # gap list above only shows outages over a second, which is exactly the wrong resolution to
  # tell those apart.
  intervals = sorted(b - a for a, b in zip(mapd_times, mapd_times[1:]))
  if intervals:
    def pct(q):
      return intervals[min(len(intervals) - 1, int(q * len(intervals)))]
    print(f"  interval p50 {pct(0.50) * 1e3:.0f} ms   p90 {pct(0.90) * 1e3:.0f} ms   "
          f"p99 {pct(0.99) * 1e3:.0f} ms   max {intervals[-1] * 1e3:.0f} ms")
    nominal = pct(0.50)
    skipped = sum(1 for i in intervals if i > 1.5 * nominal)
    print(f"  intervals over 1.5x the median: {skipped} "
          f"({100.0 * skipped / len(intervals):.1f}% of frames)")
  print(f"  gaps longer than {GAP_S:.0f} s: {len(gaps)}, "
        f"total {sum(g for _, g in gaps):.1f} s")
  attributed = attribute_gaps(gaps, pid_timeline)
  for a, g, inside in attributed[:25]:
    tag = "  <- state change here" if inside else ""
    print(f"    t+{a - t0:>8.1f}  silent {g:>6.1f} s{tag}")
  if len(gaps) > 25:
    print(f"    ... {len(gaps) - 25} more")

  print()
  print("=== verdict ===")
  restarts = count_restarts(pid_timeline)
  if restarts and gaps:
    matched = sum(1 for _, _, inside in attributed if inside)
    print(f"  {restarts} restart(s), {len(gaps)} silence(s), {matched} of them coincide.")
    print("  Coinciding means issue 88 is a live explanation for the SCC-Map fallback runs.")
  elif gaps:
    print(f"  {len(gaps)} silence(s) with NO process restart. mapd_v2 stayed up, so the")
    print("  silence is mapd choosing not to publish -- no position fix, or no way match.")
    print("  That is a coverage question, not issue 88.")
  else:
    print("  No restarts and no silence. mapd_v2 published continuously.")


if __name__ == "__main__":
  main()
