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

**It will not take the command below 25 mph.** Every measured takeover that began under Ford's own
20 mph floor made the forward camera assert cancel, and one latched it for the rest of the drive,
after which stock ACC was unavailable until the car was restarted. Above the floor the camera
tolerated every takeover measured, including one that ran 35 seconds to a full standstill. The cost
of that rule is real: a light you are already crawling towards at 20 mph is yours.

**Both ship off**, and the on-screen ACC readout turns violet and reads `OP STOP` whenever openpilot
has taken the command, so it is visible rather than inferred.
