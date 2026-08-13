### Diagnostics

Road reports are only as good as what can be measured afterwards, and several days were lost here to
tuning the wrong controller. These read the device's own logs:

- **`tools/bp_why_slow.py`** — which source governed a drive, and what caused every slowdown
- **`tools/bp_missed_curves.py`** — the opposite question: curves taken *too fast*, and whether that
  was the camera not seeing the bend, a target that was too generous, or the driver on the pedal
- **`tools/bp_hold_history.py`** — every change to the driver's hold, and what caused each one
- **`tools/bp_curve_runaway.py`** — curve slowdowns where the camera controller chased its own output
  down instead of settling, told apart from a legitimate slowdown by whether the corner the model
  claims keeps getting *tighter* as the car slows into it
- **`tools/bp_setspeed_hunting.py`** — bursts where the set speed was raised and lowered repeatedly,
  with each source's target, since the causes look identical from the driver's seat
- **`tools/bp_sunnylink_settings_audit.py`** — settings that exist on the car's screen but cannot be
  reached from SunnyLink, which is how you configure a comma 4X in practice

<!-- fragments: diagnostics -->
