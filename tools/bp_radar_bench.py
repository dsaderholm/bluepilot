#!/usr/bin/env python3
"""FusionPilot: record the rear radar from a USB-CAN adapter, or read its angle live.

THE MISSING TOOL. `bp_rear_digest_sim.py` has always documented its input as "[[t, addr, hexbytes],
...] as bp_radar_bench writes it" and this file did not exist. So the reduction that the Teensy
firmware mirrors has never been replayed against a recording -- only against numbers read off a
bench session and typed in.

  python tools/bp_radar_bench.py --list
  python tools/bp_radar_bench.py --live                  # angle readout, for the two open questions
  python tools/bp_radar_bench.py --record cap.json --seconds 30
  python tools/bp_rear_digest_sim.py cap.json            # then replay it

RUN IT WITH THE 3.12 VENV -- ../.venv-bp312/Scripts/python.exe -- which is where python-can and
pyserial live. The device does not have python-can and does not need it; this is a bench tool.

THE TWO QUESTIONS --live EXISTS FOR, both open since the firmware was written and both answered by
watching one number while moving a corner reflector:

  1. THE AZIMUTH SIGN. The feeder and the simulator both compute `y_rel = -sin(az) * range`,
     inherited from the FRONT radar's decoder. A rear-facing sensor is rotated 180 degrees, which
     SUGGESTS the sign flips back -- and that reasoning is deliberately not shipped, because
     ESR.dbc and ford_fusion_2018_adas.dbc disagree about angle sign for this same hardware. Get it
     wrong and left and right are swapped: the veto guards the lane you are NOT entering, and
     everything downstream looks perfectly healthy while doing it.

     TO SETTLE IT: put a reflector clearly off ONE side and read the y column. Decide which
     physical side that is once, out loud, and write the answer into a comment.

  2. `AZIMUTH_OFFSET_RAD`, which is 0.0f in the firmware and has never been calibrated. Its own
     comment: "3 degrees is 2.6 m at 50 m." Put a reflector on the boresight and read `az`; whatever
     it reads is the offset.

NOTHING IS TRANSMITTED. The MRR free-runs -- proven on the bench 2026-08-14, all 64 detection
addresses plus the 0x174 header with nothing sent to it -- so this only ever listens. That also
means it is safe to point at a live bus by mistake, which is worth having.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from openpilot.tools.bp_rear_digest_sim import (  # noqa: E402
  EMPTY_PREFIX, LONG_RANGE_SCANS, MAX_RANGE_M, MIN_LONG_RANGE_DIST_M,
  MRR_END, MRR_HEADER, MRR_START, Detection, mrr_layout, sig,
)

BITRATE = 500000
DBC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                   "opendbc_repo", "opendbc", "dbc", "FORD_CADS.dbc")

# CANable / candleLight running slcan firmware, which is what presents as a COM port. Listed so an
# adapter that is NOT this one is obvious rather than silently assumed.
KNOWN = {(0x16D0, 0x117E): "CANable / candleLight (slcan)"}


def list_ports() -> int:
  import serial.tools.list_ports as lp
  ports = list(lp.comports())
  if not ports:
    print("no serial ports at all -- is the adapter plugged in?")
    return 1
  print(f"{len(ports)} serial port(s):")
  for p in ports:
    vid, pid = p.vid or 0, p.pid or 0
    known = KNOWN.get((vid, pid))
    mark = f"  <- {known}" if known else ""
    print(f"  {p.device:6s}  VID:PID {vid:04X}:{pid:04X}  {p.description}{mark}")
  print()
  print("Pass --channel COMn to pick one. --interface defaults to slcan.")
  return 0


def guess_channel() -> str | None:
  import serial.tools.list_ports as lp
  for p in lp.comports():
    if (p.vid, p.pid) in KNOWN:
      return p.device
  return None


def open_bus(args):
  import can
  ch = args.channel or guess_channel()
  if ch is None:
    sys.exit("no known USB-CAN adapter found. Run --list and pass --channel COMn.")
  print(f"opening {args.interface} on {ch} at {BITRATE} ...")
  # receive_own_messages off and no filters: this listens and never writes.
  return can.Bus(interface=args.interface, channel=ch, bitrate=BITRATE, receive_own_messages=False)


def decode_cycle(layout, cycle):
  """One radar cycle of raw frames -> detections. Same filters as the reference reduction.

  Deliberately calls the SAME `mrr_layout`/`sig` the simulator and bp_digest_pick_rule use, so a
  change to the decode reaches all three rather than drifting into a bench-only copy.
  """
  out = []
  for addr, raw in cycle:
    if not (MRR_START <= addr <= MRR_END) or len(raw) < 8 or raw[:2] == EMPTY_PREFIX:
      continue
    if not sig(raw, layout["VALID_LEVEL"]):
      continue
    scan = int(sig(raw, layout["SCAN_INDEX_2LSB"]))
    rng = sig(raw, layout["RANGE"])
    if rng <= 0.0 or rng > MAX_RANGE_M:
      continue
    if scan in LONG_RANGE_SCANS and rng < MIN_LONG_RANGE_DIST_M:
      continue
    az = sig(raw, layout["AZIMUTH"])
    out.append((az, Detection(d_rel=math.cos(az) * rng, y_rel=-math.sin(az) * rng,
                              v_rel=-sig(raw, layout["RANGE_RATE"]),
                              amplitude=sig(raw, layout["AMPLITUDE"]))))
  return out


def live(args) -> int:
  layout = mrr_layout(DBC)
  bus = open_bus(args)
  print()
  print("  STRONGEST return each cycle. Move a reflector to one side and watch `y`.")
  print("  y is computed as -sin(az)*range, the convention the firmware ships -- the question is")
  print("  which PHYSICAL side a positive y is. Decide it once and write it down.")
  print()
  print(f"  {'range':>7} {'az deg':>7} {'d_behind':>9} {'y':>7} {'closing':>8} {'ampl':>6}   n")
  cycle, last, quiet = [], 0.0, 0
  try:
    while True:
      msg = bus.recv(timeout=1.0)
      if msg is None:
        quiet += 1
        if quiet in (2, 10, 30):
          print(f"  ... nothing on the bus for {quiet}s "
                f"({'radar unpowered? wrong bitrate? wrong channel?' if quiet > 2 else 'waiting'})")
        continue
      quiet = 0
      if msg.arbitration_id == MRR_HEADER:
        dets = decode_cycle(layout, cycle)
        cycle = []
        now = time.monotonic()
        if dets and now - last > 0.25:      # 33 Hz is unreadable; a quarter second is not
          last = now
          az, d = max(dets, key=lambda p: p[1].amplitude)
          rng = math.hypot(d.d_rel, d.y_rel)
          print(f"  {rng:7.2f} {math.degrees(az):7.2f} {d.d_rel:9.2f} {d.y_rel:7.2f} "
                f"{d.v_rel:8.2f} {d.amplitude:6.0f}  {len(dets):3d}")
      elif MRR_START <= msg.arbitration_id <= MRR_END:
        cycle.append((msg.arbitration_id, bytes(msg.data)))
  except KeyboardInterrupt:
    print("\nstopped.")
  finally:
    bus.shutdown()
  return 0


def record(args) -> int:
  bus = open_bus(args)
  rows = []
  t0 = None
  deadline = time.monotonic() + args.seconds
  print(f"recording {args.seconds}s to {args.record} ... (ctrl-c to stop early)")
  try:
    while time.monotonic() < deadline:
      msg = bus.recv(timeout=1.0)
      if msg is None:
        continue
      a = msg.arbitration_id
      if not (MRR_START <= a <= MRR_END or a == MRR_HEADER):
        continue
      # SECONDS, relative to the first frame -- the simulator takes rows[-1][0] - rows[0][0] as the
      # capture span and divides frame counts by it, so a monotonic wall clock in seconds is the
      # contract. micros would report a 2000x reduction ratio and look like a triumph.
      t = msg.timestamp
      if t0 is None:
        t0 = t
      rows.append([round(t - t0, 6), a, bytes(msg.data).hex()])
  except KeyboardInterrupt:
    print("\nstopped early.")
  finally:
    bus.shutdown()

  if not rows:
    print("NOTHING CAPTURED. That is not an empty road -- it means no frames arrived at all.")
    print("Check the radar is powered, the bitrate is 500k, and --channel is the right port.")
    return 1
  with open(args.record, "w", encoding="utf-8") as f:
    json.dump(rows, f)
  span = rows[-1][0] - rows[0][0] or 1.0
  hdr = sum(1 for r in rows if r[1] == MRR_HEADER)
  print(f"wrote {args.record}: {len(rows)} frames, {span:.1f}s, {len(rows)/span:.0f} frames/s, "
        f"{hdr} radar cycles ({hdr/span:.1f} Hz)")
  print(f"  replay it:  python tools/bp_rear_digest_sim.py {args.record}")
  # ~2140 frames/s and ~33 Hz of cycles is what the bench measured on 2026-08-14. Printing the
  # comparison here means a half-connected capture is obvious now rather than after it has been
  # used to validate something.
  if len(rows) / span < 1500:
    print(f"  NOTE: {len(rows)/span:.0f} frames/s is well under the ~2140 measured on the bench --")
    print("  frames were dropped, or the radar is not in its normal scan mode.")
  return 0


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--list", action="store_true", help="show serial ports and exit")
  ap.add_argument("--live", action="store_true", help="continuous angle readout")
  ap.add_argument("--record", metavar="OUT.json", help="record a capture the simulator can replay")
  ap.add_argument("--seconds", type=float, default=30.0)
  ap.add_argument("--channel", default=None, help="COM port; auto-detected if omitted")
  ap.add_argument("--interface", default="slcan", help="python-can interface (default slcan)")
  args = ap.parse_args()

  if args.list:
    return list_ports()
  if args.live:
    return live(args)
  if args.record:
    return record(args)
  ap.print_help()
  return 2


if __name__ == "__main__":
  sys.exit(main())
