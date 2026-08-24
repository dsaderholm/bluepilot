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

**THE ANDROID AUTO REGRESSION IS RULED OUT ON TIMING. From him, 2026-08-22, and it settles what
this section spends most of its length weighing.**

Asked when it broke, his answer was *"when Waze did their massive Android Auto UI overhaul a few
years ago."*

**The recent AA cluster/HUD regression is from the last several months. A fault that predates it by
YEARS cannot be it.** Everything above about "the regression hits apps unevenly and could be sparing
Maps on this head unit" was the strongest surviving competing explanation, and it is now dead on
dates alone rather than on the Maps discriminator. The section's own verdict -- *"still NOT
conclusive"* -- was correct when written and is no longer the state of the evidence.

**AND IT FAILED ON TWO PHONES, which he volunteered.** Motorola Edge+ 2020, then Motorola Edge+
2023. Same behaviour across a complete phone replacement, so it is not a corrupted install, a bad OS
image, a device-specific USB stack, or anything about his handset.

Config, recorded so a future round does not re-collect it:

    phone            Motorola Edge+ 2023   (previously Edge+ 2020, same failure)
    Android Auto     17.3.662854-release
    Waze             current Play Store release, verified 2026-08-22
    car              2020 Ford Fusion Titanium AWD, SYNC 3, wired AA
    frequency        every drive, without exception
    debug logs       submitted 2026-08-22
    thread           thread::gmI37q7JcPaPnyjfsPIBMSs::

**THE STRONGEST ARGUMENT WAS NOT IN THE EMAIL HE SENT, and it belongs on the photo follow-up.**
Noticed 2026-08-22, after the reply had gone. **The notification proves Waze HAS the data.** It
renders a maneuver glyph and a live, counting-down distance on the same drive where the cluster
shows a compass -- so Waze is computing Steps and per-step distance internally and simply not
handing them to Android Auto. That makes it a PUBLISHING failure rather than a routing or data one,
which is materially harder for triage to deflect than any symptom description.

He already promised them a cluster screenshot, so it costs no extra round: **two photos, same drive,
seconds apart** -- the phone showing the notification's maneuver and distance, and the cluster
showing the compass. That pair is the entire bug in one image.

**So the evidence now closes off four explanations rather than one:** not the phone (two of them),
not the car or head unit (Maps populates the same cluster), not the industry regression (wrong by
years), and not the Car App API Level 6 cluster feature (that needs SYNC 4/4A and is not what he is
asking for). What is left is the app.

**THE `updateTrip()` DIAGNOSIS IS STILL AN INFERENCE, and it should not harden into a fact here.**
"The compass is what a Ford IPC renders when a session is active but carries no valid Trip metadata"
is reasoned from the symptom and has never been measured. That is the same shape as the beta-program
premise this section already records as refuted -- a plausible mechanism asserted about a specific
system nobody had tested. **The measurement that would settle it is phone-side, not car-side:**
Android wireless debugging (the USB port is taken by wired AA on SYNC 3) plus `adb logcat` across
one Waze drive and one Maps drive, diffed around the navigation session. Offered 2026-08-22, not
taken up, and honestly caveated: a release build may not surface a third-party app's Car App Library
activity at all, in which case an empty capture means the instrument is blind rather than Waze
innocent.

**THE ROUND WAS SPENT, and here is what it consisted of**, since he asked for help after the "do not
spend a third" verdict. That is his call and it overrides the verdict; what it must not become is a
fourth. The reply was DRAFTED rather than researched further, which is the whole point of the bound.
The reusable part is the argument, not the draft:

1. **Name the symptom as the mechanism.** "The IPC shows the Android Auto COMPASS" is a statement
   about Trip metadata. "Waze doesn't show on my cluster" is a triage ticket. Same fact, different
   reader.
2. **Lead with what the evidence ELIMINATES**, not with the complaint. Two phones, the Maps A/B, and
   the years-ago timeline each close a door, and a report that closes four doors survives triage.
3. **Pre-empt the Car App API Level 6 close.** Waze's own help page advertises cluster rendering, so
   the likeliest outcome is "your car needs SYNC 4/4A". Saying up front that he is asking about
   `updateTrip()` on SYNC 3 -- which Maps still renders -- is what stops that closing the thread.
4. **Name their release landmark rather than a date.** "When you overhauled the Android Auto UI" is
   something they can look up and he cannot be wrong about; a guessed date sends them to the wrong
   build and costs a round.
5. **Reply on the EXISTING thread.** A third ticket splits the evidence, which is how the first two
   rounds produced the same template twice.

**And his framing needed one correction, which is the part that matters to the build.** He wrote
"once I get the canbox and Waze patched", treating both as prerequisites. **Only the canbox is** --
9e already says so and the misreading recurs. The canbox delivers route intent from whichever app is
publishing, and Maps works. Waze is the PREFERRED source, because it reroutes around traffic; it is
not a dependency. He confirmed the preference and it is a good one, but nothing is sequenced behind
it.

**One untested workaround was offered and deliberately not pushed:** rolling Waze back to a build
from before the overhaul, the mirror of the documented Maps-v25 trick. Nobody has checked it on Waze.
Sideloading an older build of the app he navigates with is his call.

