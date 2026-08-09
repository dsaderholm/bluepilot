"""
FusionPilot: reading the Valentine One's ESP bus off a USB serial adapter.

Split deliberately in two:

  EspStreamDecoder -- pure. Bytes in, the current front-panel state out, plus whether that state is
    still fresh. No I/O, no threads, no clock of its own. Everything that can be got wrong about
    framing, resynchronization and staleness lives here, where a test can reach it.

  EspSerialReader -- the thin shell. Opens the port, reads, hands bytes to the decoder, reopens
    when the adapter disappears, and writes the one packet this feature ever sends. Nothing here
    decides WHAT to send or WHEN it is warranted; it only decides whether the bus is safe to talk
    on right now. See send() for the never-transmit-at-a-stranger rule and _service_tx for the
    honest limits of the time-slice timing.

That split is the point. Nothing renders or drives offline, so the only defense against a subtly
wrong transport is that the interesting half is a pure function of its input.

WHY A THREAD AND NOT A SERVICE
------------------------------
This runs as a reader thread inside the process that owns the resolver rather than as its own
daemon publishing a cereal message. The bus is 57600 baud -- about 5.7 kB/s, one infDisplayData
packet roughly every 62.5 ms -- and there is exactly one consumer. A new service would mean a
capnp message, a services entry, a manager process and a parity test, all to move a handful of
bytes between two objects that could hold a reference to each other.

The thread blocks in pyserial's read with a timeout, which releases the GIL, so it does not
compete with the control loop for anything except the brief moment it appends to a buffer.

If a second consumer ever appears -- the onroad UI wanting the readout in its own process, say --
this becomes the wrong answer and it should be promoted to a service then, not pre-emptively.

STALENESS IS NOT AN ERROR CASE
------------------------------
The V1 broadcasts infDisplayData continuously whenever it is powered and signed on. So silence is
information: the adapter is unplugged, the detector is off, the fuse went, the cord worked loose.
The decoder reports the last state as None once it goes stale rather than holding it, because a
readout that keeps showing "no alerts" off a link that died ten minutes ago is worse than one that
says nothing -- it is a display that reads exactly like a quiet road.
"""

import os
import threading
import time

from openpilot.sunnypilot.selfdrive.controls.lib.radar_detector.esp_protocol import (
  BAUD, PID_INF_DISPLAY_DATA, TS3_SLICE_END_S, TS3_SLICE_START_S, DisplayData,
  decode_display_data, unframe,
)

# The V1 sends one infDisplayData per time slice cycle, and Table 3.4 puts a full cycle at 62.5 ms
# -- about 16 Hz. A second of silence is thirty missed packets; nothing transient looks like that.
STALE_AFTER_S = 1.0

# The unconsumed buffer is bounded by construction, so nothing here caps it.
#
# This started life as a MAX_BUFFER guard against a half-seated cord producing bytes that never
# complete a packet. Writing the test for it showed the guard could never fire: unframe() only
# holds bytes when the claimed payload length has NOT yet arrived, so the tail it returns is always
# shorter than one maximum-size packet -- 5 header bytes plus a payload length of at most 255 plus
# the end-of-frame byte. Once more than that has accumulated, the end-of-frame check runs, fails,
# and the parser advances a byte and resynchronizes.
#
# So the worst case is bounded at 261 bytes and self-heals within roughly that many bytes of
# subsequent traffic -- about a second at 16 packets per second. A cap would have been dead code
# that looked like protection, which is worse than the invariant it was guarding.
MAX_TAIL = 261

# Where the adapter shows up. by-id first because it survives reboots and other USB devices coming
# and going, which /dev/ttyUSB0 does not -- and this shares a port with whatever else the owner has
# plugged in.
PORT_GLOBS = ("/dev/serial/by-id/*", "/dev/ttyUSB*", "/dev/ttyACM*")

# Devices on this bus that are definitely not a radar detector.
#
# The comma 3X's internal Quectel EG25-G LTE modem enumerates as ttyUSB0..3 and fills
# /dev/serial/by-id/ before anything is even plugged in. Confirmed on the device 2026-08-07, not
# assumed. Anything added here needs the same standard: something actually seen, not something
# imagined.
PORT_EXCLUDE = ("QUECTEL", "EG25", "TTYUSB0", "TTYUSB1", "TTYUSB2", "TTYUSB3")

# USB-serial bridges worth preferring when several candidates survive.
#
# FTDI FIRST AND, ON THIS DEVICE, FTDI ONLY. AGNOS's kernel registers ftdi_sio and option and
# nothing else -- CP210X, CH341, PL2303 and even USB_SERIAL_GENERIC are all "is not set" in
# /proc/config.gz, and there is no /lib/modules directory, so no driver can be loaded later. A
# CP2102 or CH340 adapter will not enumerate at all on this hardware. The rest are listed because
# this file should not silently assume one device's kernel forever.
BRIDGE_HINTS = ("FTDI", "FT232", "CP210", "CH340", "CH341", "PL2303", "USB-SERIAL", "USB_SERIAL")


