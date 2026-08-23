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
