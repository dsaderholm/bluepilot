# Rear radar mount -- measurement list, design brief, and a parametric first pass

Written 2026-08-19. Companion to `BP-REAR-RADAR-PLAN.md` section 2, which is the authoritative
source for the requirements. Nothing here contradicts it; where it looks like it might, that is
called out under "Two places this brief disagrees with something" below.

**This is a design brief and a parametric starting point. It is not a part.** No dimension of the
car in this document has been measured. Every number describing the Fusion is either quoted from
the plan (which mostly read it off a photograph) or is a placeholder chosen so the geometry closes.
The parametric file at `tools/rear_radar_mount.scad` is arranged so that filling in the
measurements is the entire remaining job on the CAD side.

Confidence is stated per section, the way the plan does it.

---

## 0. The decision, and the one number that can overturn it

**The layout is settled: radar LOW, behind the unpainted textured black lower valance.** The
reason is metallic paint. Section 9 of the plan read the rear bumper off a photograph and found it
is two zones -- body-color metallic from the plate down to a moulding line, then a separate dark
grey textured lower section carrying the exhaust outlets. Metallic and pearl paints contain
aluminium flake and attenuate 76 GHz badly. Bare textured polypropylene does not. Mounting behind
the unpainted section removes the attenuation problem with no aperture, no radome and no cutting.

That is the right reason and it is the cheapest way to solve that problem. Two things it depends on,
both **UNVERIFIED**:

1. **That the lower valance on HIS car is genuinely unpainted.** The plan read this from a
   photograph. See M0.
2. **That the radar fits behind it.** See M1, and read the next paragraph before doing anything
   else.

> **The plan's own depth estimate, if it is right, kills this layout.** Section 2 says "typical
> cover-to-beam clearance on a Fusion rear bumper is 40-70 mm" and marks it UNVERIFIED. The
> absolute arithmetic floor for an ESR behind a cover is **80 mm (3.15 in)** -- and that is with a
> steel back plate, a steel yoke, minimum rib clearance and the connector not exiting rearward.
> The design as drawn wants **100 mm (3.9 in)**.
>
> So the plan's estimate and the requirement do not overlap. One of them is wrong and only a tape
> measure decides which. **This is why M1 is first and why nothing should be printed, cut or
> ordered before it exists.** It is also the single most likely way this whole layout gets
> overturned, and it has nothing to do with paint.

Confidence that the layout is right IF the depth allows: **high**. Confidence that the depth
allows: **low, and the plan agrees it is low.**

---

## Two places this brief disagrees with something

Stated separately rather than folded in, per the plan's own house style.

### 1. This brief is built for the ESR. The plan's current recommendation is not the ESR.

Section 8 of the plan, added 2026-08-03, revises the part choice: **buy a second
`JX7T-9G768-AC` Delphi MRR**, not a Delphi ESR 2.5. The reasoning there is strong -- the MRR needs
no gateway frames, the decoder already runs on this car every drive, and there is a live reference
unit in the same vehicle to compare bench output against.

This document was commissioned for the ESR and its hard requirements are ESR requirements. That is
fine and the two are reconcilable, but **the difference matters more for the bracket than for
anything else in the project**, in three specific ways:

| | ESR 2.5 | Delphi MRR (`JX7T-9G768-AC`) |
|---|---|---|
| Envelope | 173.7 x 90.2 x 49.2 mm, a brick | **smaller. UNVERIFIED how much** -- measure the one already in the car |
| Azimuth trim | +/-8 deg over CAN, hard-limited by the signal range | **unlimited** -- we decode azimuth ourselves and subtract a constant |
| Elevation trim | none | none. Identical problem |
| Depth budget | the binding constraint of the project | **probably not binding** |

**The elevation constraint is identical for both parts, so everything in this brief about the shim,
the level pad and the aim procedure applies whichever radar is fitted.** The azimuth arc slot is
over-engineered for an MRR and exactly right for an ESR. The envelope is a parameter.

