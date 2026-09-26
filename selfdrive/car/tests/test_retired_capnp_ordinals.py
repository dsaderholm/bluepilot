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
the test rather than think about it. A count cannot be satisfied that way: renaming keeps it,
deleting does not.

IT READS THE COMPILED SCHEMA, NOT THE TEXT, and the first version of this file is why that is
written down. It hand-rolled a brace counter and a regex over custom.capnp, and hand-typed the
count -- which came out as 8 against a real 13, because it was derived from "up to the retired
field" rather than from what had shipped. Four LIVE trailing fields (`vTargetRaw`,
`baselineDiverged`, `vSlaTarget`, `speedLimitLive`) could then be deleted one at a time with the
guard green, and those are the exact diagnostics CLAUDE.md records as hard-won. pycapnp already
knows the ordinals, handles groups, unions, defaults and nested structs, and cannot be hand-typed
wrong.
"""
from cereal import custom

ICBM = custom.IntelligentCruiseButtonManagement

# EXACT, not a floor. Adding a field must fail here and be bumped deliberately -- a `>=` silently
# widens the gap between what has shipped and what is defended every time somebody adds one, which
# is how the first version of this test ended up defending five fewer fields than existed.
#
# 0..7 runs through the retired `pinSuggestion @7`; 8..12 are the live diagnostics after it.
# The enum is 0..4 through the retired `pinned @4`.
SHIPPED_STRUCT_ORDINALS = 13
SHIPPED_ENUM_ORDINALS = 5

_HAZARD = (
  "Every one of those numbers is in routes already on the device, and capnp reads by POSITION -- "
  "freeing one shifts every field after it and makes stored drives decode out of the wrong bytes. "
  "Retire in place, and rename if you must: an ordinal is never reused. (`pinSuggestion @7` and "
  "`BaselineSource.pinned @4` are retired, not live -- deleting them is the tempting mistake.)"
)


def _struct_ordinals() -> list[int]:
  return sorted(f.codeOrder for f in ICBM.schema.node.struct.fields)


def _enum_ordinals() -> list[int]:
  return sorted(ICBM.BaselineSource.schema.enumerants.values())


def test_the_struct_still_declares_every_ordinal_that_has_shipped():
  found = _struct_ordinals()
  assert len(found) == SHIPPED_STRUCT_ORDINALS, (
    f"IntelligentCruiseButtonManagement declares {len(found)} ordinals; "
    f"{SHIPPED_STRUCT_ORDINALS} have shipped. {_HAZARD} If you ADDED a field, raise "
    f"SHIPPED_STRUCT_ORDINALS to {len(found)} in this file -- deliberately, having checked the "
    f"count went UP."
  )


def test_the_enum_still_declares_every_ordinal_that_has_shipped():
  found = _enum_ordinals()
  assert len(found) == SHIPPED_ENUM_ORDINALS, (
    f"BaselineSource declares {len(found)} enumerants; {SHIPPED_ENUM_ORDINALS} have shipped. "
    f"{_HAZARD} An enumerant is a wire value like any other: routes on the device carry `4`."
  )


def test_the_ordinals_are_contiguous_from_zero():
  """A GAP does not raise a Python exception -- it calls abort(). The interpreter dies at import
  with exit 127 and no traceback, and `except` never runs. That took a whole suite down once, and
  the traceback named pytest and never mentioned a schema. Reaching this assert at all means the
  schema compiled, so this is really a guard on the SOURCE staying sane for the next reader."""
  for name, found in (("IntelligentCruiseButtonManagement", _struct_ordinals()),
                      ("BaselineSource", _enum_ordinals())):
    assert found == list(range(len(found))), (
      f"{name} ordinals are {found}, which is not 0..N. capnp requires them contiguous from 0. If "
      f"a field was deleted, put it back and mark it retired instead of renumbering around it."
    )


def test_the_retired_ordinals_are_where_the_routes_expect_them():
  """The counts above cannot tell a retirement from a swap: deleting `pinSuggestion @7` and adding
  a new field at @7 keeps the count, stays contiguous, and silently redefines what every recorded
  drive decodes at that position. This pins the two retired SLOTS by type, which is what the bytes
  on disk actually are -- not by name, so the planned rename is still free."""
  by_ordinal = {f.codeOrder: f for f in ICBM.schema.node.struct.fields}
  assert by_ordinal[7].slot.type.which() == "float32", (
    f"ordinal 7 is now {by_ordinal[7].name!r} of type {by_ordinal[7].slot.type.which()!r}, not the "
    f"Float32 that `pinSuggestion` occupied. {_HAZARD}"
  )
  assert by_ordinal[6].slot.type.which() == "enum", (
    "ordinal 6 is no longer `baselineSource`'s enum -- the BaselineSource enumerants this file "
    "also guards are only meaningful while that field still reads them."
  )
