# Route intent: can openpilot know where he is GOING?

Researched 2026-08-17 for the passing assist session. The question came from him, and the motivation
is the right one -- he misses Waze putting turn-by-turn on his IPC, and asked for it back *"so
OpenPilot could know more about where I'm going, like if I am going to take an exit or not."*

**Short version: the phone cannot supply it, the car barely can, and the one usable signal is already
on the wire and has never been looked at.**

Everything below is marked MEASURED or UNVERIFIED. Two claims in this area were made confidently and
turned out to be wrong within the same day, both by reasoning past the edge of what had actually been
checked; both corrections are recorded here rather than quietly fixed.

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

**And what it carries is thinner than the phrase "navigation data" suggests.** The API is
`NavigationManager.updateTrip()`, and Google's own documentation describes it as communicating a
`Trip` containing `Step` (turn-by-turn instructions) and `Destination`, with the note that "the
information provided in this call can be used by the vehicle's cluster and heads-up displays." A
maneuver, a distance, a destination name. **A turn arrow, not road geometry.** `mapdExtendedOut.path`
already gives curvature ahead, which is strictly more useful than "right turn in 500 m".

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

**The discriminator he is running: does Google Maps still show turns on his IPC?** If yes, the car,
SYNC 3 and Android Auto are all working and it is purely Waze's implementation -- most likely an
unnoticed regression from their rewrite onto the Car App Library. If Maps is also dead, it is instead
the acknowledged Android Auto regression that broke cluster and HUD guidance for both apps, which
Google has said is being fixed.

**Either way it does not unblock anything here.** Worth wanting for his cluster; worth zero as a
feature dependency. **Do not sequence any work behind it.**

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
Useful to CONFIRM a prediction after the fact -- for scoring 5a offline, in fact -- and useless to
make one.

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
2. **Score `predicted` against the turn signal** offline. The blinker says what he actually did;
   `predicted` said what mapd thought he would do. That is a free, self-labeling accuracy measurement
   over every fork in every route already recorded from here on.
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
