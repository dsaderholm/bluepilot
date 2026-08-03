# Rear-facing radar for passing assist — research and integration plan

Research only. Nothing here has been implemented. Branch `passing-assist-phase1`, 2026-08-02.

Confidence is stated per section. Everything marked **UNVERIFIED** is a guess that has not been
checked against real frames or a primary source, and should be treated the way the BLIS
approach-detection hypothesis deserved to be treated.

---

## 0. Verdict first

**§6 (adjacent-lane traffic ahead) should be done first, and it works.** Confidence: **high** —
measured, not reasoned. See §6 for the numbers. It needs no hardware, touches 5 files, and one
of those changes is a single line.

**The rear radar is feasible but it is not a weekend.** Call it a month of evenings, and there
is one hard blocker that must be resolved *before any hardware is bought beyond the radar
itself*.

**Biggest risk of it not working at all** — not "taking longer", but "never producing a usable
target list":

> A Ford-programmed Delphi ESR may not track usefully when told the car is reversing, and may
> refuse to leave its fault / no-radiate state without the exact gateway frames Ford's own
> module sent it. Those frames are **not documented in any DBC in this repo** (`ESR.dbc` names
> the messages; `ford_fusion_2018_adas.dbc` documents only the 64 object messages and none of
> the status or gateway IDs) and I could not find a public source for them. Everything else in
> this plan is engineering. This one is an unknown.

**Second-biggest risk, and it is already confirmed rather than hypothetical:** bus 1 has no
headroom. Measured on three real Ford segments, bus 1 runs at **60–73 % of a 500 kbit/s bus**,
84 % of that being the MRR itself. An ESR adds another 37–45 %. **The rear radar cannot share
bus 1**, and all three C3X ports are already used. That forces the architecture in §3/§4: the
feeder microcontroller gets a *private* CAN bus for the ESR and emits a small digest onto bus 1.
That is more work, but it is also strictly better — openpilot then parses two messages instead
of sixty-four, and §5 collapses to almost nothing.

**Cheapest de-risking experiment, before a bracket, a radome, an MCU or a hole in the bumper:**

> Buy the salvage ESR and its pigtail (~$100–200 — money you have to spend anyway). Put it on a
> bench with a 12 V supply, a USB-CAN adapter at 500 kbit/s and a laptop. Log what it transmits
> with nothing being sent to it. Then send the four gateway frames from §4 and see what changes.
> The QUT characterisation paper ran an ESR exactly like this — statically, on a support, driven
> by a custom ROS driver — and got usable range/bearing data at 2–25 m, so a bench setup is a
> documented working configuration rather than a hope.

That one test answers the #1 risk for the price of the part you would have had to buy regardless.
If it fails, you have spent $150 and a Saturday instead of a month.

**Order of work:** §6 → bench-test the radar → decide → hardware.

---

## 1. Which radar, and what it costs

Confidence: **medium** on the part identification, **low** on price.

Buy the **front adaptive-cruise radar module from a 2013–2020 Ford Fusion / Fusion Energi with
the ACC option** — mounted behind the lower grille, not in the bumper corners.

⚠️ **Do not buy a `-14D453` part.** That part number family is the *blind-spot* (SODL/SODR)
radar. Searching for "Ford Fusion radar sensor" returns those first and they are the wrong
sensor entirely — they are the modules the BLIS investigation already established cannot answer
"is something closing". The ACC radar is a separate, larger, centre-mounted unit.

- Physical: **173.7 × 90.2 × 49.2 mm** including mounting features (Delphi datasheet). This is a
  brick, not a puck. Bumper-cavity depth is the binding constraint — see §2.
- Salvage price observed in listings for "cruise control distance radar sensor, Ford Fusion
  2017–2020": **$95–185**. **UNVERIFIED** as being the correct module — verify by photograph
  and physical size before buying, not by part number alone.
- Buy the **connector pigtail** with it. Cutting a sealed automotive connector off a donor
  harness is much easier than sourcing the mate.
- Aptiv/AutonomouStuff sell development ESR 2.5 units with documentation. They are $3–5 k. Not
  the route, but worth knowing they exist if the bench test stalls on protocol.

Specification (Delphi datasheet + QUT characterisation paper, **high** confidence):

| | |
|---|---|
| Long range | 174 m within **±10°** |
| Mid range | 60 m within **±45°** |
| Update rate | 20 Hz |
| Tracks | 64 simultaneous |
| CAN | **500 kbit/s nominal** |
| Range accuracy / resolution | ±0.25 m / 0.1 m |
| Bearing accuracy / resolution | ±0.5° / 0.1° |
| Mount height (Delphi manual) | **30–86 cm above ground** |

### Correcting the geometry in the brief

The brief used **±51.1°** from `CAN_TX_TRACK_ANGLE`'s range. That is the *signal's reporting
range*, not the antenna's field of view. The real FOV is two modes, and using them changes the
answer — for the better:

