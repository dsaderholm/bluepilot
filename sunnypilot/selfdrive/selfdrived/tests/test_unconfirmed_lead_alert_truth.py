"""FusionPilot: the unconfirmed-lead alert must not promise something that is not true.

Route 000003ba, 2026-08-24: the alert appeared on 462 frames and 278 of them -- 60% -- were under
`fallback`, where the camera has cancelled and openpilot longitudinal is authoring the ACC command.
Its second line said "Cruise will not stop for it". openpilot does stop for it.

He caught it from the seat: "it kept saying vehicle ahead radar has not confirmed it even when Ford
ACC canceled a long time ago and OP long took over".
"""
from types import SimpleNamespace as NS

import pytest

from openpilot.sunnypilot.selfdrive.selfdrived.events import unconfirmed_lead_alert

# SPELLED OUT, NOT IMPORTED FROM THE MODULE UNDER TEST. Parametrising on
# `_OPENPILOT_DRIVING_AUTHORITIES` meant deleting an entry from it deleted the test case that would
# have caught the deletion -- a mutation dropping "inert" passed the whole file.
OPENPILOT_DRIVES = ("fallback", "inert", "opStop", "openpilot", "recovery")
FORD_DRIVES = ("stock", "ford")


class FakeSM:
  def __init__(self, authority, alive=True):
    self._d = {
      'longitudinalPlanSP': NS(unconfirmedLead=NS(dRel=30.0)),
      'controllerStateBP': NS(accAuthority=authority),
    }
    self.alive = {'controllerStateBP': alive, 'longitudinalPlanSP': True}

  def __getitem__(self, k):
    return self._d[k]


def _second_line(authority, alive=True):
  a = unconfirmed_lead_alert(NS(), NS(), FakeSM(authority, alive), False, 0, 0)
  return a.alert_text_2


@pytest.mark.parametrize("authority", OPENPILOT_DRIVES)
def test_it_does_not_deny_stopping_when_openpilot_drives(authority):
  """THE REPORTED BUG -- 278 of 462 frames on route 000003ba."""
  line = _second_line(authority)
  assert "will not stop" not in line, (
    f"authority={authority}: openpilot is driving and the alert still says {line!r}")
  assert "openpilot is slowing" in line


@pytest.mark.parametrize("authority", FORD_DRIVES)
def test_it_still_warns_when_ford_is_driving(authority):
  """The original meaning must survive: under passthrough, Ford really will not stop."""
  line = _second_line(authority)
  assert "Cruise will not stop for it" in line, f"authority={authority}: got {line!r}"


def test_the_distance_survives_both_wordings():
  """The distance is the useful part and must not be lost by the branch."""
  for authority in ("ford", "fallback"):
    assert "98 ft" in _second_line(authority), _second_line(authority)


def test_an_absent_authority_assumes_ford_is_driving():
  """Conservative default: absent means warn, not reassure."""
  assert "Cruise will not stop for it" in _second_line("fallback", alive=False)


def test_the_first_line_is_unchanged_either_way():
  """A vision-only lead is worth naming regardless of who stops for it."""
  for authority in ("ford", "fallback"):
    a = unconfirmed_lead_alert(NS(), NS(), FakeSM(authority), False, 0, 0)
    assert a.alert_text_1 == "Vehicle ahead - radar has not confirmed it"
