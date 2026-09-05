#!/usr/bin/env python3
"""FusionPilot: find settings that exist on the big screen but cannot be reached from SunnyLink.

Asked for 2026-08-12: comma 4 compatibility, with every setting configurable through SunnyLink.
The comma 4 has a small screen, so SunnyLink is not a convenience there -- for a fork with 33 of its own
settings it is the practical way to configure the car at all.

WHAT THIS CHECKS. `selfdrive/ui/sunnypilot/layouts/settings/` is where the fork defines its controls,
one `*_item_sp(...)` call per setting. `sunnypilot/sunnylink/settings_ui_src/pages/*.yaml` is the
authoring tree that compiles into settings_ui.json, which is what SunnyLink and the web frontend
read. A param present in the first and absent from the second is a setting the owner can only change
by standing at the car -- exactly what a small screen makes painful.

This is deliberately an AUDIT rather than a generator. Placing an item means choosing its page,
section and sub-panel, and choosing its enablement conditions; that is judgement, and a generator
that guesses produces a settings screen nobody can navigate. So it reports what is missing and prints
a ready-to-paste YAML block carrying the title, range and step across faithfully.

`test_sunnylink_settings_complete.py` runs the same comparison and fails when something is missing,
which is what keeps a newly added setting from silently being big-screen-only.

USAGE:

    python tools/bp_sunnylink_settings_audit.py            # what is missing, with YAML to paste
    python tools/bp_sunnylink_settings_audit.py --all      # every setting found, missing or not
"""
from __future__ import annotations

import argparse
import ast
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
UI_DIRS = [
  ROOT / "selfdrive/ui/sunnypilot/layouts/settings",
  ROOT / "selfdrive/ui/bp/layouts/settings",
]
SETTINGS_UI_JSON = ROOT / "sunnypilot/sunnylink/settings_ui.json"

# The fork's own settings. Upstream's are upstream's problem: they are already carried in
# settings_ui_src, and adding them here would turn every upstream settings change into a failure
# here. See "Do not fix UNRELATED upstream bugs in this fork".
#
# ADDING A NEW PREFIX IS AN OBLIGATION, and it is silent when missed. `MapdV2` was added on
# 2026-08-16 with a toggle on the OSM screen, and this audit reported "33/33 reachable, 0 missing"
# while that toggle was unreachable from SunnyLink -- because a setting whose name matches no prefix
# here is not a fork setting as far as this tool is concerned, so it is not counted OR checked. The
# comma 4 rule is "every setting must be reachable"; a guard that cannot see a whole settings family
# reads as compliance. If you introduce a name family, put it here in the same commit.
# 2026-08-29: "FordSynthesize" and "FordPref" were narrower than the family they were meant to
# cover, and the failure this docstring predicts happened again -- `FordBlendHorizonScale` was added
# with an on-device control and this audit reported "35/35 reachable, 0 missing" while it was
# unreachable from SunnyLink. So did `FordLowSpeedFactor_ang`, `FordHighSpeedFactor_ang` and
# `FordHighSpeedDampening_ang`, which happened to be in settings_ui.json only because somebody
# hand-edited the JSON -- and regenerating from the YAML source would have silently deleted them.
# Widened to bare "Ford", which subsumes both of the old entries and every lateral tuning key.
OUR_PREFIXES = ("Icbm", "SmartCruiseControl", "SpeedLimit", "PassingAssist", "RadarDetector",
                "Mapd", "StockAcc", "Ford",
                # THIRD BLIND SPOT, found 2026-09-01 the way the comment above says to find one:
                # by adding a setting and watching the audit stay green. The angle-mode lane
                # positioning family is lowercase and matched NO prefix, so all four of its params
                # were invisible here -- the audit has never once checked them, including while
                # CLAUDE.md was recording that three of them reached settings_ui.json only by hand.
                "lane_centering", "enable_lane_positioning", "custom_path_offset",
                # FOURTH: `SteerAlertLaneGate` (2026-09-05). Added with the prefix and the audit
                # was run BEFORE the YAML entry existed, so it was watched failing rather than
                # trusted green -- which is the only check that has ever caught this list.
                "SteerAlert")

# NOT a gap in the list above, and not to be "fixed" by adding it. `BPSentryEnabled` is the fork's
# crash-reporting KILL SWITCH -- upstream inits Sentry unconditionally and this fork returns early
# from init() unless it is set, so the param exists to keep telemetry off, not to offer a feature.
# It ships off, has no on-device toggle, and `system/tests/test_sentry_disabled_by_default.py`
# fails if a merge drops the guard.
#
# Giving it a SunnyLink entry would put a REMOTE control on device telemetry the owner wants
# permanently off, which is the opposite of what the guard is for. Anything else that turns out to
# be a kill switch rather than a setting belongs here too.
DELIBERATELY_NOT_REMOTE = ("BPSentryEnabled",)

# option_item_sp(...) declares `value_change_step: int = 1`; an omitted step means 1.
DEFAULT_OPTION_STEP = 1

