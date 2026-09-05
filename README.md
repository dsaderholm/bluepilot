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

- **Passing assist's road assumptions, though not its tuning.** The detection itself travels with the
  retrofit, because it reads the CCM radar and IPMA camera you also installed rather than anything
  Fusion-specific. What does not travel is the assumption of left-hand passing lanes and US lane
  discipline throughout. Two things worth separating from portability: it commands nothing on any car
  today, for the reason in its own section, and if your car has factory blind-spot monitoring on a
  bus openpilot can read, you are better off than this one — several of its refusals exist only
  because that data is unavailable here.

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
**3.3 mph per second**, and not because of any parameter. openpilot asserts the cruise button
continuously and never releases it — but the car's own steering-column module transmits the same
message with the button released ten times a second on the same bus, interleaved with ours, so the
car sees a stream of taps rather than a hold and acts on about one press every 0.30 seconds.
Blocking the column's messages would also block the driver's own presses, so the rate is fixed.

Every feature that slows the car has to fit inside that budget, and a few requests that sound
reasonable are simply impossible because of it. Where a slowdown genuinely cannot fit, openpilot
takes the ACC command directly instead — see the slowdown section below.

- **A button contract settled on the road.** `RES +` creates or raises a HOLD — the driver's own set
  speed — and `SET −` lowers it or, with cruise off, hands the speed back to Speed Limit Assist.
  Every other feature keeps working against a hold: curves still slow the car, hazards still fire,
  and the speed returns to the driver's number afterwards rather than to the posted limit.
  **Which CAN signal carries `RES +` differs between Ford wheels**, and reading the wrong one is
  silent: the button simply never arrives, the dash moves because the stalk talks to the powertrain
  directly, and openpilot never learns the driver asked for anything. Both known signals are read
  here. If presses seem not to stick on some other Ford, that is the first thing to check.
- **A hold ends when you hand it back.** Bring the set speed to exactly what Speed Limit Assist
  wants and the hold is gone — that is the only way out that does not involve disengaging. It is
  checked against SLA's own number rather than whatever is currently governing the car, so it still
  works while a curve or a lead is braking, and it ignores a limit merely *remembered* from a road
  you have already left. A hold pinned to a place is exempt: agreeing with today's posted limit is
  not a reason to forget somewhere you deliberately marked.
- **Holds pinned to a location.** Tap the set-speed box and that hold returns whenever you drive
  through the same place; a small dot in its corner marks one that is pinned, and a hollow ring means
  tapping would create one. A hold you set by hand always outranks a pinned one.
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
- **The big number is what the car is being driven to** — the driver's hold if there is one,
  otherwise the posted limit plus offset. The slot above it can only say one thing, so it is ranked:
  the dash set speed whenever the car is not at its target (something is actively pulling it down,
  and seeing this during ordinary cruising means something is fighting the driver); then the speed
  cancelling the hold would give back; then a pin being offered; then the word `HOLD`; then `MAX`.
  The box tints while a hold owns the number and stops tinting when something else takes it, so
  whether your number is actually in charge reads without being spelled out.
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

### Ford ACC passthrough, and the complete stop

**This is Ford-specific and validated on exactly one car.** It needs a Ford whose forward camera
keeps computing ACC with openpilot's relay open, which is measured on a 2020 Fusion with retrofitted
Edge ADAS parts and nowhere else. On any other car it may silently spend most of its time falling
back to openpilot's own longitudinal, which is the thing it exists to avoid.

Under openpilot longitudinal control the relay is open, so the camera's ACC command never reaches
the car — openpilot is expected to author it instead. But the camera still has all its inputs, and
it is still computing. The passthrough reads its `ACCDATA` and republishes it: **the car drives like
stock adaptive cruise because the commands are Ford's, and openpilot only carries them.**

Everything above it keeps working. Speed limits, curve slowing and the driver's held set speed all
still act through the cruise buttons, which change what the camera is aiming for — so the decision
of *what speed* stays openpilot's while the choice of coast, engine brake, precharge or friction
stays Ford's.

**The complete stop is what it is for.** Ford's set speed cannot go below 20 mph, and stock ACC
completes a stop only when its own radar has a lead — so a stop sign or red light on an empty road
is the one thing the car cannot do. openpilot sends the braking instead of Ford for a bounded
window, then hands straight back. It never takes over when a lead is close, because Ford's
stop-and-go already owns that case, and **it has never yet been observed bringing a car to a
standstill and holding it** — the trigger is measured against recorded drives, the braking is not.

