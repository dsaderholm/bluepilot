### Route intent

**Nothing publishes this today, on any car, so it refuses nothing, moves nothing and changes
nothing.** What it is
meant to become is one fact reaching passing assist that no sensor on the car can supply: which way
the driver is actually going. The map knows where every ramp is and cannot know which one is his,
so the existing ramp test is reactive — it fires once the car is already on the ramp. A route is
the only thing that can say so beforehand.

What exists is the consumer and the wire, not a source. A `routeIntentBP` message carries four
values — a maneuver, a distance to it, whether that distance is real, and the moment the
instruction was last confirmed — and passing assist goes quiet when a committing maneuver is inside
roughly twenty seconds of driving. With no transport fitted the message never arrives, the gate
never fires, and passing assist behaves exactly as it does without any of this.

- **It refuses far more than it permits, and the asymmetry is enforced rather than intended.**
  Every candidate source is somebody else's software arriving over a link this car does not
  control, so the two failure directions are not symmetric: a source that wrongly says "exit ahead"
  costs a pass, and a source that wrongly says nothing leaves the feature as it is. Any instruction
  it cannot classify refuses a pass; only six specific instructions — exits, forks and lane
  commitments — may ask the car to move, and those six are checked to be a strict subset of the
  ones that refuse. A test parses the module and the code that calls it, and fails if that stops
  being true.
- **The one thing it asks for is the exit lane**, and it asks rather than clears. When the route
  leaves the road ahead and the car is not in the lane it leaves from, that becomes a reason to
  move over — but the lane is still cleared by the same checks that clear an overtake: painted
  geometry, blind spot, traffic closing from behind, oncoming, and the road not bending. Route
  intent supplies the motive and nothing else, and it is kept out of the code that authorises or
  reverses a commanded maneuver.
- **The transport is deliberately unnamed.** Three could supply it — the car's own CAN, which the
  instrument cluster must already be receiving to draw turn-by-turn; a navigation app on the
  driver's phone relayed to the device; or a router running on the device with a destination
  entered. Two of the three depend on other people, so the order they arrive in is not this fork's
  to choose, and nothing is sequenced behind any one of them.
- **Freshness is the message's own, not the socket's.** A phone bridge whose link has died keeps
  sending perfectly fresh messages carrying a minutes-old instruction, so the stamp is taken when
  the transport last *confirmed* the instruction rather than when it published. Anything older than
  three seconds is not evidence, and there is no extrapolation: a silent source means no claim, not
  a distance guessed forward.
- **A source that cannot measure the distance is not asked to invent one.** The maneuver glyph and
  the distance are separate reads for every candidate transport, and an instruction with no
  distance carries no bound — so it is treated as no claim rather than as a refusal, which would go
  quiet for a whole route instead of for an approach.
- **An instruction the source cannot classify still refuses.** That is the conservative direction
  and it is free, so a transport can ship before its classifier is complete rather than guessing at
  the nearest label.
- **A scripted route can be published on the bench**, labelled as scripted, so the whole chain is
  exercisable before any transport lands and cannot be mistaken for a real navigator in a drive log.
