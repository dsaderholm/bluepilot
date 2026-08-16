# mapd v2 — what it would get us, per feature

Written 2026-08-16. Read the CLAUDE.md section "WE ARE PINNED TO THE LAST RELEASE OF A DEAD MAPD"
first for the state of the world; this is the inventory of what upgrading buys, so the decision can
be made against a list instead of a vibe.

**Nothing here is scheduled.** The California trip is three weeks out and this touches the layers
this fork has customized most. This document exists so the next person does not have to re-derive
the value, and so the ICBM and radar-detector sessions can see what is in it for them.

## The three facts that frame it

1. **`v1.12.0` is the last v1 release that will ever exist.** pfeiferj shipped it and everything
   after is v2.x; upstream is on v2.3.0. We are pinned to the terminal release of a dead major
   version.
2. **sunnypilot's move to v2 is abandoned, not slow.** PR #1647 is a draft from 2026-01-14,
   `+3/-2567`, zero human comments in seven months; `mapd-v2` is 1439 commits behind master; the
   author is still active in the repo on other work. There is nothing to wait for and nothing to
   collide with.
3. **The map is EVIDENCE, NEVER PERMISSION.** His rule, and the thing that separates this from
   BlueCruise. Every item below is subject to it: map data may refuse a maneuver freely, and may
   never be the sole thing that opens one. See the CLAUDE.md section of that name.

## Why v1 is short on data, which is not the same as OSM being short on data

v1 talks to openpilot through `/dev/shm/params`. pfeifer's own introduction doc calls that the
design's biggest flaw:

- it is a **blocking** operation in the controls loop
- **every new field is a breaking change for every fork**, which is why v1 stopped at road name
  and speed limit
- **none of the data reaches the route logs**, which is why no drive analysis in this fork has ever
  been able to see what the map was saying

v2 rewrites comma's msgq in Go (`gomsgq`), so mapd speaks native cereal: non-blocking, logged, and
able to read openpilot's state directly rather than us copying GPS back out to it. The field list
below is the backlog v1's transport made impossible, not a pile of new features.

---

## What PASSING ASSIST gets

