"""FusionPilot: a capnp ordinal is never freed, so the count can only ever go UP.

Pinned holds were deleted on 2026-09-25 -- he had asked four times not to have them, and
`IcbmPinnedHolds` read `[]` for the whole two months they shipped, so not one was ever created on
the car. Two pieces of that feature COULD NOT be deleted with it:

    pinSuggestion @7              in IntelligentCruiseButtonManagement
    BaselineSource.pinned @4      in its enum

capnp reads by POSITION. Both are in every route recorded on the device, so removing either
renumbers everything after it and makes stored drives decode out of the wrong bytes. The same
hazard is already written on `baselineSource @6` in custom.capnp: "a capnp field number cannot be
reused once retired anyway."

The trap is that deleting them LOOKS like exactly the cleanup this fork asks for everywhere else --
"when a reason expires, delete rather than park at neutral" -- and the suite stays green, because
nothing reads either one any more. The cost lands on route decoding, which no test touches.

THIS GUARDS ORDINALS, NOT NAMES, and that distinction is the whole design. CLAUDE.md's rule for the
holds -> SLA migration is *"Rename the field, never the ordinal"* -- so a test keyed on
`pinSuggestion` would block the very change this fork has planned, and the next person would edit
the test rather than think about it. A high-water mark cannot be satisfied that way: renaming keeps
the count, deleting does not.

Static on purpose: it asks what the SCHEMA says, so no stub can satisfy it and pycapnp never has to
compile anything.
"""
import pathlib
import re

CAPNP = pathlib.Path(__file__).resolve().parents[3] / "cereal" / "custom.capnp"

# Ordinals recorded as SHIPPED. Raise these when a field is added; never lower them.
# 0..7 are the ICBM struct through the retired `pinSuggestion @7`; the enum runs 0..4 through the
# retired `pinned @4`.
SHIPPED = {
  ("struct", "IntelligentCruiseButtonManagement"): 8,
  ("enum", "BaselineSource"): 5,
}


def _block(kind: str, name: str) -> str:
  src = CAPNP.read_text(encoding="utf-8")
  body = src.split(f"{kind} {name} {{", 1)[1]
  # stop at the first close-brace at the block's own indent; for the struct that is column 0
  depth, out = 1, []
  for ch in body:
    if ch == "{":
      depth += 1
    elif ch == "}":
      depth -= 1
      if depth == 0:
        break
    out.append(ch)
  return "".join(out)


def _ordinals(kind: str, name: str) -> list[int]:
  body = _block(kind, name)
  if kind == "struct":
    body = re.sub(r"enum \w+ \{.*?\n  \}", "", body, flags=re.DOTALL)  # nested enums number apart
  return sorted(int(n) for n in re.findall(r"@(\d+)\s*[:;]", body))


def test_no_ordinal_has_been_freed():
  """The high-water mark. A rename keeps it; a deletion does not, whether the field was in the
  middle of the block or at the end of it."""
  for (kind, name), shipped in SHIPPED.items():
    found = _ordinals(kind, name)
    assert len(found) >= shipped, (
      f"{kind} {name} declares {len(found)} ordinals, down from the {shipped} that have SHIPPED. "
      f"Something was deleted. Every one of those numbers is in routes already on the device, and "
      f"capnp reads by position -- freeing one shifts every field after it and makes stored drives "
      f"decode out of the wrong bytes. Retire in place, and rename if you must: an ordinal is "
      f"never reused. (`pinSuggestion @7` and `BaselineSource.pinned @4` are retired, not live.)"
    )


def test_the_ordinals_are_contiguous_from_zero():
  """A GAP does not raise a Python exception -- it calls abort(). The interpreter dies at import
  with exit 127 and no traceback, and `except` never runs. That took a whole suite down once, and
  the traceback named pytest and never mentioned a schema."""
  for kind, name in SHIPPED:
    found = _ordinals(kind, name)
    assert found == list(range(len(found))), (
      f"{kind} {name} ordinals are {found}, which is not 0..N. capnp requires them contiguous from "
      f"0 and ABORTS the interpreter on a gap -- exit 127, no traceback, no Python exception. If a "
      f"field was deleted, put it back and mark it retired instead of renumbering around it."
    )
