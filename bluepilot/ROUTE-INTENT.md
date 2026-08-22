# Route intent: can openpilot know where he is GOING?

Researched 2026-08-17 for the passing assist session. The question came from him, and the motivation
is the right one -- he misses Waze putting turn-by-turn on his IPC, and asked for it back *"so
OpenPilot could know more about where I'm going, like if I am going to take an exit or not."*

**Short version: the phone cannot supply it, the car barely can, and the one usable signal is already
on the wire and has never been looked at.**

Everything below is marked MEASURED or UNVERIFIED. **Several claims in this area were made
confidently and were wrong within hours**, each by reasoning past the edge of what had been checked.
Every correction is kept in place rather than quietly fixed, because in each case the WRONG version
was the more persuasive one and would have been re-derived otherwise. Two came from him and one from
the passing assist session; none were found by thinking harder.

---

## 1. Why it matters, stated as a number rather than a feeling

Two features want the same fact and neither has it.

**The exit-ramp problem** (CLAUDE.md, "THE EXIT THAT NEVER SLOWS ENOUGH"): measured on route
00000348, SCC-Map asked for the corner **4 seconds** before peak cornering. The set speed falls at
about **3.3 mph/s** and cannot go faster, so a 65 -> 38 mph exit needs roughly **8 seconds** of
set-speed travel. The deficit is detection time, and it splits in two:

- **Availability** -- was the corner in the data yet at all. `mapdExtendedOut.path` may already fix
  this by carrying the profile ahead instead of v1's single step. UNVERIFIED, needs an observe drive.
- **Ambiguity** -- at a fork, is the ramp his road or is the mainline? Committing to the ramp's
  corner speed early is only safe if he is taking it. **This is what route intent would answer.**

**For passing assist** it is the same fact wearing a different hat: "do not offer a pass approaching
an exit" needs to know whether the exit is his.

---

## 2. Android Auto cannot supply it. This is plumbing, not policy.

**MEASURED / structural.** Android Auto navigation metadata travels:

    phone -> Android Auto -> SYNC (APIM) -> instrument cluster

On Ford that last hop is **MS-CAN**. The panda is wired to bus 0 (powertrain) and bus 2 (camera).
openpilot would never see a byte of it, however well any app implemented it.

**What it carries is a turn arrow: a maneuver, a distance, a destination name.** The API is
`NavigationManager.updateTrip()`, communicating a `Trip` of `Step` and `Destination`, with the note
that "the information provided in this call can be used by the vehicle's cluster and heads-up
displays."

**AN EARLIER VERSION OF THIS DOCUMENT CALLED THAT "THINNER THAN IT SOUNDS" AND ARGUED THAT
`mapdExtendedOut.path` ALREADY GIVES CURVATURE, SO THE ARROW ADDS LITTLE. THAT IS BACKWARDS**, and he
caught it: *"Where the ramp is, how it curves, and what speed it takes are handled by mapd v2..."*

The two are COMPLEMENTARY, not competing. mapd matches POSITION to ways. It has no destination and
structurally cannot have one -- which is exactly why `waySelectionType: predicted` is a guess from
path continuation rather than knowledge. **The arrow is thin precisely BECAUSE it is the complement:
the one bit the map layer cannot supply.** So thinness is not an objection to it at all, and the
original argument would have led the next reader to believe the data would be useless even if it
arrived.

**The blocker is TRANSPORT, and only transport.** That argument stands on its own and needs no help:
phone -> AA -> SYNC -> cluster, last hop MS-CAN, panda on bus 0 and 2. The conclusion below is
unchanged; the reasoning that used to support it was wrong.

### 2a. The Waze situation, since it will come up again

Two different features are being reported under the same headline and the coverage conflates them:

| | mechanism | who renders | his car |
|---|---|---|---|
| **`updateTrip()` metadata** | app publishes maneuver/distance | the CAR, in Ford's own IPC style | **this is what he had, and lost** |
| **Cluster rendering**, Car App API Level 6 | app draws its own map, declares `androidx.car.app.category.FEATURE_CLUSTER` | the APP | needs SYNC 4/4A; his is SYNC 3 |

Waze 5.4.0 (Feb/Mar 2025) shipped the second one, CarPlay first. Waze's own help page says
"Waze navigation is also available on the instrument cluster display of supported vehicles" on
Android Auto -- true for the new feature, useless for a SYNC 3 car.

**His symptom is a compass**, which is what a Ford IPC shows when an Android Auto navigation session
is active but no valid `Trip` metadata is arriving. That is the signature of `updateTrip` not being
called, or called without Steps.

**The discriminator he is running: does Google Maps still show turns on his IPC?**

**IT IS ONLY VALID IN ONE DIRECTION, which an earlier version of this got wrong.** The Android Auto
regression is NOT uniform: Google Maps is the most commonly affected, Waze is reported broken by some
owners and working by others, and HERE WeGo keeps working on several models. So "Waze is dead" is
expected under BOTH explanations and proves nothing on its own.

    Maps ALSO dead  ->  the Android Auto regression. Diagnostic.
    Maps still works ->  suggestive of Waze's implementation, NOT conclusive, since the regression
                         hits apps unevenly and could be sparing Maps on this head unit.

