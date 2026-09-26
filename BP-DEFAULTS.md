# Every setting this branch ships, and what it ships as

Generated -- do not hand-edit. Regenerate with:

```bash
python tools/bp_list_defaults.py --md > BP-DEFAULTS.md
```

Your car keeps the FIRST value it ever booted for a key, so where the code has moved since, your
device may still hold the old one. That is what this table is for: walk the screens and compare.


A `(was X)` means the shipped default moved during development -- those are the ones most likely
to disagree with your device.

| Where | Control | Ships as | Key |
|---|---|---|---|
| Cruise | Curve Sensitivity (High Speed) | **80** | `SmartCruiseControlVisionHighSpeedFactor` |
| Cruise | Curve Sensitivity (Low Speed) | **70** | `SmartCruiseControlVisionLowSpeedFactor` |
| Cruise | Forget My Set Speed On Limit Change | **10** | `IcbmBaselineResetDelta` |
| Cruise | Let openpilot Change The Follow Gap | **Off** | `IcbmGapControl` |
| Cruise | Map Curve Braking Rate | **8** | `SmartCruiseControlMapDecel` |
| Cruise | Mapped Corner Speed - Highway | **100** | `SmartCruiseControlMapHighSpeedFactor` |
| Cruise | Mapped Corner Speed - Tight | **90** | `SmartCruiseControlMapFactor` |
| Cruise | Max Set Speed Drop Per Step | **12** | `IcbmMaxTargetDrop` |
| Cruise | Max Set Speed Rise Per Step | **5** | `IcbmMaxTargetRise` |
| Cruise | Resume Minimum Gap | **6** | `IcbmResumeMinGap` |
| Cruise | Resume Minimum Lead Speed | **5** | `IcbmResumeMinLeadSpeed` |
| Cruise | Send GPS To The Camera | **On** | `FordSynthesizeApimGps` |
| Cruise | Slow For Stop Signs And Lights | **On** | `IcbmModelStopEnabled` |
| Cruise | Slow For Unconfirmed Vehicles | **70** | `IcbmLeadMaxTtc` |
| Cruise | Smart Cruise Control - Map | **On**  (was 0) | `SmartCruiseControlMap` |
| Cruise | Smart Cruise Control - Vision | **On**  (was 0) | `SmartCruiseControlVision` |
| Cruise | Stop Sign Sensitivity | **10** | `IcbmModelStopMinDecel` |
| Cruise | Unconfirmed Vehicle Max Distance | **180** | `IcbmLeadMaxDistance` |
| Cruise | Wait For The Car Ahead Before Resuming | **On** | `IcbmResumeGateEnabled` |
| FusionPilot | High Speed Adjustment Factor | **0.68**  (was 1.0) | `FordHighSpeedFactor_ang` |
| FusionPilot | High Speed Low Curve Adjustment Factor | **0.78**  (was 1.0) | `FordHighSpeedDampening_ang` |
| FusionPilot | Hold Steering Through Stops (mph) | **0.0** | `FordLowSpeedAngleHold_ang` |
| FusionPilot | In-Lane Offset | **0.0** | `custom_path_offset_ang` |
| FusionPilot | Lane Centering Damping | **0.0** | `lane_centering_damping_ang` |
| FusionPilot | Lane Centering Strength | **0.15** | `lane_centering_strength_ang` |
| FusionPilot | Low Speed Adjustment Factor | **0.981**  (was 1.0) | `FordLowSpeedFactor_ang` |
| Steering > Customize Blinker | Blink Spacing If Unmeasurable (ms) | **760** | `FordBlinkerBlinkPeriod` |
| Steering > Customize Lane Change | Cancel By Turning The Blinker Off | **4** | `AutoLaneChangeCancelWindow` |
| Steering > Customize Lane Change | Steer Back When Cancelled | **On** | `AutoLaneChangeRevert` |
| Steering > Customize Lane Change | Wait After Blind Spot Clears | **3** | `AutoLaneChangeBsmHoldTime` |
| Steering > Customize Lane Change | Your One-Touch Flash Time | **5.5** | `AutoLaneChangeOneTouchTime` |
| Steering > Customize Passing Assist | Assume An Unknown Middle Lane Is A Turn Lane | **On** | `PassingAssistStrictTwoWay` |
| Steering > Customize Passing Assist | Be Fussier When Not In A Hurry | **18** | `PassingAssistPatience` |
| Steering > Customize Passing Assist | Call It A Slow Pass After | **8** | `PassingAssistCrawlTime` |
| Steering > Customize Passing Assist | Check The Lane Before Suggesting It | **On** | `PassingAssistAdjacentLane` |
| Steering > Customize Passing Assist | Chime When It Decides Or Backs Out | **On** | `PassingAssistChime` |
| Steering > Customize Passing Assist | Close In Before Passing | **0** | `PassingAssistMinApproach` |
| Steering > Customize Passing Assist | Confirm For | **1** | `PassingAssistConfirmTime` |
| Steering > Customize Passing Assist | Do Not Pass In A Bend Tighter Than | **13** | `PassingAssistMaxCurve` |
| Steering > Customize Passing Assist | Keep Right Except To Pass | **On** | `PassingAssistKeepRight` |
| Steering > Customize Passing Assist | Lane Must Have Been There | **15** | `PassingAssistMinLaneAge` |
| Steering > Customize Passing Assist | Look Ahead | **220** | `PassingAssistMaxDistance` |
| Steering > Customize Passing Assist | Make The Lane Change Itself | **On** | `PassingAssistActuate` |
| Steering > Customize Passing Assist | Never Pass Into Oncoming Traffic | **On** | `PassingAssistOncomingVeto` |
| Steering > Customize Passing Assist | Only Above | **30** | `PassingAssistMinSpeed` |
| Steering > Customize Passing Assist | Passing Assist (Log Only) | **On** | `PassingAssistLogEnabled` |
| Steering > Customize Passing Assist | Remember Oncoming Traffic For | **90** | `PassingAssistOncomingMemory` |
| Steering > Customize Passing Assist | Settle After A Pass | **20** | `PassingAssistSettleTime` |
| Steering > Customize Passing Assist | Show It On The Dash | **On** | `ShowPassingInCluster` |
| Steering > Customize Passing Assist | Show Next Lane Speeds | **On** | `ShowAdjacentLanes` |
| Steering > Customize Passing Assist | Show Oncoming Speeds | **On** | `ShowOncomingSpeeds` |
| Steering > Customize Passing Assist | Show The Onroad Panel | **On** | `ShowPassingAssist` |
| Steering > Customize Passing Assist | Signal Before Moving | **1** | `PassingAssistBlinkerLead` |
| Steering > Customize Passing Assist | Slower By At Least | **4** | `PassingAssistMinDeficit` |
| Steering > Customize Passing Assist | Stay Quiet After You Take An Exit | **45** | `PassingAssistExitStandDown` |
| Steering > Customize Passing Assist | Use The Rear Radar | **On** | `PassingAssistRearRadar` |
| Steering > Customize Passing Assist | Wait Before Moving Right | **5** | `PassingAssistKeepRightDelay` |
| Steering > Customize Passing Assist | Wait If The Car Ahead Slams On | **On** | `PassingAssistLeadBrakingHold` |
