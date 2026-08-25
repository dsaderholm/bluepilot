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


def _dbc_signals_for(addr: int) -> set[str]:
  """Every signal the DBC defines for one message id."""
  import os, re
  d = os.path.dirname(os.path.abspath(__file__))
  while d != os.path.dirname(d) and not os.path.isdir(os.path.join(d, "dbc")):
    d = os.path.dirname(d)
  names, inside = set(), False
  with open(os.path.join(d, "dbc", "ford_lincoln_base_pt.dbc"), encoding="utf-8", errors="replace") as f:
    for line in f:
      if line.startswith("BO_ "):
        inside = re.match(rf"BO_ {addr} \w+\s*:", line) is not None
      elif inside and line.strip().startswith("SG_ "):
        names.add(line.strip().split()[1])
  assert names, f"no signals found for message {addr}"
  return names


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
  # Everything except the one advisory field panda will not carry. See create_acc_msg_passthrough.
  assert sent["AccPrpl_A_Pred"] == -5.0, "the predicted-accel field must be pinned to panda's escape"
  rest = {k: v for k, v in sent.items() if k != "AccPrpl_A_Pred"}
  assert rest == {k: v for k, v in stock.items() if k != "AccPrpl_A_Pred"}, (
    "values must pass through untouched -- the whole point is Ford's own numbers")


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

  # THE BOTTOM IS NO LONGER A REFUSAL EITHER, 2026-08-24, and the reversal is measured rather than
  # argued. See test_the_bottom_of_the_gas_band_is_clamped_not_refused below.
  assert not passthrough_admissible(_stock(AccPrpl_A_Rq=-1.2), True),     "a coasting request is clamped on the way out now, not refused"

  # THE TOP IS NO LONGER A REFUSAL -- it is CLAMPED on the way out, 2026-08-19. Ford's ordinary
  # launch propulsion is 2.0 m/s^2, which is exactly panda's ceiling, so refusing there threw the
  # whole frame away on every pull-away: 624 of 994 fallback frames on route 00000393 were under
  # 15 mph, all of them a smear of 2.00-2.09. See test_the_launch_clamp_keeps_fords_frame below.
  assert not passthrough_admissible(_stock(AccPrpl_A_Rq=2.5), True), \
    "the top is clamped by the builder, not refused -- refusing it costs every launch"
  # AccPrpl_A_Pred is NOT refused -- it is pinned on the way out, so Ford's value never reaches
  # panda. Drive A: refusing on it cost 9.6% of engaged frames for an advisory field.
  assert not passthrough_admissible(_stock(AccPrpl_A_Pred=-1.2), True)


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
  for name in ("AccCancl_B_Rq", "AccDeny_B_Rq", "AccBrkPrkEl_B_Rq",
               "AccBrkPulse_B_Rq", "AccAutoResum_D_Rq"):
    assert passthrough_admissible(_stock(**{name: 1}), True), f"{name} was forwarded unchecked"


def test_the_cancel_refusal_is_dropped_only_when_asked_and_only_for_itself():
  """Recovery from a cancel WE provoked, 2026-08-22, routes a8/a9/aa.

  The override takes authority, the camera watches the car brake harder than it asked, and ~1.6 s
  later it cancels. That much is inherent -- a stop is 5-8 s of contradiction and the camera
  tolerates about 1.5. What was NOT inherent is that the cancel never released: it made every
  subsequent frame inadmissible, so Ford's command stopped reaching the car, so the camera could
  never observe the car obeying it again. He lost Ford ACC for the rest of two drives and had to
  restart the ignition both times.

  `allow_cancel` exists for that one case. It must drop the cancel refusal and NOTHING else -- the
  deny bits are a different statement ("I will not let ACC run", not "stop the ACC that is
  running"), and the panda bands are not ours to relax at all."""
  assert passthrough_admissible(_stock(AccCancl_B_Rq=1), True), "cancel is refused by default"
  assert not passthrough_admissible(_stock(AccCancl_B_Rq=1), True, allow_cancel=True), (
    "the recovery path could not forward a cancelling frame, so the latch stays permanent")

  # Everything else on the unpoliced list stays refused even in recovery.
  for name in ("AccDeny_B_Rq", "AccBrkPrkEl_B_Rq", "AccBrkPulse_B_Rq", "AccAutoResum_D_Rq"):
    assert passthrough_admissible(_stock(AccCancl_B_Rq=1, **{name: 1}), True, allow_cancel=True), \
      f"{name} was forwarded during cancel recovery -- allow_cancel must drop ONE bit, not the list"

  # And the bands still bind, so recovery cannot put a frame panda would drop onto the wire.
  assert passthrough_admissible(_stock(AccCancl_B_Rq=1, AccBrkTot_A_Rq=-19.0), True,
                                allow_cancel=True), "recovery skipped the brake band"
  # THERE IS NO LONGER A GAS-BAND REFUSAL TO TEST HERE, and that is worth stating rather than
  # replacing with a fake one. The top became a clamp on 2026-08-19 (Ford's launch propulsion IS
  # panda's ceiling) and the bottom on 2026-08-24 (the fallback lurched to -1.16 while Ford asked
  # -0.13). So `AccBrkTot_A_Rq` above is the only band that still refuses -- which is correct: it
  # is the brake, and under-braking is the one direction no measurement excuses.


