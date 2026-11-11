# Parked recorder (Tesla-style "Sentry" for the comma): design note

Branch `sentry-mode`, 2026-11-10. Design only, no code. Status: draft for D.J.

**Naming.** `system/sentry.py`, `BPSentryEnabled` and the `sentry.*` calls are crash/telemetry reporting and are
untouched. This feature is the **parked recorder**: process `parked_recorder`, params `ParkedRecording*`, files
`*_bp.py` / `bluepilot/parked_recorder/`. The word "sentry" does not appear in any identifier.

## 1. What StarPilot does (MIT, read from `4a553da`)

`system/sentryd/` (about 220 lines plus a 100-line detector), toggle `SentryModeEnabled`.

- **Trigger: accelerometer only.** No camera motion detection, no panda/CAN bump input. `MotionDetector`
  compares the magnitude of consecutive accelerometer samples (polled every 0.1 s); a step above
  `SentryModeSensitivity` (0.04 m/s^2 default) counts. 10 hits (about 1 s, "warning") produces a capture;
  more than 25 hits spanning 30 s produces an "alarm". Counters reset after 60 s.
- **Arming:** 90 s after the process starts (to ignore the owner walking away). Off whenever
  `deviceState.started`.
- **What runs parked:** `sensord` (gated `started or SentryModeEnabled`) and `sentryd` (gated `not started
  and SentryModeEnabled`). `camerad` is started only for a moment by setting `SentryModeCapture` and calling
  `snapshot()`; it is then stopped again, so cameras are off in between.
- **What it stores:** **two JPEG stills** per event (`wide.jpg`, `driver.jpg`) in `/data/media/0/sentryd/<id>/`.
  No video. Last event goes in a Params JSON; an HTTP POST to its own web UI ("Galaxy") plus optional
  ntfy/webhook URL notifies the owner.
- **Power:** it does not manage the battery itself. It leans on `hardwared`: the stock offroad timer and low
  voltage shutdown, plus a notification when the shutdown reason fires or voltage <= 11.8 V.
- **Lessons:** the sensor path is cheap and proven; stills-on-trigger is the low-power shape; no
  pre-roll, so it never captures the approach, only the bump after the fact; no retention/quota logic.

IQ.Pilot: only its public one-line claim ("records bumps while parked") was used. Nothing else read.

## 2. What this hardware can do parked

All of this is from the code in this tree; **numbers marked (measure) need a bench or in-car measurement and
the comma is offline, so none are verified.**

- **Power model is on/off, not suspend.** After ignition off the comma 3X stays fully powered from the harness
  (panda sees the 12 V line) until `hardwared` sets `DoShutdown`. There is no sleep state an accelerometer
  can wake the SoM from. The LSM6DS3 is on the SoM, so a powered-off device cannot be woken by a bump; only
  ignition wakes it. Consequence: **the recorder is a bounded "stay awake" window after parking, not an
  indefinite watch.**
- **Existing shutdown timer.** `PowerMonitoring.should_shutdown` (`system/hardware/power_monitoring.py`) shuts down
  on: `MaxTimeOffroad` (default **1800 s = 30 min**, 0 = never), LPF car voltage below `VBATT_PAUSE_CHARGING`
  11.8 V after 60 s offroad, estimated battery capacity <= 0, all gated on `in_car` and not `DisablePowerDown`,
  and `ForcePowerDown` always wins. Today that is the entire battery protection.
- **Ignition:** `onroad_conditions["ignition"]` comes from panda `ignitionLine/ignitionCan`. Ignition on sets
  `started` and the recorder must stop within one loop and hand cameras back to `camerad`/`encoderd`.
- **Process gating:** `sensord` is `only_onroad` and `camerad` is `driverview` (`process_config.py`), so a
  parked recorder needs both gated on `started or parked_recorder_active`. `encoderd` is `only_onroad`;
  `stream_encoderd` exists for `notcar`, a pattern for a non-onroad encoder.
- **Power draw (measure).** Screen-off, cameras-off comma 3X offroad is on the order of 3-5 W (the
  `deviceState.powerDrawW` / `HARDWARE.get_current_power_draw()` figure is already logged every 30 s offroad);
  cameras + encoder + sensord add several watts. At about 4 W, 8 hours is 32 Wh, roughly 5-6% of a 60 Ah
  12 V battery (720 Wh). That is why triggered capture with cameras duty-cycled off is the default, and why
  continuous recording is out of scope.
- **MICI:** same SoC family and same hardwared/sensord/loggerd code, but different cameras and no panda-less
  ignition path assumptions; treat MICI as "same design, power numbers measured separately". Nothing in the
  design depends on a TICI-only API (uses `HARDWARE`, `Paths`, `snapshot`).

## 3. Design

