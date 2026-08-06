"""BluePilot: the cluster's lane display as the passing-assist instrument.

His idea: *"my LKA display just shows green on both sides of my car all the time, no matter what.
What if we hijacked this, and showed what this system is wanting to do on there?"* -- then, once it
was clear how much the signal could carry: *"this entire screen on my car can be reused for passing
assist or other features... I'm fine if we overhaul that screen to make it more useful."*

`LaActvStats_D_Dsply` is a five-by-five matrix, an independent state per side, from the DBC:

    value = left + 5 * right,  each of {None 0, Available 1, Suppress 2, Warning 3, Intervene 4}

plus LA_Off (30), which is whole-display and outside the matrix.

So each line becomes a four-level meter for its own side rather than a lane marker, and the pair
reads as a direction and an intensity at once. Nothing openpilot already said here was given up:
green still means the lane is held, a line still disappears when the model cannot see it, departure
still warns, and LA_Off -- which BluePilot had stopped sending entirely -- says nothing is running.
"""

from types import SimpleNamespace as NS

from opendbc.sunnypilot.car.ford import fordcan_ext
from opendbc.sunnypilot.car.ford.fordcan_ext import ClusterPassing


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
LA_OFF = 30


def pa(suggestion=0, maneuver_side=0, maneuver_moving=False, pass_in_play=None,
       oncoming_left=False, oncoming_right=False):
  # Defaults to whatever the other fields imply, so a test that is not about the gate does not have
  # to restate it -- and a test that IS about the gate says so explicitly.
  if pass_in_play is None:
    pass_in_play = bool(suggestion or maneuver_moving)
  return ClusterPassing(suggestion, maneuver_side, maneuver_moving, pass_in_play,
                        oncoming_left, oncoming_right)


def raw(passing=None, steering=True, main_on=True, left_depart=False, right_depart=False,
        left_visible=True, right_visible=True):
  packer = FakePacker()
  hud = NS(leftLaneDepart=left_depart, rightLaneDepart=right_depart,
           leftLaneVisible=left_visible, rightLaneVisible=right_visible)
  fordcan_ext.create_lkas_ui_msg(packer, NS(camera=2, main=0, radar=1), main_on, steering, 0, hud,
                                 dict(STOCK), passing)
  return packer.values["LaActvStats_D_Dsply"]


def lines(**kwargs):
  """(left state, right state). Only meaningful for values inside the 5x5 matrix."""
  v = raw(**kwargs)
  assert v <= 24, f"{v} is a whole-display value, not a per-side pair"
  return v % 5, v // 5


# --- what openpilot already said here, kept ------------------------------------------------------

def test_green_on_both_sides_means_openpilot_is_holding_the_lane():
  assert lines() == (AVAIL, AVAIL)


def test_a_line_the_model_cannot_see_is_not_drawn():
  """Worth keeping for its own sake: a lost lane line is exactly when lateral gets worse, and it is
  visible at a glance without reading anything."""
  assert lines(left_visible=False) == (NONE, AVAIL)
  assert lines(right_visible=False) == (AVAIL, NONE)
  assert raw(left_visible=False, right_visible=False) == 0


def test_green_is_not_shown_when_nothing_is_steering():
  """The bug this fixes is his own observation -- "green on both sides all the time, NO MATTER
  WHAT". BluePilot took main_on and enabled as arguments and used neither, so the lines went green
  whenever the model saw paint. Green that is on when nothing is steering cannot also mean
  openpilot has the wheel, which is the meaning everything else here is built on."""
  assert raw(steering=False, main_on=True) == 0
  assert raw(steering=False, main_on=False) == LA_OFF


def test_lane_assist_off_is_sent_again():
  """Upstream's value for nothing running. BluePilot never transmitted it at all."""
  assert raw(steering=False, main_on=False) == LA_OFF


def test_lane_departure_still_warns_when_openpilot_is_not_steering():
  assert raw(steering=False, main_on=False, left_depart=True) == 3
  assert raw(steering=False, main_on=False, right_depart=True) == 15


# --- what passing assist adds --------------------------------------------------------------------

def test_the_line_gives_way_on_the_side_it_wants():
  assert lines(passing=pa(suggestion=LEFT)) == (SUPPRESS, AVAIL)
  assert lines(passing=pa(suggestion=RIGHT)) == (AVAIL, SUPPRESS)


def test_the_line_goes_to_intervene_once_it_is_actually_going():
  """A suggestion and a committed lane change are different promises and must not look the same.
  Suppress is "you could"; Intervene is "the blinker is on and I am crossing"."""
  assert lines(passing=pa(maneuver_side=LEFT, maneuver_moving=True)) == (INTERVENE, AVAIL)
  assert lines(passing=pa(maneuver_side=RIGHT, maneuver_moving=True)) == (AVAIL, INTERVENE)


