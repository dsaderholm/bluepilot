# Lateral drive ledger

Every "did that help?" question on this fork needs a BASELINE, and the baselines keep being lost --
either because the device rotates old routes away (`deleter.py` holds 5 GB free) or because the
numbers were computed once, quoted in prose, and never written down in a form the next comparison
could use. This file is the durable half of a drive: kilobytes that survive the logs.

**The rule this exists to serve, from CLAUDE.md: a statistic pooled across a settings change
describes a car that never existed.** So every row records the CONFIGURATION beside the numbers, and
the confounds are stated rather than left for someone to rediscover.

**The raw logs for every route below are kept**, off the device and outside any git repo, at
`Sandbox/drivelogs/2026-08-31_600mi_lateral_sweep/` (454 segments, 4.9 GB). They are a settings
sweep across matched roads including one mid-route change, which is not reproducible once
`deleter.py` rotates them off the car. Needed only for a question this file does not already
answer.

## How each number was produced

| column | tool | conditions |
|---|---|---|
| settings | `tools/bp_settings_timeline.py` | `initData.params`, per SEGMENT, so mid-route changes are visible |
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

The last row is the flat point (`damp / 1.15` on a CAN Ford) and has NOT been driven yet.

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

## Straight-road weave — the lane-centering position loop

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