### Trigger
- v1: accelerometer, same idea as StarPilot but with a **high-pass filtered vector delta** (subtract a slow
  running mean per axis, then threshold magnitude), not the scalar-magnitude difference, so gravity tilt from
  wind/temperature drift does not trip it, and with `ParkedRecordingSensitivity` (0.5-10, default 3).
- Door slams and passengers entering a *recently parked* car are the common false positives: **arm delay 120 s**
  after ignition off (parameterised), 30 s cooldown between events.
- v2 (optional, after v1 is measured): low-fps frame differencing on the wide road camera for "someone
  approaching". It needs cameras on, so it costs power; keep it behind a separate param and the same budget.
- No panda/CAN trigger in v1 (Ford doors/alarm signals are not decoded here).

### What it records
- v1: on trigger, a short burst of stills (wide + driver, N frames over about 10 s) through the existing
  `system/camerad/snapshot.py` path, cameras started only for the burst. Cheapest, already works parked.
- v2: short HEVC clip (about 20 s, pre-roll needs cameras on so it is **not** offered) via a parked-mode encoder
  process in the `stream_encoderd` style, `ParkedRecordingClipSeconds`.

### Process and wiring (BluePilot conventions)
- New process `parked_recorder` in `bluepilot/parked_recorder/parked_recorder_bp.py`, added via `procs +=` in
  `system/manager/process_config.py` with `only_offroad and ParkedRecordingEnabled`; never replaces a stock proc.
- `sensord`/`camerad` gating: the smallest change to the stock lambdas, wrapped in `# BluePilot:` / `# End BluePilot`.
- Pure detector (`detector_bp.py`) has no I/O so it is unit tested without hardware.

### Params (off by default, in `bluepilot/params/params.json`, PERSISTENT + BACKUP)
| Name | Type | Default | Meaning |
|---|---|---|---|
| `ParkedRecordingEnabled` | bool | false | master flag |
| `ParkedRecordingSensitivity` | float | 3.0 | clamp 0.5-10 |
| `ParkedRecordingMaxHours` | float | 4 | hard window after ignition off, clamp 0.25-12 |
| `ParkedRecordingMinVoltage` | float | 12.3 | stop below this LPF voltage, clamp 12.0-13.0 |
| `ParkedRecordingMaxStorageMB` | int | 2048 | clamp 256-8192 |
| `ParkedRecordingClipSeconds` | int | 20 | v2 only |
Runtime state: `ParkedRecordingStatus` (JSON, CLEAR_ON_MANAGER_START, DONT_LOG), `ParkedRecordingLastEvent`.
Settings UI: a toggle plus the above in `selfdrive/ui/bp/layouts/settings/bluepilot.py`, with text that says it
keeps the device awake after parking.

## 4. Battery-drain safeguards (hard requirement: never flatten the car battery)

Four independent cutoffs; any one ends recording, and the device then shuts down through the stock path.

1. **Time:** `ParkedRecordingMaxHours` after ignition off (default 4 h). Because stock `MaxTimeOffroad` is 30 min,
   enabling the feature must raise the effective limit to this window, and only while it is enabled. Do that
   in `PowerMonitoring.max_time_offroad_exceeded` with a BluePilot comment block: effective limit =
   `ParkedRecordingMaxHours` when enabled **and** voltage guard healthy, else the stock value. At expiry the
   normal `should_shutdown` fires, so the device actually powers off, not just stops recording.
2. **Voltage:** recorder stops at LPF voltage < `ParkedRecordingMinVoltage` (12.3 V default), *above* the stock
   11.8 V shutdown so recording ends first and stock stays the backstop. Hysteresis: needs to stay low 30 s
   (LPF is 45 s tau already). Instantaneous voltage reads are not used (starter/door-light transients).
3. **Energy:** stop when the estimated `carBatteryCapacityUwh` falls under 50% of the cap.
4. **Fail closed:** if voltage is unknown (no panda / `in_car` false), the recorder does not run. `DisablePowerDown`
   is **not** honoured as an extension: with it set the recorder still obeys 1-3 and the feature warns. A
   watchdog in `hardwared` (not the recorder process) enforces the time limit, so a hung recorder cannot hold the
   device on. `ForcePowerDown` is untouched.

Tests: simulated voltage sag, timeout, panda missing, recorder process crash, ignition during burst.

## 5. Storage and retention

- **Own directory** `/data/media/0/parked_recorder/<date>/<event>/`, **outside** `Paths.log_root()` so
  `deleter.py` (free-space deletion, 5-preserved-segment rule via `user.preserve`), `uploader.py` and the
  Portal route scanner never see it and cannot delete or upload it.