**The cheap move: measure the MRR already fitted to the front of this car.** It is the same part
number as the one that would be bought. That turns the largest unknown in the cradle from a
datasheet lookup into a caliper reading, today, without buying anything.

### 2. The plan gives no elevation tolerance, only a direction

Section 2 says elevation "must be mechanically correct" and to "verify by whether the sensor sees
the road surface or nothing". That is right but it is not a number, and a bracket cannot be built
against it. Section 5 below derives one. This is an addition, not a correction.

---

## 1. Measurement list

Ordered so the first real measurement decides the layout. **M0 is a look, not a measurement.**
M1 through M4 are ten minutes with a tape measure and a phone. M5 needs the module in hand, and it
changes M1's threshold, so read it before you conclude anything from M1. M6 and M7 need to get
under the car.

Photograph everything with the tape in frame. A number without a photograph is a number that gets
re-measured.

Millimetres first because that is what the CAD and the calipers want; inches in brackets for the
tape.

### M0. Confirm the lower valance is unpainted. (10 seconds)

Look at the lower section of the rear bumper in daylight. Textured, matte, dark grey or black, no
gloss, no metallic sparkle when you move your head -- that is unpainted moulded plastic and the
whole reason for the low mount. If it turns out to be painted body color, stop and re-read: the
paint problem is back and the choice between an aperture, a radome or the external mount reopens.

Also feel it. Unpainted textured PP feels slightly waxy. Painted plastic feels like the rest of the
car.

### M1. Clear depth behind the valance at the intended center point. **THE DECIDER.**

Reach in from underneath, or lower the valance's lower edge enough to get a tape past it. Measure
from the **inside face of the valance skin**, straight forward toward the front of the car, to the
first hard thing you hit: the bumper reinforcement beam, the foam energy absorber, or body panel.

Measure at the lateral center and again 150 mm (6 in) either side, because a bumper cavity is not a
constant depth.

| Measured clear depth | Verdict |
|---|---|
| **under 80 mm (3.15 in)** | **Behind the valance is DEAD for an ESR.** Go to the external fallback, section 4. This is the floor with steel plates and everything trimmed to the bone; there is nothing to negotiate below it. |
| 80-100 mm (3.15-3.9 in) | Possible, but only with a steel back plate and a steel yoke, a rib clearance cut to the minimum, and the connector NOT exiting rearward. Tight enough that M5 decides it. |
| **100-130 mm (3.9-5.1 in)** | The design as drawn fits. Printed cradle, printed or steel yoke. |
| **over 130 mm (5.1 in)** | Comfortable. Room left to move the antenna standoff for RF tuning, which is worth having because that standoff is a bench question nobody has answered. |

Do not include the foam energy absorber in the "clear" number unless you have decided to trim it,
and do not decide that blind: it is crash structure.

### M2. Height above ground to the intended antenna face center, UNLOADED.

Car on level ground. Ground to the point where the middle of the radar's face would sit.

Delphi's window is **300-860 mm (11.8-33.9 in)**. The low mount deliberately sits near the bottom
of it, so this is the number with the least margin in the design. If it comes out under 380 mm
(15 in), move the mount upward toward the moulding line before doing anything else.

### M3. Height above ground at the same point, LOADED.

Load the trunk and the back seat the way they are on a road trip, then measure M2 again. Or measure
ground-to-wheel-arch-lip empty and loaded and take the difference.

**This is the measurement people skip and it does two things at once.** It subtracts directly from
M2, and -- because loading the rear pitches the body nose-up -- it aims a rear-facing radar
downward. Section 5 has the arithmetic.

| Loaded face height | Verdict |
|---|---|
| under 300 mm (11.8 in) | Below the Delphi floor when loaded. Raise the mount. |
| 300-350 mm | Inside spec but with no margin and more ground clutter. Raise it if the packaging allows. |
| over 350 mm | Fine. |

### M4. Pitch of the surface you intend to bolt to, relative to true level.

Phone clinometer, on the rear face of the bumper reinforcement beam.

