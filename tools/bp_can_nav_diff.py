#!/usr/bin/env python3
"""FusionPilot: is the navigation instruction already on a bus we read? Diff a NAVIGATING drive
against one with no route active.

THE QUESTION, AND WHY IT COMES FIRST. The IPC is a separate module from the APIM and it renders
turn-by-turn, so the instruction MUST cross a bus to reach it -- there is no third possibility. The
only open questions are WHICH bus and WHICH message, and the first one decides whether route intent
needs the canbox at all. Answering it costs one deliberate drive and no hardware.

    it shows up on bus 0/1/2        buildable today. No canbox, no phone, no Waze.
    it does not                     it is on MS-CAN, and route intent arrives WITH BLIS rather
                                    than after it.

WHAT MAKES THIS MORE THAN "IS 0x32B PRESENT". `APIM_Data_FD1` also carries exterior-light and menu
signals, which are published whether or not a route is active -- so a nav channel may well be a
message that is present in BOTH drives and merely says something different in one. Address-level
presence cannot see that. So this diffs PER BYTE: a byte that is pinned in the control drive and
takes several values in the navigating one is the signal, and it is the only shape that finds an
instruction hiding inside a message nobody has decoded.

That is the "diff the wire against the decoder" technique this fork already has written down, run
against two drives instead of against a DBC.

EXPECT NOTHING TO APPEAR, AND READ THAT AS A LOCATION RATHER THAN A DEAD END. Route 000003ab was
checked address by address and every APIM message except position (0x462) was absent from every bus
openpilot logs. If this comes back empty it says the data is on a bus the panda is not wired to,
which is exactly what the canbox is for -- the same canbox BLIS is waiting on.

THE CONTROL SIDE IS ALREADY MEASURED, SO THIS NEEDS ONE DRIVE AND NOT TWO. Run on the device
2026-08-22 against route 000003ac -- 11 segments, 2,919,073 frames, 383 (address, bus) pairs across
buses 0, 1 and 2:

    0x32B  APIM_Data_FD1     ABSENT
    0x462  APIMGPS_Nav_1     bus 0: 603 frames   bus 2: 8
    0x463 / 0x464            ABSENT     (the U0253 finding, again, on a fresh route)
    0x225 0x3F1 0x211 0x215 0x227      ABSENT

Position and nothing else. So any of those addresses appearing on a navigating drive is unambiguous
and needs no second route to compare against -- PROVIDED 000003ac was genuinely a no-route drive,
which only the owner can say. If he was navigating on it, it is not a control and the pair has to be
recorded deliberately.

AND DO NOT CAP THE SEGMENTS, measured rather than argued. The same route at 3 segments and at 11
returned the identical 383 (address, bus) pairs -- so a cap hides no ADDRESS -- but 0x462's varying
bytes went from [2,3,6,7] to [1,2,3,5,6,7]. Byte variance is the thing this tool actually keys on,
and it is precisely what a cap understates.

A BETTER EXPERIMENT THAN TWO DRIVES, and it came from him on 2026-08-22: HE CAN END THE DRIVE FROM
THE IPC, and it works with Google Maps.

That means navigation state is something he can TOGGLE ON DEMAND, which is a far sharper signal than
a between-drives comparison. A distance counting down is slow and subtle in a byte diff; nav going
active -> inactive at a clock time he wrote down is not.

    ONE drive. Navigate with Google Maps. End the drive from the IPC, restart it, three or four
    times, noting the clock time of each transition. Then look for bytes that toggle in step.

It also yields a second thing the two-drive diff cannot: whatever the IPC TRANSMITS to request the
cancel, which is the reverse direction of the same protocol.

MAYBE WITHOUT DRIVING AT ALL. If the APIM publishes nav state while stationary, this works in the
driveway. Unknown -- nav data may be gated on motion -- but it is one attempt and it is the cheapest
version of the whole experiment.

The two-drive method below still works and its control side is already recorded, so it remains the
fallback if toggling turns out to change nothing while parked.

HOW TO PRODUCE THE TWO DRIVES. Google Maps, not Waze: he confirmed Maps still renders turns on his
IPC while Waze does not, so Maps is the source that is definitely publishing today. Navigate
somewhere real for a few minutes, then a second drive over similar roads with no route active.
Similar roads matter -- a freeway drive against a city drive differs in a hundred messages that
have nothing to do with navigation.

USAGE, on the device:

    cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 \
        tools/bp_can_nav_diff.py --nav 000003b0 --control 000003b1

    ... tools/bp_can_nav_diff.py --inventory 000003b0      # one route, just what is on the wire
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

REALDATA = "/data/media/0/realdata"

# The APIM's own traffic, from ford_lincoln_base_pt.dbc. Reported by name whether present or not,
# because "absent" is the finding here as much as "present" is.
APIM_ADDRS = {
  811: "APIM_Data_FD1        DistToStopover_L_Actl, StopoverType_D_Stat, light menus",
  1122: "APIMGPS_Data_Nav_1   lat/lon                    <- the one that IS present",
  1123: "APIMGPS_Data_Nav_2   UTC, PDOP, compass         <- absent; this is the U0253 finding",
  1124: "APIMGPS_Data_Nav_3   heading, altitude, HDOP",
  549: "IPC_Infotainment_FD1 the cluster's own infotainment message",
  1009: "APIM_Send_Signals1   track number/count -- proof the APIM says SOMETHING when present",
  533: "APIM_Send_Signals_2",
  261: "APIM_Request_Signals",
  551: "APIM_Request_Signals_1",
  529: "APIM_Request_Signals_5",
  994: "Personality_APIM_Data",
}

# Known-present controls. If these come back zero the tool is broken, not the car -- which is the
# failure mode that would otherwise read as "navigation is not on the bus". Same guard, same
# reason, as bp_apim_probe.py.
CONTROL_ADDRS = {
  973: "Traffic_RecognitnData  (camera)",
  394: "ACCDATA_3              (camera)",
  131: "Steering_Data_FD1      (buttons)",
}

# A byte has 256 possible values, so per-byte state is naturally bounded. Whole payloads are not:
# a wheel-speed message has effectively unlimited distinct payloads, so that set is capped and
# reported as saturated rather than grown.
MAX_PAYLOADS = 256

# HOW MANY FRAMES THE CONTROL DRIVE NEEDS BEFORE "constant" MEANS ANYTHING.
#
# A byte seen twice is constant by luck. Without this bound the tool reports every rare message as
# a nav channel, because the navigating drive happened to catch it twice and the control once --
# a difference in SAMPLE SIZE read as a difference in behaviour, which is this fork's most-repeated
# measurement error and the reason denominators get stated here.
MIN_CONTROL_FRAMES = 200

# HOW MANY DISTINCT VALUES A BYTE MUST TAKE BEFORE IT IS A CANDIDATE.
#
# Added 2026-08-23 after this tool reported "SOMETHING CHANGED WITH A ROUTE ACTIVE" on ten addresses
# whose differing byte took TWO OR THREE values. That is two ordinary drives differing, not a nav
# channel -- and the verdict said otherwise in capital letters.
#
# The thing being hunted is a DISTANCE COUNTING DOWN toward a junction, which is the one behaviour
# nothing else in a payload has. A distance takes dozens of values over a route. A status bit takes
# two. Requiring breadth is what separates them, and without it the tool finds a "nav channel" on
# any pair of drives.
MIN_CANDIDATE_VALUES = 8


def seg_index(name: str) -> int:
  tail = name.rsplit("--", 1)[-1]
  return int(tail) if tail.isdigit() else -1


class Inventory:
  """What every (address, bus) did across a route.

  Per byte rather than per signal, deliberately: the whole point is to find something the DBC does
  not describe, and a signal-level view can only ever report what somebody already decoded.
  """

  def __init__(self, route: str):
    self.route = route
    self.segments = 0
    self.frames = 0
    self.count: dict[tuple[int, int], int] = defaultdict(int)
    self.payloads: dict[tuple[int, int], set] = defaultdict(set)
    self.saturated: set = set()
    # (addr, bus) -> list of 8 sets of byte values
    self.bytes: dict[tuple[int, int], list] = {}

  def add(self, addr: int, bus: int, dat: bytes) -> None:
    key = (addr, bus)
    self.count[key] += 1
    self.frames += 1
    if key not in self.saturated:
      self.payloads[key].add(dat)
      if len(self.payloads[key]) >= MAX_PAYLOADS:
        self.saturated.add(key)
    slots = self.bytes.get(key)
    if slots is None:
      slots = [set() for _ in range(8)]
      self.bytes[key] = slots
    for i, b in enumerate(dat[:8]):
      slots[i].add(b)

  def buses(self) -> set:
    return {bus for _, bus in self.count}

  def varying_bytes(self, key) -> list:
    slots = self.bytes.get(key)
    return [] if slots is None else [i for i, s in enumerate(slots) if len(s) > 1]


def read_route(route: str, realdata: str, max_segments: int | None) -> Inventory:
  from openpilot.tools.lib.logreader import LogReader

  inv = Inventory(route)
  segs = sorted([d for d in os.listdir(realdata) if d.startswith(route + "--")], key=seg_index)
  if max_segments is not None:
    segs = segs[:max_segments]
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
    inv.segments += 1
    for msg in lr:
      if msg.which() != "can":
        continue
      for c in msg.can:
        # Bus | 0x80 is panda's echo of OUR OWN transmissions. Counting it would report openpilot's
        # own frames as something the car started sending, which is how the 0x462 "bus 130" reading
        # was nearly misread once already.
        if c.src >= 0x80:
          continue
        inv.add(c.address, c.src, bytes(c.dat))
  return inv


def woke_up(nav: Inventory, ctl: Inventory) -> list:
  """(address, bus) pairs whose bytes moved in the NAV route and were pinned in the control.

  A FUNCTION RATHER THAN A LOOP INSIDE main() BECAUSE THE TEST HAS TO CALL THIS ONE. The first
  version of test_can_nav_diff.py reimplemented this logic in the test file, so when
  MIN_CANDIDATE_VALUES was added the suite stayed green while the new threshold went completely
  uncovered -- a test exercising its own copy of the code, which is the shape this fork records
  under "a stub laxer than the real thing hides the bug it was built to catch".
  """
  out = []
  for key in sorted(set(nav.count) & set(ctl.count)):
    if ctl.count[key] < MIN_CONTROL_FRAMES:
      continue
    nav_var, ctl_var = set(nav.varying_bytes(key)), set(ctl.varying_bytes(key))
    # Breadth, not just "it moved". See MIN_CANDIDATE_VALUES.
    gained = sorted(b for b in nav_var - ctl_var
                    if len(nav.bytes[key][b]) >= MIN_CANDIDATE_VALUES)
    if gained:
      out.append((key, gained))
  return out


def show_coverage(inv: Inventory, label: str) -> None:
  buses = ", ".join(f"bus {b}" for b in sorted(inv.buses())) or "NONE"
  print(f"  {label:<10} route {inv.route}  {inv.segments} segment(s)  "
        f"{inv.frames:,} frames  {len(inv.count)} (address, bus) pairs  [{buses}]")


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--nav", help="route recorded while a navigation app was ROUTING")
  ap.add_argument("--control", help="route recorded with no route active")
  ap.add_argument("--inventory", help="one route: just print what is on the wire")
  ap.add_argument("--realdata", default=REALDATA)
  ap.add_argument("--segments", type=int, default=None,
                  help="cap segments per route. Left off by default -- see the note it prints.")
  args = ap.parse_args()

  try:
    import openpilot.tools.lib.logreader  # noqa: F401
  except ImportError as e:
    sys.exit(f"no LogReader ({e}); see the docstring for the interpreter to use")
  if not os.path.isdir(args.realdata):
    sys.exit(f"no {args.realdata} -- run this on the device, or pass --realdata")

  if args.inventory:
    inv = read_route(args.inventory, args.realdata, args.segments)
    show_coverage(inv, "inventory")
    print()
    for (addr, bus), n in sorted(inv.count.items(), key=lambda kv: -kv[1]):
      payloads = ">=%d" % MAX_PAYLOADS if (addr, bus) in inv.saturated else len(inv.payloads[(addr, bus)])
      print(f"  0x{addr:03X} {addr:5d}  bus {bus}  {n:8,} frames  {payloads:>4} payloads  "
            f"bytes varying: {inv.varying_bytes((addr, bus))}")
    return 0

  if not (args.nav and args.control):
    raise SystemExit("pass --nav and --control (or --inventory)")

  if args.segments is not None:
    print(f"# CAPPED AT {args.segments} SEGMENTS PER ROUTE. The front segments of a route are the")
    print("# ones where the car is PARKED, so a cap understates how much anything varies -- which")
    print("# is exactly what this tool measures. Prefer the whole route.\n")

  nav = read_route(args.nav, args.realdata, args.segments)
  ctl = read_route(args.control, args.realdata, args.segments)

  print("=== coverage ===")
  show_coverage(nav, "NAVIGATING")
  show_coverage(ctl, "control")
  print()

  print("=== controls -- these MUST be present in BOTH or the diff says nothing ===")
  ok = True
  for addr, label in CONTROL_ADDRS.items():
    n = sum(v for (a, _), v in nav.count.items() if a == addr)
    c = sum(v for (a, _), v in ctl.count.items() if a == addr)
    flag = "" if (n and c) else "   <- MISSING"
    ok = ok and bool(n and c)
    print(f"  0x{addr:03X} {label:<38} nav {n:8,}   control {c:8,}{flag}")
  print()
  if not ok:
    print("A control address is missing from one of the routes, so the two are not comparable.")
    print("Either a route has no `can` stream logged or one is far too short. Fix that first;")
    print("every difference below would otherwise be a difference in what was RECORDED.")
    return 1

  nav_keys, ctl_keys = set(nav.count), set(ctl.count)

  print("=== addresses the NAVIGATING drive had and the control did not ===")
  new = sorted(nav_keys - ctl_keys, key=lambda k: -nav.count[k])
  if new:
    for addr, bus in new:
      known = APIM_ADDRS.get(addr, "")
      print(f"  0x{addr:03X} {addr:5d}  bus {bus}  {nav.count[(addr, bus)]:8,} frames   {known}")
  else:
    print("  (none)")
  print()

  print("=== addresses present in BOTH that WOKE UP while navigating ===")
  print("# A byte pinned in the control drive and moving in the navigating one. This is the case")
  print("# address-level presence cannot see, and the one APIM_Data_FD1 would land in.")
  woke = woke_up(nav, ctl)
  if woke:
    for (addr, bus), gained in sorted(woke, key=lambda kv: -len(kv[1])):
      known = APIM_ADDRS.get(addr, "")
      detail = ", ".join(
        f"b{i}:{len(nav.bytes[(addr, bus)][i])} values" for i in gained)
      print(f"  0x{addr:03X} {addr:5d}  bus {bus}  bytes {gained}  ({detail})   {known}")
  else:
    print("  (none)")
  print()
  print(f"# Only addresses with >= {MIN_CONTROL_FRAMES} control frames are compared, and a byte must")
  print(f"# take >= {MIN_CANDIDATE_VALUES} distinct values to count. A byte seen twice is constant by luck, and a")
  print("# byte with two or three values is a status flag, not a distance counting down.")
  print()

  print("=== the APIM's own traffic, named, present or not ===")
  for addr, label in APIM_ADDRS.items():
    n = {bus: v for (a, bus), v in nav.count.items() if a == addr}
    c = {bus: v for (a, bus), v in ctl.count.items() if a == addr}
    where = ", ".join(f"bus {b}: {v:,}" for b, v in sorted(n.items())) or "ABSENT"
    print(f"  0x{addr:03X} {addr:5d}  {label}")
    print(f"{'':14}nav {where}"
          f"{'' if not c else '   control ' + ', '.join(f'bus {b}: {v:,}' for b, v in sorted(c.items()))}")
  print()

  print("=== what this says ===")
  print("  FIRST, THE PREMISE: this is only a nav diff if the --nav route really was NAVIGATING.")
  print("  The tool cannot know that. If it was picked for being the newest route, everything")
  print("  below is two ordinary drives differing. Confirm with the driver before reading on.")
  print()
  if new or woke:
    print("  SOMETHING CHANGED WITH A ROUTE ACTIVE, on a bus openpilot already reads. Decode it")
    print("  next: dump the differing bytes over time against carState to see whether the value")
    print("  counts DOWN toward a junction, which is what a distance-to-maneuver does and what")
    print("  nothing else in a payload does. If it is real, route intent needs no canbox and no")
    print("  phone -- it is a source reading a bus we are already on.")
  else:
    print("  NOTHING navigation-shaped appeared on bus 0, 1 or 2. That is the expected answer and")
    print("  it is a LOCATION, not a dead end: the APIM-to-cluster traffic is on a bus the panda")
    print("  is not wired to, so route intent over CAN arrives WITH the canbox rather than after")
    print("  it -- the same canbox BLIS is waiting on.")
    print()
    print("  ONE CAVEAT BEFORE ACTING ON IT. Absence in a log is evidence about the log's")
    print("  conditions first. Check the coverage line above: if the navigating drive was short,")
    print("  or the two drives covered very different roads, the diff is noisier than it looks.")
    print("  Re-run over the whole of both routes before treating this as settled.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