# 2026-08-29: the four `*_item_sp` names are sunnypilot's own constructors, and `UI_DIRS` has
# always included `selfdrive/ui/bp/layouts/settings` -- but the BluePilot screen builds its
# controls with `float_control_item` / `int_control_item` / `toggle_item`, none of which were
# listed here. So that whole screen was walked and recognized nothing, and every setting on it was
# invisible: not counted, not checked, and reported as compliance. That is exactly the failure the
# OUR_PREFIXES comment below describes, one level deeper -- a scanner that reads the right files
# and understands none of their calls looks identical to a clean audit.
#
# It is why `FordLowSpeedFactor_ang`, `FordHighSpeedFactor_ang`, `FordHighSpeedDampening_ang` and
# the lane-centering trio reached settings_ui.json only because somebody hand-edited the JSON --
# which the generator then silently deleted on the next regeneration.
ITEM_CALLS = {
  "toggle_item_sp": "toggle",
  "option_item_sp": "option",
  "multiple_button_item_sp": "multiple_button",
  "simple_button_item_sp": "button",
  "toggle_item": "toggle",
  "float_control_item": "option",
  "int_control_item": "option",
}


def _literal(node, consts: dict | None = None) -> object | None:
  """Best-effort literal from an AST node, seeing through tr(...) and string concatenation.

  `consts` resolves bare NAMES against the file's module-level assignments. Without it the
  button lists are invisible: the device writes `buttons=SPEED_LIMIT_OFFSET_TYPE_BUTTONS`, a name,
  and returning None there is what let SunnyLink ship three choices against the device's four.
  """
  if isinstance(node, ast.Name) and consts and node.id in consts:
    return _literal(consts[node.id], consts)
  if isinstance(node, ast.Constant):
    return node.value
  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
    # tr(...) is translation, recommended(...) appends the shipped default at display time. Both
    # wrap the real string in their first argument, and both must be seen through or every
    # description here comes back empty.
    if node.func.id in ("tr", "recommended") and node.args:
      return _literal(node.args[0], consts)
  if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
    left, right = _literal(node.left, consts), _literal(node.right, consts)
    if isinstance(left, str) and isinstance(right, str):
      return left + right
  if isinstance(node, ast.JoinedStr):
    return None
  if isinstance(node, ast.Lambda):
    return None
  if isinstance(node, ast.List):
    vals = [_literal(e, consts) for e in node.elts]
    return vals if all(v is not None for v in vals) else None
  if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
    inner = _literal(node.operand, consts)
    return -inner if isinstance(inner, int | float) else None
  return None


def _toggle_param(call: ast.Call) -> str | None:
  """The param a bare `toggle_item(...)` reads and writes, when it has no `param=` kwarg.

  FIFTH BLIND SPOT, found 2026-09-05 the way the four before it were found: by adding a setting,
  running the audit, and watching it stay green. `toggle_item` on the BluePilot screen does not take
  a `param` -- it takes `initial_state=self._safe_get_bool(self._params, "X")` and
  `callback=lambda state: self._toggle_callback(state, "X")` -- so requiring `param=` made EVERY
  toggle on that screen invisible. `enable_lane_positioning_ang` and `enable_lane_positioning_curv`
  had never once been checked, and the audit reported 42/42 while doing it.

  The name is taken only when both kwargs name the SAME string, which is what makes it a param
  rather than any string that happens to be nearby: a toggle that reads one key and writes another
  is a bug this returns None for rather than silently blessing.
  """
  kw = {k.arg: k.value for k in call.keywords if k.arg}
  reads = {n.value for n in ast.walk(kw["initial_state"]) if isinstance(n, ast.Constant)
           and isinstance(n.value, str)} if "initial_state" in kw else set()
  writes = {n.value for n in ast.walk(kw["callback"]) if isinstance(n, ast.Constant)
            and isinstance(n.value, str)} if "callback" in kw else set()
  both = reads & writes
  return both.pop() if len(both) == 1 else None


def collect_ui_settings() -> dict[str, dict]:
  """Every fork-owned setting the on-device UI defines, keyed by param name."""
  found: dict[str, dict] = {}
  for d in UI_DIRS:
    if not d.exists():
      continue
    for path in sorted(d.rglob("*.py")):
      if "__pycache__" in path.parts:
        continue
      try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
      except SyntaxError:
        continue
      consts = {t.id: n.value for n in tree.body if isinstance(n, ast.Assign)
                for t in n.targets if isinstance(t, ast.Name)}
      for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
          continue
        widget = ITEM_CALLS.get(node.func.id)
        if widget is None:
          continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        param = _literal(kw.get("param"), consts)
        if param is None and widget == "toggle":
          param = _toggle_param(node)
        if not isinstance(param, str) or param in DELIBERATELY_NOT_REMOTE:
          continue
        if not param.startswith(OUR_PREFIXES):
          continue
        entry = {
          "param": param,
          "widget": widget,
          "title": _literal(kw.get("title"), consts),
          "description": _literal(kw.get("description"), consts),
          "min": _literal(kw.get("min_value"), consts),
          "max": _literal(kw.get("max_value"), consts),
          "step": _literal(kw.get("value_change_step"), consts) or DEFAULT_OPTION_STEP,
          "buttons": _literal(kw.get("buttons"), consts),
          "source": str(path.relative_to(ROOT)).replace("\\", "/"),
        }
        found.setdefault(param, entry)
  return found


