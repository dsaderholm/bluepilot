"""The abort tool's HOLD_THROUGH mirror must not rot, and its verdict must split the two cases.

WHY THIS EXISTS. On 2026-09-25 three noLead aborts at 76-79 mph were nearly reported as the
2026-09-22 radar-dropout fix having failed. They are not: LEAD_GAP_GRACE_S expired on a lead that
had genuinely been gone 0.4 s, and clearing the side in one frame is exactly what that branch is
required to do -- test_no_pass_warranted_clears_it_immediately states the property. What made the
two look identical in the output was that the tool printed the CAUSE and left the reader to know
by heart which causes the hold owns.

So the classifier is only as good as its copy of HOLD_THROUGH, and a copy is the thing that goes
stale. This reads the real tuple out of passing_assist and fails if the tool's drifts from it.
"""
import importlib.util
import os
import re

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
TOOL = os.path.join(REPO, "tools", "bp_abort_why.py")
SRC = os.path.join(REPO, "sunnypilot", "selfdrive", "controls", "lib", "passing_assist.py")


def _tool():
  spec = importlib.util.spec_from_file_location("bp_abort_why", TOOL)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def _real_hold_through():
  """Parsed out of the source rather than imported.

  passing_assist pulls in the whole controls stack, and this test has to keep working in the
  offline runner where that does not import. Parsing also means the test reads what a HUMAN reads
  in that file, which is the thing the tool is copying.
  """
  src = open(SRC, encoding="utf-8").read()
  m = re.search(r"^HOLD_THROUGH = \(([^)]*)\)", src, re.M)
  assert m, "HOLD_THROUGH is no longer a module-level tuple literal in passing_assist"
  return tuple(x.strip().split(".")[-1] for x in m.group(1).split(",") if x.strip())


def test_the_tools_copy_of_hold_through_matches_passing_assist():
  assert tuple(_tool().HOLD_THROUGH) == _real_hold_through()


@pytest.mark.parametrize("reason", ["noLaneAvailable", "adjacentSlow", "nothingSlower"])
def test_a_gate_the_hold_owns_that_only_FLICKERED_is_a_defect(reason):
  """A frame or two of a HOLD_THROUGH cause is the cc9b910b0a bug class."""
  assert _tool()._verdict(reason, held=1).startswith("DEFECT")


@pytest.mark.parametrize("reason", ["noLaneAvailable", "adjacentSlow", "nothingSlower"])
def test_the_same_gate_refusing_for_the_WHOLE_window_is_not_a_defect(reason):
  """The distinction that keeps the verdict honest.

  All twelve aborts in the 2026-09-25 post-fix set are HOLD_THROUGH causes that held for the full
  WANTED_FALL_S. Calling those defects would send the next session at WANTED_FALL_S -- lengthening
  the window a maneuver survives on stale evidence, which is the permissive direction, on data
  that says the gates were right.
  """
  m = _tool()
  v = m._verdict(reason, held=m.FALL_FRAMES)
  assert not v.startswith("DEFECT")
  assert "by design" in v


def test_the_held_count_comes_from_the_window():
  """_sustained_held must count the modal cause, not the window length or the raw total."""
  m = _tool()
  assert m._sustained_held(["adjacentSlow"] * 15) == 15
  assert m._sustained_held(["none"] * 10 + ["adjacentSlow"] * 5) == 5
  assert m._sustained_held(["none"] * 10) == 0
  # the coincidental last frame must not be credited with the window
  assert m._sustained_held(["adjacentSlow"] * 14 + ["noLaneAvailable"]) == 14


@pytest.mark.parametrize("reason", ["noLead", "driverActive", "tooSlow"])
@pytest.mark.parametrize("held", [0, 1, 7, 15])
def test_no_pass_warranted_is_reported_as_correct(reason, held):
  """The case that cost the wrong report. These MUST NOT read as defects, at ANY hold length.

  noLead is deliberately not on HOLD_THROUGH: a lead that has really gone means no pass is
  warranted at all, and the hard clear there is the documented behaviour rather than a flicker.
  Swept over held because the hold length must not be able to turn one of these into a defect.
  """
  v = _tool()._verdict(reason, held)
  assert not v.startswith("DEFECT")
  assert "by design" in v


def test_an_unattributed_abort_is_neither():
  """`none` must not fall into the by-design bucket and read as a clean bill of health."""
  for held in (0, 1, 15):
    v = _tool()._verdict("none", held)
    assert not v.startswith("DEFECT")
    assert "by design" not in v


def test_the_verdict_is_actually_printed():
  """Computed and never rendered is this fork's oldest bug; the column is the whole improvement."""
  src = open(TOOL, encoding="utf-8").read()
  assert "e['verdict']" in src, "the verdict is computed but never reaches the output"


