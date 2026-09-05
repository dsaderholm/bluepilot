"""The gate is allowed to make an alert quieter. It is never allowed to make one impossible.

Every test below is about one of two properties, and the second matters more:

  1. a saturation with the car still in its lane does not chime  -- the feature
  2. ANY doubt at all still chimes                               -- the thing that keeps it safe

Property 2 is where a bug here would actually hurt, so most of this file drives the failure paths:
no lane lines, unconfident lane lines, an empty `y`, a NaN, a stale model, a params store that
raises, a SubMaster that raises. All of them must alert. **A gate that fails quiet is worse than no
gate at all**, because the episodes on record that reached 1.43 m and 1.67 m -- nearly half a lane --
arrive in exactly the same costume as the 26 that never left it.

MUTATION-TESTED, because a green run proves nothing about a guard. Each of the following was
introduced on purpose and each failed at least one test here: dropping the `deviation is None`
branch, flipping `>=` to `>`, deleting the latch, deleting the latch RESET, making `read_params`
fail closed, and returning False from `should_alert`'s except.
"""
import math

import pytest

from openpilot.bluepilot.selfdrive.selfdrived.steer_saturated_gate import (
  LANE_DEVIATION_M, MIN_LANE_PROB, RESET_FRAMES, SteerSaturatedGate, lane_deviation)


class FakeLine:
  def __init__(self, y0):
    self.y = [] if y0 is None else [y0, y0, y0]


def lanes(offset_m: float):
  """Four lane lines whose inner pair puts the car `offset_m` off center.

  Lane center is the midpoint of laneLines[1] and [2], so a car sitting `d` to the LEFT of center
  sees both lines shifted `-d`. Outer lines are present but deliberately different values -- reading
  [0] or [3] instead of [1]/[2] has to produce a wrong answer, not a coincidentally right one.
  """
  return [FakeLine(-offset_m - 3.7), FakeLine(-offset_m - 1.85),
          FakeLine(-offset_m + 1.85), FakeLine(-offset_m + 3.7)]


CONFIDENT = [0.05, 0.99, 0.98, 0.05]


def drive(gate, offset, frames=1, start=0, valid=True):
  """Run the gate for `frames` consecutive 100 Hz frames at a fixed offset. Returns the last verdict."""
  out = True
  for i in range(frames):
    out = gate.update(lanes(offset), CONFIDENT, valid, start + i)
  return out


# ---------------------------------------------------------------------------------------------
# lane_deviation: the arithmetic, and every way it is allowed to say "I don't know".

@pytest.mark.parametrize("offset", [0.0, 0.12, 0.5, 1.4])
def test_the_deviation_is_the_distance_from_lane_center(offset):
  assert lane_deviation(lanes(offset), CONFIDENT) == pytest.approx(offset)


def test_it_is_unsigned_because_the_question_is_how_far_not_which_way():
  assert lane_deviation(lanes(-0.62), CONFIDENT) == pytest.approx(0.62)


@pytest.mark.parametrize("probs,why", [
  ([0.9, MIN_LANE_PROB - 0.01, 0.9, 0.9], "left line not believable"),
  ([0.9, 0.9, MIN_LANE_PROB - 0.01, 0.9], "right line not believable"),
  ([0.9, 0.9], "fewer probs than lines"),
])
def test_an_unconfident_lane_is_unmeasurable(probs, why):
  assert lane_deviation(lanes(0.1), probs) is None, why


def test_a_short_or_empty_lane_list_is_unmeasurable():
  assert lane_deviation([], []) is None
  assert lane_deviation(lanes(0.1)[:2], CONFIDENT[:2]) is None
  assert lane_deviation([FakeLine(None)] * 4, CONFIDENT) is None


def test_a_non_finite_lane_line_is_unmeasurable():
  """A NaN compares False against every threshold, so it would SILENCE the alert if it reached the
  comparison. That is the one arithmetic path where a missing check fails quiet."""
  assert lane_deviation([FakeLine(0.0), FakeLine(math.nan), FakeLine(1.85), FakeLine(3.7)],
                        CONFIDENT) is None
  assert lane_deviation([FakeLine(0.0), FakeLine(math.inf), FakeLine(1.85), FakeLine(3.7)],
                        CONFIDENT) is None


def test_garbage_in_place_of_lane_lines_is_unmeasurable_rather_than_an_exception():
  """This runs inside selfdrived. Raising here would take out the process that publishes every
  alert in order to suppress one of them."""
  assert lane_deviation(["not", "a", "lane", "line"], CONFIDENT) is None
  assert lane_deviation(None, None) is None


# ---------------------------------------------------------------------------------------------
# The feature: a saturation inside the lane is not worth a chime.

def test_a_centered_car_is_not_told_to_take_control():
  gate = SteerSaturatedGate()
  assert drive(gate, 0.24) is False, "0.24 m is the MEDIAN offset while this alert fires today"
  assert gate.last_suppressed is True
  assert gate.last_deviation == pytest.approx(0.24)


def test_a_car_out_of_position_is_still_told():
  gate = SteerSaturatedGate()
  assert drive(gate, 0.82) is True, "0.82 m is a real episode from route 00000420"