def test_clearing_the_cancel_changes_that_bit_and_nothing_else():
  """The forwarded frame is Ford's, minus one bit. If this ever zeroed more than the cancel it would
  be authoring a command Ford never sent, mid-recovery, with no checksum to reveal it."""
  packer = _Packer()
  stock = _stock(AccBrkTot_A_Rq=-0.9, AccPrpl_A_Rq=0.4, AccVeh_V_Trg=56.0,
                 Cmbb_B_Enbl=1, AccCancl_B_Rq=1, AccResumEnbl_B_Rq=1)

  create_acc_msg_passthrough(packer, _CAN, stock)
  _, _, plain = packer.calls[-1]
  assert plain["AccCancl_B_Rq"] == 1, "the default path must forward Ford's frame untouched"

  create_acc_msg_passthrough(packer, _CAN, stock, clear_cancel=True)
  _, _, cleared = packer.calls[-1]
  assert cleared["AccCancl_B_Rq"] == 0
  assert {k: v for k, v in cleared.items() if k != "AccCancl_B_Rq"} == \
         {k: v for k, v in plain.items() if k != "AccCancl_B_Rq"}, \
    "clearing the cancel disturbed another signal"


def test_the_stop_hold_status_is_forwarded_because_ford_needs_it():
  """`AccStopStat_B_Rq` was on the list above and should not have been.

  It went on by association with the park brake, which implicated `AccBrkPrkEl_B_Rq` -- still
  refused above. Measured on 2026-08-18: this bit is asserted on 330 frames of route 388 and 10.5%
  of route 389, and never co-occurs with `carState.parkingBrake` on any frame of either. It is
  ordinary stop-hold traffic, appearing only now because route 389 is the first time this car has
  ever held a stop under ACC.

  Refusing it handed every stop-in-traffic to openpilot longitudinal -- the one case where Ford's
  stop-and-go is most valuable and openpilot's is least trusted. He saw it from the seat: the OP
  LONG pill coming on while stopped behind a car, "where Ford ACC does just fine".

  So this asserts the OPPOSITE of the test above, deliberately and by name, so that putting it back
  on the list is a decision someone has to make against this evidence rather than a tidy-up."""
  assert not passthrough_admissible(_stock(AccStopStat_B_Rq=1), True), (
    "AccStopStat_B_Rq is refused again -- that hands every stop behind a car to openpilot "
    "longitudinal, which is the controller this whole feature exists to avoid")


def test_the_predicted_accel_is_pinned_rather_than_costing_the_frame():
  """The review was right that the gas band was never measured, and drive A showed Ford sweeping
  `AccPrpl_A_Pred` through -1.79 -> -1.29 while coasting: 25.2% of engaged frames.

  But REFUSING the whole frame over it was the wrong response, and cost 9.6% of engaged frames on
  its own. It is the PREDICTED accel -- a feed-forward hint, not a command -- and upstream openpilot
  hardcodes it to exactly -5.0, which is what this car's PCM sees on every normal op-long frame
  anyway. Pinning it keeps the frame and costs the PCM a hint it already lives without."""
  packer = _Packer()
  for pred in (-1.79, -1.74, -1.29):
    assert not passthrough_admissible(_stock(AccPrpl_A_Pred=pred), True), \
      f"AccPrpl_A_Pred {pred} cost the whole frame instead of being pinned"
  create_acc_msg_passthrough(packer, _CAN, _stock(AccPrpl_A_Pred=-1.79, AccBrkTot_A_Rq=-2.1))
  sent = packer.calls[0][2]
  assert sent["AccPrpl_A_Pred"] == -5.0
  assert sent["AccBrkTot_A_Rq"] == -2.1, "pinning one field must not disturb the real command"


