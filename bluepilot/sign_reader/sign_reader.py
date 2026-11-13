#!/usr/bin/env python3
"""bp_sign_reader: reads speed-limit signs from the road camera and publishes signReadingBP.

Started by the manager only onroad and only when BPSignReaderEnabled is on. It is a separate process
that is ADDED to the stock set and publishes a message. Everything that can go wrong here (no
weights, no MNN, a model asking for an input this fork lacks, an exception in inference, the car
reporting trouble) ends in "publish modelLoaded=False and keep idling", never in a crash that could
take anything else with it.

It is also deliberately the most polite process on the device. The first drive with it really
reading (2026-11-12) never engaged, so: it is pinned to core 6, the one core nothing else is pinned
to (controlsd/card 4, plannerd/radard 5, modeld 7, the locationd family 0-3); it is single threaded
with no busy-waiting; it runs at the lowest scheduling class; it waits 45 s after start; it looks at
most four times a second; and bluepilot/sign_reader/guard.py stops it for the rest of the drive the
moment the car reports a comm or lag problem or its own inference gets slow.
"""
import os

# Thread settings must be in place before numpy or MNN load, which happens at the imports below.
for _var, _val in (("OMP_NUM_THREADS", "1"), ("OPENBLAS_NUM_THREADS", "1"), ("MKL_NUM_THREADS", "1"),
                   ("OMP_WAIT_POLICY", "PASSIVE"), ("GOMP_SPINCOUNT", "0")):
  os.environ[_var] = _val

import time  # noqa: E402

from cereal import messaging  # noqa: E402
from openpilot.common.swaglog import cloudlog  # noqa: E402

from bluepilot.sign_reader.guard import Guard  # noqa: E402
from bluepilot.sign_reader.reader import MODEL_PATH, SignReader, UnsupportedInput, services_needed  # noqa: E402

IDLE_PUBLISH_S = 5.0
CORE = 6  # the only core no driving process is pinned to
PIN_WAIT_S = 90.0


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


def _be_polite(pm):
  """Pin to the free core and drop to the lowest scheduling class. Refuses to run if it cannot."""
  # The big cores (4-7) are powered off while the car is parked and come online as a drive starts, and
  # pinning to an offline core fails with EINVAL. This process starts with the drive, so it may start
  # a moment BEFORE its core exists: retry for up to PIN_WAIT_S instead of giving up for the drive.
  deadline = time.monotonic() + PIN_WAIT_S
  while True:
    try:
      os.sched_setaffinity(0, {CORE})
      break
    except AttributeError as e:
      _idle(pm, f"no CPU pinning on this platform: {e}")
    except OSError as e:
      if time.monotonic() > deadline:
        _idle(pm, f"could not pin to core {CORE} within {PIN_WAIT_S:.0f} s: {e}")
      time.sleep(1.0)
  try:
    os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))
  except (AttributeError, OSError, PermissionError) as e:
    _idle(pm, f"could not drop to idle scheduling: {e}")


def main():
  pm = messaging.PubMaster(["signReadingBP"])
  _be_polite(pm)  # first, so every thread created later (MNN's included) inherits it
  guard = Guard(time.monotonic())

  try:
    reader = SignReader.load(MODEL_PATH)
  except Exception:
    cloudlog.exception("bp_sign_reader: model failed to load")
    reader = None
  if reader is None:
    _idle(pm, f"no MNN or no weights at {MODEL_PATH}")

  sm = messaging.SubMaster(services_needed(reader.spec) + ["onroadEvents"])
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
    if sm.updated["onroadEvents"]:
      guard.note_events([e.name for e in sm["onroadEvents"]], time.monotonic())
    if guard.stopped():
      _idle(pm, guard.stopped_reason)  # does not return: stops reading the camera for this drive
    if buf is None:
      continue
    now = time.monotonic()
    if guard.paused(now):
      reading = {}  # the car is struggling: say nothing rather than repeat a stale reading
    if guard.may_infer(now) and reader.wants_frame(client.frame_id):
      try:
        reading = reader.step(client.frame_id, buf.width, buf.height, buf.stride, buf.uv_offset, buf.data, sm)
      except Exception:
        cloudlog.exception("bp_sign_reader: inference failed")
        _idle(pm, "inference raised")
      guard.note_inference(now, time.monotonic() - now)
    # Published on EVERY frame, repeating the last reading between inferences. The consumer decides
    # "is this process alive" from how recently it heard, so a reading sent only when the model ran
    # would look dead most of the time.
    map_fresh = bool(sm.alive["mapdOut"] and sm["mapdOut"].tileLoaded) if "mapdOut" in sm.data else False
    _publish(pm, reading, True, map_fresh, client.frame_id)


if __name__ == "__main__":
  main()
