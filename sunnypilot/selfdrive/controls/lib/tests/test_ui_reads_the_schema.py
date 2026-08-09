"""BluePilot: every capnp field the panel reads has to exist.

Written after a whole drive was lost to one that did not. On 2026-08-08 the panel latched off
four minutes in and stayed off, and the reason was:

    AttributeError: struct has no such member; name = oncomingSeconds
      hud_renderer_bp.py, in _update_passing_assist
        left = pa.adjacentLeft.oncoming or pa.adjacentLeft.oncomingSeconds > 0

`oncomingSeconds` existed on AdjacentLaneSide in the model and was never published, so the read was
against a field the schema had never carried. Capnp resolves attributes at RUNTIME, so this is not
a NameError anything static would catch, and 1353 tests passed over it.

WHY THE EXISTING GUARDS ALL MISSED IT, because each miss is a different lesson:

  - test_every_field_in_the_schema_is_actually_published walks PassingAssist's OWN fields and
    checks publish() assigns them. This field was in neither, so there was nothing to compare.
    A parity test between two things that both omit a field is silent about it.
  - The panel preview calls _draw_passing_assist, which renders a prepared string. The crash is in
    _update_passing_assist, which PREPARES it. The preview never runs that.
  - Nothing else exercises the UI offline at all.

So the check has to start from what the UI READS rather than from what the schema declares, and it
has to follow the nested structs, because that is where the missing field was.
"""

import ast
import pathlib

from cereal import custom

ROOT = pathlib.Path(__file__).resolve()
while not (ROOT / "common" / "params_keys.h").exists():
  ROOT = ROOT.parent

# The panel and the on-road overlay. Both read passingAssist directly.
SOURCES = [
  ROOT / "selfdrive" / "ui" / "bp" / "onroad" / "hud_renderer_bp.py",
  ROOT / "selfdrive" / "ui" / "bp" / "onroad" / "adjacent_lane_renderer.py",
]

# Attributes that exist on a capnp reader but are not schema fields.
NOT_FIELDS = {"schema", "which", "to_dict", "as_builder", "as_reader", "total_size", "copy"}


def _schema_of(struct):
  return set(struct.schema.fieldnames)


def _passing_assist():
  return custom.LongitudinalPlanSP.new_message().passingAssist


def _reads(src: str) -> tuple[set[str], set[str]]:
  """(top-level fields read as pa.X, nested fields read off an AdjacentLane).

  Aliases are followed one hop -- `onc = pa.adjacentLeft` then `onc.oncomingDRel` is how the
  oncoming readout is actually written, and a checker that only understood `pa.a.b` would pass
  over the very block the crash was in.
  """
  tree = ast.parse(src)
  top: set[str] = set()
  nested: set[str] = set()
  aliases: set[str] = set()

  for node in ast.walk(tree):
    # onc = pa.adjacentLeft  /  side = pa.adjacentRight
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Attribute):
      v = node.value
      if isinstance(v.value, ast.Name) and v.value.id == "pa" and v.attr.startswith("adjacent"):
        for t in node.targets:
          if isinstance(t, ast.Name):
            aliases.add(t.id)
    # for dest, side in ((pa.adjacentLeft, ...), ...)
    if isinstance(node, ast.For) and isinstance(node.target, ast.Tuple):
      if "adjacentLeft" in ast.unparse(node.iter):
        for t in node.target.elts:
          if isinstance(t, ast.Name):
            aliases.add(t.id)

  for node in ast.walk(tree):
    if not isinstance(node, ast.Attribute):
      continue
    base = node.value
    if isinstance(base, ast.Name) and base.id == "pa" and node.attr not in NOT_FIELDS:
      top.add(node.attr)
    elif isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name) \
            and base.value.id == "pa" and base.attr.startswith("adjacent"):
      if node.attr not in NOT_FIELDS:
        nested.add(node.attr)
    elif isinstance(base, ast.Name) and base.id in aliases and node.attr not in NOT_FIELDS:
      nested.add(node.attr)
  return top, nested


def test_every_field_the_panel_reads_exists_in_the_schema():
  pa = _passing_assist()
  declared = _schema_of(pa)
  adjacent = _schema_of(pa.adjacentLeft)
  assert len(declared) > 80 and len(adjacent) > 10, "schema reflection returned too little"

  missing: list[str] = []
  for path in SOURCES:
    top, nested = _reads(path.read_text(encoding="utf-8"))
    missing += [f"{path.name}: pa.{f}" for f in sorted(top - declared)]
    missing += [f"{path.name}: pa.adjacent*.{f}" for f in sorted(nested - adjacent)]

  assert not missing, (
    "the panel reads capnp fields that do not exist -- each one is an AttributeError at runtime "
    f"that latches the whole panel off for the rest of the drive: {missing}")


def test_the_field_that_caused_it_is_present():
  """A named regression, because the general check above would also pass if someone deleted the
  read instead of publishing the field -- and the read is the thing that makes the veto legible
  per side. oncomingSecondsLeft on the parent is NOT a substitute: it is the max across both
  sides, so it cannot say WHICH side the oncoming traffic was on."""
  pa = _passing_assist()
  assert "oncomingSeconds" in _schema_of(pa.adjacentLeft)
  assert "oncomingSecondsLeft" in _schema_of(pa), "the parent's max-of-both is a different field"


def test_the_alias_hop_actually_works():
  """The checker is only worth having if it follows `onc = pa.adjacentLeft`. If this stops
  resolving, the test above keeps passing while covering less, which is the worst outcome."""
  src = "def f(pa):\n    onc = pa.adjacentLeft\n    return onc.notARealField\n"
  _, nested = _reads(src)
  assert "notARealField" in nested
