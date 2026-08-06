"""BluePilot: the standstill walk that reads the cluster's lane-display vocabulary off the car.

Exactly ONE of `LaActvStats_D_Dsply`'s states has provably rendered on this cluster: Available, the
always-green line. Upstream's Intervene branch sits inside `if enabled:` and ldw.py gates departure
on `not CC.latActive`, so upstream can never reach it; its Warning and LA_Off values live in the
branch for cruise main off. BluePilot sends the engaged-style values unconditionally, so it reaches
none of those either. The passing hint is built on Suppress, which nothing has ever sent. This
walks them all so they can be named instead of guessed at.
"""

from types import SimpleNamespace as NS

import pytest

from opendbc.sunnypilot.car.ford import fordcan_ext
from opendbc.sunnypilot.car.ford.lane_display_test_ext import (
  LANE_TEST_BLOCK_MOVING, LANE_TEST_BLOCK_NONE, LANE_TEST_IDLE, LANE_TEST_STEPS, LANE_TEST_STEP_S,
  LaneDisplayTestExt, LaneTestOverride,
)

STEP_FRAMES = int(round(LANE_TEST_STEP_S / 0.01))


class FakeParams:
  """The request is an INT key. Writing a str to one raises in the real Params -- that exact bug
  made the blinker button silently do nothing -- so this refuses strings too."""

  def __init__(self, value=0):
    self.store = {"FordLaneDisplayTest": value}
    self.blocking_writes = []

  def get(self, key, return_default=False):
    return self.store.get(key, 0)

  def put(self, key, value, block=False):
    if not isinstance(value, int):
      raise TypeError(f"{key} is an INT key, got {type(value).__name__}")
    self.store[key] = value
    if block:
      self.blocking_writes.append(key)


def make(requested=0):
  ext = LaneDisplayTestExt.__new__(LaneDisplayTestExt)
  LaneDisplayTestExt.__init__(ext)
  ext.ldt_params = FakeParams(requested)
  return ext


def cs(standstill=True):
  return NS(out=NS(standstill=standstill))


def run(ext, frames, standstill=True):
  """Advance the walk and return every override it produced, in order."""
  return [ext.update_lane_display_test(cs(standstill)) for _ in range(frames)]


def test_idle_returns_nothing_and_leaves_the_display_alone():
  ext = make()
  assert run(ext, 50) == [None] * 50
  assert ext.ldt_step == LANE_TEST_IDLE


def test_it_walks_every_state_in_order():
  """The point of the whole exercise: all five looks get sent, each exactly once."""
  ext = make(requested=1)
  seen = [o for o in run(ext, STEP_FRAMES * len(LANE_TEST_STEPS) + 5) if o is not None]
  ordered = []
  for o in seen:
    if not ordered or ordered[-1] != o:
      ordered.append(o)
  assert ordered == [(lines, hands) for lines, hands, _ in LANE_TEST_STEPS]


def test_the_first_step_is_the_known_baseline():
  """Both sides Available -- the one look we know is green, because it is what gets sent for a
  visible lane today. If step 1 draws nothing, the finding is that the cluster will not render this
  at a standstill, and the walk says so about itself rather than returning blanks."""
  assert LANE_TEST_STEPS[0][:2] == (1 + 5 * 1, 0)


def test_the_right_line_stays_green_for_every_per_side_step():
  """Each of those is a side-by-side against a known-good line. A walk where both sides changed
  would give pictures with nothing to compare them to. LA_Off is whole-display and exempt."""
  for value, _, label in LANE_TEST_STEPS:
    if value > 24:
      continue
    assert value // 5 == 1, label


def test_each_per_side_state_is_visited_exactly_once():
  assert sorted({v % 5 for v, _, _ in LANE_TEST_STEPS if v <= 24}) == [0, 1, 2, 3, 4]


def test_it_also_asks_what_the_HANDS_indicator_does():
  """LaHandsOff_D_Dsply is the only other signal in IPMA_Data this fork authors rather than passes
  through, and BluePilot drives it 0 or 1 and nothing else. Level2 is "with chime" per the DBC --
  an audible channel through the car's own speakers that nothing is using. Same discipline as the
  lane states: find out before designing around it."""
  assert sorted({h for _, h, _ in LANE_TEST_STEPS}) == [0, 1, 2, 3]


def test_the_hands_steps_hold_the_lines_still():
  """One variable at a time. A hands step that also moved the lines would measure neither."""
  for lines, hands, label in LANE_TEST_STEPS:
    if hands != 0:
      assert lines == 1 + 5 * 1, label


def test_it_also_asks_what_lane_assist_OFF_looks_like():
  """30 is upstream's LA_Off and does not decompose as left + 5*right -- it is a whole-display
  value outside the 5x5 matrix. BluePilot never sends it, which is part of why the lines are green
  whether or not anything is steering, so what it draws is worth knowing."""
  assert 30 in [v for v, _, _ in LANE_TEST_STEPS]


