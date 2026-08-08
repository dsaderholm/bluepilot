"""
BluePilot: decoder for Valentine Research's Extended Serial Protocol (ESP).

This is the wire format spoken by a Valentine One on its wired accessory bus -- the ACC jack on
the Lighter Adapter or the Accessory jack on the Direct-Wire Power Adapter. Everything here is
transcribed from Valentine Research's own published specification, "ESP Specification 3_003.pdf",
which ships in their MIT-licensed AndroidESPLibrary repository. Nothing in this file is inferred
from a packet capture; where the observed behavior of the vendor's own example packets disagrees
with their prose, that is called out at the line it affects.

WHY THE WIRED BUS AND NOT BLUETOOTH
-----------------------------------
The V1 Gen2 has Bluetooth built in, but it accepts one Bluetooth device at a time, and on this car
that slot belongs to the Highway Radar app on the owner's phone -- which also carries Waze alerts
openpilot deliberately does not consume. The wired ESP bus is a second, independent channel, and
the protocol is designed for several accessories to share it (time slices TS0-TS7, section 3 of
the spec). So openpilot listens on the wire and the phone keeps the radio.

It also removes the single largest unknown in this feature. openpilot has no Bluetooth code
anywhere in the tree and AGNOS is not known to bring the comma 3X's radio up at all; a USB serial
adapter is a far shorter path than putting a static BlueZ build in a read-only filesystem.

MOSTLY LISTENING, AND EXACTLY ONE REASON TO TALK
------------------------------------------------
Reading needs nothing from us. infDisplayData ($31) is broadcast continuously by the V1 with no
request, and it already carries what the set-speed gate wants -- which bands are alerting, which
arrows are lit, the strength bar graph, and the mute state. respAlertData ($43) is richer (per-alert
frequency, separate front and rear strengths) but only streams once some accessory has sent
reqStartAlertData ($41). We never ask; if the phone app has, the V1 interleaves those packets to
every listener and we get the extra precision free.

This started out strictly listen-only, and that changed for one specific reason: the V1 Gen2 has no
GPS, so it cannot learn locations, so openpilot has to be the thing that remembers false alarms --
and remembering them is worthless if it cannot also make the detector stop chirping at them. That
means reqMuteOn ($34), and transmitting.

So the single write path is a mute, sent rarely, and everything around it is built to make talking
out of turn survivable rather than to pretend it cannot happen. The honest limits of the timing are
in EspSerialReader._service_tx; the safety rule that we never transmit on a port that has not
already spoken ESP is in EspSerialReader.send.

STRENGTH IS MEASURED IN LEDs
----------------------------
The bar graph is the scale the driver actually sees on the detector, so it is the scale the
settings use: "slow down when Ka reaches N of 8 bars" is checkable against the device on the
windshield in a way that a raw unit-less byte is not. Table 9.1 of the spec maps the alert table's
raw strength byte onto that same bar count, per band, which is what STRENGTH_TO_BARS below is.
Note the mapping is band-dependent -- the same raw byte is a different number of bars on X than on
Ka -- so anything comparing strengths across bands must go through it.
"""

from dataclasses import dataclass, field

# Serial format, section 2. Not configurable and not guessed at.
BAUD = 57600
BYTESIZE = 8
PARITY = 'N'
STOPBITS = 1

SOF = 0xAA
EOF = 0xAB
DEST_BASE = 0xD0
ORIG_BASE = 0xE0

# Device identifiers, Table 3.4. $03-$05 are the only ones Valentine makes available to third
# parties; the rest are theirs. We never transmit, so we never claim one -- these are here to
# recognize who a packet came from.
DEV_CONCEALED_DISPLAY = 0x00
DEV_REMOTE_AUDIO = 0x01
DEV_SAVVY = 0x02
DEV_GENERAL_BROADCAST = 0x08
DEV_V1_NO_CHECKSUM = 0x09
DEV_V1_CHECKSUM = 0x0A
V1_ORIGINS = (DEV_V1_NO_CHECKSUM, DEV_V1_CHECKSUM)

# Table 3.4 reserves exactly three identifiers for third-party accessories; everything else belongs
# to Valentine Research products. We claim the first. The spec asks manufacturers to publish which
# one they use so owners can tell what may share a bus -- BluePilot uses $03.
DEV_THIRD_PARTY_1 = 0x03
DEV_THIRD_PARTY_2 = 0x04
DEV_THIRD_PARTY_3 = 0x05
OUR_DEVICE_ID = DEV_THIRD_PARTY_1

# Time slice for OUR_DEVICE_ID, Table 3.4, measured from the moment the End of Frame byte of the
# V1's infDisplayData packet is received. Transmitting outside this window is what collides with
# another accessory.
TS3_SLICE_START_S = 0.02361
TS3_SLICE_END_S = 0.03125

