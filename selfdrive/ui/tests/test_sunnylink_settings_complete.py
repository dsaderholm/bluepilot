"""Every setting this fork defines must be reachable from SunnyLink, not just from the car's screen.

Asked for 2026-08-12, for comma 4 compatibility. The comma 4 screen is small enough that configuring 32
fork settings on it is impractical, so SunnyLink is the real settings surface on that hardware --
which makes "big-screen only" a shipping defect rather than a nicety.

This is the same rule the fork already applies on-device: a param with no control has not shipped,
because a feature nobody can turn on gets reported as broken. `IcbmModelStopEnabled` was exactly that
for weeks. On a comma 4, a param with no SunnyLink entry is the same failure with a different cause.

It fails LOUDLY and names the missing keys, because the fix is mechanical and the tool prints it:

    python tools/bp_sunnylink_settings_audit.py

Then place each item in the right page section and re-run
`python sunnypilot/sunnylink/tools/compile_settings_ui.py`.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[3]
AUDIT = ROOT / "tools/bp_sunnylink_settings_audit.py"
SETTINGS_UI_JSON = ROOT / "sunnypilot/sunnylink/settings_ui.json"
SETTINGS_UI_SCHEMA = ROOT / "sunnypilot/sunnylink/settings_ui.schema.json"


def _audit():
  spec = importlib.util.spec_from_file_location("bp_sunnylink_settings_audit", AUDIT)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def test_every_fork_setting_is_reachable_from_sunnylink():
  audit = _audit()
  ui = audit.collect_ui_settings()
  assert len(ui) > 20, f"only found {len(ui)} fork settings; the AST extractor has probably broken"

  missing = audit.missing_settings()
  assert not missing, (
    "these settings can only be changed by standing at the car, which does not work on a comma 4:\n"
    + "\n".join(f"  {e['param']}  ({e['widget']}, from {e['source']})" for e in missing)
    + "\n\nRun: python tools/bp_sunnylink_settings_audit.py  -- it prints the YAML to paste."
  )


def test_compiled_settings_match_the_authoring_tree():
  """settings_ui.json is GENERATED from settings_ui_src. A hand-edit here is silently lost."""
  compile_tool = ROOT / "sunnypilot/sunnylink/tools/compile_settings_ui.py"
  assert compile_tool.exists(), "the compiler is gone; settings_ui.json is no longer generated"

  data = json.loads(SETTINGS_UI_JSON.read_text(encoding="utf-8"))
  keys: list[str] = []
  _control_keys(data, keys)

  # Only OUR params. Upstream deliberately surfaces some of theirs on more than one page -- Mads and
  # a dozen others -- and that is their layout decision, not a defect for this fork to police.
  ours = set(_audit().collect_ui_settings())
  dupes = {k for k in keys if k in ours and keys.count(k) > 1}
  assert not dupes, (
    f"this fork offers the same param in two places, so the two controls disagree: {sorted(dupes)}")


def _control_keys(o, out: list[str]) -> None:
  """Keys of actual CONTROLS.

  A bare `key` is not enough: rules carry one too (`{type: param, key: Mads, equals: true}`), and a
  param that gates three other items legitimately appears four times. Only a dict carrying a
  `widget` is a control the user can operate.
  """
  if isinstance(o, dict):
    if isinstance(o.get("key"), str) and "widget" in o:
      out.append(o["key"])
    for v in o.values():
      _control_keys(v, out)
  elif isinstance(o, list):
    for v in o:
      _control_keys(v, out)


def test_numeric_settings_carry_a_usable_range():
  """An `option` with no range renders as a control the user cannot move."""
  data = json.loads(SETTINGS_UI_JSON.read_text(encoding="utf-8"))
  audit = _audit()
  ours = set(audit.collect_ui_settings())
  bad: list[str] = []

  def walk(o) -> None:
    if isinstance(o, dict):
      if o.get("widget") == "option" and o.get("key") in ours and "options" not in o:
        lo, hi, step = o.get("min"), o.get("max"), o.get("step")
        if not all(isinstance(v, int | float) for v in (lo, hi, step)) or hi <= lo or step <= 0:
          bad.append(f"{o['key']}: min={lo} max={hi} step={step}")
      for v in o.values():
        walk(v)
    elif isinstance(o, list):
      for v in o:
        walk(v)

  walk(data)
  assert not bad, "numeric settings with an unusable range:\n" + "\n".join(f"  {b}" for b in bad)


def test_compiled_settings_validate_against_their_schema():
  """The frontend reads this file; a schema violation is a broken settings screen, not a warning."""
  jsonschema = __import__("jsonschema") if _has_jsonschema() else None
  if jsonschema is None:
    import pytest
    pytest.skip("jsonschema not installed in this environment")
  jsonschema.validate(
    json.loads(SETTINGS_UI_JSON.read_text(encoding="utf-8")),
    json.loads(SETTINGS_UI_SCHEMA.read_text(encoding="utf-8")),
  )


def _has_jsonschema() -> bool:
  return importlib.util.find_spec("jsonschema") is not None