- **Own quota** `ParkedRecordingMaxStorageMB` (default 2 GB) plus a free-space floor (stop writing if
  `get_available_percent()` is under the same threshold loggerd's deleter uses). When full, delete oldest
  events first. Stills are about 200-400 KB each, so 2 GB holds thousands.
- Nothing here sets `user.preserve` on driving routes and nothing is `preserved`-by-default: parked events are
  independent of the existing preserve logic (documented so nobody wires them together).
- Event dir holds `event.json` (time, trigger score, voltage, files), so the Portal needs no scanning.

## 6. Reaching the Portal

- New read-only module `bluepilot/backend/handlers/parked_events.py`:
  `GET /api/parked/events`, `GET /api/parked/events/:id/:file` (image/video with byte-range, like
  `/api/video`), `DELETE /api/parked/events/:id` and `POST /api/parked/clear`.
- Reads are open; deletes allowed only offroad (reuses the Portal onroad 403 lockout). Path components are
  validated against the event directory (no traversal).
- Web: a "Parked" tab in `bluepilot/web/src/` (vanilla JS), new events listed with thumbnails; WebSocket event
  `parked_event` when one lands.
- The Portal is reachable only when the phone is on the comma's network (hotspot or home Wi-Fi). Parked at
  home that works; away from home the owner sees events on next connection. No push in v1.

## 7. Privacy

- **Local only by default.** Nothing leaves the device: not uploaded by `uploader.py` (outside log_root), not
  sent to comma connect, no webhook/ntfy in v1. Any later notification is an explicit opt-in param with the
  destination set by the owner.
- Driver-camera frames of an empty parked car are low-risk, but wide/road frames capture bystanders; stills are
  not annotated with GPS, and events are never added to `BackupParams`/sunnylink backups (files, not params).
- Quota and "clear all" are available in the Portal and the settings screen.

## 8. Phases

1. Detector + params + process gating + safeguards + tests (all unit-testable off-device).
2. Stills capture, storage quota, Portal tab. Bench test with the comma on the bench power supply; **needs D.J.'s
   go-ahead before touching the comma or the car**.
3. Measure power parked (cameras on/off) in the real car, then tune default window and thresholds.
4. Optional: video clips, camera-motion trigger.

## 9. Open questions for D.J.

- OK with a **bounded** window (default 4 h) rather than "all night"? Longer needs either measured draw or a
  bigger 12 V margin.
- Stills first, video later: acceptable?
- Is the Portal (on Wi-Fi/hotspot) enough for viewing, or is remote notification wanted?
- Confirm raising the effective offroad limit only while the feature is on is acceptable (it touches
  `power_monitoring.py`, a stock file, with BluePilot markers).

## 10. Archive to the NAS (added 2026-11-10, from the opconnect session; not decided)

D.J. wants parked-event footage on the NAS. This changes section 7's "never uploaded": local-only stays the
default, and archival becomes an **explicit opt-in** (`ParkedRecordingUpload`, bool, default false).

- **Backend:** opconnect (`https://car.saderholm.us`, LAN-only; device side is `BPConnectBackend=3`, on
  `passing-assist-phase1`, not yet deployed to the car). Upload is `GET v1.4/{dongle}/upload_url/?path=<key>`
  with the device JWT, which returns `{url, headers}`, then a PUT. Keys of the form `<event-id>/<file>` are
  accepted with no server change. Files land on TrueNAS as `<dongle>/<route>/<file>`.
- **Missing piece:** `uploader.py` only offers qlog/qcamera/crash/boot from `log_root`. It needs a small
  separate step in the parked recorder (or a BP uploader) that uploads from our own directory, so events
  still stay out of `log_root` and the stock deleter. Only runs when on the LAN; events are kept locally until
  the PUT succeeds, then follow the normal quota.
- **Open (D.J.):** quality. Stills and short clips are small. The road camera is 10 Mbps (about 4.5 GB/hour),
  so continuous or HQ footage is not an opconnect job; keepers go to Nextcloud via Hermes. Design for stills +
  short clips unless he says otherwise.
- **Honk trigger idea (untested):** no horn signal is logged. `soundPressure` (micd) is 10 Hz in rlog, about
  1 Hz in qlog, and a honk may spike it. Check: note the time of a real honk and read `soundPressure` in that
  segment before building anything. If it works it is one more trigger, but it is an *onroad* trigger, so it
  belongs with a separate onroad-event feature, not the parked recorder.
- **Retention context:** `deleter.py` frees the oldest segments under 10% or 5 GB free and protects only the
  newest `PRESERVE_COUNT=5` preserved segments, so many automatic onroad triggers would push old clips out.
  This is one more reason parked events live in their own directory and quota.

## 11. Follow-up research (2026-11-10, at D.J.'s request: private project, read both)

**StarPilot, Galaxy side (the web UI that views events).** Read after the first pass.
- Events are catalogued in `events.json` next to the images (atomic write, mode 0600), newest first. Events
  with no catalog entry are discovered by scanning the directory. Worth copying: the catalog is the source of
  truth, the directory scan is the recovery path.
- Viewing: list, per-image GET, DELETE (**refused unless offroad**, path-traversal checked), and a **live
  view** that starts `camerad` on demand for one frame and stops it again.
- Notifications: web push (VAPID) and optional webhook/ntfy, **rate limited to one per 180 s**.
- **No retention or quota anywhere in the Galaxy code** (no cap on event count or size, so it grows until the
  disk is full). Our quota in section 5 is a real addition, not a copy.
- Still a shallow clone (no history).

**IQ.Pilot (`IQLvbs/iqpilot`, branches `alpha`/`beta`/`release`, read at D.J.'s request; licence ignored by D.J.'s
decision, private use).** The first pass only looked at `main`'s default checkout and wrongly said it had no parked
recorder. It does, but the daemon is a compiled, signed bundle (`iqpilot_hephaestusd_private`), so what is readable is
the wiring, the param list, the module names and the power code, **not the detection logic or thresholds.**

- **Shape:** one process `iqsentryd`, split into modules named `imu`, `vision` (`iqsentryvisiond`), `body`,
  `media`, `policy`, `upload`. So it has an accelerometer trigger, a camera-based trigger, a "body" input (the
  door-check/locked params below suggest vehicle door and lock state from CAN), a clip writer, a policy layer
  and an uploader. The module names are the only evidence for those roles.
- **Params (beta `params_keys.h`):** `IQSentry` (master, off), `IQSentrySensitivity` (int, 3), `IQSentryDurationH`
  (int, 0), `IQSentryDoorCheck`, `IQSentryRequireLocked`, `IQSentryKeepStartups`, and `IQSentryRecording`
  (CLEAR_ON_MANAGER_START). Semantics of `DurationH=0` and `KeepStartups` are not readable.
- **Recording is video, through the stock stream encoder.** `IQSentryRecording` turns on `camerad` and
  `stream_encoderd` (the `notcar`/livestream encoder), and `iqsentryd` runs when parked **or** while recording
  onroad. That is the same route as our v2 idea (a parked encoder in the `stream_encoderd` style), which is now
  a pattern with a precedent rather than a guess. Cameras are *not* on all the time: the recording flag is the gate.
- **It is non-blocking.** `iqsentryd` is in `NON_BLOCKING_PROCESSES`, so a crash or hang in it cannot raise a
  selfdrive alert or stop driving. Copy that: our process must be non-blocking too.
- **Door/lock gating** (`DoorCheck`, `RequireLocked`) means it can arm only when the car is locked. On Ford, door
  and lock states are not decoded in this tree (section 3), so this is a later, DBC-dependent feature.
- **Power: FastSleep, their "deep standby", is software, not hardware suspend.** After 300 s parked with the screen
  off (or when voltage sags) `hardwared` sets `DevicePowerState=low_power`, blanks the panel, and the manager
  stops the heavy processes (`ui`, `updated`, uploader, model manager, map manager) through a `not_low_power` gate.
  The device stays powered, `iqsentryd` is **not** in the shed list so it keeps running, and ignition or charging
  wakes it. Thresholds: enter at 11.8 V, exit at 12.8 V, hard shutdown at 11.5 V, with the 30 Wh capacity
  bookkeeping shutdown disabled while FastSleep is on (they use measured voltage instead). Some EVs (EV6, Tesla,
  VW MEB) set `no_sleep` and skip the standby entirely.
- **Not found / not readable:** detection thresholds and filtering, clip length and pre-roll, storage limits,
  upload target, door-signal decoding, any notification path. Do not assume any of those.

**What this confirms and changes in section 2/4:** my "no suspend state, bounded window" model holds (their
standby is process shedding, not suspend). It adds something we should do: while the recorder is armed, **shed the
heavy parked-time processes (UI, updater, map/model managers, uploader) and blank the screen**, as a lower-draw
standby. That, not leaving the full stack idle, is what makes a multi-hour window affordable. It also warns that
the stock 30 Wh bookkeeping model empties within hours at offroad draw regardless of real battery state, so my
safeguard 3 (capacity < 50%) would fire early on that estimate; use measured voltage as the primary guard and
treat capacity as advisory.

**Changes this suggests to the design (both products):** add `events.json` as the catalog with a recovery scan; make live view
an optional later feature (starts the cameras on demand, so it costs power and falls under the same budget);
rate-limit any notification (180 s is a sensible default).

## 12. Decisions from D.J. (2026-11-10)

- Window: **4 hours** after ignition off is fine.
- **Video is wanted** (stills first is acceptable only as a stepping stone).
- Offroad-limit change (section 9, question 4): delegated to Claude; use the marked edit in `power_monitoring.py`.
- Viewing: D.J. does not want to build on the BluePilot Portal (it is upstream BluePilot code, off by default via
  `EnableWebRoutesServer`, and opconnect plus a planned MCP to the comma overlap it). Preferred path is
  archive to opconnect/NAS (section 10). Not yet decided: how events are listed/viewed there, and what happens
  when the car is away from the LAN (clips must wait on the device until it can upload).
- Battery: D.J. wants the car battery looked at for real. Plan: a parked-time logger (car voltage + `powerDrawW`
  every 30 s) so the drain curve is measured before the window or voltage floor is trusted. Needs D.J.'s OK
  before anything touches the car or comma.
- 2026-11-10, later: D.J. does not think the Portal is needed (this is his own fork, opconnect + a planned MCP
  cover it, sunnylink also exists). **Design change: drop the Portal viewer (section 6) from scope.** Events
  are listed by `event.json` on the device and viewed through opconnect/MCP.
- D.J. plans a **SIM card and Tailscale on the comma.** That lifts the "LAN-only" limit on upload (if opconnect
  is reachable on the tailnet; opconnect session to confirm) but adds: cellular data limits (upload event clips
  only, with a daily byte cap param), modem + Tailscale draw while parked (must be in the battery measurement),
  and Tailscale is not in this repo (installed on the device, outside the tree).
- 2026-11-10, later: SIM is the Computers 4 People Shield plan, per D.J.: unlimited data, possible T-Mobile
  deprioritization after 100 GB/month (slower when congested, not cut off). Not independently verified. So the
  upload byte cap is a safety net, not a budget: keep the param, default generous (suggest 5 GB/day), and
  prefer uploading when the network is not congested only if it ever matters. Video clips are fine to upload.
- 2026-11-10, opconnect session's answers (D.J. decides the open ones):
  - Viewer: **opconnect has none** (only `GET /drives`, JSON, groups files by first path segment). D.J. wants it
    kept basic, Hermes via MCP to find footage. Adopted at no server cost: upload keys `<event-id>/<file>` so an
    event shows as a "route", plus a small `event.json` (trigger, time, GPS if any, voltage, file names) uploaded
    with the clips so Hermes can search without opening video.
  - Size: server has no limit, but nginx (NPMplus) in front is **untested** for large PUTs; testing leaves a test
    row/file in the live backend, so needs D.J.'s OK. No resumable upload (a drop re-sends the whole file;
    fine for 20 s clips; chunked/Range resume can be added if needed). Signed URL lasts 1 h, bound to dongle+path.
  - Network: comma-to-opconnect over the tunnel is **untested**. The name must resolve to NPMplus (10.69.25.12)
    from the comma (AdGuard answers the wildcard on the LAN only: needs split DNS or a subnet route), and the
    `lan-only` access list may or may not include the tunnel range. **Open: UniFi WireGuard or Tailscale?**
    (the comma-remote-access branch notes say WireGuard.)
  - sunnylink replacement: opconnect implements none of it (sunnylink talks to its own backend: roles, users,
    backups, uploader, SSO pairing) and will not take it on unless D.J. asks explicitly. No de-cloud inventory is
    being done by anyone yet.

