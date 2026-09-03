# Lateral drive ledger

Every "did that help?" question on this fork needs a BASELINE, and the baselines keep being lost --
either because the device rotates old routes away (`deleter.py` holds 5 GB free) or because the
numbers were computed once, quoted in prose, and never written down in a form the next comparison
could use. This file is the durable half of a drive: kilobytes that survive the logs.

**The rule this exists to serve, from CLAUDE.md: a statistic pooled across a settings change
describes a car that never existed.** So every row records the CONFIGURATION beside the numbers, and
the confounds are stated rather than left for someone to rediscover.

**`initData.params` CANNOT SEE A MID-ROUTE SETTINGS CHANGE, and this file previously claimed it
could.** It is written once per boot and replayed unchanged into every segment. Proven on route
`0000040e`: `FordHighSpeedFactor_ang` was written at 23:12:32, segment 14 closed at 23:12:33, the
route ran to 23:28 -- and all 31 segments report the pre-change 0.68. **The earlier claim that
`00000400` seg 19 was a detected mid-route change is WITHDRAWN**: segments 0-18 were simply never
pulled, so the tool was comparing against the previous ROUTE, not against an earlier segment.
Split a suspect route by segment number against the param mtime, or read `bp_lateral_gain.py`,
which is per-frame telemetry.

**The raw logs for every route below are kept**, off the device and outside any git repo, at
`Sandbox/drivelogs/2026-08-31_600mi_lateral_sweep/` (454 segments, 4.9 GB). They are a settings
sweep across matched roads, which is not reproducible once
`deleter.py` rotates them off the car. Needed only for a question this file does not already
answer.

## How each number was produced

| column | tool | conditions |
|---|---|---|
| settings | `tools/bp_settings_timeline.py` | `initData.params` -- a BOOT SNAPSHOT; see the warning below |
| settings, real | `bp_settings_timeline.py --telemetry` | `controllerStateBP` >= 70 mph; EXACT, and sees mid-route changes |
| weave | `tools/bp_lateral_weave.py` | 6 s windows, straight, hands off, sampled at the modelV2 rate |
| applied gain | `tools/bp_lateral_gain.py` | `controllerStateBP`, per frame; the only source that cannot lie |
| road profile | speed and `1/desiredCurvature` histograms | hands off, latActive, > 8 mph |
| delivery | `abs(curvature) / abs(desiredCurvature)` | STEADY STATE (desired stable within 12% for 0.5 s), hands off, >= 40 mph |
| revs/min | tracking-error sign flips per minute of genuine turning | `abs(desired) > 0.0007`, hands off |
| weave | lane-centre offset from `modelV2.laneLines[1..2].y[0]` | straight road (6 s median under 0.00025 1/m), hands off, >= 70 mph |

Delivery is the steady state the car SETTLES at, which measures GAIN. It cannot move on a change
that targets the transient, and quoting it as "no effect" for one of those is wrong.

## Configuration timeline

Read this before any row below. `low` / `high` / `damp` are `FordLowSpeedFactor_ang` /
`FordHighSpeedFactor_ang` / `FordHighSpeedDampening_ang`; `LC` is `lane_centering_strength_ang`.

| when | route / segment | low | high | damp | LC |
|---|---|---|---|---|---|
| 2026-08-29 | `000003ed` (baseline) | 1.197 | 1.143 | 0.81 | 0.55 |
| 2026-08-31 | `00000400` seg 19 | **0.981** | **0.50** | **0.89** | 0.55 |
| 2026-08-31 | `00000401` seg 0 | 0.981 | **0.51** | 0.89 | 0.55 |
| 2026-09-01 | `00000403` seg 0 | 0.981 | 0.51 | **0.85** | 0.55 |
| 2026-09-01 | `00000406` seg 0 | 0.981 | 0.51 | **0.78** | **0.15** |
| 2026-09-01 | set by hand, post-drive | 0.981 | **0.68** | 0.78 | 0.15 |
| 2026-09-01 | set by hand, + damper | 0.981 | 0.68 | 0.78 | 0.15 + **damp 0.3** |
| 2026-09-01 | `0000040e` seg 0-14, FROM THE WIRE | — | **0.714** | 0.780 | 0.15 + damp 0.3 |
| 2026-09-01 | `0000040e` seg 15-30, FROM THE WIRE | — | **0.794** | 0.780 | 0.15 + damp 0.3 |
| 2026-09-02 | `0000041c` -> `0000041d`, FROM THE WIRE | — | 0.784 -> **0.794** | 0.780 | 0.15 + damp 0.3 |
| 2026-09-03 | set by hand, ROAD REPORT | 1.017 | 0.794 | 0.780 | **0.35** + damp 0.3 |

**The last row is a road report, not a sweep: "it was hugging some edges."** That is the measured
cost of LC 0.15 -- the car sits 0.10-0.24 m off centre there against 0.04-0.05 m at 0.55 -- so his
report and the weave table agree. `custom_path_offset_ang` was checked first and is 0.0, so nothing
was biasing him sideways; it is purely strength.

