"""FusionPilot: capnp field numbering must be unique AND contiguous, per scope.

WHY THIS IS WORTH A STATIC TEST. capnp does not raise on a bad ordinal -- it kills the process.
A duplicate calls abort(); a HOLE exits 127 with no Python-level exception at all, so `except` never
runs and nothing can report it. Either way every test in the suite disappears at once behind a
traceback that names pytest's collection machinery and never mentions a schema:

    Fatal Python error: Aborted
    File ".../cereal/__init__.py", line 9 in <module>
    ...
    Extension modules: capnp.lib.capnp

THE SHAPE THAT GETS THROUGH. On 2026-08-15 two branches each added a field to `CarStateBP` in
different parts of the file. Git merged both cleanly -- they never touched the same lines -- and
both landed on @4. A duplicate ordinal is not a conflict git can see. Two long-lived branches each
adding a message field is routine here, and the collision exists only in a numbering space no diff
shows.

AND ITS MOST LIKELY REPAIR IS THE OTHER FAILURE. Fixing a collision means moving a field, and moving
one carelessly leaves a hole where it was. So the check that catches the collision has to catch the
repair too, which is why gaps are here rather than in a separate test.

WHICH FIELD MOVES, when it happens: the one with NO WIRE HISTORY. A field already published is in
every route log on the device, and capnp reads by position -- renumbering it makes those logs decode
as whatever now holds the number, silently. A field that has never run costs nothing to move.

AND IT RECURS. capnp ordinals must be contiguous from 0, so a branch whose CarStateBP ends at @3
can only ever write @4; the higher number is unreachable there until the intervening fields exist,
which happens at rebase. The renumber is therefore a rebase-time operation, not a one-off fix on
the other branch, and it will need doing again each time.
"""
import pathlib
import re

import pytest

CAPNP_FILES = ("cereal/custom.capnp", "cereal/log.capnp", "cereal/car.capnp")


def _root() -> pathlib.Path:
  for d in pathlib.Path(__file__).resolve().parents:
    if (d / "common" / "params_keys.h").exists():
      return d
  raise AssertionError("repo root not found")


def scopes(path: pathlib.Path):
  """Every declaring scope, as (name, opening line, {ordinal: [(field, line), ...]}).

  Tracks brace depth so a nested struct gets its own numbering space -- they are independent, and a
  checker that flattened them would report a false collision on nearly every file here.
  """
  stack: list[list] = []
  depth = 0
  out = []
  for n, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
    opened = re.match(r"\s*(struct|enum|union)\s+(\w+)", line)
    if opened and "{" in line:
      entry = [opened.group(2), depth, {}, n]
      stack.append(entry)
      out.append(entry)
    field = re.match(r"\s*(\w+)\s+@(\d+)\s*[:@]", line)
    if field and stack:
      stack[-1][2].setdefault(int(field.group(2)), []).append((field.group(1), n))
    depth += line.count("{") - line.count("}")
    while stack and depth <= stack[-1][1]:
      stack.pop()
  return out


def duplicates(path: pathlib.Path):
  """(scope, ordinal, first, second, line) for every ordinal declared twice in one scope."""
  found = []
  for name, _, fields, _ in scopes(path):
    for num, decls in fields.items():
      for extra in decls[1:]:
        found.append((name, num, decls[0][0], extra[0], extra[1]))
  return found


def gaps(path: pathlib.Path):
  """(scope, missing ordinal, opening line) for numbering that is not contiguous from 0."""
  found = []
  for name, _, fields, line in scopes(path):
    if not fields:
      continue
    for want in range(max(fields) + 1):
      if want not in fields:
        found.append((name, want, line))
  return found


@pytest.mark.parametrize("rel", CAPNP_FILES)
def test_no_two_fields_share_an_ordinal(rel):
  path = _root() / rel
  if not path.exists():
    pytest.skip(f"{rel} not in this tree")
  dupes = duplicates(path)
  assert not dupes, "\n".join(
    f"{rel}:{ln} struct {scope} declares @{num} twice: {first} and {second}"
    for scope, num, first, second, ln in dupes)


@pytest.mark.parametrize("rel", CAPNP_FILES)
def test_no_scope_has_a_hole_in_its_numbering(rel):
  path = _root() / rel
  if not path.exists():
    pytest.skip(f"{rel} not in this tree")
  holes = gaps(path)
  assert not holes, "\n".join(
    f"{rel}:{ln} struct {scope} is missing @{num} -- capnp exits 127 on this, with no exception"
    for scope, num, ln in holes)


def test_the_checker_catches_a_collision(tmp_path):
  """A guard that cannot fail is decoration. This is the exact shape that got through: two fields
  far apart in the file, in the same struct, sharing a number -- and a nested struct that
  legitimately reuses it."""
  f = tmp_path / "x.capnp"
  f.write_text(
    "struct Thing {\n"
    "  alpha @0 :Bool;\n"
    "  blisLeft @1 :Bool;\n"
    "  struct Nested {\n"
    "    inner @0 :Bool;\n"
    "  }\n"
    "  accGap @1 :UInt8;\n"
    "}\n", encoding="utf-8")
  dupes = duplicates(f)
  assert len(dupes) == 1, f"expected exactly the Thing collision, got {dupes}"
  scope, num, first, second, _ = dupes[0]
  assert (scope, num, first, second) == ("Thing", 1, "blisLeft", "accGap")
  assert not gaps(f), "the nested struct's own @0 was mistaken for a hole"


def test_the_checker_catches_a_hole(tmp_path):
  """What a careless renumber leaves behind. Exit 127, no exception, nothing to catch it but this."""
  f = tmp_path / "y.capnp"
  f.write_text(
    "struct Thing {\n"
    "  alpha @0 :Bool;\n"
    "  gamma @2 :Bool;\n"
    "}\n", encoding="utf-8")
  holes = gaps(f)
  assert [(s, n) for s, n, _ in holes] == [("Thing", 1)]
  assert not duplicates(f)
