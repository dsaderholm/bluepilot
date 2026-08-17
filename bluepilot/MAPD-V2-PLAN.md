# mapd v2 — what it would get us, per feature

Written 2026-08-16. Read the CLAUDE.md section "WE ARE PINNED TO THE LAST RELEASE OF A DEAD MAPD"
first for the state of the world; this is the inventory of what upgrading buys, so the decision can
be made against a list instead of a vibe.

**Nothing here is scheduled.** The California trip is three weeks out and this touches the layers
this fork has customized most. This document exists so the next person does not have to re-derive
the value, and so the ICBM and radar-detector sessions can see what is in it for them.

## FIRST: what is actually IN the map, measured

His question, 2026-08-16, and the right one: *"right now speed limits are missing for so many roads
I drive, so how much other information is missing, too?"*

Measured against Overpass rather than assumed. Salt Lake County, 16,469 way segments,
motorway..tertiary:

    class          ways  maxspeed  lanes  oneway  advisory  turn:lanes
    motorway        941       97%   100%    100%        1%         22%
    trunk           308       93%    98%    100%        0%         21%
    primary        4046       93%    99%     72%        0%         28%
    secondary      4136       85%    93%     39%        1%         11%
    tertiary       5325       66%    79%     32%        0%          6%

His actual corridors, 3,100 segments:

    route          ways  maxspeed  lanes  oneway  advisory
    I 15            970       90%   100%     88%        0%
    US 6            536       86%   100%     22%        2%
    US 89          1594       86%    99%     56%        0%

**`lanes` IS ESSENTIALLY COMPLETE on every road he drives** -- 99-100% on all three corridors, better
covered than speed limits. The tag passing assist most needs is the one OSM has. `oneway`'s low
numbers are not gaps: absence means two-way, which is the OSM default, so US 6 at 22% is CORRECT and
is what makes it a passing-assist road.

**`maxspeed:advisory` IS 0-2% AND MUST BE TREATED AS ABSENT.** An earlier version of this document
listed `advisorySpeed` as a win for SCC-Vision -- "the yellow curve sign, an independent number for
a corner." It is not available in Utah in any useful quantity. That was a field list read as though
it were data.

**HIS CORRECTION, and it is right:** *"I'd still use advisory if it exists, though. I want to use
all data."* Struck was the wrong verdict -- what is wrong is DEPENDING on it, not consuming it. A
mapped yellow sign is a real number for that corner. So it is opportunistic input, exactly like every
other map field: may refuse, may corroborate, may never be the sole thing that opens a maneuver. What
it cannot be is the cross-check SCC-Vision is missing, because at 1% it will not be there when needed.

It is not a new or proposed tag either, which was the next reasonable guess -- it is an old one
almost nobody fills in. Worldwide, from taginfo: `maxspeed` 22.3M uses, `lanes` 19.9M,
**`maxspeed:advisory` 113K** -- half a percent of maxspeed. No import provides it (every value is
somebody standing at a yellow sign), and no router consumes it, so nothing has ever pulled it into
existence. Treat it as permanently absent rather than as coverage that will improve.

**This does NOT mean the map cannot help with curves**, and the two must not be confused: mapd
derives corner speeds from road GEOMETRY, and `MapdExtendedOut.path` carries per-point `curvature`
and `targetVelocity` regardless. What is lost is only the INDEPENDENT cross-check on a computed
curvature -- which SCC-Vision has never had and now will not get from the map.

**`turn:lanes` at 6-28% confirms the center-turn-lane case is not solved by the map either**, which
is what the passing assist section below already says for a different reason.

### THE 50x DISCREPANCY WAS NOT REAL. MEASURED AND WITHDRAWN 2026-08-16.

**It was an artifact of a PARKED CAR.** This section used to read: OSM has a limit on 86-97% of his
roads, route 00000379 measured SLA holding one on 1.7% of plan frames, so something between the tile
and SLA is eating 50 out of every 51 limits. The second number came from the FRONT of a 53-segment
route, where the GPS bbox is one point to four decimal places -- the car was sitting in the driveway.
He said so plainly at the time: *"the speed limit works."* He was right and the measurement was wrong.

**Two sessions hit the identical trap independently on the same day**, one sampling the first 6-8
segments and one the first 10, which is worth more than either finding: every tool in `tools/` caps
at `--max-segments` from the FRONT, and that cap is correct for "did this event happen" and silently
wrong for "what did the whole drive look like". `bp_map_vs_sla.py` samples EVENLY SPACED segments
instead. Check any other whole-drive percentage in this repo that was produced with a segment cap.

