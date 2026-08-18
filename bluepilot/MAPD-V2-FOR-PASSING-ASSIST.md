# mapd v2, for passing assist: what is measured, what is yours, what is not

**From the ICBM session, 2026-08-18.** mapd v2 lives on `icbm-manual-override-and-tuning` and reaches
you by rebase — it touches `cereal/`, `params_keys.h`, `process_config.py` and `sunnypilot/mapd/`,
which the base owns. Nothing here needs porting; it needs consuming.

Everything below is measured on **route 00000383**, his own drive, 7,019 `mapdOut` frames above
5 mph. Read the caveat at the end before generalizing any of it.

---

## The fields you asked for are live, and here is how much to trust each

| Field | Populated | What the drive shows |
|---|---|---|
| `oneWay` | 100% | **The strong one.** motorway 100% True across 31 distinct ways, motorwayLink 100% across 10, residential and tertiary 0% across 3 and 2. secondary is 49.8% — a divided arterial tagged as two one-way ways, which is correct rather than noise. |
| `highwayClass` | 98.6% | motorway 2305, secondary 1794, **motorwayLink 1398**, residential 1116, tertiary 308, unknown 98. The freeway/ramp split you wanted is real and well populated. |
| `lanes` | 91.5% | Plausible by class: motorway 3–5, **motorwayLink 1–2**, residential 2. The 9% unpopulated is almost all residential. |
| `estimatedRoadWidth` | 100% | median 11.10 m, p10 7.40, p90 18.50. About 3 lanes at 3.7 m, which is consistent with `lanes`. |
| `distanceFromWayCenter` | 100% | median 2.77 m, **p90 11.58 m, max 30.74 m**. The tail is longer than any real road is wide — see below. |
| `waySelectionType` | 100% | current 7186, predicted 657, **fail 313**, extended 308, possible 276. `fail` is the state v1 could not express. |

**`distanceFromWayCenter` is the one to be careful with.** A car in its lane should sit within half a
road width of the centerline, and the p90 does not. Either the way match wanders or the field means
something other than the obvious. **Do not build a lane-position gate on it without measuring it
against something else first** — the camera's own lane position is right there and disagrees or
agrees measurably.

**`lanes` looked like it had a default and does not.** Every tertiary frame reads 5, which reads as a
sentinel until you count the ways: it is 2 specific roads, one of them for 296 of the 308 frames.
Small sample, not a fabrication. Checked because assuming would have been cheap and wrong.

---

## Three things settled today that change what you would otherwise assume

**1. `targetVelocity` is `sqrt(2.2 / curvature)` and nothing else.** Verified across 6,725 path
points — every one satisfies `targetVelocity^2 * |curvature| = 2.200`, which is
`/personalities/standard/map_curve_target_lat_a` exactly. So **the path's value is the CURVATURE
profile; the velocities carry no information the curvature does not.** `MAPD-V2-PLAN.md` sold the
path on its velocities and that was backwards. If you want a corner speed for a gate, derive it from
curvature with your own lateral-acceleration number rather than inheriting mapd's 2.2.

**2. mapd never sees openpilot's personality.** `subscriber/shadow_selfdrive_state` is False, so it
always uses `standard`. Anything you read is that personality's numbers.

**3. The `MapdV2` state-2 gate is met**, on his drive: "only v1 had a limit" is 1.6%, and all eight
of those frames are ones where v2's way match returned `fail`. v2 was not wrong there, it said it did
not know. He has not driven at state 2 yet — his car is in the shop — so v2 has never actually fed
Speed Limit Assist or SCC-Map on the road.

---

## The rules that bind anything you build on this

**MAY REFUSE, MUST NEVER OPEN.** Already in CLAUDE.md and it is the whole design:

> `lanes = 3` alone cannot authorize a lane change. A wrong tile then puts the car somewhere real,
> and losing map coverage takes the feature with it.

So `highwayClass == motorwayLink` may cancel a pass. `lanes >= 2` may not offer one. Hold that and
"no map costs COVERAGE, never SAFETY" stays true by construction rather than by intention.

