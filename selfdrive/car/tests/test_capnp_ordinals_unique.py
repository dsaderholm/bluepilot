"""FusionPilot: two fields must never claim the same capnp ordinal.

WHY THIS IS WORTH A STATIC TEST. capnp does not raise on a duplicate ordinal -- it calls abort().
The process dies at import, and the traceback names pytest's collection machinery with the schema
nowhere in it:

    Fatal Python error: Aborted
    File ".../cereal/__init__.py", line 9 in <module>
    ...
    Extension modules: capnp.lib.capnp

Every test in the suite disappears at once and nothing says why. On 2026-08-15 that cost a real
detour: two branches added a field to `CarStateBP` in different parts of the file, git merged both
cleanly because they never touched the same lines, and both landed on @4.

THAT IS THE SHAPE OF IT. A duplicate ordinal is not a conflict git can see. Two long-lived branches
each adding a message field is routine here, and the collision only exists in a numbering space
neither diff shows. So the guard has to read the numbers, not the diff.

WHICH FIELD MOVES, when it happens: the one with no wire history. A field already published is in
every route log on the device, and renumbering it makes those logs decode as whatever now holds the
number -- silently, since capnp reads by position. A field that has never run costs nothing to move.
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


def duplicates(path: pathlib.Path):
  """Every (scope, ordinal, names) where one scope declares an ordinal twice.

  Tracks brace depth so a nested struct gets its own numbering space -- they are independent, and a
  checker that flattened them would report false collisions on every file here.
  """
  stack: list[list] = []
  depth = 0
  found = []
  for n, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
    opened = re.match(r"\s*(struct|enum|union)\s+(\w+)", line)
    if opened and "{" in line:
      stack.append([opened.group(2), depth, {}])
    field = re.match(r"\s*(\w+)\s+@(\d+)\s*[:@]", line)
    if field and stack:
      num, name = int(field.group(2)), field.group(1)
      scope = stack[-1]
      if num in scope[2]:
        found.append((scope[0], num, scope[2][num], name, n))
      scope[2][num] = name
    depth += line.count("{") - line.count("}")
    while stack and depth <= stack[-1][1]:
      stack.pop()
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


def test_the_checker_actually_catches_one(tmp_path):
  """A guard that cannot fail is decoration. This is the exact shape that got through: two fields
  far apart in the file, in the same struct, sharing a number."""
  f = tmp_path / "x.capnp"
  f.write_text(
    "struct Thing {\n"
    "  alpha @0 :Bool;\n"
    "  blisLeft @4 :Bool;\n"
    "  struct Nested {\n"
    "    inner @4 :Bool;\n"
    "  }\n"
    "  accGap @4 :UInt8;\n"
    "}\n", encoding="utf-8")
  dupes = duplicates(f)
  assert len(dupes) == 1, f"expected exactly the Thing collision, got {dupes}"
  scope, num, first, second, _ = dupes[0]
  assert (scope, num, first, second) == ("Thing", 4, "blisLeft", "accGap")
