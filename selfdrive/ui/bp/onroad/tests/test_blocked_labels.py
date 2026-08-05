"""
BluePilot: the words shown when passing assist is not suggesting anything.

This is the text a driver actually reads at 70 mph, and it is the only explanation they get. The
enum names are for the log; putting "nothingSlower" in front of someone at speed is a failure of
the display, not a shorthand.

Sixteen reasons, each written as it was added. What is checked is the whole set: that none is
missing, none has grown too long to read at a glance, and -- the one that actually went wrong --
that none states a number the code can change underneath it.
"""

import ast
import re
from pathlib import Path

ROOT = next(d for d in Path(__file__).resolve().parents if (d / "common" / "params_keys.h").exists())
HUD = (ROOT / "selfdrive" / "ui" / "bp" / "onroad" / "hud_renderer_bp.py").read_text(encoding="utf-8")
CAPNP = (ROOT / "cereal" / "custom.capnp").read_text(encoding="utf-8")

# Beyond this it stops being readable in a glance and starts crowding the panel.
MAX_LABEL = 30


def _labels() -> dict[str, str]:
  tree = ast.parse(HUD)
  d = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign)
           and getattr(n.targets[0], "id", "") == "_BLOCKED_TEXT")
  return {k.value: v.value for k, v in zip(d.value.keys, d.value.values)}


def _enum_names() -> list[str]:
  """Every Blocked value EXCEPT `none`.

  `none` is not a reason -- it means nothing is stopping a pass, which the panel shows as the
  suggestion itself. Giving it wording would put a refusal on screen at the moment it decided to
  go, which is the one thing this table must never do.
  """
  body = CAPNP[CAPNP.index("  struct PassingAssist {"):]
  block = body[body.index("enum Blocked {"):]
  return [n for n in re.findall(r"^\s*(\w+) @\d+;", block[:block.index("}")], re.M) if n != "none"]


def test_every_reason_has_words():
  """An unmapped reason falls through to the raw enum name. It does not crash and it does not look
  broken -- it just puts "driverChangedLanes" on the screen, which is the failure this table
  exists to prevent."""
  missing = [n for n in _enum_names() if n not in _labels()]
  assert not missing, f"reasons with no wording: {missing}"


def test_nothing_is_labeled_that_is_not_a_reason():
  extra = set(_labels()) - set(_enum_names())
  assert not extra, f"labels for reasons that no longer exist: {sorted(extra)}"


def test_none_is_too_long_to_read_at_speed():
  fat = {k: v for k, v in _labels().items() if len(v) > MAX_LABEL}
  assert not fat, f"labels past {MAX_LABEL} characters: {fat}"


def test_none_states_a_number_the_code_can_change():
  """The one that actually went wrong. "Below 40 mph" outlived the floor moving to 30 and becoming
  adjustable, so the panel confidently told the driver something false. A label cannot know a
  setting's value, so it must not claim one."""
  numeric = {k: v for k, v in _labels().items() if re.search(r"\d", v)}
  assert not numeric, f"labels stating a number that a setting can change: {numeric}"


def test_geometry_thresholds_mirrored():
  """The UI states the three geometry thresholds itself rather than importing them -- that file is
  controls, this is UI, and pulling a planner module into the UI process to read three floats is a
  dependency that buys nothing. See ACC_PROPULSION_INACTIVE for the same call made earlier.

  The only real objection to duplicating a constant is drift, so this removes it. Without it the
  panel would go on explaining a gate using numbers the gate had stopped using -- which is exactly
  the failure that produced "No lane to move into" with nothing on screen to say why.

  Read from the SOURCE, not by importing: hud_renderer_bp pulls in pyray, which cannot load
  offline on every platform. Every other guard in this file does the same.
  """
  gate = (ROOT / "sunnypilot" / "selfdrive" / "controls" / "lib"
          / "passing_assist.py").read_text(encoding="utf-8")

  def value_of(src, name):
    for node in ast.walk(ast.parse(src)):
      if isinstance(node, ast.Assign) and any(
          isinstance(t, ast.Name) and t.id == name for t in node.targets):
        return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found -- this test would pass on anything")

  for name in ("MIN_ADJACENT_LINE_PROB", "MIN_LANE_WIDTH_M", "MAX_LANE_WIDTH_M",
               "MIN_EDGE_BEYOND_LINE_M", "MAX_ROAD_EDGE_STD"):
    ui_val, gate_val = value_of(HUD, name), value_of(gate, name)
    assert ui_val == gate_val, (
      f"{name} drifted: the panel says {ui_val}, the gate uses {gate_val}")
