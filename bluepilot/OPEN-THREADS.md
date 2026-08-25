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

**MEASURED 2026-08-24, `tools/bp_override_pairs.py`, all ten episodes on 3b5/3b7/3b8/3ba:**

    driver braked?   duration        outcome
    no               0.14 - 1.84 s   clean       5 of 5
    no               2.78 - 9.00 s   CANCELLED   3 of 3
    YES              2.30, 11.92 s   clean       2 of 2

**The two long "leadless survivors" were not survivors -- he rescued them with a brake tap.** That
is the whole reason the threshold looked soft and the lead theory looked load-bearing. A brake
press disengages cruise, so the camera's cancel then lands while cruise is OFF, and this file
already records that a cruise-off cancel clears itself. Remove those two and the boundary is
clean: **between 1.84 s and 2.78 s of override with no driver input.** A stop needs 5-8 s. A
single continuous override therefore cannot work, and that is now measured rather than estimated.

**THE PCM NEVER ENTERED ITS STOP MODE, on any of the ten.** We asserted `AccStopStat_B_Rq` on
99.9% of override frames -- the 2026-08-23 fix is live and doing its job -- and `AccStopMde_D_Rq`
read `NoStop` on 100% of frames throughout. **But that is NOT yet evidence the handshake fails**,
because no episode ever reached a standstill (they ended at 8-37 mph). `Hold` may simply be
correct-to-be-absent there. Unresolved, and it needs an override that actually completes a stop.

**INTERLEAVING IS PROBABLY DEAD TOO, for a physical reason.** It rests on the camera counting
consecutive contradicted FRAMES. But this file already establishes the camera CANNOT SEE OUR
ACCDATA -- so whatever it counts, it counts by watching the car's own deceleration. Handing Ford
back one frame in five does not change how the car is moving, so it cannot reset a counter that is
fed by motion. To reset that, the car would have to genuinely stop decelerating, which un-does the
stop. Do not build interleaving without first showing the camera counts frames rather than motion.

**THE PROPOSAL THAT FITS EVERY MEASUREMENT: end the ACC session cleanly, then stop.** The camera
latches only when it cancels a session it believes is ACTIVE and misbehaving. So rather than
contradict a live session, drop out of Ford ACC deliberately at the arming moment, author the
stop, hold, and resume afterwards.

The evidence that this is tolerated is the strongest kind available -- **it is what he already
does.** He disengages before every stop he takes himself, measured twice, across thousands of
stops, and the camera has never once latched from it. The cost is honest and small: Ford ACC is
absent for the ~6 s of the stop, instead of absent for the rest of the drive.

**Next step:** confirm from a route that a clean openpilot-initiated disengage is followed by
`ford` authority returning on re-engagement with no cancel run. Then build it.

**INSTRUMENT CAVEATS on the table above, so nobody builds on the wrong column.** The ACCDATA_3
message-text column reads identically on clean and cancelled episodes, so those bits are decoded
wrong and were ignored. The lead percentages come out ~0% on all ten, which CONTRADICTS
CLAUDE.md's "4/4 with a lead survived" on these same routes -- one of the two is wrong and this
run does not settle which. The frame counts are message counts, so ratios mean something and
absolute numbers do not.

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

**HE PULLED THE GPS LEVER -- `FordSynthesizeApimGps` reads `1` on the device as of 2026-08-24**,
against the `0` this file and CLAUDE.md both recorded. Ford's TSR is a fusion system and this car
was stuck in `Available_CameraOnly` because `0x463`/`0x464` never arrive. So the next drive is the
first one that can show `Available_FusionMode`, and `tools/bp_tsr_baseline.py` is the readout.
**Check `TsrStatMsgTxt` before reading anything into a read count** -- a change there is the whole
point, and it would make every earlier baseline number incomparable.

---

## Done recently, kept only until the next drive confirms them

- **Panda gas floor** (`ed6c0b71`, 2026-08-24) — `min_gas` -0.5 -> -2.8 behind
  `FordSafetyFlagsSP.PASSTHROUGH_LONG`. **This is safety FIRMWARE: it rebuilds and reflashes on
  the next boot, so it needs a reboot, not just a pull.** Confirm from a drive that Ford's
  sub -0.5 propulsion frames now go out unclamped (`tools/bp_accdata_bands.py`).