def _is_excluded(path: str) -> bool:
  upper = path.upper()
  return any(k in upper for k in PORT_EXCLUDE)

READ_TIMEOUT_S = 0.2
REOPEN_DELAY_S = 2.0


class EspStreamDecoder:
  """Bytes off the bus -> the V1's current front panel, or None when that has gone stale."""

  def __init__(self):
    self._buf = b""
    self._display: DisplayData | None = None
    self._last_packet_t = 0.0
    # When the last infDisplayData finished arriving, and whether accessories were allowed a time
    # slice after it. Both are needed to know when it is our turn to talk -- see EspSerialReader.
    self.last_display_t = 0.0
    self.slice_allowed = False
    # Diagnostics. Cheap to keep and the only way to tell "nothing is connected" from "connected to
    # something that is not a Valentine One" without a scope.
    self.frames = 0
    self.display_packets = 0

  def feed(self, data: bytes, now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    if data:
      self._buf += data

    frames, self._buf = unframe(self._buf)

    for f in frames:
      self.frames += 1
      # Only the detector's own display packets. Another accessory's traffic is on the same wire
      # and is not ours to interpret.
      if f.from_v1 and f.packet_id == PID_INF_DISPLAY_DATA:
        decoded = decode_display_data(f.payload)
        if decoded is not None:
          self._display = decoded
          self._last_packet_t = now
          self.display_packets += 1
          # Time slices are measured from the end of this packet, and the V1 can forbid them
          # outright via the holdoff bit. Recorded here because this is the only moment that knows
          # when the packet landed.
          self.last_display_t = now
          self.slice_allowed = not decoded.ts_holdoff

  def display(self, now: float | None = None) -> DisplayData | None:
    """The current front panel, or None if the link has gone quiet. See the module docstring."""
    now = time.monotonic() if now is None else now
    if self._display is None:
      return None
    if now - self._last_packet_t > STALE_AFTER_S:
      return None
    return self._display

  @property
  def seen_a_detector(self) -> bool:
    """Has anything on this port ever looked like a Valentine One?

    Distinct from `display() is not None`, and the distinction is what a diagnostic needs: a port
    that has never produced a display packet is the wrong port or a wiring fault, while one that
    has and then stopped is a detector that was switched off or a cord that came loose.
    """
    return self.display_packets > 0


class EspSerialReader:
  """Owns the port and a reader thread. Decides nothing."""

  def __init__(self, port: str | None = None):
    self.decoder = EspStreamDecoder()
    self.port = port
    self.connected = False
    self.last_error = ""
    self.sent = 0
    self._outbox: bytes | None = None
    self._tx_lock = threading.Lock()
    self._stop = threading.Event()
    self._thread: threading.Thread | None = None

  @staticmethod
  def find_port() -> str | None:
    """First plausible serial adapter, by-id preferred. None when there is nothing plugged in.

    Deliberately not a setting. A device path is not something worth asking the owner to type into
    a settings screen, and it would be one more param to declare, migrate and explain -- for an
    answer that is discoverable and that changes on its own when the USB layout changes.

    THE MODEM IS ALREADY ON THIS BUS, and the first version of this returned it. A comma 3X has an
    internal Quectel EG25-G LTE modem that enumerates as FOUR usb-serial ports, so
    /dev/serial/by-id/ is never empty and sorted()[0] is
    usb-Quectel_EG25-G-if00-port0 -- the modem's own AT command port. Found by looking at the actual
    device rather than by reasoning about it.

    Nothing catastrophic would have followed, because send() refuses to transmit until the port has
    produced a Valentine One display packet, so we would never have typed AT commands at the LTE
    modem. But we would have held its control port open and never found the detector, and the
    symptom would have been "the radar feature does nothing" with no clue pointing at the modem.

    So: skip anything known not to be a detector, then prefer a real USB-serial bridge by name.
    """
    import glob
    for pattern in PORT_GLOBS:
      matches = [m for m in sorted(glob.glob(pattern)) if not _is_excluded(m)]
      if not matches:
        continue
      # Prefer a recognised bridge over an unknown device on the same bus.
      for m in matches:
        if any(k in m.upper() for k in BRIDGE_HINTS):
          return m
      return matches[0]
    return None

  def start(self) -> None:
    if self._thread is not None:
      return
    self._stop.clear()
    self._thread = threading.Thread(target=self._run, name="esp_reader", daemon=True)
    self._thread.start()

  def stop(self, timeout: float = 1.0) -> None:
    self._stop.set()
    if self._thread is not None:
      self._thread.join(timeout=timeout)
      self._thread = None

  def display(self) -> DisplayData | None:
    return self.decoder.display()

  def send(self, data: bytes) -> bool:
    """Queue one packet for the bus. Returns whether it was accepted.

    REFUSED UNLESS THIS PORT HAS ALREADY SPOKEN ESP. The port is auto-detected, and on a device
    that may also carry a torque interceptor or a compute box there is a real chance of picking the
    wrong one. Writing ESP packets at some other device is a genuinely bad outcome, and it costs one
    boolean to make it impossible: we only ever transmit somewhere we have already received a valid
    Valentine One display packet from.

    Also refused when nothing is queued yet -- one outstanding packet at a time. Everything we send
    is idempotent (mute on, mute off), so dropping a duplicate is free and a queue that can grow is
    a queue that can back up behind a stalled port.
    """
    if not self.decoder.seen_a_detector:
      self.last_error = "refusing to transmit: no Valentine One seen on this port"
      return False
    with self._tx_lock:
      if self._outbox is not None:
        return False
      self._outbox = data
    return True

  def _service_tx(self, ser) -> None:
    """Write a queued packet if this looks like our turn to talk.

    HONEST ABOUT THE TIMING. The spec gives device $03 a slice from 23.61 ms to 31.25 ms after the
    end of the V1's infDisplayData packet -- a 7.6 ms window. Python, USB serial buffering and a
    non-realtime thread cannot hit that reliably, and pretending otherwise would be worse than
    saying so.

    What makes it acceptable here rather than reckless:

      - We are the only wired accessory. The bus is designed so several can share it, but on this
        car nothing else is plugged into the ACC jack, so the only other talker is the V1 itself.
      - Everything we send is one short packet, sent rarely -- a mute when entering a learned false
        alarm, not a stream.
      - A collision corrupts a packet, and every packet carries a checksum. The V1 discards ours and
        we discard its, which costs one display update out of sixteen a second.
      - We honor the holdoff bit, which is the V1 explicitly saying not now.

    So the failure mode is a dropped packet, not a wedged bus. If a second accessory ever shares the
    wire this needs revisiting, which is why the reasoning is here rather than in a commit message.
    """
    with self._tx_lock:
      pending = self._outbox
    if pending is None:
      return

    dec = self.decoder
    if not dec.slice_allowed:
      return
    since = time.monotonic() - dec.last_display_t
    if not (TS3_SLICE_START_S <= since <= TS3_SLICE_END_S):
      return

    try:
      ser.write(pending)
      ser.flush()
      self.sent += 1
    except Exception as e:  # noqa: BLE001 - a failed write is the same as an unplugged adapter
      self.last_error = f"write failed: {e}"
    finally:
      with self._tx_lock:
        self._outbox = None

  def _open(self):
    """Open the port, or return None. Never raises -- a missing adapter is an expected state.

    pyserial is imported here rather than at module scope so this file stays importable in the
    offline test environment and on any device where the package is not installed. The decoder is
    the half worth testing and it must not need a serial library to be reachable.
    """
    try:
      import serial
    except ImportError:
      self.last_error = "pyserial not installed"
      return None

    port = self.port or self.find_port()
    if port is None:
      self.last_error = "no serial adapter found"
      return None
    if not os.path.exists(port):
      self.last_error = f"{port} is gone"
      return None

    try:
      return serial.Serial(port, BAUD, timeout=READ_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 - any open failure is the same "not connected" state
      self.last_error = f"{port}: {e}"
      return None

  def _run(self) -> None:
    ser = None
    while not self._stop.is_set():
      if ser is None:
        ser = self._open()
        if ser is None:
          self.connected = False
          # Wait on the stop event rather than sleeping, so shutdown does not have to sit through
          # the retry delay.
          self._stop.wait(REOPEN_DELAY_S)
          continue
        self.connected = True
        self.last_error = ""

      try:
        # read(1) blocks up to the timeout, then in_waiting drains whatever else arrived in one go.
        # Reading a fixed large block instead would either add the full timeout to every packet's
        # latency or spin.
        data = ser.read(1)
        if data:
          data += ser.read(ser.in_waiting or 0)
        self.decoder.feed(data)
        self._service_tx(ser)
      except Exception as e:  # noqa: BLE001 - unplugged mid-read is normal, not exceptional
        self.last_error = f"read failed: {e}"
        self.connected = False
        try:
          ser.close()
        except Exception:  # noqa: BLE001
          pass
        ser = None

    if ser is not None:
      try:
        ser.close()
      except Exception:  # noqa: BLE001
        pass
    self.connected = False
