# PSCM firmware investigation

**The firmware itself is NOT in this repo and must never be.** `dsaderholm/bluepilot` is public and
Ford's VBFs are Ford's. The scripts and the findings are ours and live here; the bytes stay in
`Nextcloud/Projects/Car/OpenPilot/` (`Firmware & Calibration/`, `Calibration Files/`, and the
extracted `cal_*.bin` beside them). `.gitignore` in this directory enforces it.

Run everything from the repo root, pointing at files in that Nextcloud folder:

    python tools/pscm_re/vbf.py "<path>/K2GC-14D007-BD.VBF" cal.bin     # extract
    python tools/pscm_re/diff.py cal_a.bin cal_b.bin                     # variant diff
    python tools/pscm_re/family.py cal.bin                               # replication strides
    python tools/pscm_re/cmp.py cal.bin f150_bdl.bin f150_edl.bin        # vs the F-150 reference

**Read the conclusion in CLAUDE.md before spending time here** -- it records that the published
F-150 reverse engineering does NOT transfer (their cal is float32, this one is u16 fixed-point), and
that the EPS-current measurement found no evidence of a configured ceiling. What follows is the
detailed record.

---

# PSCM firmware, 2026-09-05 -- what is established and what is not

## The files (already on disk from the retrofit work, no new download needed)

    Firmware & Calibration/K2GC-14D003-AH.VBF   933 KB   strategy, THIS CAR
    Firmware & Calibration/K2GC-14D005-AB.VBF   6.7 KB   secondary boot loader
    Firmware & Calibration/K2GC-14D007-BD.VBF    33 KB   ECU configuration
    Calibration Files/KK1C-14D003-AB.vbf        933 KB   SAME MODULE, other variant -- the diff partner

Confirmed against FORScan's PSCM firmware screen AND against `carParams.carFw` decoded from route
logs: strategy `K2GC-14D003-AH`, ECU address `0x730`. Two independent reads agree.

VBF header: `sw_part_type = EXE`, `description = "H1_Drive"`, one block of 954,368 bytes at load
address `0x00017000`, built 2018-03-15, strings `FORD_CD4470_450_D_00_02D0002` / `Ford_CD4_2HW18`.
CD4 is the Fusion/Edge platform family.

## What is ESTABLISHED

1. **The anchors from the published F-150 work do NOT appear here.** The documented speed axis
   `[0,10,30,50,70,90,130,250]` kph was searched as float32 (LE/BE), u16 (LE/BE, x1/x10/x100/x128/
   x256) and u8: **zero hits**. The F-150 LCA torque envelope `[0.0,0.7,1.5,2.5,3.5,4.5,5.5,6.5]`
   as float32: **not present**. Different platform, different breakpoints and encoding. Do not
   re-run these searches expecting a different answer.

2. **The calibration region is located.** Diffing the two variants: 15.379% of bytes differ across
   265 regions. The useful ones are contiguous and low-entropy:

       load 0x00064EFE   85,938 B   entropy 7.13   (mostly code-like)
       load 0x000D21AE   48,997 B   entropy 7.01   (mostly code-like)
       load 0x0007B918    8,568 B   entropy 2.64   <- DATA
       load 0x000C7FF0    9,582 B   entropy 4.83   <- DATA, 292 monotone u16 runs

3. **The table format is PAIRED 6-ENTRY LOOKUPS -- an axis followed by its outputs**, not the
   12-entry u16 the F-150 write-up describes. Examples from 0x000C7FF0:

       0x000C80DA   axis [0, 1024, 2048, 3072, 4096,  6144]   out [128, 128, 115, 64, 0, 0]
       0x000C8182   axis [0,  640, 1920, 7680, 8960, 10240]   out [ 52,  43,   0,  0, 0, 0]

   Several output tables taper to zero, which is the shape of an authority or derate curve
   (`[128,128,115,64,0,0]` / 128 = `[1.0, 1.0, 0.90, 0.50, 0, 0]`).

