"""FusionPilot: replay HIS OWN button bits through the decoder and count what comes out.

Unit tests pin the shape of the fix; this pins the size of it. `_replay_buttons.json` is the raw
per-frame state of the five cruise-button bits of Steering_Data_FD1 on `src 0` -- what the car
itself put on the wire -- for routes 000003b6 and 000003b7 on 2026-08-24, 130,530 frames in total.
Nothing here is synthesised.

The invariant: the decoder must emit ONE press event per rising edge on the wire. Before the fix it
emitted about seventy for a single held RES+, and 452 accelCruise events across route b7 alone.
"""
import json
from pathlib import Path
from types import SimpleNamespace as NS

from opendbc.car import Bus, structs
from opendbc.sunnypilot.car.ford.carstate_ext import CarStateExt
from opendbc.sunnypilot.car.ford.values_ext import BUTTONS

ADDR = "Steering_Data_FD1"
NAMES = ["CcAslButtnSetIncPress", "CcAslButtnResIncPress", "CcAslButtnSetDecPress",
         "CcAslButtnCnclResPress", "CcButtnOnOffPress"]
ALL = sorted({b.can_msg for b in BUTTONS})
DATA = Path(__file__).parent / "_replay_buttons.json"


class FakeParser:
  def __init__(self):
    self.vl = {ADDR: dict.fromkeys(ALL, 0)}


def _replay(seq):
  ext = CarStateExt(NS(flags=0, carFingerprint="FORD_FUSION_MK5"), NS())
  cp = FakeParser()
  emitted, physical, prev = 0, 0, None
  for row in seq:
    bits, en = row[:5], row[5]
    if prev is not None:
      physical += sum(1 for i in range(5) if bits[i] and not prev[i])
    prev = bits
    for i, n in enumerate(NAMES):
      cp.vl[ADDR][n] = bits[i]
    ret = structs.CarState.new_message()
    ret.cruiseState.enabled = bool(en)
    ext.update(ret, structs.CarStateSP(), {Bus.pt: cp})
    emitted += sum(1 for b in ext.button_events if b.pressed)
  return physical, emitted


def test_his_drives_emit_one_event_per_physical_press():
  # Run-length encoded: [[bits, repeat], ...]. The raw per-frame capture is 2.6 MB of mostly
  # identical all-zero frames; the runs are 1.6 KB and expand to the identical sequence.
  runs = json.loads(DATA.read_text())
  assert runs, "replay capture is empty"
  data = {route: [bits for bits, n in rle for _ in range(n)] for route, rle in runs.items()}

  # Every route measured BEFORE any assertion. Asserting inside the loop aborts on the first
  # failure and hides the rest, and route b7 is the one that carried the 452 events.
  results = {}
  for route, seq in sorted(data.items()):
    physical, emitted = _replay(seq)
    results[route] = (len(seq), physical, emitted)
    print()
    print("  {}: {} frames | {} rising edges on the wire | {} press events emitted".format(
      route, len(seq), physical, emitted))

  bad = {r: v for r, v in results.items() if v[2] > v[1]}
  assert not bad, "; ".join(
    "{}: {} press events for {} physical presses ({:.1f}x)".format(
      r, v[2], v[1], v[2] / max(v[1], 1))
    for r, v in bad.items()) + " -- the storm is back"
