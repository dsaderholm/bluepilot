# TSR on the retrofit Fusion — full investigation log

Opened **2026-08-11**. **Rewritten at the top on 2026-08-21**, because four load-bearing claims in
this header had become false and a cold session reads them first.

## READ THIS BEFORE ANYTHING ELSE

**THE CAMERA READS SIGNS. It did it once, and it is verified.** Route `000003a7`, segment 6, seven
consecutive frames: `TsrVLim1MsgTxt` went `255 NoLimit` -> `30 Message30`, and the camera set
`TsrVl1PrmntMsgTxt` to `ShowPermanentlyWithoutSupp` in the same frames. Position decoded from the
car's own `0x462`: **40.725463, -111.829903**, which is 2011 2100 S in Salt Lake City, and Street
View shows a **SPEED LIMIT 30** on a pole at exactly that spot. Full detail in **section 4j**.

**So the question is no longer "is the camera broken". It is "why is it bad at this".** Across every
route pulled to date -- **87 segments** -- there is exactly **one** detection event, and it needed
night, a retroreflective sign under headlights, a head-on approach and a crawl to 7 mph to happen.

**A deliberate repeat of that exact sign, at the same speed, in daylight, read nothing** (4n).

**Four claims that led this file and are now WRONG. Do not carry them forward:**

| was written here | actually |
|---|---|
| "the car is currently REVERTED and healthy" | **two writes are in place and persisted** across an ignition cycle -- `706-01-01` = `0810 A9DB B964`, `706-02-01` = `FD56 16DB 7FD3` |
| "the camera is NOT reading signs at all" | it read one, verified against Street View (4j) |
| "IPMA write refused by FORScan" | **FORScan writes `706-01-01` fine.** It refused ONE VALUE, `0450`, which was the only value ever tried (4e) |
| "the blocker is the tool, not the car" | the tool was never the blocker (4e) |

**And four theories killed on 2026-08-21, each by measurement:**

- **Hardware.** `KT4T-19H406-CE` is a **Cx** camera -- per Ford's own part scheme, Cx adds autonomous
  braking on top of Bx's AHB, TSR and tiredness alert. It read a sign. Every "replace the camera /
  replace the cluster" theory is dead (4j, 4l).
- **Fusion mode.** Not required. The sign was read in `Available_CameraOnly` with
  `NoNavDataAvailable` asserted on all 747 frames including those seven (4j).
- **Android Auto.** Not the cause. He drove with the phone off USB and `0x463`/`0x464` are still at
  zero frames (4j).
- **Firmware.** EU and US IPMA firmware are byte-identical -- same SBL, strategy and calibration,
  confirmed by an owner who obtained both (4l).

**One real defect remains measured and unexplained:** the APIM sends `0x462` (position) 3494 times a
drive and `0x463`/`0x464` **zero** times, which is the `U0253` "Missing Message" the camera raises.
Its Nav Repeater settings are already correct (4h). This is real, it is not what stops sign reads,
and it should not be conflated with TSR again.

**THE NEXT WRITE FROM 4k HAS BEEN DONE, AND IT DID NOT WORK.** See 4n: it engaged the fused mode,
moved `TsrVl1StatMsgTxt` from `LimitReliable` to `LimitOutdated`, and produced zero detections in 37
segments. **Restore `706-01-01` -> `0810 A9DB B964`.**

**THE ONE EXPERIMENT LEFT THAT COSTS NOTHING**: restore the nibble, then drive the 4j loop AT NIGHT
again. Night and the nibble both changed between the drive that read a sign and the drives that did
not, so neither is isolated. If the 30 comes back, night was the factor.

**DO NOT chase a US-market as-built** -- retracted in 4l, and the reason matters.

---

## HOW TO READ THE REST OF THIS FILE

Sections 4a through 4l were appended in the order they were discovered, not in numeric order, and
several supersede earlier ones. When two sections disagree, **the higher letter wins** and the
earlier one says so. In particular:

- **4f** argues Fusion mode is the answer. **4j disproves it.**
- **4g** recommends a US-market as-built and calls `FF` an unset region. **4k corrects the region,
  4l retracts the recommendation.**
- **4h** names Android Auto as the leading hypothesis. **4j disproves it.**

**Three confident framings about markets were wrong in one day** -- "the friend's car is a different
market so his nibbles are dangerous", "`FF` is an unset region", "get a US as-built". Every one was a
plausible story reasoned from a search result with no control to check it against. What survived is
what was measured on this car.

---

## 1. Where it stands

| | state | as of |
|---|---|---|
| **IPMA `706-01-01`** | `0810 A9DA B963` -- nibble 2 `4`->`8` then nibble 8 `B`->`A`. **RESTORE TO `0810 A9DB B964`** -- the second write only regressed the status (4n) | 2026-08-22 |
| **IPMA `706-02-01`** | `FD56 16DB 7FD3` -- nibble 4 = `6`, TSR data source Camera + APIM, **persisted** | 2026-08-21 |
| **IPMA region `706-04-01`** | `FFFC 27C3 847B`, **never written**. `FF` is NORMAL here -- a working car reads `FFFC` too | 4k |
| **APIM** | TSR enabled at `7D0-09-02`. Nav Repeater format/conformance already correct. Sends 1 of 3 GPS messages | 4h |
| **IPC** | untouched, and it **physically lacks** `720-10-01`/`720-10-02`. Governs the DASH only -- he does not want TSR on the cluster | 4f |
| **camera output** | **one** detection in 87 segments. Reaches `Available_FusionMode` and still reads nothing | 4j, 4n |
| **openpilot** | already parses `Traffic_RecognitnData` and feeds `SpeedLimitSource.car`. The consumer side is DONE -- the moment the camera emits a limit, openpilot uses it | |

**The whole configuration originally came from a Brazilian car off the internet, and there is no
original** (4g). That is still the most load-bearing fact in this file, but 4l changes what to do
about it.

---

## 2. The hardware, and why it matters

```
HIS IPMA                                 HIS FRIEND'S IPMA (TSR WORKS)
Part number:   KT4T-19H406-CE            Part number:   LV4T-19H406-CF
Calib level:   KT4T-19H406-CE            Calib level:   LV4T-19H406-CF
Strategy:      KT4T-14F397-AE            Strategy:      KT4T-14F397-AE   <- SAME
Calibration:   KT4T-14F398-AE            Calibration:   KT4T-14F398-AE   <- SAME
```

**Identical strategy and calibration, on a car where TSR works.** So the firmware is NOT the gate.
The difference is the module assembly (`LV4T` vs `KT4T`) or the as-built.

**DO NOT run the FORScan IPMA firmware update to `CF`.** It moves Strategy `AE` → `AF`, which is
*away* from the known-working software, and `KT4T-14F397-AF` is the **`FORD_EDGE_MK2`** fingerprint
in this repo while `-AE` is **`FORD_FUSION_MK5`**. It would also change steerRatio expectations
(17.07 vs 15.3) if the platform ever resolved by fingerprint. He caught this; it was nearly done.

---

## 3. The field map (from a Ford as-built reference he supplied)

FORScan's friendly names are decoded through a **Fusion** profile against an **Edge** module and are
not trustworthy. These positions come from the real reference.

### IPMA

```
706-01-01   xx*x xxxx xx--     ModuleFeatureCfg_TSR / ModuleFeatureCfg_IACC
    0 = TSR Off    ! IACC Disabled       3 = TSR SLOIF ! IACC Enabled
    1 = TSR Off    ! IACC Enabled  <-- HIS VALUE
    2 = TSR SLOIF  ! IACC Disabled       4 = TSR SLIF  ! IACC Disabled
                                         5 = TSR SLIF  ! IACC Enabled  <-- TARGET

706-02-01   **xx xxxx xx--     FeatureCfg_TSRMode (+ LKSMyKey / LKSStrategy / DPAC)
    0x = TSRMode Undefined
    4x = TSRMode CameraOnlyOn
    his value: FD   ->  4D would be CameraOnlyOn, preserving the low nibble
    NOTE: F is not in the reference's documented range. Never attempted.

706-05-01   xxx* xxxx xx--     FeatureCfg_DAS_GSR
    NOT "wheel arch height". FORScan's Fusion profile renders this block as wheel arch
    heights of 1338 mm and 1856 mm, which are not real and sent the investigation down a
    two-day detour.
```

### IPC

```
720-03-01   x*xx xxxx xx--     TempCurve ! TSR_IOD ! Upshift ! ISA
    his 2nd char = 0  ->  TSR IOD DISABLED.  4 = TSRIOD enabled, UI off, ISA off

720-03-02   xx*x xx--          TSR ! TrailerSway ! TBS ! TripStatsPause
    his 3rd char = 8  ->  TSR ALREADY ENABLED. Nothing to do here.

720-09-01   xxxx xxxx *x--     SLIF ! HUD_Cfg ! FCW_AmberRTT
    his 1st char of 3rd group = 0  ->  SLIF DISABLED  <-- likely the real blocker
    8 = SLIF enabled, no HUD, FCW_AmberRTT off
```

**The SLIF finding is the best explanation for `U2101 - Signal Plausibility Failure`:** the camera
was being asked to run `MFCTSR = SLIF` while the cluster declares SLIF unavailable. That IS an
incompatible configuration.

### APIM — the write that landed but changed nothing

`7D0-09-02` TSR enable. The write succeeded, which is notable only because FORScan refused every
other module. **`U0253` still recurs**, so it did not restore IPMA-to-APIM communication.

---

## 4. RESTORE POINT — as-built as of before any changes

### IPMA (`706`)

```
706-01-01  0410 A9DB B960      706-03-01  C000 5200 80A3
706-01-02  301A 6535 6458      706-03-02  0000 0080 0092
706-01-03  4000 0000 0051      706-03-03  0000 8000 0093
706-01-04  0000 0000 0012      706-03-04  0080 0000 0094
706-01-05  0000 0013           706-03-05  8000 0000 8015
                               706-03-06  0000 0080 0096
706-02-01  FD56 16DB 7FD3      706-03-07  0000 8000 0097
706-02-02  FFC1 55AA E1B1      706-03-08  0080 0000 0098
706-02-03  0842 1000 006C      706-03-09  0000 0000 0019
706-02-04  0000 0000 0013      706-03-10  0000 0000 0020
706-02-05  0000 0000 0014
706-02-06  0000 0008 001D      706-04-01  FFFC 26C3 847A
706-02-07  0000 0004 001A      706-04-02  0000 0000 0013
706-02-08  0000 0000 0017
706-02-09  0000 0000 0018      706-05-01  53AA 7400 0084
706-02-10  0000 0000 001F      706-05-02  0000 0014
```

### IPC (`720`)

```
720-01-01  C726 1264 72FE      720-05-01  D8B8 0000 00BD
720-01-02  0E1F 90E7           720-05-02  0000 002E
720-02-01  79E4 7C34 1E55      720-06-01  0000 0667 009B
720-02-02  3A80 FCE1           720-06-02  0000 002F
720-03-01  E002 AA00 00B7      720-07-01  0001 0000 0030
720-03-02  8884 2860           720-07-02  0100 0839
720-04-01  0400 0100 0132      720-08-01  0000 0000 0030
720-04-02  5553 0ADF           720-08-02  0000 0000 0031
                               720-09-01  2000 0000 0051
                               720-09-02  0000 0133
```

Checksum behavior observed on `706-01-01`: **additive on the low byte.** `0410`→`B960`,
`0415`→`B965`. FORScan does **not** recalculate it when you edit the data, so it must be typed.
(Correcting it did not make the write succeed, so it was not the cause of the refusals.)

---

## 4a. A SECOND RESTORE POINT NOBODY KNEW EXISTED — PRE-CAMERA, ALL 29 MODULES

**Found 2026-08-21 on his own machine**, not on the car: `C:%BS%UCDS_V3%BS%Session%BS%3FA6P0D94LR115239.xml`,
written **11 March 2026**. It is a complete UCDS as-built export — **515 blocks across 29 modules**.
It is a genuine read of this car, not a template: two demo sessions sit beside it
(`WF0EXXWPCELA25401`, `WF0EXXWPCELR15787` — Euro Mondeo VINs) and they share values with each other
that his file does not share with either.

**IT PREDATES THE CAMERA SWAP. Do not treat its `706` as current.**

```
node 706 in that file      F111 HS7T-14G025-CC     F188 HS7T-14G019-CC     <- STOCK FUSION camera
his IPMA per section 2     part KT4T-19H406-CE     strategy KT4T-14F397-AE <- the Edge camera, now
```

This resolves a discrepancy that otherwise looks alarming. That file reads
**`706-01-01 = 1FA8 2A40 4080`**, nothing like FORScan's **`0410 A9DB B960`** recorded in section 4.
**It is not a tool disagreement and not corruption — it is a different physical camera.** Anyone
comparing the two without reading the part numbers will conclude UCDS and FORScan disagree about
as-built. They do not.

`730` (PSCM) and `764` (CCM radar) already read non-Fusion in that file, so the steering and radar
retrofits were in before 11 March 2026 and **the camera went in after it**. That makes the file
specifically a *pre-camera, post-steering* snapshot.

Two uses:

