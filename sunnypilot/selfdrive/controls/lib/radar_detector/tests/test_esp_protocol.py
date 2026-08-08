"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

BluePilot: tests for the Valentine One ESP decoder.

The valuable cases here are the ones taken verbatim from Valentine Research's published
specification -- their two worked infDisplayData packets and their worked blink example. Those are
the only assertions in this file that can catch a decoder that is self-consistently wrong, which is
the failure mode that matters: a byte layout can be misread in a way that round-trips perfectly
through a test fixture we also wrote, and then produces confident nonsense against the real bus.

Everything else covers the bus tap's actual working conditions -- coming up mid-stream, packets
split across serial reads, and another accessory's traffic on the same wire.
"""

from openpilot.sunnypilot.selfdrive.controls.lib.radar_detector.esp_protocol import (
  ARROW_FRONT, BAND_KA, BAND_KU, BAND_K, BAND_X, DEV_GENERAL_BROADCAST, DEV_V1_CHECKSUM,
  DEV_V1_NO_CHECKSUM, MAX_BARS, OUR_DEVICE_ID, PID_INF_DISPLAY_DATA, PID_REQ_MUTE_ON,
  PID_RESP_ALERT_DATA, bar_graph_to_bars, build_frame, decode_alert_data, decode_display_data,
  mute_off, mute_on, seven_segment_to_digit, strength_to_bars, unframe,
)

# ESP Specification 3_003.pdf, section 2, "Non-Checksum infDisplayData Packet Example" and
# "Checksum infDisplayData Packet Example". Same display state, both packet formats.
SPEC_NO_CHECKSUM = bytes.fromhex("AA D8 E9 31 08 5B 5B 1F 38 28 0C 00 00 AB".replace(" ", ""))
SPEC_CHECKSUM = bytes.fromhex("AA D8 EA 31 09 5B 5B 1F 38 28 0C 00 00 E7 AB".replace(" ", ""))


def _only(buf):
  frames, tail = unframe(buf)
  assert len(frames) == 1, f"expected one frame, got {len(frames)} (tail {tail!r})"
  return frames[0]


class TestVendorExamples:
  """Against Valentine's own bytes. If these pass, the byte layout is right."""

  def test_non_checksum_example_frames(self):
    f = _only(SPEC_NO_CHECKSUM)
    assert f.packet_id == PID_INF_DISPLAY_DATA
    assert f.orig == DEV_V1_NO_CHECKSUM
    assert f.dest == DEV_GENERAL_BROADCAST
    assert f.from_v1
    assert f.payload == bytes.fromhex("5B5B1F38280C0000")

  def test_checksum_example_frames_and_strips_checksum(self):
    f = _only(SPEC_CHECKSUM)
    assert f.orig == DEV_V1_CHECKSUM
    # PL counts the checksum; the payload handed on must not include it, so both packet formats
    # present the same eight bytes to the decoder.
    assert f.payload == bytes.fromhex("5B5B1F38280C0000")

  def test_both_formats_decode_identically(self):
    assert decode_display_data(_only(SPEC_NO_CHECKSUM).payload) == \
           decode_display_data(_only(SPEC_CHECKSUM).payload)

  def test_display_state_from_spec_example(self):
    d = decode_display_data(_only(SPEC_CHECKSUM).payload)
    # Bogey counter image $5B is '2' under the standard seven-segment arrangement. This is the
    # check that the segment bit order is the standard one rather than some permutation.
    assert d.bogey_count == 2
    # Bar graph image $1F -- five contiguous bits, five LEDs, matching Table 9.1's row for 5.
    assert d.bars == 5
    # Image 1 $38 lights X, bit 4, and the front arrow.
    assert d.bands == BAND_X | BAND_KU
    assert d.arrows == ARROW_FRONT
    # Image 2 $28 clears bit 4, so bit 4 is the blinking (priority) indicator. This is the
    # evidence behind decoding bit 4 as a band on infDisplayData at all -- the spec's prose calls
    # it reserved, but their own example blinks it as the priority alert.
    assert d.priority_bands == BAND_KU
    assert d.priority_arrows == 0
    # Aux0 $0C: system status and display on, not muted.
    assert d.searching
    assert d.display_on
    assert not d.muted

  def test_blink_example_from_section_8(self):
    """Spec, "Reproducing the Valentine One Display": one X and one Ka to the front, Ka priority,
    encoded as image 1 = $2A and image 2 = $28."""
    payload = bytes([0x06, 0x06, 0x03, 0x2A, 0x28, 0x0C, 0x00, 0x00])
    d = decode_display_data(payload)
    assert d.bands == BAND_X | BAND_KA
    assert d.arrows == ARROW_FRONT
    assert d.priority_bands == BAND_KA
    assert sorted(d.band_names) == ["Ka", "X"]


