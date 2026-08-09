"""BluePilot: every capnp field the onroad UI reads has to exist.

Written after a whole drive was lost to one that did not. On 2026-08-08 the panel latched off four
minutes in and stayed off, and the reason was:

    AttributeError: struct has no such member; name = oncomingSeconds
      hud_renderer_bp.py, in _update_passing_assist
        left = pa.adjacentLeft.oncoming or pa.adjacentLeft.oncomingSeconds > 0

`oncomingSeconds` existed per side on AdjacentLaneSide in the model and was never published, so the
read was against a field the schema had never carried. Capnp resolves attributes at RUNTIME, so
nothing static caught it and 1353 tests passed over it.

WHY THE EXISTING GUARDS ALL MISSED IT, because each miss is a different lesson:

  - test_every_field_in_the_schema_is_actually_published walks PassingAssist's OWN fields and
    checks publish() assigns them. This field was in neither, and a parity test between two things
    that both omit a field is silent about it.
  - the panel preview calls _draw_passing_assist, which renders a PREPARED string. The crash is in
    _update_passing_assist, which prepares it. The preview never runs that.
  - nothing else exercises the UI offline at all.

So this starts from what the UI READS instead of what the schema declares.

EVERY MESSAGE, not just passingAssist. The first version of this file checked that one struct,
because that is where the crash was -- which would have left the same bug free to happen in the
blinker test, the lane-display walk, the TSR readout or the ICBM line, each of which the panel
reads the same way. An audit on 2026-08-08 found all of those clean; this keeps them that way.
"""

import ast
import pathlib

from cereal import custom, log

ROOT = pathlib.Path(__file__).resolve()
while not (ROOT / "common" / "params_keys.h").exists():
  ROOT = ROOT.parent

SOURCES = [
  ROOT / "selfdrive" / "ui" / "bp" / "onroad" / "hud_renderer_bp.py",
  ROOT / "selfdrive" / "ui" / "bp" / "onroad" / "adjacent_lane_renderer.py",
]

# Attributes every capnp reader has that are not schema fields.
NOT_FIELDS = {"schema", "which", "to_dict", "as_builder", "as_reader", "total_size", "copy"}


def _message(name: str):
  """A builder for a service by its SubMaster name.

  log.Event names its fields after the services, so it resolves openpilot's own messages. The
  fork's additions live on custom instead, hence the fallback.
  """
  try:
    # init(), not getattr: Event is a big union and reading an unset member raises.
    return log.Event.new_message().init(name)
  except Exception:  # noqa: BLE001 - not an Event field; try the fork's own schema
    return getattr(custom, name[0].upper() + name[1:]).new_message()


def _reads(src: str) -> dict[tuple[str, ...], set[str]]:
  """{("message", "nested", ...): fields read}.

  Paths are arbitrary depth, because the UI really is written in two hops --
  `pa = sm['longitudinalPlanSP'].passingAssist`, then `onc = pa.adjacentLeft`, then
  `onc.oncomingSeconds`. Modelling the path as a fixed (message, nested) pair could not express
  the third level, which is precisely where the field that crashed the panel lived.
  """
  tree = ast.parse(src)
  alias: dict[str, tuple[str, ...]] = {}

  def path_of(node) -> tuple[str, ...] | None:
    """The capnp path a node denotes, or None if it is not one."""
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "sm":
      try:
        return (ast.literal_eval(node.slice),)
      except (ValueError, SyntaxError):
        return None
    if isinstance(node, ast.Name):
      return alias.get(node.id)
    if isinstance(node, ast.Attribute):
      base = path_of(node.value)
      return base + (node.attr,) if base else None
    return None

  for n in ast.walk(tree):
    if isinstance(n, ast.Assign) and (pth := path_of(n.value)) is not None:
      for t in n.targets:
        if isinstance(t, ast.Name):
          alias[t.id] = pth

  found: dict[tuple[str, ...], set[str]] = {}
  for n in ast.walk(tree):
    if not isinstance(n, ast.Attribute) or n.attr in NOT_FIELDS:
      continue
    base = path_of(n.value)
    if base:
      found.setdefault(base, set()).add(n.attr)
  return found


def _all_reads() -> dict[tuple[str, ...], set[str]]:
  merged: dict[tuple[str, ...], set[str]] = {}
  for path in SOURCES:
    for k, v in _reads(path.read_text(encoding="utf-8")).items():
      merged.setdefault(k, set()).update(v)
  return merged


def test_every_field_the_onroad_ui_reads_exists():
  reads = _all_reads()
  assert len(reads) >= 6, f"the reader found almost nothing, so it is not checking anything: {reads}"

  missing = []
  for path, fields in sorted(reads.items(), key=str):
    label = ".".join(path)
    try:
      struct = _message(path[0])
      for hop in path[1:]:
        struct = getattr(struct, hop)
      # An ENUM is a legitimate leaf, and `.raw` on one is how the fork reads its integer value.
      # Walking into it is the checker's mistake, not a missing field -- see the note on int() vs
      # str() on capnp enums, which is a different bug in the same family.
      if not hasattr(struct, "schema"):
        continue
      declared = set(struct.schema.fieldnames)
    except Exception as e:  # noqa: BLE001 - an unresolvable path is itself the finding
      missing.append(f"{label}: could not resolve ({e})")
      continue
    for f in sorted(set(fields) - declared):
      missing.append(f"{label}.{f}")

  assert not missing, (
    "the onroad UI reads capnp fields that do not exist. Each one is an AttributeError at runtime "
    "that latches the panel off for the REST OF THE DRIVE -- only a reboot clears it, and a key "
    f"cycle does not: {missing}")


def test_the_field_that_caused_it_is_present():
  """A named regression. The check above would also pass if someone deleted the read instead of
  publishing the field, and the read is what makes the veto legible per side.
  oncomingSecondsLeft on the parent is NOT a substitute: it is the max across both sides, so it
  cannot say WHICH side the oncoming traffic was on."""
  pa = _message("longitudinalPlanSP").passingAssist
  assert "oncomingSeconds" in set(pa.adjacentLeft.schema.fieldnames)
  assert "oncomingSecondsLeft" in set(pa.schema.fieldnames), "the parent's max-of-both is not it"


def test_the_alias_hops_actually_resolve():
  """The checker is only worth having if it follows the two hops the UI really uses. If either
  stops resolving, the test above keeps passing while silently covering less -- which is the worst
  outcome and exactly the shape of bug this file exists to catch."""
  src = ("def f(sm):\n"
         "    pa = sm['longitudinalPlanSP'].passingAssist\n"
         "    onc = pa.adjacentLeft\n"
         "    return pa.notATopField, onc.notANestedField\n")
  reads = _reads(src)
  assert "notATopField" in reads[("longitudinalPlanSP", "passingAssist")]
  assert "notANestedField" in reads[("longitudinalPlanSP", "passingAssist", "adjacentLeft")]
