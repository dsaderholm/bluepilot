"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FusionPilot: tests for the ESP stream decoder.

Only the pure half is tested here, which is the whole reason the transport is split the way it is.
The serial shell opens a port and reads it; there is nothing in it to get subtly wrong that a mock
would catch honestly. The decoder is where framing, resynchronization and staleness live, and every
one of those has a failure mode that looks like working code:

  - a decoder that never resynchronizes appears fine until the first time it starts mid-packet,
    which is every single boot;
  - a decoder that holds its last value forever shows "no alerts" off a dead link, which reads
    exactly like a quiet road;
  - an unbounded buffer works perfectly until the day the cord is half seated.

The bytes fed in are the vendor's own example packet, so a change that breaks decoding fails here
rather than on the road.
"""

import time

from openpilot.sunnypilot.selfdrive.controls.lib.radar_detector.esp_protocol import (
  ARROW_FRONT, BAND_X, DEV_V1_CHECKSUM, TS3_SLICE_END_S, TS3_SLICE_START_S, mute_off, mute_on,
)
from openpilot.sunnypilot.selfdrive.controls.lib.radar_detector.transport import (
  MAX_TAIL, STALE_AFTER_S, EspSerialReader, EspStreamDecoder,
)

# ESP Specification 3_003.pdf, section 2, "Checksum infDisplayData Packet Example".
PACKET = bytes.fromhex("AAD8EA3109" "5B5B1F38280C0000" "E7" "AB")


def _other_accessory_packet():
  """A Concealed Display asking the V1 for its version -- real traffic on the same wire."""
  body = bytes([0xAA, 0xD0 + DEV_V1_CHECKSUM, 0xE0 + 0x00, 0x01, 0x01])
  return body + bytes([sum(body) & 0xFF, 0xAB])


class TestDecoding:
  def test_a_whole_packet_produces_a_display(self):
    d = EspStreamDecoder()
    d.feed(PACKET, now=100.0)
    disp = d.display(now=100.0)
    assert disp is not None
    assert disp.bars == 5
    assert disp.bands & BAND_X
    assert disp.arrows == ARROW_FRONT
    assert d.display_packets == 1

  def test_nothing_before_any_packet(self):
    d = EspStreamDecoder()
    assert d.display(now=100.0) is None
    assert not d.seen_a_detector

  def test_split_across_reads(self):
    """The normal case at 57600 baud, not an edge case."""
    d = EspStreamDecoder()
    d.feed(PACKET[:7], now=100.0)
    assert d.display(now=100.0) is None
    d.feed(PACKET[7:], now=100.1)
    assert d.display(now=100.1) is not None

  def test_starting_mid_packet_recovers(self):
    """Every boot starts mid-stream. A decoder that cannot resynchronize looks fine in a test that
    hands it whole packets and fails on the car every time."""
    d = EspStreamDecoder()
    d.feed(PACKET[6:] + PACKET, now=100.0)
    assert d.display(now=100.0) is not None

  def test_ignores_other_accessories_traffic(self):
    """A Concealed Display's request is framed correctly and is not ours to interpret."""
    d = EspStreamDecoder()
    d.feed(_other_accessory_packet(), now=100.0)
    assert d.frames == 1
    assert d.display_packets == 0
    assert d.display(now=100.0) is None

  def test_a_corrupt_packet_does_not_become_a_display(self):
    corrupt = bytearray(PACKET)
    corrupt[-2] ^= 0xFF
    d = EspStreamDecoder()
    d.feed(bytes(corrupt), now=100.0)
    assert d.display_packets == 0