def test_each_step_is_held_long_enough_to_look_up_and_back():
  ext = make(requested=1)
  first = ext.update_lane_display_test(cs())
  held = 1
  while ext.update_lane_display_test(cs()) == first:
    held += 1
    if held > STEP_FRAMES * 2:
      break
  assert held == pytest.approx(STEP_FRAMES, abs=2)


def test_it_refuses_while_the_car_is_moving():
  ext = make(requested=1)
  assert ext.update_lane_display_test(cs(standstill=False)) is None
  assert ext.ldt_step == LANE_TEST_IDLE
  assert ext.ldt_blocked == LANE_TEST_BLOCK_MOVING


def test_a_refusal_clears_the_request_so_it_cannot_ambush_him_later():
  """A request that survived would fire the moment he stopped at a light, several miles on, with
  the departure-looking states going up on the cluster unannounced."""
  ext = make(requested=1)
  ext.update_lane_display_test(cs(standstill=False))
  assert ext.ldt_params.store["FordLaneDisplayTest"] == 0
  assert run(ext, 200) == [None] * 200


def test_rolling_away_mid_walk_abandons_it_immediately():
  ext = make(requested=1)
  run(ext, STEP_FRAMES + 10)
  assert ext.ldt_step != LANE_TEST_IDLE
  assert ext.update_lane_display_test(cs(standstill=False)) is None
  assert ext.ldt_step == LANE_TEST_IDLE


def test_the_request_is_cleared_when_the_walk_starts():
  """block=True, because the read races a putNonBlocking and a leftover restarts the walk forever."""
  ext = make(requested=1)
  ext.update_lane_display_test(cs())
  assert ext.ldt_params.store["FordLaneDisplayTest"] == 0
  assert "FordLaneDisplayTest" in ext.ldt_params.blocking_writes


def test_it_runs_once_and_stops():
  ext = make(requested=1)
  run(ext, STEP_FRAMES * len(LANE_TEST_STEPS) + 5)
  assert ext.ldt_step == LANE_TEST_IDLE
  assert run(ext, 300) == [None] * 300


def test_it_publishes_what_the_screen_needs():
  ext = make(requested=1)
  CS = cs()
  ext.update_lane_display_test(CS)
  assert CS.ldt_step == 1
  assert CS.ldt_blocked == LANE_TEST_BLOCK_NONE
  assert 0.0 < CS.ldt_seconds_remaining <= LANE_TEST_STEP_S


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


def sent(lane_test, **hud_kwargs):
  packer = FakePacker()
  fields = dict(leftLaneDepart=False, rightLaneDepart=False,
                leftLaneVisible=True, rightLaneVisible=True)
  fields.update(hud_kwargs)
  hud = NS(**fields)
  fordcan_ext.create_lkas_ui_msg(packer, NS(camera=2, main=0, radar=1), True, True, 0, hud,
                                 dict(STOCK), None, lane_test)
  return packer.values["LaActvStats_D_Dsply"], packer.values["LaHandsOff_D_Dsply"]


def test_the_override_reaches_the_wire_for_every_step():
  for lines, hands, label in LANE_TEST_STEPS:
    assert sent(LaneTestOverride(lines, hands)) == (lines, hands), label


def test_the_walk_outranks_the_departure_branches():
  """Sending the departure states is the entire point -- it only runs stopped, where a real
  departure cannot be happening."""
  assert sent(LaneTestOverride(8, 0), leftLaneDepart=True) == (8, 0)


def test_no_override_leaves_the_normal_display_untouched():
  assert sent(None) == (1 + 5 * 1, 0)


def test_the_screen_labels_match_what_is_actually_sent():
  """The UI process cannot import opendbc car code, so hud_renderer_bp keeps its own copy of these
  labels. A walk that names the steps wrongly is worse than no walk -- it produces confident, wrong
  answers about a display nobody can re-check later."""
  import pathlib
  root = pathlib.Path(__file__).resolve()
  while not (root / "common" / "params_keys.h").exists():
    root = root.parent
  src = (root / "selfdrive" / "ui" / "bp" / "onroad" / "hud_renderer_bp.py").read_text(encoding="utf-8")
  # Split on the closing paren at the START of a line, not the first one anywhere -- a label
  # containing a bracket truncated the list silently and the mismatch read as a missing label.
  block = src.split("_LDT_LABELS = (", 1)[1].split(chr(10) + ")", 1)[0]
  ui_labels = [ln.strip().strip('",') for ln in block.splitlines() if ln.strip().startswith('"')]
  assert ui_labels == [label for _, _, label in LANE_TEST_STEPS]
