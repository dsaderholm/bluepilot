"""FusionPilot: forwarding the camera's own ACCDATA, and the one thing that must never happen.

The idea: under op long the relay is open, so the camera's ACC command never reaches the car -- but
the camera still HAS all its inputs (bus 0 is forwarded to it) and is still computing. So rather
than authoring longitudinal ourselves, republish Ford's own numbers. His position is the design:
"I trust how Ford ACC works."

Everything mechanical about it was checked before any of this was written, because each could have
killed it:

  - ACCDATA carries NO counter and NO checksum, so a repack is trivial and handing control BACK
    after an override needs no resynchronization.
  - The DBC leaves eight bits unmapped. Across 108,388 real frames on this car all eight are zero,
    so the repack is byte-identical in practice.
  - Panda caps braking at -3.4991 m/s^2 and the signal can express -20. Across 189,418 braking
    frames Ford never exceeded -2.70, so the cap never binds and no forwarded frame gets blocked.

WHAT THESE TESTS ARE FOR: the fail-safe. A CANParser's `vl` dict holds the last value FOREVER, so a
dead camera bus is invisible from the values alone. Forwarding a frozen brake request is the worst
outcome this feature has, and it is the one thing no amount of "it works on the road" would reveal.
"""
from __future__ import annotations

from opendbc.sunnypilot.car.ford.fordcan_ext import _ACCDATA_SIGNALS, create_acc_msg_passthrough


class _Packer:
  """Records what it was asked to pack. The DBC round-trip is not what is under test here."""

  def __init__(self):
    self.calls = []

  def make_can_msg(self, name, bus, values):
    self.calls.append((name, bus, dict(values)))
    return (390, b"\x00" * 8, bus)


class _CAN:
  main = 0
  camera = 2


def _stock(**over):
  vals = dict.fromkeys(_ACCDATA_SIGNALS, 0)
  vals.update(over)
  return vals


def _dbc_accdata_signals() -> set[str]:
  """Straight from the DBC. Comparing `_ACCDATA_SIGNALS` against ITSELF was the first version of
  this test, and deleting an entry changed both sides -- so it passed while the packer silently
  zeroed a brake request. Caught by mutation testing, not by reading it."""
  import os, re
  d = os.path.dirname(os.path.abspath(__file__))
  while d != os.path.dirname(d) and not os.path.isdir(os.path.join(d, "dbc")):
    d = os.path.dirname(d)
  names, inside = set(), False
  with open(os.path.join(d, "dbc", "ford_lincoln_base_pt.dbc"), encoding="utf-8", errors="replace") as f:
    for line in f:
      if line.startswith("BO_ "):
        inside = re.match(r"BO_ 390 ACCDATA\s*:", line) is not None
      elif inside and line.strip().startswith("SG_ "):
        names.add(line.strip().split()[1])
  assert names, "ACCDATA not found in the DBC -- did the message move?"
  return names


def test_every_dbc_signal_is_carried_and_none_is_invented():
  """A signal dropped here is silently zeroed on the wire -- no checksum would ever reveal it."""
  packer = _Packer()
  stock = _stock(AccBrkTot_A_Rq=-1.75, AccPrpl_A_Rq=0.5, AccVeh_V_Trg=104.0, Cmbb_B_Enbl=1)
  create_acc_msg_passthrough(packer, _CAN, stock)

  name, bus, sent = packer.calls[0]
  assert name == "ACCDATA"
  assert bus == _CAN.main, "the forwarded command has to go to the CAR, not back at the camera"
  assert set(_ACCDATA_SIGNALS) == _dbc_accdata_signals(), (
    "the forwarded signal list has drifted from the DBC -- a missing one ships as zero")
  assert set(sent) == _dbc_accdata_signals()
  assert sent == stock, "values must pass through untouched -- the whole point is Ford's own numbers"


def test_a_missing_signal_raises_rather_than_shipping_a_zero():
  """_ACCDATA_SIGNALS is an explicit list precisely so this is a KeyError and not a quiet -0.0."""
  packer = _Packer()
  incomplete = _stock()
  del incomplete["AccBrkTot_A_Rq"]
  try:
    create_acc_msg_passthrough(packer, _CAN, incomplete)
  except KeyError:
    return
  raise AssertionError("a missing brake request was packed as zero instead of raising")


def test_the_brake_request_is_carried_verbatim_including_hard_values():
  """No clamping here. Panda is the limit, and it was measured to never bind -- but if Ford ever
  does command harder, this must hand panda the REAL number rather than quietly softening it.
  A silent clamp would be indistinguishable from working, right up until it mattered."""
  packer = _Packer()
  create_acc_msg_passthrough(packer, _CAN, _stock(AccBrkTot_A_Rq=-5.0))
  assert packer.calls[0][2]["AccBrkTot_A_Rq"] == -5.0