**Whatever it sends is never softer than what Ford asked for.** Taking the command means Ford's
command stops reaching the car, and nothing originally guaranteed ours was at least as strong: on
one measured approach to a stopped vehicle the override held the command for nine seconds while
requesting a tenth of the deceleration Ford was already asking for. Ford's own request is now a
floor, so taking over can only ever add braking.

**It will not take the command below 25 mph, and that floor is NOT the protection it was thought to
be.** The rule came from replayed drives in which every takeover starting under Ford's own 20 mph
floor made the forward camera assert cancel, while those above it appeared tolerated. The first
three takeovers that were actually *driven* contradicted the second half of that: two of them armed
at 34 and 40 mph — well above the floor — and both provoked a cancel about 1.6 seconds later that
never released. Stock ACC was gone for the remainder of both drives and came back only after the car
was restarted.

So the honest position is that **taking the command away from the camera provokes a cancel at any
speed measured so far**, and the floor prevents only the worst version of it. What separates the one
tolerated takeover from the two that latched is not yet known; it is not the arming speed, and it is
not the size of the disagreement — the tolerated one had the largest.

**Losing the cancel is now recoverable without stopping the car.** Refusing to forward a cancelled
frame is what made the latch permanent: the camera's commands stopped reaching the car, so it could
never observe the car obeying it again and never had a reason to relent. After five seconds of a
cancel that this feature provoked, Ford's frame is forwarded again with that one bit cleared, for up
to thirty seconds, so the camera gets the evidence it was being denied. Whether it actually relents
is the open question and the reason both toggles still ship off.

**Both ship off**, and the on-screen ACC readout turns violet and reads `OP STOP` whenever openpilot
has taken the command, so it is visible rather than inferred.

### Passing assist

**This moves nothing today, on any car.** What it is meant to become is a pass the driver never
initiates: it notices the lead is slower than the set speed, signals, checks its gates while the
signal is running, and changes lanes with no stalk input. The decision, the maneuver sequence, the
commanded turn signal and the lane-change request are all written and tested — but the actuation
gate asks for a working rear sensor **on the side being entered**, and nothing produces that data
yet, so every drive so far has published its decision to the screen and the log and commanded
nothing. Read the rest of this as a design being measured daily on the road, not as behavior you
would experience if you flashed it.

The rear sensor is the gate rather than the setting, and the distinction is the whole safety
argument. `PassingAssistActuate` ships **on** — it is the driver's stated intent. Whether the car
can currently back that intent with a sensor is a separate question, asked before every commanded
output. Suggesting a lane the car cannot see behind is acceptable, because the driver still looks;
moving into it is the failure the module exists to prevent.

What the decision is built from, on a car with no blind-spot input routed:

- **Adjacent-lane occupancy from the front radar.** The forward radar's returns are read for traffic
  beside and ahead rather than only in-lane, which is the difference between "the next lane is empty"
  and "the next lane has not been checked." The rule it was built under: evidence that opens a
  maneuver must never be cheaper than evidence that refuses one.
- **Oncoming detection, which is how it tells a divided highway from an undivided one.** Traffic
  closing head-on in a candidate lane vetoes that side for 90 s. A single frame is not enough —
  sightings must corroborate across frames before they count, which cut one drive's oncoming returns
  from 511 to 166 with no veto lost.
- **Which lane it is in, anchored on the right road edge.** The map supplies how many lanes the road
  has; the model's right road edge says how far the shoulder is, which converts to a lane index by
  division. That edge is readable on only 5 to 15 percent of freeway frames, so the index is latched
  between readings and dropped on a lane change or after twenty seconds, whichever comes first. The
  left road edge is not used at all: on multi-lane freeway it was trusted on zero frames out of
  3,060.
- **All four lane lines, which is the only thing that speaks from a middle lane.** From the middle of
  a wide road the right edge is out of reach and there is genuinely a line to the left, so the two
  witnesses above both fall silent at once. A line beyond the left boundary and a line beyond the
  right together mean the car is strictly between two lanes — exact on a three-lane road, a narrowed
  range on anything wider. Both outer lines missing is a contradiction on a multi-lane road and
  claims nothing rather than picking one.
