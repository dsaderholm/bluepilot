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

---

## 8. Hardware and cost — revised: buy a second MRR, not an ESR

Added 2026-08-03. Confidence: **high** on the DBC evidence (checked in-tree), **medium** on
prices (observed listings, not quotes).

### The finding that changes the part choice

§1 says buy a Delphi ESR, and §0 names the reason it might never work: a Ford-programmed ESR may
refuse to leave its no-radiate state without gateway frames that are documented nowhere.

Compare the two DBCs in this repo:

```
ESR.dbc         BU_: Gateway ESR      <- two nodes. Something has to FEED it.
FORD_CADS.dbc   BU_: MRR              <- one node. Every message is FROM the radar.
```

Every `BO_` in `FORD_CADS.dbc` is sent by `MRR`: the 64 detection messages, four headers, the
status and fault frames, the XCP and diagnostic responses. **Nothing is addressed to it.** And
openpilot transmits nothing to any radar — `FORD_COMMON_TX_MSGS` is `Steering_Data_FD1`,
`ACCDATA_3`, `Lane_Assist_Data1`, `IPMA_Data`, all on bus 0 and 2.

Which means the MRR **cannot know the vehicle speed**, because nothing tells it. So it cannot be
running the speed-dependent stationary/oncoming classification or the auto-alignment convergence
that §2's "feed it `SPEED_DIRECTION = Reverse`" trick exists to work around. There is no internal
model to confuse by pointing it backwards. It reports range, azimuth and range-rate, and
`_update_delphi_mrr` clusters them in openpilot.

That removes the project's #1 risk rather than mitigating it — and note the trick is *impossible*
with an MRR anyway, since there is no channel to send it anything on. The two facts cancel.

Caveat, stated plainly: a single-node DBC is openpilot's view, not a bus capture. Ford could send
the MRR something in a message openpilot never decodes. The bench test settles it in ten minutes
— power the radar alone on a bench and see whether detections appear.

### Three further advantages

1. **The decoder already exists and is exercised.** `_update_delphi_mrr` runs on this car every
   drive. An ESR would need a new `radar_interface` path written against an undocumented protocol.
2. **A known-good reference.** The car already contains a working one. Bench output can be
   compared against the front radar's live output frame for frame — a debugging position nobody
   doing this with an ESR has.
3. **The feeder MCU loses a channel.** §3 specified three: private ESR bus, bus 0 listen-only for
   vehicle speed, bus 1 digest out. With an MRR there are no gateway frames to send and no vehicle
   data to relay, so channel B disappears. **Two channels, and a $15 dual-transceiver board covers
   it.**

Bus 1 still has no headroom (§3), so the digest architecture stands unchanged.

### Which part, exactly

### `JX7T-9G768-AC` — buy a second one of exactly what is already fitted

Corrected 2026-08-03 with the owner's own part numbers, which beat any cross-reference table:

- `HG9T-9G768-AG` — the **stock Fusion** radar, **removed from this car and sold**. Not the part.
- `JX7T-9G768-AC` — the **F-150 unit currently fitted and working**, feeding `FORD_CADS` over
  classic 500 kbit/s CAN on bus 1, decoded every drive by `_update_delphi_mrr`.

**Buy another `JX7T-9G768-AC` (or `-AD`).** This is a stronger recommendation than "an MRR":
identical hardware, identical protocol, identical decode path, and a live reference in the same
vehicle to compare bench output against. Nothing needs cross-referencing or verifying by
photograph.

It also corrects an over-broad warning in an earlier draft of this section. `JX7T-9G768` is
catalogued as an F-150 2019–2023 part, and that draft said to avoid F-150 units as CAN FD. Wrong
on this part: CAN FD is a property of the *car's buses and openpilot's decode path*
(`RADAR.STEER_ASSIST_DATA`, `FORD_CADS_64`), not of this radar, which demonstrably speaks classic
`FORD_CADS` on this car. Buying the same number as the working one sidesteps the whole question.

⚠️ **Still avoid `-14D453` and `-14C689`** — those are the blind-spot (SODL/SODR) modules, the
sensors the BLIS work already established cannot answer "is something closing".

### Bill of materials

**Phase 1 — bench test only.** Answers the one question that decides the project.

