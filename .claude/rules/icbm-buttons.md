---
paths:
  - "sunnypilot/selfdrive/car/intelligent_cruise_button_management/**"
  - "sunnypilot/selfdrive/car/cruise_ext.py"
  - "sunnypilot/selfdrive/controls/lib/speed_limit/hold.py"
  - "opendbc_repo/opendbc/sunnypilot/car/ford/icbm.py"
  - "opendbc_repo/opendbc/sunnypilot/car/ford/values_ext.py"
  - "opendbc_repo/opendbc/sunnypilot/car/ford/gap_control.py"
  - "tools/bp_hold_*.py"
  - "tools/bp_icbm_*.py"
  - "tools/bp_setspeed_*.py"
  - "tools/bp_why_slow.py"
---
# ICBM and holds: the button contract (muscle memory, settled 2026-08-03; ask before changing)

| Wheel key | Cruise engaged | Cruise off |
|---|---|---|
| RES+ (`CcAslButtnSetIncPress`) | `accelCruise`: creates or raises a HOLD | `resumeCruise`: engages and KEEPS the hold |
| SET- (`CcAslButtnSetDecPress`) | `decelCruise`: creates or lowers a HOLD | `setCruise`: engages; with an SLA number it CLEARS the hold, with none it holds the speed at the press |
| CNCL | his button sets `CcAslButtnCnclPress` (bit 8); `cancel` is mapped to bit 21, which never rises. Left as is on purpose. | |

- A HOLD is his own set speed; curves, leads and the hazard path keep working against it. It clears when
  the set speed returns to exactly SLA's target or the posted limit moves more than
  `IcbmBaselineResetDelta`, never for curves or leads. Without SLA, everything is a hold.
- The set-speed box IS the hold on the big screen (the badge is deleted there; mici keeps its badge).
- The tail of a RES+ press (`RESUME_TAIL_FRAMES`) never creates a hold. The SCCM clears the button bit
  between frames, so one press arrives as a burst and the release is not reliable (timers are capped at 2 s).
- A tap moves the set speed 1 mph, a held button 5 mph; ICBM taps inside `TAP_BAND` (verified on the road).
- Two set speeds: `cruiseState.speedCluster` (dash, m/s) vs `vCruiseCluster` (openpilot's max, kph).
  `vTarget` is post-hold; `vTargetRaw` is what the plan asked. Print both, labelled.
- An actuator that stops actuating holds its last value: never freeze the set speed. Delay only the
  permissive direction (a rise), never a fall.
- The 20 mph floor is Ford's; ICBM cannot stop the car.
- After resolving a merge in `controller.py`, re-check every row of this table.
- Detail: `notes/HISTORY.md`, headings "The ICBM button contract", "WITHOUT SLA, EVERYTHING IS A HOLD",
  "THE SET SPEED HUNT".