# Packet identifiers, section 4.
PID_INF_DISPLAY_DATA = 0x31
PID_REQ_MUTE_ON = 0x34
PID_REQ_MUTE_OFF = 0x35
PID_RESP_ALERT_DATA = 0x43

# Band and arrow indicator bits. Shared layout between infDisplayData byte 3/4 and respAlertData
# byte 5, with one documented difference noted at BAND_KU.
BAND_LASER = 1 << 0
BAND_KA = 1 << 1
BAND_K = 1 << 2
BAND_X = 1 << 3
# Bit 4 is "Ku Band" in the respAlertData table and "Reserved" in the infDisplayData table. The
# spec's OWN example infDisplayData packet sets it and blinks it as the priority alert
# (AA D8 E9 31 08 5B 5B 1F 38 28 ...: bit 4 present in image 1, absent in image 2), so on the
# display packet it is a real band indicator, not padding. Decoded as Ku in both, which costs
# nothing if a future firmware reuses it -- Ku is not a band this feature acts on.
BAND_KU = 1 << 4
ARROW_FRONT = 1 << 5
ARROW_SIDE = 1 << 6
ARROW_REAR = 1 << 7

BAND_MASK = BAND_LASER | BAND_KA | BAND_K | BAND_X | BAND_KU
ARROW_MASK = ARROW_FRONT | ARROW_SIDE | ARROW_REAR

BAND_NAMES = {
  BAND_LASER: "laser",
  BAND_KA: "Ka",
  BAND_K: "K",
  BAND_X: "X",
  BAND_KU: "Ku",
}

# Aux0 bits, Table 8.1.
AUX0_SOFT_MUTE = 1 << 0
AUX0_TS_HOLDOFF = 1 << 1
AUX0_SYSTEM_STATUS = 1 << 2   # 1 = signed on and actively searching
AUX0_DISPLAY_ON = 1 << 3
AUX0_EURO_MODE = 1 << 4
AUX0_CUSTOM_SWEEP = 1 << 5

# respAlertData Aux0, single defined bit.
ALERT_AUX0_PRIORITY = 1 << 7

# Seven-segment decode for the Bogey Counter, byte 0/1 of infDisplayData. Bit order from the spec
# is a,b,c,d,e,f,g,dp from bit 0 up, which is the standard arrangement -- the vendor's example
# packet carries $5B, and $5B is '2' under this table, which is the check that it is the standard
# one and not some permutation.
_SEVEN_SEGMENT = {
  0x3F: 0, 0x06: 1, 0x5B: 2, 0x4F: 3, 0x66: 4,
  0x6D: 5, 0x7D: 6, 0x07: 7, 0x7F: 8, 0x6F: 9,
}
_SEGMENT_MASK = 0x7F  # strip the decimal point before matching

# Table 9.1, alert-table raw strength -> front panel bar count, per band. Stored as the INCLUSIVE
# LOWER BOUND of each bar count, highest first, so the lookup is the first threshold met.
#
# Ku shares the K row: the spec's table has a single "K/Ku Band" column.
_STRENGTH_THRESHOLDS = {
  BAND_X:  ((8, 0xD0), (7, 0xC5), (6, 0xBD), (5, 0xB4), (4, 0xAA), (3, 0xA0), (2, 0x96), (1, 0x01)),
  BAND_K:  ((8, 0xC2), (7, 0xB8), (6, 0xAE), (5, 0xA4), (4, 0x9A), (3, 0x90), (2, 0x88), (1, 0x01)),
  BAND_KU: ((8, 0xC2), (7, 0xB8), (6, 0xAE), (5, 0xA4), (4, 0x9A), (3, 0x90), (2, 0x88), (1, 0x01)),
  BAND_KA: ((8, 0xBA), (7, 0xB3), (6, 0xAC), (5, 0xA5), (4, 0x9E), (3, 0x97), (2, 0x90), (1, 0x01)),
}

MAX_BARS = 8


@dataclass
class EspFrame:
  """One framed ESP packet with its framing and checksum already removed and verified."""
  dest: int
  orig: int
  packet_id: int
  payload: bytes

  @property
  def from_v1(self) -> bool:
    return self.orig in V1_ORIGINS


