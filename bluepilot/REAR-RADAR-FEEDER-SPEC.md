# The rear radar feeder: firmware specification

**Status: nothing built. The openpilot side is complete and shipped; this is the piece that does
not exist.** Every interface below is already in the tree and is not up for negotiation by the
firmware — `opendbc/dbc/bp_rear_radar.dbc`, `sunnypilot/selfdrive/car/rear_radar.py`,
`RearRadarBP` in `cereal/custom.capnp`. If the firmware and this document disagree with those
files, those files win.

---

## 1. What this thing is, and why it exists

There are two boxes to build, not one.

    ┌───────────────┐   private bus    ┌────────────┐   bus 1 (car)   ┌──────────┐
    │ Delphi MRR    │  ~2150 frames/s  │   FEEDER   │   60 frames/s   │  comma   │
    │ (rear-facing) │ ───────────────► │    MCU     │ ──────────────► │    3X    │
    └───────────────┘   64 det @ 33 Hz └────────────┘  3 msgs @ 20 Hz └──────────┘

**The radar** is a Delphi MRR — the same part already on the front of this car
(`FORD_FUSION_MK5` inherits `Bus.radar: RADAR.DELPHI_MRR`), which is the reason to use it: the
message format is already understood, already decoded in `opendbc/car/ford/radar_interface.py`,
and a second unit is a known quantity rather than a research project.

**The feeder** is a small microcontroller with two CAN interfaces. It listens to the radar on one
and speaks a three-message summary on the other. It is a *reducer*, and that is its entire job.

### Why a reducer rather than just wiring the radar to the car

A Delphi MRR emits 64 detection messages every scan at 33 Hz. That is roughly **2150 CAN frames
per second**. Bus 1 on this car — the bus the front radar and the camera already share — was
measured at **60-73% loaded**. There is no room. Putting a second raw radar on it would break the
car's own ADAS, which is a considerably worse outcome than not having passing assist.

The digest is **three messages at 20 Hz, 60 frames per second**. That fits in the headroom that
exists.

So the radar gets its own private CAN bus that touches nothing else, and the only thing that
crosses onto the car's bus is the answer.

### Why the answer is so small

Passing assist asks one question per side: *is something coming up behind me in that lane, and how
fast.* It does not want a point cloud. `RearApproachSide.from_radar` takes exactly two numbers —
range and range rate — and derives a time-to-contact from them. Everything else the radar produces
is thrown away by the second line of Python that touches it.

Sending 64 detections so that openpilot can discard 62 of them would spend the bus budget on data
with no consumer.

### What the feeder must NOT decide

**It must not own a threshold.** Every number that decides behaviour — how close is too close, what
counts as closing, where the lane boundary is — lives in `rear_approach.py` where it can be read,
tested and changed without a soldering iron. The feeder reports `YRel` precisely so the lane
binning stays tunable in software; the DBC says so in its own comment.

The feeder decides one thing only: **which single target per side is the most relevant**, so that
the other 62 need not be transmitted. That is a bandwidth decision, not a safety decision.

---

## 2. The contract, exactly

Three messages, all 8 bytes, all little-endian, all at **20 Hz** on **bus 1**.

Addresses were checked against `ford_lincoln_base_pt.dbc`, `ford_fusion_2018_adas.dbc` and
`FORD_CADS.dbc`: **0x640-0x642 collide with nothing on either the powertrain or the radar bus.**
The ESR map tops out at 0x53F and FORD_CADS has nothing between 0x5DC and 0x76C.

### `RearRadarLeft` — 0x640 (1600) and `RearRadarRight` — 0x641 (1601)

Identical layout on purpose, so one decoder serves both.

| Signal | Start | Len | Scale | Offset | Range | Meaning |
|---|---|---|---|---|---|---|
| `Detected` | 0 | 1 | 1 | 0 | 0-1 | a closing target is present this side |
| `DRel` | 1 | 10 | 0.25 | 0 | 0-255.75 m | distance **behind** us, positive rearward |
| `YRel` | 11 | 9 | 0.1 | -25.6 | ±25.6 m | lateral offset, **left positive** |
| `VRel` | 20 | 11 | 0.05 | -51.2 | ±51.2 m/s | **positive = CLOSING on us** |
| `TargetCount` | 31 | 5 | 1 | 0 | 0-31 | closing targets this side before reduction |
| `Counter` | 36 | 4 | 1 | 0 | 0-15 | rolling, +1 per transmission, wraps |
| `Checksum` | 56 | 8 | 1 | 0 | 0-255 | see §6 |

