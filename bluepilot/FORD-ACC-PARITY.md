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
| *"I've never seen it coast"* | brakes asserted at −0.14 m/s²; propulsion clipped at −0.5 | **measured**, below |
| *"it tricks my transmission... third gear on the freeway"* | `AccPrpl_A_Rq` slams to the −5.0 sentinel whenever `brake_actuate` toggles, across an 0.08 m/s² hysteresis band | unmeasured |
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

## Finding 2: the transmission. NOT MEASURED.

His symptom is specific: third gear on the freeway. Two candidates, both in fields that reach the
powertrain:

- **`AccPrpl_A_Rq` oscillating to the −5.0 sentinel.** `brake_actuate_target` is −0.14 and
  `brake_actuate_release` is −0.06 — an **0.08 m/s² hysteresis band** that ordinary freeway noise
  crosses constantly. Every crossing slams the propulsion request between a real value and −5.0, at
  50 Hz, at a PCM that reads it.
- **`AccVeh_V_Trg` is received by `TCM_DSL`** — the transmission. Upstream fills it with *current
  speed* (the parameter is literally named `v_ego_kph`); this fork passes `lng.target_speed` into
  that same parameter. Those are different numbers and the transmission is listening.

Both are measurable from any op-long drive, because openpilot's own frames are on the wire beside
Ford's.

---

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
   still clamps to −0.495, so nothing transmitted moves yet. **Its compile check is still owed:**
   `ford.h` is not built by `bp_offline_test.py` and the laptop lost the device mid-task.
2. **Remove the mutual exclusion** and let `create_acc_msg` carry a propulsion request alongside the
   brake. This is the one that makes blending possible at all.
3. **Measure the transmission fields** on an existing op-long drive before touching them.
4. Only then consider `brake_actuate_target`, and only after the ABS question above is settled.

**Do not change more than one of these per drive.** He has one car, and the whole reason this branch
exists is that a previous feature was tuned against theories instead of measurements.
