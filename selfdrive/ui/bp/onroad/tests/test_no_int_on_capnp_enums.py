"""FusionPilot: never int() a capnp enum read off a live message.

It is a `_DynamicEnum`, and int() raises TypeError on it. That crashed the passing-assist panel on
the road on 2026-08-07 --

    File "hud_renderer_bp.py", line 592, in _draw_drive_summary
      miss = int(pa.driverPassMissReason)
    TypeError: int() argument must be ... not 'capnp.lib.capnp._DynamicEnum'

-- and because _draw_drive_summary only runs at a standstill it took a red light to reach. The panel
then latched off for the rest of the drive: "then I just got passing assist error for the rest of my
drive after stopping at a red light."

NO TEST COULD HAVE FAILED. Every offline fixture builds passingAssist out of SimpleNamespace with
plain ints and strings, so int() works on all of them; the type that breaks only exists on a real
message. So this asserts against the SCHEMA and the SOURCE rather than through a fixture.

The ICBM session hit the identical TypeError in bp_dump_exit.py two commits earlier. Same mistake,
different file, and the lesson was not carried across -- which is the actual reason this file exists.
"""

import pathlib
import re

from cereal import custom


def _root():
  p = pathlib.Path(__file__).resolve()
  while not (p / "common" / "params_keys.h").exists():
    p = p.parent
  return p


def _enum_fields() -> set[str]:
  """Every PassingAssist field whose type is an enum, straight from the schema."""
  schema = custom.LongitudinalPlanSP.PassingAssist.schema
  names = set()
  for name, field in schema.fields.items():
    try:
      if "enum" in str(field.proto.slot.type.which()):
        names.add(name)
    except Exception:  # noqa: BLE001 - non-slot fields (groups, unions) are not enums
      pass
  return names


def test_the_schema_actually_has_enum_fields():
  """If this ever returns nothing the guard below passes vacuously, which is worse than no guard."""
  fields = _enum_fields()
  assert fields, "no enum fields found -- the schema walk is broken, not the code"
  assert "driverPassMissReason" in fields, fields


def test_no_ui_code_calls_int_on_one():
  """str() gives the enumerant name directly, which is what every one of these call sites wanted --
  the int() and the index lookup were work to recover something capnp already had."""
  ui = _root() / "selfdrive" / "ui" / "bp" / "onroad" / "hud_renderer_bp.py"
  src = ui.read_text(encoding="utf-8")
  bad = [f for f in _enum_fields()
         if re.search(rf"\bint\(\s*\w+\.{re.escape(f)}\b", src)]
  assert not bad, (
    f"int() applied to capnp enum field(s) {sorted(bad)} in hud_renderer_bp.py. On a live message "
    "these are _DynamicEnum and int() raises TypeError, which latches the panel off for the rest "
    "of the drive. Use str() -- it gives the enumerant name.")


def test_a_real_capnp_enum_does_reject_int():
  """The premise, asserted rather than assumed. If capnp ever makes _DynamicEnum int()-able this
  whole guard is unnecessary and should be deleted rather than left as folklore."""
  import pytest

  msg = custom.LongitudinalPlanSP.new_message()
  value = msg.passingAssist.driverPassMissReason
  assert str(value)
  with pytest.raises(TypeError):
    int(value)