def test_the_launch_clamp_keeps_fords_frame():
  """HIS "IT SWITCHED TO OP LONG AND WENT RIDICULOUSLY SLOW", 2026-08-19.

  Route 00000393: 994 frames where the passthrough fell back, 624 of them UNDER 15 MPH, and the
  reason is a smear sitting just over panda's ceiling --

      AccPrpl_A_Rq  2.000  2.020  2.030  2.050  2.060  2.070  2.080  2.090 ...

  Ford's ordinary pull-away propulsion IS 2.0 m/s^2, which is exactly _PANDA_GAS_MAX, and the 0.005
  margin puts even a clean 2.000 outside. So essentially every launch was refused, and openpilot --
  whose launch is far gentler -- authored it instead. Nothing ever CHOSE openpilot for launches;
  this refusal was the whole of it.
  """
  packer = _Packer()
  for gas in (2.0, 2.03, 2.07, 2.5):
    packer.calls.clear()
    create_acc_msg_passthrough(packer, _CAN, _stock(AccPrpl_A_Rq=gas, AccBrkTot_A_Rq=-1.1))
    sent = packer.calls[0][2]
    assert sent["AccPrpl_A_Rq"] <= 2.0 - 0.005 + 1e-9, f"{gas} went out unclamped"
    assert sent["AccPrpl_A_Rq"] == 2.0 - 0.005, f"{gas} was not clamped to panda's ceiling"
    assert sent["AccBrkTot_A_Rq"] == -1.1, "clamping the gas must not disturb the brake command"


def test_the_launch_clamp_leaves_ordinary_propulsion_alone():
  """It must only bind at the ceiling. A normal cruise request has to pass through untouched, or
  every frame is being rewritten to the limit."""
  packer = _Packer()
  for gas in (-0.4, 0.0, 1.0, 1.9):
    packer.calls.clear()
    create_acc_msg_passthrough(packer, _CAN, _stock(AccPrpl_A_Rq=gas))
    assert packer.calls[0][2]["AccPrpl_A_Rq"] == gas, f"{gas} was rewritten and should not have been"


def test_the_inactive_escape_value_is_never_clamped():
  """-5.0 is panda's one legal escape and is BELOW the band by construction. Clamping it to the
  ceiling would turn 'no propulsion request' into 'maximum propulsion request'."""
  packer = _Packer()
  create_acc_msg_passthrough(packer, _CAN, _stock(AccPrpl_A_Rq=-5.0))
  assert packer.calls[0][2]["AccPrpl_A_Rq"] == -5.0


def test_the_bottom_of_the_gas_band_is_clamped_not_refused():
  """REVERSED 2026-08-24, and only because the old reasoning was finally measured.

  This test used to assert the opposite, on the argument that clamping UP from -0.77 to -0.495 asks
  for less ENGINE BRAKING than Ford wanted, so the low side should fall back "to a controller we
  already ship" instead. That argument assumed the fallback brakes at least as hard as Ford.

  It does not. Route 000003bc, t+103.59..103.87 -- one of ELEVEN such refusals on that drive:

      FORD wanted brake   -0.11 .. -0.13 m/s^2 throughout
      WE actually sent    -0.18, -0.25, -0.60, -1.09, -1.16   then snapped back to -0.13

  0.28 s of up to nine times Ford's braking, because openpilot's longitudinal controller has been
  watching the car ignore it and arrives already wound up. So the real trade is 0.125 m/s^2 of
  POWERTRAIN braking -- Ford's own brake command still goes out untouched on the same frame --
  against a 1 m/s^2 lurch, fifteen times across two drives.

  `AccBrkTot_A_Rq` is a different matter and is still refused at the bottom: that field IS the
  brake, and asking for less of it than Ford wanted is the one direction no measurement excuses.
  """
  assert not passthrough_admissible(_stock(AccPrpl_A_Rq=-0.77), True),     "the low side still falls back instead of being clamped through"
  packer = _Packer()
  create_acc_msg_passthrough(packer, _CAN, _stock(AccPrpl_A_Rq=-0.77))
  sent = packer.calls[0][2]["AccPrpl_A_Rq"]
  assert sent >= -0.5, f"sent {sent}, still below panda's floor -- the frame would be dropped"
  assert sent < 0.0, f"sent {sent}; clamping must not turn engine braking into propulsion"


def test_the_brake_bottom_is_still_refused_and_must_stay_that_way():
  """The clamp above must never be extended to AccBrkTot_A_Rq. Under-braking is the one direction
  that no lurch argument excuses."""
  assert passthrough_admissible(_stock(AccBrkTot_A_Rq=-19.0), True),     "the brake bottom was made clampable -- that asks for less braking than Ford wanted"


def test_the_inactive_gas_sentinel_is_never_clamped():
  """-5.0 means 'not requesting'. Dragging it to -0.495 would be a real deceleration request."""
  packer = _Packer()
  create_acc_msg_passthrough(packer, _CAN, _stock(AccPrpl_A_Rq=-5.0))
  assert packer.calls[0][2]["AccPrpl_A_Rq"] == -5.0


