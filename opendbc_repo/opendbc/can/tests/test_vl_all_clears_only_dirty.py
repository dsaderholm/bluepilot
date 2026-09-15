"""FusionPilot 2026-09-15: CANParser.update clears only the vl_all lists the previous update filled.

It used to clear every list of every registered message on every call -- ~1,050 list.clear() calls
every 10 ms across card's three Ford parsers, measured at ~7-9% of card's CPU, on a comma core that
was 89-93% busy and the reason this fork cannot cap CPU frequency to run cooler.

The claim that makes the change safe is EQUIVALENCE: a list is appended to only inside a successful
MessageState.parse, and every successful parse adds its address to the set update() returns, so any
list not named by the previous call is already empty. These tests hold the shipped parser against a
reference that still clears everything, over randomized traffic that includes counter failures,
empty updates, several frames of one message in one call, and multi-entry batches.
"""
import random

import pytest

from opendbc.can import CANPacker, CANParser

DBC = "ford_lincoln_base_pt"
MSGS = [("EngBrakeData", 10), ("BrakeSysFeatures", 50), ("Steering_Data_FD1", 10), ("Yaw_Data_FD1", 100),
        ("EngVehicleSpThrottle", 100), ("ACCDATA_3", 5)]


class _ClearsEverything(CANParser):
  """The old behaviour: empty every vl_all list before each update."""
  def update(self, strings, sendcan: bool = False):
    for addr in self.addresses:
      for k in self.vl_all[addr]:
        self.vl_all[addr][k].clear()
    return super().update(strings, sendcan)


def _snapshot(parser, packer):
  out = {}
  for name, _ in MSGS:
    for sig in parser.dbc.name_to_msg[name].sigs:
      out[(name, sig)] = list(parser.vl_all[name][sig])
  return out


def _frame(packer, rng, name):
  msg = packer.dbc.name_to_msg[name]
  values = {s: rng.randrange(0, 4) for s in msg.sigs}
  return packer.make_can_msg(name, 0, values)


@pytest.mark.parametrize("seed", range(6))
def test_vl_all_matches_a_parser_that_clears_everything(seed):
  rng = random.Random(seed)
  packer = CANPacker(DBC)
  shipped = CANParser(DBC, MSGS, 0)
  reference = _ClearsEverything(DBC, MSGS, 0)
  t = 0
  filled = emptied_after_fill = 0
  prev_nonempty = set()
  for _step in range(400):
    batches = []
    for _ in range(rng.choice((0, 1, 1, 1, 2, 3))):
      t += 10_000_000
      frames = []
      for name, _ in MSGS:
        for _ in range(rng.choice((0, 0, 0, 1, 1, 2))):
          frames.append(_frame(packer, rng, name))
      if rng.random() < 0.1:
        frames.append((0x7FF, b"\x00" * 8, 0))      # unregistered address
      if rng.random() < 0.1:
        frames.append((frames[0][0] if frames else 357, b"\x01" * 8, 1))   # other bus
      rng.shuffle(frames)
      batches.append([t, frames])
    got = shipped.update(batches)
    want = reference.update(batches)
    assert got == want
    snap = _snapshot(shipped, packer)
    assert snap == _snapshot(reference, packer), f"diverged at step {_step}"
    nonempty = {k for k, v in snap.items() if v}
    filled += len(nonempty)
    emptied_after_fill += len(prev_nonempty - nonempty)
    prev_nonempty = nonempty
  # A run in which nothing parsed, or nothing ever had to be cleared, would pass vacuously.
  assert filled > 100 and emptied_after_fill > 100, (filled, emptied_after_fill)


def test_a_list_is_emptied_on_the_call_after_it_was_filled():
  packer = CANPacker(DBC)
  parser = CANParser(DBC, MSGS, 0)
  parser.update([0, [packer.make_can_msg("EngBrakeData", 0, {"CcStat_D_Actl": 3})]])
  assert parser.vl_all["EngBrakeData"]["CcStat_D_Actl"] == [3]
  parser.update([10_000_000, []])
  assert parser.vl_all["EngBrakeData"]["CcStat_D_Actl"] == []


def test_an_exception_part_way_through_still_gets_cleared_next_call(monkeypatch):
  """The dirty set is bound before the loop, so values appended before a raise are still cleared."""
  packer = CANPacker(DBC)
  parser = CANParser(DBC, MSGS, 0)
  state = parser.message_states[packer.dbc.name_to_msg["Yaw_Data_FD1"].address]
  real_parse = state.parse
  calls = {"n": 0}

  def parse_then_raise(t, dat):
    calls["n"] += 1
    if calls["n"] == 2:
      raise RuntimeError("boom")
    return real_parse(t, dat)

  monkeypatch.setattr(state, "parse", parse_then_raise)
  frames = [_frame(packer, random.Random(1), "Yaw_Data_FD1"), _frame(packer, random.Random(2), "Yaw_Data_FD1")]
  with pytest.raises(RuntimeError):
    parser.update([0, frames])
  sig = next(iter(packer.dbc.name_to_msg["Yaw_Data_FD1"].sigs))
  assert len(parser.vl_all["Yaw_Data_FD1"][sig]) == 1
  monkeypatch.setattr(state, "parse", real_parse)
  parser.update([10_000_000, []])
  assert parser.vl_all["Yaw_Data_FD1"][sig] == []