Do it properly or the number is worthless: put the phone on the rocker panel or the door sill
first, hit the relative-zero button so the parking spot's own slope cancels, then move it to the
beam. Record the number **and its sign**.

This is the number that picks the shim. Without it every shim in the set is a guess.

### M5. The module. Needs the radar in hand, and it modifies M1.

Once you have the ESR (or the MRR) on the bench:

1. **Envelope**, with calipers. All three axes. Do not trust the datasheet's
   "including mounting features" phrasing.
2. **Its own mounting features.** Ears with through-holes, blind threaded bosses, or a slide rail?
   Spacings and thread size.
3. **Which face the connector exits**, and how deep the mated backshell sits, plus about 40 mm
   (1.6 in) for the harness to turn.
   > **If the connector exits the REAR face, add that whole number to M1's requirement.** A 55 mm
   > backshell-plus-bend turns the 100 mm requirement into 155 mm (6.1 in), which almost certainly
   > kills the behind-the-valance position on its own.
4. **Is there a vent membrane?** A small round grey filter disc, usually near the connector. If
   there is one, it must not end up as the lowest point of the installation and must not sit in the
   direct spray path. **UNVERIFIED** whether the ESR 2.5 has one.
5. **Face squareness.** Digital angle gauge on the antenna face, then on the mounting datum. If
   they differ, that difference goes into the shim calculation. This error is invisible once the
   thing is bolted up and nothing on the CAN bus reports it.

### M6. Beam attachment survey. Under the car.

Photograph the rear bumper reinforcement beam and its crash cans from below, tape in frame. What
you are looking for, in preference order:

1. **Existing bolts or threaded holes** in the beam or its brackets.
2. **A flange or open channel** a clamp can grip.
3. Nothing usable, in which case say so, because that changes the design.

Also note whether the beam is a closed section. If it is, you cannot get a nut behind it and a
through-bolt is off the table.

Find the tow eye socket while you are there. The plan says this car has one behind a pop-out panel
and that it is usually off-center. Measure where.

### M7. Clearances at the intended position.

- **Lateral clear width** at the intended depth, between parking sensor cones, exhaust hangers and
  anything else in there. The cradle needs about **190 mm (7.5 in)**.
- **Metal in front of the face.** Anything at all between the antenna and the outside world --
  the beam itself if the radar would sit behind it, a hitch, a plate frame, exhaust tips. **Any
  metal in the boresight kills that position outright.** A metal bracket behind the radar is fine.
- **Valance thickness**, with calipers, at a trimmed edge or through an existing sensor aperture.
- **Rib depth**, by feel. Reach up behind the intended spot and find how far the internal ribbing
  stands proud of the inside of the skin. This sets how close the cradle can get to the skin.
- **Exhaust outlets.** The plan says both are at the outer corners and the center is clear. Confirm
  and measure the gap.

---

## 2. The design: behind the lower valance

Confidence in the topology: **medium-high**. Confidence in any dimension touching the car:
**none, by construction.**

### Shape of it

Five pieces, in a chain from the radar to the car:

```
  radar
    |  captured in a shell, clamped by a strap, on closed-cell foam
  CRADLE
    |  flat vertical interface, 4 x M6
  SHIM            <-- ELEVATION.  a printed wedge.  the angle IS the correction
    |
  YOKE
    |  flat horizontal interface, 3 x M6 through arc slots + a center pilot
  LEG             <-- AZIMUTH.  loosen, swing, read the dial, retighten
    |
  rear bumper reinforcement beam
```

The two adjustments are on different joints and on different planes, so setting one does not
disturb the other. That is the whole point of the layout: elevation cannot be fixed later, so it
gets a joint that is set by a part number rather than by feel.

### How elevation is set and held

**Set by which shim you install, not by how hard you tighten anything.** The wedge angle is the
correction. There is no slot, no pivot and no lock nut whose slip would change the aim.