- **Signal first, then confirm.** The lamp comes on the moment a slow lead is noticed, and the gates
  are checked during that second of signalling rather than before it. The crossing starts only if
  every gate has held clear for the whole lead. This is deliberate and is the opposite of the usual
  ordering: it buys the decision a second of road without the driver waiting on a car that appears to
  have decided nothing.
- **No pass while the road is bending.** Above a configurable lateral acceleration — 1.3 m/s², the
  same threshold the curve controller uses to decide the car is entering a turn — a suggestion is
  refused. This is a physical limit rather than a preference on this car: the retrofit power steering
  needs the car slowed before it will accept a hard steering command, so in a bend it is already near
  its authority and a lane change asks for steering on top of what the curve is taking. A crossing
  already underway is never called off by it.
- **Two ways to recognize an exit the driver is taking**, because the geometry alone cannot: ending
  up in the outermost lane, and slowing down after moving right with nothing ahead to slow for. Both
  buy a long silence, so the system does not suggest moving back left at the gore point. Every
  right-hand change is counted by which test recognized it, including the ones none did.
- **Fussier when there is nothing to be made up.** How far over the posted limit the driver has asked
  to go — from the set speed or a held cruise speed — scales how much slower a car must be before
  passing it is worth suggesting. It only ever adds patience: at 8 mph over the limit the configured
  thresholds apply unchanged, and with no posted limit known nothing changes at all.
- **Keep right**, after five seconds clear of a reason to be left.
- **Holds for a pass that is about to become wrong** — the lead braking, the gap closing, traffic
  arriving from behind, the driver taking the lane themselves, and dropping below the speed floor.
  Falling under it cancels an armed maneuver outright rather than letting it complete.
- **A per-drive history of the last 60 drives**, and `tools/bp_passing_report.py` to read it: what it
  wanted, what refused it and for how long, and which geometry term did the refusing. Every number
  above was set from those reports rather than chosen.
- **It says what it is thinking while it drives** — on the onroad panel, and behind a toggle on the
  instrument cluster's own lane lines, which is the only vocabulary this cluster has. The intent is
  that a refusal is legible at the time rather than only afterwards in a log. The panel carries a
  row of boxes, one per lane the map says the road has, drawn in three states so an unavailable
  estimate cannot be mistaken for a confident one: filled where the car has been placed, outlined
  where it is one of several candidates, and all empty where the position is unknown.

What it cannot do, stated plainly because the log looks the same in both cases:

- **A painted median or a center turn lane still reads as a passing lane.** Correct paint, correct
  width, and a road edge the camera is confident about — nothing in this sensor set separates one
  from a travel lane, and three attempts to fix it in software failed. It suggested exactly this on a
  real road. Traffic behind, from a rear radar, is the candidate fix; there is no software one.
- **The turn signal cannot be read back.** openpilot never sees its own transmissions, so a commanded
  signal lights the lamp and leaves `carState` reading the stalk, which is off. The lane-change
  request therefore reaches the model's own state machine directly instead — which means this is not
  "nudgeless lane change with a timer," and the blind-spot and torque checks that apply to a stalk
  change still apply here unchanged.

### Slowdowns the cruise buttons cannot deliver

**Ford-specific, and unproven on the road at the time of writing.** The trigger is measured against
recorded drives; the braking behaviour it produces has never been driven, so treat everything below
as a design that has been checked rather than a feature that has been used.

The set speed is the only lever a stock-ACC car has, and it moves at about **3.3 mph per second**.
That is not a tuning choice: openpilot asserts the cruise button continuously, but the car's own
steering-column module transmits the same message with the button released ten times a second on the
same bus, so the car sees a stream of taps rather than a hold and recognises roughly one press every
0.30 seconds. Blocking the column's messages would also block the driver's own presses.

Most of the time that budget is plenty. Twice it is not:

- **A corner that arrives faster than the lever moves.** Approaching a 28 mph bend at 77, the map
  asks for a 49 mph reduction — fifteen seconds of tapping, and about 650 m of road. Measured on one
  such approach the car was already pulling 5.2 m/s² of lateral acceleration, against a 2.4 target,
  while the set speed was still walking down.
- **A stopped car the radar cannot see.** The driving model spots it, and the request goes to Ford's
  20 mph floor — which from highway speed is the same enormous gap, closed at the same 3.3 mph/s.

