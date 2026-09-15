"""FusionPilot 2026-09-15: the Delphi MRR parser decodes only the detection signals the update reads.

Each of the 64 MRR_Detection messages carries ten signals and _update_delphi_mrr reads five. Decoding
the other five, 33 times a second, was a large share of the radar parse -- the biggest single cost in
card on a comma core measured 89-93% busy. Pruning them made RadarInterface.update 30% faster and,
replayed over six recorded segments of route 00000429 (36,002 updates, 84,349 radar points, parked and
driving), produced identical output on every call.

THE DANGER is the day someone reads a pruned signal: vl keeps every name, so it would silently read
0.0 forever. test_every_detection_signal_the_update_reads_is_kept parses _update_delphi_mrr itself
and fails that change at the source.
"""
import ast
import inspect
import math
import random

import pytest

from opendbc.can import CANPacker
from opendbc.car import structs
from opendbc.car.ford import radar_interface as ri
from opendbc.car.ford.values import RADAR


@pytest.fixture(scope="module")
def CP():
  from opendbc.car.ford.interface import CarInterface
  from opendbc.car.ford.values import CAR
  return CarInterface.get_non_essential_params(CAR.FORD_FUSION_MK5)


def _read_prefixes():
  """Every `msg[f"PREFIX_{ii:02d}"]` subscript inside _update_delphi_mrr, as its PREFIX."""
  tree = ast.parse(inspect.getsource(ri.RadarInterface._update_delphi_mrr).lstrip())
  prefixes = set()
  for node in ast.walk(tree):
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "msg":
      key = node.slice
      if isinstance(key, ast.JoinedStr) and key.values and isinstance(key.values[0], ast.Constant):
        prefixes.add(key.values[0].value.rstrip("_"))
  return prefixes


def test_every_detection_signal_the_update_reads_is_kept():
  read = _read_prefixes()
  assert read, "found no detection reads -- this guard has gone stale"
  assert read == set(ri._MRR_DETECTION_SIGNALS_READ), (
    f"_update_delphi_mrr reads {sorted(read)} but the parser keeps {sorted(ri._MRR_DETECTION_SIGNALS_READ)}")


def test_the_pruned_parser_decodes_exactly_the_read_signals(CP):
  parser = ri._create_delphi_mrr_radar_can_parser(CP)
  for i in range(1, ri.DELPHI_MRR_RADAR_MSG_COUNT + 1):
    state = parser.message_states[parser.dbc.name_to_msg[f"MRR_Detection_{i:03d}"].address]
    assert {s.name for s in state.signals} == {f"{p}_{i:02d}" for p in ri._MRR_DETECTION_SIGNALS_READ}
  # The headers are untouched, and vl still names every signal so nothing reading it can KeyError.
  full = ri._create_delphi_mrr_radar_can_parser(CP, prune=False)
  for name in ("MRR_Header_InformationDetections", "MRR_Header_SensorCoverage"):
    addr = parser.dbc.name_to_msg[name].address
    assert len(parser.message_states[addr].signals) == len(full.message_states[addr].signals)
  assert set(parser.vl["MRR_Detection_001"]) == set(full.vl["MRR_Detection_001"])


def _canon(rd):
  if rd is None:
    return None

  def fix(v):
    if isinstance(v, float) and math.isnan(v):
      return "NaN"
    if isinstance(v, dict):
      return tuple(sorted((k, fix(x)) for k, x in v.items()))
    if isinstance(v, (list, tuple)):
      return tuple(fix(x) for x in v)
    return v
  return fix(rd.to_dict())


def _scan(packer, rng, scan_index, bus):
  frames = [packer.make_can_msg("MRR_Header_InformationDetections", bus, {"CAN_SCAN_INDEX": scan_index})]
  for i in range(1, ri.DELPHI_MRR_RADAR_MSG_COUNT + 1):
    vals = {s.name: rng.random() * 40 for s in packer.dbc.name_to_msg[f"MRR_Detection_{i:03d}"].sigs.values()}
    vals[f"CAN_SCAN_INDEX_2LSB_{i:02d}"] = scan_index & 0b11 if rng.random() < 0.9 else rng.randrange(4)
    vals[f"CAN_DET_VALID_LEVEL_{i:02d}"] = int(rng.random() < 0.3)
    vals[f"CAN_DET_AZIMUTH_{i:02d}"] = rng.uniform(-0.5, 0.5)
    vals[f"CAN_DET_RANGE_{i:02d}"] = rng.uniform(1, 150)
    vals[f"CAN_DET_RANGE_RATE_{i:02d}"] = rng.uniform(-20, 20)
    frames.append(packer.make_can_msg(f"MRR_Detection_{i:03d}", bus, vals))
  coverage = ri.DELPHI_MRR_RADAR_RANGE_COVERAGE[scan_index & 0b11]
  frames.append(packer.make_can_msg("MRR_Header_SensorCoverage", bus, {"CAN_RANGE_COVERAGE": coverage}))
  return frames


@pytest.mark.parametrize("seed", range(3))
def test_pruned_and_full_parsers_give_identical_radar_output(CP, seed):
  rng = random.Random(seed)
  packer = CANPacker(RADAR.DELPHI_MRR)
  bus = ri.CanBus(CP).radar
  pruned = ri.RadarInterface(CP, structs.CarParamsSP())
  full = ri.RadarInterface(CP, structs.CarParamsSP())
  full.rcp = ri._create_delphi_mrr_radar_can_parser(CP, prune=False)
  outputs = points = 0
  scan = 0
  for step in range(240):
    if rng.random() < 0.05:
      scan = scan              # a frozen header, the Reverse signature
    else:
      scan += 1
    packet = [(step * 30_000_000, _scan(packer, rng, scan, bus))]
    a, b = pruned.update(packet), full.update(packet)
    assert _canon(a) == _canon(b), f"diverged at step {step}"
    if a is not None:
      outputs += 1
      points += len(a.points)
  assert outputs > 20 and points > 20, (outputs, points)
