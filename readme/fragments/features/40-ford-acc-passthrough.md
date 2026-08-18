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
is the one thing the car cannot do. For a few seconds at the end of such a stop openpilot sends the
braking instead of Ford, bounded in time, and hands straight back. It never takes over when a lead
is close, because Ford's stop-and-go already owns that case.

**Both ship off, and the reason is not caution about the code.** How the camera reacts to being
overridden for several seconds has not been measured: one drive saw it stop accepting commands after
about forty seconds of disagreement, another saw a second and a half and no reaction at all, and a
stop sits between the two. The on-screen ACC readout turns violet and reads `OP STOP` whenever
openpilot has taken the command, so it is visible rather than inferred.