The shim family is printed at 0.25 degree steps from -3 to +3. Over the 56 mm bolt span, one degree
is 0.98 mm of thickness difference, so a 0.2 mm layer resolves about 0.2 degrees and a printer
holding +/-0.15 mm holds about +/-0.15 degrees. **The shim is more accurate than any measurement
you will make of the car**, which is the right way round.

**Held** by four M6 through-bolts with washers both sides and nyloc nuts. Not heat-set inserts,
not thread-forming screws into plastic: this joint is loaded in tension across print layers, which
is the one direction a printed part is weak in. A fastener creeping here is an elevation change
with no symptom on the bus.

The wedge is engraved with its angle and with **UP**. Installing one upside down inverts the
correction and doubles the error, silently.

### How elevation is verified, with no target board

This is the procedure, and it works on a driveway:

1. **On the bench, before anything is assembled**: digital angle gauge on the module's antenna
   face, then on its mounting datum. Record the difference. That is `radar_face_squareness`.
2. **Car on level ground.** Gauge on the rocker panel or door sill. Hit relative-zero. That
   cancels the slope of wherever the car is parked.
3. **Gauge on the strap's level pad**, which is a flat printed datum parallel to the antenna face
   normal. The reading is the boresight elevation relative to the car.
4. Swap shims until it reads the target. Not zero -- see the squat bias below.
5. Re-check with the trunk loaded and confirm it lands where section 5 says it should.

**What this does and does not prove, stated plainly.** It proves the CRADLE is level. Whether the
radar's electrical boresight is square to its own case is a different question, and step 1 only
catches the mechanical part of it. **An electrical boresight offset can only be found on a bench**
-- radar on a stand at a known height pointed down a flat empty lot, sweeping the pitch and
watching where the ground clutter floor moves. That test is worth doing during the bench session
that is already planned, because it costs an extra twenty minutes and there is no other way to
find it.

### How azimuth is coarse-set

Three M6 bolts through arc slots in the leg, on a horizontal interface, about a center pilot boss.
Loosen, swing, retighten. Range is +/-8 degrees, matched to the CAN trim range on purpose.

The yoke's pad is larger than the leg's, and its exposed rim carries an engraved 2-degree
protractor with a deeper mark at zero. The leg carries a pointer. **Write down what the dial reads
when you tighten it**, because `CAN_RX_ANGLE_MOUNTING_OFFSET` has only 8 degrees to give and you
want to know how much you have already spent mechanically.

Two printed-in-place references help set it before power is ever applied:

- **Sight posts.** Two V-notched posts on the strap, on the boresight centerline. Sight along them,
  or stretch a string. Measure from that line to two symmetric points on the body.
- **Centerline notches.** A V groove on the underside of the cradle's bottom shelf and a witness
  mark on the strap's level pad, both at x = 0. Drop a plumb line from the car's centerline and it
  lands on them.

None of that gets you to a tenth of a degree, and it does not have to. It gets you inside the CAN
range, and the plan's own note stands: aim still matters more than the tolerance suggests, because
3 degrees is 2.6 m at 50 m. The residual gets calibrated by parking behind a target at a known
lateral offset and reading back the azimuth.

### Fastening to existing structure -- and where this is guessing

**Preference order, and it is mostly guesswork below the first line.**

| | What | Confidence |
|---|---|---|
| 1 | **An existing bolt or threaded hole in the rear bumper reinforcement beam or its brackets.** | **Guessing that any exists.** Not measured. M6. |
| 2 | **A band clamp around the beam.** No new holes in crash structure. Needs a second locating feature or the whole thing can rotate, and rotation is exactly the error that costs half a lane at 50 m. | Works if the beam is an accessible section. UNVERIFIED. |
| 3 | **The tow eye socket.** Confirmed to exist on this model by the plan. Threaded, strong. But single-point (rotates), usually off-center, and using it means giving up the recovery function or passing it through. | Position UNVERIFIED. |
| 4 | Drilling the beam. | Last resort. It is a crash structure and this is not an emergency. |
| X | **Bumper cover tabs. Never.** | Plastic that flexes with every gust. The plan already rules this out and it is right. |

