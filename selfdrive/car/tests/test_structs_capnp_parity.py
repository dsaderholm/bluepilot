#!/usr/bin/env python3
"""FusionPilot: guard the hand-maintained dataclass mirrors in opendbc against custom.capnp.

Several structs in cereal/custom.capnp have a Python dataclass twin in
opendbc/car/structs.py, and selfdrive/car/helpers.py converts capnp -> dataclass by splatting
the message dict straight into the constructor:

    structs.IntelligentCruiseButtonManagement(**remove_deprecated(struct_dict.get(...)))

So a field added to the schema without the matching field on the dataclass is a TypeError at
runtime, inside card.step(), on the first cycle -- not a build failure. On a Ford that means card
never transmits while the harness relay is open, the camera is cut off from the car, and the
cluster reports "Front Camera Malfunction Service Required". It cost a drive to find that way
once (overrideState, added to IntelligentCruiseButtonManagement without its mirror); this test is
so it can never cost a second one.

Only the crashing direction is asserted: every capnp field must exist on the dataclass. The
reverse -- a dataclass field with no schema field -- is dead weight but harmless, and there are
legitimate cases of it.
"""

import pytest

from cereal import custom
from opendbc.car import structs

# capnp struct name -> dataclass mirror. Add a pair here when a struct gains a mirror.
MIRRORED_STRUCTS = {
  "CarParamsSP": structs.CarParamsSP,
  "CarControlSP": structs.CarControlSP,
  "CarStateSP": structs.CarStateSP,
  "ModularAssistiveDrivingSystem": structs.ModularAssistiveDrivingSystem,
  "IntelligentCruiseButtonManagement": structs.IntelligentCruiseButtonManagement,
  "LeadData": structs.LeadData,
  "ControllerStateBP": structs.ControllerStateBP,
}


def capnp_field_names(struct_name: str) -> set[str]:
  schema = getattr(custom, struct_name).schema
  # helpers.py::remove_deprecated drops these before construction, so they need no mirror.
  return {f for f in schema.fields.keys() if not f.endswith("DEPRECATED")}


class TestStructsCapnpParity:
  @pytest.mark.parametrize("struct_name", sorted(MIRRORED_STRUCTS))
  def test_every_capnp_field_has_a_mirror(self, struct_name):
    dataclass_fields = set(MIRRORED_STRUCTS[struct_name].__dataclass_fields__)
    missing = capnp_field_names(struct_name) - dataclass_fields

    assert not missing, (
      f"custom.capnp struct {struct_name} has field(s) {sorted(missing)} with no matching field on "
      f"opendbc.car.structs.{MIRRORED_STRUCTS[struct_name].__name__}. Add them to the dataclass -- "
      f"convert_carControlSP() splats the capnp dict into the constructor, so this raises TypeError "
      f"in card at runtime, not at build time."
    )

  def test_mirrored_structs_all_resolve(self):
    """A typo in MIRRORED_STRUCTS would silently skip a struct, so fail loudly instead."""
    for struct_name in MIRRORED_STRUCTS:
      assert hasattr(custom, struct_name), f"{struct_name} is not a struct in cereal/custom.capnp"

  def test_default_construction_round_trips(self):
    """A default-constructed capnp message must be accepted by its mirror.

    This is the exact operation card performs each cycle. Catches type-level drift the field-name
    comparison above cannot see -- e.g. a new enum member the mirror's StrEnum does not define.
    """
    for struct_name, dataclass_type in MIRRORED_STRUCTS.items():
      msg = getattr(custom, struct_name).new_message()
      fields = {k: v for k, v in msg.to_dict().items() if not k.endswith("DEPRECATED")}
      # Nested lists of structs are not constructed by helpers.py; skip them as it does.
      fields = {k: v for k, v in fields.items() if not isinstance(v, list)}
      dataclass_type(**fields)


if __name__ == "__main__":
  pytest.main([__file__, "-v"])
