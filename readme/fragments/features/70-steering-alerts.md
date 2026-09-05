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
