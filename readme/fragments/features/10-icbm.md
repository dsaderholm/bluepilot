### Intelligent Cruise Button Management

Stock Ford ACC will not accept a longitudinal command, so ICBM — sunnypilot's actuator adapter —
translates openpilot's desired speed into cruise-button presses. **The set speed is the only lever
this car has**, and most of the work here is making that lever behave.

That constraint is worth understanding before reading anything else: the set speed falls at roughly
**3.3 mph per second**, which is the car's own repeat rate for a held cruise button. It is not a
parameter. Every feature that slows the car has to fit inside that budget, and a few requests that
sound reasonable are simply impossible because of it.

- **A button contract settled on the road.** `RES +` creates or raises a HOLD — the driver's own set
  speed — and `SET −` lowers it or, with cruise off, hands the speed back to Speed Limit Assist.
  Every other feature keeps working against a hold: curves still slow the car, hazards still fire,
  and the speed returns to the driver's number afterwards rather than to the posted limit.
- **Holds pinned to a location.** Tap the HOLD badge and that hold returns whenever you drive through
  the same place. A hold you set by hand always outranks a pinned one.
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
- **Rate limiters that only meter what has no deadline.** Ford coasts for small set-speed steps and
  brakes for large ones, and coasting into a lower speed limit is nicer than braking into it. But a
  curve or a mapped corner is a fixed place in the road, so those go straight to target — metering
  them spends road that was already budgeted.

