#!/usr/bin/env python3
"""bp_sign_reader: reads speed-limit signs from the road camera and publishes signReadingBP.

Started by the manager only onroad and only when BPSignReaderEnabled is on. It is a separate process
that is ADDED to the stock set and publishes a message; nothing consumes it yet. Everything that can
go wrong here (no weights, no MNN, a model asking for an input this fork lacks, an exception in
inference) ends in "publish modelLoaded=False and keep idling", never in a crash that could take
anything else with it.
"""
import os
import time

from cereal import messaging
from openpilot.common.swaglog import cloudlog

from bluepilot.sign_reader.reader import MODEL_PATH, SignReader, UnsupportedInput, decode_reading, services_needed

IDLE_PUBLISH_S = 5.0


def _publish(pm, reading: dict, loaded: bool, map_fresh: bool, frame_id: int):
  msg = messaging.new_message("signReadingBP")
  m = msg.signReadingBP
  m.speedLimit = reading.get("speedLimit", 0.0)
  m.signSpeedLimit = reading.get("signSpeedLimit", 0.0)
  m.signAgreement = reading.get("signAgreement", 0.0)
  m.heldSignConfidence = reading.get("heldSignConfidence", 0.0)
  m.modelLoaded = loaded
  m.mapInputsFresh = map_fresh
  m.frameId = frame_id
  pm.send("signReadingBP", msg)


def _idle(pm, reason: str):
  cloudlog.warning(f"bp_sign_reader idle: {reason}")
  while True:
    _publish(pm, {}, False, False, 0)
    time.sleep(IDLE_PUBLISH_S)


def main():
  try:  # lowest scheduling priority: this must never compete with the driving processes
    os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))
  except (AttributeError, OSError, PermissionError):
    pass

  pm = messaging.PubMaster(["signReadingBP"])

  try:
    reader = SignReader.load(MODEL_PATH)
  except Exception:
    cloudlog.exception("bp_sign_reader: model failed to load")
    reader = None
  if reader is None:
    _idle(pm, f"no MNN or no weights at {MODEL_PATH}")

  sm = messaging.SubMaster(services_needed(reader.spec))
  try:
    reader.check_inputs(sm)
  except UnsupportedInput as e:
    _idle(pm, f"model asks for an input this fork cannot provide: {e}")

  from msgq.visionipc import VisionIpcClient, VisionStreamType
  client = VisionIpcClient("camerad", VisionStreamType.VISION_STREAM_ROAD, True)
  while not client.connect(False):
    time.sleep(0.1)

  reading = {}
  while True:
    buf = client.recv(1000)
    sm.update(0)
    if buf is None:
      continue
    if reader.wants_frame(client.frame_id):
      try:
        reading = reader.step(client.frame_id, buf.width, buf.height, buf.stride, buf.uv_offset, buf.data, sm)
      except Exception:
        cloudlog.exception("bp_sign_reader: inference failed")
        _idle(pm, "inference raised")
    # Published on EVERY frame, repeating the last reading between inferences (the model looks about
    # every 2 s). The consumer decides "is this process alive" from how recently it heard, so a
    # reading that was only sent when the model ran would look dead most of the time.
    map_fresh = bool(sm.alive["mapdOut"] and sm["mapdOut"].tileLoaded) if "mapdOut" in sm.data else False
    _publish(pm, reading, True, map_fresh, client.frame_id)


if __name__ == "__main__":
  main()