1. **A restore reference far wider than section 4**, which covers only IPMA and IPC. This has all 29
   modules — including everything a GWM or CCC write could disturb.
2. **Proof that reading is not EXT-gated.** This read succeeded on 11 March 2026, five weeks after
   the licence expired on 4 February 2026.

Full module inventory is in the file itself; extract with the `<NODEID>` + `F111`/`F188`/`F113`
pattern. Note the XML nests DIDs *inside* the NODEID text node (`<NODEID>706<F10A>...`), so a
`<NODEID>(.*?)</NODEID>` regex matches nothing — that cost a pass on 2026-08-21.

---

## 4e. FORSCAN WRITES `706-01-01`. THE "FORSCAN REFUSES" PREMISE WAS WRONG.

**2026-08-21, live at the car.** `706-01-01` was written to **`0810 A9DB B964`** — nibble 2 only
(`4`->`8`), his own nibbles 8-9 kept, checksum computed. **FORScan accepted it. No new DTCs.**

This overturns something asserted throughout this document since day one.

```
OLD CLAIM   "FORScan cannot write these. Its profile is a 2020 Fusion; the IPMA
             changes are feature enables a Fusion never had."
REALITY     FORScan refused ONE VALUE -- 0450 -- and 0450 was the only value ever
             tried on this block. The refusal was value validation, not profile
             validation. The block writes fine.
```

**Consequence: the €130 UCDS EXT licence may be unnecessary.** A writing tool already exists and is
already in his hands. Do not buy the licence to get past mode 1; mode 1 is not where this stops.

The `U0253` seen straight after carries `EVENT_TIME` = Fri Aug 21 19:52:03 2026 — the write itself
resetting the module — and reads **"Previously Set DTC - Not Present at Time of Request"** with the
MIL off. It is the write, not a fault. Module voltage 13.5 V, ECU internal 109 °F, 125,456 miles.

### The checksum, solved

Every candidate value in this document can now be generated rather than copied from another car:

```
checksum = (0x07 + 0x06 + section + block + sum(data_bytes)) & 0xFF
```

Section and block are the **literal hex of the printed label** — `706-02-10` contributes `0x02` and
`0x10`, not decimal 2 and 10. Verified against all 58 blocks of both complete IPMA dumps with zero
failures. Tooling and both dumps live in `bluepilot/asbuilt/`; run `asbuilt.py` to re-verify and diff.

### `706-02-01` nibble 4 is the TSR data source — a 13th difference 4d never had

His live read on 2026-08-21 is **`FD52 16DB 7FCF`**. Section 4d recorded **`FD56 16DB 7FD3`** on
2026-08-12. Computing the checksum for nibble 4 = `6` reproduces 4d's value exactly, so **4d was not
a transcription error — the car changed.**

That is section 4b happening: he set *TSR data source -> Camera + APIM*, `NoNavDataAvailable` cleared,
and it reverted at the next boot. 4d caught the car inside that window.

| | nibble 4 | meaning |
|---|---|---|
| friend, TSR works | `6` | Camera + APIM — **and it holds** |
| his, 2026-08-12 | `6` | just written, section 4b |
| his, 2026-08-21 | `2` | Camera Only — reverted |

Target value, checksum valid: **`706-02-01` -> `FD56 16DB 7FD3`**.

### BOTH WRITES PERSISTED. THE TWO FIELDS ARE INTERLOCKED.

**Confirmed 2026-08-21, re-read after ignition off and on:**

```
706-01-01   0810 A9DB B964     held   (nibble 2, 4 -> 8)
706-02-01   FD56 16DB 7FD3     held   (nibble 4, 2 -> 6 = TSR data source Camera + APIM)
```

**The second one is the result.** That exact write reverted at every previous attempt — it is
section 4b, the only thing that had ever visibly worked and the only thing that had ever undone
itself at boot. It holds now, and the single thing that changed is that `706-01-01` nibble 2 is `8`.

**So 4b was never a mystery about persistence. The module was rejecting a data-source setting
inconsistent with its own feature configuration.** Set the feature config first and the data source
stays. That is what has been undoing changes on this car since day one.

Pre-change restore values, both checksum-valid:

```
706-01-01 -> 0410 A9DB B960
706-02-01 -> FD52 16DB 7FCF
```

### CAUTION: THE FRIEND'S CAR IS NOT A US CAR

**Learned 2026-08-21, and it changes how section 4d may be used.** Section 4d treats his car as a
matched control — same strategy, same calibration, TSR works, therefore the difference is as-built.
The firmware match is real. **The market is not.** He is in a different country.

So an unknown subset of the 12 differing blocks encodes **country configuration, not TSR**, and the
hex alone cannot say which. **Do not chase the remaining nibbles toward his values.** The prime
suspects are exactly the ones that were on the target list:

- `706-01-01` nibbles **8, 9**
- `706-02-01` nibble **9**
- `706-04-01` and `706-05-01`, which differ heavily

He has already had a region change throw DTCs, and that topic is closed. This is the same hazard
arriving as a nibble copied off a car built for another market.

**What this does NOT weaken:** the two writes above persisted on HIS car, threw no new DTCs, and the
interlock was demonstrated on his hardware rather than inferred from the diff. That stands.

**Open and worth knowing:** which country. EU sign recognition is circular km/h plates, US is
rectangular mph. If the same firmware carries a market config, that bears directly on whether
as-built alone can produce US sign reads.

### 0x3CD MEASURED ACROSS A WHOLE PRE-CHANGE DRIVE — AND THE REGION THEORY WEAKENS

**2026-08-21, route `000003a1--1c5cc52d49`, 16 segments, started 17:00, i.e. BEFORE the writes.**
Scanner is `bluepilot/asbuilt/tsr_scan.py`; run it on device against a route glob.

909 frames of `Traffic_RecognitnData` on bus 2. **Exactly two payloads, and `TsrVLim1MsgTxt` was
255 on every single frame:**

```
372320fffcc80220  x878   Available_CameraOnly   NoNavDataAvailable   Mph
172220fffcc80220  x31    Available_FusionMode   NoInformationAllOK   Mph
```

**The second payload is the important one.** For 31 frames the camera sat in `Available_FusionMode`
reporting `NoInformationAllOK` — its healthy state, nav data flowing, nothing wrong — **and read no
sign anyway.** So `NoNavDataAvailable` is NOT what stops sign reads. That theory is dead.

**The camera does not believe it is in an unsupported market.** `TsrMsgTxt_D_Rq` carries dedicated
codes for exactly that and never emits them:

```
TsrMsgTxt_D_Rq     5 = CountryNotSupported     6 = RegionNotSupported     <- NEVER SEEN
                   3 = NoNavDataAvailable      1 = NoInformationAllOK     <- what it actually says
TsrStatMsgTxt_D_Rq 1 = TSR_Off   5 = TSR_Error                           <- NEVER SEEN
                   3 = Available_CameraOnly    2 = Available_FusionMode   <- what it actually says
TsrVlUnitMsgTxt    2 = Mph                                               <- correct for the US
```

TSR reports itself **available**, in **mph**, with **no region or country complaint**, and reads
nothing. Any future theory has to explain that combination.

**Today's writes have not changed `0x3CD`.** The live parked payload is `372320fffcc80220` — byte for
byte the dominant pre-change payload. That is not yet a negative result: the car was stationary at
`vEgo` 0.00 for the whole capture, and the `FusionMode` frames in the pre-change route only appeared
while moving. **The comparison is only valid after a drive on the new configuration.**

### THE POST-WRITE DRIVE: ZERO SIGN READS, AND THE CONFIG MATCHED THE WORKING CAR

**2026-08-21, route `000003a2`, 27 segments + `000003a3`, decoded LOCALLY** (375 MB pulled to the
laptop — see the warning at the end of this section).

```
1765 frames of 0x3CD    peak 71.9 mph    772 frames above 10 mph
372320fffcc80220  x1765  VLim1=255  Available_CameraOnly  NoNavDataAvailable  Mph
```

**One distinct payload for the entire drive.** `TsrVLim1MsgTxt` never left 255 on a real highway run
that certainly passed posted limits.

**And the camera never reached `FusionMode` again.** The pre-write route had two payloads including
31 frames of `Available_FusionMode` / `NoInformationAllOK`. After setting the data source to
Camera + APIM — the change meant to *enable* fusion — the camera sat in `NoNavDataAvailable` for
1765 straight frames and never fused once.

**THE CONFIG MATCHED THE WORKING CAR ON BOTH IDENTIFIED NIBBLES AND STILL READ NOTHING:**

```
706-01-01 nibble 2    his 8   friend 8    MATCHED
706-02-01 nibble 4    his 6   friend 6    MATCHED
result                zero sign reads
```

That is the strongest evidence so far that **configuration is not the gate.** Still unmatched on
those two blocks: `706-01-01` nibbles 8 and 9, and `706-02-01` nibble 9 — all flagged in the
market-config caution above, because the control car is not a US car.

### TWO MISTAKES IN THIS SESSION, RECORDED SO THEY ARE NOT REPEATED

**1. Two variables were changed before the drive.** `706-01-01` nibble 2 and `706-02-01` nibble 4
both moved, so the 1765 frames cannot attribute anything to either one. Change one thing, drive,
then change the next.

**2. Camera + APIM was pursued as if it were the goal. It is not.** Line 30 of this document already
said so: *sign reading does not need nav; Camera Only means the camera reads a sign with its own
optics, and nav is a second source, not a prerequisite.* Camera + APIM matters for a **different**
feature — routing SYNC's map limits to openpilot (section 6d). It was chased here because it silenced
`NoNavDataAvailable`, which is a symptom, not the objective.

**But note the tension, because it is not resolved:** the friend's working car sits at nibble 4 = `6`
(Camera + APIM). So "Camera Only is what we want" is the theory, and "the working car runs Camera +
APIM" is the observation. Both are in the record; neither has been shown to produce a sign read.

**3. Do not ask the friend for his TSR data source. It is already in his as-built.** `706-02-01`
nibble 4 = `6`. The complete dumps in `bluepilot/asbuilt/` answer most questions of this shape —
read them before asking for anything.

### THE MATRIX, AND THE ONE CELL NEVER RUN

```
706-01-01 nibble 2 | 706-02-01 nibble 4 | signs?
       4 (old)     |   2  Camera Only   | tested for months -- no
       4 (old)     |   6  Camera + APIM | never persisted before 2026-08-21
       8 (new)     |   6  Camera + APIM | READS SIGNS -- see below. The 'no' was premature.
       8 (new)     |   2  Camera Only   | NEVER RUN
```

**THE `8 / CAMERA + APIM` ROW SAID "TESTED 2026-08-21 -- no" AND THAT WAS WRONG. Corrected
2026-08-23 by scanning 20 routes instead of the two that were to hand.**

    00000398 .. 000003a5   13 drives, ~840k frames    ZERO reads
    2026-08-21             `0810 A9DB B964` written (nibble 2: 4 -> 8), accepted
    000003a7   2026-08-22 04:14                       READ 30
    000003ac   2026-08-22 23:32                       READ 30
    000003ad   2026-08-23 13:21                       READ 30

The verdict was reached from routes `a1` and `a2`, recorded HOURS after the write. The first read
came the next morning. **A negative from the drives that happen to be on the device is not a
negative** -- reads are rare enough here (3 in 20 drives) that a single quiet drive proves nothing,
and two quiet drives were used to close a row of this matrix.

Honest weight: 13 quiet drives at the measured rate is ~15% likely by chance alone, so the count
does not carry it. What does is the coincidence with the write.

**AND THE REMAINING FAILURE IS NOT THE SENSOR, THE AIM, OR THE VISION PIPELINE.** From the owner,
2026-08-23, and it closes a line of enquiry that was about to be opened:

  *"The IPMA has been calibrated and auto high beams work great. Without calibration, LCA wouldn't
  work, either."*

Auto high beams require the camera to pick out oncoming headlights and tail lights; LCA requires
correct calibrated geometry. Both work. So the camera is aimed, calibrated, and its vision stack is
healthy, and `dataAvailable` is True on 391,355 of 391,355 frames measured -- it is up and
publishing, and merely says "no limit" 95% of the time.

**That NARROWS the fault rather than merely removing a candidate.** A camera whose optics, aim and
perception all work, and which has resolved a US 30 sign three times, is not failing to SEE signs.
It is failing to do something with a sign it has already seen -- which is the layer a REGION or
sign-set configuration governs, and this car's region is `UNSPECIFIED`. That remains refused (it
produced "hella DTCs" and is not to be proposed again), but it is unresolved rather than disproven,
and it is now the best-fitting explanation on the list.

**Also ruled out the same day, by field survey across 391,355 frames of 4 routes:**

    dataAvailable   True on every frame        not a camera that thinks it is off
    vLimitUnit      2 (mph) on every frame     not a units mismatch
    vLimit1         255 x373,651  30 x17,595
    vLimit2         constant 252, always       no reads are landing in the second slot

So nothing is being under-counted by reading only `vLimit1`, and the decode is not the problem.

### THE €130 IS DEAD

Do not buy the UCDS EXT licence for this. FORScan writes `706-01-01` (section 4e), the writes
persist, and the resulting configuration matched the working car on every nibble identified so far
and produced nothing. A writing tool was never the blocker and neither, on this evidence, is
configuration.

