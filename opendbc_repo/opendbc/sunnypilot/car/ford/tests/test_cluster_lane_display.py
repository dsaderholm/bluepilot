"""BluePilot: the cluster's lane lines as a passing-assist indicator.

His idea: *"my LKA display just shows green on both sides of my car all the time, no matter what.
What if we hijacked this, and showed what this system is wanting to do on there?"*

The signal turns out to be far richer than the green/not-green it gets used for.
`LaActvStats_D_Dsply` is a five-by-five matrix -- an independent state per side, from the DBC:

    value = left + 5 * right,  each of {None 0, Available 1, Suppress 2, Warning 3, Intervene 4}

So the line on the side it wants can OPEN while the other stays put. Suppress rather than Warning
deliberately: Warning is what lane departure looks like, and a display that cries wolf about
drifting when it means "I would like to pass" is worse than no display at all.
"""

from types import SimpleNamespace as NS

from opendbc.sunnypilot.car.ford import fordcan_ext


class FakePacker:
  def __init__(self):
    self.values = None

  def make_can_msg(self, name, bus, values):
    self.values = values
    return (0, b"", bus)


STOCK = dict.fromkeys([
  "FeatConfigIpmaActl", "FeatNoIpmaActl", "PersIndexIpma_D_Actl", "AhbcRampingV_D_Rq",
  "LaDenyStats_B_Dsply", "CamraDefog_B_Req", "CamraStats_D_Dsply", "DasAlrtLvl_D_Dsply",
  "DasStats_D_Dsply", "DasWarn_D_Dsply", "AhbHiBeam_D_Rq", "Passthru_63", "Passthru_48",
], 0)

LEFT, RIGHT = 1, 2
NONE, AVAIL, SUPPRESS, WARN, INTERVENE = 0, 1, 2, 3, 4


def lines(passing_side=0, left_depart=False, right_depart=False,
          left_visible=True, right_visible=True):
  packer = FakePacker()
  hud = NS(leftLaneDepart=left_depart, rightLaneDepart=right_depart,
           leftLaneVisible=left_visible, rightLaneVisible=right_visible)
  fordcan_ext.create_lkas_ui_msg(packer, NS(camera=2, main=0, radar=1), True, True, 0, hud,
                                 dict(STOCK), passing_side)
  v = packer.values["LaActvStats_D_Dsply"]
  return v % 5, v // 5          # (left state, right state)


def test_both_lines_are_normal_when_nothing_is_suggested():
  assert lines() == (AVAIL, AVAIL)


def test_the_line_opens_on_the_side_it_wants():
  assert lines(passing_side=LEFT) == (SUPPRESS, AVAIL)
  assert lines(passing_side=RIGHT) == (AVAIL, SUPPRESS)


def test_it_is_never_the_WARNING_state():
  """Warning is what lane departure looks like. Borrowing it would make the cluster cry wolf about
  drifting when it means "I would like to pass", which is worse than showing nothing."""
  for side in (LEFT, RIGHT):
    left, right = lines(passing_side=side)
    assert WARN not in (left, right)


def test_lane_departure_always_wins():
  """If the car is genuinely leaving its lane, nothing about a passing suggestion may hide it."""
  assert lines(passing_side=LEFT, left_depart=True) == (INTERVENE, AVAIL)
  assert lines(passing_side=RIGHT, right_depart=True) == (AVAIL, INTERVENE)


def test_a_departure_on_the_OTHER_side_still_shows_both():
  assert lines(passing_side=LEFT, right_depart=True) == (SUPPRESS, INTERVENE)


def test_an_invisible_lane_line_is_unchanged_by_any_of_this():
  assert lines(left_visible=False) == (SUPPRESS, AVAIL)
  assert lines(passing_side=LEFT, left_visible=False) == (SUPPRESS, AVAIL)