Rear-centre mount, adjacent lane centre at 3.7 m lateral, angle off boresight = `atan(3.7/d)`:

| distance behind | angle | in mid-range beam (±45°, ≤60 m) | in long-range beam (±10°, ≤174 m) |
|---|---|---|---|
| 3.7 m | 45.0° | edge | no |
| 5 m | 36.5° | yes | no |
| 10 m | 20.3° | yes | no |
| 21 m | 10.0° | yes | edge |
| 60 m | 3.5° | edge | yes |
| 174 m | 1.2° | no | yes |

The two modes run **simultaneously**, so the union is continuous: **~3.7 m to 174 m** in the
adjacent lane, with no gap. The brief's "3–200 m, blind exactly where BLIS starts" was
directionally right and slightly optimistic on both ends. The complementarity claim holds and is
now quantified: BLIS covers roughly the mirror to ~7 m back; the ESR starts at ~3.7 m. They
**overlap**, which is better than abutting.

---

## 2. Mounting and alignment

Confidence: **medium** on mounting, **medium** on the alignment signal semantics, **low** on
whether rear-facing alignment converges at all.

### Mounting

- **Height**: 30–86 cm per the Delphi manual. A Fusion's rear bumper lower fascia sits roughly
  in the middle of that. Not a constraint in practice. **UNVERIFIED** for this specific car —
  measure.
- **Depth**: 49.2 mm behind the bumper cover, plus bracket and connector backshell. Typical
  cover-to-beam clearance on a Fusion rear bumper is 40–70 mm. **This is the tightest fit in the
  whole project and it is unverified.** Measure before buying a bracket. If it does not fit
  behind the cover, the alternatives are a licence-plate-height external mount (ugly, exposed,
  but trivially aligned) or relocating to the bumper beam with an aperture cut in the cover.
- **Seeing through the cover**: front radar apertures on ADAS cars are specified for it —
  restricted paint systems, controlled thickness, sometimes a separate radome. **A rear bumper
  cover has none of that.** Metallic and pearl paints contain aluminium flake and attenuate
  76 GHz badly. If the car is a metallic colour, assume this is a real problem until measured.
  Mitigation: cut an aperture and fit a plain-plastic radome, or bench-test the attenuation with
  a cut-off piece of the actual cover between radar and target.
- **Pitch/elevation**: `ESR.dbc` has **no elevation offset signal** — `ANGLE_MOUNTING_OFFSET`
  and `ANGLE_MISALIGNMENT` are both azimuth only. Elevation must therefore be **mechanically
  correct**; there is no CAN fix. Shim the bracket, verify by whether the sensor sees the road
  surface or nothing.
- **Vibration and weather**: the module itself is a bumper-mounted automotive sensor and is
  rated for it. The *bracket* is the weak point. The connector must face down or be sealed.
- **Power**: switched ignition feed, fused. Comparable radar retrofits note >0.2 A; assume
  ~0.5 A / 6 W. **UNVERIFIED** for the ESR specifically.

### Alignment

All of the following are read directly from the in-tree `ESR.dbc` — **high** confidence that the
signals exist, **medium** confidence in the semantics:

- `SensorInput` (0x4F1, Gateway→ESR): `CAN_RX_ANGLE_MISALIGNMENT` (−8…+7.9375°, 0.0625° steps),
  `CAN_RX_USE_ANGLE_MISALIGNMENT`, `CAN_RX_LATERAL_MOUNTING_OFFSET` (±2 m).
- `Vehicle_Data2` (0x5F2, Gateway→ESR): `CAN_RX_ANGLE_MOUNTING_OFFSET` (±8°),
  `CAN_RX_AUTO_ALIGN_DISABLE`, `CAN_RX_AUTO_ALIGN_CONVERGED`, `CAN_RX_SERV_ALIGN_TYPE`,
  `CAN_RX_SERV_ALIGN_UPDATES_NEED` (0–255), `CAN_RX_AALIGN_AVG_CTR_TOTAL` (250–2000),
  `CAN_RX_RADAR_HEIGHT` (cm), `CAN_RX_RADAR_FOV_LR` (0–31°), `CAN_RX_RADAR_FOV_MR` (0–127°).
- `ESR_Output_InPath` (0x4E3, ESR→Gateway): `CAN_TX_AUTO_ALIGN_ANGLE` (±8°) — the live readback.

**So: alignment is a stored ±8° azimuth offset settable over CAN, plus an auto-alignment routine
that converges over driving with a progress counter.** It can be performed with a laptop on the
bus. No target board is needed for auto-align; it uses road clutter. A "service alignment"
(`SERV_ALIGN_TYPE`) may want a defined target at a measured distance — **UNVERIFIED**.

**Correction to the brief:** `FactoryAlignment` (0x5F3) is declared in `ESR.dbc` with **zero
signals** — an empty shell. Its payload is unknown and it cannot be used. The brief's inference
that "alignment is commanded over CAN rather than shimmed mechanically" is correct, but the
evidence for it is `Vehicle_Data2` and `SensorInput`, not `FactoryAlignment`.

