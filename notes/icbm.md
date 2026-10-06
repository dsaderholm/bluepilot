# icbm-manual-override-and-tuning: findings after 2026-10-02

New dated findings for this branch go here. Standing rules every session needs go in the master
`Sandbox/CLAUDE.md` or `.claude/rules/`; the pre-2026-10-02 notes are in `HISTORY.md`.


## 2026-10-03: THE SLOW LEFT-TURN UNWIND IS THE HUMAN-TURN OVERRIDE, NOT THE BLEND

*"It will make left turns but it still doesn't unwind fast enough, but it also isn't that bad."*
Drives 000004ce, 000004cf (2026-10-02) and 000004d0 (2026-10-03), qlogs in
`drivelogs/2026-10-03_left_unwind`. Five lefts with lateral on: 4ce t+1178 and t+1191, 4cf t+726 and
t+765, 4d0 t+556 (87 deg of heading, 9 -> 25 mph).

**On every one, `humanTurnLateralPaused` latched near the apex and stayed on through the unwind**
(1.2-3.6 s; `pathAngleFinal` 0, `blendWeight` back to 0.500). `steeringPressed` was on 95-100% of
those frames, with column torque +1 to +3 Nm in the turn's own direction, and the wheel past 45 deg
for 1.5 s, which is all `HumanTurnDetector` needs. From then on the wire is mode 0, so the rack gets
no command and the wheel comes back on caster (and whatever is on the rim), not on the model's plan.
`kappaCmd` during the override is the measured curvature (the truthful shadow), so it can't be read
as openpilot's request; compare the wheel with `geo(controlsState.desiredCurvature)` instead.

    4d0 t+556.2  wheel +190 -> +57 in 1.8 s; the model asked +120 -> +22; up to 73 deg MORE wheel than asked
    4ce t+1178.8 / 1191.1   up to 42 / 32 deg past the ask
    4cf t+765.7  up to 57 deg past the ask

Mean excess over each override is 9-37 deg, peaks 32-73 deg for about a second: real, and modest,
which fits "not that bad". Rights do the same (4cf t+146.7, t+819.1; 4d0 t+621.9: peaks 49-64 deg).
The override is 1.8% of lateral-on frames over the three drives.

**The question that decides the fix: are his hands on the rim during these turns?** Steady torque
WITH the turn while the wheel unwinds is something resisting the caster return. If it is his grip,
the detector is reading him correctly. If his hands are off, it is the rack false-press seen on
4c2 (torque is not a hands detector), and the fix is to stop the override from latching while the
torque and the wheel both agree with openpilot's own command: a takeover pushes the wheel past the
command or against it. That touches the driver-override path and ships on its own drive.

### BUILT: A PRESS THAT RIDES ALONG WITH THE TURN NO LONGER HANDS THE CAR OVER

He can't say where his hands were ("they may be, if I am about to hit a car"), so the timing decided
it: on every override the press began at turn-in, with torque WITH the turn, while openpilot was
keeping up; the latch formed near the apex and the slow unwind came after it. His rescue, when it
comes, is torque AGAINST the turn.

`human_turn.press_follows_command`: wheel and torque both the command's way, and the wheel not past
`geo(cmd) * 1.2 + 15 deg`. Such a press resets the hold timer; anything else latches as before
(1.5 s, or 3.0 s if the press began past 45 deg). Once latched it ignores agreement, because the
angle path then publishes the measured curvature as its command. The command compared is
`follow_kappa_cmd`, NOT `bp_kappa_cmd`, which is the wheel's own curvature during a press (that
mistake would have disabled the override; the first replay made it too). Curvature mode unchanged.

Replay on 4ce/4cf/4d0 with the true command (`pathAngleFinal / (v * curvatureFactor)`): 23 overrides
under the old rule, 1 under the new (4d0 t+640, where the wheel ran past the command). Ships on its
own drive; on that drive, score the unwind on lefts against `geo(desiredCurvature)`.

## 2026-10-05: "IT STRUGGLES TO GET BACK UP TO MAX" -- NOT FOUND AS A STALL ON 4dc..4e0

Drives 000004dc..000004e0 (2026-10-05, about 10 min of cruise in all), qlogs in
`drivelogs/2026-10-05_icbm_top_speed`. His words: when ICBM goes back up to max it stays under and he
has to nudge it up.

- **Every climb back finished on its own.** Slowest: 4dc t+269 -> 276, dash 26 -> 45 in 6.5 s, the
  last 2 mph by taps (`TAP_BAND` 2, one tap per `TAP_CYCLE_FRAMES` 0.6 s; 43 -> 45 took ~3.5 s).
  4dd t+228 -> 233, 31 -> 50 in 5.5 s in rise-limiter steps (36, 41, 46, 50).
