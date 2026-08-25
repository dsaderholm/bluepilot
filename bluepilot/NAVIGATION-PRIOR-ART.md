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

Read from `ajouatom/openpilot` (the canonical CarrotPilot) on 2026-08-23 -- primary source.

**THIS SECTION IS NOT A PROPOSAL TO BUILD A PHONE BRIDGE, and it was misread as one within minutes
of being written.** He asked what CarrotPilot sends; this is the answer. **The plan is unchanged:
MS-CAN via the canbox. No phone, no Android app.** He ruled that out at the start of this work and
nothing here reopens it.

**What it is actually worth is SCHEMA evidence, which is transport-agnostic.** CarrotPilot is the
only working system whose nav message we can read line by line, and the questions it answers --
how to express freshness, how to say "I have no value for this", whether to carry one maneuver or
two -- apply identically to a message filled from CAN. Read 6c and 6d for that. 6f is the only
phone-specific part and is recorded for completeness, not as a direction.

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

**The device broadcasts and the phone discovers it, not the other way round** -- which answers a
question our own fallback sketch never asked, given how often his network changes.

**RECORDED, NOT RECOMMENDED.** The phone bridge is the fallback in ROUTE-INTENT 9a and it is not
being built. This paragraph exists so that if the canbox path ever dies, the next session does not
re-derive the discovery problem from scratch. It is not a reason to start.

---

## 7. WHAT "NAVIGATE ON OPENPILOT" WOULD ACTUALLY TAKE ON THIS CAR

His, 2026-08-23: *"Eventually using all of this to navigate on OpenPilot would be pretty cool..."*
Worth answering with the dependency chain rather than a yes, because the chain is short and one item
appears in three places.

**Take the narrow case that matters -- following a route off a freeway at his exit.** Four things
have to work:

    1  KNOW the exit is his          route intent            needs the CANBOX
    2  MOVE to the exit lane         passing assist maneuver  needs the CANBOX (BLIS + rear radar)
    3  SLOW for the ramp, 65 -> 38   ICBM set-speed descent   works TODAY, given lead time
    4  SLOW below 20 mph             op long + passthrough + stop override -- the hard one

**THREE OF THE FOUR ARE THE SAME PIECE OF HARDWARE.** The canbox is not merely route intent's
blocker; it is the common blocker. That is worth knowing before anyone plans around any one of them
separately.

**Item 3 already works and is the one route intent directly fixes.** The measured exit failure is
DETECTION TIME -- SCC-Map gets the corner about 4 s out and the set speed needs about 8 s at
3.3 mph/s. Route intent supplies the missing seconds by knowing the ramp is his before it is in
sight. Nothing new has to actuate.

**Item 4 is the genuinely hard one and it has scar tissue.** ICBM floors at 20 mph -- Ford's floor,
confirmed by him -- so the end of a ramp, where there is a stop sign or a 25 mph curve, is beyond
what buttons can ask for. That is exactly what the stop override was built for, it requires op long
plus the ACCDATA passthrough, and it has already cost him Ford ACC twice with a camera cancel that
never released. Do not fold item 4 into an exit-taking feature casually.

### 7a. AND HIS CAR MIGHT DO THIS BETTER THAN COMMA'S VERSION DID, which is the non-obvious part

Section 1 records why comma withdrew navigate-on-openpilot: **the driving MODEL could not execute
the maneuvers.** They fed route information into the model and asked it to drive the route.

**This car would not do it that way.** The lane change would be passing assist's explicit maneuver
state machine -- signal, gate, cross, settle, with named abort conditions -- and the slowing would
be ICBM's set-speed descent. Both are written code with measured thresholds, not a learned policy
being asked to generalise.

That is this fork's own model-versus-code rule applied to the biggest feature it could be applied
to: **taking an exit is POLICY and CAR FACTS, not perception.** Where to be on the road and whether
the lane is clear are the model's job and it already does them. Deciding to leave the freeway,
choosing when to move, and knowing the set speed falls at 3.3 mph/s are not things a model should be
asked to learn.

**So the failure that stopped comma is not the failure this car would hit.** Its risks are different
and mostly enumerated already: the canbox, the 20 mph floor, and the camera's tolerance for
contradiction. None of them is "the model cannot drive it".

**None of which makes it near.** It is four dependencies deep and two of them are somebody else's
hardware. The point of writing it down is the ORDERING: route intent first because it is the only
one buildable today, item 3 as the first payoff because it needs no new actuation, and item 4 last
and separately, on its own evidence.

---

## 8. A WORKED EXAMPLE: HIS OWN 9.2 MILE ROUTE, BROKEN INTO REACHABLE AND NOT

He shared a real Google Maps route on 2026-08-24 -- 2513 S 1500 E to 6515 S Lion Ln, 12 min,
9.2 miles -- with *"imagine if we could get to the point where it could do this"*. It is a good
route to reason with because it contains every element, and it separates cleanly.

    0.8 mi   head to Parkway Ave, LEFT onto Parkway, RIGHT onto S 1300 E     NOT REACHABLE
    0.3 mi   right to merge onto I-80 E                                       merge -- see below
    7.8 mi   I-80 E -> I-215 S interchange -> EXIT 6                          THE TARGET
    0.6 mi   6200 S / Big Cottonwood -> S 3000 E -> S Lion Ln                 NOT REACHABLE

**THE FREEWAY SPINE IS 7.8 OF 9.2 MILES -- ABOUT 85% OF THE DISTANCE**, and 7 of the 12 minutes.
That is the part this fork has been building toward, and stating the fraction is useful because
"door to door" and "the useful majority" are very different targets and only one of them is on the
table.