Because the CAN offset range is only ±8°, **the bracket must be mechanically within ~±8° of
straight back.** That is a loose tolerance but not a free one.

### The rear-facing problem, and the proposed trick

Confidence: **low**. This is the idea the whole project hinges on and it is untested.

Auto-alignment works by observing stationary clutter and assuming it closes at −v_ego along the
boresight. A rear-facing sensor fed the car's true forward speed sees every stationary object
*opening* at +v_ego. Auto-align will not converge, and moving/stationary classification,
`ONCOMING` and probably track initiation will all be wrong.

`Vehicle_Data` (0x4F0) carries `CAN_RX_VEHICLE_SPEED` as an **unsigned magnitude** plus a
separate `CAN_RX_VEHICLE_SPEED_DIRECTION` (0 = Forward, 1 = Reverse). So:

> **Feed the ESR `SPEED = |v_ego|` with `SPEED_DIRECTION = Reverse` while the car drives
> forward.** A rear-facing sensor on a forward-moving car experiences exactly what a
> forward-facing sensor on a reversing car experiences. If the ESR's internal model is written
> in terms of that flag, this makes it correct for free.

If it does not work, the fallback is `AUTO_ALIGN_DISABLE = 1` plus a hand-measured
`ANGLE_MOUNTING_OFFSET`, and accepting that `ONCOMING` and the stationary filter are garbage —
which is survivable, because we consume raw `Target1..64`, not the ESR's own ACC outputs.

Yaw rate does **not** need a sign flip: rotating the sensor 180° about the vertical axis leaves
the z-axis direction unchanged. Lateral position **does** — the radar's "left" is the car's
right. That is one of three sign decisions in this data path (see §7).

---

## 3. Wiring and bus — and why sharing bus 1 is off the table

Confidence: **high** on the measurement, **medium** on how it transfers to this specific car.

### The measurement

Three 60-second `FORD_EXPLORER_MK6` segments from `commaCarSegments` (same `FORD_CADS` Delphi
MRR as the Edge unit on this car), replayed through the production `RadarInterface`:

```
bus 1: 2686 frames/s, every frame 8 data bytes
       of which MRR (0x120–0x174): 2266 frames/s = 84 %
addresses in the ESR window 0x4E0–0x5F4: NONE   (confirms the brief)
```

A standard CAN 2.0A frame with 8 data bytes is 111 bits including the 3-bit interframe space,
plus 0–24 stuff bits.

| | frames/s | kbit/s | % of 500 kbit/s |
|---|---|---|---|
| bus 1 today (measured) | 2686 | 298–363 | **60–73 %** |
| …of which the MRR alone | 2266 | 252–306 | 50–61 % |
| ESR + its status frames + feeder | ~1660 | 184–224 | **37–45 %** |
| **combined** | ~4350 | 482–587 | **97–118 %** |

Even if this car's bus 1 carries *only* the MRR — plausible for a retrofit, **UNVERIFIED** —
the total is 87–106 %. A CAN bus is unusable much above 70 % sustained.

And the failure would be targeted rather than random: **ESR IDs 0x4E0–0x53F are numerically
higher than the MRR's 0x120–0x174, so the ESR loses every arbitration contest.** The messages
that get delayed and dropped are precisely the rear-radar targets.

*(Caveat: the Explorer's bus 1 carries ~420 frames/s of non-MRR gateway traffic. Whether this
retrofit's bus 1 does too is unknown and worth 10 minutes with an existing log — it changes the
headroom number but not the conclusion.)*

### Bit rate

Both sides are 500 kbit/s: Ford HS-CAN is 500 kbit/s, and the ESR's nominal bus speed is
500 kbit/s (QUT paper, and consistent with every ESR integration in public use). **They match.**
Confidence: **high**.

### The architecture this forces

Three CAN channels on the feeder MCU. A Teensy 4.0/4.1 has three CAN controllers and is ~$30.

- **Channel A — private ESR bus.** 500 kbit/s. Two nodes only: MCU and ESR. ~1660 frames/s,
  ~40 % loaded, which is fine on a dedicated bus. Short run (bumper to MCU), so stub length is a
  non-issue. **Terminate at both physical ends with 120 Ω**; measure the ESR's internal
  resistance across CANH/CANL first — if it reads ~120 Ω it already has one and you fit only one
  more.
- **Channel B — bus 0, LISTEN-ONLY.** Source of vehicle speed (`BrakeSysFeatures.Veh_V_ActlBrk`,
  50 Hz, the same signal Ford `CarState` uses for `vEgo`), yaw rate and steering angle.
  Listen-only mode makes the MCU *electrically incapable* of writing to the powertrain bus. This
  is the strongest safety statement available and it sidesteps the panda TX question entirely.