def collect_sunnylink_keys() -> set[str]:
  if not SETTINGS_UI_JSON.exists():
    sys.exit(f"missing {SETTINGS_UI_JSON}; run compile_settings_ui.py first")
  keys: set[str] = set()

  def walk(o) -> None:
    if isinstance(o, dict):
      k = o.get("key")
      if isinstance(k, str):
        keys.add(k)
      for v in o.values():
        walk(v)
    elif isinstance(o, list):
      for v in o:
        walk(v)

  walk(json.loads(SETTINGS_UI_JSON.read_text(encoding="utf-8")))
  return keys


def option_mismatches() -> list[str]:
  """Controls whose CHOICES differ between the device and SunnyLink.

  A param can be present and still unusable. `SpeedLimitOffsetType` shipped with four buttons on
  the device -- None / Fixed / % / By Limit -- and only three in SunnyLink, because "By Limit" is
  this fork's own addition and nobody updated the remote copy. A car set to it matched no option,
  so the control rendered with nothing selected: not wrong-looking, just blank. It took a
  screenshot to find, because presence-only checking said everything was fine.
  """
  ui, sl_json = collect_ui_settings(), json.loads(SETTINGS_UI_JSON.read_text(encoding="utf-8"))
  remote: dict[str, list] = {}

  def walk(o) -> None:
    if isinstance(o, dict):
      if isinstance(o.get("key"), str) and "widget" in o and o.get("options"):
        remote[o["key"]] = o["options"]
      for v in o.values():
        walk(v)
    elif isinstance(o, list):
      for v in o:
        walk(v)

  walk(sl_json)
  out = []
  for param, entry in sorted(ui.items()):
    device = entry.get("buttons")
    if not device:
      continue
    got = remote.get(param)
    if got is None:
      out.append(f"{param}: device offers {len(device)} choices, SunnyLink offers none")
    elif len(got) != len(device):
      out.append(f"{param}: device offers {len(device)} ({', '.join(device)}), "
                 f"SunnyLink offers {len(got)}")
  return out


def missing_settings() -> list[dict]:
  ui, sl = collect_ui_settings(), collect_sunnylink_keys()
  return [ui[p] for p in sorted(ui) if p not in sl]


def _yaml_block(e: dict) -> str:
  title = e["title"] or e["param"]
  lines = [f'    - key: {e["param"]}',
           f'      widget: {e["widget"]}',
           f'      title: {json.dumps(title)}']
  if e.get("description"):
    lines.append(f'      description: {json.dumps(e["description"])}')
  if e["widget"] == "option" and e["min"] is not None:
    lines += [f'      min: {e["min"]}', f'      max: {e["max"]}', f'      step: {e["step"]}']
  if e["widget"] == "multiple_button" and e["buttons"]:
    # The schema wants {value, label} objects; the button's index IS its stored value, which is how
    # the on-device widget maps a press to a param value.
    lines.append('      options:')
    for i, label in enumerate(e["buttons"]):
      lines += [f'      - value: {i}', f'        label: {json.dumps(label)}']
  return "\n".join(lines)


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--all", action="store_true", help="list every fork setting, not just missing ones")
  args = ap.parse_args()

  ui, sl = collect_ui_settings(), collect_sunnylink_keys()
  missing = [ui[p] for p in sorted(ui) if p not in sl]

  if args.all:
    print(f"# {len(ui)} fork settings defined by the on-device UI\n")
    for p in sorted(ui):
      print(f"  {'ok     ' if p in sl else 'MISSING'}  {p:42s} {ui[p]['widget']:16s} {ui[p]['source']}")
    print()

  mismatched = option_mismatches()
  if mismatched:
    print("=== CONTROLS WHOSE CHOICES DISAGREE WITH THE DEVICE ===")
    print("  Present is not the same as usable: a value the remote list does not contain renders")
    print("  as nothing selected at all.")
    print()
    for line in mismatched:
      print(f"    {line}")
    print()

  print(f"=== {len(ui) - len(missing)}/{len(ui)} fork settings reachable from SunnyLink, "
        f"{len(missing)} missing ===")
  if not missing:
    print("  Nothing to do: every setting this fork defines can be changed remotely.")
    return 0

  print("\n  These can only be changed by standing at the car, which is the problem on a comma 4:\n")
  for e in missing:
    print(f"    {e['param']:42s} {e['widget']:16s} {e['source']}")
  print("\n  YAML for sunnypilot/sunnylink/settings_ui_src/pages/*.yaml -- place each item in the")
  print("  section it belongs to and add its visibility/enablement conditions, then run:")
  print("      python sunnypilot/sunnylink/tools/compile_settings_ui.py\n")
  for e in missing:
    print(_yaml_block(e))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