### THE LEADING REMAINING HYPOTHESIS IS THE HARDWARE

```
HIS      IPMA assembly  KT4T-19H406-CE     strategy KT4T-14F397-AE
FRIEND   IPMA assembly  LV4T-19H406-CF     strategy KT4T-14F397-AE   <- SAME software
```

Same firmware, different camera assembly. This fits every observation: the software reports TSR
`Available` in `Mph` with no `CountryNotSupported` and no `RegionNotSupported` because the *software*
supports TSR, and no sign is ever recognised because the *assembly* cannot do it. It would also
explain five months of as-built work moving nothing. **Unproven** — but it is now the hypothesis that
explains the most, and it points at a part, not a parameter.

### NEVER RUN THE SCAN ON THE DEVICE

`tsr_scan.py` was run on the comma while `IsOnroad=1` and he was driving. It decompressed and
capnp-parsed ~200 MB on the device's own CPU, with load already at 7.23 and 87 °F ambient. He
reported lag, then could not engage. It cleared when the processes were killed.

**Use `bluepilot/asbuilt/tsr_local.py` instead** — `scp` the `rlog.zst` files off and decode on the
laptop. The car must never pay CPU for analysis.

### THE REMAINING QUESTION IS A DRIVE

**What is NOT yet proven.** A value persisting is not TSR working. The measurement is
`TsrVLim1MsgTxt` in `Traffic_RecognitnData` (`0x3CD`): **255 is the no-data sentinel** and it has been
255 on every frame ever captured on this car. Anything else means the camera is genuinely reading a
sign. That needs a drive past a posted limit; it cannot be settled in the driveway.

Two separate things are gated separately, and only the first matters for openpilot:

| what | readout | state |
|---|---|---|
| camera reads signs | `TsrVLim1MsgTxt` on `0x3CD` | needs a drive with the comma recording |
| cluster draws a sign | IPC SLIF at `720-09-01` | **still unwritten** — and the IPC is LKA-only for everything else, so the dash is not a trustworthy TSR indicator either way |

Restore value, exact: **`706-01-01` -> `0410 A9DB B960`**.

Bisect candidates, all checksum-valid, from `bluepilot/asbuilt/asbuilt.py`:

```
0810 A9DB B964   nibble 2 only            <- WRITTEN 2026-08-21, accepted
0410 A9DA A94F   nibbles 8-9 only
0810 A9DA A953   full copy of the friend's block
0400 A9DB B950   diagnostic: IACC not TSR
```

---

## 4f. FUSION MODE IS THE ANSWER, AND `U0253` IS WHAT BLOCKS IT

> **SUPERSEDED BY 4j. Fusion mode is NOT required.** The camera read a sign in
> `Available_CameraOnly` with `NoNavDataAvailable` asserted on all 747 frames, including the seven
> that carried the limit. The `U0253` measurement below is real and stands; the conclusion drawn
> from it does not. Kept because the missing-message evidence is the best in this file and because
> the reasoning error is worth seeing.

**2026-08-21, late. This supersedes the Camera Only advice given earlier the same day, which was
wrong.**

### Two independent reports: Fusion mode is what produces sign reads

From the FORScan forum, `Focus MK4 - Activation Traffic Sign Recognition (TSR) Impossible` (t=13385):

> **`adiandy`**: "I also changed to **FUSION rather than Camera only**, and now I get the Speed sign
> as soon as i move the car."

> **`sanglt`**, vehicle listed as **Ford Mondeo MK5** -- *this platform, CD391*: "I try to active TSR
> on Mondeo MK5 too. So far I can get these thing works: **IPMA: TSR in Fusion mode**; IPC: TSR menu
> + icon show"

**A CD391 car has TSR working and it runs Fusion mode.** Line 30 of this document says sign reading
does not need nav, and that reasoning produced the instruction to revert `706-02-01` to Camera Only.
It was wrong. The working car sits at nibble 4 = `6`, and so do these reports.

### And it explains the 1765 frames

The post-write drive ran at nibble 4 = `6` (Camera + APIM) the whole way, yet `0x3CD` reported
`Available_CameraOnly` with `NoNavDataAvailable` on **every single frame**. The camera never entered
Fusion mode. It could not: Fusion mode needs the APIM feeding it nav data.

```
U0253  Lost Communication With Accessory Protocol Interface Module
       Module: Image Processing Module A
       status -2C  DTC Maturing - Intermittent at Time of Request   (2026-08-21 20:07)
```

**The camera has been saying all along that it cannot reach the APIM.** Section 6d already measured
the other half: the APIM sends position only, and `0x463` / `0x464` are absent. Nothing in five
months of as-built work touches that.

**THE BLOCKER IS THE APIM LINK, NOT THE CAMERA AND NOT ITS AS-BUILT.**

### The confirmed-working Focus MK4 recipe, for reference

```
1 - IPMA   "Traffic Sign Recognition Mode: SLOIF"
2 - IPC    "Traffic Sign Recognition"  Disabled > Enabled
3 - ASBUILT enable menus:
    IPC 720-03-01  x7xx xxxx xx   TSR IOD
    IPC 720-04-01  xxxx xxCx xx   TSR overspeed chime
    IPC 720-09-01  xxxx xxxx 0x   TSR SLIF deactivation
    IPC 720-10-01  x0xx xxxx xx   TSR NCAP deactivation
    IPC 720-03-02  xx8x xx        "Traffic Sign Recognition"
SLIF  = speed limit information function
SLOIF = speed limit and other information (overtaking signs)
```

### HIS IPC PHYSICALLY LACKS TWO OF THOSE BLOCKS

Diffing his IPC against the Euro Mondeo CD391 (same `LS7T-14C026` strategy family):

```
720-10-01   HIS --ABSENT--       EURO 0401 0000 003D    <- named in the Focus fix
720-10-02   HIS --ABSENT--       EURO 4000 0079
720-09-01   HIS 2000 0000 0051   EURO 2000 1504 016B    <- zeros where the euro car has data
```

18 blocks on his IPC, 20 on the Euro. **A missing block is not a wrong value -- the record does not
exist and no checksum gets past it.** This is structural confirmation of what BartBK said on
2026-08-09: *"The US IPC does not support TSR. You have to replace it to have TSR on the IPC."*

**But that governs the DASH, not the bus.** openpilot never reads the cluster; it reads `0x3CD`. If
the camera enters Fusion mode it broadcasts there regardless of what the IPC can render. **Do not buy
a cluster to fix an openpilot feature.**

If the dash display is ever wanted for its own sake, the donor is a **UK-market** CD391 Mondeo IPC --
mph and TSR -- not a continental km/h one.

### The as-built checksum, generalised

```
checksum = (0x0H + 0xLL of the address) + section + block + sum(data_bytes)  &  0xFF
```

The address contributes as **bytes**: `706` -> `0x07 + 0x06`, `720` -> `0x07 + 0x20`,
`726` -> `0x07 + 0x26`, `7D0` -> `0x07 + 0xD0`. The earlier 706-only formula summed three nibbles and
worked only because `0x06 == 6`. **Verified against all 201 blocks of four modules with zero
failures.** Any candidate value for any module can now be generated rather than copied off another
car.

### UCDS CANNOT DECODE THIS CAR'S CCC. THE STEP THAT SAID IT COULD WAS NEVER VERIFIED.

The `AsBuilt Editor (CCC)` model list contains, in full: GALAXY/S-Max CD340, Mondeo IV CD345, C-MAX,
Focus III, Kuga II, Escape C520, Fiesta, B-MAX, Transit and its variants, EcoSport, Ranger, Territory.
**No Edge. No Mondeo V. No CD391 at all**, and nothing newer than roughly 2017.

Selecting a model and proceeding returns:

```
Unable to find JU5T-14B476-BAR part number of BCMii module in base! Check model of car selected!
```

That is his BdyCM. The data is not in UCDS. **"Select `EDGE/S-MAX 2015-`, open AsBuilt Editor (CCC)"
was step 2 of section 7 from 2026-08-12 onward and was never possible.** Three sections were built on
a step nobody had checked existed. Delete it on sight.

### Standing caution on the two demo sessions

`WF0EXXWPCELA25401` and `WF0EXXWPCELR15787` are CD391 with a **byte-identical BdyCM** to his
(`JU5T-14F141-BAC` / `JU5T-14B476-BAR` / `JU5T-14C184-AAN`), so block layouts are directly
comparable. **But there is NO evidence either car has TSR.** They may be UCDS reference files rather
than real configured vehicles. That assumption was leaned on three times on 2026-08-21 and must be
verified before any value is copied from them.

---

## 4g. THE ENTIRE IPMA CONFIGURATION IS A BRAZILIAN FILE OFF THE INTERNET

> **PARTIALLY SUPERSEDED.** The Brazilian-origin fact is correct and remains the most load-bearing
> thing in this file. Two conclusions drawn from it are not: the `FF` region reading is corrected in
> **4k** (a working car reads `FFFC` too), and the "get a US-market as-built" recommendation is
> retracted in **4l** (US 2019+ builds may have had sign recognition removed, so a correct US config
> could turn off the one thing that works here).

**Learned from the owner 2026-08-21, and it is the most load-bearing fact in this document.** It
appears nowhere else in it, and every conclusion above was drawn without it.

He wrote a complete IPMA as-built taken from a Brazilian car, a long time ago, and **he does not have
the original**. The Edge camera's own factory configuration is gone.

**THEREFORE SECTION 4's "RESTORE POINT -- as-built as of before any changes" IS THE BRAZILIAN
CONFIGURATION.** There is no US baseline anywhere in this investigation. Every revert performed here,
including two on 2026-08-21, restored the car to Brazil.

**Why it matters more than any nibble.** Brazil follows the Vienna Convention: speed limit signs are
**circular, red-ringed, in km/h**. US signs are **rectangular, white, in mph**. A camera configured
for Brazilian signs drives past every sign in Utah and correctly reports nothing -- while still
reporting TSR `Available`, still reporting `Mph` for its DISPLAY units, and never emitting
`CountryNotSupported`, because as far as it is concerned it is working perfectly in Brazil.

That explains the whole measured picture better than anything else in this file.

**The clean fix is a correct US-market as-built for this module.** Ford publishes factory as-built by
VIN at motorcraftservice.com. What is needed is a **US 2019-2020 Edge with Co-Pilot360** carrying the
same IPMA part (`KT4T-19H406-CE`). He does NOT know the donor module's VIN -- it was bought used --
so the source is a US Edge owner's published `706` blocks, which these forums trade routinely.

### THE REGION FIELD IS ALREADY EXPLAINED, AND IT STAYS CLOSED

`706-04-01`'s first byte is the region/units field. Community values:

```
01 = region undefined / KPH        08 = North America / sign type undefined
02 = region undefined / MPH        09 = North America / KPH
                                   0A = NORTH AMERICA / MPH
```

His reads **`FF`**. That is not a defined region -- and it is exactly consistent with CLAUDE.md's
record that he set the region back to **UNSPECIFIED** after it threw DTCs. His memory and the byte
agree.

**DO NOT PROPOSE CHANGING IT.** He reminded me of this on 2026-08-21 while I was mid-research:
*"Remember when I tried to change the region in the regular config and not the as built it broke
everything."* It is explored, the answer was no, and CLAUDE.md already says so.

Recorded only because the `FF` would otherwise look like an unexplained anomaly to the next session
and get picked up as a fresh lead. It is not fresh.

## 4h. THE NAV REPEATER IS ALREADY CORRECT. THE ABSENCE IS BEHAVIOURAL.

> **The Nav Repeater finding stands. The Android Auto conclusion is SUPERSEDED BY 4j** -- he drove
> with the phone off USB and `0x463`/`0x464` were still at zero frames. The APIM is configured to
> repeat navigation data and does not, and why remains open.

The strongest-looking lead of 2026-08-21, checked and closed the same hour.

`roylion15` -- the FORScan forum's authority on this, whom every TSR thread defers to -- on what
makes an APIM feed the camera at all:

> "need two settings activation in APIM to **send nav data to IPC /IPMA** ... (in direct config with
> forscan). **nav repeater conformance, set current**. **nav repeater format, set motorola**"

"Nav Repeater" is literally the APIM function that repeats navigation data onto the bus for the IPC
and IPMA -- the exact thing measured missing. And it is Direct Config, not as-built, so nothing in
this investigation had ever looked at it.

**HIS CAR IS ALREADY SET CORRECTLY:**

```
Navigation Repeater format        Motorolla                 <- as recommended
Navigation Repeater conformance   Current                   <- as recommended
Navigation                        Enabled common interface
Non-Metric Units for NAV          Miles/Feet                <- correctly US
```

**So the APIM is configured to repeat navigation data and sends one of three GPS messages anyway.**
That is the finding: the defect is not configuration, it is runtime behaviour.

**Which leaves Android Auto as the leading explanation, unopposed.** Ford's own documentation: the
built-in GPS *"can only be used if CarPlay or Android Auto are disabled, or the phone is connected
via Bluetooth"*, and once a phone is set up for Android Auto, *"SYNC 3 will always defer to it when
that phone is plugged in -- mapping and navigation are deferred too."* He always projects. A deferred
nav stack has nothing to repeat, while `0x462` keeps flowing because raw position is wanted by
modules that do not care about navigation.

