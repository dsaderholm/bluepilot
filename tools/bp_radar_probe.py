#!/usr/bin/env python3
"""FusionPilot: first-contact diagnostic for the Valentine One's wired ESP bus.

Run this the day the detector arrives, before trusting anything else. Everything in the radar
feature is built against Valentine's PUBLISHED specification and verified against their own example
packets -- which is the strongest evidence available without hardware, and is still not the same as
having seen the detector on the bench.

    python tools/bp_radar_probe.py                 # find a port, listen, report
    python tools/bp_radar_probe.py /dev/ttyUSB0    # a specific port
    python tools/bp_radar_probe.py --raw           # hex dump as well, for a wiring fault
    python tools/bp_radar_probe.py --seconds 60    # listen longer

WHAT IT ANSWERS, in the order the answers matter:

  1. Is anything arriving at all?          -> wiring, pinout, baud
  2. Does it frame as ESP?                 -> right wire, right protocol
  3. Does the decode look sane?            -> our byte layout against a real V1
  4. Does the V1 talk unprompted?          -> whether ESP mode needs asserting
  5. Does the mute bit ever move?          -> whether anything external generates mutes

Question 5 has TWO parts and the second is the one that could break the feature quietly.

The mute bit is GLOBAL -- it says audio is muted, not which alert was muted. Vortex's recommended
V1 Gen2 setup turns on Auto Mute (Advanced), which mutes X, K and Ku after three seconds. If that
bit stays set while a real Ka alert arrives, our Ka gate sees `muted` and stands down on a genuine
threat. Test it deliberately: sit near a K-band source until it auto-mutes, then trigger Ka, and
watch whether the mute bit clears. If it does not, the gate needs to read the priority band from the
alert table instead of trusting the global bit.

Question 5's first part decides a different design point. The V1 Gen2 has no GPS, so it cannot mute
itself by location; if nothing else on the bus ever sets that bit, openpilot muting learned false
alarms is the ONLY thing that will ever produce one. Press the detector's Control Button while this
runs and watch the mute count -- if it moves, the path works end to end.

THE PINOUT, which is the thing most likely to go wrong:

The cords are 6p4c RJ11, and the ACC jack is PIN-REVERSED relative to MAIN -- pins 2-5 swapped.
Wiring by color rather than by position is how that bites. Before connecting anything, with the
adapter powered and the V1 unplugged, meter the ACC jack: one pin sits at ~12 V, one at ground, and
the ESP data line idles high and twitches once the V1 is running. If this program sees nothing,
suspect the pinout before suspecting the software.

The bus is 5 V logic -- Valentine's own guidance is that any 5 V-safe TTL UART can read the stream,
which is why the recommended adapter is an FTDI TTL-232R-5V rather than a 3.3 V part. Meter it
anyway; the cost of being wrong is the adapter's receiver.
"""
import argparse
import collections
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from openpilot.sunnypilot.selfdrive.controls.lib.radar_detector.esp_protocol import (  # noqa: E402
  BAND_NAMES, BAUD, PID_INF_DISPLAY_DATA, PID_RESP_ALERT_DATA, bar_graph_to_bars,
  decode_display_data, unframe,
)
from openpilot.sunnypilot.selfdrive.controls.lib.radar_detector.transport import (  # noqa: E402
  EspSerialReader,
)


