# Ford ACC parity — what this branch is for

**The goal, in his words:**

> *"I want complete stops at stop signs and traffic lights and the ability to go under 20mph. But,
> I also love the behavior of Ford ACC for everything else."*

> *"So we basically need to train OP long on Ford ACC?"*

**And the constraint that forces it.** Stops without a lead, and any speed under Ford's 20 mph
set-speed floor, both require authoring `ACCDATA`. `ACCDATA` is all-or-nothing by panda's
`check_relay`, so **openpilot longitudinal is the only route to either**. The stock ACC passthrough
was an attempt to dodge that and it failed for four independent reasons — see the postmortem banner
in `CLAUDE.md`, and `passthrough-archive` for the code.

So the question is not "can openpilot beat Ford's ACC". It is:

> **Can op long be made good enough that leaving it on all drive is not a downgrade?**

That is a much lower bar than "better than Ford", and it is the whole scope of this branch.

---

## His complaints, which are the specification

Each is a symptom with a suspected mechanism. None of them is vague.

| what he reports | suspected mechanism | status |
|---|---|---|
| *"I've never seen it coast"* | brakes asserted at −0.14 m/s²; propulsion clipped at −0.5 | **measured and FIXED** |
| *"it tricks my transmission... third gear on the freeway"* | two candidates | one **FIXED**, one **REFUTED** — Finding 2 |
| *"OpenPilot's ACC is ass... everyone in the community knows it"* | these constants live in `opendbc/car/ford/`, shared by every Ford | consistent, unproven |

**The community-wide part matters.** `brake_actuate_target` and `CarControllerParams.MIN_GAS` are
not specific to his retrofit. If every Ford behaves this way, a shared constant is exactly the shape
of cause to expect — and nobody appears to have checked them against what Ford itself does.

---

## Finding 1: the deceleration hierarchy. MEASURED.

The control literature is explicit that a vehicle should decelerate **first** with engine drag, wind
and rolling resistance, and apply friction brakes only when that cannot meet the demand. There is a
measured comfort reason: engine braking produces roughly **0.51 m/s³** of jerk against **>1.35** for
aggressive regen, and jerk rather than deceleration is what reads as abrupt.

**Ford does exactly that.** `tools/bp_ford_decel_hierarchy.py`, routes 3bb/3bc/3bd/3be, 143,745
frames of Ford ACC driving above 2 m/s with the driver's foot off the brake:

    Ford commanded   Ford's own AccPrpl_A_Rq
        -0.1              +0.11
        -0.2              -0.09
        -0.3              -0.17
        -0.5              -0.34
        -0.8              -0.66
      below -1.1          sentinel -- no propulsion request at all

Ford ramps engine braking to about **−0.66 m/s²** as deceleration builds toward −0.8, then hands
over completely to the friction brakes below −1.1. **It blends both across that whole band.**

**openpilot cannot do any of that**, and it is three things in `longitudinal_ext.py`:

    op_brake_actuate = True below -0.14 m/s^2          (brake_actuate_target)
    if brake_actuate: gas = INACTIVE_GAS               (mutual exclusion)
    gas clipped to CarControllerParams.MIN_GAS = -0.5

**The mutual exclusion is the real defect**, not the threshold. The instant openpilot touches the
brake at −0.14, its propulsion request becomes "not requesting". It can never be in the state the
table above describes — asking the powertrain for −0.34 *while* using the brakes — at any threshold
value. Fixing this is not a better number; it is removing an `if`.

### Two caveats on the measurement, both his

- **The brake LAMP is not an actuator signal.** UN R13-H triggers stop lamps above 1.3 m/s² and
  extinguishes below 0.7, and describes 0.7 as *"representative of the natural deceleration due to
  conventional engine/gearbox association"*. So a lamp column measures regulatory magnitude, not
  which actuator is working. **The propulsion column above is Ford's own command and is not
  confounded; the lamp column is.** He spotted this.
- **An earlier bit-based run was contaminated** and produced "Ford never uses friction brakes until
  −3.0" and a "2.96 m/s² gap". Both are retracted. That tool did not filter on cruise being engaged
  or the car moving, which is why it showed 30% braking at *positive* acceleration.

### What is still unknown here

`AccBrkTot_A_Rq` goes to `ABS_ESC` directly. Whether the brake *bits* gate the ABS or merely
prepare it is not established — if the ABS acts on the value regardless, then moving
`brake_actuate_target` alone would not stop the friction brakes engaging. **Settle that before
changing the threshold.** Removing the mutual exclusion and widening the propulsion floor do not
depend on it.

---

## Finding 2: the transmission. HALF MEASURED, HALF REFUTED, ONE HALF FIXED.

His symptom is specific: *"It tricks my transmission all the time, so I sometimes go into third gear
on the freeway."* Two candidates were named, both in fields the powertrain receives. They have now
been measured against Ford's own frames on the same drives, and they came out opposite ways.

### 2a. `AccVeh_V_Trg` carried a constant 145 kph. MEASURED, AND FIXED.

`TCM_DSL` -- the transmission -- is a listed receiver of that field, alongside the PCM and ECM.
Upstream names the parameter that fills it `v_ego_kph`, which says what belongs in it.
`carcontroller.py` passed `V_CRUISE_MAX`, so **every openpilot-authored frame told the transmission
the car wanted 145 kph (90 mph)**, at any speed, forever.

    route 000003bd, bus 0 ACCDATA        openpilot          Ford
      most common value                   145.0 kph 44.0%    0.5 kph 19.3%
      distinct values                     113                113
      mean AccVeh_V_Trg - vEgo (3 routes) +32.8 kph          +4.3 kph