In both cases openpilot takes the ACC command directly for a bounded window and brakes properly,
then hands back. Authoring the command has no button-rate limit, so the same 49 mph reduction needs
about 200 m instead of 650.

**It arms on the size of the gap, not on how urgent the plan feels.** A corner or a radar-blind lead
wanting more than 20 mph below the current speed qualifies — six seconds of tapping — and anything
smaller is left to the buttons, which close it quickly enough. Measured across four drives, gaps that
large occur on under 1% of engaged driving: one or two takeovers per drive, and none at all on two of
the four. A posted speed limit never qualifies, however large the drop: nothing is arriving, and the
buttons walking the number down is the right answer.

**It will not take the command below 25 mph** — but that floor turned out to protect far less than
it was designed to. It came from replayed drives: every takeover beginning under Ford's own 20 mph
floor made the forward camera assert cancel, while those above appeared tolerated. The first three
takeovers actually *driven* contradicted the second half. Two armed at 34 and 40 mph, comfortably
above the floor, and each drew a cancel about 1.6 seconds later that never released — stock ACC gone
for the rest of both drives, recoverable only by restarting the car.

What separates those two from the one takeover that was tolerated is not known. It is not the arming
speed, and it is not the size of the disagreement: the tolerated one disagreed with Ford the most,
while one of the latching pair matched Ford's own braking request to within 0.01 m/s² for its first
twelve seconds.

**Losing the cancel is recoverable now without stopping.** Refusing to forward a cancelled frame is
what made the latch permanent — the camera's commands stopped reaching the car, so it could never see
the car obey it again. Five seconds after a cancel this feature provoked, Ford's frame is forwarded
again with that bit cleared, for up to thirty seconds. Whether the camera relents is the open
question, and it is why this ships off.

Handing back has two shapes. When the corner ends the gap closes, Ford is still engaged, and it
simply carries on. When it ends in a stop, the override holds the standstill rather than releasing
into a creep, and does not pull away on its own — resuming is the driver's, unless **Pull Away From
Stops Automatically** is switched on. (An earlier version of this claimed Ford's own stop-and-go
state was entered on the way down. That was withdrawn: the signal it rested on is OR'd with wheel
speed, so a stopped car reports it regardless and it proves nothing.) The moment the radar acquires a
lead the override hands back on that frame, because Ford's stop-and-go is years of calibration this
has no business replacing.

### Giving the forward camera the GPS its own car withholds

**Ford-specific, unproven on the road, and — stated plainly because it was written the other way
first — this is NOT what makes traffic sign recognition work.** The defect below is measured and
real. It was also, for several hours, believed to be the reason this car's camera read no speed
limit signs. It isn't: the camera has since read one, correctly, with this fault fully present. What
the feature fixes is a genuine fault the camera reports every drive. What it does not fix is sign
recognition, and anyone reading this looking for that should stop here.

The camera is not broken and does not think it is in an unsupported country. Asked directly, it
reports traffic sign recognition **available**, in **mph**, with no region complaint — it has
dedicated fault codes for "country not supported" and "region not supported" and emits neither. What
it does report, continuously, is `U0253 — Lost Communication With Accessory Protocol Interface
Module`, additional fault symptom **Missing Message**.

That fault turns out to be literally true. The camera is a listed receiver of three GPS messages
from the SYNC module, and across a full drive:

| message | contents | frames |
|---|---|---|
| `0x462` `APIMGPS_Data_Nav_1` | latitude, longitude | **3494** |
| `0x463` `APIMGPS_Data_Nav_2` | UTC date and time, position accuracy, compass, GPS fault flag | **0** |
| `0x464` `APIMGPS_Data_Nav_3` | heading, altitude, satellites, speed, accuracy | **0** |

One of three arrives. The camera spends every drive waiting on two messages that are never
transmitted, and reports `no navigation data` continuously as a result. It is a real fault with a
real cause, independent of anything to do with reading signs.

None of that is map data — there is no map speed limit anywhere in those messages. It is plain GPS
telemetry, of the kind any navigation-equipped SYNC broadcasts whether or not a destination has ever
been entered. Why this car withholds two of the three is not yet known.