@dataclass
class DisplayData:
  """Decoded infDisplayData -- the V1's front panel, as the driver sees it.

  bands/arrows are the steady-state indicators. priority_bands/priority_arrows are the ones that
  are BLINKING, which is how the V1 marks the highest-threat alert: the spec's blink mechanism is
  two image bytes where image 2 has the blinking bits cleared, so the difference between them is
  exactly the priority set. That is the whole reason two image bytes exist.
  """
  bogey_count: int = 0
  bars: int = 0
  bands: int = 0
  arrows: int = 0
  priority_bands: int = 0
  priority_arrows: int = 0
  muted: bool = False
  searching: bool = False
  display_on: bool = False
  euro_mode: bool = False
  # The V1 forbidding accessories a time slice after this packet. Only matters to something that
  # transmits, which is why it rides on the display packet rather than being inferred.
  ts_holdoff: bool = False

  def has_band(self, band: int) -> bool:
    return bool(self.bands & band)

  @property
  def band_names(self) -> list[str]:
    return [name for bit, name in BAND_NAMES.items() if self.bands & bit]


@dataclass
class AlertRecord:
  """One entry of the Alert Table, from respAlertData."""
  index: int = 0
  count: int = 0
  frequency_mhz: int = 0
  front_strength: int = 0
  rear_strength: int = 0
  band: int = 0
  arrows: int = 0
  priority: bool = False

  @property
  def bars(self) -> int:
    """Strongest of the two antennas, on the front panel's scale."""
    return max(strength_to_bars(self.band, self.front_strength),
               strength_to_bars(self.band, self.rear_strength))


@dataclass
class AlertTable:
  """A complete Alert Table, assembled from the interleaved respAlertData stream."""
  alerts: list[AlertRecord] = field(default_factory=list)

  @property
  def priority(self) -> AlertRecord | None:
    for a in self.alerts:
      if a.priority:
        return a
    return None


def strength_to_bars(band: int, raw: int) -> int:
  """Table 9.1: alert-table strength byte -> front panel bar count (0-8) for that band.

  Unknown or multi-band input falls back to the Ka row, which is the most conservative choice for
  this feature's purposes: Ka reaches a given bar count at a LOWER raw value than X or K, so
  guessing Ka can only over-report strength, never hide a strong signal.
  """
  if raw <= 0:
    return 0
  thresholds = _STRENGTH_THRESHOLDS.get(band, _STRENGTH_THRESHOLDS[BAND_KA])
  for bars, low in thresholds:
    if raw >= low:
      return bars
  return 0


def bar_graph_to_bars(image: int) -> int:
  """infDisplayData byte 2 -> lit LED count.

  The byte is a thermometer bitmask (b0 is the leftmost LED), so the count is a popcount rather
  than a table lookup. Table 9.1's "Equivalent Bar Graph Value" column is the same thing written
  out: $1F is five contiguous bits and five LEDs.
  """
  return bin(image & 0xFF).count("1")


def seven_segment_to_digit(image: int) -> int:
  """Bogey Counter seven-segment image -> digit, or 0 for a pattern that is not a digit.

  A blank or non-digit display genuinely means "no count to show", so 0 is the honest answer
  rather than an error -- this runs against a live bus where a half-lit transitional frame is
  normal, and nothing downstream should have to handle an exception for it.
  """
  return _SEVEN_SEGMENT.get(image & _SEGMENT_MASK, 0)


def unframe(buf: bytes) -> tuple[list[EspFrame], bytes]:
  """Pull complete packets out of a byte stream. Returns (frames, unconsumed tail).

  Written to resynchronize rather than to trust, because a bus tap does not get to start at a
  packet boundary. We come up mid-stream, we see another accessory's traffic, and $AA is a
  perfectly ordinary payload byte -- so a candidate start is only accepted once the End of Frame
  lands exactly where the Payload Length said it would, and the checksum agrees. Anything else
  advances one byte and tries again.

  The tail is returned rather than dropped so the caller can prepend it to the next read; a packet
  split across two serial reads is the normal case at 57600 baud, not an edge case.
  """
  frames: list[EspFrame] = []
  i = 0
  n = len(buf)
  while True:
    start = buf.find(SOF, i)
    if start < 0:
      # No candidate at all: keep nothing. A stray SOF-less run is another device's noise.
      return frames, b""
    # Shortest legal packet is SOF DI OI PI PL EOF, six bytes.
    if start + 6 > n:
      return frames, buf[start:]
    dest_raw, orig_raw, packet_id, payload_len = buf[start + 1:start + 5]
    end = start + 5 + payload_len   # index of the EOF byte
    if end >= n:
      # PL points past what we have. Could still be a real packet once more bytes arrive, but only
      # if the header looks like one -- otherwise a garbage length byte would stall the parser
      # forever waiting for bytes that are never coming.
      if _plausible_header(dest_raw, orig_raw):
        return frames, buf[start:]
      i = start + 1
      continue
    if buf[end] != EOF or not _plausible_header(dest_raw, orig_raw):
      i = start + 1
      continue

    payload = buf[start + 5:end]
    orig = orig_raw - ORIG_BASE
    # Checksummed format is established by the V1's own device id, not per packet: $0A means every
    # packet on this bus carries a checksum as its last payload byte and PL counts it.
    if orig == DEV_V1_CHECKSUM and payload:
      if (sum(buf[start:end - 1]) & 0xFF) != payload[-1]:
        i = start + 1
        continue
      payload = payload[:-1]

    frames.append(EspFrame(dest=dest_raw - DEST_BASE, orig=orig,
                           packet_id=packet_id, payload=bytes(payload)))
    i = end + 1


