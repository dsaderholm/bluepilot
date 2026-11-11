"""Runs the real model over real road video. Skips unless everything it needs is on disk.

Needs: MNN and cv2 importable, the weights at MODEL_PATH (or BP_SIGN_READER_MODEL), and a copy of
route 000005b1 segment 4 (D.J.'s daytime drive) under BP_ROUTE_DIR. At 58.3 s into that segment the
car passes a 30 mph sign that the map also says is 30; the reader must hold 30 mph afterwards.
Not part of the offline suite (tools/bp_offline_test.py): the video is 1.7 GB and not in the repo.
"""
import os
import types
from pathlib import Path

import numpy as np
import pytest

from bluepilot.sign_reader import reader as R

MPH = 2.23694
MODEL = Path(os.environ.get("BP_SIGN_READER_MODEL", R.MODEL_PATH))
ROUTE_DIR = Path(os.environ.get("BP_ROUTE_DIR", "/home/claude/work/route-replays/routes"))
SEG = next(iter(sorted(ROUTE_DIR.glob("000005b1--*--4"))), None) if ROUTE_DIR.is_dir() else None


class _SM:
  """Just enough SubMaster, with steady made-up inputs: the point is the camera, not the map."""

  def __init__(self):
    ns = types.SimpleNamespace
    self.data = {
      "carState": ns(vEgo=11.0),
      "livePose": ns(inputsOK=True, sensorsOK=True),
      "liveLocationKalman": ns(orientationNED=ns(value=[0.0, 0.0, 0.0])),
      "gpsLocation": ns(hasFix=True),
      "mapdOut": ns(tileLoaded=True, wayId=1234, roadName="1300 East", wayRef=""),
    }
    self.alive = {k: True for k in self.data}
    self.recv_time = {k: 1.0 for k in self.data}

  def __getitem__(self, k):
    return self.data[k]


def _sm():
  return _SM()


@pytest.mark.skipif(SEG is None or not MODEL.is_file(), reason="needs the weights and route 000005b1 segment 4")
def test_reads_a_thirty_sign_and_holds_it():
  cv2 = pytest.importorskip("cv2")
  pytest.importorskip("MNN")
  reader = R.SignReader.load(MODEL)
  assert reader is not None
  sm = _sm()
  cap = cv2.VideoCapture(str(SEG / "fcamera.hevc"))
  last = {}
  frame_id = 0
  while True:
    ok, bgr = cap.read()
    if not ok:
      break
    if reader.wants_frame(frame_id):
      h, w = bgr.shape[:2]
      last = reader.step(frame_id, w, h, w, w * h, R.nv12_from_bgr(bgr), sm)
    frame_id += 1
  assert last["speedLimit"] * MPH == pytest.approx(30.0, abs=0.5)