**So openpilot sends them itself.** The comma has its own GPS receiver, and every field those two
messages carry — time, heading, altitude, satellite count, accuracy — is already in it. The two
messages are synthesized and placed on the camera's bus at 1 Hz, the same rate the car sends the
message it does send.

Three properties worth stating, because this writes to a bus:

- **It commands nothing.** These messages carry position, time and heading. There is no actuator
  field in either of them; they cannot influence steering, throttle or braking, and the vehicle side
  of the bus is untouched.
- **It stands down on its own.** The car is watched for the real messages, and one received frame
  disables the synthesizer for the rest of the drive. openpilot never competes with a working SYNC.
- **It cannot take the car off the road.** The whole path is latched off on any failure, and a
  missing attribute disables the feature rather than the car — a rule this fork learned the hard way
  when a follow-distance convenience once made the car undrivable.

One approximation is deliberate and flagged: the comma reports position accuracy in metres, while
Ford's signals want dilution of precision, which is satellite geometry the comma does not expose.
The conversion is a documented estimate and is the only part of the mapping that is not a direct
measurement.

**And for its entire life until 2026-08-22 it transmitted nothing at all.** Three drives were
measured on every bus: `0x462` arrived from the car 905 times and was forwarded 894, while `0x463`
and `0x464` appeared **zero** times — not from the car, and not from openpilot either. The car
process subscribed to the wrong GPS service: `gpsLocationExternal`, which is the receiver on a comma
two, on hardware that publishes `gpsLocation`. The fix is one line and it is in; the feature has
still never been driven in a state where it could do anything.

That failure is worth stating rather than quietly correcting, because it also sharpens the point at
the top. The one sign this camera has ever read came with `U0253` asserted, `no navigation data` on
every frame, **and** this synthesizer silent — so that read had no GPS assistance from any source
whatsoever. Nav data and sign recognition are independent, and this is the second measurement
saying so.

One rate difference, since it is not obvious: `gpsLocation` publishes at 1 Hz against
`gpsLocationExternal`'s 10, so on this hardware the transmitted fix can be a full second old —
roughly 15 m at 35 mph. Fine for a camera that wants coarse position; not a precise one.

Setting is **Send GPS To The Camera**, and it ships on.

### Only warning about steering when the car is actually out of its lane

openpilot raises **"Take Control — Turn Exceeds Steering Limit"** when the steering controller asks
for more curvature than it gets. That is a fact about the command, not about where the car ended up,
and on this car the two come apart badly. Reconstructing the alert condition at 100 Hz across 701
recorded segments — 24 routes, interstate through city — gives 61 alerts, and this is where the car
was sitting during each of them:

| | samples | median | p90 | p99 | worst |
|---|---|---|---|---|---|
| while the alert is up | 2,998 | **0.24 m** | 0.67 | 1.43 | 1.67 |
| ordinary engaged driving | 2,403,770 | **0.06 m** | 0.22 | 0.70 | 1.83 |

Twenty-four centimeters off center in a 3.7 m lane is a corner being taken correctly. Twenty-six of
the 61 alerts never reached even 0.30 m. Each one repeats a chime for two seconds, so the practical
result is a driver trained to ignore the whole class — including the episodes in that same data that
reached 1.43 m and 1.67 m, which are nearly half a lane and are the ones worth seeing.

So the alert is held back until the car is more than **half a meter** from the center of its lane.
On the recorded drives that is 61 alerts down to 24, with every wide episode kept.

**It changes nothing the car does.** Saturation still reaches every controller that consumed it
before — no gain, limit, command or lane position moves. The only thing gated is whether the warning
is shown.

**And it fails open, every time, on purpose.** Whenever the lane cannot be measured — no lane lines,
lane lines the model is not confident in, a stale or invalid model, a settings store that will not
answer — the warning appears exactly as it does upstream. That is not a rare path: 14 of the 61
alerts above are unmeasurable, most of them at 15–40 mph on unmarked streets and intersections, and
all 14 still fire. Making those quiet would mean judging lane position from something other than
lane lines, and the obvious candidate measures past the shoulder rather than the lane.

Setting is **Only Warn When Out Of Lane**, and it ships on. Turning it off restores stock behavior
exactly.

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
  reached from SunnyLink, which is how you configure a comma 4 in practice

- **`tools/bp_passing_report.py`** — every pass passing assist wanted, what refused it, for how long,
  and which geometry term did the refusing

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
