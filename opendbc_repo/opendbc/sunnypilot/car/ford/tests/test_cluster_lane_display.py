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


def test_an_unseen_lane_is_NOT_the_suggestion_state():
  """SUPPRESS MEANS ONE THING.

  This used to send Suppress both for "the line opens toward the gap" and for "the model cannot see
  that line", which made them the same picture. On worn paint -- I-15 in the rain, a repaved
  stretch, a lane line that just stops -- the hint would appear on its own and mean nothing, and
  every time it appeared for real the driver had no way to tell which of the two it was.

  An unseen lane sends None, which is what upstream sends for it and what is actually true, leaving
  Suppress to carry the suggestion by itself. Note the second assertion: the suggestion is NOT
  drawn on a side whose line was never there to open.
  """
  assert lines(left_visible=False) == (NONE, AVAIL)
  assert lines(passing_side=LEFT, left_visible=False) == (SUPPRESS, AVAIL)
  assert lines(left_visible=False) != lines(passing_side=LEFT, left_visible=False)


def test_ldw_does_not_run_while_steering():
  """The claim this whole feature rests on, asserted against openpilot's own source.

  "Lane departures aren't going to happen at all when openpilot is on, so we can repurpose this
  display for an actual use." Correct, and structurally so: ldw.py gates on `not CC.latActive`, so
  departure is not merely unlikely while engaged -- it is not computed. That is what makes borrowing
  the display free rather than a trade.

  If upstream ever drops that condition, the two uses start competing for the same lines and this
  fails before anybody has to notice it on the road.
  """
  import pathlib
  root = pathlib.Path(__file__).resolve()
  while not (root / "common" / "params_keys.h").exists():
    root = root.parent
  src = (root / "selfdrive" / "controls" / "lib" / "ldw.py").read_text(encoding="utf-8")
  assert "not CC.latActive" in src, (
    "ldw.py no longer suppresses lane departure while steering -- the cluster display is now "
    "shared between departure warnings and passing suggestions, and one will hide the other")
