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
