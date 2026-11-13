# selfhost-backend

Goal: a fourth `BPConnectBackend` option, `selfhost`, so the comma sends drives to D.J.'s own backend (opconnect) instead of comma or Konik. Branched from icbm-manual-override-and-tuning at a370fb2ad on 2026-11-10. This branch is PRIVATE: push to `origin` (Forgejo) only, never to the `github` remote (ICBM is mirrored there and other people use it). Do not deploy it to the car; it must not be flashed while the car is on or without asking the other sessions first (ListAgents, SendMessage, wait for "clear").

## The backend (done, live)
- opconnect: repo `dj/opconnect` on Forgejo, running on docker-03, behind NPMplus at `https://car.saderholm.us` (LAN-only, real cert). `GET /healthz` returns {"ok":true}.
- Serves exactly what the device calls: `POST /v2/pilotauth/`, `GET /v1.4/{dongle}/upload_url/`, `GET /v1.1/devices/{dongle}`, and the PUT it hands out. No athena, no UI.
- Uploads only what `uploader.py` offers today: qlog(.zst), qcamera.ts, crash/, boot/. Lands on TrueNAS as `<dongle>/<route>/<file>`.

## What to build here
1. `bluepilot/backend_switch.py`: add `BACKEND_SELFHOST = "selfhost"` to BACKENDS (index order matches BPConnectBackend and the settings UI lists), a label, a pairing host, and a cached-dongle-id param like BPDongleIdComma/Konik. Switching backends mints a new dongle id; ids are cached per backend so switching back is lossless.
2. `launch_env.sh`: for selfhost export `API_HOST="https://car.saderholm.us"`; ATHENA_HOST to an `.invalid` address (athena is not implemented, so athenad must not retry forever against a real host).
3. The settings UI option lists and any place that enumerates backends.
4. Tests for the switch logic next to the existing ones. Run them before saying done.

## Not here
rlog or sentry-clip upload (a later branch, after a first drive uploads end to end; that needs `immediate_priority` in `system/loggerd/uploader.py`). The comma-side MCP server (branch `comma-mcp`). Remote access / VPN (branch `comma-remote-access`).

## Rules
- No secrets in chat, files or command lines. The comma can be lost or stolen: keep what lives on it minimal.
- ALLOWED_DONGLE_IDS on the opconnect stack gets set to the car's dongle id after first registration (a homelab change, D.J. merges).
