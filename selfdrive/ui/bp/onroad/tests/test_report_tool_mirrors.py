"""The device tool's thresholds must not drift from the gate it is reporting on.

It runs on the car, where importing the planner would drag in the whole stack, so the four numbers
are mirrored by hand -- and a mirror that drifts reports which term refused using a threshold the
gate stopped using, which is worse than not reporting at all.
"""
import pathlib
import re


def _root() -> pathlib.Path:
  for d in pathlib.Path(__file__).resolve().parents:
    if (d / "common" / "params_keys.h").exists():
      return d
  raise RuntimeError("repo root not found")


ROOT = _root()
GATE = (ROOT / "sunnypilot" / "selfdrive" / "controls" / "lib" / "passing_assist.py").read_text(encoding="utf-8")
TOOL = (ROOT / "tools" / "bp_passing_report.py").read_text(encoding="utf-8")


def test_the_live_mode_uses_the_gates_own_thresholds():
  for name in ("MAX_ROAD_EDGE_STD", "MIN_ADJACENT_LINE_PROB", "MIN_LANE_WIDTH_M", "MAX_LANE_WIDTH_M"):
    m = re.search(rf"^{name} = ([0-9.]+)", GATE, re.M)
    assert m, f"{name} not found in the gate -- this test would pass on anything"
    assert m.group(1) in TOOL, f"{name} is {m.group(1)} in the gate and not present in the report tool"


def test_the_term_names_match_the_panels():
  """Same words on the screen and in the paste, or two readouts of one fact disagree."""
  hud = (ROOT / "selfdrive" / "ui" / "bp" / "onroad" / "hud_renderer_bp.py").read_text(encoding="utf-8")
  m = re.search(r"_GEO_TERMS = \(([^)]*)\)", hud, re.S)
  assert m, "_GEO_TERMS not found in the renderer"
  for term in re.findall(r'"([^"]+)"', m.group(1)):
    assert f'"{term}"' in TOOL, f"the panel says {term!r} and the report tool does not"


def test_the_tool_can_dump_everything_published():
  """Thirty-five of the eighty-nine published fields had no reader anywhere -- not the panel, not
  the drive summary, not this tool. They were in the log, which is not a channel used here.

  A generic dump is the only fix that does not go stale: curating the list would have reached those
  thirty-five and left the next field to be discovered the same way.
  """
  assert "to_dict()" in TOOL, "the tool reads named fields only; a new one would be unreachable"
  assert "--dump" in TOOL
