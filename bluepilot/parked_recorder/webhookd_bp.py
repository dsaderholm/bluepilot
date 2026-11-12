#!/usr/bin/env python3
"""Parked webhook sender process.

Runs only while the car is off and ParkedRecordingWebhookEnabled is set (see process_config.py). Every 10 s it sends whatever the recorder has
queued in the outbox to ParkedRecordingWebhookUrl. The URL is a secret: it is never logged.
"""
import time
from pathlib import Path

from openpilot.common.params import Params

from bluepilot.parked_recorder import storage_bp
from bluepilot.parked_recorder.webhook_bp import Outbox, Sender

LOOP_S = 10.0


def main() -> None:
  params = Params()
  sender = Sender(Outbox(Path(storage_bp.EVENTS_ROOT) / ".outbox"))
  while True:
    try:
      url = params.get("ParkedRecordingWebhookUrl")
      if params.get_bool("ParkedRecordingWebhookEnabled") and isinstance(url, str):
        sender.pump(url, time.monotonic())
    except Exception:
      pass   # never let a bad setting or a full disk kill the sender, and never print the URL
    time.sleep(LOOP_S)


if __name__ == "__main__":
  main()