**44.0% is exactly the share openpilot authored** -- the remaining frames on that bus are Ford's,
forwarded. Ford's own value tracks the car; openpilot's did not vary at all. A transmission told the
car wants to be 20 mph faster than it is has an obvious response, and it is a downshift.

**Fixed:** `target_speed = CS.out.vEgo * 3.6`. Three tests parse the assignment, the conversion and
the now-removed import.

**IT IS UPSTREAM'S BUG, NOT THIS FORK'S.** `upstream/bp-7.0` carries the same line and it arrived in
`d3434d4c2c` "sync long logic with bp-6.0-wip". So it plausibly affects every Ford running BluePilot
op long, which is consistent with *"everyone in the community knows it"* -- fixed here anyway,
because it changes what HIS car does, and it should be reported upstream.

**NOT PROVEN to be the downshift cause.** It is one measured discrepancy in a field the transmission
receives. The drive that tests it is the next op-long drive.

### 2b. The `AccPrpl_A_Rq` sentinel square wave. REFUTED.

The theory was that `brake_actuate_target` (-0.14) and `brake_actuate_release` (-0.06) form an
0.08 m/s^2 hysteresis band that freeway noise crosses constantly, slamming the propulsion request to
the -5.0 sentinel at 50 Hz. Measured across three routes:

                  frames     sentinel   flips/min   propulsion range
      openpilot   135,008     56.1%       14.0       -0.50 .. +2.00
      Ford        136,027     60.5%        9.6       -1.59 .. +2.26

**Similar rates, not a square wave.** Ford sits on the sentinel MORE often than openpilot does, and
flips only 31% less. Whatever the mutual exclusion costs -- and Finding 1 says it costs coasting --
it is not producing an oscillation Ford does not also produce. Do not re-open this one.

**What the same table DOES show is Finding 1 again, from the other side:** openpilot's propulsion
request stops dead at -0.50 while Ford's reaches -1.59. That is the clipped floor, visible in the
raw distribution.

## How to do the fitting, and the two rules that keep it honest

No ML is required. **The labelled data already exists**: every drive carries Ford's ACCDATA on bus 2,
a recording of Ford's controller responding to real traffic, alongside the full vehicle state. This
is a fitting problem over a handful of constants.

1. **Fit only where both are doing the same job** — steady cruise and lead-following. Ford does not
   stop for lights, does not slow for mapped corners, and knows nothing about the radar-blind lead
   path. Fitting across those frames would delete the reasons openpilot is here at all. Exclude any
   frame whose `plan_source` is `sccMap` / `sccVision` / `modelStop` / `unconfirmedLead`.
2. **Score brake-actuation disagreement, not accel error.** What he feels is the pedal arriving
   where Ford would have coasted. Mean accel error would rate a controller that brakes constantly
   but gently as excellent — which is the exact thing he dislikes.

---

## Tools on this branch

    tools/bp_ford_decel_hierarchy.py   the lamp and Ford's propulsion request per accel bucket
    tools/bp_ford_brake_curve.py       Ford's brake-bit assertion vs commanded accel (see caveat)
    tools/bp_accdata_bands.py          Ford's own ACCDATA against panda's bands

`carStateBP.brakeLightStatus` carries `accAccelRequest`, `accPropulsionRequest`, `accDecelRequest`
and `accPrechargeRequest` straight off the camera. **That is the reference channel** and it survived
the passthrough deletion precisely because it is independent of it.

---

## Next steps, in order

1. ~~**Re-gate the panda gas band on op long.**~~ **DONE.** `FordSafetyFlagsSP.WIDE_PROPULSION_BAND`,
   gated on `CP.openpilotLongitudinalControl`. Deliberately an ENVELOPE only — `create_acc_msg`
   still clamps to −0.495, so nothing transmitted moves yet. **Compile-verified on the device**
   (`gcc -fsyntax-only -Wall -Wextra`, exit 0, warning output byte-identical to the unmodified
   header) — `bp_offline_test.py` never builds `ford.h`, and the new test only parses its `#define`s
   as text, so this check has to be run by hand every time that file changes.
2. ~~**Remove the mutual exclusion**~~ **DONE.** `ford_propulsion_request()` follows Ford's measured
   curve and hands over below −1.1, gated on `FordPropulsionBlend` (ON). This is the one that
   changes how the car drives. **It also exposed that step 1 was not purely an envelope after all**
   — `fordcan_ext._PANDA_GAS_MIN` still held −0.5, so the blend's −0.66 went out as −0.490 until a
   wire-level test decoded a real frame and said so.
3. ~~**Measure the transmission fields**~~ **DONE.** `AccVeh_V_Trg` was a constant and is fixed;
   the sentinel square wave is refuted. See Finding 2.
4. Only then consider `brake_actuate_target`, and only after the ABS question above is settled.

---

## The flashing order, because two behaviour changes are now committed

Steps 2 and 3 both change what goes on the wire, and *"do not change more than one of these per
drive"* is about what he FLASHES, not about what is committed — this branch is not the one his 3X
tracks, so nothing here has reached the car.

    flash 949f7b4e20   AccVeh_V_Trg only     does the third-gear symptom stop?
    then   4a412d2119  the blend             does it coast? does it slow MORE than asked?

Taking both at once is still readable if he only cares about the second question, because the
transmission fix cannot make the car coast. It is not readable the other way round.

**And each has its own off switch**, which is what makes a single drive able to answer either:
`FordPropulsionBlend` for the blend, and for `AccVeh_V_Trg` nothing — that one is a plain bug fix
with no plausible reason to want the old value back.

**Do not change more than one of these per drive.** He has one car, and the whole reason this branch
exists is that a previous feature was tuned against theories instead of measurements.