def test_a_maneuver_still_deciding_does_not_promise_a_lane_change():
  """maneuver_moving is false through `confirming` and `waiting`. A line that went to Intervene
  while the machine was only thinking about it would promise a change that may never come."""
  assert lines(passing=pa(maneuver_side=LEFT, maneuver_moving=False)) == (AVAIL, AVAIL)


def test_oncoming_traffic_is_the_one_thing_that_gets_to_shout():
  """Warning is the departure look. For a passing suggestion that would be crying wolf, which is
  why it was refused before. For opposing traffic in the lane we were about to move into, it is
  the literally correct thing to say."""
  assert lines(passing=pa(pass_in_play=True, oncoming_left=True)) == (WARN, AVAIL)
  assert lines(passing=pa(pass_in_play=True, oncoming_right=True)) == (AVAIL, WARN)


def test_oncoming_says_NOTHING_when_no_pass_is_in_play():
  """The gate that keeps this a warning instead of wallpaper.

  Oncoming traffic is not an event on a two-lane road, it is the road working normally. Warning
  about every car coming the other way would hold the left line yellow for most of a drive down
  US-6, and nobody reads a light that is always on. It fires when it is the ANSWER to a question
  the car was actually asking.
  """
  assert lines(passing=pa(pass_in_play=False, oncoming_left=True)) == (AVAIL, AVAIL)
  assert lines(passing=pa(pass_in_play=False, oncoming_right=True)) == (AVAIL, AVAIL)


def test_it_still_warns_while_oncoming_is_the_thing_refusing_the_pass():
  """The case that matters most and the one a naive gate would lose: oncoming BLOCKS the pass, so
  the suggestion goes back to none. If the warning needed a standing suggestion it would vanish at
  exactly the moment it became the answer. `waiting` keeps pass_in_play true."""
  assert lines(passing=pa(suggestion=0, pass_in_play=True, oncoming_left=True)) == (WARN, AVAIL)


def test_oncoming_outranks_both_the_suggestion_and_the_maneuver():
  assert lines(passing=pa(suggestion=LEFT, oncoming_left=True)) == (WARN, AVAIL)
  assert lines(passing=pa(maneuver_side=LEFT, maneuver_moving=True,
                          oncoming_left=True)) == (WARN, AVAIL)


def test_the_two_sides_are_independent():
  """The whole reason this display can carry more than one thing at a time."""
  assert lines(passing=pa(suggestion=LEFT, oncoming_right=True)) == (SUPPRESS, WARN)


def test_every_level_is_a_different_picture():
  """A meter whose steps look alike is not a meter. These are the four things one side can say."""
  side = {
    "normal": lines()[0],
    "wants to go": lines(passing=pa(suggestion=LEFT))[0],
    "going": lines(passing=pa(maneuver_side=LEFT, maneuver_moving=True))[0],
    "do not go": lines(passing=pa(pass_in_play=True, oncoming_left=True))[0],
    "cannot see it": lines(left_visible=False)[0],
  }
  assert len(set(side.values())) == len(side), side


def test_no_passing_state_at_all_is_the_plain_display():
  assert lines(passing=None) == (AVAIL, AVAIL)


def test_passing_assist_cannot_paint_anything_while_openpilot_is_not_steering():
  """It only suggests while engaged, so this is belt and braces -- but the not-steering branch is
  openpilot's own status and passing assist has no business overwriting it."""
  assert raw(passing=pa(suggestion=LEFT, oncoming_right=True),
             steering=False, main_on=False) == LA_OFF


def test_ldw_does_not_run_while_steering():
  """The claim this whole feature rests on, asserted against openpilot's own source.

  "Lane departures aren't going to happen at all when openpilot is on, so we can repurpose this
  display for an actual use." Correct, and structurally so: ldw.py gates on `not CC.latActive`, so
  departure is not merely unlikely while engaged -- it is not computed. That is what makes borrowing
  the display free rather than a trade, and it is why the departure branches live in the
  not-steering case and nowhere else.

  If upstream ever drops that condition, the two uses start competing for the same lines and this
  fails before anybody has to notice it on the road.
  """
  import pathlib
  root = pathlib.Path(__file__).resolve()
  while not (root / "common" / "params_keys.h").exists():
    root = root.parent
  src = (root / "selfdrive" / "controls" / "lib" / "ldw.py").read_text(encoding="utf-8")
  assert "not CC.latActive" in src, (
    "ldw.py no longer gates lane departure on `not CC.latActive`. Departure and passing assist can "
    "now both want the cluster's lane lines at once -- decide which wins before shipping this.")