**THE DISCRIMINATOR CAME BACK, 2026-08-22, AND IT POINTS AT WAZE.** His words: *"Google Maps still
shows turns on my IPC!"*

    Maps ALSO dead   -> the Android Auto regression
    Maps still works -> suggestive of WAZE'S IMPLEMENTATION      <- this is his car

**So the Maps v25 test below is now moot** -- there is nothing to revive, Maps was never broken here.

**AND IT REVERSES WHAT TO DO.** Waze Support asked him for debug logs in the app's debug mode
(search `2##2`, then Report -> Debug), and the working assumption had been that this was the wrong
channel because the bug was Android Auto's. With Maps working, Waze's own request is the RIGHT
channel: they would be collecting logs for a bug that looks like theirs, from a car that reproduces
it. Sending them is now the sensible move rather than a detour.

**THE BETTER ROUTE IS THE WAZE BETA PROGRAM, AND THIS IS THE THING THAT WAS NEVER WRITTEN DOWN.**
He remembered it and this document did not have it -- it survived only as a bare URL in the source
list at the bottom, with no statement of what it was for. On 2026-08-22 he asked *"do you remember
what we were doing with that?"* and the honest answer was no, because a link is not a record.

    https://support.google.com/waze/beta/answer/7576732   Start Testing Waze on Android Auto

**Why the beta channel beats the support ticket**, which is the reasoning that was lost:

- Beta builds carry their own in-app bug reporting, and those reports land with the team that owns
  the Android Auto integration rather than with front-line support triage.
- The support reply he got is a generic template -- it asks for username, device model, car make and
  the `2##2` debug dance, and it opens by asking him to update to the newest version, which is the
  first thing a triage script says. A beta report skips that loop.
- If the fix already exists unreleased, the beta is where it shows up first, so joining answers
  "is this fixed" as a side effect of reporting it.

**Cost, stated because it is the reason not to:** beta builds are less stable, and it is a
navigation app he relies on. It is reversible -- leave the program and reinstall the release build --
but that is the trade.

**AND THE PREMISE IS REFUTED, SAME DAY, BY HIM ACTUALLY DOING IT.** He submitted through the app and
the reply was the **identical template** -- same agent, same steps, and the same thread identifier
`thread::gmI37q7JcPaPnyjfsPIBMSs::` as the support ticket. So the in-app route did NOT reach a
different team; it landed in the same front-line queue and produced the same `2##2` debug-log
request.

**The "beta reports skip triage" reasoning above was wrong**, and it is left in place rather than
deleted because it is the persuasive version and would be re-derived. What is actually true, from
one attempt:

    support ticket   -> template asking for username, device, car, AA version, debug logs
    in-app report    -> the SAME template, the SAME thread

**So there is one channel, and it wants the debug logs.** If this is pursued at all, the move is to
answer the template once: the `2##2` debug-mode capture with the drive reproduced, plus username,
device model, car (2020 Ford Fusion, SYNC 3), AA version, and timestamps. Bounded work, one round.

**The scope verdict is unchanged and now doubly earned.** This is a cluster nicety, nothing here is
sequenced behind it, and it has now cost two rounds of research and one live attempt. **Do not spend
a third.**

**A note on how the earlier reasoning went wrong**, since the shape recurs: "beta programs have
direct developer channels" is true of many products and was applied to this one without a way to
check it. That is the same move as reading `roadEdgeStds` as a 0..1 scale from a rendering clamp, or
inferring a US as-built would help TSR -- **a plausible general fact, asserted about a specific
system nobody had tested.** He tested it in one afternoon.

**THE LOGGING RULE FAILED HERE, one day after it was written.** CLAUDE.md's "LOG IT THE SESSION IT
ARRIVES" says a fact from outside the code gets transcribed, put where it will be used, with source
and date. This was reasoning produced IN conversation and it left behind only a citation. **A source
link is not a decision. If a link is worth keeping, the sentence saying what it is for is worth
keeping with it.**

**Still NOT conclusive**, and the reason is already in this section: the Android Auto regression hits
apps unevenly and could be sparing Maps on this head unit. What his test rules out is the strongest
version of the "it is not Waze's fault" story, which is what the debug-log request was being weighed
against.

**And it changes nothing about scope.** This remains a cluster nicety -- see the paragraph below --
and no work here is sequenced behind it.

**A stronger test exists, and it is actionable:** a reported workaround is downgrading Google Maps to
**version 25**, which restores HUD navigation and points at the bug arriving with Maps 26. If Maps is
dead on his car and v25 revives it, that pins it to the known regression conclusively. REPORTED,
not verified here. **Superseded for his car by the paragraph above: Maps is not dead here.**

On Google's position: what exists is a Google Community Specialist on a support forum saying the dev
team has been informed and a fix is in the works. **No timeline, no version, no release note.** An
earlier version of this document said "Google has said is being fixed", which reads as a commitment
it is not.

**Either way it does not unblock anything here.** Worth wanting for his cluster; worth zero as a
feature dependency. **Do not sequence any work behind it.**

### 2b. If "which branch" is the only missing input, it need not come through the car at all

Worth stating because it follows directly from the correction above: the blocker is transport, so a
transport that avoids the car is the shape of any answer.

**phone -> WiFi -> comma device** never touches SYNC, MS-CAN, the cluster or the canbox, and
sidesteps all four links in the chain below.

**The catch is the same wall in a new place.** Waze exposes no route to third parties. The usual read
is Android's persistent navigation notification, which returns to exactly the publishing question
that produced his compass -- if the app is not emitting, there is nothing to scrape. Google Maps is
the likelier source there.