- **Channel C — bus 1, TX only.** Two digest messages at 20 Hz = ~40 frames/s, **under 1 % added
  load**. `get_fwd_bus()` in `opendbc/safety/safety.h` maps only 0↔2, so bus 1 is an island and
  this traffic can never reach a bus that actuates the car. Confirmed in-tree.

Pick digest IDs that are absent from this car's bus 1 — verify against the driver's own log
rather than assuming. Note that `ford_lincoln_base_pt.dbc` puts `BoundaryAlert_Left_1..4` at
0x4E0–0x4E3, colliding with `ESR_Status` and `ESR_Output_InPath`; that is on **bus 0**, which is
another reason the ESR must never go there.

**Nothing needs to be added to `FORD_COMMON_TX_MSGS`.** openpilot transmits nothing to the ESR.
Panda safety is untouched. This was the brief's preferred outcome and it is the correct one.

---

## 4. The feeder microcontroller's exact job

Confidence: **medium** on which frames (the DBC names them; direction is `Gateway`→ESR),
**low** on the exact contents required for the ESR to actually radiate. This is the part the
bench test exists to settle.

Decoded from `ESR.dbc`. Rates are proposals consistent with a 20 Hz sensor, **UNVERIFIED**.

### `Vehicle_Data` — 0x4F0, 8 bytes, 50 Hz

| signal | scale | value to send |
|---|---|---|
| `CAN_RX_VEHICLE_SPEED` | 0.0625 m/s, unsigned | `abs(v_ego)` |
| `CAN_RX_VEHICLE_SPEED_DIRECTION` | 0=Fwd 1=Rev | **`1` while driving forward** — see §2 |
| `CAN_RX_YAW_RATE` | 0.0625 °/s, signed | vehicle yaw rate |
| `CAN_RX_YAW_RATE_VALIDITY` | | `1` |
| `CAN_RX_RADIUS_CURVATURE` | 1 m, signed | `v² / yaw_rate`, clamped to ±8191 |
| `CAN_RX_STEERING_ANGLE` + `_SIGN` | 1°, magnitude + sign bit | steering wheel angle |
| `CAN_RX_STEERING_ANGLE_RATE` + `_SIGN` | 1 °/s | derivative |
| `CAN_RX_STEERING_VALIDITY` | | `1` |

### `SensorInput` — 0x4F1, 8 bytes, 50 Hz

This is the control frame and it contains the handshake.

| signal | value |
|---|---|
| `CAN_RX_SCAN_INDEX_ACK` | **echo the last `ESR_Status.CAN_TX_SCAN_INDEX` (0x4E0)** |
| `CAN_RX_RADAR_CMD_RADIATE` | `1` |
| `CAN_RX_MAXIMUM_TRACKS` | `64` |
| `CAN_RX_GROUPING_MODE` | try `3` (group moving + stationary) |
| `CAN_RX_ANGLE_MISALIGNMENT` / `_USE_` | alignment offset, 0.0625° |
| `CAN_RX_LATERAL_MOUNTING_OFFSET` | lateral offset from vehicle centreline |
| `CAN_RX_BLOCKAGE_DISABLE` | `1` initially — a bumper cover may read as blockage |
| `CAN_RX_CLEAR_FAULTS` | one-shot on startup |
| `CAN_RX_MMR_UPSIDE_DOWN` | only if the bracket inverts the unit |

> **The scan-index acknowledgement is the most likely single point of failure.** If the ESR gates
> transmission on seeing its own scan index echoed back, and the loop is not closed correctly,
> the radar will sit silent and every other part of this plan is untestable. It is also the
> first thing the bench test will reveal.

### `Vehicle_Data2` — 0x5F2, 8 bytes, 50 Hz

`CAN_RX_LAT_ACCEL` / `CAN_RX_LONG_ACCEL` (0.03125 m/s²) with validity bits;
`CAN_RX_RADAR_HEIGHT` (cm, as measured); `CAN_RX_RADAR_FOV_LR = 10`, `CAN_RX_RADAR_FOV_MR = 45`;
`CAN_RX_AUTO_ALIGN_DISABLE` (start at `0`, fall back to `1`);
`CAN_RX_ANGLE_MOUNTING_OFFSET` (±8°, 0.0625°); `CAN_RX_WHEEL_SLIP = 0`.

### `VehicleData3` — 0x5F4, 8 bytes, 10 Hz

