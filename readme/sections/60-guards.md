### Guards for what tests cannot reach

- **`tools/bp_offline_test.py`** — the offline suite, which re-execs under the pinned Python and stubs
  the device-only modules. Bare `pytest` fails here in ways that look like environment noise.
- **`tools/bp_merge_upstream.py`** — takes a newer BluePilot end to end: tags a rollback point,
  regenerates `car_list.json` rather than merging it, prints what is ours in each conflict, and runs
  the suite.
- **Static checks** for duplicate CAN registrations that strand the car at boot, capnp fields added
  without their dataclass mirror, params declared twice, `int()` on a capnp enum, and settings that
  ship without a control to reach them.

