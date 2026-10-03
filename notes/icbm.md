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