## 13. Phase 2 as built: video capture (code written, NOT run on a device)

- **How:** on a bump the recorder sets `ParkedRecordingCapture`; the manager then starts `camerad` and
  `stream_encoderd` (both gated with `or_(..., parked_capture)`, BluePilot markers). The stream encoder publishes
  H.264 frames as cereal messages (`livestream*EncodeData`: header + data per frame, 20 Hz). The recorder
  subscribes without conflation (a dropped frame would corrupt the clip) and writes `wideRoad.h264` and
  `driver.h264` (Annex-B, header repeated per frame so it decodes from any point) into the event directory.
  Nothing is transcoded on the comma; the server can remux to MP4 (ffmpeg is not on the comma-side path).
- **Clip length** `ParkedRecordingClipSeconds` (5-60, default 20), counted from the first frame. Gives up if no
  frame arrives in 20 s. Hard cap 120 MB per event. At most 12 captures per hour (rate limit), so wind cannot
  keep the cameras on; a rate-limited bump still gets its `event.json`, with `captured:false`.
- **Cameras off again** as soon as the clip ends, the guard trips (window, voltage, no voltage), or the process exits.
- **Limits you should know:**
  - The clip starts a few seconds *after* the bump (cameras take that long to start). Keeping the cameras on
    while armed would catch the bump itself but costs far more power; that is a later, measured decision.
  - The comma's cameras face **forward (wide road) and into the cabin (driver)**. Bumps to the side or rear
    are heard by the accelerometer but not seen. It is a front-of-car and break-in recorder, not 360 degrees.
  - Untested on hardware: the livestream encoder bitrate (default 5 Mbps per camera, ~12 MB per 20 s camera
    clip), camerad start time, and whether camerad/encoderd start cleanly while parked with the screen off.
