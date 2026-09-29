"""FusionPilot: a boot where the car does not answer must not delete his settings.

Route 000004ba, 2026-09-28. He was clearing DTCs in FORScan after the PSCM fault; the device booted
while the modules were not answering (VIN all zeros, every firmware response null) and card
fingerprinted the car as MOCK. The cleanup code then read "no ICBM, no op long, PCM cruise speed" and
deleted IntelligentCruiseButtonManagement, SmartCruiseControlMap, SmartCruiseControlVision and
CustomAccIncrementsEnabled, and downgraded SpeedLimitMode from assist to warning. The next boot
recognised the car and drove with all of it off until he turned each one back on by hand.

Three places did the deleting -- card at init, the UI's constraint check, and the cruise settings
page -- plus plannerd rewriting SpeedLimitMode every params period. All four now treat a mock car as
"not known yet", which this fork already decided is not the same as "not supported".

The negative cases matter as much: a REAL car without ICBM must still be cleaned up, or the mock
guard has quietly disabled the cleanup for everyone.
"""
import ast
import pathlib

from opendbc.car import structs
from openpilot.sunnypilot.selfdrive.car.interfaces import _cleanup_unsupported_params
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.common import Mode
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.helpers import set_speed_limit_assist_availability

REPO = pathlib.Path(__file__).resolve().parents[4]


class RecordingParams:
  """Holds his settings as they stood before route 000004ba, and records every write."""

  def __init__(self):
    self.store = {
      "IntelligentCruiseButtonManagement": True,
      "SmartCruiseControlMap": True,
      "SmartCruiseControlVision": True,
      "CustomAccIncrementsEnabled": False,
      "DynamicExperimentalControl": False,
      "SpeedLimitMode": int(Mode.assist),
      "IsReleaseSpBranch": False,
    }
    self.removed: list[str] = []
    self.written: list[str] = []

  def get(self, key, *a, **k):
    return self.store.get(key)

  def get_bool(self, key, *a, **k):
    return bool(self.store.get(key, False))

  def put(self, key, value, *a, **k):
    self.written.append(key)
    self.store[key] = value

  def put_bool(self, key, value, *a, **k):
    self.put(key, value)

  def remove(self, key):
    self.removed.append(key)
    self.store.pop(key, None)


def _cars(brand: str):
  """What card saw on route 000004ba: nothing supported, PCM cruise speed. Only the brand varies."""
  CP = structs.CarParams.new_message()
  CP.brand = brand
  CP.openpilotLongitudinalControl = False
  CP_SP = structs.CarParamsSP()
  CP_SP.intelligentCruiseButtonManagementAvailable = False
  CP_SP.pcmCruiseSpeed = True
  return CP, CP_SP


def test_a_mock_car_deletes_nothing_at_car_init():
  params = RecordingParams()
  _cleanup_unsupported_params(*_cars("mock"), params)
  assert params.removed == []
  assert params.written == []


def test_a_real_car_without_icbm_is_still_cleaned_up():
  """The guard must not switch the cleanup off for a car that genuinely lacks the features."""
  params = RecordingParams()
  _cleanup_unsupported_params(*_cars("toyota"), params)
  assert "IntelligentCruiseButtonManagement" in params.removed
  assert "SmartCruiseControlMap" in params.removed
  assert "SmartCruiseControlVision" in params.removed
  assert params.store["SpeedLimitMode"] == int(Mode.warning)


def test_a_mock_car_leaves_speed_limit_assist_alone():
  """plannerd calls this every params period, so a mock car would keep rewriting it."""
  params = RecordingParams()
  assert set_speed_limit_assist_availability(*_cars("mock"), params) is False
  assert params.store["SpeedLimitMode"] == int(Mode.assist)
  assert params.written == []


def test_a_real_pcm_car_still_loses_assist():
  params = RecordingParams()
  assert set_speed_limit_assist_availability(*_cars("toyota"), params) is False
  assert params.store["SpeedLimitMode"] == int(Mode.warning)


# ---- the UI side, read statically: instantiating either file drags in raylib, fonts and textures.

def _function(path: pathlib.Path, cls: str, name: str) -> ast.FunctionDef:
  tree = ast.parse(path.read_text(encoding="utf-8"))
  for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef) and node.name == cls:
      for item in node.body:
        if isinstance(item, ast.FunctionDef) and item.name == name:
          return item
  raise AssertionError(f"{cls}.{name} not found in {path}")


def _remove_calls(fn: ast.FunctionDef):
  return [n for n in ast.walk(fn)
          if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "remove"]


def _mentions_mock(node: ast.AST) -> bool:
  return any(isinstance(n, ast.Constant) and n.value == "mock" for n in ast.walk(node))


def test_the_ui_constraint_check_returns_on_a_mock_car_before_any_removal():
  fn = _function(REPO / "selfdrive/ui/sunnypilot/ui_state.py", "UIStateSP", "_enforce_constraints")
  guard = next((s for s in fn.body if isinstance(s, ast.If) and _mentions_mock(s.test)
                and any(isinstance(b, ast.Return) for b in s.body)), None)
  assert guard is not None, "no early return on a mock CarParams"
  removes = _remove_calls(fn)
  assert removes, "the function no longer removes anything -- this test is checking nothing"
  assert all(r.lineno > guard.lineno for r in removes), "a removal runs before the mock guard"


def test_every_removal_on_the_cruise_page_sits_behind_the_mock_check():
  fn = _function(REPO / "selfdrive/ui/sunnypilot/layouts/settings/cruise.py", "CruiseLayout", "_update_state")
  parents = {}
  for node in ast.walk(fn):
    for child in ast.iter_child_nodes(node):
      parents[child] = node
  removes = _remove_calls(fn)
  assert removes, "the page no longer removes anything -- this test is checking nothing"
  for call in removes:
    child, node, guarded = call, call, False
    while node in parents:
      child, node = node, parents[node]
      # In the IF-branch of a test that rules out mock -- the else-branch would be the opposite.
      if isinstance(node, ast.If) and _mentions_mock(node.test) and child in node.body:
        guarded = True
        break
    assert guarded, f"cruise.py:{call.lineno} removes a param without the mock check above it"
