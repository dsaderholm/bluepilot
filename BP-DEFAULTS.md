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

A `(was X)` means the shipped default moved during development -- those are the ones most likely
to disagree with your device.