**THE TEST IS FREE AND NEEDS NO SOFTWARE:** one drive with the phone off USB (Bluetooth is fine --
it is projection that defers, not pairing), first half with no destination and second half actively
navigating in SYNC, past posted limits. Raw CAN is in the route log on any branch. Three outcomes,
all useful: the messages appear (Android Auto was it), they appear only once routed (same answer,
and unusable day to day), or still nothing (the APIM is configured to repeat and does not, which
points at the gateway and is genuinely new).

## 4i. TWO ERRORS ON 2026-08-21, BOTH FROM REASONING PAST THE RECORD

**1. I talked him into reverting `706-02-01` to Camera Only.** Line 30 of this document says sign
reading does not need nav, so Camera Only looked like the mode that targets optical recognition. But
the friend's working car sits at nibble 4 = `6`, and two forum reports name **Fusion mode** as the
state in which signs are actually read. Section 4f has the detail. The advice was wrong and was
reversed the same day.

**2. I reconstructed a change to `706-02-01` that the record already contradicted.** Section 4d
recorded `FD56` on 2026-08-12 and the live read was `FD52` on 2026-08-21, and I concluded the `6` was
a transient from 4b that had reverted. **Section 4's restore point ALSO reads `FD56`** -- so `6` was
the value "before any changes", and the movement was `6` -> `2`, the opposite direction. Checking
section 4 against 4d before reasoning from the difference would have caught it in one grep.

---

## 4j. THE CAMERA READ A SIGN. VERIFIED AGAINST STREET VIEW. 2026-08-21.

**Route `000003a7--f0fee7f062`, segment 6, seven consecutive frames:**

```
                        every previous frame       these seven
TsrVLim1MsgTxt_D_Rq     255  NoLimit          ->   30  Message30
TsrVl1PrmntMsgTxt_D_Rq  0    DoNotShowSign    ->   1   ShowPermanentlyWithoutSupp
payload                 372320fffcc80220      ->   3723201efcc90220
```

Byte 3 went `ff` -> `1e`. That is a whole byte, not a packed field. And the camera independently set
"show this sign permanently" in the same frames -- **two semantically coherent fields moving
together, which noise does not do.**

**GROUND TRUTH: `40.725463, -111.829903`, 2011 2100 S, Salt Lake City.** Street View shows a
**SPEED LIMIT 30** on a pole at exactly that spot. He confirmed it: *"Bingo. Literally right there."*
Decoded from the car's own `0x462`, at 15.9 -> 6.7 mph, at night.

**THIS IS THE FIRST SIGN THIS CAMERA HAS EVER BEEN OBSERVED TO READ.**

### What it kills

- **Every hardware theory.** `KT4T-19H406-CE` can recognise a US speed limit sign and report it. The
  base-camera-variant theory, the `LV4T` assembly difference against the friend's car, the "US IPC
  means replace the cluster" reading -- all dead. It is configuration, and configuration is fixable.
- **Fusion mode is NOT required.** It read the sign in `Available_CameraOnly` with
  `NoNavDataAvailable` asserted on all 747 frames including the seven. Line 30 of this document was
  right the whole time: sign reading does not need nav.
- **Android Auto was not suppressing anything.** He drove with the phone off USB and `0x463`/`0x464`
  are still at **zero frames**. Section 4h's leading hypothesis is wrong.
- **The synthesized-GPS feature is not the fix.** The missing messages are a real, measured defect
  and `U0253` is real, but they are not what was stopping sign reads. The code stays -- it addresses
  a genuine fault and stands down by itself -- but it must not be described as the TSR fix.

### THE DETECTION RANGE IS ZERO. THIS IS THE DEFECT, AND IT IS MEASURABLE.

**The most useful number from 2026-08-21.** Distance from the sign against what the camera reported,
decoded from the car's own `0x462`:

```
 dist to sign    mph   VLim1        heading   turn
      183 m     ~29     255            90
      157 m     ~28     255            91      +1
      130 m     ~28     255            91      +0
      104 m     28.2    255            90      -1
       78 m     27.1    255            91      +2
       54 m     26.0    255            89      -2
       31 m     24.3    255            89      -1
       10 m     22.3    255            89      +1
        0 m     15.9     30   <- read   89      -0
       11 m      8.0     30              111    +19   <- turn STARTS, after the sign
       18 m      7.9     30              163
       21 m      7.9    255              148
```

**183 metres of dead-straight approach at a constant 89-91 degrees, and the camera recognised
nothing until level with the sign.** The turn only begins AFTER passing it, which is what ends the
read five seconds later as the sign leaves frame. So there is no "it was around a corner"
explanation.

**THE SIGN IS VISIBLE AT 104 m IN THE DASHCAM FOOTAGE, AND HE HAD TO POINT IT OUT.** Frames were
pulled from segment 6's `qcamera.ts` and the 104 m one was read as "the road ahead is dark, the sign
was not lit yet" -- which was WRONG, and would have retracted a correct finding. **He marked the sign
in the frame: a small bright rectangle, plainly there.** Retroreflective signs return headlight light
far beyond the distance at which headlights illuminate the ROAD, which is the entire point of them.
The mistake was looking at road illumination instead of the retroreflector.

So the finding stands and is better supported than before: **the sign was visible from at least
104 m and the camera reported nothing at 104, 78, 54, 42, 31, 20 or 10 m.**

**The one fair caveat is RESOLVABILITY, not visibility, and the footage bounds that too.** At 104 m
the sign is a handful of pixels -- enough to see something is there, not enough to read digits. But
**at 31 m it is an unambiguous white rectangle on the pole, clearly resolved in a heavily compressed
526x330 qcam frame, at night.** The IPMA is purpose-built for this and far higher resolution than
the comma's qcam. It reported `255 NoLimit` at that exact moment.

```
 104 m   visible as a retroreflective point         camera: 255
  31 m   clearly resolved as a white rectangle      camera: 255
  10 m   unmissable                                 camera: 255
   0 m                                              camera: 30
```

So the conclusion does not depend on the far end of the approach at all. A production TSR commits at
30-50 m; this one failed at 31 m, at 20 m and at 10 m.

**Daylight is still the better measurement**, because it removes the argument entirely: a sign is
visible AND resolvable from 200 m+, and "recognised at N metres" becomes a number nobody has to
argue about.

### THE CAMERA IS A MOBILEYE, AND THE PIXEL ARITHMETIC CLOSES THE ARGUMENT

**The IPMA is a Mobileye unit.** Ford has a global agreement with Mobileye for the EyeQ family, and
a teardown of a 2018 F-150 IPMA (`JL3T-19H406-AD`) found a **Mobileye EyeQ3** fabricated by ST
Micro. This car's `KT4T-19H406-CE` is the same part series one generation on, so EyeQ3 or EyeQ4.

Taking the EyeQ3-era Mobileye mono camera -- 1280 px wide, 38-52 degrees horizontal FOV, typical for
that generation though not confirmed for this exact part -- a 24-inch US `SPEED LIMIT` sign subtends:

```
 dist     38 deg lens   52 deg lens    camera reported
 104 m        11 px         8 px       255 NoLimit
  78 m        15 px        11 px       255 NoLimit
  54 m        22 px        16 px       255 NoLimit
  42 m        28 px        20 px       255 NoLimit
  31 m        38 px        28 px       255 NoLimit
  20 m        59 px        43 px       255 NoLimit
  10 m       118 px        86 px       255 NoLimit
```

Numerals need roughly 20-30 px of sign width to classify. **Even on the wider lens that is satisfied
from about 31 m in.** So at 10 metres the sign occupied 86-118 px of width -- unmissable, filling a
substantial fraction of the frame -- and the camera reported no limit.

**THIS IS NOT AN OPTICS, RESOLUTION OR DISTANCE PROBLEM.** The sign was present, lit, resolved and
enormous. The classifier did not match it.

And it quantifies the gap: an EyeQ3 doing TSR normally commits around 20-30 px, which on this lens is
**40-50 metres**. This one needed zero. That is not a degraded system -- it is a system matching
against the wrong thing, which is exactly what a Vienna-Convention circular-plate template does
against a US rectangle no matter how many pixels it is given.

**A working TSR recognises a sign 30-50 m out. This one has an effective range of about zero.** That
is the entire explanation for the hit rate:

```
at  7 mph   ~3.1 m/s    several seconds beside the sign   ->  read
at 30 mph  ~13.4 m/s    well under a second                ->  missed
at 70 mph  ~31 m/s      no chance
```

He got this one because he crawled past at 16 mph and slowed to 7. Every sign on the 72 mph drive
was gone before the camera could commit.

**AND THE SLOW SPEED WAS NOT INCIDENTAL -- he was turning around.** His words: *"I was going slow
because this is actually right before I turned around!"* The heading trace agrees: +19, +24, +28
degrees immediately after the sign, then swinging back. So this was a deliberate deceleration for a
maneuver, not a representative moment of driving.

**Which means the one detection this camera has ever produced happened under about the most
favourable conditions available on a public road**: head-on approach, 183 m of clear sightline,
decelerating through 16 to 7 mph, at night with headlights on a retroreflective sign, and several
extra seconds beside it during the turn. It needed ALL of that to get one read.

Recorded because "he was slow" is exactly the detail that gets lost and then misread later as
evidence that low speed is the FIX. It is not a fix; it is how far conditions had to be stacked
before a 0 m detection range produced anything at all.

### THE DENOMINATOR, FROM HIS OWN TILE STORE

"many signs" is not a number. `tools/bp_offline_map.py --at` against the OSM tiles on his device,
sampled along the route-a7 track, gives one:

```
1500 East / Chadwick / Parkway Ave    residential     20 mph
1700 East                             tertiary        25 mph
2100 South                            secondary       30 mph   <- the one it read
```

The drive ran **20 -> 25 -> 30 and back down**, out and back. That is at least SIX limit transitions,
each of which carries a sign, plus the repeater signs a secondary road like 2100 South carries
mid-block. **The camera reported exactly one value all drive.**

It also confirms the read was CORRECT, with three independent sources agreeing: OSM has 2100 South
at 30, Street View has a `SPEED LIMIT 30` on the pole at those coordinates, and the camera said 30.
No ambiguity anywhere in that chain -- this was a true positive, not a lucky wrong number.

And it makes the arithmetic concrete. On 20-30 mph residential and tertiary streets a sign is in
useful view for **one to two seconds**. With a detection range of zero the camera needs the sign to
fill the frame, so it catches one when he happens to crawl past at 7 mph and nothing otherwise.

**WHY THIS REFRAMES EVERYTHING.** A DISABLED system reads nothing at any range. This one reads at
0 m. So TSR is not switched off -- it is a recognition pipeline running at the absolute edge of its
capability, which is what a camera matching against the WRONG SIGN TEMPLATE looks like: it only
scores a hit when the sign fills the frame. That is exactly what a Brazilian configuration
(circular, metric) would do on US roads (rectangular, mph).

**It also gives every future change a metric instead of a yes/no.** Score with one command:

```
python bluepilot/asbuilt/tsr_score.py "route_dir/*.rlog.zst"
```

It reports every detection with a maps link, the approach profile and range for each, whether the
APIM sent `0x463`/`0x464`, and the camera's status enumerants across the drive. Re-run against
route `000003a7` and it reproduces everything above -- and measures the straight approach at
**237 m**, longer than the 183 m first quoted. **The number to beat is 0 m.** A config change that moves
recognition out to even 20 m would multiply the hit rate several-fold and would be unmistakable in
one drive.

**And it lowers expectations for the 4k write.** A single mode nibble is unlikely to move a
detection range from 0 m to 40 m. Still worth the drive -- it is one nibble with a clean restore --
but do not expect it, and score it on RANGE rather than on whether any sign is read at all.

### What it does NOT show

**One sign, out of many 20s, 25s and 30s on a 13-minute drive.** 739 of 747 frames still read 255.
That is not a working camera; it is a physically capable camera recognising almost nothing.

**The configuration was IDENTICAL to the 1765-frame drive that read zero** -- `706-01-01` at
`0810 A9DB B964`, `706-02-01` at `FD56 16DB 7FD3`, confirmed by him. So configuration is not what
changed between the two drives. What differed was **night, and 32 mph instead of 72**. Speed limit
signs are retroreflective, so under headlights at 7 mph they are far higher contrast than the same
sign in daylight at highway speed. A camera hunting for the wrong sign STANDARD would plausibly get
a lucky match exactly there and nowhere else.

Which points back at section 4g: the configuration came from Brazil, where signs are circular and
metric, and the region byte reads `FF`. **The evidence now points there rather than at nav data.**
The region control is still closed -- he has said no twice and the DTCs were real -- but a correct
**US-market as-built for `706`**, written as raw blocks with computed checksums, is a different
action from the friendly-name control that burned him.

### A decode bug worth remembering

The first coordinates reported were `40.725463, -110.170097` -- Ashley National Forest, 1.7 degrees
east of the truth, and he said so immediately. `0x462`'s minutes are a MAGNITUDE: they move the
position away from zero, so on a western longitude they must be SUBTRACTED from a negative degree.
Adding them walks the fix east. Latitude was unaffected because Utah is north. **A position that
lands in the wrong place is obvious; one that lands 400 m away is not** -- check a decoded
coordinate against something known before quoting it.

