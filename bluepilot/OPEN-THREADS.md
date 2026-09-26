# Open threads — icbm-manual-override-and-tuning

Live work with a named next step. **Delete an entry when it closes**; a stale list is worse than
none. Findings and their evidence go in `CLAUDE.md`, not here — this file is only "what is not
finished and what would finish it".

Last swept: 2026-08-24.

---

## 1. CLOSED: the stock ACC passthrough is deleted from this branch

Its only benefit was the stop override, and that could not work for four independent reasons -- see
the postmortem banner in `CLAUDE.md`. It was also strictly worse than leaving op long off, which
gives Ford's driving through a closed relay with no cancel risk at all.

**The code is frozen on `passthrough-archive` at `25ae8a6413`.** That branch is dead: nothing
rebases onto it and nothing is developed there. It exists so the implementation can be re-read and
the measurements re-run.

**The work that replaced it is on `ford-acc-parity` (`../bluepilot-ford`).**

**This branch is ICBM, SCC and SLA only.** Do not add longitudinal-authoring work here.

## 2. CLOSED WITH THE PASSTHROUGH: the cancel-recovery mystery

`RECOVERY BLOCKED BY THE FRAME` on route `b5` episode 1 was never explained -- attribution passed,
the bands were clean across 7,032 camera frames, every gate was satisfied, and recovery never ran.

**It cannot recur and it is not worth chasing.** The passthrough that raised it is deleted (item 1)
and `passthrough-archive` is frozen. The measurements and the postmortem stay in CLAUDE.md for the
lessons; the open question dies with the feature.

---

## 3. TSR stays quarantined behind `SpeedLimitPolicy = 1`

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

**THE "FUSION MODE IS REACHABLE" HALF WAS NOT NEW AND WAS NOT A FINDING. Retracted 2026-08-25.**
`TSR-INVESTIGATION.md` section 4n had already recorded it from THREE independent drives -- both
2026-08-22 drives and route `000003a1` -- reaching `Available_FusionMode` with `NoInformationAllOK`
and reading nothing, and it ends with "Fused mode is reachable on this car and it is not what
produces sign reads. **Do not re-open it.**"

It was re-opened anyway, written up as new, and reported to him as an open question. He answered it
from memory in one line. **This is the failure CLAUDE.md names first under "Working with the owner":
read the module before extending it, and grep for the concept before treating it as unexplored.**
The whole 7:21-7:34 PM window was likewise already answered -- 4n prescribes the exact write he made:

    706-01-01  ->  0810 A9DB B964      restore nibble 8: A -> B

**WHAT THE MEASUREMENT DOES ADD, and it is worth keeping:**

1. **The restore reached the camera.** Every route from the 2026-08-22 write onward read
   `TsrVl1StatMsgTxt = LimitOutdated` on 100% of frames -- the regression 4n documented. Route
   `3bd` (Mon 2026-08-24, 9:09 PM local / 03:09 UTC, after the restore) is the first to carry `LimitReliable` again, at
   7.2%, on the one route that read a sign. The status regression is reversed on the wire.
2. **The GPS synthesis now transmits in volume and sustains it** -- 500-1400 frames of
   `0x463`/`0x464` per drive across four drives. Section 7 step 3 records that it had never
   transmitted a single frame until 2026-08-22; that is no longer the open question it was.
3. **And it changes nothing about TSR**, which is what 4n and section 7 step 3 both already say.

**THE READ RATE IS UNCHANGED AND STILL TERRIBLE.** Post-restore: `3b9`, `3ba`, `3bb`, `3bc` zero,
`3bd` one read (30 mph at 28 mph). Against a pre-restore baseline of 3 reads in 7 routes. The
restore undid a regression; it did not buy detections.

**AND NIGHT LOOKS MORE LIKE THE FACTOR, NOT LESS. Corrected 2026-08-25 -- THE DEVICE RUNS IN UTC.**
An earlier version of this entry called `3bb`/`3bc`/`3bd` night drives at "11:12 PM, 1:20 AM,
3:09 AM" and concluded night was not sufficient. Those are UTC. Utah is UTC-6 in August, so they
are **5:12 PM, 7:20 PM and 9:09 PM** -- two of them in broad daylight. Every timestamp in this
investigation is UTC and sunset in Salt Lake City in late August is around 8 PM, so **converting is
not cosmetic here: light level is the variable under test.**

    route   local (Utah)        light    reads
    3a7     Fri 08-21 10:14 PM  dark     1     <- the 4j verified read
    3b7     Mon 08-24 09:00 AM  day      0
    3b8     Mon 08-24 01:22 PM  day      0
    3b9     Mon 08-24 01:33 PM  day      0
    3ba     Mon 08-24 01:34 PM  day      0
    3bb     Mon 08-24 05:12 PM  day      0
    3bc     Mon 08-24 07:20 PM  day      0
    3bd     Mon 08-24 09:09 PM  dark     1

