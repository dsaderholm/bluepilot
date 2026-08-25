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

**AND "END THE ACC SESSION CLEANLY, THEN STOP" IS DEAD TOO. Checked 2026-08-25, before building
it.** It was proposed here the night before as the one design that fits every measurement, on the
grounds that the camera only latches when it cancels a session it believes is ACTIVE. That part is
probably still true. It does not matter, because openpilot cannot brake with the cruise off:

    ford.h:547            pcm_cruise_check(cruise_engaged)   <- from CcStat_D_Actl, the PCM's own
    longitudinal.h:3      get_longitudinal_allowed() = controls_allowed && !gas_pressed_prev
    longitudinal.h:9      accel_valid = get_longitudinal_allowed() && within-band
                          ...else the ONLY legal value is limits.inactive_accel

**Openpilot's authority to brake this car is borrowed from Ford's cruise being engaged.**
Disengaging to avoid the cancel removes exactly the permission the stop needs, and panda drops
every braking frame. That is also why he takes those stops with his foot: there is no alternative
available to him either.

### SO EVERY AVENUE IS NOW CLOSED, AND THAT IS THE FINDING

    continuous override      kills Ford ACC for the drive        measured, 3/3 beyond ~2 s
    interleaving             camera watches MOTION, not frames   handing back 1-in-5 changes nothing
    AccVeh_V_Trg             not sent to the brake controller    DBC receiver list
    disengage, then stop     no panda longitudinal authority     controls_allowed is Ford's

**Passthrough and automatic stopping look MUTUALLY EXCLUSIVE on this car.** Not "hard" -- the four
mechanisms above are independent and each is sufficient on its own to kill the feature.

**THE ONE CONFIGURATION NEVER TRIED IS PURE OP LONG WITH THE PASSTHROUGH OFF.** He has confirmed
openpilot stops this car -- *"when I use OP long fully, it does come to a complete stop"* -- and in
that mode cruise stays engaged (so `controls_allowed` holds) while the camera is out of the control
loop entirely rather than being interrupted mid-command. Every cancel on record came from a drive
with the passthrough ON. So the cancel may be a passthrough phenomenon rather than an op-long one,
and nobody has separated the two.

That is a real experiment and it is one drive: op long ON, `StockAccPassthrough` OFF, approach an
empty light. But it buys stops by giving up the thing the passthrough exists for, and he has been
plain that op long on this car is *"absolute trash"*, so it is HIS trade to make, not a default to
move. Do not switch it for him.

**Next step:** ask him whether he wants that drive, and do not build anything else here until the
answer is known -- the four rows above say there is nothing left to build on the passthrough side.

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

### THE GPS LEVER IS SPENT. IT WORKS, AND IT DOES NOT PRODUCE FUSION MODE.

Measured 2026-08-24 with `tools/bp_tsr_fusion.py`, across the param write at 19:21:13 which
routes `3b9` onward all start after. The synthesis unambiguously reaches the wire:

    route   0x463/0x464 (ours)   TsrStatMsgTxt              TsrMsgTxt
    3b8     0 / 0                Available_CameraOnly 100%  NoNavDataAvailable 100%
    3b9     0 / 0                Available_FusionMode 100%  NoInformationAllOK 100%
    3ba     893 / 893            Available_CameraOnly 96.2% + FusionMode 3.4%
    3bb     1396 / 1396          Available_CameraOnly 100%  NoNavDataAvailable 100%
    3bc     500 / 500            Available_CameraOnly 100%  NoNavDataAvailable 100%
    3bd     595 / 595            Available_CameraOnly 100%  NoNavDataAvailable 100%

**Read the 3b9 and 3bb rows together: the synthesized GPS is NEITHER NECESSARY NOR SUFFICIENT.**
3b9 reached fusion mode with none of it on the wire; 3bb/3bc/3bd had 500-1400 frames of it and
never left camera-only. That is a two-directional disproof, not a weak correlation.

**AND FUSION MODE IS REACHABLE ON THIS CAR, which is new and is the useful half.** CLAUDE.md has
said throughout that the camera is always `Available_CameraOnly` / `NoNavDataAvailable`. It is
not: `3b9` ran `Available_FusionMode` + `NoInformationAllOK` -- the fully healthy state -- for
every one of its 10 TSR frames.

