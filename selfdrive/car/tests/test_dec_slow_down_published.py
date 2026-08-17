"""FusionPilot: the stop-sign signal has to reach the wire, not just the controller.

He reported stop-sign slowing as inaccurate while traffic lights are fine. That complaint was
unattributable, because `has_slow_down()` -- the thing the whole stop-sign path keys on -- had never
been published. Nothing in any recorded route says whether the model failed to see the sign or saw
it and the response was wrong.

This is the third time in this fork a value has been computed correctly and never rendered, so the
test asserts the WIRING rather than the arithmetic: the capnp field exists, and plannerd's publish
site sets it from the accessor rather than from a mode flag that happens to correlate.
"""
from __future__ import annotations

import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[3]


def test_the_capnp_fields_exist_and_are_contiguous():
  """capnp ordinals must run from 0 with no gap -- a gap calls abort() and kills the interpreter
  with no Python-level exception, which is how a whole suite dies behind a traceback naming pytest.
  """
  src = (REPO / "cereal" / "custom.capnp").read_text(encoding="utf-8")
  start = src.index("struct DynamicExperimentalControl {")
  body = src[start:src.index("enum DynamicExperimentalControlState", start)]
  ordinals = sorted(int(tok[1:]) for tok in body.split() if tok.startswith("@") and tok[1:].isdigit())
  assert ordinals == list(range(len(ordinals))), f"non-contiguous ordinals: {ordinals}"
  for field in ("hasSlowDown", "slowDownUrgency", "slowDownEndpoint"):
    assert field in body, f"{field} is not on the wire"


def test_plannerd_publishes_them_from_the_accessors():
  """The failure this guards is a field that exists, reads plausibly, and is fed by the wrong thing.
  Assert the call actually made, not that the name appears somewhere in the file."""
  path = REPO / "sunnypilot" / "selfdrive" / "controls" / "lib" / "longitudinal_planner.py"
  tree = ast.parse(path.read_text(encoding="utf-8"))

  assigned: dict[str, str] = {}
  locals_: dict[str, str] = {}
  for node in ast.walk(tree):
    if not isinstance(node, ast.Assign):
      continue
    for target in node.targets:
      if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "dec":
        assigned[target.attr] = ast.dump(node.value)
      elif isinstance(target, ast.Name):
        locals_[target.id] = ast.dump(node.value)

  # Follow one level of indirection: slowDownEndpoint is fed through a local so the inf guard has
  # something to test. Resolving the local is the difference between checking the VALUE's origin
  # and checking that a variable happens to be spelled a certain way.
  for field, dump in list(assigned.items()):
    for name, src in locals_.items():
      if f"Name(id='{name}'" in dump:
        assigned[field] = dump + " <- " + src

  assert "hasSlowDown" in assigned, "plannerd never publishes hasSlowDown"
  assert "has_slow_down" in assigned["hasSlowDown"], (
    "hasSlowDown is not fed by has_slow_down() -- a mode flag that correlates is not the signal")
  assert "urgency" in assigned.get("slowDownUrgency", "")
  assert "endpoint_x" in assigned.get("slowDownEndpoint", ""), (
    "slowDownEndpoint must come from endpoint_x(), which is where the model expects to stop")


def test_an_infinite_endpoint_is_not_published_as_a_finite_one():
  """endpoint_x() is inf when the model's plan is not full length. Publishing inf makes every reader
  handle it; publishing it unguarded as a large float would read as a real distance."""
  path = REPO / "sunnypilot" / "selfdrive" / "controls" / "lib" / "longitudinal_planner.py"
  src = path.read_text(encoding="utf-8")
  assert "isfinite" in src, "endpoint_x() reaches the wire without an inf guard"
