- **`tools/bp_can_nav_diff.py`** — diffs a drive recorded while a navigation app was routing against
  one with no route active, per address and per byte, to find whether the turn instruction is
  already on a bus the device reads
- **`tools/bp_route_intent_stub.py`** — publishes a scripted route, so the refusal gate can be
  driven end to end with no transport fitted
