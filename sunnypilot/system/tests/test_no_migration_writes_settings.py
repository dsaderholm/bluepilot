"""FusionPilot: no migration may write a value to a setting the owner controls.

The rule that survived, decided 2026-08-08: *"I want to go back to how upstream does it."* There is
no defaults migration on this fork any more -- `_migrate_bp_redefaulted`, `_BP_REDEFAULTED` and
`BP_DEFAULTS_GENERATION` were all deleted with the other 272 lines, and a changed default is a
RECOMMENDATION he applies rather than anything that reaches his car. A migration that PUTS a value
is either overruling him or creating a second source of truth for a default.

So this test now guards the whole category rather than one delivery mechanism: nothing in
params_migration.py may write a settings key, by any means.

This exists because the migration that broke the rule came BACK. It was deleted on 2026-08-05, and
a later rebase conflict resolved "keep both sides" and restored the whole function -- unreferenced,
so nothing failed, and it sat there one accidental call away from setting his display toggles again.
Static, so it costs nothing to keep forever.
"""

import ast
import pathlib


def _root() -> pathlib.Path:
  for d in pathlib.Path(__file__).resolve().parents:
    if (d / "common" / "params_keys.h").exists():
      return d
  raise RuntimeError("repo root not found")


SRC = (_root() / "sunnypilot" / "system" / "params_migration.py").read_text(encoding="utf-8")
TREE = ast.parse(SRC)

# Keys a driver sets and a migration must never assign. Markers, and keys carried forward by a
# rename, are deliberately absent -- those preserve intent rather than override it.
FORBIDDEN = {
  "ShowPassingAssist", "ShowAdjacentLanes", "ShowOncomingSpeeds", "ShowBrakeStatus",
  "PassingAssistKeepRight", "PassingAssistMinApproach", "PassingAssistLogEnabled",
  "PassingAssistChime", "PassingAssistMinDeficit", "PassingAssistMinSpeed",
  "FordLowSpeedFactor_ang", "FordHighSpeedFactor_ang", "FordHighSpeedDampening_ang",
  "FordPrefLateralControl", "SpeedLimitMode", "SpeedLimitOffsetType",
}


def _put_targets():
  """Every literal key passed to params.put / put_bool anywhere in the module."""
  out = []
  for node in ast.walk(TREE):
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("put", "put_bool") and node.args
        and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
      out.append(node.args[0].value)
  return out


def test_the_scan_finds_the_writes_that_are_allowed():
  """A scan that matches nothing would make the test below pass on anything."""
  targets = _put_targets()
  assert targets, "no put() calls found at all -- the pattern has drifted"
  assert any("Migrated" in t or "Generation" in t for t in targets), (
    "no marker writes found; migrations do write their own markers")


def test_no_migration_assigns_a_setting_the_owner_controls():
  bad = sorted(set(_put_targets()) & FORBIDDEN)
  assert not bad, (
    "params_migration writes settings the owner controls: " + ", ".join(bad) +
    "\nChanged defaults are delivered by CLEARING the key, never by writing a value.")


def test_no_settings_dict_is_left_lying_around():
  """The function that broke the rule came back through a rebase as dead code. A dict of settings
  in this module is the shape of that mistake even when nothing calls it."""
  for node in ast.walk(TREE):
    if isinstance(node, ast.Dict):
      keys = {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
      assert not (keys & FORBIDDEN), (
        f"a dict of owner settings is defined here: {sorted(keys & FORBIDDEN)}")
