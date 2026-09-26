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
