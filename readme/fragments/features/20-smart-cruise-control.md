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

