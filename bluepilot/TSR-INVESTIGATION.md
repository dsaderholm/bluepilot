# TSR on the retrofit Fusion — full investigation log

Session of **2026-08-11**, in the car, several hours. Written so this can be picked up cold.

**The car is currently REVERTED and healthy.** Every IPMA change was undone, the radar fault cleared
without needing an alignment drive, and ACC works. The one change left in place is on the APIM, and
it is the one that helped.

---

## 1. Where it stands

| | state |
|---|---|
| **APIM** | TSR **enabled** (`7D0-09-02`). Write succeeded. **`U0253` cleared.** Left in place. |
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

### APIM — the one that worked

`7D0-09-02` TSR enable. Written successfully, and `U0253` went from constantly present to
*"Previously Set - Not Present"*.

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

## 5. Dead ends — tested, do not repeat

| tried | result |
|---|---|
| Use Ford nav (SYNC 3 route) instead of Waze | No change. `NoNavDataAvailable` persisted. |
| Set "TSR data source" to Camera Only | **It was already set that way.** |
| Set it to Camera + APIM | Cleared the message, then the write reverted on ignition cycle. |
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

**FORScan** cannot write these. Its profile is a 2020 Fusion; the IPC and IPMA changes are feature
enables a Fusion never had. The APIM one was allowed, which is why that single change worked.

**UCDS** is installed (`v3.0.001.023`), adapter connected via USB, SN `7E 4E 6A 9B`. **All three
licences read "Not activated"** despite having been purchased and used before. Most likely their
activation server is unreachable from the US — same reason their site stopped international sales.

UCDS matters for one specific reason: **it lets you pick the vehicle manually.** `EDGE/S-MAX 2015-`
is in its list, so the IPMA can be decoded and validated as the Edge module it actually is, instead
of through Fusion definitions. That is precisely the wall FORScan hits.

Pricing if it can be bought: EXT licence 6,000 ₽/year, ~$60-70. Their site no longer does direct
international sales.

---

## 7. Next steps, in order

1. **Ask the friend for two lines** — free, and decides whether anything else is worth doing:
   - his `706-01-01` (is his 3rd character a `5`?)
   - his `720-09-01` (is SLIF enabled?)

   If his look like a working target, the config is known and only the tool is missing. If they look
   like his own, TSR is coming from somewhere we have not identified.

2. **UCDS free tests**, no licence needed if reading is ungated:
   - select `EDGE/S-MAX 2015-`, open `AsBuilt Editor (CCC)` and `Direct Config`
   - read `706-01-01` under **Edge** definitions and compare against FORScan's Fusion decode
   - if the nonsense fields resolve into sane values, the mapping theory is confirmed outright

3. **Recover the UCDS licences** — Setup → re-activate; try a VPN if the server is geoblocked; check
   whether an older UCDS build still shows them activated.

4. **Untried, if a writing tool becomes available**, in this order:
   - IPC `720-09-01` → SLIF enabled (the dependency the camera is most likely checking)
   - IPC `720-03-01` → TSR IOD enabled
   - IPMA `706-01-01` → `0450` (TSR SLIF, IACC stays enabled)
   - IPMA `706-02-01` → `4D56` (TSRMode CameraOnlyOn) — never attempted

5. **A diagnostic write worth doing once**, to learn whether the nibble is writable at all:
   `706-01-01` → `0400` (changes IACC, not TSR). If `0` writes and `5` does not, the field is
   writable and TSR values specifically are blocked. Revert immediately after.

---

## 8. What this is actually for

Not the cluster icon — he has said repeatedly he does not want it. **Speed Limit Assist needs a
camera speed limit source.** On the 2026-08-11 drive the set speed froze because the road had no map
speed limit data and nothing else was asking. A working TSR would give SLA a second source on exactly
those roads.

See `tools/bp_tsr_check.py` for the on-device measurement side, and `CLAUDE.md` for the standing
rules — in particular that the region change has been tried twice and is not to be proposed again.