# --- what the review found: panda, not Ford, is the tight constraint --------------------------
#
# `ford_tx_hook` does not clamp a value it dislikes, it DROPS THE WHOLE MESSAGE. So an inadmissible
# forwarded frame does not produce a slightly-wrong command; it makes a 50 Hz message vanish for as
# long as Ford holds that value and then reappear. Intermittent absence is worse than either
# controller, which is why `passthrough_admissible` is consulted before forwarding.
#
# The measurement this feature was built on covered AccBrkTot_A_Rq only. The gas band is four times
# narrower and was never measured at all.
from opendbc.sunnypilot.car.ford.fordcan_ext import passthrough_admissible


def test_the_gas_band_is_enforced_and_not_only_the_brake_cap():
  """FORD_LONG_LIMITS: gas is [-0.5, 2.0] with one legal escape at exactly -5.0, checked against
  BOTH AccPrpl_A_Rq and AccPrpl_A_Pred. A coasting Ford lives right in the gap between them."""
  assert not passthrough_admissible(_stock(AccPrpl_A_Rq=-0.2), True)
  assert not passthrough_admissible(_stock(AccPrpl_A_Rq=-5.0), True), "the inactive value is legal"

  assert passthrough_admissible(_stock(AccPrpl_A_Rq=-1.2), True), "a coasting request must be refused"
  assert passthrough_admissible(_stock(AccPrpl_A_Pred=-1.2), True), "the PREDICTED request is checked too"
  assert passthrough_admissible(_stock(AccPrpl_A_Rq=2.5), True)


def test_the_brake_cap_is_enforced_at_pandas_number_not_the_signals():
  """The signal can express -20. Panda stops at -3.4991 and drops the frame beyond it."""
  assert not passthrough_admissible(_stock(AccBrkTot_A_Rq=-2.70), True), "the measured worst case"
  assert passthrough_admissible(_stock(AccBrkTot_A_Rq=-5.0), True)


def test_cmbb_deny_is_refused_because_panda_blocks_it_outright():
  """ford.h does `violation |= cmbb_deny` unconditionally, and ford/carstate.py already reads that
  same bit as accFaulted -- so this is a state the camera really does enter on this car."""
  assert passthrough_admissible(_stock(CmbbDeny_B_Actl=1), True)


def test_nothing_is_forwarded_with_openpilot_longitudinal_inactive():
  """Two separate reasons, either sufficient. Panda passes only the inactive frame when
  get_longitudinal_allowed() is false, so Ford's real command would be dropped per-frame. And
  create_acc_msg clears Cmbb_B_Enbl when long_active is false -- that is how openpilot's own
  disengagement reaches the car, and forwarding would leave it asserted instead."""
  assert passthrough_admissible(_stock(), False)
  assert not passthrough_admissible(_stock(), True), "a benign frame must still pass when active"


# --- drive A, 2026-08-18: what the road said -----------------------------------------------------

def test_the_unpoliced_actuation_bits_are_refused():
  """Panda checks five fields in ACCDATA. It does not check these, and the first version of this
  function only asked "would panda allow it" -- which was never the same question as "do we
  understand it".

  `AccBrkPrkEl_B_Rq` is the one that made the point: four forwarded frames carried it and the car
  applied the park brake behind a stopped vehicle. `AccCancl_B_Rq` is the one that mattered more --
  the camera asserted it in 70.6% of its frames, so forwarding was relaying a CANCEL request for
  most of the drive."""
  for name in ("AccCancl_B_Rq", "AccDeny_B_Rq", "AccBrkPrkEl_B_Rq", "AccStopStat_B_Rq",
               "AccBrkPulse_B_Rq", "AccAutoResum_D_Rq"):
    assert passthrough_admissible(_stock(**{name: 1}), True), f"{name} was forwarded unchecked"


def test_the_predicted_gas_band_is_what_actually_binds():
  """The review said the gas band was never measured and was four times narrower than the brake cap.
  Drive A: `AccPrpl_A_Pred outside panda's band` is the dominant refusal reason in the log, sweeping
  -1.79 -> -1.29 while coasting. Every one of those frames would have been transmitted and then
  DROPPED by panda without this check."""
  for pred in (-1.79, -1.74, -1.69, -1.29):
    assert passthrough_admissible(_stock(AccPrpl_A_Pred=pred), True), \
      f"AccPrpl_A_Pred {pred} was admitted; panda would have dropped the whole frame"