| Item | Price | Note |
|---|---|---|
| Delphi MRR, salvage | $50–150 | listings seen $33–469; used mostly $50–150 |
| Connector pigtail | $0–30 | buy the harness stub with it; cutting one off a donor is far easier than sourcing the mate |
| CANable 2.0 USB-CAN | ~$36 | 500 kbit/s classic CAN is all that is needed |
| 12 V bench supply, 3 A | $25–40 | a spare battery and a fuse also works |
| **Phase 1 total** | **$110–255** | money spent regardless of which way the answer goes |

**Phase 2 — only if phase 1 shows detections.**

| Item | Price | Note |
|---|---|---|
| Teensy 4.0 | $24 | 4.1 is $30 and unnecessary; three CAN controllers either way, two used |
| Dual CAN-Bus adapter for Teensy | $15 | transceivers *and* termination on board |
| Wiring, fuse, inline connectors, DC-DC | ~$30 | |
| Bracket / radome | $0–40 | 3D print, or fabricate from the donor's own bracket |
| **Phase 2 total** | **$70–110** | |

**All-in: roughly $180–365**, with the go/no-go decision reached after the first $110–255.

### The alternative, and why it is second

eFiniLan's `openpilot-ext-radar-addon` — the sunnypilot author's own external-radar project — is
a ~$236 AliExpress module with its own DBC, `radar_interface.py` and a `card.diff`. It is the only
prior art for adding a radar openpilot never expected, and its architecture is the template.

It is second here for three reasons: it is front-facing and unproven rearward, its protocol is not
one this fork already decodes, and it costs more than a salvage MRR. Its real value to this project
is the patch shape, which applies whichever sensor is fitted. Worth reading before writing §5.

Its warning is also worth repeating, because it independently confirms §3: *"CAN Message Conflict:
This proof of concept may trigger errors due to conflicts. A CAN filter or gateway can resolve
them."* He put his on CAN1 and hit exactly the contention this plan routes around.

### Build the MCU as a sensor hub, not a radar adapter

Asked 2026-08-03: what if more sensors get added later? Worth answering now, because two
decisions made at build time cost nothing today and are expensive to retrofit.

**The Teensy already has three CAN controllers and this uses two.** One private sensor bus, one
digest out to bus 1. The third is free. A second radar — rear corners for true blind spot, say —
either joins the private bus (two MRRs at ~1400 frames/s each is ~80 % of a 500 kbit/s bus: tight
but survivable, and they can be given different scan phasing) or takes the spare controller
outright. **No hardware change, no second MCU.** Buy the Teensy 4.0 with this in mind rather than
something with one controller.

**Put a sensor ID in the digest from day one.** The digest is a message this project defines from
nothing, so it costs a nibble now and a redesign later. Something like:

```
byte 0   bits 0-3  sensor id   (0 = rear centre, 1 = rear left, 2 = rear right, ...)
         bits 4-7  object index within that sensor
byte 1-7 range, range-rate, azimuth, validity
```

Without it, a second sensor means either a second message ID hard-coded per sensor, or a
protocol change that touches the MCU, the DBC and the openpilot decoder together.

**The cereal side is already shaped for this.** `PassingAssist.RearApproach` carries a `Source`
enum (`none` / `blis` / `radar`) precisely so a fitted sensor is distinguishable from an absent
one, and `available` is per side. Adding sensors fills those in; it does not change the decision
chain, which was the point of building the consumer before the producer.

**Bus 1 has room for the digests even though it has none for raw radar.** The measured 60–73 %
is raw MRR traffic. Two 20 Hz digest messages are under 1 %; ten of them are under 5 %. The
digest architecture is what makes expansion cheap — the constraint is the private sensor bus,
not bus 1.

**What is worth adding later, roughly in order of value:**

1. **Rear centre radar** — this project. Answers "is something closing" for lane changes.
2. **BLIS via the canbox** — no new sensor, just routing `Side_Detect_L/R_Stat` onto a bus
   openpilot reads. Complements rather than duplicates: BLIS covers the mirror to ~7 m, the radar
   starts at ~3.7 m, and they overlap.
3. **Rear corner radars** — only if the centre unit's ±45° mid-range beam proves insufficient
   close in. Measure before buying.

### What the front radar is actually doing on this car

Also asked 2026-08-03, and the answer is more than it looks. openpilot does not do longitudinal
control here — stock Ford ACC brakes and accelerates — so it is tempting to conclude the front
radar is idle apart from passing assist. It is not. `radarState` feeds:

