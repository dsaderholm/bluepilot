# Open threads — icbm-manual-override-and-tuning

Live work with a named next step. **Delete an entry when it closes**; a stale list is worse than
none. Findings and their evidence go in `CLAUDE.md`, not here — this file is only "what is not
finished and what would finish it".

Last swept: 2026-08-24.

---

## 1. Passthrough stopping — the feature this branch exists for

**State:** the stop override works and costs him Ford ACC for the rest of the drive, 4 times out
of 5. The camera's tolerance is bracketed between **1.1 s and 2.6 s** of continuous override; a
stop needs 5–8 s. So a single continuous override can never work.

**Ruled out by measurement** (do not re-derive): contradiction magnitude; accumulated
disagreement; arm speed (`ARM_MIN_SPEED` twice); dropped TX frames; radar health;
cancel-and-re-engage; disengaging; a MAIN press; waiting for the camera to relent; the camera
seeing our frame.

**Ruled out structurally, 2026-08-24 — `AccVeh_V_Trg` is NOT a way to make Ford brake itself.**
The DBC receiver lists split ACCDATA cleanly:

    braking     -> ABS_ESC    AccBrkTot_A_Rq, AccBrkPrchg/Decel/Pulse, AccStopStat_B_Rq
    propulsion  -> PCM/ECM/TCM   AccPrpl_A_Rq, AccPrpl_A_Pred, AccVeh_V_Trg

`AccVeh_V_Trg` is not sent to the brake controller at all, so lowering it can close the throttle
and downshift but **cannot stop the car**. A stop requires authoring `AccBrkTot_A_Rq`, which is
taking authority, which is what the camera cancels. No drive needed to close this.

**Next step:** `tools/bp_override_pairs.py` (written 2026-08-24) dumps every `opStop` episode with
a wide feature set. Route `3b5` carries the experiment: **two leadless episodes, 9.0 s CANCELLED
and 11.9 s SURVIVED, on the same drive.** Whatever differs there is the mechanism. The leading
candidate is the PCM stop protocol — `AccStopMde_D_Rq` is received by `IPMA_ADAS`, so the camera
watches it, and the 2026-08-23 fix (`lng.stopping or override` feeding `AccStopStat_B_Rq`) has
never been validated on a drive.

**The untried lever if that comes up empty:** INTERLEAVING — bursts under ~1.0 s with Ford's own
frame handed back between them, which separates "consecutive contradiction" from "cumulative".
Its known cost is a lumpier stop, because a leadless Ford asks for *positive* accel on the frames
it gets back.

---

## 2. `RECOVERY BLOCKED BY THE FRAME` — episode 1 is still unexplained

Route `b5` episode 1: attribution passed, bands clean across all 7,032 camera frames, every gate
satisfied, and recovery never ran and logged nothing. The silent refusal inside the recovery body
now logs. **Next step:** read that line off the next drive that has an override.

Likely one of the unpoliced bits (`AccDeny_B_Rq`, park brake, `CmbbDeny_B_Actl`).

---

## 3. The model-stop path arms ~12 s late

Route `3bb`: the gate was satisfied immediately at t+138.0 and did not arm until t+150, which is
most of the braking distance. Cause is the arming accumulator zeroing on any single false frame
while `dec.hasSlowDown` chatters across its threshold by design.

**The obvious fix is wrong and was reverted** — a blanket gap tolerance took that file from 60
passing to 11 failing, because `model_candidate` is an AND of the chattering flag and a physics
term, so tolerating gaps arms on stops reachable by coasting.

**Next step:** debounce the FLAG term alone, *before* it is ANDed with `a_required >= min_decel`.
That is a change to what `model_candidate` is made of, not to how its result is accumulated.

---

## 4. TSR stays quarantined behind `SpeedLimitPolicy = 1`

The camera does read signs, but on the whole recorded baseline it has **only ever returned 30 mph,
and only below 35 mph**. Those two are confounded — in this city a 30 road *is* a slow road.

**What separates them, neither yet observed:**
- a **25** read kills "only 30" and leaves range standing
- a read on a **45+** road kills "only slow" and leaves the sign set standing

Both are one-line reads of `tools/bp_tsr_baseline.py`. The bar for leaving quarantine is several
drives of readings that are CORRECT, not merely present — a confident wrong read is worse than no
read, and the phantom 80 walked the set speed to 90 for 13 minutes.

**Also open:** `TsrVl1StatMsgTxt` gating is in, but the phantom 80 was graded `LimitReliable` on
58% of its frames, so the gate would NOT have stopped it. Corroborating the car source against the
map source is a resolver change nobody has made.

**Unpulled lever, and it is HIS to pull:** `FordSynthesizeApimGps` is `0` on the device against a
`"1"` shipped default. Ford's TSR is a fusion system and this car runs `Available_CameraOnly`
because `0x463`/`0x464` never arrive. Toggle is **"Send GPS To The Camera"**. Not flipped — his
settings are his.

---

## 5. Confirm `SpeedLimitPolicy` is still `1`

It silently reverted `1 -> 4` between routes `b5` and `b6`, and combined with the phantom 80 that
is the chain that put him into an exit at 57 mph unwinding from 90. Read the param, and read its
mtime against the route times before attributing anything.

---

## Done recently, kept only until the next drive confirms them

- **Panda gas floor** (`ed6c0b71`, 2026-08-24) — `min_gas` -0.5 -> -2.8 behind
  `FordSafetyFlagsSP.PASSTHROUGH_LONG`. **This is safety FIRMWARE: it rebuilds and reflashes on
  the next boot, so it needs a reboot, not just a pull.** Confirm from a drive that Ford's
  sub -0.5 propulsion frames now go out unclamped (`tools/bp_accdata_bands.py`).
