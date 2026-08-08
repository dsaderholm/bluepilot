"""BluePilot: `int()` on a capnp enum field is a crash no fixture can reach.

A capnp enum arrives as `capnp.lib.capnp._DynamicEnum`, and `int()` on it raises

    TypeError: int() argument must be a string, a bytes-like object or a real number,
               not 'capnp.lib.capnp._DynamicEnum'

Use `str(x)` for the enumerant name and `x.raw` for the integer.

THE REASON THIS NEEDS A STATIC CHECK is that no behavioral test can catch it. Every offline fixture
in this repo builds messages from `SimpleNamespace` with plain ints, and `int()` works on all of
them. The type that breaks only exists on a real message, so a green suite proves nothing here.

It has now happened twice from one root cause:

  - 2026-08-07, `tools/bp_dump_exit.py`: crashed after parsing an entire route, on
    `longitudinalPlanSource`. Fixed in 5f2e30f63.
  - 2026-08-08, `hud_renderer_bp.py` on the passing-assist branch: crashed the drive-summary panel
    ON THE ROAD, on `driverPassMissReason`, two commits after reading and verifying the fix above.

Reading the fix did not prevent the second one. A test does.

This walks the SCHEMA rather than a hardcoded list of field names, so a new enum field is covered
the day it is declared. Its counterpart on the passing-assist branch covers that branch's UI files;
this one covers what this branch owns.
"""
from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[3]

# Schemas whose enum fields our code reads.
SCHEMAS = (
  REPO / "cereal" / "custom.capnp",
  REPO / "cereal" / "log.capnp",
  REPO / "opendbc_repo" / "opendbc" / "car" / "car.capnp",
)

# Files this branch owns that read capnp messages. The passing-assist branch has its own list for
# its own files; keep them separate so neither branch's test fails on the other's code.
COVERED = (
  "sunnypilot/selfdrive/car/intelligent_cruise_button_management",
  "sunnypilot/selfdrive/controls/lib",
  "sunnypilot/selfdrive/selfdrived",
  "selfdrive/ui/bp/onroad",
  "selfdrive/car",
  "tools",
)

_ENUM_DECL = re.compile(r"^\s*enum\s+([A-Za-z_]\w*)\s*\{", re.M)
# `state @0 :OverrideState;` and `sources @2 :List(LongitudinalPlanSource);`
_FIELD_DECL = re.compile(r"^\s*([a-z]\w*)\s*@\d+\s*:\s*(?:List\()?([A-Za-z_]\w*)", re.M)


def _enum_field_names() -> set[str]:
  """Field names in these schemas whose type is an enum declared in the same file."""
  names: set[str] = set()
  for schema in SCHEMAS:
    if not schema.exists():
      continue
    text = schema.read_text(encoding="utf-8", errors="replace")
    enums = set(_ENUM_DECL.findall(text))
    for field, type_name in _FIELD_DECL.findall(text):
      if type_name in enums:
        names.add(field)
  return names


def _sources() -> list[pathlib.Path]:
  out: list[pathlib.Path] = []
  for rel in COVERED:
    root = REPO / rel
    if not root.exists():
      continue
    out.extend(p for p in root.rglob("*.py")
               if "tests" not in p.parts and not p.name.startswith("test_"))
  return out


def test_no_int_on_a_capnp_enum_field():
  fields = _enum_field_names()
  assert fields, "parsed no enum-typed fields; the schema regexes have gone stale"
  # int(<anything>.<enumField>) -- the accessor chain in front does not matter, the attribute does.
  pattern = re.compile(r"\bint\(\s*[\w.\[\]()]*\.(" + "|".join(sorted(fields)) + r")\b")

  offenders = []
  for path in _sources():
    for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
      if line.lstrip().startswith("#"):
        continue
      m = pattern.search(line)
      if m:
        offenders.append(f"{path.relative_to(REPO).as_posix()}:{n}: int() on enum "
                         f"field '{m.group(1)}' -- use str(x) for the name, x.raw for the integer")

  assert not offenders, (
    "int() on a capnp enum raises TypeError on the device and CANNOT fail offline, because every "
    "fixture here builds messages from SimpleNamespace with plain ints:\n  " + "\n  ".join(offenders))


def test_the_check_would_actually_catch_one():
  """A guard that catches nothing is worse than none, and this one is all regex.

  Pins that the pattern matches the two real crash sites verbatim, so a regex that silently stops
  matching -- a renamed field, an escaping slip -- fails here instead of going quietly green.
  """
  fields = _enum_field_names()
  assert "longitudinalPlanSource" in fields, "custom.capnp's plan source is no longer seen as an enum"
  pattern = re.compile(r"\bint\(\s*[\w.\[\]()]*\.(" + "|".join(sorted(fields)) + r")\b")

  assert pattern.search("  src = int(msg.longitudinalPlanSP.longitudinalPlanSource)")
  assert pattern.search("      miss = int(pa.driverPassMissReason)") or \
      "driverPassMissReason" not in fields, "passing-assist field known but not matched"
  assert not pattern.search("  n = int(self._content_rect.width)"), "matches ordinary geometry"
  assert not pattern.search("  v = str(msg.carControlSP.icbm.state)"), "str() must not be flagged"