### `RearRadarStatus` — 0x642 (1602)

| Signal | Start | Len | Meaning |
|---|---|---|---|
| `RadarAlive` | 0 | 1 | the feeder is receiving valid radar frames right now |
| `ScanIndexOk` | 1 | 1 | the scan index is advancing 0→1→2→3 as it should |
| `DetectionHz` | 2 | 8 | measured rate of `MRR_Detection` frames, saturating at 255 |
| `ValidDetections` | 10 | 7 | detections passing the validity filter this scan |
| `FeederUptime` | 17 | 16 | seconds since feeder boot, saturating at 65535 |
| `Counter` | 36 | 4 | rolling |
| `Checksum` | 56 | 8 | see §6 |

### How openpilot reads it

From `rear_radar.py`, which is already written:

```python
"dataAvailable": alive and bool(status["RadarAlive"]) and detection_hz >= MIN_DETECTION_HZ
```

`MIN_DETECTION_HZ` is **10**. `alive` is `CANParser.can_valid`, which is frequency-based — so all
three messages must actually arrive at 20 Hz or the whole digest is discarded.

**`dataAvailable` false means "we cannot see", and passing assist refuses on it.** It never reads
as a clear road. That is the single most important property of the whole design and §5 exists to
protect it.

---

## 3. The input side: what the radar actually sends

Derived from `opendbc/car/ford/radar_interface.py::_update_delphi_mrr`, which is the working
decoder for the identical part on the front of this car.

- **64 messages**, `MRR_Detection_001` through `MRR_Detection_064`, at 33 Hz.
- **`MRR_Header_InformationDetections`** carries `CAN_SCAN_INDEX`, which rotates 0→1→2→3. Each
  detection message carries `CAN_SCAN_INDEX_2LSB_nn` and **a detection whose index does not match
  the header's is stale and must be discarded.**
- **`MRR_Header_SensorCoverage`** carries `CAN_RANGE_COVERAGE`, which must match the scan index per
  `DELPHI_MRR_RADAR_RANGE_COVERAGE = {0: 42, 1: 164, 2: 45, 3: 175}` metres.
- Per detection: `CAN_DET_VALID_LEVEL_nn`, `CAN_DET_RANGE_nn` (m), `CAN_DET_AZIMUTH_nn` (rad),
  `CAN_DET_RANGE_RATE_nn` (m/s).

**Scan indices 0 and 2 reach ~40 m; 1 and 3 reach ~170 m.** The front decoder uses only 2 and 3
because they have ±60 m/s Doppler coverage and produce fewer duplicate points. The feeder should
do the same, and **must use index 3 for anything past 45 m** — which is where a car closing at
highway speed will be when it matters.

Upstream also throws out long-range returns below a minimum distance because the sensitive mode
detects the road surface. Carry that filter over.

---

## 4. The reduction, step by step

