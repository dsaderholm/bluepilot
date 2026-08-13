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

WHAT THIS DOES NOT CHECK, established 2026-08-12 by probing it rather than reading the count. Both
sides of the comparison are "settings the on-device UI defines", so anything outside that net cannot
be reported missing and a green 37/37 says nothing about it:

- **A param with NO control at all is invisible.** It is absent from both sides, so it cancels out.
  Such a param is not automatically a gap: `BPSentryEnabled` is one, and it is a KILL SWITCH that
  must never be remote -- see `DELIBERATELY_NOT_REMOTE` below, which is the authoritative answer for
  it. The blind spot is still worth knowing, because the next such param might be a real setting and
  this tool will not say so.
- **`param=` must be a literal.** visuals.py builds its toggles in a loop over `_toggle_defs`, so
  every one is skipped. All eleven are upstream display prefs, which CLAUDE.md leaves alone anyway.
- **Only `ITEM_CALLS` widgets count.** `button_item_sp` and `dual_button_item_sp` are in use and not
  in that map; no fork param uses either today, so nothing is hidden by it right now.
- **Only the two `UI_DIRS` are scanned** -- deliberately, since mici gets no screens for our stuff.

Checked and clean as of 2026-08-12: the three fork-prefixed params with no control
(`IcbmHoldObservations`, `IcbmPinnedHolds`, `IcbmPinHoldRequest`) are learned data and a transient
onroad-tap request, not settings, so their absence is correct.

There is deliberately no test for the first bullet. It needs "fork-added" rather than "matches a
prefix" -- 6 of the 40 prefix-matching keys are upstream's own -- and the only offline way to get
that is diffing params_keys.h against upstream/bp-7.0. A suite that turns red because UPSTREAM added
a `SpeedLimit*` key teaches people to ignore a red suite, which costs more than this catches.

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
OUR_PREFIXES = ("Icbm", "SmartCruiseControl", "SpeedLimit", "PassingAssist", "RadarDetector")

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

ITEM_CALLS = {
  "toggle_item_sp": "toggle",
  "option_item_sp": "option",
  "multiple_button_item_sp": "multiple_button",
  "simple_button_item_sp": "button",
}


def _literal(node) -> object | None:
  """Best-effort literal from an AST node, seeing through tr(...) and string concatenation."""
  if isinstance(node, ast.Constant):
    return node.value
  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
    # tr(...) is translation, recommended(...) appends the shipped default at display time. Both
    # wrap the real string in their first argument, and both must be seen through or every
    # description here comes back empty.
    if node.func.id in ("tr", "recommended") and node.args:
      return _literal(node.args[0])
  if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
    left, right = _literal(node.left), _literal(node.right)
    if isinstance(left, str) and isinstance(right, str):
      return left + right
  if isinstance(node, ast.JoinedStr):
    return None
  if isinstance(node, ast.Lambda):
    return None
  if isinstance(node, ast.List):
    vals = [_literal(e) for e in node.elts]
    return vals if all(v is not None for v in vals) else None
  if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
    inner = _literal(node.operand)
    return -inner if isinstance(inner, int | float) else None
  return None


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
      for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
          continue
        widget = ITEM_CALLS.get(node.func.id)
        if widget is None:
          continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        param = _literal(kw.get("param"))
        if not isinstance(param, str) or param in DELIBERATELY_NOT_REMOTE:
          continue
        if not param.startswith(OUR_PREFIXES):
          continue
        entry = {
          "param": param,
          "widget": widget,
          "title": _literal(kw.get("title")),
          "description": _literal(kw.get("description")),
          "min": _literal(kw.get("min_value")),
          "max": _literal(kw.get("max_value")),
          "step": _literal(kw.get("value_change_step")) or DEFAULT_OPTION_STEP,
          "buttons": _literal(kw.get("buttons")),
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