Re-measured over whole routes, with positions bucketed on a ~55 m grid so a red light cannot outvote
a mile of freeway, and only above 5 mph:

    route 00000379   62,940 plan frames   SLA had a valid limit in 50.9%   1200 positions, 170 blind
    route 00000378   44,900 plan frames   SLA had a valid limit in 76.4%   1197 positions, 169 blind

And for every blind position, what the device's OWN tiles hold there:

                                              00000379        00000378
      a way with a maxspeed was right there    20  11.8%      17  10.1%   <- genuinely lost
      nearest way carries no maxspeed         149  87.6%     152  89.9%   <- OSM has nothing
      no way within 40 m                        1   0.6%       0   0.0%
      no tile covering the point                0   0.0%       0   0.0%   <- coverage is complete

**So the honest number is about 1.7% of positions, not 98% of them** -- and note that the figure
withdrawn from this section and the residual real defect happen to share a number, which is a good
way to quote the wrong one. Two independent drives agree to within two points on every row, which is
what makes this trustworthy where the original was not.

Three conclusions, all different from what this section used to say:

- **The map data is NOT being eaten.** The dominant reason SLA has no limit is that OSM has no
  `maxspeed` on that way -- 88-90% of blind positions, and they are residential streets, which is
  exactly where the county-wide 86-97% figure does not apply. That figure counts WAYS across
  motorway..tertiary; a drive spends its minutes elsewhere. Comparing them was comparing populations.
- **Not one blind position lacked a tile.** Downloading more maps would fix nothing.
- **There IS a residual defect and it is on the freeway**, which is why it still matters at 1.7%: six
  consecutive positions on US 40/189 where the tile says 65 mph, and one on I-80 where it says 70,
  with SLA holding nothing. That is v1 way-matching or SLA validation, it needs no migration to
  investigate -- and it is precisely what mapd v2 would make legible, since `waySelectionType`
  including `fail` says whether the map was LOST rather than confident and wrong.

`PassingAssistPatience` still needs a posted limit and is still inert without one, but it is inert
across roughly 14% of the road he covers rather than 98% of it.

### A HOLD IS A LABELED DATA POINT ABOUT THE MAP

His, immediately after the coverage numbers above, and it reframes what a hold is:

  *"If I am setting a hold, though, it means the speed limit is wrong or I want to go slower/faster
   than my offset."*

Correct, and those two cases separate over TIME even though no single drive can tell them apart:

  same offset, same place, every trip   systematic -- the map is WRONG on that stretch
  a one-off                             intent -- traffic, weather, running late

So the hold history is a personal speed-limit correction layer built from data he already generates,
needing no mapd upgrade and no TSR -- which matters, because TSR is still blocked on U0253 and cannot
supply the true sign.

**A hold BELOW the limit is the unambiguous half.** Nobody corrects a posted limit downward by
accident, so that is a statement about the ROAD rather than about the map, and it is the direction to
trust first.

**It also bounds a live feature.** `PassingAssistPatience` (shipped 2026-08-16) computes
`reference_speed - posted_limit`, and `reference_speed` IS the ICBM hold when one is set -- so a
wrong map limit makes it misread hurry in exactly the way he describes. What contains that is the
one-directional design: the scale is clamped at 1.0 and can only ADD patience, never remove it below
his configured deficit. A wrong limit therefore costs the extra fussiness and nothing else; it can
never make the car more willing to pass than his own settings. Keep that clamp.

**Owned by ICBM** -- holds are theirs, `bp_hold_history.py` already walks every change to one with
`baselineSource` naming the mechanism, and that is where a correction layer would be built from.

#### HE HAS SPECIFIED IT: three sightings and the hold is remembered

*"A hold will get remembered if it's set 3 times."* 2026-08-16. So this is a feature request with a
threshold already chosen, not an idea to evaluate.

**THE KEY IS THE WHOLE DESIGN, and it is the strongest concrete argument for mapd v2 in this
document** -- stronger than any field on the list above, because it decides whether the feature
works at all rather than making it nicer:

  v1 today   `RoadName` + `MapSpeedLimit` are the only identifiers available. The key becomes
             something like ("I 15", 65) -> 72. But "I 15" is FOUR HUNDRED MILES. A correction made
             in Utah County applies through St George, and a road whose posted limit is wrong in
             different ways along its length cannot be represented at all.
  v2         `wayId`, added in mapd v2.1.0, identifies the actual OSM way -- typically a few hundred
             meters to a few miles. "The map is wrong HERE" instead of "somewhere on this highway."