def test_the_threshold_itself_is_inclusive():
  """Exactly at the line alerts. A `>` here would be invisible in any road data and would only ever
  show up as a suppression nobody could explain."""
  assert drive(SteerSaturatedGate(), LANE_DEVIATION_M) is True
  assert drive(SteerSaturatedGate(), LANE_DEVIATION_M - 0.01) is False


# ---------------------------------------------------------------------------------------------
# Fail open. Everything here MUST alert.

def test_an_unmeasurable_lane_alerts():
  gate = SteerSaturatedGate()
  assert gate.update(lanes(0.0), [0.9, 0.1, 0.1, 0.9], True, 0) is True
  assert gate.last_deviation == -1.0, "-1 means unmeasurable; 0.0 would read as perfectly centered"


def test_a_stale_or_invalid_model_alerts():
  """The car is dead center by the numbers and the model is not to be believed, so it alerts."""
  assert drive(SteerSaturatedGate(), 0.0, valid=False) is True


def test_a_params_store_that_raises_leaves_upstream_behavior():
  class Exploding:
    def get_bool(self, key):
      raise OSError("params store is busy")

  gate = SteerSaturatedGate()
  gate.read_params(Exploding())
  assert gate.enabled is False
  assert drive(gate, 0.0) is True, "a params failure must not silence a take-control warning"


def test_a_submaster_that_raises_alerts():
  class Exploding:
    def __getitem__(self, key):
      raise RuntimeError("no such service")

  assert SteerSaturatedGate().should_alert(Exploding()) is True


def test_the_toggle_off_reproduces_upstream_exactly():
  gate = SteerSaturatedGate()
  gate.enabled = False
  assert drive(gate, 0.0) is True
  assert gate.last_suppressed is False


# ---------------------------------------------------------------------------------------------
# The latch: no chattering, and no forgetting mid-episode.

def test_an_episode_that_goes_wide_keeps_its_alert_when_it_comes_back():
  """Without this the chime cycles on and off across the threshold, which is a worse experience
  than the alert this whole feature exists to quieten."""
  gate = SteerSaturatedGate()
  assert drive(gate, 0.70, frames=5, start=0) is True
  assert drive(gate, 0.05, frames=20, start=5) is True, "the episode already earned its alert"


def test_a_new_episode_starts_silent_again():
  """The latch has to RESET or the first wide corner of a drive alerts for the rest of it."""
  gate = SteerSaturatedGate()
  assert drive(gate, 0.70, frames=5, start=0) is True
  assert drive(gate, 0.05, frames=1, start=5 + RESET_FRAMES + 1) is False


def test_a_gap_shorter_than_the_reset_is_the_same_episode():
  gate = SteerSaturatedGate()
  assert drive(gate, 0.70, frames=1, start=0) is True
  assert drive(gate, 0.05, frames=1, start=RESET_FRAMES) is True


# ---------------------------------------------------------------------------------------------
# It ships on, and it ships wired up. Both have bitten this fork before.

def test_the_shipped_default_is_on():
  """A feature defaulting off is a recommendation not to use it -- and `IcbmModelStopEnabled` spent
  weeks untested that way."""
  import pathlib
  keys = pathlib.Path(__file__).parents[4] / "common" / "params_keys.h"
  line = [ln for ln in keys.read_text(encoding="utf-8").splitlines()
          if '"SteerAlertLaneGate"' in ln]
  assert len(line) == 1, "the param must be declared exactly once in params_keys.h"
  assert "BOOL" in line[0] and '"1"' in line[0], line[0]


def test_selfdrived_actually_calls_the_gate():
  """A gate nothing calls is the same as no gate, and reads exactly as correct. Parsed as text
  rather than imported because selfdrived needs msgq, which does not exist offline."""
  import pathlib
  src = (pathlib.Path(__file__).parents[4] / "selfdrive" / "selfdrived"
         / "selfdrived.py").read_text(encoding="utf-8")
  lines = src.splitlines()
  raises = [i for i, ln in enumerate(lines)
            if "self.events.add(EventName.steerSaturated)" in ln]
  assert len(raises) == 1, "more than one raise site -- this test only guards one of them"

  # The line directly above the raise is its `if`. Asserting on THAT rather than on the file
  # anywhere is what makes this fail against a gate that is called and then ignored -- the
  # "assert on the expression, not a window" rule.
  guard = lines[raises[0] - 1]
  assert guard.lstrip().startswith("if "), guard
  assert "lac.saturated" in guard and "steer_saturated_gate.should_alert(self.sm)" in guard, guard


def test_the_toggle_is_registered_and_not_merely_defined():
  """`IcbmModelStopEnabled` shipped unreachable because it was defined and never put in the returned
  items list, and he reported the feature as broken when it was only unenableable."""
  import pathlib
  src = (pathlib.Path(__file__).parents[4] / "selfdrive" / "ui" / "bp" / "layouts" / "settings"
         / "bluepilot.py").read_text(encoding="utf-8")
  assert "self._steer_alert_lane_gate = toggle_item(" in src, "not defined"
  lateral = src.split("lateral_items = [", 1)[1].split("]", 1)[0]
  assert "self._steer_alert_lane_gate," in lateral, "defined but never rendered"