**Say the guess out loud: I do not know what fasteners exist on the back of this car.** The plan
states there are no factory radar mounting points back there at all. `leg_beam` in the SCAD is a
placeholder with deliberately long slots, and the honest expectation is that it gets redrawn once
M6 exists.

**The leg and the yoke should probably not be printed.** See section 3.

### The connector, and the valance

**Connector faces DOWN.** That is right at the bottom of the bumper for the same reason it is right
anywhere: water leaves downward, and the module is designed to sit that way in a front bumper. Four
things go with it at this height, where spray is worse:

1. **It stays BEHIND the valance skin.** The skin is the stone shield. The connector never
   protrudes past it.
2. **Drip loop.** The harness leaves the connector, drops below it, then rises. Water runs down the
   wire and falls off the bottom of the loop instead of tracking into the seal. This is free and it
   is the single most effective thing on this list.
3. **Center it laterally.** The calmest air and the least spray on the back of a car is the
   centerline; the plume comes off the rear tires. The plan already prefers center for lane-math
   reasons; spray is a second reason for the same choice.
4. **Find the vent, if it has one** (M5.4). A vent membrane pointed straight down into a
   pressure-washed spray zone is how a "sealed" module fills with water. If there is one, orient it
   sideways.

**The valance is a radome, not a mounting point.** The cradle hangs off the beam and never touches
the skin. Four foam-capped standoff pads at the corners hold the skin off the antenna face so it
cannot buzz against it, and they are deliberately soft: anything rigid tying the aim to a panel
that flexes in a crosswind is a slow-motion misalignment.

Air gap from the face to the inside of the skin is a parameter (`face_standoff`, default 25 mm),
and **the right value is a bench question**. Two schools exist: keep the gap large enough that the
reflection diffuses, or tune it to a multiple of a half wavelength in air (1.96 mm at 76.5 GHz).
**UNVERIFIED which one this module wants.** The bench test can settle it for the price of a scrap
of the actual valance, which the plan already recommends holding in front of the radar during the
bench session.

### Drainage

The cradle has front-to-back drain slots in its floor. A cradle that holds water is a cradle that
freezes, and this car spends its winters in Utah. Nothing in the design forms a cup.

---

## 3. The fallback: external, below the bumper

Confidence: **medium**. This exists for one case only -- M1 comes back under 80 mm.

Same cradle, same shim, same yoke. A longer leg drops from the beam or the tow eye to put the face
below the valance in free air, at roughly licence-plate height or a little below.

What changes:

- **RF path is better than behind the valance, not worse.** Nothing between the antenna and the
  world at all. If it ever comes to a coin flip, this is the option with no radome question.
- **Elevation is easier to verify** because the face is visible and reachable without pulling
  anything.
- **Everything else is worse.** Direct stone impact, direct spray, direct sun, direct car wash,
  and it is visible from behind, which the plan is right to call ugly.
- **It gets a hood.** A printed lip above the face projecting rearward, with a drip groove, so
  spray sheds off the lip rather than tracking back along the underside onto the antenna. The hood
  sits above the aperture and never crosses it.
- **Material moves to ASA or PC, not optional.** See below.
- **Height gets easier, not harder.** Hanging it below the bumper does not have to mean hanging it
  low; the leg length is a parameter and the 300 mm floor is the constraint.

It is not the plan and it should not be built unless M1 forces it.

---

## 4. Material and process

Confidence: **high** on the failure modes, **medium** on which one bites first here, because the
cavity temperature has never been measured.

### The failure mode that matters is not the one people design for

A bracket that cracks tells you it failed. **A bracket that creeps does not.** It sags a fraction of
a degree over a hot afternoon, the elevation moves, and there is no signal anywhere in the system
that reports it, because `ESR.dbc` has no elevation readback. The radar goes on producing a target
list that looks entirely reasonable and is aimed at the tarmac at 20 m.