## What is NOT established -- do not act on any of it

**Which table is the steering authority limit. Nothing here identifies a table's ROLE.** The shapes
are suggestive and that is all. ford-pscm-re's own note applies: only ~0.5% of a Ford EPS cal is
statically attributable to a reader function, because everything routes through AUTOSAR `Rte_Prm`
pointer tables -- roles come from shape plus CROSS-VEHICLE DIFFING, and the diff available here is
between two variants of the same car, which cannot label anything.

Also unresolved, and it decides whether any of this matters: whether the rack is being *told* to
stop or is *physically* saturating. Delivery falls 0.892 -> 0.743 as command grows, and a smooth
rolloff looks the same either way from outside. `LatCtlLim_D_Stat` is dead on non-CAN-FD Fords, so
the module never reports limiting.

## The next step that would actually crack it

Obtain ford-pscm-re's F-150 firmware (that repo ships dumps) and compare STRUCTURE against this
image -- their tables have known offsets and known roles. That is the reference this analysis
lacked. Failing that, disassembly to find which function reads the candidate tables.

## Scripts

`vbf.py` (parser/extractor), `diff.py` (variant diff), `region.py` (code-vs-table characterizer),
`bell.py` (curve-shape hunt), `scan1.py` (F-150 anchor search, negative result).

    python vbf.py "K2GC-14D003-AH.VBF" pscm.bin

---

# ROUND 2, same day: the F-150 reference was obtained. IT DOES NOT TRANSFER.

Cloned `ghostdev137/ford-pscm-re` (185 MB; needs `core.longpaths` + sparse checkout, a Ghidra
project path breaks a plain Windows clone). It ships real dumps and their measured tables.

## THE CALIBRATION IS `14D007`, NOT THE STRATEGY. First-round target was wrong.

    K2GC-14D007-BD.VBF   sw_part_type = DATA   32,768 B @ load 0x000C6000   <- THE CALIBRATION
    K2GC-14D003-AH.VBF   sw_part_type = EXE    954,368 B @ load 0x00017000  <- code

The F-150's cal is likewise their `14D007`. The strategy's low-entropy table region found in round 1
(load 0x000C7FF0) sits inside the cal's address range -- i.e. cal offset 0x1FF0. Consistent.

## METHOD VALIDATED, then the answer came back negative

Their documented signatures were searched in THEIR cal first, and land exactly where documented:

    bell curve BDL [14,25,32,40,43,44,42,33,23,14,9,0]  -> 0x1660 0x25b4 0x3508 0x445c 0x53b0
    19-entry axis  [0,51,66,78,100,135,182,240,...]     -> 0x0da8 0x2c50 0x3ba4 0x4af8

So the search works. In HIS cal: **all three absent.** And the reason is architectural, not a
matter of different tuning:

    F-150 cal+0x00B8 as float32:  0.8  0.5  200  10  3.5  0.055  55  3.6  1.5  30  55  1440 ...
    HIS   cal+0x00B8 as float32:  0.0326 0.0538 0.161 0.0163 19 33.76 nan nan 7.3e-39 ...

**Their calibration is float32. His is u16 fixed-point.** No offset, axis, encoding or patch site
from that project applies to this module. Only the METHOD transfers. Do not re-derive this.

## HIS cal's own structure, mapped

- 32,768 B, u16 fixed-point little-endian.
- **Record size 0x44 (68 B)**, holding a 16-entry u16 table behind a 2-word header (`16, 0, ...`).
  Family strides observed: 0x28, 0x44, 0x88, 0xC8, 0xCC, 0xF0 (F-150's is 0xF54 -- theirs is a
  195 KB cal, his is 32 KB).
- Candidate authority-SHAPED tables (rise, peak, decay to zero):

      cal+0x220C  [140, 160, 1536, 1536, 1526, 1485, 1403, 1281, 1128, 1046, 1026, 0]
      cal+0x01FA / 0x0226 / 0x0252   three copies, stride 0x2C, each decaying to 0
      cal+0x1AE4  [908, 927, 950, 974, 1000, 1032, 1057, 1082, 1100, 1113, 1115, 0]

