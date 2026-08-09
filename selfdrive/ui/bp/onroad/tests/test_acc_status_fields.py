"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FusionPilot: the capnp field reads behind the onroad ACC/ICBM readout.

Written against a crash loop on the car. The readout did `int(icbm.sendButton)`, which works fine
on the enum CONSTANTS every other test uses -- custom.IntelligentCruiseButtonManagement.
SendButtonState.increase is a plain int -- but a field read off a LIVE message is a
capnp _DynamicEnum, and int() rejects it with TypeError. Every ICBM test passed; the UI died on
the first frame with the car on, and took the display with it.

So these tests build real messages and read fields off them, which is the only way to see the
difference. They deliberately do not import the renderer: it pulls in raylib and a GPU context,
and the bug was never in the drawing.
"""

import pytest

from cereal import custom, messaging


def icbm_of(msg):
  return msg.selfdriveStateSP.intelligentCruiseButtonManagement


class TestLiveEnumReads:
  def test_int_on_a_live_enum_raises(self):
    """Pin the trap itself, so nobody reintroduces int() here."""
    msg = messaging.new_message('selfdriveStateSP')
    with pytest.raises(TypeError):
      int(icbm_of(msg).sendButton)

  def test_raw_is_the_accessor_that_works(self):
    msg = messaging.new_message('selfdriveStateSP')
    icbm = icbm_of(msg)
    assert icbm.sendButton.raw == 0
    assert icbm.overrideState.raw == 0

  @pytest.mark.parametrize("name,expected", [("none", 0), ("increase", 1), ("decrease", 2)])
  def test_send_button_raw_values_match_the_arrow_map(self, name, expected):
    """The readout maps 1 -> '+' and 2 -> '-'. If the schema order ever changes, catch it here."""
    msg = messaging.new_message('selfdriveStateSP')
    icbm_of(msg).sendButton = name
    assert icbm_of(msg).sendButton.raw == expected

  @pytest.mark.parametrize("name,expected", [("auto", 0), ("manual", 1)])
  def test_override_state_raw_values(self, name, expected):
    msg = messaging.new_message('selfdriveStateSP')
    icbm_of(msg).overrideState = name
    assert icbm_of(msg).overrideState.raw == expected

  def test_baseline_round_trips(self):
    msg = messaging.new_message('selfdriveStateSP')
    icbm_of(msg).vBaseline = 58.0
    assert round(icbm_of(msg).vBaseline) == 58


class TestBrakeLightStatusFields:
  """The ACC side. These are Bool/Float32, so they have never had the enum problem -- but the
  readout cannot tell accelerating from coasting without accPropulsionRequest, so pin it."""

  def test_all_fields_the_readout_uses_exist(self):
    msg = messaging.new_message('carStateBP')
    bls = msg.carStateBP.brakeLightStatus
    for f in ("dataAvailable", "brakeLightsOn", "accDataAvailable", "accDecelRequest",
              "accPrechargeRequest", "accAccelRequest", "accPropulsionRequest"):
      assert hasattr(bls, f), f"carStateBP.brakeLightStatus is missing {f}"

  def test_propulsion_and_brake_are_separate_fields(self):
    """accAccelRequest is AccBrkTot_A_Rq -- the BRAKE total, despite the name."""
    msg = messaging.new_message('carStateBP')
    bls = msg.carStateBP.brakeLightStatus
    bls.accPropulsionRequest = 0.8
    bls.accAccelRequest = -1.2
    assert bls.accPropulsionRequest > 0 and bls.accAccelRequest < 0