**NOT A POWER-ON TRANSIENT, checked rather than assumed.** `tools/bp_tsr_startup.py` prints the
first frames of each route: `3bb` and `3bd` are `Available_CameraOnly` from t+0.18 and t+0.28,
their very first TSR frame. So the camera does not boot optimistic and decay; on those drives it
had already decided.

**WHAT IT CORRELATES WITH IS THE CLOCK, NOT THE FEATURE.** Fusion mode appears at 19:33 (`3b9`,
100%) and 19:34 (`3ba`, 3.4%, at the start), and is gone by 23:12 (`3bb`) and on every route
after. Two one-minute ignition cycles a minute apart is the signature of somebody standing at the
car -- and he wrote `FordSynthesizeApimGps` at 19:21, twelve minutes before.

**THE NEXT STEP IS A QUESTION FOR HIM, NOT A MEASUREMENT:** what was he doing at the car around
19:21-19:34 on 2026-08-24 -- FORScan, a DTC clear, an as-built write, an APIM power cycle? Fusion
mode is the thing TSR has always needed and it was briefly present in that window. Whatever
produced it is the actual lever, and no amount of log reading will name it.

**Do not re-run the GPS experiment.** It is answered in both directions. The feature itself is
fine and should stay on -- it fixes the real `U0253 Missing Message`, which CLAUDE.md is already
explicit is a SEPARATE fault from sign reading.

**And the read behaviour is unchanged:** 1 read across all six routes, `30` mph at 28 mph, which
is the same "only 30, only slow" pattern the baseline records. `TsrVl1Stat` is `LimitOutdated` on
91-100% of frames everywhere, so the reliability gate is doing real work.

---

## 5. Finish the Ford safety A/B for the widened gas floor

The panda change was verified by COMPILING it on the device -- `gcc -Wall -Wextra`, exit 0, warning
output byte-identical to the unmodified header -- which retires the "does it build" risk that
`tools/bp_offline_test.py` structurally cannot see.

**What is NOT finished is opendbc's own `test_ford.py`.** A full run against the modified header
hit its 1800 s timeout partway and showed failures, and **those cannot be attributed without a
baseline**: this fork modifies `ford.h` heavily (pinion geometry, MADS, the brake gate, the reset
latch) against a test written for upstream's version, so red there is the expected state until
proven otherwise. CLAUDE.md's rule -- compare against the merge base before treating a finding as
yours.

**AND THE FIRST A/B ATTEMPT WAS INVALID, which is the trap to avoid on the retry.** The script took
its "baseline" from `/data/openpilot/.../ford.h` on the device -- and by then the passing-assist
branch had rebased onto this one and the device had auto-pulled it, so the device's own header
ALREADY CONTAINED the change. It printed `flag present: 3` for the baseline column. **Never take a
baseline from the running device; take it from git.**

`tools/bp_ford_gas_ab.sh` now expects `/tmp/ford_base.h`, produced by:

```bash
git show ed6c0b71d7^:opendbc_repo/opendbc/safety/modes/ford.h > /tmp/ford_base.h
```

**Next step:** scp `ford_base.h` and `ford_new.h` to the device, run the script, and compare the two
columns. A difference is mine; anything red in both is pre-existing and belongs upstream, not here.

**Note `/tmp` on the device is tmpfs and a reboot clears it** -- the tree, both headers and the
result file all vanished once during this work. Rebuild with the tar one-liner in the script.

---

## Done recently, kept only until the next drive confirms them

- **Panda gas floor** (`ed6c0b71`, 2026-08-24) — `min_gas` -0.5 -> -2.8 behind
  `FordSafetyFlagsSP.PASSTHROUGH_LONG`. **This is safety FIRMWARE: it rebuilds and reflashes on
  the next boot, so it needs a reboot, not just a pull.** Confirm from a drive that Ford's
  sub -0.5 propulsion frames now go out unclamped (`tools/bp_accdata_bands.py`).