**Both reads on record are after dark and every daylight drive read nothing.** That is the same
direction 4n's confound points, now with a second night read behind it rather than one.

It is still not the controlled repeat: none of these was the deliberate 4j loop, the roads differ,
and `3b5` (Sun 8:21 PM, right at dusk) read nothing. So this is corroboration, not proof.

**Next step is 4n's, unchanged: drive the 4j loop deliberately, AFTER DARK, with the nibble
restored.** `bluepilot/asbuilt/tsr_drive.py` scores it. Several detections means night was the
factor all along; zero means neither variable was, and the detection-range defect in 4j stands as
the whole explanation.

**And the as-built restore was at 1:21 PM Monday, not 7:21 PM** -- same six-hour error, and it is
what made the FusionMode window at 1:33/1:34 PM look like an evening trip to the car.

**Do not re-run the GPS experiment.** It is answered in both directions. The feature itself is
fine and should stay on -- it fixes the real `U0253 Missing Message`, which CLAUDE.md is already
explicit is a SEPARATE fault from sign reading.

**And the read behaviour is unchanged:** 1 read across all six routes, `30` mph at 28 mph, which
is the same "only 30, only slow" pattern the baseline records. `TsrVl1Stat` is `LimitOutdated` on
91-100% of frames everywhere, so the reliability gate is doing real work.

---

## 4. CLOSED ON THIS BRANCH: the Ford safety A/B for the widened gas floor

`ed6c0b71d7` widened panda's `min_gas` from -0.5 to -2.8 for the passthrough, and `a623652cf1`
reverted it with everything else. **`ford.h` here reads `.min_gas = 450` (-0.5) again**, so there is
nothing on this branch to A/B.

**The method is the part worth keeping, and `ford-acc-parity` will need it** -- that branch is where
openpilot's propulsion floor is the whole subject:

- opendbc's `test_ford.py` is RED on this fork against upstream's expectations (pinion geometry,
  MADS, the brake gate, the reset latch), so red is the expected state until a baseline proves
  otherwise. Compare against the merge base before treating a finding as yours.
- **Never take the baseline header from the running device.** The first attempt did, the device had
  already auto-pulled the change, and the baseline column printed `flag present: 3`. Take it from
  git: `git show <commit>^:opendbc_repo/opendbc/safety/modes/ford.h > /tmp/ford_base.h`.
- `/tmp` on the device is tmpfs; a reboot took the tree, both headers and the result file once.

---

## 5. Moved to `ford-acc-parity`: making op long behave like Ford ACC

The finding that replaced the passthrough -- openpilot asserts the friction brakes at -0.14 m/s^2
and clips propulsion at -0.5, while Ford ramps engine braking to -0.66 and only hands over below
-1.1, blending both across that band. Full detail and the measurement tools live on that branch now.

## 6. THE STEER-SATURATED ALERT: he reports it is fixed, and it is UNVERIFIED

2026-09-25: *"I never see those warnings anymore."* The lane gate shipped 2026-09-05 and cut 61
alerts to 24 across the 701-segment baseline, so this is the expected outcome and his report is the
primary evidence.

**IT IS NOT THE SAME CLAIM AS "THE GATE WORKS", and the distinction is one command.** Silence has
two causes: the gate suppressing alerts (working), or no saturation occurring at all
(uninformative). Route 00000427 had ZERO episodes and that was arithmetic, not a result -- 13
segments against a base rate of 61 per 701.

`tools/bp_steer_saturated.py` reports shown AND silenced separately, so the next pull settles it:
a healthy result is a real silenced count with the shown ones still landing on wide episodes. Until
then this is his observation, correctly believed, and not a measurement.

---

## 7. WHAT A HOLD IS, SETTLED 2026-08-25. Do not redesign it again.

  *"I just want to be able to override the speed when I want and it to not be remembered. Memory
  will be me editing OSM."*

  *"If the speed limit changes from my hold a lot, going back to SLA would be nice, which I think we
  do now."*

  *"Like I don't want to go 10 over in my neighborhood because I was going 10 over on the freeway."*

**That is the whole specification.** A hold is a TRANSIENT override for right now. It is not a
statement about a place, not a mood carried across a drive, and not anything that persists. When
the road should be a different speed, he fixes OSM -- the map is the memory, not the car.

**IT ALREADY BEHAVES THIS WAY and he is right that it does.** `IcbmBaselineResetDelta` defaults to
10 and `update_manual_override` clears the baseline when the SLA target moves further than that
from the one he overrode, gated on `plan_source == speedLimitAssist`. Covered by
`test_drive_scenario.py` step 6, "new zone, 55 -> 35". Nothing to build.