- `unconfirmed_lead.py`, whose output **ICBM acts on** by commanding the set speed down. The
  radar is the *negative* signal there: the trigger is a vehicle the camera sees and the radar has
  **not** confirmed.
- `controlsd_ext.py`, which copies `leadOne`/`leadTwo` into `carControlSP`.
- the onroad lead chevrons and the distance/speed/time readout.
- `dec.py` (dynamic experimental control) and `e2e_alerts_helper.py`.
- `passing_assist.py` and `adjacent_lane.py` — this project.

So the accurate statement is narrower: the front radar is not driving openpilot's *own* brake and
throttle, because those are Ford's. It is very much driving what ICBM does to the set speed, and
that does move the car.

### §2 revisited for the MRR — what changes, and what gets easier

§2 above was written for the ESR. Most of it still applies; three things change, and one of them
is a genuine upgrade.

**Alignment is no longer settable over CAN — and that is fine, because we can do it in software.**
Every alignment signal in §2 (`ANGLE_MOUNTING_OFFSET`, `AUTO_ALIGN_DISABLE`, the ±8° range) is an
`ESR.dbc` message *sent to* the radar. The MRR receives nothing, so none of it exists. There is no
stored offset, no auto-alignment routine, no ±8° limit.

That sounds like a loss and is not, because **we decode the azimuth ourselves**. `_update_delphi_mrr`
reads `CAN_DET_AZIMUTH` per detection and turns it into a lateral position. A rear unit's decoder
can subtract a constant. So:

- The ESR's mechanical tolerance was ±8°, hard-limited by the CAN signal range.
- The MRR's is **whatever we choose**, corrected by one constant in our own code, calibrated by
  parking behind a known target and reading back the measured azimuth.

Elevation still has to be mechanically right, exactly as before — nothing in the data path can fix
a sensor pointed at the tarmac.

**Aim matters more than the loose tolerance suggests.** Lane assignment comes from azimuth, and at
range a small angle is a big offset: **3° of yaw error is 2.6 m at 50 m** — more than half a lane.
So "roughly straight back" is not enough; get it close mechanically, then measure the residual and
put it in the code. The calibration is a parked car and a target at a known offset, not a drive.

**Half of the depth problem goes away; the other half does not.** §2 calls this "the tightest fit
in the whole project and it is unverified", assuming a 49.2 mm ESR brick and unknown Fusion rear
clearance. The JX7T is in the car now, on a custom adapter bracket cut at a metal shop to carry a
JX7T on the *Fusion's stock HG9T front mounting points*. Be precise about what that buys.

**Transfers to a rear build:**

1. The radar-side geometry — bolt pattern, cradle, standoffs, the face plane. That is the
   dimension-critical half, and it exists as a proven drawing.
2. Its real measured dimensions, rather than a datasheet lookup for a part family.
3. The shop, the material and thickness, and the knowledge that the design survives a bumper's
   vibration and weather.

**Does not transfer:**

1. The car-side geometry. That bracket bolts to the Fusion's *front* radar bosses. The rear of the
   car has no equivalent — there are no factory radar mounting points back there at all.
2. Rear cavity clearance. Front bumper cavities on an ADAS car are packaged around a radar; rear
   ones are not. Depth behind the rear cover is still unmeasured and still the tightest unknown.

So the second bracket is the proven radar-side half joined to a new car-side half — a redraw, not a
reinvention, and the dimensions that were hard are the ones already solved.

**Candidate rear attachment points**, in order of how much they resemble the front install:

1. **The rear bumper beam / reinforcement bar.** Steel, bolted to the frame rails through the crash
   cans, structural, and at roughly the right height. The closest analogue to what the front
   bracket does, and the first thing to measure.
2. **The threaded tow eye** behind its cover in the rear fascia. Strong and already threaded, but a
   single point — it needs a second locating feature or the sensor can rotate, and rotation is
   exactly the error that costs half a lane at 50 m.
3. **Bumper cover tabs.** Plastic. Not for a sensor whose aim has to hold.

### Where to put it on the back of a Fusion

Ranked by whether it works, then by effort. Everything here needs measuring on the actual car —
these are the constraints to measure *against*, not answers.

| Location | RF path | Effort | Verdict |
|---|---|---|---|
| Behind the rear bumper cover, centred | plastic, good | pull the cover | **first choice** — mirror of the front install |
| On a bracket below the bumper, off the beam or tow points | free air, best | no disassembly | fallback — exposed to spray and stone chips |
| Behind the licence plate | **blocked** | — | no: the plate and frame are metal |
| Inside the rear glass | **blocked** | — | no: defroster grid, tint, raked angle, cabin multipath |

