#!/usr/bin/env python3
"""FusionPilot: score a route-intent TRANSPORT from a drive, by replaying the real consumer.

WHY THIS EXISTS BEFORE ANY TRANSPORT DOES. The day a source lands, three questions decide whether it
is good enough, and none of them can be answered by looking at the code:

    COVERAGE    how much of the drive did it actually speak for
    FRESHNESS   how far behind was it, against MAX_INSTRUCTION_AGE_S
    LEAD TIME   how long before each maneuver did it say so, against the ~8 s the set speed needs

**LEAD TIME IS THE ONE THAT DECIDES IT, and this fork has already been burned by scoring the other
thing.** `bp_route_intent_score.py` measured mapd's fork prediction at 96-100% ACCURATE and 1.0 s of
lead -- and the accuracy is the vanity figure. A source that is always right and always late is
useless for the exit problem. Score lead time first.

IT REPLAYS `RouteIntent` ITSELF, not a copy of it. The consumer's freshness rule, its no-claim cases
and its bound are all subtle and all argued in route_intent.py; a report that reimplemented them
would drift, and would eventually disagree with the car while looking authoritative. This fork made
exactly that mistake this week in a test that reimplemented its tool's own logic and then failed to
cover a threshold added later. So: import the class, feed it the logged messages, ask it.

WHAT IT CANNOT SEE, said up front. The consumer publishes no per-frame state -- deliberately, since
an unread field is this fork's oldest bug -- so its verdict reaches the log only as
`blockedBy == routeManeuver`, which earlier gates mask. Everything here is RECONSTRUCTED by replay
rather than read back, which is honest but means a disagreement between this and the car is
possible in principle. If one ever appears, believe the car.

USAGE, on the device:

    cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 \
        tools/bp_route_intent_report.py                  # newest route
    ... tools/bp_route_intent_report.py --route 000003b6

EXPECT "no routeIntentBP on this route" UNTIL A TRANSPORT IS FITTED. That is the correct answer
today and the tool says so plainly rather than printing a table of zeros, because a table of zeros
is how "the tool is broken" and "the feature is inert" come to look identical.
"""
from __future__ import annotations

import argparse
import os
import sys

REALDATA = "/data/media/0/realdata"


def seg_index(name: str) -> int:
  tail = name.rsplit("--", 1)[-1]
  return int(tail) if tail.isdigit() else -1


def routes(realdata: str) -> list[str]:
  entries = [d for d in os.listdir(realdata) if "--" in d and seg_index(d) >= 0]
  names = {d.rsplit("--", 1)[0] for d in entries}
  return sorted(names, key=lambda r: max(os.path.getmtime(os.path.join(realdata, d))
                                         for d in entries if d.startswith(r + "--")),
                reverse=True)


def pct(n: int, d: int) -> str:
  return f"{100.0 * n / d:5.1f}%" if d else "    -"


def quantiles(xs: list[float]) -> str:
  if not xs:
    return "-"
  s = sorted(xs)
  def q(f):
    return s[min(len(s) - 1, int(f * len(s)))]
  return f"p10 {q(0.10):.2f}  p50 {q(0.50):.2f}  p90 {q(0.90):.2f}  max {s[-1]:.2f}"


