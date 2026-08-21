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

**It will not take the command below 25 mph**, and that floor is the difference between a feature
and a bricked drive. Every measured takeover that began under Ford's own 20 mph floor made the
forward camera assert cancel, and one of them latched it for the remaining nine minutes of the drive
— after which Ford ACC was gone until the car was restarted. Every takeover that began above the
floor was tolerated, including one that ran 35 seconds to a complete standstill. Once it has the
command, carrying the car below 25 and down to a stop is fine; it is the *taking* that the camera
objects to.

Handing back is measured in both shapes. When the corner ends the gap closes and Ford is still
engaged and simply carries on. When the corner ends in a stop, it holds the standstill rather than
releasing into a creep, and Ford's own cruise status follows into its stop-and-go state on the way
down — so resuming afterwards is an ordinary Ford resume, and the car pulls away under stock ACC.
The moment the radar acquires a lead it hands back on that frame, because Ford's stop-and-go is
years of calibration this has no business replacing.
