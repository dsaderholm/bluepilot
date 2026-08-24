# How everyone else does navigation, and what comma actually built

Researched 2026-08-23, for the route-intent branch. The question was his: how do other self-driving
systems use navigation, and what did comma have before they removed it.

**Short version: comma's data model is better than ours and still readable in our own tree; the
reason they removed the flashy half does NOT apply to what we are building; and every production
system uses its OWN router, which is the one place his design is genuinely unusual.**

Sources are marked. Anything from our own tree is primary and quoted exactly; anything from the web
is attributed and should be treated as the weaker claim.

---

## 1. COMMA HAD TWO SEPARATE FEATURES AND ONLY ONE WAS REMOVED FOR THE REASON PEOPLE REMEMBER

This is the single most important thing in this document, because "comma tried navigation and gave
up" is the folk version and it is wrong in a way that matters to us.

    turn-by-turn navigation      navd + Mapbox + the nav messages. A DISPLAY and a data feed.
                                 Shipped for prime users.
    navigate on openpilot        feeding route info INTO THE DRIVING MODEL so the car takes exits
                                 and follows the route. Experimental mode, 0.9.4.

**`navigate on openpilot` was removed in 0.9.7, and the stated reason was ACTUATION, not data:** the
driving model's ability to execute the maneuvers required to follow navigation was limited, making
the experience unreliable, and removing it reduced training-stack complexity so they could iterate
on core problems. The turn-by-turn navigation feature itself was explicitly **not** affected by that
removal.

**WHY THAT MATTERS HERE, and it is reassuring rather than merely interesting.** comma's failure was
"the model could not drive the route". Our design asks the model to do NOTHING new -- route intent
gates a decision the passing-assist maneuver machinery already makes, and the maneuver itself is the
one that already exists. **The failure mode that killed comma's version is not reachable from our
architecture.**

The corollary is the warning: if anyone here ever proposes feeding route intent to the MODEL as an
input, that is the thing comma tried and withdrew, and it should be argued on its own merits rather
than as an obvious next step.

---

## 2. COMMA'S DATA MODEL, READ OUT OF OUR OWN TREE

The structs are still in `cereal/log.capnp` as deprecated ordinals -- `navInstructionDEPRECATED @82`,
`navRouteDEPRECATED @83`, `navModelDEPRECATED @104`. Primary source, exact:

```
struct NavInstruction {
  maneuverPrimaryText, maneuverSecondaryText   the display strings
  maneuverDistance      # m
  maneuverType          # Text -- "TODO: Make Enum"
  maneuverModifier      # Text
  distanceRemaining, timeRemaining, timeRemainingTypical
  lanes                 # List(Lane) -- directions, active, activeDirection
  showFull
  speedLimit            # m/s
  speedLimitSign        # mutcd (US) | vienna (EU)
  allManeuvers          # List(Maneuver): distance, type, modifier
}

struct NavRoute { coordinates :List(Coordinate) }
```

**FOUR THINGS THEIRS HAS THAT OURS DOES NOT, and two of them are worth stealing.**

**`allManeuvers` -- the whole remaining list, not just the next one.** `RouteIntentBP` carries one
instruction, which we justified because Android Auto itself says *"display may only show information
about the first step"*. That is true of the AA transport and is NOT a law of the problem. comma
carried the lot. For a gate that only refuses, one is enough; for planning a descent into an exit,
knowing the maneuver AFTER the next one is how you avoid braking twice.

**`NavRoute` -- the route as a POLYLINE.** This is the big one, and it is a better answer than the
idea recorded in ROUTE-INTENT 11f. That section proposes joining `Step.road` (a street NAME) against
`Way.name` in the OSM tiles, and notes the join is fuzzy exactly at interchanges. **With the route as
coordinates you do not join by name at all** -- you map-match a polyline against tile geometry, which
is the thing map-matching is actually good at and which mapd already does for the current position.
If a transport can ever give us the route geometry rather than just the next instruction, take it
over the name.

**`speedLimit` + `speedLimitSign`.** comma took a nav-sourced speed limit as a first-class field.
Relevant to the standing SLA problem here, where TSR is dead and mapd covers about half.

