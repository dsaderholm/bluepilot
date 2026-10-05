# passing-assist-phase1: findings after 2026-10-02

New dated findings for this branch go here; the pre-2026-10-02 notes are in `HISTORY.md`.

## 2026-10-05: SLOW PASS fired in a middle lane. The overtake gate was still on the camera proxy.
He saw SLOW PASS while the lane strip showed a middle lane. The hog gate moved from
`not left_geometry_ok` to `lane_anchor.in_leftmost_lane()` after the 2026-08-19 report, but the
`self.overtake.update(...)` call kept the old proxy for `in_leftmost`. Both now ask the anchor, the
same source as the lane strip; unknown means not leftmost, so no warning. Guarded by
`test_the_slow_pass_gate_asks_the_anchor_too` (mutation-tested).

## 2026-10-05: review of passing_assist / lane_anchor / overtake_progress, four fixes
1. `in_leftmost_lane()`: a KNOWN index with lanes to its left now vetoes the bare line witness.
   Before, a carried index the bound could not check (2 lanes, or no far-right prob) read leftmost
   on one frame of a missing far-left line.
2. A stalk signal moves the anchor only if `modelV2.meta.laneChangeState` reached starting/finishing
   during it. A cancelled tap now drops the index instead of shifting it. Exit detection still uses
   the stalk's direction (`_stand_down(..., anchor_follows=)`).
3. `_track_driver_change` subtracts `_own_blinker()` like `_driver_override`. Inert until `actuating`.
4. A left pass held by the left blind spot or rear no longer falls through to suggesting RIGHT
   (that showed a right suggestion against a left blinker). Reverses
   `test_falls_through_to_right_when_left_occupied`; right is still offered when there is no left lane.
Only these three files were reviewed; passing_maneuver, adjacent_lane, rear and the UI were not.
