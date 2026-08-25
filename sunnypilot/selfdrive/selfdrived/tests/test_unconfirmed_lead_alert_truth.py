"""FusionPilot: the unconfirmed-lead alert must not promise a stop that is not coming.

REDUCED 2026-08-25, when the stock-ACC passthrough was deleted. This file used to assert that the
second line CHANGED with `accAuthority` -- "openpilot is slowing for it" while openpilot authored
ACCDATA, "Cruise will not stop for it" while Ford did. That distinction only existed because the
passthrough created a state where the two took turns, and `accAuthority` is gone with it.

What survives is the half that was always the point: with Ford's own ACC driving, a vision-only lead
is one its radar has not confirmed, and stock cruise genuinely will not stop for it. Saying anything
softer would be the alert lying about who is responsible -- which is what the original 2026-08-24
report was about.
"""
from types import SimpleNamespace as NS

from openpilot.sunnypilot.selfdrive.selfdrived.events import unconfirmed_lead_alert


class FakeSM:
  def __init__(self, alive=True):
    self.alive = {'controllerStateBP': alive}

  def __getitem__(self, k):
    # Only `longitudinalPlanSP.unconfirmedLead` is read; dRel feeds the distance in the text.
    return NS(unconfirmedLead=NS(dRel=30.0))


def _second_line(alive=True):
  a = unconfirmed_lead_alert(NS(), NS(), FakeSM(alive), False, 0, 0)
  return a.alert_text_2


def test_it_warns_that_cruise_will_not_stop():
  assert "Cruise will not stop for it" in _second_line()


def test_it_says_the_same_thing_when_the_message_is_absent():
  """`ignore_alive` means the struct can be missing. The wording must not depend on it -- an alert
  that goes quiet because a diagnostic message dropped is the failure mode, not a feature."""
  assert "Cruise will not stop for it" in _second_line(alive=False)
