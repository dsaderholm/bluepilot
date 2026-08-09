![](selfdrive/assets/img_bluepilot_boot.jpg)

# FusionPilot

A personal fork of [BluePilot](https://github.com/bluepilotdev/bluepilot), for one specific car:
a **2020 Ford Fusion Titanium AWD running retrofitted Ford Edge ADAS hardware** — Edge PSCM, rack,
IPMA camera and CCM radar, with the Fusion's own ABS, instrument cluster and steering column.

That combination does not exist from the factory, which is the whole reason this fork exists rather
than being a settings profile. Platform `FORD_FUSION_MK5`, flags `ALT_STEER_ANGLE | TSR`, not CAN FD.

The name is the car and the fork at once: a Fusion body with Edge brains, and four upstream projects
fused into one tree.

## Lineage, and what still comes from where

```
openpilot (comma.ai)  →  sunnypilot  →  BluePilot  →  FusionPilot
```

**BluePilot is still upstream and updates are still taken from it regularly.** This fork is a layer
on top, not a departure. Everything below is what this layer adds — the Ford lateral scheme, ICBM,
Speed Limit Assist, MADS and Smart Cruise Control all come from BluePilot and sunnypilot, and are
not reimplemented here.

Keeping updates easy is an explicit design constraint. Every line this fork changes in an upstream
file is a merge conflict paid forever, so new work goes into new files wherever it can, hooks into
upstream files are kept to one-liners, and additions whose reason has expired get deleted rather
than parked. See `CLAUDE.md` for the rules that enforce that.

## What this fork adds

### Intelligent Cruise Button Management — tuning and repair

ICBM is sunnypilot's actuator adapter: stock Ford ACC will not accept a longitudinal command, so
openpilot's desired speed is translated into cruise-button presses. The set speed is the only lever
this car has, and most of the work here is making it behave.

- **A button contract settled on the road.** `RES +` creates or raises a HOLD — the driver's own set
  speed — and `SET −` lowers it or, with cruise off, hands the speed back to Speed Limit Assist.
  Every other feature keeps working against a hold: curves still slow the car, hazards still fire,
  and the speed returns to the driver's number rather than the posted limit.
- **Holds pinned to a location.** Tap the HOLD badge and that hold returns every time you drive
  through the same spot. For the places that need the same correction on every trip.
- **A standstill resume gate.** openpilot asserts resume from its own plan, which on a stock-ACC car
  is not the controller that then has to drive — Ford reads resume as "go" and brakes hard when its
  radar finds the lead still there. Resume is held until the lead has actually gone.
- **Radar-blind lead detection.** Ford's ACC follows only radar-confirmed leads and its manual says
  plainly that it may not detect stationary vehicles below 6 mph. The driving model sees them. When
  it does and the radar has not, the set speed is brought to Ford's 20 mph floor and the driver is
  told, with the deceleration as reaction time rather than a warning after the fact.
- **Stop signs and red lights.** The same channel, for the case the lead trigger structurally cannot
  catch: an empty intersection, where there is no vehicle to measure. Gated so it acts only once the
  stop actually requires braking rather than while coasting would still arrive in time.
- **A drop limiter that only meters what has no deadline.** Ford coasts for small set-speed steps
  and brakes for large ones, and coasting into a new speed limit is nicer than braking into it. But
  a curve or a mapped corner is a fixed place in the road, so those go straight to target — metering
  them spends road that was already budgeted.

### Smart Cruise Control — curves and exits

- **Freeway exits.** `SmartCruiseControlMapDecel` is a trigger distance, not a rate: the map
  publishes the corner speed at exactly the moment braking must begin. Anything that re-paces it
  downstream misses the corner, which is what the drop limiter used to do.
- **`SmartCruiseControlMapFactor`**, new here — the magnitude control mapped corners never had.
  Mapped targets follow the posted yellow advisory, which assumes factory steering; this car's
  retrofit PSCM may want less.
- **A curve ceiling.** While a bend is being tracked the set speed follows the target *down* and
  never back up. A curve target that briefly rises is noise, and chasing it costs the road needed
  for the rest of the bend.

### Speed limits

Banded offsets, a configurable policy and fallback, lookahead for higher limits, and a maximum set
speed that Speed Limit Assist will never exceed regardless of what is posted.

### Passing assist

Suggests and manages lane changes on multi-lane roads: adjacent-lane occupancy from the front radar,
an oncoming-traffic veto that tells a divided highway from an undivided one, keep-right prompting,
lead-braking holds, two-way-road strictness, and a per-drive history so the behavior can be reviewed
afterwards rather than argued about from memory.

### Radar detector integration

Reads a radar detector over USB and slows for alerts, with a per-speed-band threshold fitted against
what the car can actually shed, a false-alarm mute, and a minimum bar count.

### Diagnostics and guards

- **`tools/bp_offline_test.py`** — the offline suite, which re-execs under the pinned Python and
  stubs the device-only leaves. Bare `pytest` fails here in ways that look like environment noise.
- **`tools/bp_merge_upstream.py`** — takes a newer BluePilot end to end: tags a rollback point,
  regenerates `car_list.json` instead of merging it, prints what is ours in each conflict, and runs
  the suite.
- **Route diagnostics** for exits, stops and controls mismatches, which read the device's own logs.
- **Static guards for what tests cannot reach**: duplicate CAN registrations that strand the car at
  boot, capnp fields added without their dataclass mirror, params declared twice, `int()` on a capnp
  enum, and settings that ship without a control.

## Settings

Settings behave **exactly as they do on stock BluePilot, sunnypilot and openpilot**: `manager.py`
writes each param's default on the first boot that knows the key, and the stored value never changes
again.

So a changed default is a **recommendation**, not a change. Every tunable control prints its shipped
default in its own description — read live, so it cannot go stale — and applying it is a deliberate
act. There is no migration deciding what your settings should be.

## Updating

```bash
python tools/bp_merge_upstream.py
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

This is alpha-quality software for research purposes. It is not a product. The driver is responsible
at all times, and this remains an SAE Level 2 system: extra sensors and better tuning do not change
that. Users are responsible for complying with local laws.
