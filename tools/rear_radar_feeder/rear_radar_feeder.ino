// FusionPilot: rear radar feeder for Teensy 4.0
//
// WHAT IT DOES. Listens to a Delphi MRR on its own private CAN bus, reduces ~2140 detection
// frames/s to three digest messages at 20 Hz, and puts only those on the car's bus 1.
//
// WHY THE REDUCTION IS THE POINT. Bus 1 already carries the front radar and ACC at 60-73% load,
// measured. The raw stream cannot go there. Measured on the bench 2026-08-14: 2140 frames/s in,
// 60 out, a 36x reduction.
//
// IT NEVER TRANSMITS TO THE RADAR. The MRR free-runs -- proven on the bench, all 64 detection
// addresses plus the 0x174 header with nothing sent to it. The ESR this project originally planned
// around needed Vehicle_Data and SensorInput just to stay alive; this one needs no transmit path at
// all, which is why the radar channel below is receive-only.
//
// THIS MIRRORS tools/bp_rear_digest_sim.py. That file is the reference and is unit tested in
// selfdrive/car/tests/test_rear_digest_reduction.py. When the two disagree, Python is right --
// a sign error here inverts closing and receding, which looks entirely reasonable in a log.
//
// WIRING, by the board's wire colours
//   red / black        12 V switched and ground
//   CAN1 white/lt blue THE CAR, bus 1, 500 kbps. CUT ITS 120 OHM RESISTOR -- that bus already has
//                      its two terminators and a third takes it to ~40 ohm, which can stop the
//                      front radar and ACC this car actually drives on.
//   CAN2 yellow/green  the radar's private bus, 500 kbps. KEEP its 120 ohm: the radar carries the
//                      other one, giving the ~60 ohm a healthy pair should read.
//   purple             spare switched output, unused
//
#include <FlexCAN_T4.h>

// CHANNEL ASSIGNMENT FOLLOWS THE BOARD'S OWN LABELS, not what reads naturally in code. The
// Electroneering hat silkscreens CAN1 as the VEHICLE side (white/light blue) and CAN2 as the DEVICE
// side (yellow/green), confirmed by the seller 2026-08-14. Writing it the other way round would
// mean wiring against the labels, which is the kind of thing that is correct in a commit message
// and wrong in a car.
FlexCAN_T4<CAN1, RX_SIZE_16, TX_SIZE_64> carBus;     // vehicle side: bus 1, digest OUT
FlexCAN_T4<CAN2, RX_SIZE_256, TX_SIZE_16> radarBus;  // device side: private radar bus, listen only

// ---- the radar's own protocol -------------------------------------------------------------
static const uint32_t MRR_START = 0x120;
static const uint32_t MRR_END = 0x15F;
static const uint32_t MRR_HEADER = 0x174;

// ---- our digest ---------------------------------------------------------------------------
static const uint32_t TX_LEFT = 0x640;
static const uint32_t TX_RIGHT = 0x641;
static const uint32_t TX_STATUS = 0x642;
static const uint32_t DIGEST_PERIOD_MS = 50;  // 20 Hz

// ---- constants mirrored from bp_rear_digest_sim.py ----------------------------------------
static const float MIN_LONG_RANGE_DIST_M = 30.0f;
static const float MIN_CLOSING_MS = 0.5f;
static const float MAX_RANGE_M = 175.0f;
static const float OWN_LANE_HALF_WIDTH_M = 1.4f;

// MOUNTING CORRECTION, the one number that is not shared with the simulator. The MRR receives
// nothing, so it has no stored alignment and no auto-align routine -- the offset is ours to apply.
// Calibrate by parking behind a target at a known offset and reading the reported azimuth back.
// Aim matters more than the loose mechanical tolerance suggests: 3 degrees is 2.6 m at 50 m.
static const float AZIMUTH_OFFSET_RAD = 0.0f;

// Health. The bench measured 33 Hz of detection frames with the sensor idle, so a rate this low
// means the radar is not well whatever else the bus says. A silent digest and an empty road must
// never read the same downstream.
static const uint8_t MIN_DETECTION_HZ = 10;

struct Detection {
  float dRel, yRel, vRel;
};

struct Side {
  bool detected;
  float dRel, yRel, vRel;
  uint8_t count;
};

static Detection cycleDets[64];
static uint8_t cycleCount = 0;

static Side gLeft, gRight;
static volatile uint32_t detFrames = 0;
static uint8_t detHz = 0;
static bool radarAlive = false;
static uint8_t validDetections = 0;
static uint16_t uptimeS = 0;
static uint8_t txCounter = 0;

// Little-endian signal packing, matching the @1+ layout in bp_rear_radar.dbc. Validated against
// cantools before this was flashed -- see the note in that DBC.
static inline void packLE(uint64_t &w, uint32_t value, uint8_t start, uint8_t len) {
  const uint64_t mask = (len >= 64) ? ~0ULL : ((1ULL << len) - 1ULL);
  w |= (uint64_t)(value & mask) << start;
}

static inline uint32_t quantise(float v, float scale, float offset, uint32_t maxRaw) {
  float raw = (v - offset) / scale;
  if (raw < 0.0f) raw = 0.0f;
  if (raw > (float)maxRaw) raw = (float)maxRaw;
  return (uint32_t)(raw + 0.5f);
}