So the feature is crude on what ships today and precise after the upgrade.

Constraints it has to respect, both already recorded elsewhere in this repo:

- **It must show what it is doing.** "The one thing it must never become is a system that retunes
  itself and mentions it nowhere." The HOLD badge already draws and is already a tap target, so the
  surface exists -- but a REMEMBERED hold has to be distinguishable from one he just set, or he
  cannot tell a decision he made from one made for him.
- **The counter is a JSON param and those encode themselves.** `put(key, json.dumps(x))` is a
  TypeError and `get` returns the decoded object. That mistake shipped pinned holds completely dead
  for their entire life, because both directions were broken and therefore agreed with each other.
- **A hold BELOW the limit is the direction to trust first**, per the paragraph above.

Open questions for whoever builds it: what counts as "the same" hold (exact mph, or a band); whether
the count decays when he drives the same road WITHOUT setting one; and how a remembered hold
interacts with `enforce_no_limit_no_hold` -- it should be consistent, since the key includes a posted
limit and therefore cannot exist where no limit is known.

### DOES OPENPILOT'S OWN NAVIGATION SUPERSEDE THIS? No -- different mechanism entirely

Checked 2026-08-16, because it is the obvious risk to this whole branch and neither of us knew where
comma was with it.

  Navigate on openpilot shipped in 0.9.4 (the Taco Bell drive), gained navigation INSTRUCTIONS in
  0.9.5 as a ternary left/straight/right vector, and was REMOVED in 0.9.7 "to focus on improving the
  driving model", with a stated plan to bring it back better. There is no `nav` or `map` directory in
  openpilot's `selfdrive` today, and no date for its return. (Same shape as sunnypilot's mapd v2
  promise, which is worth noticing having now seen it twice.)

  CORRECTED 2026-08-16, same day: an earlier version of this paragraph said navigation DISPLAY was
  being rebuilt, citing `ui: navigation stack` (#37094, merged 2026-02-21) and its tici twin. **Those
  are about navigating the USER INTERFACE** -- screens, keyboards, dialogs, back buttons; the PR body
  reads "Makes navigating the ui more intuitive" and a sibling PR is titled "Nav stack: pop_widget
  takes widget". Matched on the word and inferred maps. There is no map work in flight at comma that
  is visible from the outside.

  **AND OPENPILOT ITSELF HAS NEVER USED MAPD.** mapd is a FORK component -- pfeiferj built it for
  forks, and sunnypilot, FrogPilot and the rest are its users. comma ships no OSM tiles and no
  offline map at all. When comma did navigation they used **Mapbox**, verified rather than recalled:
  `from_mapbox_tuple` in `selfdrive/navd/helpers.py` at tag v0.9.4. So their nav was online and
  commercial, which is the same dependency that makes Mapbox a poor primary source for us -- the
  roads with the worst OSM coverage are disproportionately the roads with the worst LTE.

  The consequence for THIS branch: there is no upstream, at either comma or sunnypilot, whose arrival
  would make this work redundant. Nothing to wait for in either direction.

**NOO NEVER PUT MAP DATA IN THE PLANNER.** It rendered the map to an IMAGE, compressed it, and fed
that to the driving model, which predicted where a human would drive to follow the route. So there is
no `lanes`, no `oneWay`, no `highwayClass` published anywhere a gate could read -- it is pixels into a
neural net. **Nothing in this document is made redundant by navigation returning.**

**And it explains his observation about the Taco Bell drive**, which is a forced consequence of that
design rather than a gap comma did not get to: *"it only changed lanes for navigational purposes."*
Navigation was the only thing the model was told. There is no representation of "that car ahead is
slow" anywhere in the input, so a passing decision was not merely absent -- it was unrepresentable.

The real interaction, if it returns, is two systems wanting the same actuator: the model wanting the
exit lane, passing assist wanting the overtake. Nothing structurally conflicts -- the passing hook is
a pre-maneuver gate -- and the constraint to hold is that **nav must never displace radar oncoming
detection**. Re-check against whatever actually ships.

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
- `lanes` coverage is NOT the worry it looks like -- measured at 99-100% on his corridors and 79%
  even on tertiary streets. `tileLoaded` and `waySelectionType` still have to gate it, because an
  absent tag must read as "unknown" and never as "one lane"; but the tag is there.
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
| `advisorySpeed`, `nextAdvisorySpeed` | **Use it where it exists; never depend on it.** 0-2% coverage in Utah, 113K uses worldwide — so it cannot be a defense SCC leans on, but where a yellow sign IS mapped it is a real independent number for that corner and there is no reason to discard it. *"I want to use all data."* Same rule as everything else here: may refuse, may corroborate, may never be the sole thing that opens. |
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

## NOTHING HERE COSTS HIM MONEY. Stated because it read the other way once.

**OpenStreetMap is free** -- volunteer-made, openly licensed, no account, no API key, no billing.
**The map tiles are free too.** pfeiferj takes the free OSM planet file, converts it to the compact
format mapd reads, and hosts it at `map-data.pfeifer.dev`; the device downloads from there at no
charge. His introduction doc mentions the hosting costing about $5/month -- **that is HIS bill, and
he raises it as a point about efficiency**, not a price to anyone. Quoting it without saying whose it
was is what caused the confusion, on 2026-08-16.

The only map option with a real bill attached is **Mapbox**: commercial, API key, per-request
pricing. It is what FrogPilot uses as a speed-limit fallback and what comma used for navigation, and
it is the one we are NOT doing -- now for two reasons, since it also needs live LTE exactly where LTE
is worst.

The single genuine consideration is that tile hosting rests on one person continuing to pay his own
$5. That is a single-point dependency, not a charge, and regeneration was measured live above with a
file dated two days before the check.

## What it costs to INTEGRATE

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

## WHY SUNNYPILOT WILL NOT DO THIS FOR US, AND WHY WE NEED IT FIRST

His question, 2026-08-16: *"SunnyPilot right now has no need for a newer mapd, right? Will they
ever?"* Correct, and it explains the stall better than "abandoned" does.

**Their map-consuming features are Speed Limit Assist and map turn speed control, and both work on
v1.** So the v2 move is a refactor with no user-visible feature behind it, and refactors with no
feature attached lose to comma 4 support every time. PR #1647 is not neglect, it is a correct
priority call for a fork that does not need the data.

**WE NEED IT FIRST, AND FOR A REASON THEY DO NOT HAVE.** Passing assist wants `lanes`,
`distanceFromWayCenter`, `highwayClass` and `oneWay`. **None of sunnypilot's features want any of
those**, and the remembered-hold feature he specified needs `wayId`, which is measured at 100%
populated on tiles already on his device. "Wait for upstream" was never waiting for a peer with the
same problem; it was waiting for someone with no reason to move.

**One urgency argument was raised and is DISPROVED by the measurements below** -- recorded because it
is the obvious worry and someone will have it again. The fear was that v1's tiles come from one
person's hosting, that v2.1.0 changed the map file format, and that v1-format regeneration would
therefore stop and strand every fork on v1 with silently stale speed limits. **There is only one
dataset.** Same URL, same store, same files; the format change was additive under the same capnp file
id, and the box pulled from the SHIPPED v1 downloader was dated two days before the check. v1 has been
reading post-v2 tiles in production all along. There is no countdown.

**So the cost of doing it here is the only thing left to weigh**, and finding 1 below is what makes
it acceptable: divergence from sunnypilot inside their own files is the price CLAUDE.md says outranks
being right elsewhere -- but a pure observer touches none of their map layer at all.

## THE OPEN QUESTION — ANSWERED 2026-08-16: YES

**Can the Minimal path run ALONGSIDE v1 without the SLA/SCC teardown that PR #1647 performs?**

**Yes.** v2 can run as a pure observer. Every mechanism the two would have had to share turns out
either to be untouched by v2 or to be *already shared and already compatible*. The teardown in
PR #1647 is sunnypilot's consolidation choice, confirmed rather than assumed.

Four findings, in descending order of how much they decide it.

### 1. THE TWO TRANSPORTS DO NOT TOUCH AT ALL

This is the whole answer, and it is a one-line fact: **v2's `params/params.go` has no `/dev/shm`
concept in it.** v1 declares both `ParamsPath = "/data/params/d"` and
`MemParamsPath = "/dev/shm/params/d"`; v2 declares only the first, and the only key it writes is
`MapdSettings` (it declares a `LastGPSPosition` path and never uses it). Everything else v2 emits is
cereal over msgq.

So the sixteen `/dev/shm/params` keys v1 publishes — `RoadName`, `MapSpeedLimit`,
`MapTargetVelocities`, `MapCurvatures`, `MapTargetLatA`, `NextMapSpeedLimit`, the `OSMDownload*`
trio and the rest — are written by v1 and read by SLA/SCC exactly as today, with **nothing v2 does
able to perturb them.** SLA, SCC-Map and `map_controller.py` do not change, are not read from, and
cannot be affected.

**And v2 gets its own position rather than wanting ours.** `cereal/gps.go` subscribes to
`gpsLocationExternal` and falls back to `gpsLocation` — both already in our `cereal/services.py`
(10 Hz / 1 Hz). We do not have to copy GPS back out to it the way v1 requires, and we do not have to
publish `mapdIn`: the CLI publishes `mapdCli` itself.

**The msgq ABI matches.** `gomsgq` is a pure-Go reimplementation, not a binding — it mmaps
`/dev/shm/<openpilot_prefix>/<endpoint>` and overlays a header of `num_readers`, `write_pointer`,
`write_uid` and three `NUM_READERS`-long arrays, with `NUM_READERS = 15`. That is field-for-field
`msgq/msgq.h` at our pinned `9beb84af`.

### 2. THE MAP STORE IS ALREADY SHARED, AND THAT IS GOOD NEWS RATHER THAN A COLLISION

The question assumed two tile sets. There are not two. v2's `GetBaseOpPath()` returns
**`/data/media/0/osm`** — literally `Paths.mapd_root()`. Same base path, same source URL
`https://map-data.pfeifer.dev/offline/{lat}/{lon}.tar.gz`, same `tmp/offline` staging, same
extraction layout. v1.12.0 and v2.3.0 are the same client against the same store.

**And the tile format is additive, with the same capnp file id `0xda3a0d9284ca402f`.** v2's `Way`
appends `id @14`, `highwayClass @15` and the three `maxSpeedConditional` fields @16-18 to v1's
fourteen. A v1 reader ignores ordinals it does not know; that is what capnp evolution guarantees.

**Measured, not reasoned about.** The 1-degree box covering Salt Lake City was pulled from the URL
the SHIPPED v1.12.0 downloader uses, and parsed against the v2 schema:

    box 40.75,-112.0 -> 41.0,-111.75     file dated 2026-08-14      9,165 ways

    id (wayId)      100.0%
    highwayClass    100.0%
    lanes            73.5%
    advisorySpeed     0.5%      (as the coverage section above predicted)
    maxspeed:cond     0.0%

    motorway 294   motorwayLink 403   trunk 17   primary 591   secondary 1153
    tertiary 1632  residential 4178   unclassified 831   *Link 66

Three consequences:

- **The tiles already on his device carry the v2 fields.** The regeneration is live (that file was
  built two days ago), it is one hosted dataset, and v1 has been reading post-v2 tiles in
  production all along. So the shared store costs **zero extra disk and zero extra download** — the
  two binaries read the same files.
- **`highwayClass` separates freeway from ramp in his own city today** — 294 motorway against 403
  motorwayLink in one box. The exit problem's missing fact is sitting on the eMMC unread.
- **`wayId` is 100% populated**, so the remembered-hold key is available the moment there is a
  transport for it. What is missing has never been the data; it is that v1 cannot say it.

**This also sharpens the 50x discrepancy above.** The tiles hold `maxspeed` for these roads and are
already on disk, so the loss is between the tile and SLA — v1's way-matching or SLA's own validation
— and not coverage. Chase it there.

### 3. FOUR SMALL COLLISIONS, ALL MECHANICAL

- **The process name `mapd` is taken.** `process_config.py` already has
  `NativeProcess("mapd", Paths.mapd_root(), ...)` for v1. The second entry needs its own name, and
  its own binary path — v1 lives at `third_party/mapd_pfeiferj/mapd`, v2's integration doc wants
  `selfdrive/mapd`. `mapd_installer.py` pins v1's version and hash, so v2 needs a separate install
  path; the v2.3.0 release asset is a single 20.8 MB binary.
- **Downloads are commanded, not automatic** — by CLI or a `mapdIn` message, against
  `download_menu.json`. So keep v1 as the downloader and never command v2's, or the two race in the
  shared `tmp/offline` staging directory.
- **The OSM settings screen's DELETE button `rmtree`s `/data/media/0/osm/offline`** — shared store
  means shared fate. Correct behavior, worth saying out loud rather than discovering.
- **Capnp slots 17-19 are free on every branch** and land at `@143/@144/@145`, exactly what mapd
  hardcodes. Passing assist took 15 (`rearRadarBP @141`); nobody has claimed 16-19. The
  wire-history tiebreaker still governs *when* they are declared — claim them on the branch that
  will own them, and never renumber a field already recorded on the device.

### 4. SKIP STEPS 6 AND 7. THEY ARE THE ONLY NON-ADDITIVE ONES.

Step 7 clamps `v_cruise` to `mapdOut.suggestedSpeed` inside `longitudinal_planner.py`. That single
edit is what turns v2 from an observer into a controller, and it would hand the map an unmediated
veto over the set speed — the exact shape "the map is EVIDENCE, never PERMISSION" forbids. Steps
1-5 and 8 are pure additions: capnp definitions in reserved slots, a service entry, a param key, a
binary, a process entry, and a SubMaster subscription.

Which means the back-out is deleting one process entry.

### MEASURED ON THE DEVICE, 2026-08-16 — AND v1 IS NOT CHEAP

    /data              89G total, 75G used, 9.0G free (90%)   -- 65G of it is realdata
    /data/media/0/osm  524M, 3072 tiles, all downloaded 2026-08-02
    mapd (v1.12.0)     ~22% of a core, 204 MB RSS (5.5%)      -- OFFROAD, engine off
    mapd_manager       0.3% CPU, 89 MB
    memory             3607 MB total, 2184 MB available

**The device's OWN tile was copied off and parsed, not just the hosted one.** Same box, downloaded
2026-08-02: 9,159 ways, `wayId` 100%, `highwayClass` 100%, 292 motorway against 403 motorwayLink. So
this is not a claim about what the server would send if we re-downloaded — **the freeway/ramp tag and
the way ids are on his eMMC now**, and have been since before every drive analyzed in this document.

**The honest reading of the CPU number: coexistence is affordable but it is not free.** v1 alone
burns about a fifth of a core and 200 MB while the car is parked, which makes it one of the more
expensive processes on the device, and v2's claim of "sub-10% memory and a few percent of a core" is
against the same baseline rather than on top of a small one. With 2.1 GB available and 76% idle
offroad there is room, and the shared tile store means no second copy of 524 MB. But **run both for
the migration window, not permanently** -- the point of the observer period is to prove the data,
then move the consumers over and delete v1, not to keep two map daemons resident forever.

**Disk is not a constraint here and the pressure is elsewhere:** 9 GB free, of which v2 needs 20.8 MB
for its binary and zero for tiles. What has /data at 90% is 65 GB of realdata, which is `deleter`'s
business and nothing to do with this.

Still unmeasured, and it needs v2 actually running:

- **CPU and memory with both up at once.** Two Go processes mmapping tiles for the same area is the
  specific thing to watch, against the ~22%/204 MB baseline above.
- **Route size with `mapdOut` logged at 20 Hz** — the diagnostics win has a cost and it should be a
  number.

### SO THE SEQUENCING CHANGES, AS EXPECTED

Take the DATA first and migrate the CONSUMERS later, one at a time, each behind its own toggle. The
first consumer is not SLA or SCC: it is a diagnostic, because for the first time the map's own
account of a drive lands in the route.

### WHAT TO ASK THE FIRST OBSERVE DRIVE, IN ORDER

Observe mode went live 2026-08-16. Once a drive with `mapdOut` in it exists, run
`tools/bp_mapd_compare.py` and answer these three, in order:

1. **Is "only v1 had a limit" near zero?** That is the gate for state 2. Note the tool scores only
   frames above 5 mph, because v1 serves the PREVIOUS drive's limit out of `/dev/shm/params` while
   stationary and it reads exactly like a live one.
2. **DOES mapd PUBLISH A NON-ZERO `speedLimit` ON FRAMES WHERE `waySelectionType` IS `fail`?** This is
   the one review finding left deliberately open: it cannot be settled offline, and guessing at a
   gate would be inventing behavior. `MapdV2MapData` passes `speedLimit` straight through today
   without consulting the confidence field. If mapd zeroes the limit when its matcher fails, nothing
   needs doing. **If it does not, the reader needs a gate before state 2** -- otherwise a guessed
   limit reaches Speed Limit Assist labelled exactly like a matched one, which is the opposite of
   what this migration is for.
3. **What do the `only v2` rows look like?** Those are places v1 was blind. The measured US 40/189
   case -- tile holds 65 mph, SLA showed nothing -- should appear among them; if it does not, the
   residual defect is somewhere other than v1's way-matching and the 1.7% figure needs re-deriving.