class TestStaleness:
  def test_goes_stale_and_reports_nothing(self):
    """A link that died must not keep showing the last quiet frame."""
    d = EspStreamDecoder()
    d.feed(PACKET, now=100.0)
    assert d.display(now=100.0 + STALE_AFTER_S - 0.01) is not None
    assert d.display(now=100.0 + STALE_AFTER_S + 0.01) is None

  def test_a_new_packet_refreshes(self):
    d = EspStreamDecoder()
    d.feed(PACKET, now=100.0)
    d.feed(PACKET, now=100.0 + STALE_AFTER_S * 2)
    assert d.display(now=100.0 + STALE_AFTER_S * 2) is not None

  def test_seen_a_detector_survives_going_stale(self):
    """The diagnostic distinction: wrong port and wiring fault look the same as a detector that was
    switched off, unless something remembers that this port once spoke ESP."""
    d = EspStreamDecoder()
    d.feed(PACKET, now=100.0)
    assert d.display(now=1000.0) is None
    assert d.seen_a_detector

  def test_empty_feed_does_not_refresh(self):
    """Being called with no new bytes is not evidence of a live link."""
    d = EspStreamDecoder()
    d.feed(PACKET, now=100.0)
    d.feed(b"", now=100.0 + STALE_AFTER_S * 2)
    assert d.display(now=100.0 + STALE_AFTER_S * 2) is None


class FakeSerial:
  def __init__(self):
    self.written = b""

  def write(self, data):
    self.written += data

  def flush(self):
    pass


class TestTransmitting:
  """openpilot talks on this bus to mute the detector at learned false alarms. These cover the two
  things that could go badly: writing ESP packets at a device that is not a Valentine One, and
  talking out of turn."""

  def _live(self):
    r = EspSerialReader(port="/dev/null")
    r.decoder.feed(PACKET, now=100.0)
    return r

  def test_refuses_to_transmit_before_a_detector_has_been_seen(self):
    """The port is auto-detected on a device that may also carry a torque interceptor or a compute
    box. Writing at the wrong one is a genuinely bad outcome and costs one boolean to prevent."""
    r = EspSerialReader(port="/dev/null")
    assert not r.send(mute_on())
    assert "refusing to transmit" in r.last_error

  def test_transmits_once_the_port_has_spoken_esp(self):
    assert self._live().send(mute_on())

  def test_one_packet_outstanding_at_a_time(self):
    """Everything we send is idempotent, so dropping a duplicate is free -- and a queue that can
    grow is a queue that can back up behind a stalled port."""
    r = self._live()
    assert r.send(mute_on())
    assert not r.send(mute_on())

  def test_honors_the_holdoff_bit(self):
    """The V1 saying explicitly that no accessory may take a slice."""
    r = self._live()
    r.send(mute_on())
    r.decoder.slice_allowed = False
    r.decoder.last_display_t = time.monotonic() - (TS3_SLICE_START_S + TS3_SLICE_END_S) / 2
    ser = FakeSerial()
    r._service_tx(ser)
    assert ser.written == b""

  def test_waits_for_its_own_time_slice(self):
    """Device $03 owns 23.61 ms to 31.25 ms after the display packet. Talking outside that is what
    collides with another accessory."""
    r = self._live()
    r.send(mute_on())
    r.decoder.slice_allowed = True
    r.decoder.last_display_t = time.monotonic()      # zero elapsed: the slice has not opened
    ser = FakeSerial()
    r._service_tx(ser)
    assert ser.written == b""

  def test_sends_inside_the_slice(self):
    r = self._live()
    r.send(mute_on())
    r.decoder.slice_allowed = True
    r.decoder.last_display_t = time.monotonic() - (TS3_SLICE_START_S + TS3_SLICE_END_S) / 2
    ser = FakeSerial()
    r._service_tx(ser)
    assert ser.written == mute_on()
    assert r.sent == 1
    # ...and the outbox is clear, so the next mute is not blocked behind a delivered one.
    assert r.send(mute_off())

  def test_a_failed_write_clears_the_outbox_rather_than_wedging(self):
    """An adapter unplugged mid-write must not leave a packet stuck forever."""
    class Broken(FakeSerial):
      def write(self, data):
        raise OSError("gone")

    r = self._live()
    r.send(mute_on())
    r.decoder.slice_allowed = True
    r.decoder.last_display_t = time.monotonic() - (TS3_SLICE_START_S + TS3_SLICE_END_S) / 2
    r._service_tx(Broken())
    assert r.sent == 0
    assert r.send(mute_on())