**`type` + `modifier` instead of one flat enum.** Mapbox splits `"turn"` + `"left"`, `"off ramp"` +
`"slight right"`. Android Auto uses one enum of 47 values, which is what `RouteIntentBP.Maneuver`
mirrors. Neither is wrong; note only that a Mapbox-shaped transport will arrive as a PAIR and the
adapter has to flatten it.

### 2a. What survived the deletion, and one piece of it is broken

**`sunnypilot/navd/helpers.py` is much more than ROUTE-INTENT section 8 claims.** That section calls
it "a Coordinate class and geometry, surviving only because mapd and SCC import it for coordinate
math". It also contains:

    parse_banner_instructions()   the MAPBOX BANNER PARSER -- primary/secondary text, type,
                                  modifier, and lane components with active + directions
    distance_along_geometry()     how far along a polyline the car is
    minimum_distance()            point-to-segment distance
    coordinate_from_param()       reads a destination out of a param
    maxspeed_to_ms(), string_to_direction()

**So the PARSING layer of comma's navd is still here, working, and unused.** Anyone reviving Mapbox
routing on this fork would find most of the translation already written. That is a materially
different starting position from "nothing to build on", which is what section 8 currently implies.

**And `system/athena/athenad.py` still has `setNavDestination`**, an RPC that writes a
`NavDestination` param -- so the destination-ENTRY path survived too, over the cloud connection.

**BUT IT IS BROKEN HERE: `NavDestination` is not declared in `common/params_keys.h`.** Params raises
on unknown keys, so calling that RPC on this fork would throw. Not ours to fix by the upstream-scope
rule -- it cannot affect his car unless something calls it -- but worth knowing before anyone plans
to use it as the destination-entry mechanism.

---

## 3. THE PRODUCTION SYSTEMS, AND THE ONE THING THEY ALL SHARE

| | route source | map dependence | what the route DOES |
|---|---|---|---|
| **Tesla** Navigate on Autopilot | its own nav | none required -- cameras + network, works on unmapped roads | takes exits, changes interchanges, auto lane change to pass |
| **GM** Super Cruise | its own nav | **LiDAR-mapped highways only**, ~400k miles NA | hands-free in the mapped zone; lane change is DRIVER-prompted |
| **Ford** BlueCruise | its own nav | prequalified Blue Zones | hands-free in zone |
| **comma** NoA (removed) | Mapbox via navd | none | fed the model; withdrawn because the model could not execute |

**EVERY ONE OF THEM USES ITS OWN ROUTER.** That is the structural fact, and it is why none of them
had the problem this fork has spent a week on: they never needed a third party to expose a route.

**HIS DESIGN IS THE ODD ONE OUT, DELIBERATELY, AND HE IS RIGHT ABOUT WHY.** His words:
*"I just love waze navigation"*, and earlier, *"Waze is already running on my phone and going to
Android Auto, you know?"* A router he already uses, already trusts, and that already reroutes around
traffic, at zero marginal cost per drive.

    what he gives up   an API. Waze exposes no route, which is the entire cause of the transport
                       problem -- see ROUTE-INTENT sections 2a and 9.
    what he gains      the route he is ACTUALLY going to drive, from a router that knows about the
                       accident ahead, with no destination to enter before every trip.

**That trade is the design**, and it is worth restating because the tempting "fix" is to drop Waze
for Mapbox and inherit everyone else's architecture. That buys an API and loses traffic-aware
routing and the zero-effort property, and the zero-effort property is the one that decides whether a
feature is used at all -- the same reasoning that killed "run this on your next drive" as a
diagnostic style here.

