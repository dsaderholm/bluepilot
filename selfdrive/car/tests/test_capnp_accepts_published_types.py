"""FusionPilot: the values plannerd publishes must be types capnp will accept.

On 2026-08-18 `dec.hasSlowDown = self.dec.has_slow_down()` killed plannerd on the first frame:

    KjException: Tried to set field: 'hasSlowDown' with a value of: 'False'
    which is an unsupported type: '<class 'numpy.bool'>'

`has_slow_down()` is `urgency_filtered > SLOW_DOWN_PROB` and urgency_filtered is a numpy scalar, so
it returns `numpy.bool`. Python treats that as a bool everywhere except at the capnp boundary.

**A STATIC TEST CANNOT CATCH THIS AND ONE WAS WRITTEN.** `test_dec_slow_down_published.py` asserts
the wiring by reading the AST -- it proved the field was fed from the right accessor and could not
possibly have noticed the type. This is the same category as the 2026-08-15 CarController crash:
pure-logic and structural tests do not execute the boundary, and the boundary is where the process
dies. So this one BUILDS THE REAL MESSAGE and assigns real numpy values into it.
"""
from __future__ import annotations

import numpy as np
import pytest

from cereal import custom


def _dec():
  msg = custom.LongitudinalPlanSP.new_message()
  return msg.dec


def test_a_numpy_bool_is_refused_by_capnp():
  """The failure itself, pinned. If pycapnp ever starts accepting numpy.bool this test tells us the
  cast is no longer load-bearing rather than silently becoming decoration."""
  with pytest.raises(Exception):
    _dec().hasSlowDown = np.bool_(False)


def test_the_published_types_all_survive_the_boundary():
  """What plannerd actually assigns, with the casts it actually uses."""
  dec = _dec()
  dec.hasSlowDown = bool(np.float32(0.9) > np.float32(0.5))
  dec.slowDownUrgency = float(np.float32(0.73))
  dec.slowDownEndpoint = float(np.float64(142.5))
  assert dec.hasSlowDown is True
  assert dec.slowDownUrgency == pytest.approx(0.73, abs=1e-4)
  assert dec.slowDownEndpoint == pytest.approx(142.5)


def test_an_infinite_endpoint_is_clamped_before_it_is_published():
  """endpoint_x() is inf when the model's plan is not full length. 0 means "no endpoint"."""
  endpoint = float('inf')
  dec = _dec()
  dec.slowDownEndpoint = 0.0 if not np.isfinite(endpoint) else endpoint
  assert dec.slowDownEndpoint == 0.0


def test_plannerd_casts_every_numpy_derived_field_it_publishes():
  """Reads the real publish site: each dec.* assignment must go through bool() or float().

  Guards the whole class rather than the one field that bit -- the next accessor added here will be
  numpy-derived too, and nothing else in the suite executes this line."""
  import ast, pathlib
  path = pathlib.Path(__file__).resolve().parents[3] / "sunnypilot" / "selfdrive" / "controls" / "lib" / "longitudinal_planner.py"
  tree = ast.parse(path.read_text(encoding="utf-8"))
  bad = []
  for node in ast.walk(tree):
    if not isinstance(node, ast.Assign):
      continue
    for target in node.targets:
      if not (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "dec"):
        continue
      # Verified plain-Python sources, so they need no cast:
      #   state    - a capnp enum value
      #   enabled  - params.get_bool(), a Python bool
      #   active   - `selfdriveState.experimentalMode and self._enabled`, a Python bool
      # Everything else in DEC is derived from numpy scalars and must be cast.
      if target.attr in ("state", "enabled", "active"):
        continue
      dump = ast.dump(node.value)
      if "Name(id='bool'" not in dump and "Name(id='float'" not in dump and "IfExp" not in dump:
        bad.append(target.attr)
  assert not bad, f"published without a Python cast, which capnp rejects for numpy types: {bad}"


def test_every_acc_authority_value_is_a_real_capnp_enumerant():
  """`AccAuthority` must round-trip through capnp by NAME, and one line in structs.py is why it can.

  `convert_to_capnp` calls `ControllerStateBP.new_message(**asdictref(struct))`, so each member's
  VALUE is handed to capnp as an enumerant name. `enum.StrEnum`'s `auto()` lowercases the member
  name, which would make `opStop` into "opstop" and get

      KjException: enum has no such enumerant; name = opstop

  -- card dying on the first passthrough frame. What prevents it is the StrEnum subclass at the top
  of structs.py, whose `_generate_next_value_` returns the name verbatim. That override is load
  bearing for every camelCase enum in the file and nothing else asserts it; `LateralMode` could not
  have caught a regression because all three of its names are lowercase either way.

  Iterates the members rather than listing them, so one added later is covered without anyone
  remembering this file exists."""
  from opendbc.car.structs import ControllerStateBP

  assert any(m.name != str(m).lower() for m in ControllerStateBP.AccAuthority), (
    "every member is lowercase, so this test can no longer detect a lowercasing auto() -- add a "
    "camelCase member or assert the StrEnum override directly")

  for member in ControllerStateBP.AccAuthority:
    msg = custom.ControllerStateBP.new_message()
    msg.accAuthority = str(member)          # exactly what convert_to_capnp hands over
    assert str(msg.accAuthority) == str(member), (
      f"{member!r} did not survive the capnp round trip")


def test_the_default_acc_authority_converts():
  """The default matters on its own: every non-Ford car publishes it untouched, so a broken default
  would take down cars that have nothing to do with this feature."""
  from opendbc.car.structs import ControllerStateBP

  default = ControllerStateBP().accAuthority
  msg = custom.ControllerStateBP.new_message()
  msg.accAuthority = str(default)
  assert str(msg.accAuthority) == "stock"