static void decodeDetection(const CAN_message_t &m) {
  // The empty-slot reject, and the whole reason this keeps up. An unused slot reads 8080xx...,
  // and ~97% of frames are empty. Decoding them all costs more time than they take to arrive.
  if (m.buf[0] == 0x80 && m.buf[1] == 0x80) return;

  uint64_t w = 0;
  for (int i = 7; i >= 0; i--) w = (w << 8) | m.buf[i];

  const bool valid = (w >> 0) & 0x1;
  if (!valid) return;

  const uint8_t scan = (w >> 17) & 0x3;
  const float rng = (float)((w >> 31) & 0x3FFF) * 0.015625f;
  if (rng <= 0.0f || rng > MAX_RANGE_M) return;
  if ((scan == 1 || scan == 3) && rng < MIN_LONG_RANGE_DIST_M) return;

  const float az = (float)((w >> 47) & 0x3FFF) * 0.0003834f - 3.1416f - AZIMUTH_OFFSET_RAD;
  const float rate = (float)((w >> 15) & 0x3FFF) * 0.015625f - 128.0f;

  if (cycleCount >= 64) return;
  cycleDets[cycleCount].dRel = cosf(az) * rng;
  cycleDets[cycleCount].yRel = -sinf(az) * rng;
  cycleDets[cycleCount].vRel = -rate;  // negated ONCE: positive is closing, per from_radar
  cycleCount++;
}

static void reduceCycle() {
  Side l = {false, 0, 0, 0, 0};
  Side r = {false, 0, 0, 0, 0};
  for (uint8_t i = 0; i < cycleCount; i++) {
    const Detection &d = cycleDets[i];
    if (d.vRel < MIN_CLOSING_MS) continue;
    Side *s = nullptr;
    if (d.yRel > OWN_LANE_HALF_WIDTH_M) s = &l;
    else if (d.yRel < -OWN_LANE_HALF_WIDTH_M) s = &r;
    else continue;  // dead astern is our own lane, not a lane we would move into
    if (s->count < 31) s->count++;
    // SOONEST, not nearest. Changed 2026-08-21 on a measurement -- see bp_rear_digest_sim.py, which
    // this mirrors. Picking min(dRel) hid the arriving car on 3.9% of multi-target side-scans, and
    // the worst case reported 12.0 s while discarding a target at 0.4 s. vRel is already known
    // >= MIN_CLOSING_MS here, so the divide is safe.
    const float t = d.dRel / d.vRel;
    if (!s->detected || t < s->dRel / s->vRel) {
      s->detected = true;
      s->dRel = d.dRel;
      s->yRel = d.yRel;
      s->vRel = d.vRel;
    }
  }
  validDetections = cycleCount < 127 ? cycleCount : 127;
  gLeft = l;
  gRight = r;
  cycleCount = 0;
}

static void sendSide(uint32_t id, const Side &s) {
  uint64_t w = 0;
  packLE(w, s.detected ? 1 : 0, 0, 1);
  packLE(w, quantise(s.dRel, 0.25f, 0.0f, 0x3FF), 1, 10);
  packLE(w, quantise(s.yRel, 0.1f, -25.6f, 0x1FF), 11, 9);
  packLE(w, quantise(s.vRel, 0.05f, -51.2f, 0x7FF), 20, 11);
  packLE(w, s.count, 31, 5);
  packLE(w, txCounter, 36, 4);

  CAN_message_t m;
  m.id = id;
  m.len = 8;
  for (int i = 0; i < 8; i++) m.buf[i] = (w >> (8 * i)) & 0xFF;
  uint16_t sum = 0;
  for (int i = 0; i < 7; i++) sum += m.buf[i];
  m.buf[7] = sum & 0xFF;
  carBus.write(m);
}

static void sendStatus() {
  uint64_t w = 0;
  packLE(w, radarAlive ? 1 : 0, 0, 1);
  packLE(w, 1, 1, 1);  // ScanIndexOk
  packLE(w, detHz, 2, 8);
  packLE(w, validDetections, 10, 7);
  packLE(w, uptimeS, 17, 16);
  packLE(w, txCounter, 36, 4);

  CAN_message_t m;
  m.id = TX_STATUS;
  m.len = 8;
  for (int i = 0; i < 8; i++) m.buf[i] = (w >> (8 * i)) & 0xFF;
  uint16_t sum = 0;
  for (int i = 0; i < 7; i++) sum += m.buf[i];
  m.buf[7] = sum & 0xFF;
  carBus.write(m);
}

void setup() {
  radarBus.begin();
  radarBus.setBaudRate(500000);
  // Receive only. Nothing is ever sent to this radar -- see the header note.
  radarBus.setMaxMB(16);
  radarBus.enableFIFO();

  carBus.begin();
  carBus.setBaudRate(500000);
}

void loop() {
  static uint32_t lastDigest = 0;
  static uint32_t lastSecond = 0;

  CAN_message_t m;
  while (radarBus.read(m)) {
    if (m.id >= MRR_START && m.id <= MRR_END) {
      detFrames++;
      decodeDetection(m);
    } else if (m.id == MRR_HEADER) {
      // The header closes a cycle: every detection in it agreed on the scan index.
      detFrames++;
      reduceCycle();
    }
  }

  const uint32_t now = millis();

  if (now - lastSecond >= 1000) {
    lastSecond = now;
    const uint32_t f = detFrames;
    detFrames = 0;
    detHz = f > 255 ? 255 : (uint8_t)f;
    // BOTH conditions, because a feeder that outlives its radar would otherwise report an empty
    // road forever -- the failure the whole status message exists to prevent.
    radarAlive = detHz >= MIN_DETECTION_HZ;
    uptimeS++;
  }

  if (now - lastDigest >= DIGEST_PERIOD_MS) {
    lastDigest = now;
    txCounter = (txCounter + 1) & 0xF;
    sendSide(TX_LEFT, gLeft);
    sendSide(TX_RIGHT, gRight);
    sendStatus();
  }
}