# --- the dash must show the gap that is actually driving the car -------------------------------

def test_the_dash_shows_fords_gap_when_ford_owns_it():
  """Drive B, 2026-08-18: seven physical gap presses, seven camera gap changes at the same
  timestamps, the camera cycling 4-3-2-1 through Ford's five settings -- while the dash drew 3-2-1,
  openpilot's three personalities on a five-state indicator.

  His button was working the whole time and the display was showing something else, which is why it
  read as an aggressiveness control that did nothing. Under the passthrough Ford owns the gap, so
  Ford's number is what belongs on the dash."""
  from opendbc.sunnypilot.car.ford.fordcan_ext import create_acc_ui_msg

  class _CP:
    openpilotLongitudinalControl = True

  class _Hud:
    leadDistanceBars = 2      # openpilot's personality
    leadVisible = True
    leftLaneDepart = False
    rightLaneDepart = False
    leftLaneVisible = True
    rightLaneVisible = True
    visualAlert = 0

  # Every ACCDATA_3 signal, from the DBC -- which is what a real CANParser `vl` dict holds. Listing
  # a subset by hand just fails one KeyError at a time as the passthrough list grows.
  stock = dict.fromkeys(_dbc_signals_for(394), 0)
  stock["AccTGap_D_Dsply"] = 4   # Ford's actual setting

  def draw(gap_is_fords):
    packer = _Packer()
    create_acc_ui_msg(packer, _CAN, _CP(), True, True, False, False, _Hud(), stock,
                      False, True, True, 0, 0, gap_is_fords=gap_is_fords)
    return packer.calls[0][2]["AccTGap_D_Dsply"]

  assert draw(True) == 4, "the dash drew openpilot's personality while Ford was deciding the gap"
  assert draw(False) == 2, "without the passthrough openpilot IS the follow controller; show its own"


def test_the_accel_ceiling_is_clamped_not_refused():
  """HIS "THE SPEED WENT UP AND DOWN", 2026-08-23, and it is the launch bug on a second field.

  Straight off his swaglog, every line one refused frame handed to openpilot --

      AccBrkTot_A_Rq  1.996  2.019  2.043  2.066  2.090  2.105  2.125  2.140

  `_PANDA_ACCEL_MAX` is 1.9999, so with the margin anything from 1.995 up was thrown away. Despite
  the name this is Ford's TOTAL acceleration request, positive while accelerating, and Ford sits on
  that ceiling pulling away exactly as it sits on the gas ceiling.

  The fix is the same asymmetry as the gas clamp: DOWN is conservative, so the top is clamped; UP
  would ask for less braking, so the bottom stays a refusal.
  """
  packer = _Packer()
  for accel in (1.996, 2.043, 2.140, 3.0):
    packer.calls.clear()
    create_acc_msg_passthrough(packer, _CAN, _stock(AccBrkTot_A_Rq=accel, AccPrpl_A_Rq=0.5))
    sent = packer.calls[0][2]
    assert sent["AccBrkTot_A_Rq"] == 1.9999 - 0.005, f"{accel} was not clamped to panda's ceiling"
    assert sent["AccPrpl_A_Rq"] == 0.5, "clamping the accel must not disturb the gas command"
  # And the frame is now ADMISSIBLE at those values -- refusing them is what cost the pull-aways.
  for accel in (1.996, 2.043, 2.140):
    assert not passthrough_admissible(_stock(AccBrkTot_A_Rq=accel), True), \
      f"{accel} must be clamped by the builder, not refused"


def test_the_accel_floor_is_still_a_refusal():
  """The braking side must NOT be clamped. Raising -3.6 to -3.494 asks for LESS braking than Ford
  wanted, and a silent softening of a brake command is indistinguishable from working until the
  moment it matters. This is the half the 'carried verbatim' note was always right about."""
  assert passthrough_admissible(_stock(AccBrkTot_A_Rq=-3.6), True), \
    "below panda's floor must fall back, never be clamped up"
  packer = _Packer()
  create_acc_msg_passthrough(packer, _CAN, _stock(AccBrkTot_A_Rq=-2.70))
  assert packer.calls[0][2]["AccBrkTot_A_Rq"] == -2.70, "the measured worst case must go out intact"


def test_ordinary_accel_requests_are_left_alone():
  """It must bind only at the ceiling, or every frame is being rewritten to the limit."""
  packer = _Packer()
  for accel in (-2.70, -1.1, 0.0, 1.0, 1.9):
    packer.calls.clear()
    create_acc_msg_passthrough(packer, _CAN, _stock(AccBrkTot_A_Rq=accel))
    assert packer.calls[0][2]["AccBrkTot_A_Rq"] == accel, f"{accel} was rewritten and should not have been"