**`mapdOut.suggestedSpeed` is banned**, and `test_mapd_schema.py` fails by file and line if any
decision-making file reads it. It is mapd's own arbitration and cannot know this car is driven by
button presses at 3.3 mph/s. Take the ingredients instead.

**The `Mapd*` structs in `custom.capnp` are THEIRS.** The binary is compiled against its own copy and
capnp reads by position, so an inserted field makes `speedLimit` decode out of other bytes with no
error anywhere. Put your fields in your own structs — and the tiebreaker for a collision is **wire
history**, not base branch: the field that has never been written to a log is the one that moves.

---

## What is NOT yours

**Do not remove mapd v1.** It is one dependency away from removable and that dependency is
`SmartCruiseControlMap` falling back to `MapTargetVelocities` when the v2 path is None — this
branch's code, gated on a state-2 drive that has not happened. `mapd_ready()` also does not check
`MapdV2` at all, so v1 currently runs in every state including 2, at ~22% of a core and 204 MB. That
is a known item here, not a discovery to make there.

**The curvature descent is this branch's next build** — planning a corner entry against the 3.3 mph/s
the buttons deliver, instead of reacting to a step. Do not start it from your side; it changes
SCC-Map's judgement and wants the state-2 drive as its baseline.

---

## The caveat on every number above

**One drive, and a small road sample**: 31 distinct motorway ways, 13 secondary, 10 motorwayLink,
3 residential, 2 tertiary. Enough to say a field is populated and behaves sensibly; **not** enough to
set a threshold on. Anything with a number in it wants his roads under it first — which is the same
rule the four SCC-Map defenses were each built under.

## THE LEFT ROAD EDGE IS NEVER TRUSTED ON A FREEWAY. MEASURED, AND IT KILLS AN ASSUMPTION.

Route 00000383, above 34 mph, `tools/bp_left_edge_profile.py`, 2026-08-17:

    motorway, 4 lanes   1257 frames    left edge trusted   0.0%
    motorway, 5 lanes   1024 frames                        0.0%
    motorway, 3 lanes    779 frames                        0.0%
    secondary, 5 lanes   576 frames                        0.0%
    motorwayLink, 1 lane 463 frames                       14.7%   at p50 3.0 m

**Zero, across 3,060 frames of multi-lane motorway.** The only place the left edge is ever trusted
is a single-lane ramp, where it genuinely is beside the car.

**WHAT THIS KILLS.** It was stated earlier the same day that a physically separated HOV lane -- a
concrete wall or pylon barrier -- would produce a road edge and that "the existing left-edge logic
already refuses" a target beyond it. **The premise is false.** `_on_our_carriageway` never has a
trusted left edge to work from at freeway speed, so that path cannot refuse anything there. It is
also why `UNTRUSTED_EDGE_ONCOMING_M` exists: untrusted is the NORMAL state, not an edge case.

**SO NO CALIFORNIA HOV BOUNDARY IS HANDLED BY PERCEPTION TODAY**, in any of its three forms. The
earlier three-case table implied two of them were covered. None are:

    concrete or pylon wall   no trusted edge at freeway speed. NOT refused.
    double white line        paint, and no edge either way. NOT refused.
    candlestick delineators  unknown, and now the ONLY candidate for producing one.

That inverts what the California drive is for. It is not confirming a fallback already works; it is
testing whether candlesticks are the one boundary type that can produce a trusted left edge at all.
**If they do, that is a NEW capability. If they do not, perception offers nothing and only
`hov:lanes` can help -- which is 0% in California.**

**THE MEASUREMENT IS ALREADY INSTRUMENTED, which is why no capnp field was added.** `modelV2`,
`roadEdgeStds` and `mapdOut` are all in every route already, so the trip records what is needed
whether or not anyone remembers to enable something. Run the tool on a California route and compare
against the table above. Utah is the control: the owner reports Utah uses a double white line and no
posts, so every existing route is the paint case.

**Read the std VARIABILITY, not just the trusted fraction.** The failure that matters is not a
present or absent edge, both of which are decidable. It is FLICKER -- an edge appearing at each post
and vanishing between them would let a pass open in the gaps. Route 00000383's 4-lane motorway
already shows stdev-of-std at 2.83, so churn is the baseline behavior and a California run has to
beat that rather than merely show movement.
