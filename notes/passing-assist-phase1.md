# passing-assist-phase1: findings after 2026-10-02

New dated findings for this branch go here; the pre-2026-10-02 notes are in `HISTORY.md`.

## 2026-10-05: SLOW PASS fired in a middle lane. The overtake gate was still on the camera proxy.
He saw SLOW PASS while the lane strip showed a middle lane. The hog gate moved from
`not left_geometry_ok` to `lane_anchor.in_leftmost_lane()` after the 2026-08-19 report, but the
`self.overtake.update(...)` call kept the old proxy for `in_leftmost`. Both now ask the anchor, the
same source as the lane strip; unknown means not leftmost, so no warning. Guarded by
`test_the_slow_pass_gate_asks_the_anchor_too` (mutation-tested).