| Field | What it answers | Against which open problem |
|---|---|---|
| `lanes` | how many lanes the carriageway has | the outer lane line — paint refuses at 0.01-0.05 with `geoLoosenTo` 0.0, so **no threshold exists** that fixes it |
| `distanceFromWayCenter` | where we are across the road | which lane we are IN, which `lanes` alone cannot give |
| `estimatedRoadWidth` | lanes x lane width | the third leg of the above |
| `highwayClass` | the raw OSM tag, `motorway` vs `motorwayLink` vs `trunk`… | **freeway vs on/off-ramp**, stated exactly rather than heuristically |
| `oneWay` | is this carriageway one-way | divided-highway corroboration for the radar oncoming veto — the I-15 false-positive case |
| `roadContext` | freeway / city / unknown | coarse road class where `highwayClass` is missing |
| `waySelectionType` | `current`/`predicted`/`possible`/`extended`/**`fail`** | knowing when the map is LOST rather than confident and wrong. Same discipline as `available` on the radar |
| `tileLoaded` | was there a tile at all | "no data here" vs "no map here" |

**The honest limits, so this is not oversold:**

- None of it authorizes anything on its own — see fact 3. `lanes = 3` may not open a lane change.
- `lanes` is frequently absent in OSM outside major roads. `tileLoaded` and `waySelectionType` are
  what keep that from reading as "one lane".
- **It does not solve the center turn lane.** OSM does tag `turn:lanes`, but mapd does not publish
  it, so the arterial TWLTL case that reverted the road-edge waiver on 2026-08-09 stays exactly
  where it is: `geoLeftTravelProven` vs `geoLeftProven` is still the measurement that settles it.

## What ICBM, SCC and SLA get

This is the larger half, and it was underrated because the OUTPUTS get all the attention while the
SETTINGS are where the exit problem actually lives.

**The exit that never slows enough.** CLAUDE.md ends that section with *"the remaining lever is how
far ahead `MapTargetVelocities` is populated, which is mapd's, upstream of this fork."* In v2 that
lever is a **setting**, not a code change:

- **`Curve Target Speed Time Offset`** — how far ahead of a corner the target is published. The
  measured deficit was the map firing 4 s before peak cornering against an 8 s need at 3.3 mph/s.
  This is that dial.
- **`Map Curve Target Lateral Acceleration`** and **`Vision Target Lateral Acceleration`** — the
  corner aggressiveness, set directly. Today we reach it through
  `SmartCruiseControlMapFactor`/`...HighSpeedFactor` multipliers layered on upstream constants,
  which is the shape "keep only additions that earn their place" wants gone.
- **`Speed Limit Decrease/Increase Target Speed Time Offset`** — how early to act on a limit change,
  the same earliness question for SLA.
- **`Target Speed Jerk` / `Target Speed Accel`** — descent shaping, against the 3.3 mph/s hardware
  ceiling.

**`MapdExtendedOut.path` is the bigger structural change.** It is a list of points ahead, each with
`latitude`, `longitude`, `curvature` and `targetVelocity`. Today SCC-Map publishes **one corner
speed as a step, at the moment braking must begin** — which is why "SCC-Map decel is a trigger
distance, not a rate" is a whole entry in the got-wrong-before list, and why four defenses had to be
built to question a number that arrives with no context. With the path we would have the entire
speed profile ahead and could plan a descent against the budget rather than react to a step.

Other outputs that land on ICBM/SLA directly:

| Field / setting | Why it matters here |
|---|---|
| `advisorySpeed`, `nextAdvisorySpeed`, `nextAdvisorySpeedDistance` | the yellow curve-advisory sign — an independent number for a corner, which **SCC-Vision has never had**. CLAUDE.md: "vision got none, and vision is the controller that owns the near field" |
| `speedLimit` with forward/backward direction handling | correct limit on divided roads where each direction is tagged separately |
| `nextSpeedLimit` + `nextSpeedLimitDistance` | already used, but logged and at 20 Hz |
| `conditionalSpeedLimit` (raw `maxspeed:conditional`) | school zones and time-of-day limits, with the raw tag exposed so we can evaluate conditions mapd does not |
| `hazard`, `nextHazard`, `nextHazardDistance` | a hazard path that is not derived from curvature |
| `tileLoaded` | separates "no limit" from "no map" — the distinction behind a hold inferred for 36.5% of route 00000379 |
| `Speed Limit Priority` setting | dash-vs-map ordering, which is where TSR plugs in if it is ever fixed |
| `Hold Last Seen Speed Limit` setting | the no-coverage road, in mapd rather than in our fallback |
| `visionCurveSpeed` | mapd's own vision curve estimate, as a cross-check on SCC-Vision |

## What future projects get

- **Rear radar / passing assist phase 2.** `highwayClass` + `oneWay` narrow when a rear-approach
  veto should be strict, without another sensor.
- **Radar detector integration.** `roadContext` and `highwayClass` are the road class that session
  would otherwise have to infer from speed.
- **Anything that wants to know where the car is.** `MapdExtendedOut.position` (added v2.3.0) gives
  the position mapd resolved, for drawing the path.
- **Diagnostics generally.** This is the one nobody asks for and everybody uses: **map data lands in
  the route.** Every tool in `tools/` is currently blind to what the map was saying.

---

## What it costs

From mapd's own `docs/integration.md`, the **Minimal** path — note it is ADDITIVE:

1. capnp definitions into `cereal/custom.capnp`, replacing `CustomReserved17-19`, and the matching
   `mapdExtendedOut`/`mapdIn`/`mapdOut` entries in `log.capnp`'s Event struct
2. `mapdOut` as a service in `cereal/services.py`
3. `MapdSettings` (JSON) in `common/params_keys.h`
4. the binary in `selfdrive/`, and a `NativeProcess` entry in `process_config.py`
5. `mapdOut` on plannerd's SubMaster
6. optionally clamp `v_cruise` — **we would skip this**, since our own controllers do it
7. `./selfdrive/mapd i` on the device once, to download maps

**Known collisions:**

- We already claim `customReserved` slots — the rear radar took `customReserved15`. Slots 17-19 need
  checking against every branch before this starts, and the wire-history tiebreaker applies.
- sunnypilot's `live_map_data` layer reads `mem_params` throughout and would need a v2 reader.
- `mapd_installer.py` pins the version and hash; v2 ships differently.

## THE OPEN QUESTION, and it is the one to answer first

**Can the Minimal path run ALONGSIDE v1 without the SLA/SCC teardown that PR #1647 performs?**

That teardown is sunnypilot's consolidation choice, not a technical requirement of mapd v2. If v2
can run as a pure observer — publishing `mapdOut` for us to read, while v1 keeps feeding SLA and SCC
exactly as today — then this becomes an additive change we can take incrementally and back out of,
instead of a rewrite of three subsystems.

Unverified. Things to check before anything else:

- do v1 and v2 fight over map storage (`Paths.mapd_root()`), or use separate trees?
- do both want the same GPS/params, and does v1's blocking param write interfere?
- two tile sets on disk — how much space, and is it acceptable on the 3X?
- CPU and memory with both running. v2 claims sub-10% memory and a few percent of a core; v1 is
  what we already pay.

If the answer is yes, the sequencing question changes completely: we would take the DATA first and
migrate the CONSUMERS later, one at a time, each behind its own toggle.