def test_the_sustained_cause_beats_the_abort_frame():
  """Route 0000049f seg 5 t+33.35, reproduced as the sequence the tool sees.

  adjacentSlow held for the fifteen frames before the abort -- exactly WANTED_FALL_S, which is
  what released the side. On the abort frame itself leftEdgeStd ticked 1.19 -> 1.31 and the cause
  read noLaneAvailable for two frames while width, beyond and paint sat steady. Taking the abort
  frame at face value names a coincidence, and it sent two scans to different answers about one
  event before this existed.
  """
  m = _tool()
  window = ["adjacentSlow"] * 15
  assert m._sustained(window) == "adjacentSlow"
  # the coincidental frame does not get a vote it can win on
  assert m._sustained(window + ["noLaneAvailable"]) == "adjacentSlow"


def test_a_genuinely_sustained_gate_is_still_named():
  """The opposite case must keep working, or the fix trades one wrong answer for another."""
  m = _tool()
  assert m._sustained(["noLaneAvailable"] * 15) == "noLaneAvailable"


def test_suggestion_frames_do_not_drown_out_the_cause():
  """`none` means a suggestion WAS being made, so it is not a cause and must never win the vote.

  It is also the most common value in most windows -- counting it would report `none` on nearly
  every abort, which is the useless answer this whole line of work started from.
  """
  m = _tool()
  assert m._sustained(["none"] * 20 + ["adjacentSlow"] * 3) == "adjacentSlow"
  assert m._sustained(["none"] * 20) == "none"


def test_the_disagreement_is_printed():
  """Computed and not rendered is the recurring failure; the whole point is that a reader sees it.

  PINS THE GUARD, NOT JUST THE INTERPOLATION. The first version asserted only that "e['instant']"
  appeared in the source, and a mutant replacing the condition with `if False:` left that text
  sitting under it and passed. Parse the branch and check the test is a real comparison of the two
  causes -- same reason test_the_tap_target_is_the_set_speed_box parses for its assignment.
  """
  import ast as _ast
  tree = _ast.parse(open(TOOL, encoding="utf-8").read())
  found = False
  for node in _ast.walk(tree):
    if not isinstance(node, _ast.If) or not isinstance(node.test, _ast.Compare):
      continue
    keys = {_ast.unparse(n) for n in _ast.walk(node.test) if isinstance(n, _ast.Subscript)}
    if not {"e['instant']", "e['reason']"} <= keys:
      continue
    printed = any(isinstance(c, _ast.Call) and getattr(c.func, "id", "") == "print"
                  for n in node.body for c in _ast.walk(n))
    assert printed, "the disagreement branch exists but prints nothing"
    found = True
  assert found, "nothing compares the abort-frame cause against the sustained one"


def test_the_fall_window_matches_the_debounce():
  """FALL_FRAMES is a copy of WANTED_FALL_S / DT_MDL and copies rot. Read both and compare."""
  import re as _re
  src = open(SRC, encoding="utf-8").read()
  fall = float(_re.search(r"^WANTED_FALL_S = ([0-9.]+)", src, _re.M).group(1))
  # DT_MDL is openpilot's model cadence, 20 Hz.
  assert _tool().FALL_FRAMES == round(fall / 0.05), (
    f"FALL_FRAMES {_tool().FALL_FRAMES} no longer matches WANTED_FALL_S {fall} at 20 Hz")


def test_the_verdict_call_site_passes_the_held_count():
  """A call site that omits it would relabel every sustained refusal a defect, silently.

  `held` is a required argument so that mistake is a TypeError rather than a wrong answer -- but
  nothing in this suite runs main(), so the call site itself is pinned here too. This was the one
  mutant that survived the first pass.
  """
  import ast as _ast
  tree = _ast.parse(open(TOOL, encoding="utf-8").read())
  calls = [n for n in _ast.walk(tree)
           if isinstance(n, _ast.Call) and getattr(n.func, "id", "") == "_verdict"]
  assert calls, "nothing calls _verdict"
  for c in calls:
    assert len(c.args) + len(c.keywords) == 2, (
      "_verdict must be called with the reason AND the held count")
    # AND the count must be COMPUTED. `_verdict(reason, 0)` has two arguments and relabels every
    # sustained refusal a defect -- it survived two rounds of mutation testing that only counted
    # the arguments. Pin the expression, not the arity.
    held = c.keywords[0].value if c.keywords else c.args[1]
    assert isinstance(held, _ast.Call) and getattr(held.func, "id", "") == "_sustained_held", (
      "the held count must come from _sustained_held, not a literal")


def test_the_held_count_is_required():
  """No default, so omitting it cannot quietly mean zero."""
  import inspect
  sig = inspect.signature(_tool()._verdict)
  assert sig.parameters["held"].default is inspect.Parameter.empty
