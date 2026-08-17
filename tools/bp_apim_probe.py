#!/usr/bin/env python3
"""FusionPilot: is the APIM actually transmitting? A direct measurement of U0253.

`U0253 - Lost Communication With Accessory Protocol Interface Module` is logged by the IPMA camera,
recurring, and it has never been explained. See `bluepilot/TSR-INVESTIGATION.md` section 6d.

The APIM puts three GPS messages on the bus, and `IPMA_ADAS` is a listed RECEIVER on all three --
the camera is supposed to take its position from SYNC. So the DTC is a claim that can be tested by
counting frames:

    PRESENT  -> the frames exist and the camera is rejecting or not receiving them for some other
                reason. A wiring fault is ruled out and the search moves to the camera's own config.
    ABSENT   -> nothing is transmitting them. The DTC is literal, and it is a routing or wiring
                fault rather than anything about TSR configuration.

Either answer narrows it, and this costs one route read. No as-built write, no FORScan, no GWM
change -- these are frames on a bus this fork already parses.

WHAT THIS IS NOT
----------------
It is NOT a search for a second speed-limit source. That question is CLOSED, from the DBC: the only
things the APIM broadcasts are a GPS receiver feed and route trivia (exterior light menus, distance
to stopover). No speed limit, no road class, no route geometry -- the navigation database stays
inside SYNC. Section 6d has the full inventory; do not re-derive it.

WHY THE TRANSMITTER FIELD IS NOT THE APIM
-----------------------------------------
`BO_ 1122 APIMGPS_Data_Nav_1_FD1: 8 GWM` -- the transmitter reads GWM, the gateway, because that is
what forwards them onto this bus. The DBC's transmitter column is the node that puts the frame on
THE MODELED BUS, not the module that originated the data. Misreading that column is exactly how
`AccTGap_D_Dsply` was recorded as unreadable for a week.

USAGE, on the device:

    cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 tools/bp_apim_probe.py
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

REALDATA = "/data/media/0/realdata"

# The APIM's own traffic. Names and contents from ford_lincoln_base_pt.dbc.
APIM_ADDRS = {
  1122: "APIMGPS_Data_Nav_1  lat/lon/hemispheres",
  1123: "APIMGPS_Data_Nav_2  UTC, PDOP, compass, GPS fault bit",
  1124: "APIMGPS_Data_Nav_3  speed, heading, altitude, HDOP/VDOP, sats",
  811: "APIM_Data_FD1       light menus, distance to stopover",
}
# Known-present controls. If these come back zero the probe itself is broken, not the car -- which
# is the failure mode that would otherwise read as "the APIM is silent".
CONTROL_ADDRS = {
  973: "Traffic_RecognitnData  (camera, known present and forwarded)",
  394: "ACCDATA_3              (camera, known present -- carries the ACC gap)",
  131: "Steering_Data_FD1      (buttons, known present)",
}


def seg_index(name: str) -> int:
  tail = name.rsplit("--", 1)[-1]
  return int(tail) if tail.isdigit() else -1


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--route", default=None)
  ap.add_argument("--segments", type=int, default=3)
  args = ap.parse_args()

  try:
    from openpilot.tools.lib.logreader import LogReader
  except ImportError as e:
    sys.exit(f"no LogReader ({e}); see the docstring for the interpreter to use")

  if not os.path.isdir(REALDATA):
    sys.exit(f"no {REALDATA} -- run this on the device")

  entries = [d for d in os.listdir(REALDATA) if "--" in d and seg_index(d) >= 0]
  if not entries:
    sys.exit("no route segments")
  route = args.route or max(
    {d.rsplit("--", 1)[0] for d in entries},
    key=lambda r: max(os.path.getmtime(os.path.join(REALDATA, d))
                      for d in entries if d.startswith(r + "--")))
  segs = sorted([d for d in entries if d.startswith(route + "--")], key=seg_index)[:args.segments]
  print(f"# route {route}, {len(segs)} segment(s)")

  # (address, bus) -> frames. Bus matters as much as presence: bus 0 is the powertrain side, bus 2
  # the camera side, and which one carries them says where the gateway is forwarding to.
  seen: dict[tuple[int, int], int] = defaultdict(int)
  total = 0
  for d in segs:
    p = os.path.join(REALDATA, d, "rlog")
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
        total += 1
        if c.address in APIM_ADDRS or c.address in CONTROL_ADDRS:
          seen[(c.address, c.src)] += 1

  print(f"# {total} CAN frames scanned\n")

  def report(title: str, addrs: dict[int, str]) -> dict[int, int]:
    print(f"=== {title} ===")
    totals = {}
    for addr, label in addrs.items():
      buses = {bus: n for (a, bus), n in seen.items() if a == addr}
      totals[addr] = sum(buses.values())
      where = ", ".join(f"bus {b}: {n}" for b, n in sorted(buses.items())) or "NOT PRESENT"
      print(f"  0x{addr:03X} {addr:5d}  {label:<52} {where}")
    print()
    return totals

  controls = report("controls -- these MUST be non-zero or the probe is wrong", CONTROL_ADDRS)
  apim = report("the APIM's own traffic", APIM_ADDRS)

  if not any(controls.values()):
    print("THE CONTROLS ARE ALL ZERO, so this route says nothing about the APIM. Either no `can`")
    print("stream was logged or the addresses moved. Fix the probe before believing the result.")
    return 1

  gps = sum(apim.get(a, 0) for a in (1122, 1123, 1124))
  print("=== verdict ===")
  if gps:
    print(f"  The APIM IS TRANSMITTING -- {gps} GPS frames reached a bus openpilot can see.")
    print("  So U0253 is NOT 'nothing is on the wire'. The frames exist and the camera is either")
    print("  not receiving them on its own bus or rejecting them. That points at the camera's")
    print("  configuration or the gateway's forwarding, and rules out an APIM wiring fault.")
  else:
    print("  The APIM IS SILENT on every bus openpilot can see, across this route.")
    print("  U0253 is then literal: the camera cannot hear the APIM because nothing is sending.")
    print("  That is a wiring or gateway-routing fault, and NOT a TSR configuration problem --")
    print("  so no as-built change to the camera or cluster could fix it.")
    print()
    print("  ONE CAVEAT BEFORE ACTING ON THIS: openpilot sees bus 0 and bus 2. If the APIM talks")
    print("  only on a bus the panda is not wired to, these frames would be absent while the")
    print("  module is perfectly healthy. Absence here narrows the search; it does not convict.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
