"""FusionPilot: the rear digest parser, which is built at car init.

WHY THIS SHAPE. `CANParser` needs compiled extensions and a real DBC, so nothing offline can
construct one -- and car-init code is exactly where this fork has already stranded the car once, on
a duplicate CAN registration that killed `card` and left it on "waiting to start". So this stubs the
parser and asserts on the ARGUMENTS, the same shape as test_can_parser_messages.py.

The bus number is the part worth pinning. Bus 1 already carries the front radar and ACC at 60-73%
load; the raw rear detections are ~2150 frames/s and must never appear there. This asserts the
parser subscribes to three digest messages and nothing else.
"""
import sys
import types

import pytest


@pytest.fixture
def stub_can_parser(monkeypatch):
  """Replace opendbc.can.CANParser before rear_radar imports it, and record the call."""
  calls = []

  class FakeParser:
    def __init__(self, dbc, messages, bus):
      calls.append({"dbc": dbc, "messages": messages, "bus": bus})
      self.can_valid = True
      self.vl = {
        "RearRadarLeft": {"Detected": 1, "DRel": 60.0, "YRel": -3.7, "VRel": 12.0, "TargetCount": 4},
        "RearRadarRight": {"Detected": 0, "DRel": 0.0, "YRel": 0.0, "VRel": 0.0, "TargetCount": 0},
        "RearRadarStatus": {"RadarAlive": 1, "ScanIndexOk": 1, "DetectionHz": 33,
                            "ValidDetections": 6, "FeederUptime": 120},
      }

    def update_strings(self, _):
      return []

  fake = types.ModuleType("opendbc.can")
  fake.CANParser = FakeParser
  monkeypatch.setitem(sys.modules, "opendbc.can", fake)
  for mod in [m for m in sys.modules if "rear_radar" in m]:
    monkeypatch.delitem(sys.modules, mod, raising=False)
  return calls


def _load():
  from openpilot.sunnypilot.selfdrive.car.rear_radar import RearRadarParser
  return RearRadarParser


class TestWhatItAsksTheBusFor:

  def test_it_parses_only_the_digest_and_only_on_bus_one(self, stub_can_parser):
    _load()(True)
    assert len(stub_can_parser) == 1
    call = stub_can_parser[0]
    assert call["dbc"] == "bp_rear_radar"
    assert call["bus"] == 1, "the digest is on bus 1; the raw radar must be on a private bus"
    names = [m[0] for m in call["messages"]]
    assert names == ["RearRadarLeft", "RearRadarRight", "RearRadarStatus"]

  def test_the_toggle_off_builds_no_parser_at_all(self, stub_can_parser):
    """Not merely ignored output -- no parser, so a car isolating this puts nothing on the bus and
    costs nothing at init."""
    p = _load()(False)
    assert stub_can_parser == []
    assert not p.available
    assert p.update([]) is None


class TestItCannotTakeDownCard:

  def test_a_missing_dbc_is_survived(self, monkeypatch, stub_can_parser):
    """A DBC that fails to load must not stop the car starting. This fork has stranded the car on
    car-init before; that is the whole reason this test exists.

    Patches the name WHERE IT IS USED rather than swapping sys.modules again: rear_radar binds
    CANParser at import, so whether a module-cache swap reaches it depends on what else the suite
    imported first. That made this pass alone and fail in the full run.
    """
    import openpilot.sunnypilot.selfdrive.car.rear_radar as rr

    def boom(*a, **kw):
      raise RuntimeError("no such dbc")
    monkeypatch.setattr(rr, "CANParser", boom)
    p = rr.RearRadarParser(True)
    assert not p.available
    assert p.update([]) is None

  def test_a_malformed_frame_returns_none_rather_than_raising(self, stub_can_parser):
    p = _load()(True)

    def boom(_):
      raise ValueError("garbage")
    p.cp.update_strings = boom
    assert p.update([b""]) is None


class TestWhatItHandsUp:

  def test_a_healthy_digest_decodes_both_sides(self, stub_can_parser):
    out = _load()(True).update([b""])
    assert out["dataAvailable"] is True
    assert out["left"]["detected"] is True
    assert out["left"]["dRel"] == 60.0 and out["left"]["vRel"] == 12.0
    assert out["right"]["detected"] is False

  def test_a_dead_radar_behind_a_live_feeder_is_not_available(self, stub_can_parser):
    """The failure the status message exists for. Every field decodes and nothing is stale."""
    p = _load()(True)
    p.cp.vl["RearRadarStatus"]["RadarAlive"] = 0
    assert p.update([b""])["dataAvailable"] is False

  def test_a_collapsed_detection_rate_is_not_available(self, stub_can_parser):
    p = _load()(True)
    p.cp.vl["RearRadarStatus"]["DetectionHz"] = 2
    assert p.update([b""])["dataAvailable"] is False