**FORD WAS EARLY ON THIS, NOT LATE. Researched 2026-08-22, and it changes how to read the silence.**

    AA turn-by-turn in a Ford cluster   requires SYNC 3.4; native from SYNC 4
    his car                             renders it from Google Maps, so he is on 3.4+
    CarPlay                             does NOT get it -- AA and factory nav only
    Google's OWN cluster integration    demoed May 2021 on the BMW iX, two years later, and
                                        covered as a feature few cars would ever get

So Ford built this on the older `updateTrip()` metadata path BEFORE Google had a general solution,
and while most manufacturers had nothing. **His 2020 car was ahead of the curve on exactly this
feature**, which is the opposite of the assumption a reader would bring to a six-year-old head unit.

**THAT IS WHY WAZE CAN BREAK IT AND NOT NOTICE.** A feature a minority of OEMs implement, on a path
Google has since superseded, is one that regresses silently -- the population who would report it is
tiny and it is probably in nobody's test matrix. Worth stating to support directly: sparse reports
are not evidence of no bug here.

**AND IT MEANS FORD WILL NOT FIX IT.** If he is on the last SYNC 3 build -- he believes he is, exact
build unconfirmed -- there are no more head-unit updates coming, so every lever is on Waze's side.
Another reason nothing may be sequenced behind this.

**One confound it closes:** SYNC updates and the Waze overhaul both happened in the same era, so
"what changed" had two candidates. Maps rendering today rules SYNC out -- the cluster path
demonstrably still works.

**MEASURED ON THE ROAD 2026-08-23, AND IT REFUTES THE HYPOTHESIS BELOW. Read this first.**

He drove it deliberately. Observed, not inferred:

    WAZE          "Calculating Route" on the IPC for UNDER A SECOND, then compass + speedometer
                  the CANCEL-ROUTE OPTION IS NOT REACHABLE AT ALL -- he cannot even open the IPC
                  submenu it lives in. Corrected 2026-08-23; an earlier draft said "the button
                  does nothing", which is a weaker and different claim.
    GOOGLE MAPS   worked fully, and rendered a U-TURN glyph on the cluster
    OSMAND+       worked fully as well

**OSMAND+ IS THE STRONGEST CONTROL OF THE THREE, and it is worth more than the Maps one.** Google
Maps working can be argued away -- Google's own app, plausibly special-cased by Ford or taking a
privileged path. **OsmAnd+ is a small open-source third party using the plain public Car App
Library**, with no relationship to Ford and no leverage with Google. It populates his cluster
correctly.

So the AA cluster path on this car works for an ORDINARY THIRD-PARTY APP. That kills three
explanations at once: "Ford only really supports Maps", "the cluster path is privileged", and
"SYNC 3.4's cluster support is Maps-specific". Three apps, two work -- including the least
resourced one -- and the one that fails is the app Google owns.

**A FREE OBSERVATION THAT WOULD LOCALISE IT FURTHER, for the next drive:** do Maps and OsmAnd+ also
flash "Calculating Route" before the turn arrow? If they do, the cluster state machine is
calculating -> populated and Waze stalls at step one. If they jump straight to a maneuver, then Waze
ENTERING that state at all may itself be the anomaly.

**"CALCULATING ROUTE" KILLS THE "WAZE NEVER STARTS A SESSION" EXPLANATION.** The cluster only draws
that when it has been told a route is being computed, so something DOES reach the host: almost
certainly `navigationStarted()` IS called and a first `updateTrip()` DOES land. The section below
was written before this drive and its conclusion is wrong; it is kept because the reasoning was
sound on the evidence available and would be re-derived otherwise.

**WHAT IT IS INSTEAD, and the API has a field with exactly this shape.** `Trip` carries
**`isLoading`**, and the builder refuses Steps while it is set -- *"Step information may not be set
while loading."* So "Calculating Route" is that flag, and the failure is precisely the transition
out of it: **Waze publishes the loading Trip and never publishes the populated one that replaces
it.**

**THE MISSING SUBMENU IS BETTER EVIDENCE THAN A DEAD BUTTON, and the distinction decides between
the two hypotheses below.** A button that does nothing is a cluster that HAS a route and will not
cancel it. A submenu that cannot be opened is a cluster that **does not believe a route exists** --
so the nav state it is tracking went active for under a second and then back to inactive.

That is a state TRANSITION, not a stuck command, and it is what H1 predicts.

**AND IT IS THE TOGGLE MARKER THE CAN HUNT WANTED, for free.** Whatever byte carries "a route is
active" went 0 -> 1 -> 0 within a second of him starting Waze navigation. Starting a Waze route is
therefore a cheap, repeatable, precisely-timed edge to look for -- and unlike the Maps case it does
not even require driving anywhere.

**AND THE DEAD END-DRIVE BUTTON PICKS BETWEEN TWO VERSIONS OF THAT:**

    H1  Waze ENDS the navigation session right after the loading state.
        -> compass returns because the session is gone
        -> nothing left for the IPC to cancel        ONE fault, explains both symptoms
    H2  the session persists, Waze never sends a populated Trip, AND its onStopNavigation
        is broken                                    TWO independent faults

**H1 is the better explanation of the same evidence** and should be the one stated to Waze. H2 is
not ruled out.

**A THIRD CANDIDATE WORTH NAMING, because it is concrete and logcat would show it:** `Trip.Builder`
enforces strict parity -- steps and stepTravelEstimates must match in count and order, and it throws
otherwise. A mismatch would make `build()` throw while assembling the real Trip, leaving the loading
state as the last thing successfully published. Speculative, but it is a specific exception to grep
for alongside `IllegalStateException: No callback has been set`.

