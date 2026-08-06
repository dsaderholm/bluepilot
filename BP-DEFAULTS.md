# Every setting this branch ships, and what it ships as

Generated -- do not hand-edit. Regenerate with:

```bash
python tools/bp_list_defaults.py --md
```

Your car keeps the FIRST value it ever booted for a key, so where the code has moved since, your
device may still hold the old one. That is what this table is for: walk the screens and compare.

| Where | Control | Ships as | Key |
|---|---|---|---|
| BluePilot | High Speed Adjustment Factor | **0.87**  (was 1.0) | `FordHighSpeedFactor_ang` |
| BluePilot | Low Speed Adjustment Factor | **0.92**  (was 1.0) | `FordLowSpeedFactor_ang` |
| Cruise | Curve Detection Earliness | **110** | `SmartCruiseControlVisionEarliness` |
| Cruise | Curve Sensitivity (High Speed) | **100** | `SmartCruiseControlVisionHighSpeedFactor` |
| Cruise | Curve Sensitivity (Low Speed) | **110** | `SmartCruiseControlVisionLowSpeedFactor` |
| Cruise | Forget My Set Speed On Limit Change | **10** | `IcbmBaselineResetDelta` |
| Cruise | Map Curve Braking Rate | **8** | `SmartCruiseControlMapDecel` |
| Cruise | Max Set Speed Drop Per Step | **12** | `IcbmMaxTargetDrop` |
| Cruise | Max Set Speed Rise Per Step | **5** | `IcbmMaxTargetRise` |
| Cruise | Pinned Hold Range | **60** | `IcbmPinnedHoldRadius` |
| Cruise | Remember Holds By Location | **On** | `IcbmPinnedHoldsEnabled` |
| Cruise | Resume Minimum Gap | **6** | `IcbmResumeMinGap` |
| Cruise | Resume Minimum Lead Speed | **5** | `IcbmResumeMinLeadSpeed` |
| Cruise | Slow For Stop Signs And Lights | **On** | `IcbmModelStopEnabled` |
| Cruise | Slow For Unconfirmed Vehicles | **70** | `IcbmLeadMaxTtc` |
| Cruise | Smart Cruise Control - Map | **On**  (was 0) | `SmartCruiseControlMap` |
| Cruise | Smart Cruise Control - Vision | **On**  (was 0) | `SmartCruiseControlVision` |
| Cruise | Unconfirmed Vehicle Max Distance | **180** | `IcbmLeadMaxDistance` |
| Cruise | Wait For The Car Ahead Before Resuming | **On** | `IcbmResumeGateEnabled` |
| Steering > Customize Blinker | Blink Spacing If Unmeasurable (ms) | **760** | `FordBlinkerBlinkPeriod` |
| Steering > Customize Lane Change | Cancel By Turning The Blinker Off | **4** | `AutoLaneChangeCancelWindow` |
| Steering > Customize Lane Change | Steer Back When Cancelled | **On** | `AutoLaneChangeRevert` |
| Steering > Customize Lane Change | Wait After Blind Spot Clears | **3** | `AutoLaneChangeBsmHoldTime` |
| Steering > Customize Lane Change | Your One-Touch Flash Time | **5.5** | `AutoLaneChangeOneTouchTime` |
| Steering > Customize Passing Assist | Assume An Unknown Middle Lane Is A Turn Lane | **On** | `PassingAssistStrictTwoWay` |
| Steering > Customize Passing Assist | Call It A Slow Pass After | **8** | `PassingAssistCrawlTime` |
| Steering > Customize Passing Assist | Check The Lane Before Suggesting It | **On** | `PassingAssistAdjacentLane` |
| Steering > Customize Passing Assist | Chime When It Decides Or Backs Out | **On** | `PassingAssistChime` |
| Steering > Customize Passing Assist | Close In Before Passing | **0** | `PassingAssistMinApproach` |
| Steering > Customize Passing Assist | Confirm For | **1** | `PassingAssistConfirmTime` |
| Steering > Customize Passing Assist | Keep Right Except To Pass | **On** | `PassingAssistKeepRight` |
| Steering > Customize Passing Assist | Lane Must Have Been There | **15** | `PassingAssistMinLaneAge` |
| Steering > Customize Passing Assist | Look Ahead | **220** | `PassingAssistMaxDistance` |
| Steering > Customize Passing Assist | Never Pass Into Oncoming Traffic | **On** | `PassingAssistOncomingVeto` |
| Steering > Customize Passing Assist | Only Above | **30** | `PassingAssistMinSpeed` |
| Steering > Customize Passing Assist | Passing Assist (Log Only) | **On** | `PassingAssistLogEnabled` |
| Steering > Customize Passing Assist | Pause For | **15** | `PassingAssistSuspendMinutes` |
| Steering > Customize Passing Assist | Remember Oncoming Traffic For | **90** | `PassingAssistOncomingMemory` |
| Steering > Customize Passing Assist | Settle After A Pass | **20** | `PassingAssistSettleTime` |
| Steering > Customize Passing Assist | Show Next Lane Speeds | **On** | `ShowAdjacentLanes` |
| Steering > Customize Passing Assist | Show Oncoming Speeds | **On** | `ShowOncomingSpeeds` |
| Steering > Customize Passing Assist | Show The Onroad Panel | **On** | `ShowPassingAssist` |
| Steering > Customize Passing Assist | Signal Before Moving | **1** | `PassingAssistBlinkerLead` |
| Steering > Customize Passing Assist | Slower By At Least | **4** | `PassingAssistMinDeficit` |
| Steering > Customize Passing Assist | Stay Quiet After You Take An Exit | **45** | `PassingAssistExitStandDown` |
| Steering > Customize Passing Assist | Wait Before Moving Right | **10** | `PassingAssistKeepRightDelay` |
| Steering > Customize Passing Assist | Wait If The Car Ahead Slams On | **On** | `PassingAssistLeadBrakingHold` |

A `(was X)` means the shipped default moved during development -- those are the ones most likely
to disagree with your device.
