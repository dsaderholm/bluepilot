# The injected blinker and Ford's ACC gap: what is measured, what is assumed

**For passing assist, from the ICBM session, 2026-08-17.** You own the blinker actuator and most of
what follows is yours already. Skip to *"The correction"* — that is the only part that changes
anything, and it changes a line in CLAUDE.md that currently reads as settled.

---

## What is genuinely measured

Driver's stalk, 2026-08-14, routes 00000365 / 0000036b / 0000036f, 92,000 frames with a lead inside
80 m, engaged, above 18 mph:

|             | frames | ACC braking | mean gap |
|-------------|--------|-------------|----------|
| blinker ON  | 9,277  | 4.3%        | 2.00 s   |
| blinker off | 82,550 | 18.9%       | 2.14 s   |

Stock Ford ACC brakes about four times less often while the stalk is on. Correlational — he signals
when he intends to pass — but 4.4x is too large to be selection alone.

The owner's own verdict on the size of it, and it still stands: *"I don't think it closes the gap
enough."* 0.14 s at 65 mph is about 4 m.

## The correction

CLAUDE.md now contains, as a conclusion:

> passing assist actuates a blinker the camera does not see as a stalk, ACC therefore keeps its
> full following distance through the crossing

**That was never measured.** Fifty lines above it, the same section says the opposite about its own
status:

> **That is a measurement, not a guess to make.** Compare braking rate during passing-assist-commanded
> blinker against driver-stalk blinker, using the same query shape as above.

Nothing between those two passages reports it having been run. The paragraph that reads most like a
closing argument — *"AND THERE IS NO ROUTE OUT. THE OWNER HAS CLOSED THE ONLY ONE"* — is about the
**SCCM stalk-contact tap being ruled out as too invasive**, which is a decision about hardware, not
evidence about the camera.

So a conservative assumption has hardened into a stated fact, and the cost of that is real: it is
the premise for *"the only lever left is the follow gap itself."* I repeated it as settled earlier
today before rereading it. **The honest state is unknown.**

## Why it is unknown rather than unlikely

Nothing is being faked. `TurnLghtSwtch_D_Stat` is the same field the stalk sets, in the same message
(`BO_ 131 Steering_Data_FD1`). There is no more authentic frame to send.

What differs is **contention**, and `blinker_phase_lock.py` already states it exactly:

> Both claim the switch and the BCM obeys whichever landed last, so our command owns the switch for
> however long happens to remain before the gateway's next frame overwrites it.

The phase lock makes our frame land right after each gateway frame, so we hold the switch for most
of each 100 ms cycle. **The lamps flashing is proof the BCM accepts that. It is not proof anything
else does.** The DBC lists three receivers of the signal:

    SG_ TurnLghtSwtch_D_Stat : 5|2@0+ (1,0) [0|3] "SED"  IPMA_ADAS,PSCM

IPMA_ADAS sees the same alternating stream: our frame with the bit set, the gateway's with it clear,
ten times a second. Whether that reads as "signalling" depends entirely on how the camera debounces
— last-wins like the BCM, or N consecutive frames, in which case it sees a ~50% duty cycle and never
latches.

**That is the whole question.** Not whether the signal is right. Whether one receiver's debounce
tolerates alternation.

## The cheap measurement nobody has run

Not the road test — a log query, and it may already be answerable.

`ford/carstate.py:232` reads the blinker back off bus 0:

```python
ret.leftBlinker  = cp.vl["Steering_Data_FD1"]["TurnLghtSwtch_D_Stat"] == 1
ret.rightBlinker = cp.vl["Steering_Data_FD1"]["TurnLghtSwtch_D_Stat"] == 2
```

That parser updates on **every** received frame, ours and the gateway's alike. So `carState.leftBlinker`
during a commanded blink is a direct readout of what a receiver sampling that stream sees.

**Compare the duty cycle of `carState.leftBlinker` during a commanded blink against a stalk blink.**

- Near 100% during commanded → our frames dominate the stream, and the "camera cannot see it"
  hypothesis loses most of its mechanism.
- Near 50% → the alternation is real and visible, which is exactly the condition under which a
  debouncing receiver would reject it. It also then quantifies what a fix would have to achieve.

This needs no new drive if any 2026-08-06-onward log has the commanded blinker running. Those runs
were scored on lamp counts, so the data may exist and simply never have been asked this question.

PSCM is the second non-BCM receiver and offers the same kind of free evidence: any observable change
in its behaviour while the commanded blinker is asserted is evidence that a receiver which is *not*
the BCM consumes the injected frame. Weaker than the duty-cycle query, and worth noting only because
it costs nothing.

## What follows either way

Even if the camera does believe it, this is a **supplement, not a replacement**. 2.14 → 2.00 s is
4 m. The owner has already said it is not enough on its own, and that judgement predates all of this.

So the gap button remains the mechanism. What changes if the measurement comes back favourable is
that the crossing itself stops being a window where ACC holds full distance — which is the specific
cost this branch currently books as permanent.

---

## Two gap-controller contract changes from today's review

Both are on `icbm-manual-override-and-tuning` at `753e3235d` and are **not** on
`passing-assist-phase1` yet. Content check rather than hash, since a rebase changes every hash:

```bash
git show origin/passing-assist-phase1:opendbc_repo/opendbc/sunnypilot/car/ford/gap_control.py | grep -c _watching
```

**1. A driver gap press now latches `abandoned` with no lease open.** Previously that only happened
mid-lease. When you request the gap the car is already at, no lease opens — and both override paths
were gated on `self.active`, so the driver's press was invisible and the next frame's mismatch
opened a fresh lease that pressed their choice straight back out. Recovery is unchanged but matters
more now: **the request must drop to 0 to clear the latch.** A requester that keeps asserting after
a driver press stays refused indefinitely.

**2. A preempted press is abandoned and retried whole, not resumed.** Not calling `update()` while
ICBM held the button did not preserve the pulse — nothing holds the bit asserted during the stand
down, the gateway's own frames keep reaching the camera with the gap bits clear, and the resumed
remainder landed as a *second* press. Two toggle steps for one intended, or a delta of 2 that the
probe reads as "incDec works" and latches for the ignition cycle.

The cost is timing: the *"up to ~4.5 s"* in the module docstring is now a floor rather than a
ceiling whenever ICBM is hunting the set speed. `longitudinal_planner.py:172`'s "request EARLY"
comment is right and the margin it needs is larger than it was.
