"""Speed-limit sign reader: the model-facing half of bluepilot/sign_reader.

Runs a small neural net over the road camera and reports the speed limit it sees. This module holds
everything that can be exercised without a camera (building the model's inputs, decoding its
outputs, the stepping class). sign_reader.py is the process that feeds it frames.

The model file is FrogPilot's speed_limit_reader.mnn. Its repository is MIT licensed, but nothing
this author has read says what licence covers the trained weights, so THE WEIGHTS ARE NOT IN THIS
REPOSITORY. They are copied onto the device by hand (see MODEL_PATH) and the process idles, loudly,
when they are missing. This code is written against the model's own self-description (the JSON in
its `bizCode`: the numbers, text and outputs it wants), not copied from FrogPilot's source.

Units: the model speaks m/s. 0 means "no limit".
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

MODEL_PATH = Path("/data/bluepilot/models/speed_limit_reader.mnn")

# Far older than any real message. Used for "age" inputs when a source never reported.
NEVER_S = 1e4

# What the model returns that we publish. Names are the model's own output names.
READING_OUTPUTS = ("speedLimit", "signSpeedLimit", "signAgreement", "heldSignConfidence")


class UnsupportedInput(Exception):
  """The model asks for an input this fork cannot provide. Refuse to run rather than feed it zeros."""


def build_text(values: list[str]) -> np.ndarray:
  """The model's text input: its text fields joined by NUL, as raw bytes."""
  return np.frombuffer("\0".join(str(v) for v in values).encode(), np.uint8)


def build_context(frame_id: float, width: int, height: int, stride: int, uv_offset: int,
                  numbers: list[float], state: np.ndarray) -> np.ndarray:
  """The model's context vector: camera geometry, then its numeric inputs, then its own last output.

  The model is recurrent: `state` is the full output vector from the previous step and goes back in
  untouched, which is how it holds a limit between frames and chooses the next frame to look at.
  """
  return np.array([frame_id, width, height, stride, uv_offset, *numbers, *state], np.float32)


def output_index(names: list[str]) -> dict[str, int]:
  return {name: i for i, name in enumerate(names)}


def decode_reading(state: np.ndarray, index: dict[str, int]) -> dict[str, float]:
  """The fields we publish, from the model's output vector. Non-finite becomes 0 (= no limit)."""
  out = {}
  for name in READING_OUTPUTS:
    value = float(state[index[name]]) if name in index else 0.0
    out[name] = value if np.isfinite(value) else 0.0
  return out


def nv12_from_bgr(bgr: np.ndarray) -> np.ndarray:
  """Flat NV12 (Y plane then interleaved UV), stride == width, from a decoded BGR frame.

  Only for tests and replays; on the device the camera hands over NV12 directly.
  """
  import cv2
  h, w = bgr.shape[:2]
  i420 = cv2.cvtColor(bgr, cv2.COLOR_BGR2YUV_I420).reshape(-1)
  y = i420[: w * h]
  u = i420[w * h: w * h * 5 // 4]
  v = i420[w * h * 5 // 4:]
  return np.concatenate([y, np.stack([u, v], 1).reshape(-1)]).astype(np.uint8)


def read_field(sm, field: str):
  """One model input by its name ("service.path"), from a SubMaster-like `sm`.

  `sm` needs: sm[service], sm.alive[service], sm.recv_time[service], sm.data (a dict of the
  services it has). Raises UnsupportedInput for a name this fork cannot answer.
  """
  service, _, path = field.partition(".")
  if service == "frogpilotCarState" and path == "vEgo":  # the model's name for the car speed
    return float(sm["carState"].vEgo)
  if service == "gpsLocationExternal" and service not in sm.data:
    return False if path == "hasFix" else NEVER_S  # no external GPS on this fork
  if service == "livePose" and path == "orientationNED.z":  # heading comes from locationd's kalman output
    value = sm["liveLocationKalman"].orientationNED.value
    return float(value[2]) if len(value) > 2 else 0.0
  if path == "age":
    received = sm.recv_time[service]
    return time.monotonic() - received if received else NEVER_S
  if path == "alive":
    return bool(sm.alive[service])
  if service == "mapdOut" and path in ("roadName", "wayRef"):
    return str(getattr(sm[service], path))
  node = sm[service]
  try:
    for part in path.split("."):
      node = getattr(node, part)
  except AttributeError as e:
    raise UnsupportedInput(field) from e
  return node


def services_needed(spec: dict) -> list[str]:
  """SubMaster services required to answer every input the model lists."""
  fields = spec["numbers"] + spec["text"]
  needed = set()
  for field in fields:
    service = field.split(".", 1)[0]
    if service == "frogpilotCarState":
      needed.add("carState")
    elif service == "livePose":
      needed.update(("livePose", "liveLocationKalman"))
    elif service != "gpsLocationExternal":
      needed.add(service)
  return sorted(needed)


class SignReader:
  """Steps the model once per chosen frame and keeps its recurrent state."""

  def __init__(self, modes: list, spec: dict):
    self.modes = modes  # the model is loaded twice, once per "mode" the model switches between
    self.spec = spec
    self.index = output_index(spec["outputs"])
    self.state = np.zeros(len(self.index), np.float32)

  @classmethod
  def load(cls, path: Path = MODEL_PATH):
    """A SignReader, or None when MNN or the weights file is missing (never raises for that)."""
    try:
      import MNN
    except ImportError:
      return None
    if not Path(path).is_file():
      return None
    runtime = MNN.nn.create_runtime_manager(({"backend": "CPU", "numThread": 1},))
    modes = [MNN.nn.load_module_from_file(str(path), ["frame", "text", "context"], [mode], runtime_manager=runtime)
             for mode in ("0", "1")]
    return cls(modes, json.loads(modes[0].get_info()["bizCode"]))

  def wants_frame(self, frame_id: int) -> bool:
    """The model picks which frame it wants next; skipping the rest is what keeps this cheap."""
    return frame_id >= self.state[self.index["nextFrameId"]]

  def step(self, frame_id: int, width: int, height: int, stride: int, uv_offset: int,
           nv12: np.ndarray, sm) -> dict[str, float]:
    import MNN
    numbers = [float(read_field(sm, f)) for f in self.spec["numbers"]]
    text = build_text([read_field(sm, f) for f in self.spec["text"]])
    context = build_context(frame_id, width, height, stride, uv_offset, numbers, self.state)
    mode = self.modes[int(self.state[self.index["nextMode"]])]
    self.state = mode.forward([
      MNN.expr.const(nv12, [nv12.size], MNN.expr.NCHW, MNN.expr.uint8),
      MNN.expr.const(text, [text.size], MNN.expr.NCHW, MNN.expr.uint8),
      MNN.expr.const(context, [context.size], MNN.expr.NCHW, MNN.expr.float),
    ])[0].read()
    return decode_reading(self.state, self.index)

  def check_inputs(self, sm) -> None:
    """Raise UnsupportedInput now, at startup, if any input cannot be read."""
    for field in self.spec["numbers"] + self.spec["text"]:
      read_field(sm, field)