class TestBufferIsBoundedByConstruction:
  """These replaced a MAX_BUFFER guard that could never fire.

  A half-seated cord produces a plausible header claiming a payload that never arrives, and the
  worry was an unbounded tail. It cannot happen: unframe() only holds bytes while the claimed
  length is still outstanding, so the tail is always shorter than one maximum-size packet, and once
  more than that accumulates the end-of-frame check fails and the parser resynchronizes. Asserting
  the invariant is worth more than a cap that looks like protection and is dead code.
  """

  # A plausible header with a payload length that never arrives -- the worst input for the parser.
  STUCK = bytes([0xAA, 0xD8, 0xEA, 0x31, 0xFF])

  def test_sustained_garbage_stays_under_one_packet(self):
    d = EspStreamDecoder()
    for _ in range(400):
      d.feed(self.STUCK, now=100.0)
      assert len(d._buf) <= MAX_TAIL

  def test_one_large_burst_of_garbage_stays_bounded(self):
    d = EspStreamDecoder()
    d.feed(self.STUCK * 300, now=100.0)
    assert len(d._buf) <= MAX_TAIL

  def test_recovers_within_about_one_packet_of_real_traffic(self):
    """The cost of a stuck candidate is latency, not a permanent stall -- it clears once enough
    bytes arrive for the end-of-frame check to run and fail. At 16 packets a second that is roughly
    one second, which is why the staleness window is where it is."""
    d = EspStreamDecoder()
    d.feed(self.STUCK, now=100.0)
    for _ in range(MAX_TAIL // len(PACKET) + 2):
      d.feed(PACKET, now=100.1)
    assert d.display(now=100.1) is not None

  def test_a_single_packet_behind_a_stuck_header_is_lost_not_fatal(self):
    """Stated so it is a known cost rather than a surprise: the packets that arrive during the
    resynchronization window are discarded with the garbage."""
    d = EspStreamDecoder()
    d.feed(self.STUCK, now=100.0)
    d.feed(PACKET, now=100.1)
    assert d.display(now=100.1) is None


class TestPortSelection:
  """Found on the real device, not by reasoning.

  A comma 3X has an internal Quectel EG25-G LTE modem that enumerates as FOUR usb-serial ports, so
  /dev/serial/by-id/ is never empty and the first entry alphabetically is the modem's own AT command
  port. The original find_port returned it. Nothing catastrophic followed -- send() refuses to
  transmit until the port has produced a Valentine One packet -- but it would have held the modem's
  control port open, never found the detector, and looked like "the radar feature does nothing".
  """

  REAL_C3X = [
    "/dev/serial/by-id/usb-Quectel_EG25-G-if00-port0",
    "/dev/serial/by-id/usb-Quectel_EG25-G-if01-port0",
    "/dev/serial/by-id/usb-Quectel_EG25-G-if02-port0",
    "/dev/serial/by-id/usb-Quectel_EG25-G-if03-port0",
  ]
  FTDI = "/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_A50285BI-if00-port0"

  def _patched(self, monkeypatch, entries):
    import glob as _glob
    monkeypatch.setattr(_glob, "glob", lambda pat: entries if "by-id" in pat else [])

  def test_never_returns_the_lte_modem(self, monkeypatch):
    self._patched(monkeypatch, self.REAL_C3X)
    assert EspSerialReader.find_port() is None

  def test_finds_the_adapter_alongside_the_modem(self, monkeypatch):
    """The real case once he plugs one in: the modem is still there and must be ignored."""
    self._patched(monkeypatch, sorted(self.REAL_C3X + [self.FTDI]))
    assert EspSerialReader.find_port() == self.FTDI

  def test_prefers_a_recognised_bridge_over_an_unknown_device(self, monkeypatch):
    unknown = "/dev/serial/by-id/usb-Some_Vendor_Widget-if00"
    self._patched(monkeypatch, sorted([unknown, self.FTDI]))
    assert EspSerialReader.find_port() == self.FTDI

  def test_still_takes_an_unknown_device_when_it_is_all_there_is(self, monkeypatch):
    """A bridge this list has never heard of is better than refusing to look."""
    unknown = "/dev/serial/by-id/usb-Some_Vendor_Widget-if00"
    self._patched(monkeypatch, [unknown])
    assert EspSerialReader.find_port() == unknown