Runs once per completed scan cycle (on the index-3 header, matching upstream's trigger).

**4.1 — Collect valid detections.** For each of the 64: skip if `CAN_SCAN_INDEX_2LSB` does not
match the header, skip if `CAN_DET_VALID_LEVEL` is clear, skip long-range returns below the
minimum distance. Count what survives into `ValidDetections`.

**4.2 — Transform into the car's frame.**

    range  = CAN_DET_RANGE
    az     = CAN_DET_AZIMUTH
    d_behind = cos(az) * range
    y_car    = ±sin(az) * range        <- SIGN NOT DETERMINED. See below.

**THE AZIMUTH SIGN IS A BENCH MEASUREMENT, NOT A DERIVATION.** The front decoder uses
`yRel = -sin(azimuth) * dist`. A rear-facing sensor is rotated 180°, so the sensor's left is the
car's right, which *suggests* the sign flips back. Do not ship that reasoning. `ESR.dbc` and
`ford_fusion_2018_adas.dbc` already disagree about the sign of angle for the same hardware, which
is exactly why the DBC comment states the convention explicitly instead of inheriting it.

Determine it by walking a corner reflector down one side of the parked car and reading which way
`YRel` moves. Write the answer into a comment in the firmware next to the sign.

**4.3 — Reject anything that is not behind us.** `d_behind` must be positive and within a sane
range. A rear radar mounted low behind the valance will see the ground and the bumper; establish
the near-field floor on the bench.

**4.4 — Compute closing rate.**

    VRel = -CAN_DET_RANGE_RATE

**This negation is the single easiest thing in the document to get wrong.** For a target behind
the car, the range *decreases* as it catches up, so the raw range rate is **negative when
something is closing**. The digest is specified the other way: `VRel` positive means closing,
because that is what `RearApproachSide.from_radar` expects:

```python
self.closing = v_rel >= MIN_CLOSING_MS      # 1.5 m/s
self.ttc = (d_rel / v_rel) if v_rel >= MIN_CLOSING_MS else NO_THREAT_TTC_S
```

Get the sign backwards and every approaching car reports as receding. The system then never
refuses, never aborts, and **authorizes lane changes into traffic** — with a live radar and a
green `dataAvailable`, which is the worst possible failure because nothing looks broken.

Verify it on the bench before it goes in the car: a reflector moved toward the sensor must produce
positive `VRel`.

**4.5 — Bin by side.** Split on the sign of `y_car`, with a dead band around zero so a target
directly astern does not flicker between sides. The dead band is a bandwidth heuristic, not a
lane boundary — openpilot receives `YRel` and does the real lane reasoning.

**4.6 — Pick one per side.** Among targets with `VRel >= 1.5 m/s`, choose the one with the
**smallest time to contact**, `d_behind / VRel` — not the nearest, and not the fastest.

A car 80 m back closing at 15 m/s arrives in 5.3 s. A car 20 m back closing at 2 m/s arrives in
10 s. The first is the one that decides a lane change, and picking by distance alone would report
the second.

Set `TargetCount` to how many closing targets that side had **before** the pick, so a log can show
when a busy road was reduced to one number. `TargetCount == 0` with `Detected` set is impossible
and indicates a firmware bug; the DBC says so.

**4.7 — Emit.** If a side has no closing target: `Detected = 0` and zero the fields. That is a
real answer — *watched, and nothing coming* — and openpilot treats it as available and clear.

---

## 5. Failure behaviour, which is the part that matters

The entire rear-approach design rests on one distinction: **"nothing there" and "I cannot see" must
never look the same.** `rear_approach.py` says so at the top of the file, twice.

The feeder is where that distinction is created or lost.

**The rule: when in doubt, say you cannot see. Never fabricate a clear road.**

| Condition | `RadarAlive` | `DetectionHz` | Effect |
|---|---|---|---|
| Radar silent > 300 ms | 0 | 0 | openpilot: unavailable, refuses |
| Scan index not advancing | keep 1, clear `ScanIndexOk` | actual | see below |
| Detection rate below 10 Hz | 1 | actual (<10) | openpilot: unavailable |
| Malformed / unparseable frames | 0 | 0 | unavailable |
| Radar healthy, no targets | 1 | ~33 | available, `Detected = 0` |

**Keep transmitting the status message even when everything is wrong.** A feeder that goes silent
when its radar dies is indistinguishable from a feeder that was never fitted — which openpilot also
treats as unavailable, so the outcome is the same today, but the *diagnosis* is lost. A status
frame saying `RadarAlive = 0, DetectionHz = 0` is a defect report. Silence is a mystery.

**The scan-index case is deliberately not fatal.** Upstream tolerates 5 consecutive bad indices
before declaring the radar unavailable, because in reverse the MRR repeats its last messages.
Mirror that: count, and only clear `RadarAlive` once the count is sustained.

**Never latch a stale target.** If the current scan produced nothing for a side, emit
`Detected = 0` for that side. Holding the last known target across scans invents a car that is no
longer there, and — worse in the other direction — a target held from before a dropout would
survive the moment the radar went blind.

**The watchdog must not resurrect a lie.** If the firmware watchdog resets the MCU, `FeederUptime`
returns to 0. Openpilot does not currently act on that, but it makes a reset visible in the route
log, which is why the field is in the message.

---

## 6. Counter and checksum

Both are in the DBC and **both must be populated**, even though `rear_radar.py` does not validate
them today — its `MESSAGES` list declares frequency only, so `can_valid` is a rate check.

They are specified now so that validation can be switched on later without reflashing the feeder.
A message format that omits them is a format that can never be verified.

- **`Counter`**: 4 bits, increments by one on every transmission of that message, wraps 15→0.
  Each of the three messages keeps its own counter.
- **`Checksum`**: 8 bits, in byte 7. Sum bytes 0-6 as unsigned, take the low 8 bits, and subtract
  from 0xFF (one's complement of the truncated sum). Compute it **last**, after the counter.

This is the simplest scheme that catches a stuck byte and a stuck frame, which are the two failure
modes a rate check misses.

---

## 7. Hardware

**Two CAN interfaces are mandatory.** One MCU with a single controller cannot do this job — the
whole point is that the two buses stay electrically separate. A single-bus design that bridges the
radar onto bus 1 is the thing this specification exists to prevent.

Requirements:

- 2× CAN 2.0B, 500 kbit/s both sides.
- Sustained RX of ~2150 frames/s on the radar side without dropping frames. That is the real
  constraint and it rules out the slower hobby boards. Budget the RX interrupt path first.
- Enough headroom to do 64 × (sin, cos) per scan at 33 Hz. A fixed-point or lookup-table azimuth
  transform is entirely adequate and avoids leaning on an FPU.
- Automotive power: 12 V in, tolerant of cranking dropouts and load dump, with a proper watchdog.
- Physically small and sealed enough to live near the rear bumper.

**Prior art:** eFiniLan's `ext-radar` addon is the closest existing thing in the openpilot world
and is the template to read before starting. Nobody in openpilot has done a *rear* radar, so there
is no reference implementation for the part that matters.

**Mounting** is a separate open item and needs three measurements not yet taken: cover-to-beam
depth, height above ground, and a caliper of the front MRR for comparison. Behind the unpainted
lower valance, low and central. Radar sees through unpainted plastic; metallic paint attenuates it.

---

## 8. Bench acceptance, before it goes near the car

Run every one of these on the bench with the radar and feeder on a table. The car is not a
debugging environment and the failure modes here are the kind that authorize a lane change.

1. **Rate.** All three messages arrive at 20 Hz ±1. `can_valid` goes true in openpilot.
2. **Sign of `VRel`.** A reflector moved *toward* the sensor produces **positive** `VRel`. Moved
   away, negative. This is the §4.4 trap and it must be demonstrated, not reasoned about.
3. **Sign of `YRel`.** A reflector on the car's left produces **positive** `YRel`. This is the
   §4.2 open question and the bench is what closes it.
4. **`DRel` scale.** A reflector at a measured 10 m reads 10 m ±0.5.
5. **Empty road.** Sensor pointed at open space: `Detected = 0` both sides, `RadarAlive = 1`,
   `DetectionHz` around 33, `dataAvailable` **true** in openpilot.
6. **Dead radar.** Unplug the radar. Within 300 ms: `RadarAlive = 0`, `DetectionHz = 0`, status
   message **still transmitting**, `dataAvailable` **false** in openpilot.
7. **Slow radar.** Force the detection rate under 10 Hz. `dataAvailable` false.
8. **No stale latch.** Present a target, remove it, confirm `Detected` clears on the next scan.
9. **TTC pick.** Two targets, one nearer and slow, one further and fast. The reported one is the
   one with the shorter time to contact.
10. **Bus load.** Measure bus 1 with the feeder live and confirm the added load is ~60 frames/s and
    that the car's own ADAS is undisturbed — no DTCs, front radar and camera unaffected.

**Then, and only then**, `PassingAssistRearRadar` and `PassingAssistActuate` are both already
defaulted on, so a fitted feeder makes the system live at the next boot. There is no further
software step, which is a reason to finish this list rather than most of it.

---

## 9. What happens once it works

`RearApproach` fills both sides with `source = radar`. `may_actuate` requires
`source == Source.radar` **on both sides** — so a single rear radar covering left and right is
sufficient, and BLIS alone never is.

From there `passing_assist_desire` reaches `desire_helper`, which performs the lane change, and
`PassingAssistBlinker` in the Ford carcontroller lights the lamp.

**Nothing in that chain has ever actuated, on any car.** The first drive with a feeder fitted is
the experiment, not the rollout. Read `bp_route_report` afterwards rather than assuming.

One known thing to watch on that first drive: this car's one-touch turn signal runs seven flashes,
so the lamp will keep flashing for a few seconds after a maneuver ends. The state machine does not
re-arm off it — our own commanded blinker is invisible to `carState`, because panda drops our TX —
but the lamp over-running the maneuver is real and will look wrong from the seat.