- **Tests:** 58 unit tests with a fake messaging layer (stop-on-trip and no-frames paths mutation-checked).

## 14. Parked battery logger (code written, not run in the car)

`ParkedBatteryLog` (bool) starts `parked_battery_logger` while the car is off; it appends a row every 30 s to
`/data/media/0/parked_recorder/battery_log.csv` (rotated at 5 MB): raw and filtered car voltage, `powerDrawW`,
`somPowerDrawW`, temperature, CPU, network, screen, the recorder's state, and a run label (`ParkedBatteryLogLabel`).
Read-only. Summary: `python -m bluepilot.parked_recorder.battery_log_bp [csv]` prints, per label, hours, start/end
voltage, mV/hour slope and mean/max watts.

**Car test (needs D.J. there and his OK; nothing here has run on a device):**
1. Park, ignition off, doors closed, comma on. Set `ParkedBatteryLog=1` and a label (`stock`, `recorder armed`,
   `modem+vpn`, ...), one configuration per night so the runs can be compared.
2. The comma shuts down after `MaxTimeOffroad` (30 min stock) unless the recorder window is on; for a longer
   baseline, raise `MaxTimeOffroad` for the test only. The 11.8 V low-voltage shutdown stays as the backstop.
3. Ignore the first 30-60 minutes of each run: a battery that has just been driven sits above its resting voltage
   and drifts down on its own (surface charge), which looks like drain. Compare slopes after that.