**AND THE MAPS CONTROL GAVE US SOMETHING FOR THE CAN SIDE.** The cluster rendered a U-TURN, which is
not a trivial glyph -- so the maneuver vocabulary crossing that bus is real and has range. Combined
with "Calculating Route", the cluster's known nav states are now at least: calculating, a maneuver
glyph with distance, and a compass fallback. That is the start of the protocol catalogue.

**THE END-DRIVE BUTTON REFRAMES THE WHOLE DIAGNOSIS. 2026-08-22, from him plus the androidx source.**

He can end the drive from the IPC with Google Maps, and is **pretty sure he cannot with Waze**
(believed, NOT yet tested -- see the caveats below).

**How the IPC ends a drive**, researched rather than assumed:

    IPC button -> SYNC/APIM -> Android Auto host -> NavigationManagerCallback.onStopNavigation()

Two constraints from `androidx/car/app/navigation/NavigationManager.java`, both quotable:

    setNavigationManagerCallback()  REQUIRED before navigationStarted(), which otherwise throws
                                    IllegalStateException("No callback has been set")
    updateTrip()                    "should only be invoked once the navigation app has called
                                    navigationStarted(), or else THE UPDATES WILL BE DROPPED BY
                                    THE HOST"

**THAT SECOND QUOTE IS THE FINDING, and it collapses two symptoms into one cause.** If Waze never
calls `navigationStarted()`:

    every updateTrip() it makes is silently discarded by the host   ->  cluster shows the COMPASS
    the host has no session for Waze to stop                        ->  END-DRIVE DOES NOTHING

One root cause, both symptoms. That is a far better explanation than the two independent ones this
section previously carried.

**AND IT DEMOTES THE STANDING HYPOTHESIS.** Above, the compass is called "the signature of
`updateTrip` not being called, or called without Steps". A third possibility now looks more likely:
**`updateTrip` may be called correctly, with full Steps, and thrown away because the session was
never started.** That fits the notification evidence exactly -- Waze demonstrably computes maneuvers
and distances, and the notification path does not go through Android Auto at all, so correct data
and a discarded `updateTrip` are entirely consistent.

**IT ALSO UNDERCUTS AN ASSUMPTION NOBODY HAD MARKED.** "The compass means a navigation session is
ACTIVE but carries no Trip" was read as evidence that a session exists. Under the no-session
hypothesis the compass is just what the cluster draws when Android Auto is connected and it has no
turn to render -- which is equally consistent and assumes less. **The compass is probably not
evidence of a session at all.**

**TWO CAVEATS, both load-bearing:**

- **He said "pretty sure", not "tested".** One deliberate press with Waze navigating settles it, and
  it is the single highest-value measurement on this whole topic.
- **That Ford's end-drive is implemented via `onStopNavigation()` is the obvious mechanism and is
  NOT proven for Ford specifically.** It is the only path the Car App Library offers a host, which
  is strong, but it is inference.

**IT SHARPENS THE LOGCAT CAPTURE FROM FISHING TO A SPECIFIC STRING.** If Waze fails to register a
callback, `navigationStarted()` throws `IllegalStateException: No callback has been set` -- a
concrete thing to grep for. And the absence of any `navigationStarted` call at all is the other
signature. That turns the phone-side capture from "diff two logs and hope" into a yes/no.

**FOR THE SUPPORT THREAD:** this belongs in the photo follow-up he already owes them, not in a new
email. "Ending the drive from my instrument cluster works with Google Maps and does nothing with
Waze" is a second, independent symptom pointing at the navigation session rather than at the trip
data -- and it is the kind of detail that tells an integration engineer which function to look at.

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

**WHAT THE NEW SESSION SHOULD DO FIRST, in order:**

1. **The Google Maps diff drive.** No hardware, no code. Drive with Maps NAVIGATING, then diff the
   logged buses against a no-route drive. Anything that appears or starts varying is the channel.
   This decides whether the canbox is needed at all, and it is one drive. **TOOLED, 2026-08-22:
   `tools/bp_can_nav_diff.py`. The drive is still owed.**
2. **Only then choose a transport.** MS-CAN via canbox (9e), Waze notification (9b), or Mapbox
   routing (9c) -- and the interface must make them interchangeable, because two of the three depend
   on other people. **The interface exists as of 2026-08-22; see section 10.**
3. **Build the REFUSAL, not the opener.** "Do not offer a pass N metres before his exit" needs a
   maneuver and a distance and nothing else -- no wayId join, no map join. It is useful the day any
   source lands and it satisfies *evidence that opens must never be cheaper than evidence that
   refuses*. Opening a lane change on route intent is a later and much higher bar. **DONE,
   2026-08-22; see section 10.**

**WHAT IT MUST NOT DO:** consume route intent as PERMISSION. The same rule that governs map data
governs this -- a wrong instruction that merely costs a missed pass is a bad day; one that opens a
lane change is a different category. And the fork already has the precedent written down twice this
week, where `oneWay` and the lane anchor both scored perfectly on questions they were not allowed
to answer.

**WHAT PASSING ASSIST ALREADY PROVIDES IT**, so the new session does not rebuild any of it: the lane
anchor (index, bounds, `noLaneLeft`), `mapdOut` (`highwayClass`, `oneWay`, `lanes`, `waySelectionType`),
the adjacent-lane radar, the maneuver state machine, and the panel. The missing piece really is only
the instruction.

