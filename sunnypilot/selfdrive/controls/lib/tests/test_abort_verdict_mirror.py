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
def test_a_gate_the_hold_owns_is_reported_as_a_defect(reason):
  assert _tool()._verdict(reason).startswith("DEFECT")


@pytest.mark.parametrize("reason", ["noLead", "driverActive", "tooSlow"])
def test_no_pass_warranted_is_reported_as_correct(reason):
  """The case that cost the wrong report. These MUST NOT read as defects.

  noLead is deliberately not on HOLD_THROUGH: a lead that has really gone means no pass is
  warranted at all, and the hard clear there is the documented behaviour rather than a flicker.
  """
  v = _tool()._verdict(reason)
  assert not v.startswith("DEFECT")
  assert "by design" in v


def test_an_unattributed_abort_is_neither():
  """`none` must not fall into the by-design bucket and read as a clean bill of health."""
  v = _tool()._verdict("none")
  assert not v.startswith("DEFECT")
  assert "by design" not in v


def test_the_verdict_is_actually_printed():
  """Computed and never rendered is this fork's oldest bug; the column is the whole improvement."""
  src = open(TOOL, encoding="utf-8").read()
  assert "e['verdict']" in src, "the verdict is computed but never reaches the output"