NOT PROPOSED, and nothing here should be sequenced behind it. Recorded so the next person reaches for
the right shape rather than re-deriving that the bus is a dead end.

---

## 3. The canbox -- open, and worth answering for other reasons

**UNVERIFIED, and explicitly not to be assumed.** Only BLIS routing is confirmed. Nothing about the
canbox is documented anywhere in the repo.

His question was whether it could route MS-CAN to a bus we see, the way he is doing for BLIS. The
answer splits:

- **If it decodes a FIXED LIST** (doors, wheel buttons, reverse, BLIS) into a head-unit protocol --
  which is what most aftermarket canboxes are -- then no, and there is no setting that changes it.
- **If it can MIRROR ARBITRARY CAN IDs** onto another bus, that is a general capability and it
  matters fork-wide. CLAUDE.md currently records that openpilot can read body state on bus 0 and can
  never reach MS-CAN, and several ideas have died against that line.

**But it does not rescue the Waze path, for a reason that has nothing to do with the box:** routing
cannot carry data that is not being sent. The compass is the evidence that Waze is publishing
nothing, so SYNC has nothing to translate onto MS-CAN, so a perfect canbox would forward an absence.
The full chain would need Waze to fix it, SYNC to emit it, the box to forward it, and us to parse it
-- four links, with the first outside his control.

**Ask for the general answer anyway** when it is installed: fixed-list decode, or arbitrary mirroring?

---

## 4. The APIM, measured -- and a correction that matters for scope

`tools/bp_apim_probe.py`, run 2026-08-17 on three independent routes (0000037d, 00000379, 00000378):

    0x462  APIMGPS_Data_Nav_1  lat/lon/hemispheres              present, ~1 Hz, forwarded to bus 2
    0x463  APIMGPS_Data_Nav_2  UTC, PDOP, compass, GPS fault    NOT PRESENT
    0x464  APIMGPS_Data_Nav_3  speed, heading, alt, HDOP, sats  NOT PRESENT
    0x32B  APIM_Data_FD1       light menus, stopover distance   NOT PRESENT

Controls (`Traffic_RecognitnData`, `ACCDATA_3`, `Steering_Data_FD1`) were healthy on every run.

**So the APIM sends POSITION AND NOTHING ELSE on the powertrain bus.** Not silent, not whole. What
the camera is missing is precisely the quality half of a fix -- the fault bit, all three DOP figures,
satellites in view, actual-vs-inferred position, and the UTC timestamp. A consumer handed coordinates
it cannot validate or age has every reason to declare them unusable, which is what
`NoNavDataAvailable` says. That reframes `U0253` as a **partial feed** rather than a dead module.

**Not a usable GPS source for us**, incidentally: 1 Hz against the comma's 10 Hz, and with Nav_2/Nav_3
absent there is no accuracy estimate of any kind. A position source with no quality signal is worse
than one with fewer decimals.

### THE CORRECTION, because it is the kind of mistake that propagates

An earlier version of the TSR document said "**No speed limit. No road class. No route geometry** --
the APIM broadcasts none of it, and that question is closed." **That was a claim about ONE BUS stated
as a fact about the car.** The search covered `ford_lincoln_base_pt.dbc`, the powertrain bus. His
car's MS-CAN is modeled by no DBC in this repo.

He broke it with one question: *"then how has my IPC shown speed limit before I even got my new
IPMA?"* It did, so a path existed. And section 4b of the TSR document was already direct evidence:
the IPMA carries a **"TSR data source"** setting whose options include **Camera + APIM**, and
selecting it cleared `NoNavDataAvailable` immediately. A module cannot take TSR data from a source
that sends none.

**Absence from the bus we model is not absence from the car.** Apply that everywhere in this
document, and to the canbox question above.

**One upside worth knowing:** the camera FUSES its sources and republishes in `Traffic_RecognitnData`
(0x3CD) on bus 2, which this fork already parses. If "Camera + APIM" can be made to persist, SYNC's
map limits would reach Speed Limit Assist through the camera, with openpilot never needing MS-CAN.

---

## 5. What IS available, ranked by cost

### 5a. `waySelectionType` already guesses, and nobody has looked

**This is the recommendation.** `mapdOut.waySelectionType` takes `current` / `predicted` / `possible`
/ `extended` / `fail`. **`predicted` is mapd doing exactly this job** -- inferring which way he is on
when it is not yet certain. It is published at **20 Hz** and logged in every observe route.

**MEASURED 2026-08-22, four drives, 63,000 mapdOut frames. IT IS ACCURATE AND IT IS USELESS.**

    route      predictions resolved   correct        lead time: median / best / worst
    000003ab            88             88  (100%)         1.0 s / 1.2 s / 0.5 s
    000003aa            77             75  ( 97%)         1.0 s / 2.7 s / 0.4 s
    000003a8            22             21  ( 95%)         1.0 s / 3.1 s / 0.9 s
    0000039f            76             73  ( 96%)         1.0 s / 1.9 s / 0.7 s

**Accuracy 95-100%. Lead time a median of ONE SECOND on every drive, best ever 3.1 s.** The budget,
from section 1, is about **8 seconds** -- a 65 -> 38 mph exit at 3.3 mph/s of set-speed travel. So
`predicted` delivers roughly an eighth of what the exit-ramp problem needs.