---

## 10. BUILT 2026-08-22: THE INTERFACE AND THE REFUSAL. NO TRANSPORT, ON PURPOSE.

Section 9d said build the consumer first behind a transport-agnostic interface, against a stub.
That is what exists now. **Every line of it is inert until a transport is fitted**, which is not a
limitation to work around -- it is the property that makes a gate fed by somebody else's software
safe to ship at all.

### 10a. What was added

    cereal/custom.capnp          struct RouteIntentBP -- renamed from CustomReserved16, keeping
                                 its struct id, per upstream's own "rename the struct, keep the
                                 identifier" rule
    cereal/log.capnp             routeIntentBP @142   (claims the customReserved16 slot)
    cereal/services.py           frequency 0, should_log True
    sunnypilot/selfdrive/controls/lib/route_intent.py     the CONSUMER and the whole policy
    sunnypilot/routeintent/source.py                      what a TRANSPORT implements, + StubSource
    tools/bp_route_intent_stub.py                         publishes a scripted route, bounded
    tools/bp_can_nav_diff.py                              the Maps-vs-no-route CAN diff (step 1)

Footprint inside passing assist, which is deliberately four lines: construct `RouteIntent`, update
it each cycle, one gate, one `Blocked` enumerant with its panel wording. Per 9g, passing assist
stays additive-only from this side.

### 10b. THE FOUR VALUES, AND WHY THERE ARE ONLY FOUR

`maneuver`, `distance`, `distanceKnown`, `observedMonoTime`. Nothing else, because a field recorded
and never read is this fork's oldest bug one level out -- the passing-assist audit found 25 of them
in one struct, and 53 across thirteen.

**`distanceKnown` is not ceremony.** The glyph and the number are SEPARATE READS for every
transport on the list: a CAN message may carry one without the other, and a notification scraper may
parse the icon while the number defeats it. A source that cannot measure the distance must not
invent one -- the `RearApproachSide.from_blis` rule, where a fabricated `ttc = 0.0` would have
commanded an emergency abort at 50 Hz on presence-only evidence.

**`observedMonoTime` is stamped at RECEIPT and cached, never at publish.** This is the only thing
standing between a dead link and a believed instruction, and SubMaster cannot supply it: it holds
the last message forever, so a publisher that died an hour ago still presents a well-formed frame,
and a phone bridge whose WiFi is gone keeps SENDING fresh messages carrying minutes-old content.
Hence frequency 0 in services.py -- a declared rate would make `sm.alive` report on the TRANSPORT
and be read as a statement about the INSTRUCTION.

### 10c. THE POLICY, in one table

    no message / not valid              no claim
    observedMonoTime == 0               no claim  <- capnp's default; an unstamped source
    age > 3 s, or a stamp in the future no claim
    maneuver none / continueAhead       no claim
    distanceKnown false                 NO CLAIM -- the one deliberately permissive branch
    distance < 0                        no claim
    distance <= max(150 m, v * 20 s)    REFUSE
    anything else                       no claim

**The permissive branch is the interesting one.** A maneuver with no distance carries no bound, so
refusing on it goes quiet for the ENTIRE ROUTE rather than for the approach. That is not a
conservative version of this gate, it is a different and much worse feature.

**Everything not in the no-claim set refuses, including `unknown` and including enumerants nobody
has added yet.** Written as "not in this short set" rather than "in the set of committing
maneuvers", so the DEFAULT for a new maneuver type is to refuse. A new type silently not refusing is
invisible in a log; one that refuses costs a pass and shows up as `routeManeuver` in `blockedBy`.

**20 s is derived, not measured** -- ~15 s for the pass itself (the figure `LIMIT_DROP_LOOKAHEAD_M`
is already reasoned from) plus ~5 s to get back across and settle. At 70 mph that is ~620 m, which
is long deliberately: the limit-drop gate chose 250 m over 300 because a limit change is on the
horizon most of the time, and **this gate has the opposite economics** -- it fires only for
maneuvers on HIS OWN ROUTE, one or two per trip. That asymmetry is the whole reason route intent
beats the map here. Fit it from drive data once a transport lands.

### 10d. THE REFUSAL-ONLY GUARANTEE IS PARSED, NOT PROMISED

Four structural tests, each verified to fail with the property broken:

    the consumer's public surface is exactly {update, reset, refuses_pass}   -- no way to say yes
    passing_assist calls only update and refuses_pass on it
    the gate's body is one _reset_outputs(Blocked.routeManeuver) and no else branch
    `route_intent` is reachable from __init__ and _decide ONLY -- never may_actuate, never
      _must_abort, never _run_maneuver

The last one is CLAUDE.md's gate review made executable: enumerate every consumer, label each
REFUSES / AUTHORIZES / DISPLAYS. `may_actuate` AUTHORIZES and `_must_abort` performs a maneuver of
its own, and the BLIS bug was exactly a refusal-shaped input reaching the second of those.

Prose is not enough here and this file should say why: the identical argument has now been made
three times in a week about signals that scored PERFECTLY on questions they were not allowed to
answer -- `oneWay` for the oncoming flag, the lane anchor for the left gate, and now this.

### 10e. MUTATION-TESTED, AND ONE TEST WAS VACUOUS UNTIL IT WAS

Nine mutations, eight caught. **The one that was not is worth the whole exercise:** deleting the
`observedMonoTime <= 0` guard left the entire suite green, because the test used a clock value of
1e12 ns, where an unstamped message ages out at 1000 seconds and the FRESHNESS check catches it for
the wrong reason.

