"""FusionPilot: every tunable setting states its shipped default.

*"Why don't we just put in the description of the option what the recommended value is so I can
easily know what to change it to?"* (2026-08-05)

Since manager.py stores every declared key on its first boot and these are PERSISTENT | BACKUP,
changing a default no longer reaches a car that has been driven. The settings screen is the only
place the new value can be acted on, so a control without its recommendation is a recommendation
that cannot be followed.

Static, because nothing renders a settings screen offline -- see CLAUDE.md. It parses the source
rather than importing it, so it needs neither raylib nor a built Params.
"""
import ast
import pathlib

UI_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCREENS = (
  UI_ROOT / "sunnypilot" / "layouts" / "settings" / "cruise.py",
  # Added when the radar detector's controls landed here. Covering only cruise.py meant this guard
  # passed for nine settings it had never looked at, including the new ones -- a guard with a hole
  # in it reads exactly like a guard, which is the failure mode it exists to prevent elsewhere.
  UI_ROOT / "sunnypilot" / "layouts" / "settings" / "cruise_sub_layouts" / "speed_limit_settings.py",
)
ITEM_CALLS = {"option_item_sp", "toggle_item_sp"}

# Controls that take a param but have no meaningful "recommended" to state. Each needs a reason.
NO_RECOMMENDATION: dict[str, str] = {
  # Both descriptions are built at runtime in _update_state from what the car actually supports,
  # and when it does not support them the panel removes the param outright. A baked-in
  # "Recommended: On" would sit under text explaining the feature is unavailable on this car.
  "IntelligentCruiseButtonManagement": "description is assembled at runtime from car capability",
  "CustomAccIncrementsEnabled": "same -- enabled and described only when the car supports it",
  # Its description is _get_offset_description, a runtime callable that changes with the selected
  # offset type -- a percentage reads differently from a fixed value. There is no static string to
  # append to, and the control it sits under (SpeedLimitOffsetType) decides what it even means.
  "SpeedLimitValueOffset": "description is a runtime callable that varies with the offset type",
}


def _calls(path: pathlib.Path):
  tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
  for node in ast.walk(tree):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ITEM_CALLS:
      kw = {k.arg: k.value for k in node.keywords}
      param = kw.get("param")
      if isinstance(param, ast.Constant) and isinstance(param.value, str):
        yield path, node, param.value, kw.get("description")


def test_every_param_control_states_its_default():
  missing = []
  for path, _node, param, desc in (c for s in SCREENS for c in _calls(s)):
    if param in NO_RECOMMENDATION:
      continue
    ok = (isinstance(desc, ast.Call) and isinstance(desc.func, ast.Name)
          and desc.func.id == "recommended")
    if not ok:
      missing.append(f"{path.name}:{param}")
  assert not missing, (
    f"settings with no stated default: {sorted(missing)}. Wrap the description with "
    "recommended(tr(...), \"<Param>\", <label_callback>) so the screen says what to set it to, "
    "or add the key to NO_RECOMMENDATION with a reason.")


def test_the_recommendation_quotes_the_control_it_sits_on():
  """A description recommending a different key than the control changes is worse than none, and
  copy-paste between two adjacent option blocks is exactly how that happens."""
  wrong = []
  for path, _node, param, desc in (c for s in SCREENS for c in _calls(s)):
    if not (isinstance(desc, ast.Call) and getattr(desc.func, "id", None) == "recommended"):
      continue
    quoted = [a.value for a in desc.args
              if isinstance(a, ast.Constant) and isinstance(a.value, str)]
    if quoted and quoted[0] != param:
      wrong.append(f"{path.name}: control sets {param}, description quotes {quoted[0]}")
  assert not wrong, wrong


def test_a_scaled_control_passes_its_label():
  """Where the stored number is not the number on screen, the description has to render through the
  same callback -- otherwise IcbmLeadMaxTtc reads "70" in the text and "7.0 s" on the control
  beside it, and the driver types 70 into a field that means tenths."""
  bad = []
  for path, node, param, desc in (c for s in SCREENS for c in _calls(s)):
    kw = {k.arg: k.value for k in node.keywords}
    if "label_callback" not in kw:
      continue
    if not (isinstance(desc, ast.Call) and getattr(desc.func, "id", None) == "recommended"):
      continue
    if len(desc.args) < 3:
      bad.append(f"{path.name}:{param} renders with a label_callback the description does not use")
  assert not bad, bad
