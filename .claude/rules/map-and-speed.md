---
paths:
  - "sunnypilot/mapd/**"
  - "sunnypilot/selfdrive/controls/lib/smart_cruise_control/**"
  - "sunnypilot/selfdrive/controls/lib/speed_limit/**"
  - "sunnypilot/selfdrive/controls/lib/longitudinal_planner.py"
  - "selfdrive/controls/plannerd.py"
  - "tools/bp_mapd_*.py"
  - "tools/bp_scc_*.py"
  - "tools/bp_tsr_*.py"
  - "tools/bp_missed_curves.py"
  - "tools/bp_curve_runaway.py"
---
# Maps, Smart Cruise Control and Speed Limit Assist: standing facts

- `MapdV2`: 0 off, 1 observe (SLA reads v1), 2 on (v1 doesn't run). He runs 2. Pin: `MAPD_V2_VERSION`.
- Never consume `mapdOut.suggestedSpeed` (`test_mapd_schema.py` enforces it). The `Mapd*` capnp structs
  are mapd's; never insert fields into them.
- The map may refuse, never open. Refused already: `waySelectionType` fail and possible; a lower
  `nextSpeedLimit` on a motorway (an exit ramp).
- Never hand-run `mapd_v2` while manager has it. Offroad it publishes nothing; that is not a fault. A new
  param on its first flash: write `/data/params/d/<key>` directly.
- The SCC-Map path cache rebuilds on `logMonoTime` changing, with a 5 s age guard. The mapd stall
  watchdog allows 420 s cold start, then 60 s.
- `SmartCruiseControlMapDecel` is a trigger distance, not a rate. `longitudinalPlanSource` names a winner
  even when nobody asked: check `.active` and the published target.
- TSR (the car source) stays excluded (`SpeedLimitPolicy` map-only): it reads I-80 route shields as 80.
- He drives curves himself because of the PSCM; don't tune SCC map or vision sensitivities for him.
- Detail: `notes/HISTORY.md`, headings "MAPD V2 IS SHIPPED", "SCC-Map has four defenses",
  "2026-09-12: THE ROOT CAUSE", "TSR".