def _plausible_header(dest_raw: int, orig_raw: int) -> bool:
  """Cheap sanity check on the two identifier bytes, used to reject false $AA starts."""
  return (DEST_BASE <= dest_raw <= DEST_BASE + 0x0A and
          ORIG_BASE <= orig_raw <= ORIG_BASE + 0x0A)


def decode_display_data(payload: bytes) -> DisplayData | None:
  """infDisplayData payload -> DisplayData. None if the payload is too short to trust.

  Checksummed payloads arrive here already stripped by unframe(), so both packet formats present
  the same eight bytes and this needs no mode flag.
  """
  if len(payload) < 6:
    return None

  image1 = payload[3]
  image2 = payload[4]
  aux0 = payload[5]

  # Blinking indicators are those set in image 1 and cleared in image 2. See the DisplayData
  # docstring -- this is the V1's own encoding of "priority alert", not a heuristic.
  blinking = image1 & ~image2

  return DisplayData(
    bogey_count=seven_segment_to_digit(payload[0]),
    bars=bar_graph_to_bars(payload[2]),
    bands=image1 & BAND_MASK,
    arrows=image1 & ARROW_MASK,
    priority_bands=blinking & BAND_MASK,
    priority_arrows=blinking & ARROW_MASK,
    muted=bool(aux0 & AUX0_SOFT_MUTE),
    searching=bool(aux0 & AUX0_SYSTEM_STATUS),
    display_on=bool(aux0 & AUX0_DISPLAY_ON),
    euro_mode=bool(aux0 & AUX0_EURO_MODE),
    ts_holdoff=bool(aux0 & AUX0_TS_HOLDOFF),
  )


def build_frame(dest: int, pid: int, payload: bytes = b"", orig: int = OUR_DEVICE_ID,
                checksum: bool = True) -> bytes:
  """Assemble a packet to send to the Valentine One.

  The checksum form is what a V1 with device id $0A establishes for the whole bus, and the payload
  length INCLUDES the checksum byte -- that off-by-one is the easiest thing to get wrong here, and
  the vendor's own worked examples in section 4 are what the tests check against.

  A non-checksum V1 ($09) exists and is supported for completeness, but every packet we would send
  goes to a Gen2, which uses checksums.
  """
  body = bytearray([SOF, DEST_BASE + dest, ORIG_BASE + orig, pid,
                    len(payload) + (1 if checksum else 0)])
  body += payload
  if checksum:
    body.append(sum(body) & 0xFF)
  body.append(EOF)
  return bytes(body)


def mute_on(dest: int = DEV_V1_CHECKSUM) -> bytes:
  """reqMuteOn -- the V1 treats this exactly as a press of its own mute button.

  Which is the whole reason this is the right mechanism for location lockouts rather than
  suppressing the alert only inside openpilot: the driver hears what the car decided. A lockout
  that silenced the set-speed logic but left the detector chirping would be a car and a detector
  disagreeing out loud, and the driver could not tell which one had learned the place.

  Per the spec this is only in effect until the V1 stops tracking all current alerts, so it is a
  per-encounter action rather than a persistent setting -- it cannot get stuck on.
  """
  return build_frame(dest, PID_REQ_MUTE_ON)


def mute_off(dest: int = DEV_V1_CHECKSUM) -> bytes:
  """reqMuteOff. Note the spec's carve-out: this will NOT unmute a laser alert."""
  return build_frame(dest, PID_REQ_MUTE_OFF)


def decode_alert_data(payload: bytes) -> AlertRecord | None:
  """respAlertData payload -> AlertRecord. None if too short to trust.

  Index and count share byte 0 as two nibbles, count in the low half. A count of zero with an
  index of zero is the V1 saying the table is empty, which it sends periodically -- that is a
  valid record, not a malformed one, and the caller distinguishes it by count.
  """
  if len(payload) < 7:
    return None

  index_count = payload[0]
  band_arrow = payload[5]
  return AlertRecord(
    index=(index_count >> 4) & 0x0F,
    count=index_count & 0x0F,
    frequency_mhz=(payload[1] << 8) | payload[2],
    front_strength=payload[3],
    rear_strength=payload[4],
    band=band_arrow & BAND_MASK,
    arrows=band_arrow & ARROW_MASK,
    priority=bool(payload[6] & ALERT_AUX0_PRIORITY),
  )