**AND IT IS THE DAMPER TEST THAT COULD NOT BE RUN BEFORE.** At LC 0.15 the position loop is too weak
for a lead term to matter, which is part of why the weave numbers were indistinguishable noise. At
0.35 the loop has real authority, so the next drive separates the two outcomes:

    edges stop, straights stay calm   the damper works, and 0.5 is available
    edges stop, weave returns         the damper is not doing its job at 0.3

Both are worth more than the five drives before it. Score it with `bp_lateral_weave.py` and READ THE
RANGE COLUMN, not the median -- and only on a route with real sustained highway, because every drive
from `00000412` to `0000041d` had under 1.3 minutes of qualifying straight.

**THE LAST TWO ROWS COME FROM `--telemetry`, NOT FROM `initData`, AND THEY DISAGREE WITH IT.** The
boot snapshot says 0.68 for all 31 segments of `0000040e`; the wire says he was at 0.714 by segment
12 and 0.794 from segment 15. He changed it TWICE during that drive and the params snapshot saw
neither. Any row above sourced from `initData` alone is a boot value, not necessarily what drove.

**Neither of the last two rows has been driven.** The first is the flat point (`damp / 1.15` on a
CAN Ford). The second adds `lane_centering_damping_ang` 0.3, the lead term on the position loop.

**Two changes at once, and they are readable apart because they act in different places.** The high
factor is the ramp SLOPE and is bit-identical below `|kappa| = 0.0005`, which is 98-99% of
straight-road frames -- so it cannot touch the weave. The damper acts on the position loop, and the
weave metric qualifies on straight road only. Score them with different rows of this file:

    high 0.51 -> 0.68    the delivery-by-radius table, and whether tight curves hold
    damper 0 -> 0.3      the straight-road weave table (p2p offset, crossings/min)

`lane_centering_strength_ang` is deliberately LEFT at 0.15 for that drive. Raising it in the same
breath would make the weave numbers unattributable, and 0.5 is the reward if the damper works, not
part of the test.

## Roads driven — the reason nothing transferred

| route | segs | min hands-off | over 2000 m | 1000-2000 | 500-1000 | under 500 | speed |
|---|---|---|---|---|---|---|---|
| `00000400` | 102 | 58 | 90% | 6% | 3% | 1% | 74% at 75+ |
| `00000402` | 147 | 118 | 91% | 7% | 2% | 1% | 89% at 75+ |
| `00000405` | 176 | 167 | 93% | 6% | 1% | 0% | 80% at 75+ |
| `00000406` | 11 | 6 | **68%** | **18%** | **11%** | **3%** | 21% under 45 |

**The entire 600-mile drive was 90-93% curves over 2000 m radius.** The morning drive was 68%, with
14x the sub-1000 m exposure. That is why a setting tuned over 600 miles could not take curves the
next morning: at a 2000 m radius the high factor barely participates at all -- 0.51 and 0.87 produce
an identical 0.780 -- so the whole drive tuned dampening and never exercised the other knob.

## Delivered / commanded curvature, by radius

| block | config | >2000 m | 1400-2000 | 1000-1400 | 700-1000 | 450-700 | <450 | revs/min |
|---|---|---|---|---|---|---|---|---|
| `00000400` seg 19+ | high 0.50, damp 0.89 | 0.895 | 0.966 | 1.019 | 0.911 | 0.845 | 0.839 | 62.1 |
| `00000402` | high 0.51, damp 0.89 | 0.923 | 0.958 | 0.993 | 0.969 | 0.863 | 0.750 | 78.5 |
| `00000405` | high 0.51, damp 0.85 | 0.973 | 0.983 | 0.861 | 0.926 | 0.739 | 0.777 | 50.0 |
| `00000406` | high 0.51, damp 0.78, LC 0.15 | — | 0.757 | 0.859 | 0.848 | 0.725 | 0.720 | 58.9 |

**Within any single row, delivery falls as the curve tightens** -- that slope is the gain schedule,
not anything the driver did, because the settings are constant across a row. On `00000405` it runs
0.983 to 0.739 between the 1400-2000 m and 450-700 m bands.

**CONFOUNDED, and it cannot be un-confounded from this data:** `00000406` changed dampening AND lane
centering in the same edit, so its across-the-board drop cannot be attributed to either one.
`00000400` seg 0-18 held the pre-change settings but produced no qualifying steady-state frames, so
the one same-road A/B in the whole drive is empty.

## Straight-road weave, RE-MEASURED 2026-09-02 with one instrument

**The table below this one is the OLD instrument and is kept only as history.** It came from an
ad-hoc script that no longer exists, at an unrecorded sampling rate; a second ad-hoc script returned
3-4x its crossing rate on comparable road. `tools/bp_lateral_weave.py` now defines the measurement
(6 s windows, straight throughout, hands off, lane probs >= 0.30, sampled at the modelV2 rate).
**Only rows in THIS table may be compared with each other.**