class TestBuildingPackets:
  """Against the vendor's own worked request, because the payload-length rule is the easy thing to
  get wrong: with checksums, PL counts the checksum byte, so a request with no payload has PL = 1
  rather than 0. A packet the V1 rejects is silent -- it simply does not mute -- so nothing on the
  road would tell us this was wrong."""

  def test_matches_the_spec_worked_request(self):
    # ESP Specification 3_003.pdf, section 7: reqMaxSweepIndex from device $06 to the V1 ($0A).
    built = build_frame(dest=DEV_V1_CHECKSUM, pid=0x19, orig=0x06)
    assert built == bytes.fromhex("AADAE6190184AB")

  def test_payload_length_counts_the_checksum(self):
    built = build_frame(dest=DEV_V1_CHECKSUM, pid=0x19, orig=0x06)
    assert built[4] == 0x01          # no payload, but PL is 1
    assert len(built) == 7

  def test_non_checksum_form_drops_the_byte(self):
    built = build_frame(dest=DEV_V1_CHECKSUM, pid=0x19, orig=0x06, checksum=False)
    assert built[4] == 0x00
    assert built == bytes.fromhex("AADAE61900AB")

  def test_mute_on_is_addressed_from_our_third_party_id(self):
    """Table 3.4 reserves $03-$05 for third parties; every other id belongs to Valentine products.
    Sending from one of theirs would make us indistinguishable from a Concealed Display."""
    built = mute_on()
    assert built[2] == 0xE0 + OUR_DEVICE_ID
    assert built[3] == PID_REQ_MUTE_ON
    assert OUR_DEVICE_ID in (0x03, 0x04, 0x05)

  def test_built_packets_survive_our_own_parser(self):
    """The strongest cheap check available without hardware: what we emit, we can read back.

    Note the deliberate asymmetry, which this test pins so it stays deliberate: unframe() strips the
    checksum only from V1-ORIGINATED packets, so our own come back with it still on the payload. The
    V1 is the only originator whose payloads we interpret, and stripping generically would mean
    guessing whether a trailing byte is a checksum on a bus whose mode we have not yet observed.

    It matters because a single-wire half-duplex bus can echo our own transmission back at us -- so
    these packets DO arrive at the decoder, and must parse without being mistaken for detector data.
    """
    for pkt in (mute_on(), mute_off()):
      frames, tail = unframe(pkt)
      assert tail == b""
      assert len(frames) == 1
      assert frames[0].dest == DEV_V1_CHECKSUM
      assert frames[0].orig == OUR_DEVICE_ID
      assert not frames[0].from_v1          # our own echo is never mistaken for the detector
      assert len(frames[0].payload) == 1    # the unstripped checksum, see above

  def test_mute_on_and_off_are_different_packets(self):
    assert mute_on() != mute_off()


class TestFraming:
  def test_rejects_bad_checksum(self):
    corrupt = bytearray(SPEC_CHECKSUM)
    corrupt[-2] ^= 0xFF
    frames, _ = unframe(bytes(corrupt))
    assert frames == []

  def test_resynchronizes_after_garbage(self):
    """A tap does not get to start at a packet boundary."""
    frames, tail = unframe(b"\x11\x22\x33" + SPEC_CHECKSUM)
    assert len(frames) == 1
    assert tail == b""

  def test_stray_sof_in_leading_garbage_does_not_swallow_the_packet(self):
    # $AA is an ordinary payload byte, so a false start must cost one byte of resync, not a packet.
    frames, _ = unframe(b"\xAA\x00\xAA" + SPEC_CHECKSUM)
    assert len(frames) == 1

  def test_back_to_back_packets(self):
    frames, tail = unframe(SPEC_CHECKSUM + SPEC_CHECKSUM)
    assert len(frames) == 2
    assert tail == b""

  def test_split_across_reads_is_held_not_dropped(self):
    """At 57600 baud a packet spanning two serial reads is the normal case, not an edge case."""
    cut = 6
    frames, tail = unframe(SPEC_CHECKSUM[:cut])
    assert frames == []
    assert tail == SPEC_CHECKSUM[:cut]
    frames, tail = unframe(tail + SPEC_CHECKSUM[cut:])
    assert len(frames) == 1
    assert tail == b""

  def test_garbage_length_does_not_stall_the_parser(self):
    """A plausible header with an impossible length must be discarded, not waited on forever.

    Holding it as a tail would mean every subsequent read re-parsed the same wreckage and no real
    packet behind it was ever seen again.
    """
    bad = bytes([0xAA, 0x00, 0x00, 0x31, 0xFF])
    frames, tail = unframe(bad + SPEC_CHECKSUM)
    assert len(frames) == 1
    assert tail == b""

  def test_other_accessory_traffic_is_framed_but_marked_not_from_v1(self):
    """The bus is shared. We must parse a Concealed Display's request without mistaking it for
    detector data."""
    pkt = bytes([0xAA, 0xD0 + DEV_V1_CHECKSUM, 0xE0 + 0x00, 0x01, 0x00, 0xAB])
    f = _only(pkt)
    assert not f.from_v1
    assert f.orig == 0x00