**That is why creep resistance outranks toughness here, and it is why PLA is not a candidate at
any point in the process.**

### The materials

| | Glass transition | Verdict here |
|---|---|---|
| **PLA** | 55-60 C | **No. Not even for a fit check left in the car.** A dark plastic part in a sealed cavity in a Utah August is plausibly above its Tg, and PLA under sustained bolt preload creeps well below Tg. It will not crack; it will droop, and nothing will tell you. |
| **PETG** | ~80 C | **Fit-check article only, then throw it away.** Cheap, prints on anything, tough enough. But it creeps under sustained load somewhere above 65 C and gets notch-sensitive in the cold. Fine for confirming the radar drops in and the bolts line up. Not a service part on a joint that holds an angle. |
| **ASA** | 100-105 C | **The service material for printed parts.** UV-stable by design, tough at winter temperatures, well clear of any cavity temperature this will see. Needs an enclosure and it warps; that is the cost. |
| **PC / PC-CF** | ~145 C | **Better, if the printer can do 290 C in an enclosure.** Highest stiffness and the best creep resistance on this list, which is exactly the property that matters. PC yellows and crazes in UV, which is irrelevant behind the valance and a real objection for the external fallback unless it is painted. |
| **PA12-CF / PA6-CF** | -- | **Good, and the best fatigue life here.** Filled, not unfilled: **unfilled nylon absorbs moisture and moves dimensionally with humidity**, and a part whose only job is holding an angle cannot have a dimension that tracks the weather. PA12 absorbs far less than PA6. |

**Recommendation, given there is no schedule pressure and the instruction is to prefer the version
that is right:**

- **Cradle, strap, shims, hood: printed.** ASA, or PC-CF if the printer allows. PETG for the first
  fit-check article only.
- **Yoke and leg: cut and bent in steel or aluminium at the shop that did the front bracket.**
  These are the two parts carrying the cantilever moment into the car, they are flat plate and one
  bend, and the plan already records that a shop cut a custom adapter bracket for the front install
  and that it survives a bumper's vibration and weather. Printing them is the compromise, not the
  goal. The SCAD geometry is deliberately flat-plate-and-bend so it can be handed over; OpenSCAD's
  `projection()` exports a DXF section.
- **Shims stay printed regardless.** No shop will hold 0.25 degree steps for the price of a
  filament change, and the printed resolution is already better than the measurement.

### Print orientation, and why each one

The vibration failure is interlayer separation. Layer planes must not be perpendicular to a tensile
or peeling load.

| Part | Orientation | Why |
|---|---|---|
| **Cradle** | standing on its bottom shelf, as drawn | The load is the module's mass cantilevered rearward, a moment about X carried by the side walls. In this orientation that is an in-plane load on the walls. Printed lying on its back plate it is a pure interlayer tensile load and it delaminates on a washboard road. |
| **Strap** | flat, **level pad DOWN on the glass** | The pad is a datum. A bed-side surface is the flattest thing a printer makes. Do not print it pad-up and rely on ironing. |
| **Shim** | flat, thin face down | Layers perpendicular to the bolt axis, so a clamped joint loads them in compression. The one part where that orientation is correct, and the engraving lands on the crisp first layer. |
| **Yoke, leg** (if printed) | profile in the bed plane, extruded along X | The corner of the L is where the whole moment goes. Any other orientation makes that corner an interlayer joint in tension. |

Settings: **5 perimeters minimum, 40 percent gyroid.** Perimeters carry this, not infill. Do not
solve a strength problem with 90 percent infill and three walls; it is heavier and weaker.

### Fasteners and the rest of the environment

- **A2 stainless minimum, A4 (316) preferred.** Utah runs road salt and magnesium chloride, and
  this is the lowest, wettest, saltiest part of the car. Zinc-plated hardware will not last.
- **Nyloc nuts or wedge-lock washers on every joint.** A fastener backing out on the elevation
  joint is an aim change with no symptom.