**AND SUPER CRUISE IS THE CAUTIONARY ONE.** It is calm and predictable inside its LiDAR-mapped zone
and simply unavailable outside it. That is the map-as-PERMISSION architecture this fork already
rejected in writing (*"BlueCruise always requires map data... no map costs COVERAGE, never
SAFETY"*). The industry's most polished map-based system is the clearest example of what he does not
want.

---

## 4. THE FORKS -- AND A CORRECTION TO OUR OWN DOCUMENT

**FrogPilot** ships *Primeless Navigation*: full turn-by-turn using the user's OWN Mapbox keys
(public and secret), with downloadable offline regions for routing and tiles.

**AND ITS SPEED LIMIT CONTROLLER TAKES THREE SOURCES: downloaded OpenStreetMap, online Mapbox, AND
THE VEHICLE'S DASHBOARD** -- plus a "Speed Limit Filler" that logs limits from the dashboard, Mapbox
and navigate-on-openpilot while driving. So *reading the car's own dashboard speed limit as an SLA
source is established prior art*, not a novel idea. That is directly relevant to the open question
here about the 25 mph on his cluster.

**THE CORRECTION.** `ROUTE-INTENT.md` section 9c says FrogPilot *"will keep left or right
appropriately at forks and exits"* and concludes **"The CONSUMER side is proven. A fork already
turns route intent into lane positioning, which is exactly what he wants passing assist to do."**

**That overstates it.** FrogPilot's own wiki says that when navigating on openpilot, **lane change
behaviour is unchanged and still activated by the driver.** What FrogPilot varies is lane
POSITIONING -- where in the lane the car sits, including at forks and exits and with uncertain
lanelines -- which is a different and much smaller thing than deciding to change lanes.

So the honest statement is: **no fork has proven the consumer side of what he wants.** FrogPilot
proves route intent can influence lateral positioning. Nobody has shown a fork making an autonomous
lane-change DECISION from route intent, which is what passing assist would eventually be.

**CarrotPilot** remains as recorded in 9c: an Android "Navigation Data Bridge" ingesting AMAP,
Tencent and Google Maps and feeding the fork. It proves the phone-bridge TRANSPORT. It does not
bridge Waze, for the reason that has driven this whole investigation.

---

## 5. WHAT TO ACTUALLY TAKE FROM THIS

1. **Stop worrying that comma's removal is a bad omen.** They withdrew the model-driven half because
   the model could not drive it. We are not asking a model to do anything.
2. **Prefer route GEOMETRY over a street name** if a transport ever offers it. `NavRoute` is a
   polyline; map-matching a polyline beats fuzzy name joins at exactly the interchanges where the
   name join is worst. This supersedes the approach sketched in ROUTE-INTENT 11f.
3. **Consider carrying more than one maneuver** if the transport has them. comma carried
   `allManeuvers`; Android Auto will not give us that, but the car's own CAN might.
4. **A nav-sourced speed limit is normal, not exotic.** comma modelled it and FrogPilot ships it
   from three sources including the dashboard.
5. **`parse_banner_instructions` already exists in this tree** if Mapbox ever becomes the fallback
   transport. That path is much shorter than section 8 implies.
6. **Do not quietly adopt everyone else's architecture.** Using our own router would solve the
   transport problem and lose the two properties he actually chose Waze for.

---

## 6. WHAT CARROTPILOT'S NAV BRIDGE ACTUALLY SENDS

Read from `ajouatom/openpilot` (the canonical CarrotPilot) on 2026-08-23 -- schema and transport,
primary source. This is the closest working prior art to a phone bridge, and it is worth more than
the one-line summary in ROUTE-INTENT 9c.

**There are TWO generations of schema in that tree**, which is itself informative -- they outgrew
the first one.

### 6a. `CarrotMan` -- the flat legacy struct

One message, 29 flat fields: `xTurnInfo` (an Int32 turn code), `xDistToTurn`, `xTurnCountDown`,
`xSpdType/Limit/Dist/CountDown` (speed-camera zones), `nRoadLimitSpeed`, `szPosRoadName`,
`szTBTMainText`, `nGoPosDist`/`nGoPosTime` (to destination), `xPosLat/Lon/Angle/Speed` (the PHONE's
own position), `naviPaths` (route geometry, as Text), and `desiredSpeed` + `desiredSource`.

### 6b. `CarrotNaviState` -- the structured one, and it is well designed

    schemaVersion, generation, sessionId, publishMonoTimeNanos, connected
    vehicle          lat/lon, heading, speed, roadName, virtualGps
    guidanceCurrent  } distanceM, timeSec, turnType, roadName, mainText,
    guidanceNext     } near/mid/farDirection, pointValid + latitude/longitude
    laneCurrent, laneAhead[]   count, currentLane, turnInfo[], available[]
    speed            road limit, SDI zones, section-average enforcement, secondary SDI
    trafficSignal    red/green/left/right/uturn, each with REMAINING SECONDS
    crossroad        junction image
    route            remaining/moved/total distance and time, polyline :List(Coordinate)
    navigationStatus mode, guidanceActive, offRoute, routePresent