Constraints for whichever is chosen:

- **Height 30–86 cm** to the sensor face (Delphi manual figure, quoted for the ESR — treat as
  indicative for the MRR and sanity-check against where Ford mounts the front one). A Fusion's
  rear bumper centre sits comfortably inside that.
- **Centred laterally**, or the lane maths inherits an offset. Off-centre is correctable in code
  like azimuth, but it is one more constant to calibrate — prefer centred.
- **Level, pointing straight back.** See the 3°-is-half-a-lane note above.
- **Nothing metal in front of the antenna face.** A metal bracket *behind* it is fine.
- **Away from the tailpipe.** Heat and vibration, and exhaust plume is not something to ask a
  radar to see through.
- **Connector facing down or sealed.** The module is rated for bumper life; the bracket and the
  connector are what fail.
- **Paint still matters.** Metallic and pearl paints contain aluminium flake and attenuate 76 GHz.
  If mounting behind the cover, test with a cut-off piece of the actual cover between radar and
  target during the bench test — that is nearly free to add and settles it before anything is cut.

---

## 9. Wiring

Added 2026-08-03. Confidence: **high** on topology and bus numbering (verified in-tree),
**medium** on the connector, which is not publicly documented.

### The standalone question is largely answered already

opendbc PR #351, which added `FORD_CADS` in the first place, says:

> "I have tested this DBC extensively with a ford radar part number H1BT-9G768-AG off a fiesta as
> well as someone else testing it with a focus radar (**JX7T-9G768-AC**)"
>
> "this radar also presents a very affordable alternative to the tesla or Toyota radars for **OP
> long in custom setups** as you can buy it for only $100"

Two things fall out. The DBC was validated against **this exact part number**, not merely the
family. And the radar is *already used as a bolt-on in custom builds* — which is a third party
independently confirming what §8 inferred from the single-node DBC: power it and it talks, with no
gateway frames and no host vehicle. The bench test drops from "does this project work at all" to a
confirmation step.

### Topology — controller at the front, which is the right instinct

```
   REAR                                                    FRONT
   ┌──────────────┐                                        ┌──────────────┐
   │ rear JX7T    │  12 V  ─────────── fused feed ───────► │ front JX7T   │
   │              │  GND   ─────────── chassis             │ (untouched)  │
   │              │  CANH  ──┐                             └──────┬───────┘
   │              │  CANL  ──┤ twisted pair, full length          │ bus 1
   └──────────────┘          │ of the car                         │
                             ▼                                    │
                    ┌────────────────────┐                        │
                    │ Teensy 4.0         │                        │
                    │  CAN1  private ────┘   TERMINATE            │
                    │  CAN2  digest ──────────────────────────────┘  DO NOT TERMINATE
                    └────────────────────┘
```

Putting the controller at the front is correct, for reasons beyond convenience:

- **Power is there.** The front radar already has a switched, fused feed to borrow a reference
  from (take a separate fused feed rather than sharing its circuit — see below).
- **It is dry and reachable.** Reflashing a microcontroller buried in a wet rear bumper is a bad
  afternoon.
- **Bus 1 is physically there.** `fordcan.py` fixes the numbering — `main` = 0, `radar` = **1**,
  `camera` = 2 — so the front radar's own CAN pins *are* bus 1. No hunting.

Wire count is the same either way: four conductors run the length of the car regardless of which
end the controller sits at. So take the end with power, shelter and access.

**The controller must never bridge raw rear-radar frames onto bus 1.** It emits only the digest.
That is the entire reason it exists — §3 measured bus 1 at 60–73 % and a second MRR would add
37–45 %.

### The gotcha most likely to break the working front radar

**The $15 Tindie dual-CAN board has termination resistors on both channels.** That is right for
one channel and wrong for the other:

- **CAN1, private rear-radar bus:** wants termination. Measure the radar's own CANH–CANL first —
  Delphi modules often carry an internal 120 Ω. If it does, the board's resistor is the second and
  the bus is correct at ~60 Ω. If it does not, you need one more at the far end.
- **CAN2, the bus 1 tap:** must **not** be terminated. Bus 1 already has its two terminators. A
  third takes it to ~40 Ω, which can stop the bus working — and that bus is the one your *working*
  front radar and ACC depend on. Remove or disable that resistor before connecting anything.

