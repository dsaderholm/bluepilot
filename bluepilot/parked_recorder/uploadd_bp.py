#!/usr/bin/env python3
"""Parked event upload process.

Runs only while the car is off and ParkedRecordingUpload is set (see process_config.py). Sends captured sentry events to the self-hosted
backend, and only when ALL of these hold: the backend is the self-hosted one (BPConnectBackend == 3, so footage can never go to comma's
cloud by mistake), the link is unmetered Wi-Fi, and the daily byte cap has room. Uses the same signed-URL flow as the stock uploader.
"""
import datetime
import time

import cereal.messaging as messaging
import requests
from cereal import log
from openpilot.common.api import Api
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

from bluepilot.parked_recorder import storage_bp
from bluepilot.parked_recorder.upload_bp import EventUploader

SELFHOST_BACKEND = 3
NetworkType = log.DeviceState.NetworkType
LOOP_S = 5.0
PUT_TIMEOUT = (10, 120)


def main() -> None:
  params = Params()
  dongle_id = params.get("DongleId")
  if not dongle_id:
    cloudlog.info("parked_uploadd: no dongle id")
    return
  api = Api(dongle_id)

  def get_url(key: str):
    try:
      resp = api.get(f"v1.4/{dongle_id}/upload_url/", timeout=10, path=key, access_token=api.get_token())
      if resp.status_code != 200:
        return None
      data = resp.json()
      return data["url"], data.get("headers", {})
    except Exception:
      return None

  def put(url: str, headers: dict, path) -> int | None:
    try:
      with open(path, "rb") as f:
        return requests.put(url, data=f, headers=headers, timeout=PUT_TIMEOUT).status_code
    except (requests.RequestException, OSError):
      return None

  uploader = EventUploader(storage_bp.EVENTS_ROOT, get_url, put)
  sm = messaging.SubMaster(["deviceState"])
  while True:
    sm.update(1000)
    try:
      ds = sm["deviceState"]
      ok = (params.get_bool("ParkedRecordingUpload") and params.get("BPConnectBackend") == SELFHOST_BACKEND
            and ds.networkType == NetworkType.wifi and not ds.networkMetered)
      if ok:
        result = uploader.step(time.monotonic(), datetime.date.today().isoformat(), params.get("ParkedRecordingUploadDailyMB"))
        cloudlog.event("parked_uploadd.step", result=result)
        if result in ("progress", "uploaded"):
          continue     # more to do, no pause
    except Exception:
      cloudlog.exception("parked_uploadd: step failed")
    time.sleep(LOOP_S)


if __name__ == "__main__":
  main()