**And EVERY sub-struct carries an `ItemMeta`:**

    present @0 :Bool
    sequence @1 :UInt64
    sourceTimestampMillis @2 :UInt64     when the APP produced it
    receivedMonoTimeNanos @3 :UInt64     when the DEVICE received it

### 6c. THEY ARRIVED AT OUR FRESHNESS DESIGN INDEPENDENTLY, AND WENT FURTHER

`receivedMonoTimeNanos` is exactly `RouteIntentBP.observedMonoTime`, and `present` is exactly
`distanceKnown`. Two projects, no contact, same two conclusions: **stamp on the DEVICE at receipt,
and let a field say it has no value rather than sending a zero.** That is the strongest possible
corroboration of the two design choices in our schema that were argued from first principles.

**Three ways theirs is better, and all three are cheap to steal if a transport ever needs them:**

- **`ItemMeta` is PER ITEM, not per message.** Guidance can be fresh while lane data is stale. Our
  single-instruction message does not need this, but a CAN transport delivering several fields at
  different rates would.
- **`sequence`** catches dropped and reordered frames. We have no equivalent.
- **BOTH timestamps.** Source time AND receive time lets you measure the link's own latency rather
  than just its staleness.

### 6d. FOUR THINGS THEY CARRY THAT WE CANNOT REPRESENT AT ALL

**`offRoute` IS THE ONE THAT MATTERS AND IT IS A REAL HOLE IN OUR SCHEMA.** It is the state where
the instruction is perfectly FRESH and nonetheless wrong, because he has left the route the
navigator was following. **Our freshness rule cannot catch it** -- the stamp is current, the
maneuver is populated, and the whole thing refers to a route he abandoned. It would cause a spurious
refusal until the app reroutes, which is benign and brief, but it is a state we have no way to
express and would never think to ask about. Record it for whoever builds the transport; do not add
the field until something reads it.

**`guidanceNext`** -- a second maneuver. That is now THREE independent systems carrying more than
one (comma's `allManeuvers`, Android Auto's `steps` list, this pair).

**`Guidance.latitude/longitude` with `pointValid`** -- the COORDINATES of the maneuver point, not
just a distance to it. Directly map-matchable against the tiles, with no name join at all.

**`Route.polyline`** -- the full route geometry, and the THIRD independent system to carry it after
comma's `NavRoute`. The convergence is the argument: everyone who builds this ends up shipping the
route as coordinates, which is why section 2 recommends preferring geometry over `Step.road`.

### 6e. WHERE WE DELIBERATELY DIVERGE, AND SHOULD STAY DIVERGED

**CarrotPilot consumes an app-computed TARGET SPEED** -- `desiredSpeed` with `desiredSource`, plus
`vTurnSpeed` (a recommended speed for the upcoming turn). **This fork refuses exactly that shape of
input**, in writing, for mapd's `suggestedSpeed`: it cannot know about ICBM's button presses, about
holds, or about the four SCC-Map defenses built from measured events here, and
`test_mapd_schema.py` fails the build if any decision-making file reads it.

So the divergence is deliberate and already argued. Take CarrotPilot's INGREDIENTS -- maneuver,
distance, geometry -- and never its answer.

**And their `trafficSignal` with per-phase remaining seconds is a reminder of how much a rich bridge
CAN carry** (that is Korean nav apps publishing live signal countdown). It would be transformative
for the stop override. It is also not available on any source this car will ever have, so it is
noted and dropped.

### 6f. THE TRANSPORT, which is the practical part

    UDP broadcast :7705    the DEVICE advertises itself
    TCP           :7706    the main channel
    HTTP          :7713    aiohttp server for nav data

**The device broadcasts and the phone discovers it, not the other way round.** That is the answer to
a question our own phone-bridge sketch never asked -- how the phone finds the comma on a WiFi network
whose addressing changes constantly. Worth copying if a bridge is ever built here.