---

## 4k. NIBBLE 8 OF `706-01-01` IS THE TSR MODE FIELD. THE NEXT WRITE.

**Found 2026-08-21 on the FORScan forum, thread 9806 page 32.** `Dragunov`, on a **Mondeo MK5 --
CD391, this platform**, names the field outright:

> "Activated TSR by changing the code in this line ... **IPMA `706-01-01` `xxxx xxx* xx`, `*` to `A`
> (Reading + GPS), the `*` was `9` (Disable)**"

That is nibble 8. Against the two cars in this document:

```
his              0810 A9DB B964      nibble 8 = B      one sign in 747 frames
his friend       0810 A9DA A953      nibble 8 = A      TSR works
documented       9 = Disable,  A = Reading + GPS
```

**THE NEXT WRITE, checksum computed and verified:**

```
706-01-01  ->  0810 A9DA B963       nibble 8: B -> A
restore    ->  0810 A9DB B964
```

Nibble 9 is deliberately LEFT at `B` rather than copying the friend's whole block. Nibble 8 has a
documented meaning; nibble 9 does not, and the same thread's advice is *"make the changes per
function and not all in one."*

**What it is expected to fix: UNKNOWN, and possibly nothing.** Stated plainly because this was
oversold once already:

- `9` is Disable and the camera **read a sign**, so whatever `B` is, it is not disabled. This field
  is therefore not what is holding the hit rate at one-in-many.
- `A` is "Reading **+ GPS**", a fused mode, and **this car's APIM does not send GPS** -- `0x463` and
  `0x464` measured at zero frames. Whether `A` degrades gracefully without it or waits on data that
  never arrives is not known. It could be a step backwards.
- `Dragunov` made exactly this change and reported signs still did not appear.

It is worth doing anyway because it is ONE nibble, on a named field, toward the value a working car
on this platform runs, with a clean restore and a measured baseline to compare against. **Drive the
same roads past the same signs and count reads against one-in-747.**

### CORRECTION: `706-04-01` WAS MISREAD, AND IT WAS NEARLY WRITTEN UP AS A SECOND CHANGE

This file recorded his `706-04-01` as `FFFC 26C3 847A` from 2026-08-21 onward. **The car reads
`FFFC 27C3 847B`, and he confirms he has never written that block.**

Both values are checksum-valid, so the checksum cannot arbitrate between them. But `7 -> 6` and
`B -> A` are each "one less" -- the shape of a transcription slip off a screenshot, not of a module
rewriting itself. **Treat `FFFC 27C3 847B` as the true value throughout.**

**Why it matters beyond tidiness.** On 2026-08-22 he wrote the nibble-8 change from 4k, sent a fresh
screenshot, and the difference against the misrecorded value read as a SECOND concurrent write. It
was reported to him that way -- "that's two changes, the drive can't attribute" -- and he corrected
it: *"I only made one change... I only changed 706-01-01."* He was right. One change is in place and
the next drive CAN attribute.

**The lesson is the one this file keeps re-learning**: a value transcribed by eye from an image is
not a measurement, and a self-consistent checksum does not make it one -- an off-by-one in two
digits preserved validity perfectly. Re-read the block off the car before reasoning from a
difference.

### AND THE REGION READING FROM 4g WAS WRONG

Section 4g called `FF` in `706-04-01` an unset region, from a community value table where `0A` is
"North America / MPH". **`fred4009`'s WORKING Fiesta reads `FFFC 27xx xx`** -- so `FF` in that
position is normal, not unspecified. The table is for a different position or a different vehicle
line.

So the region is not the anomaly 4g claimed, there is no reason to go near the control that threw
DTCs, and the `FF` should not be picked up as a lead again. That is the SECOND time a value table
found by search has been applied to this car's blocks without a control to check it against.

### Two other things from thread 9806 worth keeping

- **A write that reports success and silently reverts is a known IPMA behaviour.** `alextheboss96`:
  *"Once I press write it says blocks programmed successfully, but when I close the module and
  reopen it, it has the previous value."* That is section 4b's failure mode, on someone else's car.
  **His module does NOT do this** -- both 2026-08-21 writes persisted across an ignition cycle,
  which is a real difference in his favour.
- **`WWA` (Wrong Way Alert) gave `Dragunov` a camera error.** If a future block touches it, expect a
  fault.

---

## 4l. TWO OTHER OWNERS OF THIS EXACT CAMERA GET NOTHING. AND A US AS-BUILT IS THE WRONG TARGET.

**FORScan forum topic 30014, "TSR in Ford Edge 2.7 [2019]", January-February 2026.** Found
2026-08-21. It is the closest match to this car that exists anywhere, and it reverses the
recommendation section 4g/4j were building toward.

**`lucuszysko`, Ford Edge ST 2019, IPMA `KT4T-19H406-CE` -- the same part number:**

> "It seems that everything I enabled in the configuration - there are no errors from the modules -
> IPMA calibration has been performed - a menu option regarding sign reading appeared ... **Despite
> all of this, no signs are detected while driving.**"

Then, after more work:

> "It looks like the **Region configuration is not equal for all three modules: IPC, APIM, and
> IPMA.** For the first two, I was able to set the Region to PL, but **for IPMA it is somehow
> blocked -- any attempt to change this value ends up in failure.**"

**`marjanoos`, same part number:**

> "I think the IPMA fw has to be european. **I have the same issue with the same part number.**"

and after getting EU Edge Vignale calibration files:

> "It has the same names, SBL, Strategy and Calibration. **So it looks like we have the same
> firmware.** However **I can't change the region as well.** ... Found european coding but **it
> doesn't contain 706-04-XXX section at all.**"

**`pdxpeter`, 2020 Ford Edge ST -- a US car:**

> "**the speed signs were removed from the 2019+ firmware, at least in the US.**"

### What this establishes

1. **This car is AHEAD of both of them.** They report zero signs. His camera read a 30 and set
   `ShowPermanentlyWithoutSupp` in the same frames (section 4j). Whatever his configuration is
   doing, it is doing more than two people who did everything by the book.
2. **The IPMA region is WRITE-BLOCKED on this module for both of them**, and his is not -- both
   2026-08-21 writes persisted across an ignition cycle. That is a real capability they do not have,
   and it is worth not squandering.
3. **EU and US IPMA firmware are IDENTICAL** -- same SBL, strategy and calibration, confirmed by
   someone who obtained both. **The firmware theory is dead**, and the standing rule against running
   the IPMA update to `CF` loses its last competing justification but stays for its own reasons.
4. **A EUROPEAN as-built has no `706-04-xx` section at all.** His car has `706-04-01` and
   `706-04-02`. So block presence differs by market, and a wholesale foreign import is not a
   like-for-like swap.

### THE RETRACTION: DO NOT CHASE A US-MARKET AS-BUILT

Sections 4g and 4j worked toward "get a correct US-market `706` config for this module". **On this
evidence that is the wrong target and may be actively harmful.**

If US 2019+ builds had sign recognition removed, a correct US configuration would plausibly turn off
the one thing that works here. **The Brazilian configuration may be WHY this camera reads anything
at all**, rather than why it reads so little -- the exact opposite of section 4g's reasoning.

**Which makes the friend's car the right reference after all**: a non-US vehicle where TSR genuinely
works, running the same strategy and calibration. Section 4d's caution about copying his
market-specific nibbles was written before any of this was known, and it now points the wrong way.

**This is the third time in one day that a confident framing about markets has been wrong** -- first
"the friend's car is a different market so his nibbles are dangerous", then "`FF` is an unset
region", now "get a US as-built". Every one was reasoning from a plausible story rather than from a
control. The measurements that survived are the ones taken on his own car.

---

## 4m. THE CAUSAL CHAIN, END TO END -- AND WHY ITS ENDPOINT IS HIS CALL, NOT A RECOMMENDATION

Assembled 2026-08-21 from the Mobileye research plus everything measured that day. **Every step is
sourced. The endpoint is the one thing he has ruled out twice, so it is recorded as analysis and
explicitly NOT proposed.**

1. **EU and US IPMA firmware are byte-identical.** `marjanoos` obtained EU Edge Vignale calibration
   files and compared against a US car: same SBL, same Strategy, same Calibration (4l). One image
   serves both markets.
2. **Therefore sign templates cannot be baked into the firmware.** If one binary serves Europe and
   North America, the sign set must be selected at RUNTIME by a configuration value.
3. **Mobileye confirms TSR is region-parameterized.** The IPMA is a Mobileye EyeQ unit. Their ISA
   product is *"certified for use in all 27 EU countries as well as Israel, Norway, Switzerland and
   Turkey"* -- a per-country certification -- and they describe *"signature-based classification
   that loads the 'signature' of a new traffic sign to the vehicle"*. Sign signatures are DATA,
   selected per region.
4. **This camera resolves a sign at 86-118 px and matches nothing.** Not optics, not resolution, not
   distance -- see the pixel arithmetic above. The classifier is comparing against the wrong
   signature set.
5. **The runtime selector is the region field**, and this car's entire IPMA configuration came from a
   Brazilian file with no original kept (4g). Brazil is Vienna Convention: circular, metric.
6. **And he may be UNIQUELY able to change it.** Both other owners of `KT4T-19H406-CE` report the
   IPMA region write FAILS -- *"for IPMA it is somehow blocked, any attempt to change this value ends
   up in failure"*, and *"I can't change the region as well"* (4l). **His module accepts writes**:
   two landed on 2026-08-21 and persisted across an ignition cycle.

**This is the strongest causal story this investigation has produced.** It explains the 118-pixel
failure, which nothing else does.

### AND IT IS STILL NOT A RECOMMENDATION

**He set the region once, got DTCs, and set it back to unspecified.** He has said no twice, most
recently on 2026-08-21 while this research was in progress: *"Remember when I tried to change the
region in the regular config and not the as built it broke everything."* **That is a decision, not an
obstacle to route around. Do not pitch it, do not re-raise it in a later session as though it were
unexplored, and do not treat this section as permission.**

One fact recorded beside it, because it is fact rather than argument: that attempt went through
FORScan's **friendly-name control**, on the one module FORScan decodes through a 2020 Fusion profile
-- the same profile that reports "wheel arch height 1338 mm" for a feature-configuration byte
(section 3). Whether it wrote the field it claimed to is unknown. Raw as-built with a computed
checksum is a different mechanism from that control.

**If he ever chooses to revisit it, this is the reasoning.** If he does not, this section is why the
investigation stops here rather than why it should continue.

---

## 4n. THE NIBBLE-8 WRITE: TOOK EFFECT, CHANGED THE STATUS FOR THE WORSE, READ NOTHING

**2026-08-22.** `706-01-01` written to `0810 A9DA B963` -- nibble 8 `B` -> `A`, "Reading + GPS"
(section 4k). One change only; the `706-04-01` difference reported at the time was a misread, see
the correction above. Three daylight drives followed, one of them a deliberate repeat of the route
from 4j **past the same sign at the same speed.**

```
000003a7   2026-08-21  NIGHT      747 frames  peak 32.2 mph   7 frames, 1 sign
000003a8   2026-08-22  daylight   913 frames  peak 36.6 mph   0
000003a9   2026-08-22  daylight   265 frames  peak 35.1 mph   0
000003aa   2026-08-22  daylight   953 frames  peak 74.5 mph   0
```

**Zero detections in 37 segments**, including the controlled repeat.

### THE WRITE REACHED THE CAMERA, AND ITS ONLY EFFECT WAS A REGRESSION

The broadcast changed -- byte 2 went `20` -> `30`, and exactly one signal moved:

```
TsrVl1StatMsgTxt_D_Rq    before  2  LimitReiable
                         after   3  LimitOutdated
```

So `A` = "Reading + **GPS**" genuinely engaged the fused mode, and because the GPS half never
arrives -- `0x463`/`0x464` still zero on all three drives -- the camera now rates its (nonexistent)
limit as OUTDATED rather than reliable. **That is the risk written into 4k before the write, and it
is what happened.**

**RESTORE `706-01-01` -> `0810 A9DB B964`.** The change bought a status regression and nothing else.

### THE CONFOUND, STATED PLAINLY

**Two variables moved between the drive that read a sign and the drives that did not**: night ->
daylight, AND the nibble. This does not isolate either. The experiment that separates them is to
restore the nibble and drive the same loop AT NIGHT. If the 30 comes back, night was the factor and
the nibble was neutral-to-harmful.

Worth weighing before assuming the nibble is the culprit: a retroreflective sign under headlights at
7 mph is a far easier target than the same sign in daylight at 30, and 4j already measured that the
one detection needed about the most favourable conditions a public road offers.

### AND FUSION MODE IS NOW CONCLUSIVELY NOT THE ANSWER

Both 2026-08-22 drives reached `Available_FusionMode` with `NoInformationAllOK` -- the camera's
fully healthy state, nothing wrong, nav data flowing -- for ~30 frames each, **and read nothing**.
Route `000003a1` showed the same on 2026-08-21 (section 4f). Three independent drives now.

**Fused mode is reachable on this car and it is not what produces sign reads.** Do not re-open it.