**His own example run through it:** freeway 65 with his `+10` high-band offset means SLA wanted 75,
so `v_target_overridden` is 75. In the neighborhood SLA wants `25 + 2` = 27. `abs(27 - 75)` is 48,
far past the delta of 10, so the hold clears and the 10-over does NOT follow him home. The threshold
has ~4x margin on exactly the transition he is worried about.

**TWO DESIGNS WERE PROPOSED AT HIM AND BOTH ARE DEAD. Do not revive either:**

- **"A hold is a MOOD"** -- capture `v_baseline - v_sla_target` and RE-APPLY the delta on a zone
  change instead of discarding it. Written up here on the strength of *"what mood am I in? am I in a
  hurry today?"*, and it is the exact opposite of what he wants: he wants the big zone change to
  hand the speed BACK to SLA. His answer was *"I don't know, man."*
- **Pinned holds** -- *"I doubt I am going to use pinned holds at all. Those were for before I knew
  about how easy it was to use OSM."* The map does per-place speeds now, which was the entire job
  pins were invented for.

**THE LESSON, because it cost several rounds:** he described a behaviour and it was answered with a
taxonomy. He does not want a richer concept of a hold; he wants the simple one to work. Check
whether the current code already does the thing before proposing a model for it.

**AND ONE THING THE DELETION SURFACED BUT DID NOT FIX: mici swallows every tap that is not the
lateral overlay.** `MiciHudRendererBP._handle_mouse_press` returns early when the overlay is off
screen and never calls `super()`, so on the comma 4 no tap reaches upstream's handler at all. That
predates the pin removal -- both branches always returned -- but with the pin branch gone the
function has one job and the gap is plain.

It is NOT fixed here, deliberately: it changes tap routing on a screen this car does not have (he
runs a 3X), it cannot be rendered or driven offline, and the big screen's own history is that
getting this ordering wrong made a gesture read as dead for days. Whoever ports more of the on-road
UI to mici should settle it then, with the screen in front of them.

**PINNED HOLDS ARE DELETED, 2026-09-25.** He said it plainly -- *"yeah delete pins, I don't want
them"* -- which is the fourth time he has told us he does not want the concept. `pinned_holds.py`,
`apply_pinned_hold`, the five `IcbmPin*`/`IcbmHoldObservations` params, the three settings controls,
the set-speed box's tap target and mici's pin dot are all gone. `pinSuggestion @7` and
`BaselineSource.pinned @4` are RETIRED IN PLACE -- an ordinal cannot be reused, and both are in
every route on the device.

It had also never once worked: `IcbmPinnedHolds` read `[]` from 2026-08-11 until the day it was
removed, so not a single pin was ever created on this car.

---

## 8. Three warts in the new hold rules, none of them fixed

Raised when he asked *"does this all make sense and is how most people would want to use it?"* --
these are consequences of the 2026-08-25 changes that nobody chose, not bugs. Watch for them before
building anything on top.

**1. PIN SUGGESTIONS WILL GET NOISIER -- CLOSED 2026-09-25 by deleting pins.** It predicted that
"every set speed is a hold" would make the observer learn from ordinary engagements rather than from
deliberate corrections, so suggestions would appear at his driveway within a week. It was never
worth a gate, because he has never wanted the feature; the whole mechanism is gone. Kept here only
so a future "we should learn where he corrects the limit" idea starts from the objection rather than
rediscovering it.

**2. SET's MEANING NOW DEPENDS ON STATE HE CANNOT SEE AT PRESS TIME.** With a live limit it hands
the speed to SLA; without one it holds the speed he pressed at. SLA coverage flickers, so the same
physical press does two different things on the same road. He sees the outcome in the box a moment
later but cannot predict it. This is the most likely source of the next *"why did it do that"*, and
there is currently no cue.

**3. CLOSED 2026-09-25 BY DELETING PINS.** The `press` vs `fallbackIdle` distinction was invisible
on screen and it MATTERED, because a pressed hold deferred to a pin and an inferred one lost to it.
With pinned holds gone nothing reads `baselineSource` to decide anything -- it is a route diagnostic
now and nothing else -- so two holds that look identical in the box behave identically.

---

## Done recently, kept only until the next drive confirms them

- **Panda gas floor** (`ed6c0b71`, 2026-08-24) — `min_gas` -0.5 -> -2.8 behind
  `FordSafetyFlagsSP.PASSTHROUGH_LONG`. **This is safety FIRMWARE: it rebuilds and reflashes on
  the next boot, so it needs a reboot, not just a pull.** Confirm from a drive that Ford's
  sub -0.5 propulsion frames now go out unclamped (`tools/bp_accdata_bands.py`).