`CAN_RX_WHEELBASE = 285 cm`; `CAN_RX_STEERING_GEAR_RATIO = 17.07` (both from
`FORD_FUSION_MK5`'s `CarSpecs`); `CAN_RX_DISTANCE_REAR_AXLE`; `CAN_RX_FUNNEL_OFFSET_LEFT/RIGHT`;
`CAN_RX_BEAMWIDTH_VERT`; `CAN_RX_CW_BLOCKAGE_TRESHOLD`; `CAN_RX_OVERSTEER_UNDERSTEER = 0`;
`CAN_RX_YAW_RATE_BIAS_SHIFT = 0`.

⚠️ `CAN_RX_DISTANCE_REAR_AXLE` is `(2, 200)` cm — **minimum expressible value 2.00 m**. A
rear-mounted sensor is well under 2 m from the rear axle, so this cannot be expressed honestly.
It only feeds the ESR's own path prediction, which drives `ESR_Output_InPath` and
`CIPV_Targets_Etc` — messages we do **not** consume. Send the minimum and ignore the consequence.

### Where the feeder gets its data

**Not from bus 1** — the ESR is on its own bus, and whether this car's bus 1 even carries vehicle
speed is unknown. Channel B reads bus 0 listen-only: `BrakeSysFeatures` for speed,
`Yaw_Data_FD1` for yaw, `Steering_Data_FD1` / `SteeringPinion_Data` for steering angle. All three
are confirmed present on bus 0 for this platform via `carstate.py`.

### What the feeder sends back to openpilot (channel C)

Recommendation: **send the nearest few raw-ish targets, not a decision.**

Two 8-byte messages at 20 Hz, two targets each: `dRel` (11 bits, 0.1 m), `yRel` (10 bits signed,
0.1 m), `vRel` (14 bits signed, 0.01 m/s), `status` (3 bits), `valid` (1 bit) = 39 bits/target.

The side/closing/TTC policy then stays in `rear_approach.py`, where `MIN_CLOSING_MS` and
`UNSAFE_TTC_S` are already tunable and where changing a threshold does not mean reflashing a
microcontroller in a car park.

---

## 5. Code changes for the rear radar — file by file, described not written

Confidence: **high**. The digest architecture makes this small.

The key structural fact: **`CANParser(dbc_name, messages, bus)` takes a DBC name directly.** The
`DBC[CP.carFingerprint][Bus.radar]` single-DBC-per-bus limit is a convention inside
`RadarInterface`, not a constraint of the parser. Two parsers on one bus are fine. But with the
digest we do not even need that — the rear data is two custom messages, not 64 ESR targets.

| # | file | change | additive? |
|---|---|---|---|
| 1 | `opendbc_repo/opendbc/dbc/bp_rear_radar.dbc` | **new**, 2 messages | additive |
| 2 | `selfdrive/car/card.py` | a small `RearRadarParser` alongside `self.RI`; publish `rearRadarBP` | ~12 lines, additive |
| 3 | `cereal/custom.capnp` | new `RearRadarBP` struct: `dataAvailable`, list of targets | additive |
| 4 | `cereal/services.py` | `"rearRadarBP": (True, 20., 5)` | one line |
| 5 | `selfdrive/controls/plannerd.py` | add `'rearRadarBP'` to the SubMaster | one line |
| 6 | `sunnypilot/.../rear_approach.py` | fill in `update(sm)` — nearest closing target per side → existing `from_radar()` | ~20 lines, the module was built for this |
| 7 | `common/params_keys.h` + `ui/sunnypilot/layouts/settings/cruise.py` | `PassingAssistRearRadar` toggle + TTC threshold | additive |
| 8 | `sunnypilot/.../tests/test_rear_approach.py` | **new** | additive |

`passing_assist.py` needs **no change at all** — the gate is already wired and already ordered
correctly. That was the point of building the consumer first, and it paid off.

### Keeping rear detections out of the lead path

This is **not** a matter of filtering. `radard` subscribes to exactly one radar source:

```python
sm = messaging.SubMaster(['modelV2', 'carState', 'liveTracks'], poll='modelV2')
RD.update(sm, sm['liveTracks'])
```

`liveTracks` is published only by `card` from `self.RI.update()`. **Nothing routes
automatically.** As long as the rear parser is a separate object publishing a separate message
and never touches `RI.pts`, rear detections cannot reach `radard`, `radarState`, or the leads. It
is purely additive. Confidence: **high**, read directly.

### On message rate and `carStateBP`

`carStateBP` is declared at 100 Hz and is already over-published — the brief is right to flag it.
The tempting shortcut is to hang the rear targets off it, since `plannerd` already subscribes and
`rear_approach.update(sm)` would need nothing new. **Don't.** That would republish 20 Hz sensor
data five times over, and it would put a hardware-optional feature into a message every BluePilot
Ford emits. A separate 20 Hz `rearRadarBP` costs one line in `services.py` and one in the
SubMaster, and it publishes at the rate the sensor actually runs. Treat `carStateBP`'s 100 Hz as
a mistake not to repeat, not a precedent.

### Does anything else need to know?

No. `card` gains a parser. `plannerd` gains a subscription. `radard`, the planners, the
controllers, panda and the safety layer are all untouched. `RadarInterface` is still instantiated
once and still returns one `RadarData`; nothing about it assumes singleton-ness in a way this
plan disturbs, because this plan does not create a second one.

---

## 6. Adjacent-lane traffic ahead — verdict: **yes, and do it first**

Confidence: **high**. This is measured, not argued.

The follow-up correctly established that off-path detections exist by construction:
`_update_delphi_mrr` reads a per-detection azimuth, derives `yRel`, and applies no lateral
filter — only `CAN_DET_VALID_LEVEL`, a scan-index match, and a 30 m ground-return guard on
long-range mode. `do_clustering()` then produces tracked objects with stable `trackId`, so
`liveTracks` carries clustered vehicles rather than raw returns. That left one empirical
question: **does the MRR actually return adjacent-lane targets in practice?**

### Method

The public `commaCarSegments` segments contain only `can`, `pandaStates` and `carParams` — no
`liveTracks`. That turned out to be an advantage: rather than reading a recorded message, I
replayed the raw CAN through the **production `RadarInterface`**, so these are the exact points
`card` would have published. Vehicle speed came from the real bus-0 `BrakeSysFeatures.Veh_V_ActlBrk`
— the same signal Ford `CarState` uses — so "moving vs stationary" is not inferred from the radar
itself. Three 60 s `FORD_EXPLORER_MK6` segments (same `FORD_CADS` MRR as this car's Edge unit).

### Results

**Publish rate is 8.3 Hz, not the 20 Hz declared in `services.py`.** `_update_delphi_mrr` returns
early unless `headerScanIndex == 3`, so `liveTracks` for a Delphi MRR emits once per four scan
modes: 33 Hz / 4 = 8.25 Hz. Measured 8.3 Hz in all three segments. Worth knowing before writing
anything that assumes 20 Hz.

Adjacent-lane band, |yRel| 2.5–5.5 m:

| segment | v_ego | pts/frame in band | frames with ≥1 | moving tracks living ≥1 s |
|---|---|---|---|---|
| 1 (highway) | 27.2 m/s | 0.84 | 44 % | 11 |
| 2 (arterial) | 16.6 m/s | 5.88 | 99.8 % | 14 |
| 3 (urban) | 5.5 m/s | 2.90 | 91 % | 23 |

Individual tracks, after separating traffic from scenery by absolute speed:

```
id1618  life  4.97s  dRel 131.5 m  |yRel| 3.5 m  vRel  +2.2  v_abs +29.4 m/s
id1525  life  5.94s  dRel  65.1 m  |yRel| 5.4 m  vRel  -0.9  v_abs +26.4 m/s
id3593  life 28.73s  dRel  19.6 m  |yRel| 3.4 m  vRel  +1.1  v_abs +19.0 m/s
id14    life 26.42s  dRel  32.5 m  |yRel| 3.6 m  vRel  -1.3  v_abs +14.8 m/s
id1578  life 13.09s  dRel  37.3 m  |yRel| 3.5 m  vRel  +0.4  v_abs +13.8 m/s
```

**Range**: adjacent-lane vehicles are tracked out to at least **131 m** with plausible
kinematics. That is comfortably inside `PassingAssistMaxDistance` (220 m default) for the
question being asked.

**Noise**: the honest caveat. Median lifetime of an adjacent-band track is **0.12–0.48 s** — most
are one- or two-frame flickers. But in-path moving tracks behave identically (median 0.12 s), so
this is how MRR clustering behaves generally, not an adjacent-lane deficiency. `radard`'s Kalman
tracker and model fusion is what cleans it up for leads; an adjacent-lane consumer needs its own,
much simpler, equivalent.

**Stability of the actual decision** — "is there a moving vehicle in the target lane, ahead,
within 220 m", with and without a 3-consecutive-frame persistence filter:

| segment | v_ego | filter | occupied | transitions/min | longest run |
|---|---|---|---|---|---|
| 1 | 27.2 m/s | raw | 40.2 % | 59 | 7.2 s |
| 1 | 27.2 m/s | **3 frames** | 28.8 % | **19** | 6.9 s |
| 2 | 16.6 m/s | raw | 97.0 % | 19 | 25.5 s |
| 2 | 16.6 m/s | **3 frames** | 90.8 % | 19 | 25.2 s |
| 3 | 5.5 m/s | raw | 72.0 % | 97 | 12.2 s |
| 3 | 5.5 m/s | **3 frames** | 43.0 % | 50 | 11.9 s |

A 3-frame filter (~0.36 s) cuts transitions by two thirds at highway speed while leaving the long
continuous runs intact. And the noisiest case — segment 3, urban, 50 transitions/min — is already
excluded by `MIN_V_EGO_MS` (40 mph), which passing assist gates on before anything else.

### Answers to the specific questions

- **Does the MRR return usable off-path points?** Yes, to 131 m in the band, with vehicle-like
  persistence and kinematics.
- **Effective coverage?** Lateral: the band is well populated from 2.5 m outward. Range: usable
  to ~130 m for tracked vehicles; the histogram has returns to 168 m but those are mostly
  clutter.
- **Sign convention?** **Opposite, and both live in this repo.** Radar `yRel` is left-positive
  (`radar_interface.py:316`). Model lane geometry is left-negative — `ldw.py:31` tests
  `lane_lines[1].y[0] > -(1.08 + CAMERA_OFFSET)`, and `passing_assist.py` documents the same. One
  of them must be flipped. Do not assume which; assert it in a test against a recorded lane
  change on this car.
- **Cost of consuming `liveTracks`?** One line in `plannerd`'s SubMaster — it is **not** there
  today. It publishes at 8.3 Hz on this radar (not 20), so the observer must tolerate staleness
  relative to the 20 Hz `modelV2` poll.
- **Good enough to answer "will I immediately be stuck again"?** **Yes, with the persistence
  filter and at the speeds passing assist actually operates at.** Not good enough raw.

### Files for §6

| # | file | change |
|---|---|---|
| 1 | `selfdrive/controls/plannerd.py` | add `'liveTracks'` to the SubMaster — one line |
| 2 | `sunnypilot/.../adjacent_lane.py` | **new**: sign flip, band per side, `v_abs = vRel + vEgo` with `|v_abs| < 3 m/s` rejected as scenery, ≥3 consecutive publishes on the same `trackId`, report nearest qualifying target per side |
| 3 | `sunnypilot/.../passing_assist.py` | veto a side whose target lane already holds a slower vehicle inside the horizon; log it either way |
| 4 | `cereal/custom.capnp` | per-side `adjacentDRel` / `adjacentVAbs` / `adjacentAvailable` on `PassingAssist` |
| 5 | `common/params_keys.h` + `ui/.../cruise.py` | toggle + threshold |

⚠️ **`self.points` holds deliberately doubled values** — appended as `[dRel, yRel*2, distRate*2]`
so the clustering threshold behaves across dimensions, and divided by 2 on the way out. The
published `RadarPoint` is correct; reading `self.points` directly silently gives double the
lateral offset and double the relative speed. `adjacent_lane.py` must consume the published
`liveTracks` points and never reach into the radar interface.

This replaces the anti-weave settle timer with an actual measurement, and it is a lane-selection
input for free.

---

## 7. Feasibility

### Prior art

Confidence: **medium-high** that there is none for the specific thing.

- **[openpilot-ext-radar-addon](https://github.com/eFiniLan/openpilot-ext-radar-addon)** — the
  closest and the only real precedent. Adds a radar to a car that has none, front-facing, wired
  to the comma device over the OBD2 harness, no microcontroller, no factory radar to coexist
  with. It even documents the problem this plan is dominated by: *"CAN Message Conflict: this
  proof of concept may trigger errors due to conflicts. A CAN filter or gateway can resolve
  them."* That is the digest MCU, arrived at independently.
- HKG forks reconfigure factory radars over UDS to emit debug points on bus 1 — that is
  *reconfiguration*, not addition.
- Radar *replacement* (swapping a factory unit, or retrofits for radar-less cars) is a
  meaningfully easier problem and is well-trodden. Running **two radars simultaneously, one
  rear-facing, on a car whose radar bus is already 60–73 % loaded** — I found nobody who has done
  it.

That is a real signal about difficulty. It is not a reason to stop, but it does mean there is no
one to ask when the ESR sits silent on the bench.

### Architectural fit

openpilot **accommodates** this, and the digest architecture is why:

- `RadarInterface` is instantiated once and returns one `RadarData`. This plan never creates a
  second one — the rear path is an independent parser publishing an independent message. Nothing
  assumes singleton-ness that we disturb.
- Keeping rear detections out of the lead path is a matter of **not calling it**. `radard`
  subscribes to `liveTracks` alone; nothing routes automatically.
- The rear data needs its own message: a new 20 Hz `rearRadarBP`, not a field on `carStateBP`.
  See §5.
- `card`, `plannerd` and the safety layer need nothing. `card` gains a parser, `plannerd` gains a
  subscription. Purely additive.

### Effort, by who can do it

**Bucket 1 — agent alone, verifiable at the desk.**

- §6 in full: 5 files, 1 new, two one-line insertions. **~1 day.**
- Rear radar openpilot side: 8 files, 3 new, everything else additive. **~1 day.**
- Unit tests for both, `rear_approach.py` sign handling, the persistence filter, UI controls.

**Bucket 2 — agent writes, only the driver can verify.**

- The `CAN_TX_TRACK_ANGLE` sign, against real frames.
- Whether `SPEED_DIRECTION = Reverse` makes a rear-facing ESR behave.
- Whether the four gateway frames actually make it radiate, and whether the scan-index handshake
  is required.
- Whether a Ford-programmed ESR emits `ESR_Status` (0x4E0) at all.
- The digest IDs being free on this car's bus 1; whether bus 1 carries vehicle speed.
- Real bus-1 load on this car rather than an Explorer's.
- The §6 sign flip against real `modelV2` lane lines, and refitting the 3-frame filter and the
  2.5–5.5 m band from this driver's own routes.

**Bucket 3 — driver only.**

Buy radar + pigtail. Bench rig (12 V supply, USB-CAN adapter). Bracket. Bumper aperture and
radome if needed. Elevation shim. Power feed and fuse. Build the MCU: Teensy + three
transceivers, one in listen-only. Termination measurement and fit. Mounting. Alignment.

### Where an agent is likely to be wrong

All three previous crash-loops were code that was correct against a convenient stand-in and wrong
against the real thing. These have that exact shape:

1. **`CanBusBase` offset — I hit this today, in this session.** Building a `CarParams()` by hand
   without `safetyConfigs` gives `offset = 4 * (0 - 1) = -4`, so `CanBus(CP).radar == -3`, every
   parser matches no bus, and the trigger message never fires. The interface returns `None`
   forever with **no error**. A unit test with a hand-built CP passes; the car sees nothing. Any
   test for a new parser must use a `CarParams` from a real log or the car.
2. **The `ANGLE` sign.** `ESR.dbc` says `(0.1, 0)`, `ford_fusion_2018_adas.dbc` says `(-0.1, 0)`
   for the same bits of the same message on the same hardware. Worse: **the branch that consumes
   it is dead code.** No platform in `values.py` maps to `RADAR.DELPHI_ESR` — every Ford is
   `FORD_CADS`, `FORD_CADS_64` or `ford_lincoln_base_pt`. `_update_delphi_esr` has never run on a
   supported car in this repo, so its "left is positive" comment is an assertion, not a
   validation.
3. **`RANGE_RATE` bit layout — a second disagreement the brief did not catch.** `ESR.dbc` places
   it at `53|14@0-`, `ford_fusion_2018_adas.dbc` at `52|13@0-`. The 13-bit version is the 14-bit
   version minus its MSB and saturates at **±40.96 m/s** instead of ±81.92. Within that range
   both decode identically (the extra bit is sign extension), so it will look correct — until
   oncoming traffic on an undivided road exceeds 40.96 m/s relative and the sign wraps. For a
   *rear* radar that is exactly the case that matters.
4. **The small-angle approximation.** `_update_delphi_esr` computes
   `yRel = X_Rel * Angle * DEG_TO_RAD`. At the ±10° a front radar works at, that is fine. At the
   ±45° a rear radar's mid-range mode works at, `sin(45°) = 0.707` versus `0.785 rad` — **11 %
   error, growing with angle.** Must use `range * sin(angle)`.
5. **Three sign decisions in one data path.** The radar's `ANGLE` sign (unknown); the 180°
   rotation making the radar's left the car's right; and the model's left-negative convention
   versus radar left-positive. A test double gets all three right by construction because the
   author picks the fixture to match the code. Only a recorded pass on a known side settles it.
6. **Bench proves nothing about the road.** The QUT setup got clean static measurements from an
   ESR on a support. That says the radar talks. It says nothing about auto-alignment converging
   at 70 mph facing backwards.

### The honest verdict

**§6: do it now.** One day, no hardware, measured to work, and it improves the lane-selection
decision more than the settle timer it replaces.

**Rear radar: a month of evenings, gated on one bench test.** Not a weekend — the three-channel
MCU alone is a project. But not "should not be started", either: the sensing case is sound, the
geometry works out better than the brief assumed, the code integration is genuinely small, and
the one hard blocker can be tested for $150 before any other money or bumper is committed.

**Do not buy anything except the radar and its pigtail until the bench test passes.**

---

## Appendix — corrections to the brief

Things the brief asserted that turned out to be wrong or incomplete. All verified in-tree.

1. **`FactoryAlignment` (0x5F3) has zero signals** in `ESR.dbc`. The conclusion that alignment is
   commanded over CAN is right, but it rests on `Vehicle_Data2` and `SensorInput`, not on this
   message, which cannot be used at all.
2. **±51.1° is the reporting range of `CAN_TX_TRACK_ANGLE`, not the antenna FOV.** The real FOV
   is 174 m at ±10° and 60 m at ±45°, simultaneously. Adjacent-lane coverage is ~3.7–174 m.
3. **The ANGLE disagreement is not the only one.** `RANGE_RATE` differs too — 14 bits vs 13,
   ±81.92 vs ±40.96 m/s.
4. **`_update_delphi_esr` is dead code.** No platform maps to `RADAR.DELPHI_ESR`. Whatever it
   does today has never been validated against a car.
5. **"No CAN ID collision" is true for `FORD_CADS` and confirmed on real bus-1 traffic, but
   `ford_lincoln_base_pt` puts `BoundaryAlert_Left_1..4` at 0x4E0–0x4E3**, which collide with
   `ESR_Status` and `ESR_Output_InPath`. Bus 0 only — one more reason the ESR goes on a private
   bus.
6. **Sharing bus 1 was assumed to be an open question; it is closed.** Measured 60–73 % occupancy
   before adding anything, and the ESR would lose arbitration to the MRR by ID priority.
7. **`liveTracks` publishes at 8.3 Hz on a Delphi MRR**, not the 20 Hz declared in
   `services.py` — `_update_delphi_mrr` emits once per four scan modes.
