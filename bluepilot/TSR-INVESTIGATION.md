# TSR on the retrofit Fusion — full investigation log

Session of **2026-08-11**, in the car, several hours. Written so this can be picked up cold.

**The car is currently REVERTED and healthy.** Every IPMA change was undone, the radar fault cleared
without needing an alignment drive, and ACC works. The one change left in place is on the APIM, and
**it did not help** -- see below.

**RE-OPENED 2026-08-21: THE CAMERA IS NOT READING SIGNS AT ALL.** Measured on routes 0000039f and
000003a1 by decoding `Traffic_RecognitnData` (0x3CD) off bus 2: **`TsrVLim1MsgTxt` is 255 -- the
no-data sentinel -- on every frame of both drives.** 000003a1 carries just TWO distinct payloads
across 909 frames, differing only in `TsrMsgTxt` and `TsrStatMsgTxt`, which are status enumerants.

This contradicts the note that had been carried forward as settled -- "the camera reads signs anyway,
what the region gates is the STATUS enumerants, not the detection" -- and that note is why this was
treated as a display problem not worth chasing. **It is not a display problem. There is no detection
happening.** He said so directly: *"the signs it's reading are wrong. Those aren't actually signs."*

So the as-built is the LIVE question, not a closed one, and section 4d's targeted write -- `706-01-01`
to `0810 A9DA A953` -- is still the sharpest untried experiment. Its blocker was never knowing the
value; it is write access (section 6).

**`U0253` IS UNRESOLVED, AND IT IS PROBABLY NOT IN THIS PATH. Demoted 2026-08-21 -- it led this
document as "THE BLOCKER" and that was framing, not measurement.**

The fault is the IPMA failing to reach the **APIM**, which is the nav module. That explains missing
nav-sourced limits and the `NoNavDataAvailable` message the camera threw -- which is what was being
chased on 2026-08-11, and why it ended up as the headline.

**But sign reading does not need nav.** That is the entire meaning of the camera's "TSR data source:
Camera Only" mode, which is what this camera is set to: it reads a sign with its own optics, and nav
is a second source to fuse, not a prerequisite. So `U0253` does not explain `TsrVLim1MsgTxt` sitting
at the no-data sentinel on every frame. That is the camera not DETECTING, which is upstream of any
fusion. He put it plainly: *"Why do we care about U0253?"*

What keeps it on the page at all, and no more than that: the camera rejects TSR configuration with
`U2101 Configuration Incompatible`, instantly, and if part of what it validates at startup is
"do I have the data sources my configuration claims", a missing APIM link could be one of the checks
it fails. **That is a plausible mechanism, not a measured one.** Do not treat it as established, and
do not let it gate the as-built experiment in section 7.

Also worth recording about the fault itself: it was called fixed on 2026-08-11 because a read came
back "Previously Set - Not Present at Time of Request". That means not present at that instant, not
resolved -- the same read said "Test not complete" -- and he said repeatedly that it keeps coming
back.

**TWO OF TWO load-bearing claims in this file have now turned out to be framing rather than
measurement** -- this one, and "the camera reads signs anyway". Treat anything else here that leads
with emphasis and no numbers the same way.

---

## 1. Where it stands

| | state |
|---|---|
| **APIM** | TSR **enabled** (`7D0-09-02`). Write succeeded. **`U0253` STILL RECURS -- this fixed nothing.** |
| **IPC** | SLIF **disabled**. Write refused by FORScan. Reverted. |
| **IPMA** | TSR **off** at `706-01-01`. Write refused by FORScan. Reverted. |
| **comma** | still reports `NoNavDataAvailable`; SLA has no camera speed limit source |

**The blocker is the tool, not the car.** FORScan accepted the APIM change and refuses the IPC and
IPMA ones with *"Writing blocks failed - incompatible configuration"*, regardless of value or
checksum. Its vehicle profile is a 2020 Fusion, and a Fusion never had TSR.

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

### IT PERSISTED ACROSS AN IGNITION CYCLE

**Confirmed 2026-08-21.** `706-01-01` = `0810 A9DB B964` survived ignition off and on.

**This is the first change in the entire investigation that has ever held.** *Camera + APIM* landed
and reverted; `0450` was never sent; the IPC SLIF change was refused. Nibble 2 wrote, was accepted,
and stayed. Mode 2 passed.

**And it suggests why 4b failed.** Section 4b guessed the revert was "the module accepting into
working memory and then failing its own configuration validation at startup". If nibble 2 of
`706-01-01` is part of what that validation checks, then *Camera + APIM* reverted **because
`706-01-01` was still `4`** — the module rejecting a data-source setting inconsistent with its own
feature configuration.

That is directly testable on the block that failed before: write **`706-02-01` -> `FD56 16DB 7FD3`**,
key cycle, re-read. **If it holds now where it reverted in August, the two fields are interlocked and
that was the whole problem.**