`time.monotonic_ns()` counts from BOOT. One second into the clock, an unstamped message is 1.0 s
old -- comfortably fresh -- and would have been believed. plannerd starts seconds after boot, which
is exactly when a new transport is coming up too. The test now runs at that instant and the guard
is covered. Same lesson as everywhere else in this file: green was not evidence.

Two behaviours had no test at all until mutation found them -- `keep_wanted` on the refusal, and
the gate's position ahead of the map gates.

### 10f. THE CAPNP SLOT, so a collision is anticipated rather than discovered

`routeIntentBP` claims **`customReserved16` @142**, the next free slot after `rearRadarBP` @141 and
before mapd's @143-145. **The radar-detector branch has claimed no slot and is not yet rebased past
`rearRadarBP`**, so when it is, @142 is the number it would naturally reach for next.

The tiebreaker is WIRE HISTORY, not base branch -- a field already written to a route log cannot
move, because capnp reads by position and every recorded drive would decode as garbage.
`routeIntentBP` has never been published anywhere, so if the radar detector gets there first, THIS
one moves. `test_capnp_ordinals_unique.py` catches the collision either way, loudly, which is the
point of it.

### 10g. WHAT IS STILL OWED, and the first item is a drive

1. **The Google Maps diff drive -- and it is now ONE drive, not two.** `tools/bp_can_nav_diff.py`
   was run on the device 2026-08-22 against route **000003ac** (11 segments, 2,919,073 frames, 383
   (address, bus) pairs on buses 0/1/2). Every APIM address is ABSENT except position:

       0x32B ABSENT   0x463 ABSENT   0x464 ABSENT   0x225/0x3F1/0x211/0x215/0x227 ABSENT
       0x462 present -- bus 0: 603 frames, bus 2: 8

   **So the control side is measured** and any of those appearing on a navigating drive is
   unambiguous. Navigate somewhere real with **Google Maps** (not Waze -- Maps demonstrably still
   renders turns on his IPC) and diff against 000003ac. Expect nothing to appear, which locates the
   data on MS-CAN and means route intent arrives WITH the canbox.

   **The one thing that would invalidate that shortcut:** 000003ac is a control only if he was NOT
   navigating during it, which only he can say. Absence in a log is evidence about the log's
   conditions first -- ask before relying on it.

   **And do not cap `--segments`.** Measured on the same route: 3 segments and 11 return the
   identical 383 (address, bus) pairs, so a cap hides no ADDRESS -- but 0x462's varying bytes go
   from [2,3,6,7] to [1,2,3,5,6,7]. Byte variance is what the diff keys on, and it is exactly what
   a cap understates. That caveat was written from argument and is now measured.
2. **A transport.** Nothing here is sequenced behind any particular one.
3. **Fit the 20 s** against how often it goes quiet, and whether a pass offered inside the window
   was one he would have made.
4. **NOT the opener.** "His route goes left, so a left pass is fine" is the version that moves the
   car on a stale instruction. It stays unbuilt until there is a source with measured accuracy AND
   measured lead time -- and note that the one source ever measured on this car, mapd's own fork
   prediction, was 96-100% accurate with a lead of 1.0 s against an 8 s budget. Accuracy was never
   the binding number.

---

## 11. WHAT EACH TRANSPORT ACTUALLY CARRIES. Researched 2026-08-22.

Three candidate sources, and until now only one of them had ever been described. This section is
the field-by-field comparison, so a transport author is choosing rather than discovering.

### 11a. `updateTrip()` -- VERIFIED from the androidx source, not recalled

Read from `androidx/car/app/navigation/model/*.java` on android.googlesource.com. This is what an
Android Auto navigation app hands the head unit, and therefore the ceiling on what SYNC could
possibly forward:

    Trip
      destinations[]              paired 1:1 with destinationTravelEstimates[]
      steps[]                     paired 1:1 with stepTravelEstimates[]
        Step
          maneuver                Maneuver: 47 TYPE_* constants (see 11b)
                                    + roundaboutExitNumber, roundaboutExitAngle, icon
          lanes[] + lanesImage    lane guidance
          cue                     the verbal instruction; "a fallback when Maneuver is not set"
          road                    the street being turned ONTO
          
      currentRoad                 the street currently on
      isLoading

    TravelEstimate
      remainingDistance           a Distance object, carries its own units
      remainingTimeSeconds        long; REMAINING_TIME_UNKNOWN = -1
      arrivalTimeAtDestination    with time zone
      remainingTime/DistanceColor, tripText, tripIcon

**THREE THINGS IN THERE MATTER MORE THAN THE REST.**

**`Step.road` is documented as CLUSTER-TARGETED**, in Google's own words: *"This value is primarily
used for vehicle cluster and heads-up displays and may not appear in the navigation template."* So
the cluster path is not an afterthought of this API -- there is a field that exists mainly to feed
it. That is a good sign for how much Ford has to work with.

**`stepTravelEstimates[0]` IS DISTANCE AND TIME TO THE NEXT MANEUVER**, which is exactly what the
gate needs, and it gives BOTH. `RouteIntentBP` carries distance and converts it to time at the
current speed; a source with `remainingTimeSeconds` could supply the time directly and skip the
conversion. Not changing the schema for it today -- an unread field is this fork's oldest bug -- but
recorded, because the day a source has it, `LOOKAHEAD_S` stops needing a speed at all.

