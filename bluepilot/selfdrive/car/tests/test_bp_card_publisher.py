"""FusionPilot: the BP diagnostic publishers must never be able to stop the car.

`publish_controller_state_bp` is called from card.py:298 with NO try/except around it. Everything
it publishes is diagnostic -- nothing in the driving path reads `controllerStateBP` -- so an
exception in there costs a drive and buys nothing. That is verbatim the failure this fork already
paid for once, when one AttributeError in `icbm.py::_update_gap` left the car on "waiting to start".

The specific hazard is the settings snapshot: ~60 field names in a dict, applied with `setattr` in a
loop. One field renamed in custom.capnp without its entry here, or one value of a type capnp
refuses, takes out `card`.

STUBBED AND IMPORTED, NOT SKIPPED. `cereal.messaging` needs `msgq`, which does not exist offline.
`pytest.importorskip` would make this whole file vanish from a green run -- which happened here
before, hiding seven tests behind an "8 passed" line. Stubbing the module chain means a chain that
breaks fails loudly instead.
"""
import sys
import types

import pytest

from opendbc.car import structs


# Everything this file puts into sys.modules. It MUST all come back out: the offline runner
# executes the whole suite in one process, so a stub left behind is inherited by every test that
# runs after this file. The first version of this file did exactly that and turned 1033 green into
# "23 failed, 12 errors" in two unrelated files -- both of which passed when run on their own,
# which is the signature. If a test file stubs a module, it owns restoring it.
_STUBBED = (
  "cereal",
  "cereal.messaging",
  "openpilot.common.params",
  "openpilot.common.swaglog",
  "openpilot.selfdrive.car.helpers",
  "openpilot.bluepilot.selfdrive.car.bp_card_publisher",
)


@pytest.fixture(autouse=True)
def _restore_sys_modules():
  saved = {k: sys.modules.get(k) for k in _STUBBED}
  cereal_mod = sys.modules.get("cereal")
  saved_attr = getattr(cereal_mod, "messaging", None) if cereal_mod is not None else None
  had_attr = cereal_mod is not None and hasattr(cereal_mod, "messaging")
  yield
  for name, mod in saved.items():
    if mod is None:
      sys.modules.pop(name, None)
    else:
      sys.modules[name] = mod
  # `cereal` is usually a REAL module here, and we set an attribute on it rather than replacing
  # it -- restoring sys.modules alone would leave that attribute behind.
  if cereal_mod is not None:
    if had_attr:
      cereal_mod.messaging = saved_attr
    else:
      try:
        del cereal_mod.messaging
      except AttributeError:
        pass


def _install_stubs():
  """Stub the leaf modules bp_card_publisher imports, not their chains."""
  cereal = sys.modules.setdefault("cereal", types.ModuleType("cereal"))
  messaging = types.ModuleType("cereal.messaging")
  messaging.new_message = lambda name: types.SimpleNamespace(valid=False)
  sys.modules["cereal.messaging"] = messaging
  cereal.messaging = messaging

  swaglog = types.ModuleType("openpilot.common.swaglog")
  swaglog.cloudlog = types.SimpleNamespace(
    exception=lambda *a, **k: None, error=lambda *a, **k: None, warning=lambda *a, **k: None)
  sys.modules["openpilot.common.swaglog"] = swaglog

  helpers = types.ModuleType("openpilot.selfdrive.car.helpers")
  helpers.convert_to_capnp = lambda x: x
  sys.modules["openpilot.selfdrive.car.helpers"] = helpers


class _FakeParams:
  """Strict on purpose: a Mock returns a Mock for any key and the suite would pass while the car
  published garbage. This is the 'a stub laxer than the device hides the bug' rule."""

  def __init__(self, values=None):
    self.values = values or {}
    self.asked = []

  def get(self, key, return_default=False):
    self.asked.append(key)
    return self.values.get(key)

  def get_bool(self, key):
    self.asked.append(key)
    return bool(self.values.get(key, False))


def _load(params_values=None):
  _install_stubs()
  fake = _FakeParams(params_values)
  params_mod = types.ModuleType("openpilot.common.params")
  params_mod.Params = lambda *a, **k: fake
  sys.modules["openpilot.common.params"] = params_mod
  sys.modules.pop("openpilot.bluepilot.selfdrive.car.bp_card_publisher", None)
  import openpilot.bluepilot.selfdrive.car.bp_card_publisher as mod  # noqa: E402
  mod._settings_cache = {}
  mod._settings_last_read = 0.0
  mod._publish_failed = False
  mod._car_state_publish_failed = False
  return mod, fake


# ---------------------------------------------------------------------------------------------
# The guard that keeps a diagnostic from stopping the car.

def test_a_raising_publisher_does_not_reach_card():
  """card.py calls this with no guard, so anything that escapes here stops the car."""
  mod, _ = _load()

  def boom(CI, pm):
    raise RuntimeError("field renamed in capnp and not here")

  mod._publish_controller_state_bp = boom
  mod.publish_controller_state_bp(object(), object())      # must not raise
  assert mod._publish_failed is True, "the publisher must latch off after a failure"