### A SCORER DEFECT WORTH KNOWING

`tsr_score.py` reported "2 with a limit" on route `000003a9`. Both were BOOT frames -- `VLim1 = 0`,
with `TsrStatMsgTxt` reading `Null` and `NoDataExists`. Zero is not a limit, it is an uninitialised
frame. Fixed: a detection now requires `VLim1` outside `(0, 255)`. **A tool that counts the
uninitialised state as a success will eventually report a fix that did not happen.**

---

## 4b. THE ONLY THING THAT DEMONSTRABLY WORKED: "TSR data source = Camera + APIM"

Filed as a dead end at first. It is the opposite -- it is the single piece of positive evidence from
two days, and he had to point that out twice before it was written down properly.

**What happened:** his IPMA was on "TSR data source: Camera Only". He set it to **Camera + APIM** and
`NoNavDataAvailable` CLEARED on the comma screen immediately. After an ignition cycle the message came
back AND the setting had reverted to Camera Only.

**Why that matters more than anything else here.** THERE ARE TWO DIFFERENT FAILURE MODES and they had
been lumped together:

| mode | what happens | example |
|---|---|---|
| **FORScan refuses** | error dialog, nothing reaches the car, no DTC | `0450`, the IPC SLIF change |
| **Module accepts, then reverts** | the write lands, behavior CHANGES, a power cycle undoes it | Camera + APIM |

The second one got through. The camera acted on it. So the camera **can** be made to stop asking for
navigation data -- which is the entire goal -- and the remaining problem is not "what value" but
**"why will the module not commit it."**

A write that applies and then reverts at the next boot is the module accepting into working memory and
then failing its own configuration validation at startup, restoring what it had. That is `U2101`
again, and probably the same cross-module check that refuses everything else.

**So the target has changed.** Do not go looking for a different value. The value is known and it
worked. Find out why it does not persist.

**Ask the friend what HIS TSR data source is set to.** If his reads Camera + APIM and it persists,
that is the exact target state and the question narrows to why this module will not hold it.

## 4c. THE FAILURE IS TSR-SPECIFIC. THE MODULE COMMITS OTHER CHANGES FINE.

Stated by him 2026-08-12, and it eliminates three theories at once: **other IPMA as-built changes
have persisted, with the same UCDS adapter, under the same power conditions.**

So all of these are OUT:

- **The adapter.** IPMA writes land with it.
- **Power-saving / auto-engine-off interrupting the commit.** Other changes commit under the same
  conditions in the same sessions.
- **FORScan being generally unable to write this module.** It can; it does.

What is left is narrow and specific: **the camera accepts reconfiguration, but not reconfiguration
that enables TSR.**

  - As Built view, TSR fields -> refused outright, nothing reaches the car
  - Friendly view, TSR data source -> lands, works, reverts at the next boot
  - Anything else -> sticks

**And the DTCs are INSTANT.** He reports getting them the moment he makes a TSR change -- not after a
drive, not intermittently. The camera evaluates the new configuration immediately and rejects it.
Unrelated changes in the same session produce nothing.

That is a FEATURE AUTHORIZATION, not a mechanical write failure. Some other authority on the car
declares whether this vehicle has TSR, and the camera defers to it -- which is exactly what `U2101
Control Module Configuration Incompatible / Signal Plausibility Failure` means.

**Do not spend more time on adapters, cables, voltage, power-saving, checksums or which FORScan view
to use.** They are all ruled out by one sentence: other changes to the same module work.

The authority is most likely the gateway, which he does not want WRITTEN to (section 6b -- reading it
and reasoning about it are fine, and that distinction matters). If that
is right, TSR is not reachable on this car by configuration alone. The remaining evidence that could
overturn it is his friend's car -- same question, sharper: does a Fusion exist where the IPMA HOLDS a
TSR-enabling configuration across a restart?

## 4d. THE FRIEND'S AS-BUILT, DIFFED — AND IT KILLS TWO THEORIES

Obtained 2026-08-12. Same strategy and calibration as his, TSR WORKING. Twelve blocks differ in data
(checksum-only differences ignored):

```
block       yours            friend           differing nibbles (1-based, checksum excluded)
706-01-01   0410 A9DB B960   0810 A9DA A953   2, 8, 9      <-- the block of interest
706-01-02   301A 6535 6458   101A A535 6478   1, 5
706-02-01   FD56 16DB 7FD3   FD56 16DB 5FB3   9
706-02-02   FFC1 55AA E1B1   FFC3 55AA E1B3   4
706-02-03   0842 1000 006C   F840 0800 0052   1, 4, 5, 6
706-02-04   0000 0000 0013   0008 0000 001B   4
706-02-05   0000 0000 0014   0800 0000 0824   2, 10
706-02-07   0000 0004 001A   0000 0084 009A   7
706-03-01   C000 5200 80A3   8000 0000 8011   1, 5, 6
706-03-04   0080 0000 0094   0089 0000 009D   4
706-04-01   FFFC 27C3 847B   1EFC 26C3 485D   1, 2, 6, 9, 10   (his was MISREAD as ...26C3 847A)
706-05-01   53AA 7400 0084   566A B800 008B   2, 3, 5, 6
```

**TWO THEORIES DIE HERE.**

1. **`706-01-01` nibble 3 -- the reference's `ModuleFeatureCfg_TSR` -- is `1` on BOTH cars.** `1`
   means "TSR Off" per that map, and TSR works on the friend's car. So either the position is wrong
   for these modules or that field is not the enable. **`0450` was never the answer**, and FORScan
   refusing it prevented a change that would have done nothing.
2. **`706-02-01` nibbles 1-2 (`FeatureCfg_TSRMode`) are `FD` on both.** So `4D` / "CameraOnlyOn" was
   never needed either. Both of those consumed hours.

**What is actually different in the block of interest:** `706-01-01` nibble **2** (`4` -> `8`), and
nibbles **8** and **9** (`B` -> `A`, `B` -> `A`).

**Caution before copying anything.** Twelve blocks differ and most of that is legitimately different
car content, not TSR -- these are different vehicles with different options. Copying the whole dump
would import his options onto this car. The targeted experiment is `706-01-01` alone, to
`0810 A9DA A953`, with section 4's restore point in hand and the expectation that the radar
calibration will need redoing (section 5).

**And the reference map is now suspect generally.** It was derived from a Ford as-built document for
a different vehicle line, and its one testable claim -- that nibble 3 is the TSR enable -- is
contradicted by a working car. Do not trust its other positions without the same kind of check.

## 5. Dead ends — tested, do not repeat

| tried | result |
|---|---|
| Use Ford nav (SYNC 3 route) instead of Waze | No change. `NoNavDataAvailable` persisted. |
| Set "TSR data source" to Camera Only | **It was already set that way.** |
| Change Region | `U2101 Configuration Incompatible`, twice, months apart. |
| Maverick community values (`xxD2`/`xxD3` in the **second** group of `706-01-01`) | **Wrong field for this module.** Caused `U2101` and a radar fault. |
| IPMA firmware update to `CF` | Would move to Strategy `AF`, away from the known-working `AE`. Not run. |
| Fixing the checksum (`B9A0`, `80D1`) | Same refusal. Not a checksum problem. |

### Two facts that killed otherwise-plausible theories

- **DTC clearing did not cause the radar fault.** He has cleared DTCs thousands of times without it.
- **The calibration was not missing all along.** ACC has always worked.

So `B1433 - Forward Looking Sensor Alignment / Missing Calibration` was caused by the IPMA as-built
write itself, and **reverting the IPMA cleared it with no alignment drive needed.** The camera and
radar are calibrated as a pair — expect any future IPMA as-built work to disturb the radar, and
expect a revert to undo it.

---

## 6. Tooling

**FORScan** — **THIS PARAGRAPH WAS WRONG, see section 4e.** It used to read "cannot write these.
Its profile is a 2020 Fusion; the IPC and IPMA changes are feature enables a Fusion never had."
On 2026-08-21 FORScan wrote `706-01-01` to `0810 A9DB B964` and accepted it. It had refused exactly
one value, `0450`, which was the only value ever tried on that block. Value validation, not profile
validation. The APIM write was never the outlier it was assumed to be.

**UCDS** is installed (`v3.0.001.023`), adapter connected via USB, SN `7E 4E 6A 9B`. **All three
licences read "Not activated".**

**THE LICENCE EXPIRED. IT IS NOT A GEOBLOCK. Established 2026-08-21.** This paragraph used to say
"most likely their activation server is unreachable from the US", which was a guess written as a
finding, and it aimed the next step at a VPN.

The receipt settles it. Order #7238 from autodiagnosticsolutions.com, **4 February 2025**, €535.50,
"UCDS PRO Wireless (V5 ODO+Extended license)". The product page lists package contents verbatim as
**"1 x EXT+ODO Extended license 12 months"**. Twelve months from 4 Feb 2025 is **4 February 2026** —
roughly seven months before the TSR session that found all three licences dead.