**What is NOT yet proven.** A value persisting is not TSR working. The measurement is
`TsrVLim1MsgTxt` in `Traffic_RecognitnData` (`0x3CD`): **255 is the no-data sentinel** and it has been
255 on every frame ever captured on this car. Anything else means the camera is genuinely reading a
sign. That needs a drive past a posted limit; it cannot be settled in the driveway.

Restore value, exact: **`706-01-01` -> `0410 A9DB B960`**.

Bisect candidates, all checksum-valid, from `bluepilot/asbuilt/asbuilt.py`:

```
0810 A9DB B964   nibble 2 only            <- WRITTEN 2026-08-21, accepted
0410 A9DA A94F   nibbles 8-9 only
0810 A9DA A953   full copy of the friend's block
0400 A9DB B950   diagnostic: IACC not TSR
```

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
706-04-01   FFFC 26C3 847A   1EFC 26C3 485D   1, 2, 9, 10
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

0. ~~Run the APIM bus probe.~~ **DONE 2026-08-17 -- see above. The APIM sends position only.**
   The follow-up is to find out why 0x463 and 0x464 are absent: APIM configuration, or gateway
   forwarding. Reading the friend's car for the same three addresses would settle which, and costs
   him one route.

1. ~~**Ask the friend for his `706-01-01`.**~~ **DONE 2026-08-12, section 4d.** `0810 A9DA A953`.
   Nibble 3 is `1` on BOTH cars, so the reference map's "TSR enable" position is wrong and the answer
   is not a single documented bit. **His `720-09-01` (IPC SLIF) is still unasked** and still free.

2. **UCDS free tests. This decides whether €130 is needed at all — do it before paying:**
   - select `EDGE/S-MAX 2015-`, open `AsBuilt Editor (CCC)`
   - read `706-01-01` under **Edge** definitions and compare against FORScan's Fusion decode. If the
     nonsense fields resolve into sane values the mapping theory is confirmed outright — and that is
     also the explanation for FORScan's refusal, since it decodes an Edge module through a Fusion
     profile.
   - **then see whether it offers to WRITE.** Only `Direct Config` is named as an extended-licence
     feature. If the AsBuilt Editor writes without EXT, the expired licence never mattered here.

2a. **FREE AND STILL UNASKED — do these before spending anything.** Both are one message to the
   friend. Neither needs the car, the adapter, or the licence:
   - **What is his TSR data source set to?** Section 4b is the only positive result in this whole
     document: setting *Camera + APIM* cleared `NoNavDataAvailable` immediately, then reverted at the
     next boot. If his reads *Camera + APIM* and holds, that is the exact target state.
   - **His `720-09-01`** (IPC SLIF), open since 2026-08-12.
   - His `0x463` / `0x464` presence, from step 0.

**WHAT €130 ACTUALLY BUYS — read this before paying.** Section 4b records two distinct failure
modes, and the licence only addresses one of them:

| mode | what happens | does EXT help? |
|---|---|---|
| **FORScan refuses** | error dialog, nothing reaches the car, no DTC | **yes** — a tool with Edge definitions would send it |
| **Module accepts, then reverts** | write lands, behavior changes, power cycle undoes it | **no** — this is the module validating its own config at startup |

The known-working change (*Camera + APIM*) already got past mode 1 and still died at mode 2. So
€130 buys the ability to *attempt* `0810 A9DA A953`; it does not buy persistence. That is still worth
knowing — it is the only way to move the question from mode 1 to mode 2 — but do not expect the
purchase to end this on its own.

3. **Renew the UCDS EXT licence, €130** — `UCDS V5 EXT License (12 Months)`, autodiagnosticsolutions
   .com, the seller from order #7238. **Only if step 2 shows the editor refusing to write.** This is
   NOT a VPN or re-activation problem: the licence expired 4 February 2026, twelve months from
   purchase. Do not buy ODO or PATS.

4. **Untried, if a writing tool becomes available.** THE TARGET LIST CHANGED on 2026-08-12 and this
   step listed dead values until 2026-08-21:
   - **IPMA `706-01-01` → `0810 A9DA A953`** — the friend's ACTUAL value on a same-strategy,
     same-calibration camera where TSR works. This is the experiment. Differences from his own are
     nibble 2 (`4`→`8`) and nibbles 8-9 (`B`→`A`). Section 4's restore point in hand, and expect the
     radar calibration to be disturbed and to come back on revert (section 5).
   - IPC `720-09-01` → SLIF enabled, only if the above does not hold across a boot.
   - ~~IPMA `706-01-01` → `0450`~~ **DEAD.** Nibble 3 is `1` on the working car too.
   - ~~IPMA `706-02-01` → `4D56`~~ **DEAD.** `FD` on the working car too.

5. **A diagnostic write worth doing once**, to learn whether the nibble is writable at all:
   `706-01-01` → `0400` (changes IACC, not TSR). If `0` writes and `5` does not, the field is
   writable and TSR values specifically are blocked. Revert immediately after.

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