- **openpilot's max reads ~2% above the dash and that is not a shortfall.** `vCruiseCluster` 82.1 kph
  = 51.01 mph with the dash at 50; 78.86 kph = 49.0 with the dash at 48. Without SLA the max is derived
  from Ford's own set speed. ICBM holding dash 50 under "max 51" IS the max.
- **What pulled it down was the model-stop path** (`unconfirmedLead.trigger == modelStop`): 6 firings
  at 42-49 mph, 0.5-3.5 s each, target 20-37 mph, none followed by a stop within 25 s; gas pressed
  within 3 s after 5 of the 6. The one firing on 4ce/4cf/4d0 ended in a real stop. Code and params
  (`IcbmModelStopEnabled` 1, `IcbmModelStopMinDecel` 10) unchanged since August.
- His manual presses on these drives (4dd t+208, 4df t+473, 4e0 t+315) were not "stuck under max".
- 4ce t+788 (2026-10-02): dash 75 under max 80 for 5 min was HIS SET- press, i.e. his own hold.

He doesn't think it is the model stop. Open: which drive or place showed the stall he means.

### FOUND, ACROSS 33 DRIVES: THE "STUCK UNDER MAX" STRETCHES ARE HIS OWN HOLDS AGAINST THE +10 OFFSET

`stuck.py`-style scan of 4c3..4e0 (open road, cruise on, dash >= 1.5 mph under the plan after the 2%
cluster offset, no lead within 100 m, no SCC or unconfirmed lead): 43 stretches, almost all on 70 mph
freeways with the plan at 80 and ICBM holding 75 (or 70) and sending nothing, up to 87 s each, on
4c8, 4ce, 4d2, 4d3, 4d9, 4da.

Full-rate rlogs show how they start, both times checked: 4ce t+787.6 (held SET- 80 -> 75) and 4d2
t+326.8 (three SET- taps 78 -> 75), each within seconds of SLA raising the set speed to 80. That is
`SpeedLimitOffsetHigh` = 10 above `SpeedLimitOffsetHighThreshold` 65: a 70 limit plans 80, and he
takes it back to 75 by hand every time. Per the button contract that is a HOLD (`selfdriveStateSP`
shows vBaseline 77 -> 75, `baselineSource` press, `overrideState` manual), and it clears only when the
set speed returns exactly to SLA's target or the limit moves more than `IcbmBaselineResetDelta` 10 --
so ICBM, correctly by its rules, never takes it back up to 80.

The hold fields are on `selfdriveStateSP`; `carControlSP`'s copy has vBaseline/vTargetRaw 0. Read the
former. Also seen: the plan flips 75 <-> 80 for a second when the limit drops out (4ce t+783.3-784.2),
which ICBM chases (75 -> 78 -> 77 -> 80). Not his complaint; noted only.

The fix is his setting (he sets 75 by hand on 70 roads: an offset of +5 above 65 would plan 75), not
ICBM code. Settings are his; told him where.

### FOUND AND FIXED: ICBM'S ONE-FRAME TAPS LAND IN THE SCCM'S DEAD HALF AND STAY THERE

He said it is not the holds. With holds excluded (`selfdriveStateSP` vBaseline == 0), the shape is
in the TAP band: 25 waits of 2-43 s across 33 drives where ICBM, 1-2 mph under its target, kept
tapping RES+ and the dash did not move (4c4 t+364: 78 under 80 for 43 s at 75 mph; 4d5 t+185: 31
under 33 for 20 s, ~35 taps, then one landed and a held press jumped it to 40).

The frames DID go out (sendcan 131, bus 0 and 2, one per 0.6 s, SetInc bit set; no counter or
checksum in Steering_Data_FD1). What decides is WHEN, against the SCCM's own 10 Hz frame on bus 0:

    0-50 ms after the SCCM frame     4 of 4 taps registered
    50-100 ms after it               2 of 83 registered

(4 rlog segments: 4ce--13, 4d2--4, 4d2--5, 4d5--3; `tapphase.py` in the session scratchpad.) The tap
cycle, `TAP_CYCLE_FRAMES` 60 = 0.6 s, is an exact multiple of 100 ms, so a late phase never walks
off; it stays late until the clocks drift. And the rise limiter cannot step past it: it waits for
the cluster to reach its ceiling, which the dead taps never deliver.

Fix (`icbm.py` `_align_first_frame`, `carstate_ext.buttons_stock_fresh`): a new press's first frame
waits for the tick on which the SCCM's frame was parsed (`cp.vl_all` non-empty), at most
`TAP_ALIGN_MAX_FRAMES` 12; a held press keeps its 6-frame cadence after that. Unknown freshness reads
as fresh (old behaviour); any exception latches the aligner off for the drive. Smoke tests drive the
real CarController; each guard mutation-tested.

On the next drive: rerun the tap-band scan; the 2 s+ waits should be gone or near it.