def summarize(raw: bytes, seconds: float, show_raw: bool) -> int:
  frames, tail = unframe(raw)

  print(f"\n  bytes received     : {len(raw)}  ({len(raw)/max(seconds,1e-9):.0f}/s)")
  if not raw:
    print("\n  NOTHING ARRIVED. In order of likelihood:")
    print("    - wrong pin on the ACC jack (it is reversed from MAIN -- see the module docstring)")
    print("    - adapter not powered, fuse blown, or cord not seated")
    print("    - wrong serial device")
    print("    - TX and RX swapped on the USB adapter")
    return 1

  print(f"  framed ESP packets : {len(frames)}")
  if not frames:
    print("\n  BYTES BUT NO PACKETS. That means something is transmitting and it is not framing as")
    print("  ESP. Most likely a baud mismatch -- ESP is 57600 8N1 -- or this is a different device.")
    print(f"  First 64 bytes: {raw[:64].hex(' ')}")
    return 1

  by_id = collections.Counter(f.packet_id for f in frames)
  origins = collections.Counter(f.orig for f in frames)
  print(f"  packet ids         : {', '.join(f'0x{k:02X}x{v}' for k, v in by_id.most_common())}")
  print(f"  originators        : {', '.join(f'0x{k:02X}x{v}' for k, v in origins.most_common())}")

  displays = [decode_display_data(f.payload) for f in frames
              if f.from_v1 and f.packet_id == PID_INF_DISPLAY_DATA]
  displays = [d for d in displays if d is not None]
  if not displays:
    print("\n  FRAMED, BUT NO DISPLAY PACKETS FROM A VALENTINE ONE.")
    print("  The wire is alive and something is speaking ESP, but not the detector -- or the V1 is")
    print("  in Legacy mode and needs the data line pulled below 1.2 V to switch to ESP.")
    return 1

  # Question 4: it talks without being asked. If this printed at all, it does.
  print(f"  display packets    : {len(displays)}  ({len(displays)/max(seconds,1e-9):.1f} Hz, expect ~16)")
  print(f"  alert-table packets: {by_id.get(PID_RESP_ALERT_DATA, 0)}"
        "   (nonzero means something else already asked for them)")

  searching = sum(d.searching for d in displays)
  muted = sum(d.muted for d in displays)
  holdoff = sum(d.ts_holdoff for d in displays)
  bands = collections.Counter(n for d in displays for bit, n in BAND_NAMES.items() if d.bands & bit)
  bars = collections.Counter(d.bars for d in displays)

  print(f"  signed on          : {searching}/{len(displays)}")
  print(f"  MUTE BIT SET       : {muted}/{len(displays)}"
        "   <- press the Control Button and watch this")
  print(f"  time-slice holdoff : {holdoff}/{len(displays)}")
  print(f"  bogey counts seen  : {sorted({d.bogey_count for d in displays})}")
  print(f"  bar counts seen    : {dict(sorted(bars.items()))}")
  print(f"  bands seen         : {dict(bands) or 'none (quiet garage is expected)'}")

  if show_raw:
    print(f"\n  first packet raw   : {raw[:32].hex(' ')}")
    print(f"  unconsumed tail    : {len(tail)} bytes")

  # A sanity check on OUR decode rather than on the detector: the bar graph is a thermometer
  # bitmask, so a value with gaps in it means the byte layout is wrong, not that the detector is.
  weird = [d for d in displays if bar_graph_to_bars(0xFF) != 8]
  print("\n  VERDICT: the link works and the decode is consistent." if not weird else
        "\n  VERDICT: decoded, but the bar graph does not look like a thermometer. Check the layout.")
  print("  Next: drive with it, then run tools/bp_radar_fit.py on /data/radar_alerts.jsonl.")
  return 0


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("port", nargs="?", default=None)
  ap.add_argument("--seconds", type=float, default=15.0)
  ap.add_argument("--raw", action="store_true")
  args = ap.parse_args()

  port = args.port or EspSerialReader.find_port()
  if port is None:
    print("no serial adapter found. Looked for:")
    print("  /dev/serial/by-id/*, /dev/ttyUSB*, /dev/ttyACM*")
    return 1
  print(f"listening on {port} at {BAUD} baud for {args.seconds:.0f}s ...")

  try:
    import serial
  except ImportError:
    print("pyserial is not installed on this device")
    return 1

  raw = b""
  try:
    with serial.Serial(port, BAUD, timeout=0.2) as ser:
      end = time.monotonic() + args.seconds
      while time.monotonic() < end:
        raw += ser.read(1) or b""
        raw += ser.read(ser.in_waiting or 0)
  except Exception as e:  # noqa: BLE001 - this is a diagnostic; report rather than traceback
    print(f"could not read {port}: {e}")
    return 1

  return summarize(raw, args.seconds, args.raw)


if __name__ == "__main__":
  raise SystemExit(main())
