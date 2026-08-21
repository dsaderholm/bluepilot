"""FusionPilot: the cruise buttons must mean what the wheel says they mean.

This wheel is CNCL / RES+ / SET-, with a dedicated CNCL. RES+ and SET- each change meaning with
cruise state, which is the whole reason the mapping is easy to get wrong -- and it was wrong:
CcAslButtnSetIncPress, the RES+ signal, emitted setCruise when cruise was off. Every resume was
reported to openpilot as a set, and setCruise is the one event that discards the driver's hold.

Pure data assertions on the button table. The dispatch that consumes it needs a CAN parser and
cannot run offline, so this guards the half that can.
"""
from opendbc.car import structs
from opendbc.sunnypilot.car.ford.values_ext import BUTTONS

ButtonType = structs.CarState.ButtonEvent.Type


def types_for(signal: str) -> set:
  return {b.event_type for b in BUTTONS if b.can_msg == signal}


class TestWheelLabelsMatchTheEvents:
  def test_res_plus_resumes_and_increases(self):
    """RES + : resume when off, increase when engaged. It must NOT offer setCruise -- that is what
    threw the hold away on every resume."""
    t = types_for("CcAslButtnSetIncPress")
    assert ButtonType.resumeCruise in t
    assert ButtonType.accelCruise in t
    assert ButtonType.setCruise not in t, "RES+ must never report a set"

  def test_set_minus_sets_and_decreases(self):
    t = types_for("CcAslButtnSetDecPress")
    assert ButtonType.setCruise in t
    assert ButtonType.decelCruise in t
    assert ButtonType.resumeCruise not in t, "SET- must never report a resume"

  def test_cancel_cancels(self):
    assert ButtonType.cancel in types_for("CcAslButtnCnclResPress")

  def test_set_is_still_reachable(self):
    """Handing the speed back to speed limit assist is SET, and it has to stay possible from
    somewhere -- it is the only way out of a hold that does not need a disengage."""
    assert any(ButtonType.setCruise in types_for(b.can_msg) for b in BUTTONS)

  def test_resume_is_reachable(self):
    assert any(ButtonType.resumeCruise in types_for(b.can_msg) for b in BUTTONS)

  def test_every_button_reads_a_real_ford_signal(self):
    """A typo here is silent: the signal never fires and the button simply does nothing.

    AND SO IS A REAL SIGNAL THE WHEEL NEVER SENDS, which this list could not catch and which cost
    weeks. `CcAslButtnSetIncPress` is a real Ford signal, spelled correctly, present in the DBC --
    and measured on 2026-08-20 to have ZERO driver-side rising edges across two full drives, while
    `CcAslButtnResIncPress` had six on each. His `+` button was invisible to openpilot the whole
    time. A whitelist of plausible names cannot tell "this signal exists" from "this car sends it";
    only the wire can.
    """
    known = {"CcAslButtnSetIncPress", "CcAslButtnResIncPress",
             "CcAslButtnSetDecPress", "CcAslButtnCnclResPress",
             "CcButtnOnOffPress"}
    for b in BUTTONS:
      assert b.can_msg in known, f"{b.can_msg} is not a signal this car's mapping expects"
      assert b.can_addr == "Steering_Data_FD1"
