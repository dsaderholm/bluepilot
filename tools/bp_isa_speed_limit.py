#!/usr/bin/env python3
"""FusionPilot: the camera's OTHER speed-limit message. IPMA_Data2 (0x3D9), which nothing in this
fork had ever read.

WHY THIS EXISTS. The whole TSR investigation -- `bluepilot/TSR-INVESTIGATION.md`, `tsr_local.py`,
`tsr_scan.py`, the carstate registration, `bp_tsr_check.py` -- reads `Traffic_RecognitnData`
(0x3CD / 973) and nothing else. On 2026-08-23 a grep confirmed that `IsaVLim`, `IaccVLim` and
`IPMA_Data2` appeared NOWHERE in the tree. `IPMA_Data2` is transmitted by the same camera and
carries two more speed-limit signals.

WHAT IT FOUND, and it falsified a standing claim. CLAUDE.md states that `TsrVLim1MsgTxt` is "the
no-data sentinel 255 on every frame of every recent drive" and that "the camera contributes NOTHING
today". Measured:

    route       IsaVLim (0x3D9 b0)        TsrVLim1MsgTxt (0x3CD b3)
    0000039f    254 no-data   99.6%       255 no-data   99.2%
    000003a1    254 no-data  100.0%       255 no-data  100.0%
    000003ac     30 on        25.6%
    000003b6     80 on        34.8%        80 on        33.2%

TWO ROUTES, TWO DIFFERENT PLAUSIBLE LIMITS -- so not a stuck default, which would be the same number
every time. 30 mph is an ordinary street and 80 mph is a real Utah freeway limit, and each appears
on a quarter to a third of frames rather than constantly, which is what a camera reading signs
intermittently looks like. The older measurements hold exactly on the routes they were taken on:
they are STALE, not wrong.

WHAT IS NOT ESTABLISHED, and it is the part that decides whether SLA may use this: whether those
values match the roads actually driven. 80 on a Utah freeway is plausible; 80 on a residential
street is a fault wearing a plausible number. **The cross-check is correlating the value against
`mapdOut.highwayClass` and position on the same route.** Not done. Do not wire this into SLA before
it is.

Signals, from ford_lincoln_base_pt.dbc, all @0+ (big-endian):

    IsaVLim_D_Rq         7|8    -> byte 0        Intelligent Speed Assist limit
    IsaVLimUnit_D_Rq    15|2    -> byte 1 bits 7..6
    IaccVLim_D_Rq       23|8    -> byte 2        Intelligent ACC limit
    IaccVLimUnit_D_Rq   11|2    -> byte 1 bits 3..2
    TsrRegionTxt_D_Stat 47|5    -> byte 5 bits 7..3

USAGE, on the device:

    cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 \
        tools/bp_isa_speed_limit.py                 # newest route
    ... tools/bp_isa_speed_limit.py --route 000003b6
    ... tools/bp_isa_speed_limit.py --sweep 12      # the last 12 routes, one line each

THE SWEEP IS BUILT IN ON PURPOSE. The first version of this was a shell loop that grepped the
per-route output, and its grep dropped the `===` section headers -- so values from `IsaVLimUnit`
ran together with `IsaVLim` and the unit field's constant 2 was parsed as a speed limit. It produced
a confident table of nonsense. A tool whose output has to be re-parsed by a fragile shell pipeline
will eventually be re-parsed wrongly; doing the aggregation in here removes the pipeline.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

REALDATA = "/data/media/0/realdata"

ISA = 985   # 0x3D9 IPMA_Data2
TSR = 973   # 0x3CD Traffic_RecognitnData

# Values that mean "nothing to report" rather than a limit. 254 and 255 are the two sentinels seen
# in practice; 0 is included because an unpopulated byte is not a 0 mph zone.
NO_DATA = {0, 254, 255}


def seg_index(name: str) -> int:
  tail = name.rsplit("--", 1)[-1]
  return int(tail) if tail.isdigit() else -1


def routes(realdata: str) -> list[str]:
  entries = [d for d in os.listdir(realdata) if "--" in d and seg_index(d) >= 0]
  names = {d.rsplit("--", 1)[0] for d in entries}
  return sorted(names, key=lambda r: max(os.path.getmtime(os.path.join(realdata, d))
                                         for d in entries if d.startswith(r + "--")),
                reverse=True)


def scan(route: str, realdata: str, max_segments: int):
  """Counters for one route. Returns (n_isa, n_tsr, isa, unit, iacc, region, tsr)."""
  from openpilot.tools.lib.logreader import LogReader

  isa, unit, iacc, region, tsr = Counter(), Counter(), Counter(), Counter(), Counter()
  n_isa = n_tsr = 0
  segs = sorted([d for d in os.listdir(realdata) if d.startswith(route + "--")],
                key=seg_index)[:max_segments]
  for d in segs:
    p = os.path.join(realdata, d, "rlog")
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
      if msg.which() != "can":
        continue
      for c in msg.can:
        # Bus | 0x80 is panda's echo of our own TX. Counting it reports our frames as the car's.
        if c.src >= 0x80:
          continue
        b = bytes(c.dat)
        if len(b) < 8:
          continue
        if c.address == ISA:
          n_isa += 1
          isa[b[0]] += 1
          unit[(b[1] >> 6) & 0x3] += 1
          iacc[b[2]] += 1
          region[(b[5] >> 3) & 0x1F] += 1
        elif c.address == TSR:
          n_tsr += 1
          tsr[b[3]] += 1
  return n_isa, n_tsr, isa, unit, iacc, region, tsr


def live(counter: Counter) -> list[int]:
  return sorted(v for v in counter if v not in NO_DATA)


def summarise(counter: Counter, total: int, limit: int = 4) -> str:
  if not counter:
    return "-"
  return "  ".join(f"{v}@{100.0 * n / max(total, 1):.0f}%"
                   for v, n in sorted(counter.items(), key=lambda kv: -kv[1])[:limit])


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--route", default=None)
  ap.add_argument("--sweep", type=int, default=0, help="summarise the last N routes, one line each")
  ap.add_argument("--segments", type=int, default=40)
  ap.add_argument("--realdata", default=REALDATA)
  args = ap.parse_args()

  try:
    import openpilot.tools.lib.logreader  # noqa: F401
  except ImportError as e:
    sys.exit(f"no LogReader ({e}); see the docstring for the interpreter to use")
  if not os.path.isdir(args.realdata):
    sys.exit(f"no {args.realdata} -- run this on the device, or pass --realdata")

  all_routes = routes(args.realdata)
  if not all_routes:
    sys.exit("no routes")

  if args.sweep:
    print(f"{'route':<12} {'IsaVLim 0x3D9 b0':<28} {'TsrVLim1 0x3CD b3':<24} verdict")
    hits = 0
    for r in all_routes[:args.sweep]:
      n_isa, n_tsr, isa, _u, _ia, _rg, tsr = scan(r, args.realdata, args.segments)
      real = live(isa) or live(tsr)
      hits += bool(real)
      verdict = f"REAL: {real}" if real else ("no data" if n_isa else "no frames")
      print(f"{r.split('--')[0]:<12} {summarise(isa, n_isa):<28} {summarise(tsr, n_tsr):<24} {verdict}")
    print(f"\n{hits} of {min(args.sweep, len(all_routes))} routes carried a non-sentinel value.")
    print("A RATE IS NOT A VERDICT: whether those values match the roads actually driven is the")
    print("open question, and it needs correlating against mapdOut.highwayClass and position.")
    return 0

  route = args.route or all_routes[0]
  # Accept either the bare id or the full name with the hash.
  if not any(d.startswith(route + "--") for d in os.listdir(args.realdata)):
    match = [r for r in all_routes if r.split("--")[0] == route]
    if not match:
      sys.exit(f"route {route} not found")
    route = match[0]

  n_isa, n_tsr, isa, unit, iacc, region, tsr = scan(route, args.realdata, args.segments)
  print(f"route {route}")
  print(f"0x3D9 IPMA_Data2 frames: {n_isa}      0x3CD Traffic_RecognitnData frames: {n_tsr}\n")

  for title, counter, total, note in (
      ("IsaVLim_D_Rq (byte 0)", isa, n_isa, "254/255 = no data; a real limit looks like 25/35/80"),
      ("IsaVLimUnit_D_Rq", unit, n_isa, ""),
      ("IaccVLim_D_Rq (byte 2)", iacc, n_isa, ""),
      ("TsrRegionTxt_D_Stat", region, n_isa, ""),
      ("CONTROL: 0x3CD TsrVLim1MsgTxt (byte 3)", tsr, n_tsr,
       "CLAUDE.md says 255 on every frame -- that is true of some routes and not others"),
  ):
    print(f"=== {title} ===  {note}")
    if not counter:
      print("  no frames")
    for v, n in sorted(counter.items(), key=lambda kv: -kv[1])[:12]:
      print(f"  value {v:3d}   {n:7d} frames   {100.0 * n / max(total, 1):5.1f}%")
    print()

  print("=== verdict ===")
  real = live(isa)
  if real:
    print(f"  IsaVLim TAKES REAL VALUES: {real}")
    print("  The camera emits a speed limit on 0x3D9. Check them against the roads actually")
    print("  driven before believing them -- a plausible number on the wrong road is still wrong.")
  elif isa:
    print("  IsaVLim is sentinels only on this route -- no speed limit here.")
    print("  Worth having: a null result on BOTH camera messages is a stronger statement than")
    print("  the current claim, which silently covers only 0x3CD.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