- **No 19-entry monotone axes anywhere.** His cal does not use that family shape.

## A SAME-ARCHITECTURE DIFF PARTNER EXISTS: `L2GC-14D007-AB.vbf`

Also 32,768 B @ 0x000C6000, also u16. Diff against his: **29.35% of bytes, 24 regions.** Beware a
4-byte layout shift between them -- many "differences" are the same table at a shifted offset and
must be aligned before reading anything into them. Genuine value differences do exist:

    cal+0x2DB8  his  [80, 400, 800, 1200, 1600, 2000, 2400, 3200, 4000, 4800]
                L2GC [80, 160, 240,  320,  400,  600,  800, 1000]        <- ~5x smaller

## STILL NOT ESTABLISHED -- and now the reason is precise

**Which table is the steering authority limit.** The reference cannot label his tables, because it
describes a different calibration architecture. Labeling now requires one of:

1. A cal from a CD4-era PSCM whose on-road behavior differs in a KNOWN way (so a diff means
   something). `L2GC` differs, but nobody knows what car it came from or how it drives.
2. **Disassembling `K2GC-14D003-AH` to find which function reads which table.** This is the real
   answer and it is a large job -- Ghidra with a V850/RH850 spec, against 954 KB with AUTOSAR
   `Rte_Prm` pointer indirection that the reference project says defeats static attribution for
   ~99.5% of the cal.

And the prior question is still open and still decides everything: whether the rack is being TOLD
to stop or is physically saturating. Nothing in the firmware answers that.

## Artifacts here

`cal_k2gc.bin` (his cal, extracted), `cal_l2gc.bin` (diff partner), plus `cmp.py` (reference-
signature comparison) and `family.py` (stride/family finder).

---

# ROUND 3: AN INSTRUMENT FOR THE "TOLD vs PHYSICAL" QUESTION -- and a first answer

The question blocking everything was whether the rack is being TOLD to stop or is physically out of
capability. It turns out the car reports something that bears on it, and openpilot throws it away.

## `SteMdule_I_Est` -- EPS motor current, in a message openpilot already parses

    EPAS_INFO (130), from the PSCM, on bus 0
    SteMdule_I_Est : 21|12@0+ (0.05,-64) [A]
    Motorola: MSB at byte2 bit5, 12 bits -> byte2[5:0] then byte3[7:2]
        raw = ((b2 & 0x3F) << 6) | (b3 >> 2);   amps = raw*0.05 - 64

openpilot reads `SteeringColumnTorque` and `EPAS_Failure` out of this message and ignores the rest.

**FIRST DECODE WAS WRONG** -- I walked to byte1 instead of byte3. In Motorola you advance to the
NEXT byte after finishing one. It produced two distinct values across 45,000 samples, which is the
tell.

**DECODER VALIDATED TWO WAYS before anything was read into it:**
- `byte0*0.0625-8` reproduces `carState.steeringTorque`, mean error **0.006 Nm**
- `byte4*0.05+6` gives **12.8-14.5 V** -- battery voltage, as the DBC says

**AND THE SIGNAL IS REAL, not a dead field like its neighbours:**
- r = **+0.55** against |driver torque|, r = +0.35 against |steering angle|
- mean **1.811 A** at |angle| > 90 deg versus **0.012 A** at |angle| < 5 deg -- a 150x swing

(Note how much of this message IS dead on this retrofit module: bytes 1, 5, 6, 7 are frozen
constants, and `DrvSte_Tq_Actl` is a fixed 128 = 0.0 Nm. Third dead signal found on this PSCM after
`LatCtlLim_D_Stat`. Check a field varies before trusting it.)

## THE MEASUREMENT: during real `steerSaturated` episodes (2026-09-04 pull, 33 episodes)

                          n        p50      p90      p99      MAX
    DURING saturation    4,710    0.10 A   0.55 A   2.00 A   5.05 A
    normal driving     275,939    0.00 A   0.05 A   0.30 A   2.00 A