Confirmed against this machine so nobody hunts for it again: there is no licence file anywhere under
`C:\UCDS_V3\`, `HKCU:\SOFTWARE\UCDS_V3` holds only settings and an empty `Adapter_SN`, the
`.ulog` files are encrypted, and none of the launcher, main binary or `ucdsj2534.dll` contains an
activation host. **The licence state lives on the adapter and on their server. Nothing local can
report it.**

One red herring, recorded as such: session files show it reading cars on **11 March 2026**, thirteen
months after purchase, which briefly looked like evidence against a twelve-month term. It is not.
Reading never needed the extended licence — per the product page, EXT is what unlocks **VBF Loader,
Update Wizard and Direct Config**.

**BENCH-CONFIRMED 2026-08-21, adapter plugged in, no car.** The expiry is no longer an inference
from the receipt. UCDS was launched with the adapter on USB and the network watched:

| Test | Result |
|---|---|
| adapter enumerates | `UCDS Adapter V5`, `USB\VID_0483&PID_1236\UCDS_V5`, WinUSB driver (NOT a COM port) |
| Adapter SN | `7E 4E 6A 9B` — matches this document |
| licence panel | EXT / ODO / PATS all **"Not activated"** |
| host contacted | **`ucdsys-server.online` → `147.93.88.191`**, freshly resolved |
| TCP 443 and 80 | **both succeed** from his machine |
| TLS cert | valid `CN=ucdsys-server.online`, Sectigo, **renewed 23 Mar 2026** |
| VPN | **none** — default route is his own Wi-Fi via `192.168.1.1` |
| connection state | contacted, answered, closed — no hang, no timeout |

Their server is up, answers over an ordinary Utah connection with no VPN, and the answer is "not
activated". **A geoblocked or dead host cannot produce that reply.** The VPN idea is dead by
measurement. The cert having been renewed in March 2026 also says they are still trading, so a
renewal should actually land.

**THE ADAPTER IS NOT LICENCE-GATED — the application is.** `ucdsj2534.dll` is registered at
`HKLM:\SOFTWARE\WOW6432Node\PassThruSupport.04.04` as `UCDS Team / UCDS-J2534 V3`, `CAN=1`,
`ISO15765=1`, and exports the full standard PassThru set (`PassThruOpen/Connect/WriteMsgs/ReadMsgs/
StartMsgFilter/Ioctl/SetProgrammingVoltage`). The only URLs inside it are GlobalSign code-signing and
timestamp endpoints — Authenticode metadata, not runtime calls. So **any J2534 application can drive
this adapter with the licence expired.**

**PROVEN BY USE, not by string-scanning.** He runs FORScan on this same adapter. Every FORScan
session in this document is dated 11–21 August 2026 — six months after the licence expired on
4 February — and the APIM write `7D0-09-02` **succeeded** in that window. So as-built reading AND
writing both work through this adapter with all three UCDS licences dead. No hedge required.

It still **does not unblock FORScan**, which refuses the IPMA write on *profile* grounds — a Fusion
profile against an Edge module — not adapter grounds.

Static analysis of the gate is a dead end and should not be attempted again: `UCDS_V3.exe` is packed
(37 MB, 55,800 extractable strings, not one matching `licen|activat|expire`), and the definitions
live in a 906 MB `ucds.udb`. None of the visible UI text exists in plaintext on disk.

Renewal is **`UCDS V5 EXT License (12 Months)`, €130**, same seller. The ODO (€100, odometer) and
PATS (€80, keys) licences are irrelevant to as-built work — do not buy them. **But test before
paying:** As-Built editing may be base functionality, since only Direct Config is named on the
extended-licence list. Section 7 step 2 is now what decides that.

UCDS matters for one specific reason: **it lets you pick the vehicle manually.** `EDGE/S-MAX 2015-`
is in its list, so the IPMA can be decoded and validated as the Edge module it actually is, instead
of through Fusion definitions. That is precisely the wall FORScan hits.

Pricing is settled: **€130/year for EXT alone** from autodiagnosticsolutions.com, which is where
order #7238 came from. The old note here said "6,000 ₽/year" and "their site no longer does direct
international sales" — both stale; he bought internationally from this reseller in 2025.

---

## 6b. THE GWM EXPLAINS IT — AND HE DOES NOT WANT IT WRITTEN TO

Reported 2026-08-12, and it reframes everything above:

> "Auto high beams stopped working with the new IPMA, but that got fixed by a GWM update, and now the
> IPC has a new indicator for them that it didn't have when they were working on my old IPMA."

That is a retrofit ADAS feature restored by updating the GATEWAY, plus the cluster gaining an
indicator it never had. Both are exactly what TSR needs, and neither came from the IPMA.

**Why this fits the evidence better than anything tried so far.** The GWM broadcasts
`GGCC_Config_Mgmt_ID_1_FD1` (0x40A), carrying `VehicleGGCCData` -- a 64-bit vehicle-configuration
identity whose receiver list in the Ford DBC includes **`IPMA_ADAS`**. That is what the camera
compares its own as-built against, and `U2101 - Control Module Configuration Incompatible` with
symptom **Signal Plausibility Failure** is precisely the shape of a module finding its configuration
inconsistent with what the vehicle declares.

So every refused write may have been the camera correctly refusing to enable a feature the GATEWAY
says this car does not have. Enabling TSR in the IPMA and IPC while the GWM declares no TSR is an
incompatible pair, and the module is the one telling the truth.

**It also undermines "the US IPC does not support TSR."** His cluster gained a new indicator from a
gateway update. That claim was about hardware; what he observed is configuration.

**HE HAS RULED OUT WRITING TO IT, 2026-08-12: "I don't think I should touch the GWM. It took me
forever to get where it is."** Reasonable -- the gateway routes every module on the car, so a bad
write there is not a reverted as-built, it is a car that does not start, and he spent a long time
getting the retrofit stable. **So: do not propose changing its firmware or as-built.** That is the
whole of it.

**READ THE SCOPE OF THAT SENTENCE AND DO NOT WIDEN IT.** This section previously said the GWM was
"OFF LIMITS", told the next reader not to suggest "just reading" it, and declared the subject closed.
That was an overreach, it got quoted in conversations with nothing to do with the gateway, and he
corrected it in plain terms on 2026-08-16:

> "Bro always says the gateway is off limits but that was completely unrelated to openpilot! Stop
> fucking saying that! That was because I didn't want to change firmware on it once and now you just
> keep taking it out of context!"

Reading the GWM, decoding what it broadcasts, and reasoning about its role are all fine and are how
the paragraphs above were written in the first place. The same mistake is recorded once already in
CLAUDE.md, where the ruling was stretched to cover reading ACCDATA_3 -- a frame `carstate.py` already
parses. Twice is a pattern: **when quoting his decision, quote what he actually decided.**

**What that means honestly.** If the gateway is the gate, then TSR via as-built may not be reachable
at all on this car, because the camera validates against what the vehicle declares at runtime and a
different tool does not change what the gateway broadcasts. The remaining paths are narrower than
they looked an hour ago, and one of them may be "not achievable without touching the thing he will
not touch". Say that plainly rather than letting a search continue that cannot terminate.

## 6c. THE AUTHORITY IS THE BdyCM's CENTRAL CONFIGURATION (CCC) — NOT THE GATEWAY

Researched 2026-08-12, and it identifies the thing that has been refusing every TSR write:

- **`U2101` sets when a module's configuration does not match the CENTRAL CAR CONFIGURATION**, or when
  it receives invalid vehicle configuration data. That is the definition of the code, not a theory.
- **Ford's Central Car Configuration (CCC) is stored in the BdyCM**, not the gateway. "Configuration
  for all modules is stored at some main module and the main module provides necessary information to
  all modules that need it. CC changes are made in BdyCM."
- UCDS's own tool is labelled **"AsBuilt Editor (CCC)"**, which is exactly this block.
- FORScan has a documented Central Configuration programming procedure.

**This matters because it is NOT the module he ruled out.** Section 6b closes the GWM, correctly and
permanently. The CCC lives somewhere else. Whether he wants to touch the BdyCM is a separate decision
that is his to make -- but it should be presented as a different question, not smuggled in as the
gateway by another name.

**It also explains every observation at once**, which nothing else has:

- TSR writes rejected INSTANTLY, while unrelated writes to the same module commit fine -- the camera
  checks the new config against CCC on the spot.
- Both As Built and friendly-view TSR changes fail, in different ways -- one refused, one accepted and
  reverted at boot when the camera re-validates against CCC.
- His friend's car works on the SAME camera software -- his CCC presumably declares TSR.
- Auto high beams were fixed by a module update after the retrofit -- the same class of problem,
  solved by making the car's declared configuration match the installed equipment.

**A documented remedy exists for exactly this:** load an as-built from a car that has the options you
want. That is the donor request, aimed at the right module this time.

## 6d. THE APIM'S OWN TRAFFIC, FROM THE DBC — AND A U0253 TEST NOBODY HAS RUN

Raised 2026-08-16, from his question about whether SYNC's maps could be a second speed-limit source:
*"If we could somehow use the SYNC maps for speed limits or something else if it's worth it."*

**They cannot, and the DBC settles it without touching the car.** Everything the APIM puts on the
bus, from `ford_lincoln_base_pt.dbc`:

    APIMGPS_Data_Nav_1_FD1  (0x462)  latitude, longitude, hemispheres
    APIMGPS_Data_Nav_2_FD1  (0x463)  UTC date/time, PDOP, compass direction, GPS fault bit
    APIMGPS_Data_Nav_3_FD1  (0x464)  speed, heading, altitude, HDOP/VDOP, satellites in view
    APIM_Data_FD1           (0x32B)  exterior light menus, distance to stopover, GoT edit times

**No speed limit. No road class. No route geometry** -- ON THIS BUS.

**THAT QUALIFIER WAS MISSING AND THE CONCLUSION DRAWN FROM IT WAS WRONG. Corrected 2026-08-17**, when
he asked the question that breaks it: *"then how has my IPC shown speed limit before I even got my
new IPMA?"* It did, so a working path existed, and "there is no third speed-limit source" cannot be
true as stated.

What the search actually covered was `ford_lincoln_base_pt.dbc`, the POWERTRAIN bus. **His car's
MS-CAN is not modeled by any DBC in this repo** -- the only body-CAN file here is
`ford_cgea1_2_bodycan_2011`, a different platform generation, and its single APIM message
(`Personality_APIM_Data3_MS`) carries no limit either. Absence from the one bus we model is not
absence from the car, and section 4b is direct evidence against it: the IPMA has a **"TSR data
source"** setting whose options include **Camera + APIM**, and selecting it changed the camera's
behavior immediately. A module cannot take TSR data from a source that sends none.

So the honest state: the APIM CAN feed the camera speed limits, over a bus openpilot cannot see.

**AND THAT MAKES FIXING 4b WORTH MORE THAN THIS DOCUMENT HAS BEEN TREATING IT.** The camera FUSES its
sources and republishes the result in `Traffic_RecognitnData` (0x3CD), which is on bus 2 and which
this fork already parses. So if "Camera + APIM" can be made to persist, SYNC's map limits reach
Speed Limit Assist through the camera -- WITHOUT openpilot ever needing to see MS-CAN. That is a
second speed-limit source for exactly the roads mapd has nothing for, which is the original problem.

It also explains his report cleanly. His car had a working configuration; the Edge camera arrived set
to Camera Only and will not hold the change. Which of those two is why the display stopped is not
settled here -- the old camera doing TSR by itself would look the same from the driver's seat.

**But the receiver list is the interesting part: `IPMA_ADAS` is listed on all three GPS messages.**
The camera is *supposed* to be getting its position from the APIM, and `U0253` is precisely "lost
communication with the APIM". So:

**UNTESTED, AND IT IS A DIRECT U0253 MEASUREMENT:** are addresses 1122/1123/1124 actually present on
a bus openpilot can see? If they are ABSENT, the camera is not hearing the APIM because nothing is
transmitting — a wiring or routing fault, and the DTC is literal. If they are PRESENT on bus 0 or 2,
the frames exist and the camera is rejecting or not receiving them for some other reason, which
points somewhere entirely different. Either answer narrows the search, and it costs one route read.

### RUN 2026-08-17. THE ANSWER IS NEITHER OF THE TWO EXPECTED ONES.

`tools/bp_apim_probe.py`, on three independent routes (0000037d, 00000379, 00000378):

    0x462  APIMGPS_Data_Nav_1  lat/lon/hemispheres              bus 0: ~240   forwarded to bus 2
    0x463  APIMGPS_Data_Nav_2  UTC, PDOP, compass, GPS fault    NOT PRESENT
    0x464  APIMGPS_Data_Nav_3  speed, heading, alt, HDOP, sats  NOT PRESENT
    0x32B  APIM_Data_FD1       light menus, stopover distance   NOT PRESENT

Controls were healthy on every run (`Traffic_RecognitnData`, `ACCDATA_3`, `Steering_Data_FD1` all
present), so the probe was working.

**THE APIM SENDS POSITION AND NOTHING ELSE.** Not silent, which would have meant a wiring fault, and
not whole, which would have pointed at the camera. One message of four, at roughly 1 Hz, and
openpilot's relay is faithfully forwarding it to the camera bus -- the `bus 130` column is
openpilot's own TX echo (`src = bus | 0x80`), so the frames ARE reaching the camera side.

**Why that is a strong lead on U0253.** `IPMA_ADAS` is a listed receiver on all three GPS messages.
What it is missing is precisely the QUALITY half of a GPS fix: `Gps_B_Falt` (the fault bit),
`GPS_Pdop`/`GPS_Hdop`/`GPS_Vdop`, `GPS_Sat_num_in_view`, `GPS_Actual_vs_Infer_pos` (is this position
measured or dead-reckoned), and the UTC time. A consumer given coordinates with no way to tell a
good fix from a stale one, and no timestamp to age it, has every reason to declare it unusable --
which is exactly what `NoNavDataAvailable` says.

So U0253 is not "the APIM is dead" and not "the camera is misconfigured about TSR". It is a
PARTIAL feed. The next question is why two of four messages are absent: an APIM configuration that
disables them, or gateway forwarding that carries 0x462 and not 0x463/0x464.

**This does not need any as-built write to test further** -- comparing against the friend's Edge, or
against any Ford with working TSR, would say whether a healthy car sends all three.

The probe is `tools/bp_apim_probe.py` and is permanent now; it was written twice before and lost
both times.

Note this needs no as-built write, no GWM change and no FORScan -- it is reading frames off a bus we
already parse.

## 7. Next steps, in order

**Rewritten 2026-08-21. Every step of the previous list was premised on something since disproved**
-- UCDS Edge definitions (impossible, 4f), the EUR 130 licence (dead, 4e), "if a writing tool becomes
available" (FORScan writes, 4e), and a diagnostic write to prove the field is writable (already
proved -- two writes persisted).

### 1. THE ONE WRITE, and measure it against a real baseline

```
706-01-01  ->  0810 A9DA B963      nibble 8: B -> A  ("Reading + GPS")
restore    ->  0810 A9DB B964
```

Rationale, limits and the forum citation are in **4k**. It may fix nothing -- `9` is Disable and the
camera is not disabled, so this field is probably not what holds the hit rate down.

**Drive it in DAYLIGHT, and drive the same roads.** Daylight is not a detail -- the one detection
happened at night, and the dashcam shows the sign was not lit until the headlights reached it, so a
night drive caps the sightline at 50-80 m and hides the quantity being measured. In daylight a sign
is visible from 200 m+, and "recognised at N metres" becomes a real number.

**The route is known-good for this**: `bp_offline_map.py` puts six limit transitions on it
(20 residential -> 25 tertiary -> 30 secondary, out and back) plus mid-block repeaters, so there is
plenty to detect. The baseline to beat, across every route pulled so far:

```
route a2   31 segments   0 detections
"latest"    6 segments   0 detections
route a7   13 segments   1 detection
           ------------------------
           50 segments   1 detection