4. Judge by mV/hour and watts, not one reading. Draw (W) is the comma's own meter; the voltage slope is the
   battery's answer, and what sets the real safe window and floor.

## 15. Bug found before the first deploy (2026-11-10)

`common/params_keys.h` is the list `Params` actually accepts at runtime; `params.json` only describes params (the
Portal reads it) and `bp_params.initialize_custom_params` is not called anywhere. Six Parked* keys were only in
`params.json`, so on a device the recorder's first write to `ParkedRecordingLastEvent` would raise and its settings
(sensitivity, storage, clip length) would never be read. Fixed by registering every key in `params_keys.h`;
`test_params_registered.py` now fails if a key used by the code, or listed in `params.json`, is missing there.
The fake Params in the other tests cannot catch this class of bug; only a real device run or this check can.

## 16. Bench test results (2026-11-10, comma on a bench, 5 V supply, parked, not in the car)

Comma at 827311b42. Recorder and logger were enabled only for the test and switched off again; test files removed.

- **Registered params work** (the section 15 fix): both processes started, nothing raised.
- **Bug found:** the recorder stopped itself with `no_voltage` on its first loop. The panda's first voltage message
  arrives about 0.25 s after start, and the guard failed closed at 100 ms and latched. Fixed with a 15 s grace
  period (`NO_VOLTAGE_GRACE_S`); 3 tests fail without it.
- **The comma reads about 4.98 V on the bench**, so the 12.3 V floor correctly stops the recorder there (guard
  working as designed). The recorder cannot run on a 5 V supply at all; recorder runs need the car's battery.
- **Camera capture (forced via `ParkedRecordingCapture`, no recorder):** `camerad` and `stream_encoderd` start,
  first frame **2.1 s** after the flag; 20 s clip = 401 wide frames (13.1 MB) + 400 driver frames (12.6 MB), about
  5.2 Mbps each, **about 26 MB per event**. Both files **decode completely** (PyAV: 401 and 400 frames at
  1928x1208). Both processes stop within 8 s of clearing the flag. The driver camera is infrared (reads red/dark).
  Correction to section 13: SPS/PPS headers arrive on every fifth frame (keyframes, gop 5), not on every frame;
  the writer simply writes whatever header comes with each frame, which is correct.