**`Trip.steps` is a LIST and the API expects only the first to matter**: *"display may only show
information about the first step."* So the single-next-instruction shape of `RouteIntentBP` is not
an impoverished version of what the car gets -- it is what the car gets.

### 11b. THE 47 MANEUVER TYPES, AND THE THREE WE DID NOT HAVE

Full list, verified: UNKNOWN, DEPART, NAME_CHANGE, KEEP_LEFT/RIGHT, TURN_SLIGHT_L/R,
TURN_NORMAL_L/R, TURN_SHARP_L/R, U_TURN_L/R, ON_RAMP_{SLIGHT,NORMAL,SHARP,U_TURN}_{L,R} (8),
OFF_RAMP_{SLIGHT,NORMAL}_{L,R} (4), FORK_L/R, MERGE_L/R/SIDE_UNSPECIFIED, ROUNDABOUT_* (8),
STRAIGHT, FERRY_{BOAT,TRAIN}{,_L,_R} (6), DESTINATION{,_STRAIGHT,_LEFT,_RIGHT} (4).

Mapped onto ours:

    AA type family                    ->  RouteIntentBP.Maneuver
    UNKNOWN                               unknown
    DEPART, NAME_CHANGE, STRAIGHT         continueAhead
    KEEP_LEFT / KEEP_RIGHT                keepLeft / keepRight      <- ADDED, see below
    TURN_SLIGHT_*                         slightLeft / slightRight
    TURN_NORMAL_*                         turnLeft / turnRight
    TURN_SHARP_*                          sharpLeft / sharpRight
    U_TURN_*                              uTurn
    ON_RAMP_* (8)                         onRamp                    <- ADDED
    OFF_RAMP_* (4)                        exitLeft / exitRight
    FORK_*                                forkLeft / forkRight
    MERGE_*                               merge
    ROUNDABOUT_* (8)                      roundabout
    DESTINATION_* (4)                     destination
    FERRY_* (6)                           unknown  -- refuses, which is right for a ferry

**`keepLeft`, `keepRight` and `onRamp` were added because of this check.** All three previously fell
to `unknown`, which REFUSES, so nothing was unsafe -- but a log reading `unknown` where the car was
plainly told "keep left" is a log nobody can score. Vocabulary is free. The parametrised test walks
the schema, so all three were covered the moment they existed.

The roundabout exit NUMBER and ANGLE are deliberately not carried. The gate does not steer and does
not need to know which exit; it needs to know a roundabout is coming.

### 11c. WHAT FORD PUTS ON CAN -- MEASURED, AND IT IS ALMOST NOTHING

`ford_lincoln_base_pt.dbc`, the whole file: **331 messages, 2,150 signals.** Everything
navigation-related in it:

    APIM_Data_FD1 (0x32B)  DistToStopover_L_Actl   16 bit, 0.1 km, 0..6553.4 km
                           StopoverType_D_Stat     3 bit
    Steering_Data          SteWhlSwtchNav_B_Stat   a BUTTON, not data

**That is the entire inventory. No maneuver. No road name. No ETA. No lane guidance.**

**AND THE ONE DISTANCE FIELD IS THE WRONG QUANTITY.** A "stopover" is a route WAYPOINT, so
`DistToStopover_L_Actl` is distance to the next waypoint or destination -- NOT distance to the next
maneuver, which is what the gate needs. Reading it as turn distance would be wrong on every route
that has no waypoints, which is most of them.

**But the DBC is community reverse-engineered, and the structural argument beats it.** His IPC
renders Google Maps turn arrows TODAY. The IPC is a separate module from the APIM, so that
instruction MUST cross a bus. It is therefore in a message this DBC does not describe. That gap is
exactly what `tools/bp_can_nav_diff.py` exists to close, and the control side of that diff is
already recorded (see 10g).

**HOW LOSSY TO EXPECT, since he asked** -- *"I'm hoping Ford integrates most of this into the IPC
display or the CAN signal sent from sync"*: expect the cluster's vocabulary to be **much smaller
than the API's**. A Ford IPC turn arrow is a handful of glyphs, not 47 enumerants, and SYNC will
have collapsed the API's types down to whatever it can draw.

**That is fine, and it is worth saying plainly, because it sounds like bad news and is not.** The
gate collapses all 47 types into one bit -- does this commit the car to leaving this road -- plus a
distance. A crude arrow and a distance satisfy it completely. **Route intent is the consumer least
harmed by Ford being lossy**, which is a good reason for it to be the first consumer built.

**THE LINK IS BIDIRECTIONAL. From him, 2026-08-22: he can END THE DRIVE from the IPC, and it works
with Google Maps.**

Worth more than it sounds, in three ways.

**It is a COMMAND going the other way.** Not APIM -> IPC pushing a display, but IPC -> APIM sending
something the phone acts on. A cluster that can cancel a route is in a real session with the APIM.

**Which raises confidence that the bus carries STRUCTURED data rather than pixels.** If Ford were
shipping a pre-rendered image to the cluster there would be no reason for a cancel command to exist
in that protocol. Inference, not measurement -- but it is the right direction, and it is the first
evidence either way.

**And it hands the CAN hunt a MARKER HE CAN TRIGGER.** This is the practical part. A distance
counting down is a slow, subtle thing to find in a byte diff. Navigation going active -> inactive at
a moment he noted is sharp, repeatable, and on demand. **One drive with navigation started and ended
three or four times at known clock times beats two whole drives compared against each other**, and
it produces both halves at once: the nav-state bit, and whatever the IPC transmits to request the
cancel.

