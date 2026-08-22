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

**It has never been examined.** Before anyone builds route prediction, measure the one already
running: at the forks he actually drives, how often is `predicted` right? If it is good, the problem
is far smaller than it looks. If it is poor, that is the evidence that justifies 5b.

The data is being collected now -- `MapdV2=1`, observe mode.

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