**THE TWO ENDS ARE A CAPABILITY GAP, NOT A ROUTE-INTENT GAP.** They need turns at intersections from
a stop -- judging cross traffic, committing to a 90 degree turn, choosing a gap. openpilot does not
do that, this fork is not trying to, and no quantity of navigation data closes it. Say so plainly
whenever this comes up, because a route-intent conversation naturally drifts toward door-to-door and
the drift is not supported by anything.

**WHAT THE MIDDLE ACTUALLY ASKS FOR, and it is three things:**

    1  the canbox                      route intent at all
    2  route intent                    "exit 6 is his", early enough to matter
    3  A LANE-SELECTION DECISION       "be in the exit lane" -- NOT BUILT

**Item 3 is the honest correction to the optimism.** Passing assist's maneuver machinery -- signal,
gate, cross, settle, abort -- is written and tested and would carry the move. But the DECISION that
drives it asks one question: *is there a slower car ahead worth passing.* "Get into the right-hand
lane because we leave the freeway in 600 metres" is a different question using the same machinery.
That is real work, not wiring, and nothing here should imply otherwise.

**And exit 6 is exactly the case the whole design exists for.** Route intent says the ramp is his,
SCC-Map commits to the ramp's corner speed with the ~8 s the set speed needs instead of the ~4 s it
gets today, and the descent completes. That is the exit-ramp problem, measured on route 00000348,
solved on a specific exit he actually drives.

---

## 9. INTERSECTION TURNS FROM A STOP: NOT "NOT YET". NOT SENSABLE ON THIS CAR.

He asked, 2026-08-24, how other systems do the thing section 8 wrote off. The answer firms the
scope boundary up considerably, because it turns a shrug into a reason.

**FIRST, THE DISTINCTION THIS SECTION MUST NOT BLUR, because he checked: GOING STRAIGHT THROUGH AN
INTERSECTION IS FINE AND ALREADY WORKS.** The car stays on the same road, nothing crosses its path
that it has to judge a gap against, and it is therefore ordinary lane keeping -- which MADS does on
every drive. Nothing in this section applies to it.

    straight through   lane keeping. Works today. In scope.
    TURNING            gap acceptance against cross traffic. The subject of this section.

The one thing a straight-through intersection still asks for is STOPPING at a red or a stop sign,
and that is not a perception problem either -- it is the 20 mph ICBM floor and the stop override,
which are tracked separately in CLAUDE.md.

**So "surface streets are unreachable" would be the wrong summary of section 8.** A surface street
with no turns on it is drivable now. What is unreachable is the TURN.

### 9a. It is the hardest thing in the field, for everyone, and the reason is OCCLUSION

An unprotected turn -- crossing or joining traffic that has right of way -- comes down to gap
acceptance under uncertainty, and the research framing is blunt: **the worst case is a through-moving
vehicle that is just beyond the field of view at the moment the decision is made.** You cannot
choose a gap you cannot see, and the gap you cannot see is the one that hurts.

The two failure modes are opposite and both are reported in the field:

    too eager   Tesla FSD creeping forward at the wrong moment, and owners reporting it cannot
                make safe unprotected turns onto high-speed roads. Cited range around 80 m, which
                is not enough against 55 mph cross traffic.
    too timid   Waymo stopping dead in an intersection and blocking it.

**And even Tesla's answer is a hardware answer.** They mount B-pillar cameras and repeaters
specifically to look down the cross street, and owners still complain about occlusion, because a
fixed camera cannot duck, lean and peek around an obstruction the way a driver does. Creep exists
precisely to buy visibility that the sensors cannot get from the stop line.

### 9b. THIS CAR HAS NO LATERAL PERCEPTION AT ALL

That is the whole answer, and it is not about software maturity.

    IPMA camera        forward, behind the windshield
    Delphi MRR radar   forward
    comma device       forward -- wide and narrow road cameras
    rear radar         BEHIND, and not fitted yet
    side / corner      NOTHING

**An unprotected turn needs to see roughly 90 degrees left and right, down the cross road, past the
A-pillar, to a distance that covers cross traffic at speed.** Nothing on this car looks there. The
wide road camera has real horizontal reach but it is mounted behind the windshield looking forward,
so the extreme edges of its field are exactly where resolution is worst and where the pillar and
door frame occlude -- and 90 degrees is past the edge, not at it.

**So this is a SENSOR gap, not a model gap.** No amount of navigation data, no model update and no
amount of work in this repo closes it. Closing it means side-facing cameras or corner radar, which
is hardware nobody sells as a bolt-on for this stack.

### 9c. AND THAT IS EXACTLY WHY THE FREEWAY SPINE IS THE RIGHT TARGET

Worth stating, because it makes the scope look chosen rather than merely convenient.

**On a freeway, everything that matters is ahead or behind, in the same direction of travel.**

    ahead    forward camera + forward radar        fitted, and heavily used already
    behind   rear radar                            arriving with the canbox
    beside   adjacent-lane tracks from the front radar, plus BLIS with the canbox

**The freeway spine IS the domain this sensor set covers.** The fork did not pick it and then
discover the sensors happened to suit; the sensors define the domain and everything here has
converged on it -- passing assist, the rear digest, the oncoming veto, route intent.

Intersections are the domain where the sensor set is blind, and that is the honest line between
what to build and what to leave alone. **Do not revisit intersection turns as a software problem.**
