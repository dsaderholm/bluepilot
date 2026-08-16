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

What the decision is built from, on a car with no blind-spot input routed and no lane count in the
map data:

- **Adjacent-lane occupancy from the front radar.** The forward radar's returns are read for traffic
  beside and ahead rather than only in-lane, which is the difference between "the next lane is empty"
  and "the next lane has not been checked." The rule it was built under: evidence that opens a
  maneuver must never be cheaper than evidence that refuses one.
- **Oncoming detection, which is how it tells a divided highway from an undivided one.** Traffic
  closing head-on in a candidate lane vetoes that side for 90 s. A single frame is not enough —
  sightings must corroborate across frames before they count, which cut one drive's oncoming returns
  from 511 to 166 with no veto lost.
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
  that a refusal is legible at the time rather than only afterwards in a log.

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