**Possibly without driving at all.** If the APIM publishes nav state while stationary, the whole
channel could be found in the driveway. Unknown -- nav data may be gated on motion -- but it costs
one attempt to find out and it is the cheapest version of this experiment.

**AND IT IS A FREE DISCRIMINATOR FOR THE WAZE BUG.** Can he end the drive from the IPC while WAZE is
navigating?

    yes  ->  the AA navigation session IS established and the IPC/APIM link is live, so the failure
             is narrowly in Trip/Step CONTENT -- exactly the updateTrip hypothesis
    no   ->  the session itself is not registering, which is a DIFFERENT bug and a different report

Either answer is worth having and it costs one press.

**AND HE WAS SURPRISED THE CLUSTER COULD DO IT AT ALL, which is itself worth noting.** An
undiscovered capability means the IPC's navigation integration is deeper than its UI advertises --
so **the cluster's own nav menu is a CATALOGUE of the protocol**. Every nav thing it can display or
command is, by construction, something crossing the bus. Walking that menu once and writing down
what is in it is free, needs no hardware, and bounds the search before any decoding starts.

**THE FLOOR IS NOW MEASURED, from photographs, 2026-08-23.** He shot the SYNC 3 screen and the IPC
in the same moment with Google Maps navigating. Everything below is READ OFF THE CLUSTER, so all of
it necessarily crosses the bus:

    maneuver glyph      a ROUNDABOUT -- a specific type, not a generic arrow      Step.maneuver
    distance            "350 ft", matching the head unit exactly                  stepTravelEstimate
    street turned onto  "S 1100 E"                                                Step.road
    second string       "S 1100 E / S 11th E"                                     Step.cue, probably
    speed limit         "25", matching the head unit
    interaction         "Press OK to Repeat"  -- another IPC -> APIM command

**TWO PREDICTIONS IN 11c WERE WRONG, BOTH IN THE GOOD DIRECTION, and they are left above rather than
edited so the scoring is visible.** It said street name was "possible, and I'd bet against it being
cheap" -- it is plainly rendered. And it predicted "a handful of glyphs, not 47 enumerants" -- a
roundabout, plus the U-turn seen on the same drive, is not a crude four-arrow set. **Ford's
translation is materially less lossy than predicted**, which is good news for every consumer, not
just this one.

`Step.road` rendering is the sharpest single confirmation: Google documents that field as
*"primarily used for vehicle cluster and heads-up displays"*, and here is a Ford cluster displaying
it. The design intent and the observed behaviour line up exactly.

**AN ANOMALY WORTH KEEPING.** The top of the cluster reads `0 ft` and `0:00min` while the head unit
says 1.9 mi and 6 min to arrival. So the cluster HAS fields for trip totals and Android Auto is not
filling them -- either Maps sends no `destinationTravelEstimates`, or Ford does not map them. The
STEP data lands and the DESTINATION data does not. Worth knowing before anyone plans on an ETA.

**ONE EXPLANATION FOR THE ZEROS, THE DEAD REPEAT PROMPT, AND THE WORKING END-DRIVE. From two more
of his observations, 2026-08-23: "Press OK to Repeat" NEVER works, even with Google Maps.**

`NavigationManagerCallback` was read rather than recalled. It declares exactly two methods:

    onStopNavigation()     the real one -- stop navigating
    onAutoDriveEnabled()   a simulator hook, not a user action

**There is NO repeat callback.** So the prompt has nothing to call.

**FORD'S CLUSTER NAV UI IS A SUPERSET BUILT FOR FORD'S OWN EMBEDDED NAVIGATION, AND ANDROID AUTO
FILLS A SUBSET OF IT.** That single statement explains all three:

    "Press OK to Repeat"   no AA callback exists          -> dead, and NOT a fault in his car
    "0 ft / 0:00min"       Ford fields AA does not fill   -> zeros
    end-drive              onStopNavigation() exists      -> works

**AND IT CORRECTS AN OVERCLAIM MADE HERE YESTERDAY.** The end-drive discovery was written up as
proving the link is "bidirectional", with the implication that the reverse channel is rich. It is
bidirectional by EXACTLY ONE COMMAND. Nothing else Ford's cluster offers can reach an Android Auto
app, because nothing else exists to reach it with.

**A SPECIFIC, TESTABLE GUESS ABOUT THE ZEROS.** The one nav signal Ford's DBC documents is
`DistToStopover_L_Actl`, and a stopover is a route WAYPOINT. A Google Maps route with no waypoints
has no stopover, so the field reads zero -- which is exactly what the cluster shows.

**HE ASKED THE SHARPER VERSION: are those wrong numbers a GOOGLE MAPS bug?** Three candidates, and
the third is the stopover guess below, which has cooled:

    1  Maps does not send destinationTravelEstimates      -> a Maps bug
    2  Ford never maps them into those cluster fields     -> Maps innocent, unfixable
    3  they are the STOPOVER pair and the route had none  -> nobody is buggy, 0 is correct

**Against 3, and it is worth stating because it was written here confidently an hour earlier:** the
row shows a distance AND A TIME, and `APIM_Data_FD1` carries `DistToStopover_L_Actl` with no time
counterpart. A distance-plus-time pair reads far more like "to destination" than "to waypoint".