| route | LC | damper | floor | straight min | off-centre | p2p | cross/min |
|---|---|---|---|---|---|---|---|
| `00000400` | 0.55 | 0.0 | 70 | 23.0 | 0.05 m | 0.17 m | 90.0 |
| `00000402` | 0.55 | 0.0 | 70 | 74.6 | 0.04 m | 0.18 m | 90.0 |
| `00000405` | 0.55 | 0.0 | 70 | 106.7 | 0.05 m | 0.17 m | 70.0 |
| `000003ed` | 0.55 | 0.0 | 70 | 1.5 | 0.05 m | 0.16 m | 70.0 |
| `00000406` | **0.15** | 0.0 | 45 | 0.8 | **0.24 m** | 0.16 m | **175.0** |
| `00000407` | 0.15 | **0.3** | 45 | 0.6 | **0.13 m** | 0.31 m | **50.0** |
| `0000040e` | 0.15 | **0.3** | 45 | 2.4 | **0.12 m** | 0.22 m | **65.0** |

**THAT DAMPER CLAIM IS WITHDRAWN, 2026-09-03. IT DID NOT REPLICATE.** Two drives later, on the
SAME settings and through the same tool, `0000041c` and `0000041d` read 100 and 180 crossings/min
against the 50-65 recorded below. And the per-window RANGE settles it: `[20-190]` on `0000040e`,
`[10-230]` on `0000041c`, `[30-290]` on `0000041d` -- three brackets that overlap almost entirely,
i.e. one distribution and no measurable effect. **The spread across identical settings was wider
than the effect, and it was reported as a direction before anyone checked that.** `bp_lateral_weave`
now prints the range and the window count for exactly this reason. What follows is kept as the
record of the error, not as a result:

**(WITHDRAWN) The damper moved both columns the right way, which a pure P-gain trade cannot do.** At LC 0.15 the
undamped car sits 0.24 m off centre and crosses 175 times a minute; with the lead term it sits
0.12-0.13 m off and crosses 50-65. Lower hunting AND better centring is the signature of a
derivative term working, not of a gain change.

**BUT THE EXPOSURE IS 0.6-2.4 MINUTES AND THE ROADS ARE NOT MATCHED.** Those three drives were
evening errands with almost no sustained straight highway -- `00000407` rejected 17,563 frames for
hands-on-or-not-engaged and 11,104 for speed. **This is a direction, not a result.** It needs one
highway drive before `lane_centering_strength_ang` goes back up.

**And the LC 0.55 rows say the damper has room to buy back:** at 0.55 the car centres to 0.04-0.05 m.
If the damper holds at higher strength, that is the target -- 0.05 m centring at 50 crossings rather
than the 70-90 it costs today.

## (OLD INSTRUMENT — history only) Straight-road weave

| route | LC | straight min | median off-centre | p2p swing | crossings/min |
|---|---|---|---|---|---|
| `00000400` | 0.55 | 33.0 | 0.05 m | 0.29 m | 17.5 |
| `00000402` | 0.55 | 87.9 | 0.04 m | 0.31 m | 20.0 |
| `00000405` | 0.55 | 118.0 | 0.06 m | **0.44 m** | 13.7 |
| `00000406` | **0.15** | 1.1 | **0.21 m** | 0.36 m | **6.5** |

29-44 cm peak-to-peak at strength 0.55. For scale, the steering dither chased for days elsewhere is
0.10-0.30 DEGREES and provably imperceptible; a third of a metre of lane position is not. At 0.15
the crossing rate halves and the car sits four times further off centre -- the P-gain trade.

`00000406` has 1.1 minutes of qualifying straight. Treat its size loosely; the direction is the
finding, not the magnitude.

## Where the high factor can and cannot act

`curvature_factor` interpolates from `abs(kappa_cmd) = 0.0005`, so BELOW that the high factor is
outside the interpolation entirely and has no effect whatsoever. On straight road at 70+ mph:

| route | median kappa on straights | p90 peak excursion | frames above 0.0005 |
|---|---|---|---|
| `00000400` | 0.000073 (13,654 m) | 0.000492 | 1.9% |
| `00000402` | 0.000057 (17,412 m) | 0.000303 | 1.0% |
| `00000405` | 0.000053 (18,864 m) | 0.000442 | 1.2% |

**98-99% of straight-road frames are below the line**, which is why the high factor cannot have been
causing the straight-road ping-pong and why moving it 0.51 -> 0.68 is bit-identical there.

## Adding a row

Pull the routes off-device (never decode on the car), then:

```bash
python tools/bp_settings_timeline.py <dir>          # ALWAYS first -- split on it, never through it
python tools/bp_lateral_gain.py <dir>               # applied gain + ramp shape, drives after 2026-09-01
python tools/bp_lateral_matched.py <dir>            # delivery at matched curvature
python tools/bp_lateral_curve_cycle.py <dir>        # the 6 s curve oscillation, the only metric that
                                                    # tracks what he reports on curves
```

`bp_lateral_rate.py` and `bp_lateral_episodes.py` use a 2 s window and are STRUCTURALLY BLIND to the
~4.7 s limit cycle. Do not rank two settings with them; that mistake produced a recommendation he
had already rejected from the seat.