**This is why the tool reports lead time and not accuracy**, and the warning in step 2 was exactly
right: accuracy alone is a vanity figure here. A guess that is right but lands one second before the
road resolves it is not a prediction, it is a confirmation. `waySelectionType == predicted` tells us
which way he went at about the moment he went.

**Ramps specifically, which is the case that matters:** 25/25, 8/8 and 3/5 correct across the three
drives that had any -- and the same 1.0 s median lead. The blinker was `none` at resolution on 35 of
38, so it is not being helped by his signal either, consistent with 5c.

**SO 5a IS CLOSED AND 5b IS NOW JUSTIFIED BY EVIDENCE RATHER THAN BY ARGUMENT.** The cheap option was
worth measuring first and it is not enough; learning his own forks from repeated driving is the
remaining candidate, and it is the one that can be early because it does not wait for geometry to
disambiguate -- it knows before the fork is in sight.

**What this does NOT say:** that mapd is doing badly. Predicting a fork earlier than one second
requires knowing the destination, which mapd does not have and this car cannot supply -- that is
sections 2 through 4. mapd is answering a different question well.

### 5b. His own driving history, if 5a is not enough

He drives the same roads daily, and **the machinery already exists**:
`IcbmHoldObservations` learns "he sets 45 here" from repeated sightings and offers a pin once it has
seen enough. The same shape learns "at this fork he takes the ramp."

No phone, no Waze, no MS-CAN, no destination to enter, and it degrades to nothing on a road he has
not driven -- which is the correct failure.

### 5c. The turn signal confirms, it cannot predict

Genuine intent, and openpilot already sees it in `carState.leftBlinker` / `rightBlinker`. But it
arrives at the gore point, seconds after the decision needed to be made against an 8-second budget.
Useful to CONFIRM a prediction after the fact, and useless to make one. Note the scoring tool does
not need it: the resolved `wayId` labels each fork on its own.

---

## 6. The constraint any of this has to satisfy

From CLAUDE.md, and it is not negotiable: **the map is evidence, never permission.** Evidence that
OPENS a maneuver must never be cheaper than evidence that refuses one.

Applied to route intent:

- **A prediction MAY REFUSE freely.** "He is probably taking this exit, so do not offer a pass" costs
  a missed pass and nothing else.
- **A prediction MUST NEVER BE THE SOLE THING THAT OPENS.** "He is probably staying on the mainline,
  so the exit does not apply" cannot be the only reason a gate lets something through.

For SCC-Map the asymmetry is mild in our favor: slowing early for a ramp he does not take is a
comfort cost, not a safety one. For a passing gate it is not mild, and the rule above governs.

---

## 7. What to do, in order

1. **Drive.** The next drive is the first with `mapdOut` in it, and it answers the state-2 gate
   question, `predicted` quality, and several unrelated open items at once.
2. **Score it with `tools/bp_route_intent_score.py`** -- already written, on
   `passing-assist-phase1`. It scores `predicted` against the LATER RESOLVED `wayId`, which is
   self-labelling and needs no blinker for the core number, and it scores ramps separately.

   **It reports LEAD TIME, and that is the metric that matters.** Accuracy alone is a vanity figure
   here: a prediction that is right but only lands 3 s before the fork does nothing against an 8 s
   budget. Being right EARLY is the whole requirement.
3. **Only then** decide whether 5b earns its place.
4. **Separately, and not on this critical path:** ask what the canbox actually is.

---

## Appendix: a trap that cost two round trips

`mapdOut` was absent from all four newest routes, and that nearly went out as "observe mode is
broken". The newest route was recorded at 00:25; the device had booted at 03:54 and been parked
since. **Every route predated the build being asked about.** Combined with the documented fact that
v2 publishes nothing offroad, a parked device can neither confirm nor deny observe mode and will
support whichever conclusion you arrive with.

**Check `uptime` against segment mtimes before reading anything into a missing message.**

---

## Sources for the Android Auto / Waze findings