class Replay:
  """Feeds logged routeIntentBP frames to the REAL consumer and records what it decided."""

  def __init__(self):
    from openpilot.sunnypilot.selfdrive.controls.lib.route_intent import RouteIntent
    self.ri = RouteIntent()
    self.last_msg = None          # the most recent routeIntentBP seen, as (mono_ns, fields)
    self.frames = 0               # plan frames scored
    self.available = 0
    self.refused = 0
    # The counterpart to `refused`, and the more consequential of the two: frames where the source
    # would have asked the car to MOVE. A transport is judged on both -- a refusal costs a pass, an
    # open costs a lane change -- and reporting only the refusals would score the safe half.
    self.opened = 0
    self.opened_sides: dict[str, int] = {}
    self.ages: list[float] = []
    self.maneuvers: dict[str, int] = {}
    self.sources: dict[str, int] = {}
    # first-seen monotime per (maneuver, run), for lead time
    self._run_start: float | None = None
    self._run_maneuver: str | None = None
    self.leads: list[tuple[str, float]] = []

  def on_intent(self, mono_ns: int, msg) -> None:
    self.last_msg = (mono_ns, msg)

  def on_frame(self, now_ns: int, v_ego: float) -> None:
    """One plan frame. `now_ns` is that frame's monotime, which is what the consumer's clock read."""
    if self.last_msg is None:
      return
    self.frames += 1

    class _SM:
      def __init__(self, m):
        self.valid = {'routeIntentBP': True}
        self.alive = {'routeIntentBP': True}
        self._m = m

      def __getitem__(self, s):
        return self._m

    self.ri.update(_SM(self.last_msg[1]), now_ns=now_ns)
    if not self.ri.available:
      self._close_run(now_ns)
      return

    self.available += 1
    self.ages.append(self.ri.age_s)
    self.maneuvers[self.ri.maneuver] = self.maneuvers.get(self.ri.maneuver, 0) + 1
    self.sources[self.ri.source] = self.sources.get(self.ri.source, 0) + 1

    # LEAD TIME: a "run" is a continuous stretch reporting the same committing maneuver. Its length
    # is how long the source told us about that maneuver before it went away -- which, for a
    # maneuver actually driven, is the warning it gave.
    committing = self.ri.maneuver not in ("none", "continueAhead")
    if committing and self.ri.maneuver != self._run_maneuver:
      self._close_run(now_ns)
      self._run_maneuver = self.ri.maneuver
      self._run_start = now_ns / 1e9
    elif not committing:
      self._close_run(now_ns)

    if self.ri.refuses_pass(v_ego):
      self.refused += 1
    side = self.ri.committed_side(v_ego)
    if side is not None:
      self.opened += 1
      self.opened_sides[side] = self.opened_sides.get(side, 0) + 1

  def _close_run(self, now_ns: int) -> None:
    if self._run_maneuver is not None and self._run_start is not None:
      self.leads.append((self._run_maneuver, now_ns / 1e9 - self._run_start))
    self._run_maneuver = None
    self._run_start = None


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--route", default=None)
  ap.add_argument("--segments", type=int, default=60)
  ap.add_argument("--realdata", default=REALDATA)
  ap.add_argument("--min-speed", type=float, default=2.2, help="m/s; parked frames say nothing")
  args = ap.parse_args()

  try:
    from openpilot.tools.lib.logreader import LogReader
  except ImportError as e:
    sys.exit(f"no LogReader ({e}); see the docstring for the interpreter to use")
  if not os.path.isdir(args.realdata):
    sys.exit(f"no {args.realdata} -- run this on the device, or pass --realdata")

  all_routes = routes(args.realdata)
  if not all_routes:
    sys.exit("no routes")
  route = args.route or all_routes[0]
  route = next((r for r in all_routes if r.startswith(route)), route)

  segs = sorted([d for d in os.listdir(args.realdata) if d.startswith(route + "--")],
                key=seg_index)[:args.segments]
  print(f"# route {route}, {len(segs)} segment(s)")

  rep = Replay()
  n_intent = n_plan = 0
  v_ego = 0.0
  moving = 0

  for d in segs:
    p = os.path.join(args.realdata, d, "rlog")
    if not os.path.exists(p):
      p += ".zst"
    if not os.path.exists(p):
      continue
    try:
      lr = LogReader(p)
    except Exception as e:  # noqa: BLE001
      print(f"# {d}: unreadable ({e})")
      continue
    for msg in lr:
      w = msg.which()
      if w == "carState":
        v_ego = float(msg.carState.vEgo)
      elif w == "routeIntentBP":
        n_intent += 1
        rep.on_intent(msg.logMonoTime, msg.routeIntentBP)
      elif w == "longitudinalPlanSP":
        n_plan += 1
        if v_ego >= args.min_speed:
          moving += 1
          rep.on_frame(msg.logMonoTime, v_ego)

  print(f"# {n_plan:,} plan frames, {moving:,} of them moving (>= {args.min_speed} m/s)")
  print(f"# {n_intent:,} routeIntentBP frames\n")

  if n_intent == 0:
    print("NO routeIntentBP ON THIS ROUTE.")
    print()
    print("  That is the CORRECT answer until a transport is fitted -- nothing publishes the")
    print("  message today, so the gate is inert and passing assist behaves exactly as it always")
    print("  has. It is not a fault and it is not this tool failing.")
    print()
    print("  When a source does land, re-run this. The number that decides whether it is good")
    print("  enough is LEAD TIME, not accuracy: mapd's own fork prediction scores 96-100% correct")
    print("  and gives 1.0 s of warning against the ~8 s the set speed needs, which is why it was")
    print("  closed as a source.")
    return 0

  print("=== coverage ===")
  print(f"  frames with a usable instruction   {rep.available:7,} of {rep.frames:,}"
        f"   {pct(rep.available, rep.frames)}")
  print(f"  frames the gate would have refused {rep.refused:7,} of {rep.frames:,}"
        f"   {pct(rep.refused, rep.frames)}")
  print(f"  frames it would have asked to MOVE  {rep.opened:7,} of {rep.frames:,}"
        f"   {pct(rep.opened, rep.frames)}"
        + ("   [" + ", ".join(f"{k} {v:,}" for k, v in sorted(rep.opened_sides.items())) + "]"
           if rep.opened_sides else ""))
  print()
  print("  THE SECOND NUMBER IS THE CONSEQUENTIAL ONE. A refusal costs a pass; an open costs a lane")
  print("  change. It should be much the SMALLER of the two -- only exits, forks and lane")
  print("  commitments open, while anything unclassifiable refuses -- and if it is not, the source")
  print("  is emitting committing maneuvers far more often than a real route contains them.")
  print()

  print("=== freshness, seconds behind ===")
  print(f"  {quantiles(rep.ages)}")
  print("  Against MAX_INSTRUCTION_AGE_S in route_intent.py. A p90 near the bound means the")
  print("  transport is only just keeping up and the gate is dropping out between publishes.")
  print()

  print("=== what it said ===")
  for m, n in sorted(rep.maneuvers.items(), key=lambda kv: -kv[1]):
    print(f"  {m:<16} {n:7,}   {pct(n, rep.available)}")
  print()
  print("  sources: " + ", ".join(f"{s} x{n:,}" for s, n in sorted(rep.sources.items())))
  if "stub" in rep.sources:
    print("  ** CONTAINS STUB FRAMES -- tools/bp_route_intent_stub.py was running. Not a real")
    print("     navigator, and any rate above is about a scripted route.")
  print()

  print("=== LEAD TIME per committing maneuver, seconds ===")
  if rep.leads:
    print(f"  n={len(rep.leads)}   {quantiles([s for _, s in rep.leads])}")
    for m, s in rep.leads[:12]:
      print(f"    {m:<16} {s:6.1f} s")
    print()
    print("  THIS IS THE NUMBER THAT DECIDES THE SOURCE. The set speed falls at about 3.3 mph/s,")
    print("  so a 65 -> 38 mph exit needs roughly 8 seconds of warning. A source with a median")
    print("  below that cannot fix the exit problem however accurate it is.")
  else:
    print("  no committing maneuvers seen -- nothing to score.")
  print()

  print("=== read this before quoting any rate above ===")
  print("  Everything here is RECONSTRUCTED by replaying the real RouteIntent against the logged")
  print("  messages, because the consumer publishes no per-frame state. It is the same class the")
  print("  car runs, but if this ever disagrees with the car, believe the car.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