**For 1 there is precedent:** a Google Maps Community thread exists titled *"Google Maps not passing
all navigation info to Ford Sync 3"* -- exactly this class of complaint. Its contents could not be
read (the page is JS-rendered), so the title is the whole of the evidence.

**THE DECISIVE TEST IS OSMAND+, AND HE ALREADY HAS IT WORKING ON THE CLUSTER.** Same car, same
cluster, different app, one navigation session:

    OsmAnd+ fills the row, Maps does not   ->  a Google Maps bug, proven
    OsmAnd+ also shows 0                   ->  Ford never maps the fields; Maps is innocent

That is a better test than the waypoint one below and should be run first. It also generalises: any
cluster field can be attributed to app-versus-car by running the same A/B, which makes OsmAnd+ the
standing control for this whole investigation rather than a one-off data point.

**Secondary test, for hypothesis 3: add a stop to a Google Maps route and see whether that top row
populates.** If it does, the
row is the stopover pair, and it retro-confirms the DBC reading from the car's own display. It also
confirms in the other direction why that signal is useless to the gate: distance to a waypoint is
not distance to the next maneuver, which section 11c derived from the DBC alone.

**AND A BONUS CANDIDATE NOBODY HAD CONSIDERED: a phone-sourced SPEED LIMIT.** The cluster shows 25
and so does Google Maps. If that number comes from Android Auto rather than Ford's own map database,
a canbox would hand us a speed-limit source from the phone -- which matters well beyond route
intent, since TSR is dead on this car and map coverage runs around 50% of moving frames. **HE THINKS IT IS FORD'S OWN MAP DATABASE, not the phone** (2026-08-23), and if he is right that
makes it MORE useful rather than less: a Ford-sourced limit is on the bus whether or not anything is
navigating, and needs no app running.

**Cheap test, and it decides which: does the cluster show a speed limit with NO navigation active at
all?** Yes means Ford's own, always available. That would be a speed-limit source over MS-CAN
independent of the phone -- worth real money on a car where TSR is dead and mapd coverage runs about
50% of moving frames. Not established either way from one photo.

**AND THE FLOOR IS FREE TO MEASURE:** whatever the IPC DRAWS had to cross the bus. One glance at
the cluster with Maps navigating -- does it show an arrow only, or arrow plus distance, or arrow
plus distance plus street name -- bounds what a canbox could expose, before any canbox exists.

### 11d. THE WAZE NOTIFICATION -- THE WEAKEST OF THE THREE, AND IN A SPECIFIC WAY

The only primary evidence is his own screenshot (9b): a **maneuver icon** and a **distance**
("60 ft"), updating live.

**Nobody has published its structure.** Searched 2026-08-22: what exists on GitHub is Waze *alert*
and traffic scraping through a reverse-engineered non-public URL -- a different thing entirely, and
fragile. No navigation-instruction parser exists, which is consistent with CarrotPilot bridging
AMAP, Tencent and Google Maps and pointedly skipping Waze.

Field by field against the other two:

    what the gate could use      updateTrip()            Ford CAN            Waze notification
    maneuver type                47 enumerated types     unknown, undecoded  an ICON, not text
    distance to maneuver         Distance, with units    wrong quantity      text, parseable
    time to maneuver             remainingTimeSeconds    unknown             no
    road turning ONTO            Step.road               unknown             no
    current road                 Trip.currentRoad        unknown             no
    lane guidance                lanes[] + lanesImage    unknown             no
    destination + ETA            destinationTravelEst.   StopoverType?       no

**THE ASYMMETRY IS THE FINDING, AND IT IS BACKWARDS FROM WHAT YOU WOULD WANT.** The notification
gives the DISTANCE cleanly, as text -- and the MANEUVER as a bitmap. So the field that is trivial
to parse is the one the gate treats as a bound, and the field that decides whether the gate fires at
all has to be recovered by hashing icons against a known set, and re-done at every Waze redesign.

**WHICH CREATES A FAILURE MODE THE EXISTING RULE DOES NOT COVER, and a bridge author must handle
it.** The rule in `source.py` is: an instruction you cannot classify is `unknown`, not the nearest
label and not silence -- and `unknown` refuses, which is the safe direction for ONE strange glyph.
But if a redesign breaks the icon set WHOLESALE, every instruction becomes `unknown`, every one
refuses, and passing assist goes quiet for the entire route on every drive.

**So a bridge must tell "this one glyph is new" from "I cannot read any glyph."** The second is a
HEALTH failure, not a classification result: it should stop publishing and let the consumer age it
out, which returns the feature to today's behaviour. The first stays `unknown` and refuses. That
distinction belongs in the bridge, because the consumer cannot see it -- from `routeIntentBP` a
broken parser and a genuinely strange junction look identical.

### 11e. WHAT TO MEASURE, cheapest first

1. **The cluster, with Google Maps navigating.** One glance. Bounds what crosses the bus, needs no
   hardware, and he has already offered.
2. **The Waze notification, PARKED.** Dump it with any notification-inspector app, or
   `adb shell dumpsys notification --noredact`, with a route set in the driveway. **No driving
   required**, five minutes, and it is the cheapest unknown on this whole list -- it decides whether
   the fallback transport is viable at all, and nobody has ever looked.
3. **`bp_can_nav_diff.py` on a Maps-navigating drive.** One drive; the control is already recorded.
4. Everything else waits on the canbox.