- **Power (comma's own meter):** idle parked, screen off, wifi: **3.0-3.5 W** (SoM 1.5-1.8 W). Cameras plus
  encoder: **about 5.5 W** (peaks 6.5 W), back to 2.9 W in 8 s. So a 20 s clip costs about 0.03 Wh, negligible;
  idle draw is what matters for the battery.
- **Accelerometer noise at rest (60 s, 106.8 Hz):** residual after the detector's baseline: median 0.008, p99
  0.018, p99.9 0.021, **max 0.023 m/s^2**. The default threshold (sensitivity 3 = 0.1 m/s^2) is about 4x the worst
  noise seen, with zero false triggers at any sensitivity from 1 to 10. A mount in a car (wind, passing trucks) will
  be noisier; the real number needs the car. `sensord` starts and stops with `ParkedRecordingActive` as designed.
- **Not tested:** a real bump through the recorder (needs the car's 12 V and someone to knock on it), the
  recorder-driven capture end to end, the battery logger against a real battery, upload.
- **Battery logger worked** (rows every 30 s with voltage, draw, temperature, recorder state).

## 17. Research: what could Sentry do to deter a thief? (2026-11-10, read-only; nothing built)

**The car:** the comma's stored CarParams say `FORD_FUSION_MK5` (brand ford, plain CAN, not CAN-FD, safety config `ford`).
The Fusion DBC (`ford_fusion_2018_pt.dbc`) is small (70 signals) but has `Doors` (message 947: `Door_FL/FR/RL/RR_Open`)
and `Steering_Buttons` (message 131, with `Left_Turn_Light` / `Right_Turn_Light`). No hazard, horn, alarm or lock *command*
exists in this tree for the Fusion.

**Can the comma command the car (lights/horn/lock) while parked? Almost certainly not as it stands:**
- `pandad.cc` sets the panda to `NO_OUTPUT` whenever the ignition is off or the device is not onroad (its own comment
  mentions ELM327 as the alternative), so nothing can be transmitted parked without changing that.
- `SAFETY_ALLOUTPUT` (send anything) is compiled only under `ALLOW_DEBUG`, i.e. not in release panda firmware.
- `SAFETY_ELM327` skips the allow-list but its TX hook only passes ISO 15765-4 diagnostic addresses (0x7DF, 0x7xx,
  0x6xx, 0x18DB33F1, 0x18DAxxF1), 8 bytes: diagnostic/UDS requests, the family FORScan uses, not ordinary control
  messages. Whether the harness bus reaches the body module at all is unknown (the harness taps the camera side; FORScan
  goes through the OBD port).
- Passing assist's blinker is a **spoof of the steering-stalk message** (`Steering_Buttons`, `create_button_msg(turn_signal=)`
  on the main bus), sent only onroad under the Ford safety mode. That is the stalk position (left/right), not a body-module
  command, and there is no hazard signal in it. A parked car with the bus asleep will not act on it. Bus sleep timing after
  ignition off is unverified.
- **Experiment that settles it (needs the car, read-only, tell D.J. first):** log CAN with the car off and locked, see
  whether `Doors`/`Steering_Buttons` still appear and for how long after shutdown.

**Home Assistant / FordPass (cloud via the car's own modem), not the comma's CAN:**
- Fusion modem: 2017 "SYNC Connect", 2018 "FordPass Connect" (some 2018 still SYNC Connect), 2019 on Ford's list, all 2020+.
  Ford lists remote start/stop, lock/unlock, location and health as available. Bulletin SSM 49084 (2017-2020) notes some
  Fords with inoperative remote features until the modem is fixed.
- HA integrations (itchannel / jrprinty / no-gram / crowedavid forks of fordpass-ha): lock/unlock and remote start are
  evidenced; "Guard Mode" is listed (only supported cars). **Honk, flash lights and alarm: not confirmed in any search
  result.** Unofficial API; one report of account lockout above ~200 calls per 24 h. Check the installed fork's entity list.
- Remote start: Ford designs it so the car cannot be shifted out of park without a recognised key inside (forum and owner-
  manual evidence, anecdotal). It does not deter anyone, wastes fuel/battery and is a CO risk in a garage. Not recommended as a response.

**Speaker (Sentry-owned deterrent):** `hardwared` puts the device in power save when the ignition is off and the screen is off,
and `set_power_save(True)` shuts the speaker amplifier down (the code says "amplifier, 100mW at idle"). `soundd` is
only started with `driverview`. So a parked siren needs: leave power save (re-initialise the amp), start soundd, play, then
power back down. Untested; loudness outside the car unknown; amp draw while playing unknown.

**Screen:** it faces the driver, so only someone inside the car sees it.

**Unknowns that remain:** bus behaviour parked (car test), whether the speaker is audible outside and its draw (bench test),
whether D.J.'s FordPass HA fork exposes honk/flash/alarm (check the entity list), 12 V battery size for a Fusion (and whether
it is the hybrid, `FordPrefHybridVehicle` was not set on the comma).

## 18. `DisablePowerDown` gap closed (2026-11-10)

With `DisablePowerDown` set, `power_monitoring.should_shutdown` never fires (stock behaviour), so the end of the 4 h window
and the low-voltage floor only stopped the recorder; the comma kept drawing about 3 W. Sentry now **refuses to run** while
`DisablePowerDown` is on (guard reason `power_down_disabled`, shown in `ParkedRecordingStatus`, latched for the ignition
cycle): it only runs when hardwared can be relied on to power the device off at the end of the window. D.J.'s setting is
not overridden. Tests: 3 guard tests plus 1 process test (mutation-checked).

## 19. Parked idle measurement, and why sleep-mode shedding is not worth building yet (2026-11-10)

Comma on the bench (5 V, wifi, screen 0%, about 19 min after a reboot), one 8 s read-only sample:
- **Total draw 2.12 W, of which the SoM 0.83 W.** So the processor module is under 40% of the idle draw; the rest is the
  board (panda, amp, regulators). The earlier 3.0-3.5 W readings were taken soon after a rebuild, before power save had
  settled; steady parked idle is about 2.1 W.
- CPU is 84% idle. Biggest users: `pandad` 23% (stock, polling), `sunnypilot` modeld-side process 9.6%, `selfdrive.ui.ui` 8.9%
  (screen off but still rendering), `hardwared` 5%, loggerd 2%, `mapd_v2` 1.7%, sunnylink 1.6%. 1.4 GB of 3.6 GB RAM used.
- **Battery maths:** 2.12 W at 12 V is 0.18 A. A 4 h window is about 8.5 Wh, 0.7 Ah. Even a small 12 V battery (35-45 Ah, size
  for the Fusion not yet confirmed) loses 2% or less. IQ.Pilot-style shedding would remove part of the SoM's 0.83 W: roughly
  0.1-0.3 W, i.e. 0.03-0.07 Ah over 4 h. Not worth the added failure modes (a stuck gate leaves the UI off in the car).
- **What does matter:** cameras and encoder (about 5.5 W while recording; keep clips short and rate limited, as built), and later
  the **SIM modem and VPN**, which are unmeasured and could add more than everything above. Re-run the battery logger once
  they are on, in the car, labelled by configuration, before deciding on shedding or on a longer window.
- Decision: **not building sleep-mode shedding now.** Revisit only if the car test shows a real drain, or for windows near 24 h.

## 20. What other cars do, and the parked warning (2026-11-10)

**Tesla Sentry Mode** (from owner-manual and press descriptions; names and volumes unconfirmed): two stages. A minor
trigger (someone leaning on the car) puts it in Alert: the cameras record and the **touchscreen shows a "cameras are
recording" message**, headlights flash. A major trigger (glass break, serious impact) starts an Alarm: car alarm, lights, the
screen brightens, reportedly loud music; the manual says it returns to standby after 30 s. **Other cars:** Rivian Gear Guard
is the closest (internal storage, up to 10 days of clips); GM announced OnStar Connected Cameras (30 s clips, but only when
the theft alarm fires, not on proximity); Polestar 4 per a forum post; Lucid reportedly lacks one; aftermarket dashcam
parking modes (Garmin, BlackVue, Thinkware) record on motion but do not deter.

**What deters (evidence is thin):** no rigorous study found on cameras. A Highway Loss Data Institute study reportedly found
alarms do not noticeably reduce theft losses; people ignore them. Best-supported measures: nothing visible inside, doors locked
(an unlocked door was the most common entry in an Aviva report), lit and busy parking, layered visible deterrents that add time.
So Sentry's real value is evidence and a response, and any deterrent is a bonus.

**Parked warning (built as logic only; the UI that draws it is NOT built):**
- `warning_bp.py`: `WarningTracker` escalates on one hard hit (score >= 8, i.e. the peak is 8x the trigger threshold) or 3
  triggers inside 120 s; `flash_frame()` gives the flash colour (styles: police red/blue, amber, white; capped at 3 flashes/s).
- The recorder raises `ParkedRecordingWarning` for `ParkedRecordingWarningSeconds` (10-120, default 30) when escalation fires and
  `ParkedRecordingWarningEnabled` is on (off by default). It clears the flag on expiry, on a guard stop, and on exit.
- **Next (needs a screen to look at):** a BluePilot UI overlay that wakes the screen and draws it while the flag is set;
  `selfdrive/ui` `Device` handles brightness and wake, and the overlay would hook in there. Preview mock published as a page.
- The screen faces the driver, so it only reaches someone inside the car. Red/blue may be restricted on private vehicles in
  some states; amber and white are the safe defaults, and the style is meant to be a setting.
- **D.J.'s choice (2026-11-10): default pattern is red/blue, then white, as a fast strobe, at maximum screen brightness.**
  Built into `warning_bp.py`: sequence `police_white` = police (red/blue) for 6 s, then white for 4 s, repeating, at 5 flashes/s
  by default; the ceiling was raised from 3 to 8 flashes/s at his request (still capped: fast saturated red is the worst case
  for photosensitive epilepsy). A NaN or negative time no longer crashes the function (found by a test). 90 tests.
  The overlay must set the backlight to 100% for the whole warning regardless of ambient light, keep the screen awake, and
  restore the previous brightness afterwards; that part is UI work and is not built.

## 21. FordPass in Home Assistant can honk and flash (2026-11-11)

D.J. showed his HA "Garage" device page (screenshot): **Doors lock/unlock, "Honk & Flash [1 sec.]" and "[3 sec.]" buttons, Local Sync and
Remote Sync buttons, remote-start (RC) start/extend-time, and a charge-level entity that was unavailable.** So the unknown in section 17
is answered: honk and flash exist as one-shot HA buttons. There is no separate "alarm" entity.
- Plan (not built): comma POSTs the event (and its tier: bump or warning) to an HA webhook; an HA automation presses Honk & Flash,
  optionally a few times with gaps, and sends the phone alert. The comma only needs the webhook step.
- Cautions: each press goes through Ford's cloud (latency of seconds, not instant); the unofficial API reportedly locks an account
  after about 200 calls in 24 h, so cap presses per event and per hour in the automation; the comma reaches HA only on home Wi-Fi
  until the SIM and VPN exist; remote start is still not a sensible response to a bump.