def test_the_latch_stops_it_retrying_every_frame():
  """Retrying at 100 Hz would flood swaglog and bury the one line that explains the failure --
  the same reason icbm_gap_failed latches rather than retries."""
  mod, _ = _load()
  calls = []

  def boom(CI, pm):
    calls.append(1)
    raise RuntimeError("boom")

  mod._publish_controller_state_bp = boom
  for _ in range(50):
    mod.publish_controller_state_bp(object(), object())
  assert len(calls) == 1, f"latched publisher re-entered {len(calls)} times"


def test_the_car_state_publisher_latches_independently():
  """One failing publisher must not silence the other -- they carry different diagnostics."""
  mod, _ = _load()

  def boom(CI, pm, can_valid):
    raise RuntimeError("boom")

  mod._publish_car_state_bp = boom
  mod.publish_car_state_bp(object(), object(), True)
  assert mod._car_state_publish_failed is True
  assert mod._publish_failed is False, "carStateBP failing must not disable controllerStateBP"


# ---------------------------------------------------------------------------------------------
# The hazard the guard exists for, checked directly rather than only caught.

def test_every_snapshot_key_is_a_real_field_on_the_struct():
  """This is what the setattr loop would raise on. Checking it here means the guard is a backstop
  rather than the only thing standing between a renamed field and an undrivable car."""
  mod, _ = _load()
  snapshot = mod._refresh_settings_cache()
  fields = set(structs.ControllerStateBP.__dataclass_fields__)
  unknown = sorted(k for k in snapshot if k not in fields)
  assert not unknown, (
    f"settings snapshot publishes fields that do not exist on ControllerStateBP: {unknown}. "
    "card.py applies these with setattr in a loop -- this would stop the car.")


def test_the_snapshot_survives_params_that_raise():
  """A device with a params store mid-write must degrade to defaults, not to a dead card."""
  mod, _ = _load()

  class Exploding:
    def get(self, key, return_default=False):
      raise OSError("params store is busy")

    def get_bool(self, key):
      raise OSError("params store is busy")

  sys.modules["openpilot.common.params"].Params = lambda *a, **k: Exploding()
  snapshot = mod._refresh_settings_cache()          # must not raise
  assert snapshot["bmsLaneCenteringStrength"] == 0.0
  assert snapshot["bmsHighSpeedDampening"] == 1.0


# ---------------------------------------------------------------------------------------------
# The five angle-mode settings that were missing, and why they matter.

ANGLE_LANE_POSITIONING = {
  "bmsHighSpeedDampening":       ("FordHighSpeedDampening_ang", "0.78", 0.78),
  "bmsInLaneOffsetAng":          ("custom_path_offset_ang", "0.1", 0.1),
  "bmsLaneCenteringStrength":    ("lane_centering_strength_ang", "0.45", 0.45),
  "bmsLaneCenteringDamping":     ("lane_centering_damping_ang", "0.3", 0.3),
}


@pytest.mark.parametrize("field,spec", sorted(ANGLE_LANE_POSITIONING.items()))
def test_each_angle_setting_reaches_the_wire_from_the_right_param(field, spec):
  """The bms block carried enable_lane_positioning_CURV, custom_path_offset_CURV and
  LC_PID_gain_UI_CURV -- all curvature-mode keys -- while this car runs ANGLE mode. So the settings
  that actually governed it never reached a route, and the 2026-09-04
  lane_centering_strength_ang 0.35 -> 0.45 change could not be scored afterwards: initData is a boot
  snapshot and the wire carried nothing.

  Asserts the VALUE round-trips from the named param, so pointing a field at the wrong key (the
  _curv/_ang confusion that caused this) fails rather than reading plausibly."""
  key, raw, expected = spec
  mod, fake = _load({key: raw})
  snapshot = mod._refresh_settings_cache()
  assert key in fake.asked, f"{field} never read {key}"
  assert snapshot[field] == pytest.approx(expected)


def test_lane_positioning_enable_reaches_the_wire():
  """Separate from the float cases above -- it is the only bool of the five."""
  mod, fake = _load({"enable_lane_positioning_ang": True})
  snapshot = mod._refresh_settings_cache()
  assert "enable_lane_positioning_ang" in fake.asked
  assert snapshot["bmsEnableLanePositioningAng"] is True


def test_the_angle_fields_are_not_the_curvature_fields():
  """The bug this whole block exists for: reading a _curv key into an _ang field would look
  entirely correct and publish the wrong car's settings."""
  mod, _ = _load({"lane_centering_strength_ang": "0.45", "custom_path_offset_curv": "0.9"})
  snapshot = mod._refresh_settings_cache()
  assert snapshot["bmsLaneCenteringStrength"] == pytest.approx(0.45)
  assert snapshot["bmsInLaneOffsetAng"] == 0.0, "the _ang offset must not read the _curv key"
  assert snapshot["bmsInLaneOffset"] == pytest.approx(0.9), "the _curv field still reads _curv"