class TestStrengthScale:
  def test_bar_graph_popcount(self):
    assert bar_graph_to_bars(0x00) == 0
    assert bar_graph_to_bars(0x01) == 1
    assert bar_graph_to_bars(0x1F) == 5
    assert bar_graph_to_bars(0xFF) == MAX_BARS

  def test_table_9_1_ka_boundaries(self):
    assert strength_to_bars(BAND_KA, 0xFF) == 8
    assert strength_to_bars(BAND_KA, 0xBA) == 8   # inclusive lower bound of the 8-LED row
    assert strength_to_bars(BAND_KA, 0xB9) == 7   # one below drops a bar
    assert strength_to_bars(BAND_KA, 0x90) == 2
    assert strength_to_bars(BAND_KA, 0x8F) == 1
    assert strength_to_bars(BAND_KA, 0x01) == 1
    assert strength_to_bars(BAND_KA, 0x00) == 0

  def test_table_9_1_x_and_k_boundaries(self):
    assert strength_to_bars(BAND_X, 0xD0) == 8
    assert strength_to_bars(BAND_X, 0xCF) == 7
    assert strength_to_bars(BAND_K, 0xC2) == 8
    assert strength_to_bars(BAND_K, 0xC1) == 7
    # Ku shares the K row in Table 9.1.
    assert strength_to_bars(BAND_KU, 0xC2) == 8

  def test_scale_is_band_dependent(self):
    """The whole reason strength comparisons must go through the table: the same raw byte is a
    different number of bars depending on the band it came from."""
    assert strength_to_bars(BAND_KA, 0xBA) == 8
    assert strength_to_bars(BAND_X, 0xBA) == 5

  def test_unknown_band_falls_back_to_ka(self):
    """Over-reporting strength is the safe direction; it can never hide a strong signal."""
    assert strength_to_bars(0, 0xBA) == 8


class TestSevenSegment:
  def test_digits(self):
    assert seven_segment_to_digit(0x3F) == 0
    assert seven_segment_to_digit(0x5B) == 2
    assert seven_segment_to_digit(0x7F) == 8

  def test_decimal_point_is_ignored(self):
    assert seven_segment_to_digit(0x5B | 0x80) == 2

  def test_non_digit_pattern_reads_as_zero(self):
    """A half-lit transitional frame is normal on a live bus and must not raise."""
    assert seven_segment_to_digit(0x41) == 0
    assert seven_segment_to_digit(0x00) == 0


class TestAlertTable:
  def test_decodes_a_priority_ka_alert(self):
    # index 1 of 2, 34.700 GHz, front strength $BA (8 bars on Ka), Ka band, front arrow, priority.
    payload = bytes([0x12, 0x87, 0x8C, 0xBA, 0x00, BAND_KA | ARROW_FRONT, 0x80])
    a = decode_alert_data(payload)
    assert a.index == 1
    assert a.count == 2
    assert a.frequency_mhz == 34700
    assert a.band == BAND_KA
    assert a.arrows == ARROW_FRONT
    assert a.priority
    assert a.bars == 8

  def test_bars_take_the_stronger_antenna(self):
    payload = bytes([0x11, 0x87, 0x8C, 0x01, 0xBA, BAND_KA, 0x00])
    assert decode_alert_data(payload).bars == 8

  def test_empty_table_is_a_valid_record(self):
    """The V1 sends index 0 / count 0 periodically to say there is nothing. That is data, not a
    malformed packet."""
    a = decode_alert_data(bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]))
    assert a is not None
    assert a.count == 0
    assert a.bars == 0

  def test_short_payloads_return_none(self):
    assert decode_alert_data(b"\x00" * 6) is None
    assert decode_display_data(b"\x00" * 5) is None

  def test_alert_packet_framing_round_trip(self):
    body = bytes([0xAA, 0xD0 + DEV_GENERAL_BROADCAST, 0xE0 + DEV_V1_CHECKSUM,
                  PID_RESP_ALERT_DATA, 0x08, 0x11, 0x87, 0x8C, 0xBA, 0x00,
                  BAND_KA | ARROW_FRONT, 0x80])
    pkt = body + bytes([sum(body) & 0xFF, 0xAB])
    f = _only(pkt)
    assert f.packet_id == PID_RESP_ALERT_DATA
    assert decode_alert_data(f.payload).band == BAND_KA