**No plateau.** The top values are spread (0.65, 0.60, 0.80, 0.75, 2.00) with no single value
dominating -- a hard current clamp would pin many frames at one number. And the peak seen anywhere
is ~5 A against a signal that ranges to 140 A.

## WHAT THIS SUPPORTS, AND WHAT IT DOES NOT

**Supports:** the rack is NOT straining when openpilot reports steering exhaustion. During
saturation the motor typically draws a tenth of the current the same signal reaches elsewhere on the
same drives. A motor at its mechanical or electrical limit does not look like this. That is evidence
**against** "physically saturating" and therefore **for** "being told to stop" -- which is the
reading that keeps the firmware idea alive.

**Does NOT establish:** that a hard limit exists. A table-based authority limit is applied SMOOTHLY
as a function of speed and angle, so it would produce exactly this -- a low, continuously-varying
current with no plateau. Absence of a plateau rules out a hard CLAMP, not a shaped limit.

**Treat the absolute amps with suspicion.** ~5 A is low for an EPS motor doing real work, so the
DBC's 0.05 A/bit may not apply to this module, or the field may be a filtered estimate. The
RELATIVE comparison (saturation vs normal, high angle vs centred) is what carries the finding; the
absolute scale is not load-bearing and should not be quoted as amps to anyone.

## Next measurement, and it needs no new tooling

Bin current against commanded path angle WITHIN the saturation episodes at 29-56 mph. If current
flattens while command keeps rising, that is the shaped limit and the firmware lever is real. The
pending 29-56 mph curve drive supplies the sample.

---

# ROUND 4: SETTLED. THE LIMIT IS POLICY, NOT PHYSICS. HIS ARGUMENT, MEASURED.

*"I mean I steer manually completely and have no limits."* That is the argument that closes the
question, and the data already contained the proof. Same motor, same rack, one drive:

    EPS motor current                    n        p50    p90    p99     MAX
    HIS hands on the wheel           118,184     0.05   2.00  20.00   75.85 A
    openpilot, reporting SATURATED     4,710     0.10   0.55   2.00    5.05 A
    openpilot, normal                275,939     0.00   0.05   0.30    2.00 A

    peak ratio  15.0x        p99 ratio  10.0x

**The rack is not close to its capability when openpilot reports steering exhaustion.** It delivers
fifteen times more current when he turns the wheel himself.

## AND THIS RETIRES MY OWN SCALE CAVEAT FROM ROUND 3

Round 3 said "~5 A is low for an EPS motor, so the DBC's 0.05 A/bit may not apply -- do not quote
the absolute amps." **Withdrawn.** 75.85 A under manual steering is exactly what a real EPS motor
draws, so the scaling is correct. The reason the numbers looked too small was not a bad scale: it
was that openpilot never gets near the hardware's capability. The instrument was right and the
inference about it was wrong.

## THE ONE HONEST QUALIFICATION

Manual steering is ASSIST -- the driver supplies input torque and the motor amplifies it. LKA/LCA is
the motor acting alone. So the two modes are not asked for the same thing, and there are legitimate
engineering reasons an automated function is allowed only a fraction of assist authority (driver
override must always win; UN R79-class type approval caps ACSF authority).

**That does not weaken the conclusion, it names it.** The limit is a POLICY choice about how much of
a demonstrably capable motor the automated feature may use. Policy choices of that kind live in
calibration tables. The hardware has roughly an order of magnitude of headroom.

## WHAT THIS MEANS FOR THE FIRMWARE IDEA

The blocking question -- "is there anything to raise, or is the rack simply out of road?" -- is
ANSWERED: **there is something to raise.** That does not make the patch reachable; the cal is still
unlabelled and the F-150 reference still does not transfer (Round 2). But the reason to keep
pursuing it is no longer speculative.

**Do not re-open "maybe the rack is physically saturating."** It is measured and it is false.
