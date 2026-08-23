### Ford ACC passthrough, and the complete stop

**This is Ford-specific and validated on exactly one car.** It needs a Ford whose forward camera
keeps computing ACC with openpilot's relay open, which is measured on a 2020 Fusion with retrofitted
Edge ADAS parts and nowhere else. On any other car it may silently spend most of its time falling
back to openpilot's own longitudinal, which is the thing it exists to avoid.

Under openpilot longitudinal control the relay is open, so the camera's ACC command never reaches
the car — openpilot is expected to author it instead. But the camera still has all its inputs, and
it is still computing. The passthrough reads its `ACCDATA` and republishes it: **the car drives like
stock adaptive cruise because the commands are Ford's, and openpilot only carries them.**

Everything above it keeps working. Speed limits, curve slowing and the driver's held set speed all
still act through the cruise buttons, which change what the camera is aiming for — so the decision
of *what speed* stays openpilot's while the choice of coast, engine brake, precharge or friction
stays Ford's.

**The complete stop is what it is for.** Ford's set speed cannot go below 20 mph, and stock ACC
completes a stop only when its own radar has a lead — so a stop sign or red light on an empty road
is the one thing the car cannot do. openpilot sends the braking instead of Ford for a bounded
window, then hands straight back. It never takes over when a lead is close, because Ford's
stop-and-go already owns that case, and **it has never yet been observed bringing a car to a
standstill and holding it** — the trigger is measured against recorded drives, the braking is not.

**Whatever it sends is never softer than what Ford asked for.** Taking the command means Ford's
command stops reaching the car, and nothing originally guaranteed ours was at least as strong: on
one measured approach to a stopped vehicle the override held the command for nine seconds while
requesting a tenth of the deceleration Ford was already asking for. Ford's own request is now a
floor, so taking over can only ever add braking.

**It will not take the command below 25 mph, and that floor is NOT the protection it was thought to
be.** The rule came from replayed drives in which every takeover starting under Ford's own 20 mph
floor made the forward camera assert cancel, while those above it appeared tolerated. The first
three takeovers that were actually *driven* contradicted the second half of that: two of them armed
at 34 and 40 mph — well above the floor — and both provoked a cancel about 1.6 seconds later that
never released. Stock ACC was gone for the remainder of both drives and came back only after the car
was restarted.

So the honest position is that **taking the command away from the camera provokes a cancel at any
speed measured so far**, and the floor prevents only the worst version of it. What separates the one
tolerated takeover from the two that latched is not yet known; it is not the arming speed, and it is
not the size of the disagreement — the tolerated one had the largest.

**Losing the cancel is now recoverable without stopping the car.** Refusing to forward a cancelled
frame is what made the latch permanent: the camera's commands stopped reaching the car, so it could
never observe the car obeying it again and never had a reason to relent. After five seconds of a
cancel that this feature provoked, Ford's frame is forwarded again with that one bit cleared, for up
to thirty seconds, so the camera gets the evidence it was being denied. Whether it actually relents
is the open question and the reason both toggles still ship off.

**Both ship off**, and the on-screen ACC readout turns violet and reads `OP STOP` whenever openpilot
has taken the command, so it is visible rather than inferred.
