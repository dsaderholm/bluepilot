<!-- GENERATED FILE. Edit readme/sections/ or readme/fragments/, then run
     python tools/bp_build_readme.py
     Editing this file directly will be overwritten. -->

![](selfdrive/assets/img_fusionpilot_boot.jpg)

# FusionPilot

A personal fork of [BluePilot](https://github.com/bluepilotdev/bluepilot), built for one specific
car: a **2020 Ford Fusion Titanium AWD running retrofitted Ford Edge ADAS hardware** — Edge PSCM,
steering rack, IPMA camera and CCM radar, with the Fusion's own ABS, instrument cluster and steering
column.

That combination does not exist from the factory, which is why this is a fork rather than a settings
profile. Platform `FORD_FUSION_MK5`, flags `ALT_STEER_ANGLE | TSR`, not CAN FD.

The name is the car and the fork at once: a Fusion body with Edge brains, and four upstream projects
fused into one tree.

## Status: constantly changing

**This is under active, near-daily development and is not a release.** Behavior is tuned from road
reports and log analysis, sometimes several times a week, and things that worked last month may work
differently now. Branches are rebased often, so commit hashes are not stable — check what a build
contains by looking at the code, not the hash.

There is no support, no release cadence, and no promise that any given commit is good. If you run it,
read the commit messages: they carry the measured evidence behind each change, including the ones
that were tried and reverted.

## Will this work on my car?

**If you did the same retrofit, most of it should.** The tuning here is fitted to an Edge PSCM and
rack in a CD4-platform Ford, and a Lincoln MKZ with the same swap is the same problem — same
platform, same retrofitted steering hardware, so the numbers that matter were fitted against the
component you also have rather than against a Fusion badge.

Two practical notes before you try it:

- **The fingerprint is three-quarters retrofit parts, and one part that is not.**
  `FORD_FUSION_MK5` is fingerprinted in `opendbc/sunnypilot/car/ford/fingerprints_ext.py` on four
  ECUs: the Edge PSCM (`K2GC-14D003-AH`), the CCM radar (`JX7T-14D049-AC`) and the IPMA camera
  (`KT4T-14F397-AE`) are all retrofit hardware you would also have installed, so those match. The
  fourth is the **Fusion's own ABS** (`KG9C-2D053-MD`), and yours will be a different part number.

  So expect fingerprinting not to complete on a different donor car. Either add your ABS firmware
  string to that same entry — a one-line addition, and the right fix if you want it recognized
  automatically — or select "Ford Fusion (ADAS retrofit) 2020" by hand.
- **Check the platform specs against your car.** `CarSpecs(mass=1731, wheelbase=2.85,
  steerRatio=17.07)` in `opendbc/car/ford/values.py`. Wheelbase and steer ratio should carry across
  the platform and the shared rack; mass is the one that moves, particularly on a hybrid, and it
  feeds the lateral tuning.

**On a stock Fusion, Edge or MKZ, expect it to be wrong rather than merely unnecessary.** Several
constants exist specifically to compensate for the retrofit PSCM having different steering authority
from either donor car in stock form.

What does not transfer at all:

- **Pinned holds**, which are literally GPS coordinates on one person's commute
- **Anything fitted to one driver's comfort.** The curve-speed factors were set from measured
  cornering that this driver repeatedly chose and was happy with, around 0.28-0.31 g. That is a
  preference, not a limit, and yours may differ.

## Lineage, and what still comes from where

```
openpilot (comma.ai)  →  sunnypilot  →  BluePilot  →  FusionPilot
```

**BluePilot is still upstream and updates are taken from it regularly.** This fork is a layer on top,
not a departure. The Ford lateral scheme, ICBM, Speed Limit Assist, MADS and Smart Cruise Control all
come from BluePilot and sunnypilot and are not reimplemented here.

Keeping updates easy is an explicit design constraint. Every line changed in an upstream file is a
merge conflict paid forever, so new work goes into new files where it can, hooks into upstream files
are kept to one-liners, and additions whose reason has expired are deleted rather than parked at a
neutral value. `CLAUDE.md` documents the rules that enforce this.

## What this branch adds

### Intelligent Cruise Button Management

Stock Ford ACC will not accept a longitudinal command, so ICBM — sunnypilot's actuator adapter —
translates openpilot's desired speed into cruise-button presses. **The set speed is the only lever
this car has**, and most of the work here is making that lever behave.

That constraint is worth understanding before reading anything else: the set speed falls at roughly
**3.3 mph per second**, which is the car's own repeat rate for a held cruise button. It is not a
parameter. Every feature that slows the car has to fit inside that budget, and a few requests that
sound reasonable are simply impossible because of it.

- **A button contract settled on the road.** `RES +` creates or raises a HOLD — the driver's own set
  speed — and `SET −` lowers it or, with cruise off, hands the speed back to Speed Limit Assist.
  Every other feature keeps working against a hold: curves still slow the car, hazards still fire,
  and the speed returns to the driver's number afterwards rather than to the posted limit.
- **Holds pinned to a location.** Tap the HOLD badge and that hold returns whenever you drive through
  the same place. A hold you set by hand always outranks a pinned one.
- **A standstill resume gate.** openpilot asserts resume from its own plan, which on a stock-ACC car
  is not the controller that then has to drive — Ford reads resume as "go" and brakes hard when its
  radar finds the lead still there. Resume is held until the lead has actually gone.
- **Radar-blind lead detection.** Ford's ACC follows only radar-confirmed leads, and its manual says
  plainly that it may not detect stationary vehicles below 6 mph. The driving model does see them.
  When it does and the radar has not, the set speed is taken to Ford's 20 mph floor and the driver is
  told — the deceleration itself is the reaction time, rather than a warning after the fact.
- **Stop signs and red lights**, on the same channel, for the case the lead trigger structurally
  cannot catch: an empty intersection with no vehicle to measure. Gated so it acts only once the stop
  actually requires braking, rather than while coasting would still arrive in time.
- **Rate limiters that only meter what has no deadline.** Ford coasts for small set-speed steps and
  brakes for large ones, and coasting into a lower speed limit is nicer than braking into it. But a
  curve or a mapped corner is a fixed place in the road, so those go straight to target — metering
  them spends road that was already budgeted.

### Smart Cruise Control — curves and corners

Two controllers can slow the car for a bend: SCC-Vision, from the driving model's predicted path, and
SCC-Map, from mapped corner geometry. They feed a `min()`, so either can lower the speed and neither
could historically overrule the other. Most of the work here is about that asymmetry.

- **Corner-speed factors split by the corner, not the car.** A loop ramp is a 25 mph corner entered
  at 75, and a highway sweeper is a 50 mph corner entered at 75 — identical vehicle speed, opposite
  requirements. A single factor cannot serve both, and one keyed on vehicle speed cannot tell them
  apart, so the blend is keyed on the corner's own speed.
- **A camera veto over mapped corners that are not there.** Bad map geometry used to slow the car
  with nothing able to say no. When the model looks at the road the map is describing and sees no
  bend at all, the map's request is dropped.
- **A second veto for when the camera sees a *gentler* bend than the map claims.** "The camera sees
  something" is not "the camera agrees" — the model's own predicted lateral acceleration implies a
  speed, and a map demanding far less than that is not describing the same road.
- **Both vetoes are deliberately excluded from exit ramps.** On an exit the model predicts the path it
  expects to drive, straight down the highway, so a ramp's curvature may never enter its plan until
  the car is on it. Camera silence there is blindness, not evidence — and seeing around a bend the
  camera cannot is the entire reason SCC-Map exists.
- **A curve ceiling.** While a bend is tracked the set speed follows the target down and not back up,
  because a curve target that briefly rises is noise, and chasing it costs the road needed for the
  rest of the bend. It releases once the model's own ask has recovered for long enough to be real.

### Speed limits

Banded offsets, a configurable policy and fallback, lookahead for higher limits, and a maximum set
speed that Speed Limit Assist will never exceed regardless of what is posted.

### Diagnostics

Road reports are only as good as what can be measured afterwards, and several days were lost here to
tuning the wrong controller. These read the device's own logs:

- **`tools/bp_why_slow.py`** — which source governed a drive, and what caused every slowdown
- **`tools/bp_missed_curves.py`** — the opposite question: curves taken *too fast*, and whether that
  was the camera not seeing the bend, a target that was too generous, or the driver on the pedal
- **`tools/bp_hold_history.py`** — every change to the driver's hold, and what caused each one
- **`tools/bp_curve_runaway.py`** — curve slowdowns where the camera controller chased its own output
  down instead of settling, told apart from a legitimate slowdown by whether the corner the model
  claims keeps getting *tighter* as the car slows into it
- **`tools/bp_setspeed_hunting.py`** — bursts where the set speed was raised and lowered repeatedly,
  with each source's target, since the causes look identical from the driver's seat
- **`tools/bp_sunnylink_settings_audit.py`** — settings that exist on the car's screen but cannot be
  reached from SunnyLink, which is how you configure a comma 4X in practice

### Guards for what tests cannot reach

- **`tools/bp_offline_test.py`** — the offline suite, which re-execs under the pinned Python and stubs
  the device-only modules. Bare `pytest` fails here in ways that look like environment noise.
- **`tools/bp_merge_upstream.py`** — takes a newer BluePilot end to end: tags a rollback point,
  regenerates `car_list.json` rather than merging it, prints what is ours in each conflict, and runs
  the suite.
- **Static checks** for duplicate CAN registrations that strand the car at boot, capnp fields added
  without their dataclass mirror, params declared twice, `int()` on a capnp enum, and settings that
  ship without a control to reach them.

## Settings

Settings behave **exactly as they do on stock BluePilot, sunnypilot and openpilot**: `manager.py`
writes each param's default on the first boot that knows the key, and the stored value never changes
again.

So a changed default is a **recommendation, not a change**. Every tunable control prints its shipped
default in its own description — read live, so it cannot go stale — and applying it is a deliberate
act. Nothing here decides what your settings should be.

## Installing and updating

This is installed the same way as any openpilot fork, by URL at device setup. Updating:

```bash
python tools/bp_merge_upstream.py     # pull in a newer BluePilot release
```

Then on the device:

```bash
cd /data/openpilot && git pull && sudo reboot
```

## License and attribution

openpilot is released under the MIT license by comma.ai — see `LICENSE`.

This project uses software from Haibin Wen and SUNNYPILOT LLC and is licensed under a custom license
requiring permission for use. See `LICENSE.md`.

BluePilot's Ford work — the angle-control lateral scheme in particular — is the foundation this fork
is built on.

## Safety

This is alpha-quality software for research purposes. **It is not a product.**

The driver is responsible at all times, and this remains an **SAE Level 2** system. Extra sensors and
better tuning do not change that: everything here assumes a driver watching the road and ready to
take over immediately. Several features exist precisely because the car cannot be trusted to see
everything, which is the point rather than a caveat.

Users are responsible for complying with local laws.
