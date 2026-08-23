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