- [Build a navigation app -- Android for Cars](https://developer.android.com/training/cars/apps/navigation) -- `updateTrip`, cluster use, Car App API Level 6 rendering
- [Use Waze on Android Auto -- Waze Help](https://support.google.com/waze/answer/15113302)
- [Waze update brings maps to your car's instrument cluster -- 9to5Google](https://9to5google.com/2025/02/27/waze-update-instrument-cluster-display/)
- [Waze Rolls Out Instrument Cluster Integration -- BMWBlog](https://www.bmwblog.com/2025/03/01/waze-instrument-cluster-carplay-android-auto-update/)
- [Top Android Auto Feature Stops Working After Recent Update -- autoevolution](https://www.autoevolution.com/news/top-android-auto-feature-stops-working-after-recent-update-266075.html) -- the acknowledged AA cluster/HUD regression affecting both Maps and Waze
- [Start Testing Waze on Android Auto -- Waze Beta Help](https://support.google.com/waze/beta/answer/7576732) -- bug reporting and log submission

---

## 8. ACTUAL NAVIGATION -- destination, turn-by-turn. Measured 2026-08-22.

Everything above is about ROUTE INTENT: which branch, for the exit problem. He asked the separate
question -- *"I am thinking of actual navigation, eventually"* -- meaning enter a destination and get
turn-by-turn. Different feature, different answer, so it gets its own section.

**THERE IS NO NAVIGATION IN THIS FORK, AND NOTHING TO BUILD ON. Three layers, all empty:**

    comma        DELETED it. log.capnp carries navInstructionDEPRECATED @82,
                 navRouteDEPRECATED @83, navModelDEPRECATED @104. No selfdrive/navd/.
                 No NavDestination or Mapbox token in params_keys.h.
    sunnypilot   `sunnypilot/navd/` is ONE FILE, helpers.py -- a Coordinate class and geometry,
                 surviving only because mapd and SCC import it for coordinate math. No process.
                 `settings/navigation.py` is a stub: `items = []`, renders nothing.
    mapd         has no destination, route, maneuver or instruction concept anywhere in its
                 schema. It is a map MATCHER, not a router. `waySelectionType` is the whole of
                 its opinion about where you are going, and section 5a measured that at 1 s.

`helpers.py` still has `from_mapbox_tuple`, which is the fossil of comma's Mapbox-based navd. That
is the only trace left of a working implementation.

**THE TILES ARE THE REAL ASSET, AND THEY CANNOT ROUTE.** mapd already downloads OSM for his region
to `/data/media/0/osm/offline` and it is there today. What a tile holds, from
`tools/bp_offline_tile.capnp`:

    Way    name, ref, id, highwayClass, maxSpeed (+ forward/backward/conditional),
           lanes, oneWay, advisorySpeed, hazard, bbox, nodes
    Coordinates    latitude, longitude          <- AND NOTHING ELSE

**`Coordinates` has no node ID.** OSM ways connect by SHARING node ids, and that identity is not in
the file. So the tiles carry geometry and attributes but no junction topology, no connectivity, and
no turn restrictions -- the three things a router is made of.

**So offline routing here means RECONSTRUCTING THE GRAPH FIRST**, by matching way vertices that
coincide within some epsilon, across tile boundaries too (the `overlap` field exists for that). It
is possible and it is approximate, and it is least reliable exactly where it matters most: complex
interchanges, where several ways pass near each other without connecting and a float-coordinate
match cannot tell a junction from an overpass.

**HONEST DISTANCE, for the version he means:**

    a router                     the whole project. Graph reconstruction, or an ONLINE router
                                 (Mapbox / OSRM / Graphhopper) which skips it but needs a token
                                 and connectivity. comma's deleted navd was the online shape.
    destination entry            no UI exists. comma's came through the app and Connect.
    turn-by-turn transport       the nav messages are deprecated; new structs, or ours.
    rendering                    nothing draws a maneuver today.
    consumers                    SCC, SLA and passing assist would each want maneuver+distance.

**AND THE STRATEGIC POINT, so this is not started for the wrong reason:** navigation is a
heavyweight way to obtain a lightweight fact. Everything sections 1-7 want is answered by 5b --
learning his own forks from repeated driving -- with no router, no destination to enter, and no
network. **If the goal is the exit-ramp problem, build 5b. If the goal is turn-by-turn on the
screen because he wants turn-by-turn on the screen, that is a legitimate and separate want, and
this section is the map of what it costs.**

---

## 9. HIS ARCHITECTURE: WAZE DECIDES, OSM IS THE MAP. Stated 2026-08-22, in his words.

Section 8 measured what building navigation from scratch costs and concluded it is a heavyweight way
to get a lightweight fact. **That framing was answering the wrong question**, and he corrected it:

  *"my thought was to have Waze IPC data sort of drive passing assist to make turns and lane
   changes... Waze would drive the decisions and OSM would be the map."*

**THIS IS NOT "BUILD NAVIGATION". IT IS A SOURCE SWAP.** Nobody writes a router. Waze already routes
-- it knows traffic, closures and reroutes, which no offline OSM router on a comma ever will -- and
the tiles already on the device supply geometry, lane counts, classes and speed limits. What is
missing is only the WIRE between them.

That also disposes of section 8's main objection. The graph-reconstruction problem, the missing node
ids, the destination-entry UI: all of it belongs to building a router, and this design does not build
one.

### 9a. Two transports, and his priority order is explicit

  *"The notification interception is the fallback if we can't just get the data that Waze should put
   on the IPC."*

    1. WAZE IPC DATA  -- the updateTrip() metadata Ford's cluster renders. This is what he HAD and
       lost, and why the Waze bug matters. See 2a: his symptom is a compass, the signature of
       updateTrip not being called or called without Steps.
    2. NOTIFICATION INTERCEPTION -- the fallback. Read Waze's own navigation notification on the
       phone and ship it to the comma over WiFi.

### 9b. THE NOTIFICATION IS ALIVE, AND SECTION 2b SAID IT MIGHT NOT BE. He has a screenshot.

2b dismissed this path with: *"the usual read is Android's persistent navigation notification, which
returns to exactly the publishing question that produced his compass -- if the app is not emitting,
there is nothing to scrape."*

**That is now measured and it is wrong for this car.** His screenshot, 2026-08-22, shows Waze's live
navigation notification carrying a **maneuver icon (U-turn) and a distance (60 ft)**, updating.

**So the two paths fail INDEPENDENTLY.** Waze is not emitting to the cluster and IS emitting to the
notification, which means the fallback does not inherit the bug that killed the primary. 2b's
reasoning treated one publishing failure as evidence about a different publisher, and the screenshot
separates them.

**What the notification carries, from the screenshot:** app identity, a maneuver glyph, a distance,
and a freshness stamp. Enough for "there is a turn in 60 ft" -- which is the whole input passing
assist needs. What it does NOT carry is the road it turns ONTO, so joining it to a `wayId` is
inference, not a read.

### 9c. What other forks do -- surveyed 2026-08-22, because none of this needs inventing

| fork / project | what it does | relevance |
|---|---|---|
| **FrogPilot** | **Primeless Navigation**: full turn-by-turn with the user's OWN Mapbox keys, no comma prime. Destination via a web console on `:8082` or iOS Shortcuts. **Navigate-on-openpilot feeds route info to the MODEL**, and it *"will keep left or right appropriately at forks and exits"*. | **The CONSUMER side is proven.** A fork already turns route intent into lane positioning, which is exactly what he wants passing assist to do. |
| **CarrotPilot** (jixiexiaoge) | An Android **"Navigation Data Bridge"** for comma3. Ingests AMAP, Tencent and **Google Maps**, normalizes it and delivers it to the fork for NOO. Web console on port 7000. | **The TRANSPORT side is proven**, and it is precisely 9a's fallback shape: phone app scrapes a nav source, ships it to the device. Waze is not among its sources. |
| **twilsonco/OpenPilotSiriShortcuts** | iOS Shortcuts that set the openpilot DESTINATION from Waze, Google Maps or Apple Maps via the share sheet. | Sets a destination; does NOT stream maneuvers. Wrong shape here -- he does not want to re-enter a destination, he wants the live instruction. |
| **dragonpilot** | OSM speed limits, stop signs, road names. | Map data, not routing. Same layer this fork already has via mapd. |
| **comma** | DELETED. See section 8. | -- |

**Neither half is novel.** FrogPilot proves a fork can act on route intent; CarrotPilot proves a
phone can feed one. What nobody has done is use **Waze** as the source, and the reason is visible in
the table: CarrotPilot bridges AMAP, Tencent and Google Maps -- apps with usable outputs -- and skips
Waze, which exposes no route to third parties (2b).

### 9d. THE RECOMMENDATION: build the consumer first, behind a transport-agnostic interface

**Do NOT wait for comma.** He asked. They did not pause navigation, they REMOVED it -- `navd` gone,
`navInstruction`/`navRoute`/`navModel` all `DEPRECATED` ordinals. Deprecated ordinals do not come
back. This is the same shape as the mapd v1 situation already recorded in CLAUDE.md, where "wait for
upstream" was measurably false, and the same answer applies.

**Do NOT drop Waze either**, and the reason is the part that makes his design better than
FrogPilot's: a Mapbox route is a route through GEOMETRY. Waze's is the route he is actually going to
drive, because it reroutes around traffic. If the point is for the car to know which fork he takes,
the router that knows about the accident ahead is the one that predicts him correctly.

**But nothing may be SEQUENCED behind Waze**, and today is the evidence: he filed the bug and got a
triage template, twice, through two channels. So:

    passing assist consumes  ->  a maneuver + a distance + (later) a target wayId
                                 from an INTERFACE, not from Waze

    sources behind it, interchangeable and added in any order:
       Waze IPC          if the bug is ever fixed
       Waze notification the fallback, ALIVE today per 9b
       Mapbox/FrogPilot  if he ever wants destination-entry routing
       nothing           the current state, and everything must degrade to it

**That ordering also matches the fork's own rule.** Route intent OPENS or REFUSES a maneuver, so it
is evidence, and *evidence that opens must never be cheaper than evidence that refuses*. A turn
instruction may freely REFUSE a pass -- "do not offer one 300 m before his exit" is the first and
safest consumer, needs no wayId join, and is useful the day any source lands. Letting it OPEN a lane
change is a later and much higher bar.

**So the first build is the refusal**, against a stub source, with the anchor and the map already
providing everything else. Then whichever transport arrives first fills it.

### 9e. THE CHEAPEST VERSION -- read it straight off CAN -- IS CLOSED. Measured 2026-08-22.

Before any phone bridge, the obvious question: **Ford's own cluster renders turn-by-turn, so does
the instruction cross a bus the comma already reads?** If it did, there is no app, no WiFi, no
notification and no dependency on Waze's publishing at all -- just a DBC entry.

**Ford does put navigation state on CAN.** `ford_lincoln_base_pt.dbc`, `BO_ 811 APIM_Data_FD1`:

    DistToStopover_L_Actl   31|16@0+ (0.1,0) [0|6553.4] "kilometer"
    StopoverType_D_Stat     47|3@0+

A stopover is a route waypoint. So the APIM broadcasts route DISTANCE. There is no maneuver field in
this DBC, but it is community reverse-engineered and incomplete, so that alone proves nothing.

**AND THE MESSAGE IS NOT ON ANY BUS WE LOG.** Route 000003ab, six segments, every APIM address in
the DBC:

    0x105  APIM_Req            bus 1     x358    1 payload  (static)
    0x3E2  Personality_APIM    bus 0/2/130       1 payload  (static)
    0x462  APIMGPS_Nav_1       bus 0/2/130       6 payloads, VARYING  <- position, as known
    0x32B  APIM_Data_FD1       ABSENT
    0x463 / 0x464  Nav_2 / Nav_3   ABSENT   (already known -- this is the U0253 finding)
    0x3F1 / 0x215 / 0x227 / 0x211   ABSENT

**`APIM_Data_FD1` also carries exterior-light and menu signals**, which are published whether or not
a route is active -- so its total absence is evidence about the BUS, not about navigation being off
during that drive. That distinction matters, because "absence in a log is evidence about the log's
conditions first" is a rule this file already carries, and it does not rescue this one.

**This is consistent with what CLAUDE.md already records: there is no MS-CAN on this car.** The
APIM-to-cluster navigation traffic evidently lives on a bus the comma is not tapped into.

**"CLOSED" WAS WRONG AND HE CORRECTED IT THE SAME DAY:** *"MS-CAN will be routed with the CANBOX,
though, just like we are using it for BLIS! I am confident with that!"*

**So the CAN path is not closed, it is PENDING THE SAME HARDWARE BLIS IS PENDING ON.** That is a
completely different status, and it reorders everything below -- because if MS-CAN reaches a bus
openpilot reads, the APIM's own navigation broadcast becomes readable with no phone, no app, no
WiFi, no notification, and no dependency on Waze exposing anything to third parties.

**AND THE ARGUMENT THAT MAKES IT LIKELY IS STRUCTURAL, not hopeful.** The IPC is a separate module
from the APIM. It renders turn-by-turn. Therefore the instruction MUST cross a bus to reach it.
There is no third possibility. The only questions are which bus and which message, and the canbox
answers the first.

**IT ALSO DECOUPLES THIS FROM THE WAZE BUG ENTIRELY.** He confirmed Google Maps still renders turns
on his IPC -- so that data is crossing the bus TODAY, from Maps, while Waze is broken. A canbox
routing MS-CAN would deliver route intent from whichever app is working, and Waze becomes his
PREFERRED source rather than a prerequisite.

**What is confirmed and what is not, kept separate because the memory
[[canbox-capabilities-unknown]] says only BLIS routing is confirmed:**

    HIS PLAN, stated                 the canbox routes MS-CAN, as it will for BLIS
    STRUCTURAL, near-certain         the instruction crosses SOME bus, or the IPC could not draw it
    NOT ESTABLISHED                  that it is on MS-CAN specifically
    NOT ESTABLISHED                  that a MANEUVER is broadcast at all, not merely a DISTANCE.
                                     `APIM_Data_FD1` gives `DistToStopover_L_Actl` and no turn
                                     field, and this DBC is community reverse-engineered, so the
                                     maneuver may be in a message nobody has decoded -- or in one
                                     nobody has named.

**THE MEASUREMENT THAT SETTLES IT NEEDS NO NEW HARDWARE AND SHOULD BE DONE FIRST.** Drive with
**Google Maps navigating**, then diff the logged buses against a drive with no route active. Any
address that appears or starts varying is the navigation channel, and its bus tells us whether the
canbox is even needed. That is the "diff the wire against the decoder" technique already in memory,
and it is a single deliberate drive.

If it shows up on bus 0/1/2 today, this is buildable now. If it does not, it is on MS-CAN and the
canbox is the whole unlock -- and it arrives with BLIS rather than after it.

### 9f. Why WAZE and not a router, in his words -- and why passing assist outranks all of it

Two corrections he made while this section was being written, both of which change the priority
rather than the design:

  *"Of course I care more about automatic passing than navigation."*

**So navigation here is INSTRUMENTAL. It is not a feature being built for its own sake** -- it is an
input to passing assist, and any work on it that does not make passes better is off-scope. That
retires most of section 8 as a curiosity: destination entry, turn-by-turn rendering and a routing
engine serve a navigation PRODUCT, and he is not asking for one.

  *"Waze is already running on my phone and going to Android Auto, you know?"*

**And that is the strongest argument in the whole design, stronger than route quality.** A Mapbox or
FrogPilot-style router needs a destination entered on the device before every drive. Waze needs
NOTHING -- it is already open, already routing, already streaming to Android Auto on every trip he
takes. The marginal cost to him is zero, and a feature with zero marginal cost is one that is
actually used, which is the same reasoning that killed "run this on your next drive" as a
diagnostic style.

It also explains why the notification fallback is attractive rather than a hack: the data is already
being produced on a device already in the car, and the only missing piece is a wire.

### 9g. THIS IS A DIFFERENT BRANCH. Passing assist stays as it is.

His instruction, 2026-08-22: *"we would want to create a new session and branch and everything and
keep passing assist as it is."*

**So nothing here lands on `passing-assist-phase1`.** That branch is measured, shipped and driven
daily. This document is the handoff and is written to be read cold.

**IT BUILDS ON PASSING ASSIST, NOT BESIDE IT** -- his correction, and the first version of this
paragraph got it wrong by calling it a sibling:

    icbm-manual-override-and-tuning        the base every line of work takes updates from
      └── passing-assist-phase1            takes ICBM by MERGE (per the reflog, not rebase)
            └── route-intent  (new)        takes passing assist the same way

**Why a child and not a sibling, which is the part that matters.** The radar detector is a sibling
because it needs nothing passing assist owns. Route intent is the opposite: its entire purpose is to
feed passing assist's gates, and it consumes the lane anchor, `_geometry`'s terms, the maneuver state
machine and the panel -- all of which live here. A sibling branching off ICBM would have none of
them and would have to duplicate or wait.

**The consequence for the new session:** it inherits passing assist AND, transitively, ICBM, so it
gets mapd v2, the tiles, the anchor and the whole gate structure for free. It adds only the
instruction source and the gate that consumes it. **And passing assist stays additive-only from that
side** -- if the new work needs a change in passing assist's own code, that change belongs HERE and
reaches the child by the normal update, exactly as "a fix belongs to the branch that owns the code"
already requires.

**TWO CONSTRAINTS HE STATED 2026-08-22 THAT DECIDE THE ORDER:**

  *"I prefer waze, obviously."*   and   *"I also don't have the canbox yet."*

**No canbox means the MS-CAN transport cannot be tested end to end today**, only its negative half.
**The broken IPC path means Waze is not publishing to the cluster** regardless of hardware. So of
the three transports, exactly one is live right now:

    Waze IPC via MS-CAN      needs the canbox AND Waze's bug fixed      TWO blockers, neither his
    Mapbox / FrogPilot       works today, but is not Waze and costs a destination entry per drive
    WAZE NOTIFICATION        works today, no hardware, no vendor cooperation    <- the only one

**AND IT IS NOT A COMPROMISE ON HIS PREFERENCE, which is the point that reorders everything.** The
notification is WAZE -- his own app, his own route, with the traffic and rerouting that made him
prefer it. It is the same source over a different wire. So the fallback in 9a delivers his first
choice of DATA while the IPC path waits on two things he does not control.

**BUT THE CANBOX ROUTE IS THE PREFERRED DESTINATION, and he asked directly: "we prefer the canbox
route, right?" Yes.** The paragraph above argued the notification should therefore be built FIRST,
and that does not follow -- being the only live transport makes it the FALLBACK that is available,
not the one to build toward.

**Why MS-CAN wins on every axis that lasts:**

    no phone in the loop         nothing to keep running, charged, connected or foregrounded
    structured CAN               decode once against a DBC; a notification is a rendered glyph
                                 and a string, and reading a maneuver out of it is INFERENCE
    stable                       Waze can restyle a notification in any release; a CAN signal
                                 does not move
    no second link               the notification path needs phone -> WiFi -> comma, which is
                                 another thing to fail silently mid-drive
    no companion app             which would be ours to write, ship and maintain forever
    works for ANY nav app        whatever is driving the cluster feeds it

**AND THE WAZE BUG IS NOT ACTUALLY A BLOCKER FOR IT, which is the part that settles the ordering.**
Google Maps renders turn-by-turn on his IPC today. So:

    canbox + Google Maps     works as soon as the hardware is in -- no vendor cooperation at all
    canbox + Waze            works the day Waze fixes the IPC bug, with no code change

**So the canbox route degrades gracefully to Maps and upgrades to Waze for free.** It is not waiting
on Waze; only his PREFERENCE of source is.

**REVISED ORDERING:**

    1. the transport-agnostic interface and the REFUSAL gate, against a stub    build now
    2. MS-CAN via canbox                                                        the target
    3. Waze notification bridge                                                 ONLY if the canbox
                                                                                slips badly

Point 3 is a real cost -- an Android app to write and maintain -- and it buys time rather than
capability. **Do not start it while the canbox is weeks away.** It is in this document so the option
is understood, not because it is scheduled.

**WHAT THE NEW SESSION SHOULD DO FIRST, in order:**

1. **The Google Maps diff drive.** No hardware, no code. Drive with Maps NAVIGATING, then diff the
   logged buses against a no-route drive. Anything that appears or starts varying is the channel.
   This decides whether the canbox is needed at all, and it is one drive. `tools/bp_offline_map.py`
   and the APIM probe in this session's history are the starting shapes.

   **AND WITH NO CANBOX FITTED, "NOTHING APPEARED" IS THE EXPECTED RESULT AND IS NOT A DEAD END.**
   It says the instruction is on a bus the comma cannot currently see, which is what the canbox
   exists to fix -- it does NOT say the instruction is absent. This file already records the same
   trap twice (mapd v2 offroad, and a route older than the boot): **absence in a log is evidence
   about the log's conditions first.** The drive is still worth doing, because the OTHER outcome --
   something appearing on bus 0/1/2 today -- would make the whole canbox dependency unnecessary,
   and that is worth one drive to rule in or out.
2. **Only then choose a transport.** MS-CAN via canbox (9e), Waze notification (9b), or Mapbox
   routing (9c) -- and the interface must make them interchangeable, because two of the three depend
   on other people.
3. **Build the REFUSAL, not the opener.** "Do not offer a pass N metres before his exit" needs a
   maneuver and a distance and nothing else -- no wayId join, no map join. It is useful the day any
   source lands and it satisfies *evidence that opens must never be cheaper than evidence that
   refuses*. Opening a lane change on route intent is a later and much higher bar.

**WHAT IT MUST NOT DO:** consume route intent as PERMISSION. The same rule that governs map data
governs this -- a wrong instruction that merely costs a missed pass is a bad day; one that opens a
lane change is a different category. And the fork already has the precedent written down twice this
week, where `oneWay` and the lane anchor both scored perfectly on questions they were not allowed
to answer.

**WHAT PASSING ASSIST ALREADY PROVIDES IT**, so the new session does not rebuild any of it: the lane
anchor (index, bounds, `noLaneLeft`), `mapdOut` (`highwayClass`, `oneWay`, `lanes`, `waySelectionType`),
the adjacent-lane radar, the maneuver state machine, and the panel. The missing piece really is only
the instruction.