Verify before and after: **~60 Ω across CANH–CANL on a healthy terminated bus, key off.** If it
reads ~40 Ω after the tap, the resistor is still in.

Keep the bus 1 stub short. 500 kbit/s is forgiving but a metre of unterminated spur is not free.

### The connector

Not published anywhere I could find — Ford service documentation only. But it does not need to be,
because **there is a wired, working example of it in the car**: replicate the front install's
assignment onto the new connector.

For an independently bought pigtail, verify rather than assume:

1. **Radar unplugged and unpowered**, measure resistance between each pin pair. A pair reading
   ~120 Ω is CANH/CANL — the module's internal termination identifies them for you.
2. The remaining two are supply and ground. **Identify ground by continuity to the module case or
   to chassis on the vehicle side.** Do not infer polarity from wire colour.
3. Fuse the rear radar's feed **separately** rather than splicing into the front radar's circuit.
   Comparable retrofits draw well over 0.2 A; assume ~0.5 A and size for two radars only if you
   deliberately choose to share, which there is no reason to do.
4. Reverse polarity kills these modules. Meter it twice.

### Rear recovery point — it exists, but check where it is

The 2020 Fusion **does** have a rear towing eye: a threaded socket behind a pop-out panel in the
bumper cover, with a screw-in eye stored with the spare. So §8's second mounting candidate is real
on this car.

Two caveats before designing around it. It is usually **off-centre**, and a centred sensor is worth
more than a convenient one — an offset is correctable in code but is one more constant to
calibrate. And using it as a mount means either giving up the recovery function or designing a
bracket that passes it through. Measure where it actually sits before choosing it over the bumper
reinforcement beam.

### Will the car mind the new messages? The messages are not the risk

Asked 2026-08-03. The honest answer reframes the worry rather than soothing it.

**The message content is close to harmless, provided the ID is genuinely unused.** CAN receivers
filter by ID, usually in hardware. A frame with an ID a module was never told about is dropped
before any software sees it. That is not a Ford quirk, it is how the bus is designed to work, and
it is why an aftermarket node can sit on a vehicle bus at all.

Three conditions make that true, and each is checkable rather than hopeful:

1. **The ID must actually be unused on THIS car's bus 1.** Verify against the owner's own log, not
   a generic Ford DBC. If the ID collides, we are no longer sending something ignorable -- we are
   impersonating a module that something does listen to.
2. **Pick a HIGH id number.** On CAN the ID is also the priority: lower wins arbitration. A
   low-numbered digest would win against the radar's own frames and delay them. High-numbered
   traffic yields instead.
3. **Bus load stays trivial.** Two frames at 20 Hz is ~40 frames/s against a bus already carrying
   thousands. Under 1 %.

**The wiring is the part that can actually hurt.** A node that is electrically wrong -- the wrong
bit rate, a third terminator, a swapped or shorted pair -- does not send ignorable frames. It sends
**error frames**, and enough of those drive the bus into error-passive or bus-off. That can set
DTCs, and on a bad day it takes down the bus that the *working* front radar and stock ACC depend
on. Nothing about the message payload matters at that point.

So the risk is not "will Ford's modules be confused by a message they do not know". It is "is the
new node electrically correct". Which is good news, because that is testable.

### Stage it: listen-only on bus 1 before transmitting anything

The strictly safer order, and it costs one firmware flag:

1. **Bench.** Teensy plus rear radar on the private bus, nothing connected to the car. Confirm the
   radar talks and the digest is built correctly. No vehicle involvement at all.
2. **Listen-only on bus 1.** Connect the bus 1 channel with the CAN controller in listen-only mode.
   It is then *electrically incapable* of transmitting -- it cannot send a frame, and it cannot
   even send the error flags that a mis-set bit rate would otherwise produce. Drive it. This proves
   the tap, the bit rate and the termination while the node is physically unable to disturb
   anything.
3. **Enable transmit.** Only after step 2 is clean for a drive. Flip the flag, log bus 1, and
   confirm the digest appears and nothing else changed.

Step 2 is the one worth insisting on. It converts "I hope the wiring is right" into a measurement,
and the failure mode it protects against -- taking out bus 1 while the car is moving -- is the
worst one available in this project.

Add a physical disconnect and its own fuse while building, so step 3 is reversible in ten seconds
at the roadside.
