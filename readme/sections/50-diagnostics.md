### Diagnostics

Road reports are only as good as what can be measured afterwards, and several days were lost here to
tuning the wrong controller. These read the device's own logs:

- **`tools/bp_why_slow.py`** — which source governed a drive, and what caused every slowdown
- **`tools/bp_missed_curves.py`** — the opposite question: curves taken *too fast*, and whether that
  was the camera not seeing the bend, a target that was too generous, or the driver on the pedal
- **`tools/bp_hold_history.py`** — every change to the driver's hold, and what caused each one

<!-- fragments: diagnostics -->