```

Score it with `bluepilot/asbuilt/tsr_drive.py` against a locally-pulled route. Several detections per
drive means it worked. One or zero means restore.

### 2. FREE, and neither has been asked

One message to the friend, whose car runs the same strategy and calibration with TSR working:

- **His `706-04-01`.** Section 4d recorded 12 differing blocks but this one was never interpreted,
  and 4k shows the first byte is not what a search result claimed. His value is `1EFC 26C3 485D`
  against this car's `FFFC 27C3 847B` -- that difference is unexplained and it is in the block a
  working Fiesta also differs on.
- **Whether his APIM sends `0x463` / `0x464`.** Needs a comma or a bus tool, so it may not be
  answerable, but it separates "this APIM is broken" from "no CD391 APIM sends these".

### 3. THE APIM GPS DEFECT -- a separate track, do not merge it with TSR again

Measured: `0x462` arrives 3494 times a drive, `0x463` and `0x464` **zero**, and the camera raises
`U0253 Missing Message` because of it. Nav Repeater format and conformance are already correct
(4h), and Android Auto is not the cause (4j).

It is a real fault and it is **not** what stops sign reads. Conflating the two cost most of
2026-08-21. Open question: APIM or gateway. The gateway may not be written to.

**openpilot can synthesize both messages from the comma's own GPS** -- built 2026-08-21, ships on,
stands down the moment the car sends the real ones. It is not a TSR fix and must not be described
as one.

**AND IT HAD NEVER TRANSMITTED A SINGLE FRAME UNTIL 2026-08-22.** The line above used to end "and
has never been driven", which was wrong and hid the defect: it HAD been driven, three times, and
sent nothing. Measured across routes a8/a9/aa on every bus --

    0x462  src 0    905    the APIM's position message, arriving fine
    0x462  src 130  894    us forwarding it to the camera
    0x463  ---        0
    0x464  ---        0

`LateralCurvExt` owned the SubMaster and subscribed to `gpsLocationExternal` alone -- the ublox on
a comma two / panda. A 3X takes its fix from the qcom modem and publishes `gpsLocation`; his routes
carry 322 frames of it and none of the other. So `self.gps` stayed None for the life of every drive
and the carcontroller's `if gps is not None` never passed. `selfdrived` gets this right through
`get_gps_location_service(params)`; the car process hardcoded the wrong one. Both are subscribed
now, and a static test parses the `SubMaster(...)` call so it cannot regress silently.

**THIS STRENGTHENS THE SEPARATION RATHER THAN WEAKENING IT.** The one sign this camera has ever read
came with `U0253` asserted, `NoNavDataAvailable` on every frame -- and now it is known the synthesis
was silent then too. That read had no nav data from ANY source. Nav data and sign reads are
independent, which is what section 4j says and this confirms from a second direction.

**WHAT IT DOES UNLOCK: nibble 8 = `A` becomes testable for the first time.** The 2026-08-22 write to
`A` ("Reading + GPS") downgraded the camera to `LimitOutdated` precisely because the GPS half never
arrived (4n). `A` WITH GPS actually flowing is the friend's configuration and has never existed on
this car.

**AND IT IS A VARIABLE IN ANY TSR EXPERIMENT NOW, because it enables itself.** The param defaults to
`1`, so the feature starts transmitting on the next drive after the code lands -- no toggle, nothing
to remember. Before the confound-separating night drive he was told to switch "Send GPS To The
Camera" OFF, so that night stays the only change. Turn it back on afterwards.

### 4. CLOSED. Do not re-open any of these.

| | why |
|---|---|
| Buy the UCDS EXT licence | FORScan writes the block. The licence is irrelevant (4e) |
| UCDS `AsBuilt Editor (CCC)` | its model list has no CD391 at all, and the app says so by name (4f) |
| Replace the IPMA | it is a Cx camera and it read a sign (4j, 4l) |
| Replace the IPC | governs the dash only, and he does not want TSR on the cluster |
| IPMA firmware update | EU and US firmware are byte-identical (4l) |
| Change the region | he has said no twice, the DTCs were real, and `FF` is normal anyway (4k) |
| A US-market as-built | retracted -- US 2019+ builds may have had TSR removed (4l) |
| Android Auto | tested, not the cause (4j) |
| Fusion mode as a prerequisite | tested, not required (4j) |

---

## 8. What this is actually for

Not the cluster icon — he has said repeatedly he does not want it. **Speed Limit Assist needs a
camera speed limit source.** On the 2026-08-11 drive the set speed froze because the road had no map
speed limit data and nothing else was asking. A working TSR would give SLA a second source on exactly
those roads.

**And the payoff is larger than this section assumed.** It was written while the notes said the
camera read signs about 10% of the time, which made TSR look like a partial improvement. Measured
2026-08-21, `TsrVLim1MsgTxt` is the no-data sentinel on every frame — the camera contributes
NOTHING today. Enabling it is the difference between no camera source and a camera source, not
between a poor one and a good one.

See `tools/bp_tsr_check.py` for the on-device measurement side, and `CLAUDE.md` for the standing
rules — in particular that the region change has been tried twice and is not to be proposed again.

---

## 9. THERE IS A SECOND CAMERA SPEED-LIMIT MESSAGE AND THIS INVESTIGATION HAS NEVER LOOKED AT IT

Found 2026-08-23, from the route-intent branch, while chasing where the CLUSTER's speed limit comes
from.

**Everything above -- this whole document, `tsr_local.py`, `tsr_scan.py`, the carstate registration
and the `bp_tsr_check.py` measurement -- reads `Traffic_RecognitnData` (0x3CD / 973) and nothing
else.** Grepped: `IsaVLim`, `IaccVLim` and `IPMA_Data2` appear NOWHERE in this fork.

**`IPMA_Data2` (0x3D9 / 985) is transmitted by IPMA_ADAS -- the same camera -- and carries:**

    IsaVLim_D_Rq        byte 0,  8 bit   Intelligent Speed Assist speed limit
    IsaVLimUnit_D_Rq             2 bit   its unit
    IaccVLim_D_Rq       byte 2,  8 bit   Intelligent ACC speed limit
    IaccVLimUnit_D_Rq            2 bit   its unit
    TsrRegionTxt_D_Stat byte 5,  5 bit   region

A DIFFERENT MESSAGE from the one the 255-on-every-frame measurement was taken on. That measurement
is not wrong; it is simply about `Traffic_RecognitnData`, and it has been quoted throughout as though
it settled whether the camera emits a speed limit AT ALL. It does not settle that.

**THE PRIOR WRITTEN HERE WAS "IT IS PROBABLY EMPTY TOO". IT WAS MEASURED WITHIN THE HOUR AND IT IS
WRONG.** Kept rather than deleted, because it was the reasonable expectation and would be
re-derived: ISA is normally DERIVED from sign recognition, so if the camera reads no signs it should
have nothing to publish.

**Measured 2026-08-23, four routes, `IsaVLim_D_Rq` (0x3D9 byte 0) beside `TsrVLim1MsgTxt`
(0x3CD byte 3):**

    route       IsaVLim                     TsrVLim1MsgTxt
    0000039f    254 no-data   99.6%         255 no-data   99.2%
    000003a1    254 no-data  100.0%         255 no-data  100.0%
    000003ac     30 on        25.6%         (control cut off, see sweep)
    000003b6     80 on        34.8%          80 on        33.2%

**TWO DIFFERENT ROUTES, TWO DIFFERENT PLAUSIBLE LIMITS. That is not a stuck default** -- a stuck
value is the same number every time. 30 mph is an ordinary city street and 80 mph is a real Utah
freeway limit, and each appears on a quarter to a third of that route's frames rather than
constantly, which is what a camera reading signs intermittently looks like.

**SO TWO STANDING CLAIMS IN CLAUDE.md ARE STALE, NOT WRONG.** "`TsrVLim1MsgTxt` is the no-data
sentinel 255 on every frame of every recent drive" and "the camera contributes NOTHING today" were
measured on 0000039f and 000003a1, and both hold there exactly. They do not hold on 000003ac or
000003b6. **A measurement dated in this file is a measurement about the routes it was taken on**, and
this is the second time in this document that a TSR claim has had to be re-scoped that way.

**AND IT BEARS DIRECTLY ON THE "TSR 80 LEAK".** CLAUDE.md records a constant 80 mph arriving from the
CAR source in the speed-limit resolver, and the 2026-08-21 correction ruled the camera out as its
origin precisely because `TsrVLim1MsgTxt` read 255. **On 000003b6 it reads 80.** That correction was
right about its own routes and cannot be quoted as a general fact any more; the leak's origin is
open again, and the camera is back on the list.

**WHAT IS NOT YET ESTABLISHED, and it is the part that decides whether SLA can use this:** whether
those values match the roads actually driven. 80 on a Utah freeway is plausible; 80 on a residential
street is a fault wearing a plausible number. **The cross-check is correlating the value against
`mapdOut.highwayClass` and position on the same route**, which is real work and has not been done.
**MEASURED, AND THEN HANDED OFF. 3 of 12 routes carried a non-sentinel value** -- and on every
route `IsaVLim` and `TsrVLim1MsgTxt` AGREE, in value and in rate, within a point. **So 0x3D9 is
REDUNDANT with 0x3CD: the same number published twice, not a second source.** The "message nobody
had examined" is real and adds nothing, which deflates the finding and is worth saying plainly.

**AND THE 80 IS CONFIRMED BAD BY HIM, 2026-08-23** -- he is working TSR in a separate session and
already knows that value is wrong. So the camera is not reading signs after all; it is emitting a
bad number on both of its messages. **TSR IS THAT SESSION'S WORK, NOT THIS BRANCH'S.** Nothing more
here.

**~~OWED: the rate across the last twelve routes.~~ DONE, above.** Four spot checks are not a rate, and this fork
keeps having to withdraw one-route numbers. The sweep was started twice on 2026-08-23 and neither
run was retrieved -- the device dropped off the network mid-run both times.

    cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3         tools/bp_isa_speed_limit.py --sweep 12 > /data/media/0/isa_sweep.txt

**DO NOT WRITE THE OUTPUT TO `/tmp` ON THIS DEVICE.** Three sweeps were lost that way on
2026-08-23: `/tmp` is cleared on reboot, the car auto-updates and reboots roughly hourly, and a
twelve-route sweep takes minutes. The third loss was the worst kind -- the job reported `finished`
and the result file had already been deleted out from under it, which reads as "the tool produced
nothing" rather than "the disk was wiped". Write to `/data/media/0/`, which survives.

**AND THE FIRST ATTEMPT AT THAT SWEEP PRODUCED A CONFIDENT TABLE OF NONSENSE**, which is why the
sweep now lives inside the tool. It was a shell loop grepping the per-route output, and the grep
dropped the `===` section headers -- so `IsaVLimUnit`'s constant 2 ran together with `IsaVLim` and
was parsed as a speed limit, flagging 8 of 9 routes as carrying a "REAL LIMIT". Caught only by the
values looking wrong. **A tool whose output has to be re-parsed by a fragile pipeline will
eventually be re-parsed wrongly**; the aggregation belongs in the tool.

**WHY IT IS WORTH THE MEASUREMENT ANYWAY:**

- It costs nothing. Every CAN frame is already in every route on the device; this is a decode of
  logs that exist, not a drive.
- ISA and TSR may be fed differently. `IaccVLim` in particular is an ACC-facing limit, and ACC on
  this car demonstrably works.
- **A NULL RESULT IS ALSO WORTH HAVING.** "The camera emits no speed limit on either of its two
  speed-limit messages" is a stronger and more quotable statement than the current one, which
  silently covers only one of them.

**FIRST QUESTION, BEFORE ANY DECODING: is 0x3D9 even on a bus openpilot logs?** Unknown as of
2026-08-23 -- the device went off mDNS mid-check. `tools/bp_can_nav_diff.py --inventory <route>`
answers it in one run, and it prints per-byte variance, so a constant-zero `IsaVLim` shows up as
`bytes varying: []` without writing a decoder at all.

**AND THE THING THAT PROMPTED IT IS SEPARATE AND ALSO UNRESOLVED.** He reports the CLUSTER shows a
speed limit **when Waze is running and when nothing is navigating at all** -- so that number is not
coming from Android Auto. Candidates: this camera message, or Ford's own embedded navigation map
over MS-CAN. If it is the latter it is invisible until the canbox lands, and it would be a
speed-limit source that needs no app running and no camera sign read. Either answer helps SLA; they
are just answered by different measurements.

### 9a. CLOSED 2026-08-24 BY THE OTHER SESSION, AND IT RETRACTS THE INFERENCE ABOVE

The 80 is explained, and section 9's reasoning about it was wrong.

**IT WAS AN I-80 ROUTE SHIELD.** From commit `805166cd50`, decoded on his own routes: the phantom 80
on 000003b6 was the camera reading an **I-80 interstate shield near 2100 S** as a speed limit, and
it graded that read `LimitReliable` on 58% of its frames.

**SO THE ARGUMENT IN SECTION 9 DOES NOT HOLD.** It said:

> TWO DIFFERENT ROUTES, TWO DIFFERENT PLAUSIBLE LIMITS. That is not a stuck default -- a stuck value
> is the same number every time.

True as far as it goes, and it does not support the conclusion it was used for. **Two different
route shields also produce two different plausible numbers.** "Plausible" was carrying the weight
of "read from a speed limit sign", and a shield reading 80 on an interstate numbered 80 is the most
plausible-looking wrong answer available. The inference should have been "not a stuck default",
full stop -- everything after that was reaching.

**AND THE 30 IS NOT VINDICATED BY THIS EITHER WAY.** It may be the genuine sign read this document
already records at 2011 2100 S, or another shield. Section 9 does not establish which and neither
does this note.

**IsaVLim IS NOW DEFINITIVELY WORSE THAN THE SIGNAL WE ALREADY READ, not merely redundant.** The
same commit found `TsrVl1StatMsgTxt_D_Rq` -- the camera's OWN verdict on the value it is sending
(`LimitReliable` / `LimitChanged` / `LimitOutdated` / `Null`) -- parsed and thrown away, and now
gated on. `IPMA_Data2` carries no equivalent grade. So the 0x3CD path has a confidence channel and
the 0x3D9 path has none, on top of carrying the identical number.

**Nothing further is owed here from the route-intent side.** The live thread is the other session's,
and the bigger finding is theirs too: `TsrStatMsgTxt_D_Rq` reads `Available_CameraOnly` on
essentially every frame with `NoNavDataAvailable` at the same rate, so **Ford's TSR is a FUSION
system being run on the camera alone on this car** -- which is why it reads so few signs, and which
points back at the APIM nav path rather than at anything in this repo.