- **Clearance holes and washers everywhere, never press fits.** A steel bolt and an ASA part have
  very different expansion coefficients, and a printed part clamped hard against a cold steel bolt
  in January is not clamped at all in July.
- **Closed-cell EPDM foam tape** between the module and the cradle, and on the standoff pad faces.
  Not open-cell; open cell holds water.
- **Measure the cavity temperature before trusting any of this.** Tape a cheap max-reading
  thermometer or a $15 logger into the intended spot for a week in August. The whole material
  argument above is built on a temperature nobody has measured, and it is the easiest UNVERIFIED
  item on this page to close.

---

## 5. The arithmetic

Confidence: **high**. This is trigonometry and it is checked.

### The hard requirements, sanity-checked

| Claim | Check |
|---|---|
| 173.7 x 90.2 x 49.2 mm | 6.84 x 3.55 x 1.94 in. Consistent. |
| Mount height 30-86 cm | 11.8-33.9 in. Consistent. |
| +/-8 deg azimuth trim | At 50 m that is +/-7.0 m of lateral error, about two lane widths. So the CAN range is generous as an ANGLE and useless as an aim spec. The plan's own note -- 3 deg is 2.6 m at 50 m -- checks: tan(3 deg) x 50 = 2.62 m. The two statements are consistent, and the second one is the one that governs. |
| Depth is the binding constraint | Yes, and worse than the plan's framing. Floor 80 mm against a plan estimate of 40-70 mm. |

### Elevation, and why the low mount makes it the critical axis

A boresight pitched down by theta at mount height h meets the road at `h / tan(theta)`. That
distance is where the sensor stops looking at traffic and starts looking at tarmac.

| pitch down | at h = 380 mm (the low mount) | at h = 600 mm (the painted zone) |
|---|---|---|
| 0.5 deg | 44 m | 69 m |
| 1.0 deg | 22 m | 34 m |
| 2.0 deg | 11 m | 17 m |
| 3.0 deg | 7 m | 11 m |

**The low mount costs about a third of the road intercept at every angle.** At 1 degree down, a
380 mm mount is looking at pavement from 22 m back -- inside the range where the rear-approach
logic does its work. That is the price of dodging the metallic paint, it is a fair price, and it
converts "elevation must be mechanically correct" into a number:

> **Target +/-0.5 degrees. Accept +/-1.0. Anything past 2 degrees is not a mount, it is a
> pavement sensor.**

Pointing up is not free either. At 2 degrees up, the boresight is 3.5 m above the mount line at
100 m, over the roof of everything. You lose the far half of the range instead of the near half.

### The squat term, which is why the target is not zero

Loading the rear drops the ride height and pitches the body nose-up, which aims a **rear**-facing
radar **down**. With a 2850 mm wheelbase (`FORD_FUSION_MK5` CarSpecs, quoted in the plan's section 4):

| rear squat | body pitch | boresight aimed down by |
|---|---|---|
| 15 mm | 0.30 deg | 0.30 deg |
| 25 mm | 0.50 deg | 0.50 deg |
| 40 mm | 0.80 deg | 0.80 deg |

So a mount aimed dead level empty is aimed half a degree into the ground with the trunk loaded, and
loaded is exactly the road trip case.

**Set the unloaded aim slightly UP, by half the measured squat bias.** With 25 mm of squat that is
0.25 degrees up empty and 0.25 degrees down loaded, which splits the error instead of paying all of
it when it matters. M3 is what makes that number real.

### Shim resolution

Over the 56 mm interface bolt span, one degree is `56 x tan(1 deg)` = **0.98 mm** of thickness
difference. A 0.2 mm layer resolves 0.20 degrees; a printer holding +/-0.15 mm holds
+/-0.15 degrees. Comfortably finer than the +/-0.5 degree target, and finer than the phone
clinometer that measures the car.

### Depth budget

| | as drawn | absolute floor |
|---|---|---|
| antenna face standoff, clearing the ribs | 25 | 14 |
| cradle back plate | 8 | 4 (steel) |
| radar body | 49.2 | 49.2 |
| shim | 6 | 6 |
| yoke | 10 | 5 (steel) |
| **total** | **98.2 mm (3.9 in)** | **78.2 mm (3.1 in)** |

Plus the connector stickout if and only if it exits the rear face. Which is why M5.3 exists.

---

## 6. The parametric file

`tools/rear_radar_mount.scad`.

Every unknown is a named parameter at the top with a comment saying what to measure to fill it in.
The parameters are grouped: the radar, the car, the elevation joint, the azimuth joint, structure.

Parts are selected with `part = "..."`: `assembly`, `cradle`, `strap`, `shim`, `shim_set`, `yoke`,
`leg_beam`, `leg_drop`, `hood`.

It echoes a check on the numbers you have entered and warns when they do not close -- when the
stack is deeper than the clear depth you measured, when the cradle is wider than the clear width,
when the face height falls outside the Delphi window, when it falls below 300 mm once the squat is
subtracted, and when the standoff is inside the rib depth.

Alignment features are printed in place: the strap's level pad, two V-notched sight posts on the
boresight centerline, centerline notches top and bottom, the yoke's engraved 2-degree protractor,
and the leg's pointer. Each shim is engraved with its own angle and with UP.

**It has been parsed and hand-checked, but NOT rendered.** OpenSCAD is not installed on this
machine and I did not install it. Bracket balance, module references and identifier definitions all
check out, and the geometry was reviewed by hand -- several real defects were found and fixed that
way, including a sight-post span that overhung the plate and a protractor engraved where nothing
was. **Open it and render it before printing anything.** Treat any remaining geometry error as
likely rather than surprising.

---

## 7. What this cannot answer

Everything below needs the car, the module in hand, or a bench. None of it can be reasoned to.

**Needs the car:**

- Whether the radar fits behind the lower valance at all. M1, and it can end this layout.
- Whether the lower valance is genuinely unpainted on this car. Read off a photograph so far.
- Valance thickness, rib depth, rib spacing.
- Whether the rear bumper reinforcement beam offers any usable fastener. The plan says there are no
  factory radar mounting points back there; what there IS instead has never been looked at.
- Whether the beam is a closed section.
- Where the tow eye socket actually sits, and how far off-center.
- The real height window between the moulding line and the bottom of the valance.
- Rear squat, empty to loaded.
- The pitch of the beam's rear face relative to level.
- Cavity temperature in August.

**Needs the module in hand:**

- The real envelope. The datasheet's "including mounting features" is doing unknown work.
- The mounting features themselves: type, pattern, thread.
- Which face the connector exits, and how deep the mated backshell is. **This alone can kill the
  layout even if M1 passes.**
- Whether there is a vent membrane and where.
- Antenna face squareness to the mounting datum.

**Needs a bench:**

- **The electrical boresight offset.** The level pad proves the cradle is level. Whether the beam
  agrees with the case is a different question and only a bench answers it.
- **The right antenna-to-skin air gap.** Whether it wants a large diffusing gap or a tuned
  half-wavelength multiple is unresolved, and the answer changes `face_standoff`.
- **Whether the valance attenuates at all.** Nearly free to add to the bench session already
  planned: hold a scrap of the actual valance between the radar and a target and compare.
- **Whether 380 mm is high enough in practice.** Point the module down an empty flat lot from a
  stand at 250, 300, 400 and 500 mm and compare where the clutter floor sits. That answers the
  height question for this specific module rather than trusting a manual's window.
- **Whether the ESR radiates at all** without Ford's gateway frames. That is the plan's stated
  number-one risk and it sits upstream of every word in this document. If it fails, none of this
  gets built.

**And one thing nothing answers before the road:** whether a rear-facing radar's aim holds after a
few thousand miles. The bracket is the weak point; the plan says so and it is right. Re-check the
level pad after the first long drive, and again after the first winter. That is a five-minute check
with a gauge and it is the only way a slow creep gets caught before it becomes a target list nobody
should trust.
