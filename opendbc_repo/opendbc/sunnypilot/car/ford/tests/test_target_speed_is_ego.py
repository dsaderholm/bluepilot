"""FusionPilot: `AccVeh_V_Trg` must carry the car's own speed, not a constant.

The DBC lists `TCM_DSL` -- the TRANSMISSION -- among that field's receivers, alongside the PCM and
ECM. Upstream names the parameter it feeds `v_ego_kph`, which says what belongs in it.

BluePilot passes `V_CRUISE_MAX` (145 kph / 90 mph) instead, so on every openpilot-authored frame the
transmission was told the car wanted to be 90 mph. Measured on route 000003bd: `145.0` on 44.0% of
bus-0 ACCDATA, which is exactly the share openpilot authored, while Ford's own value on the same
drive averaged just +4.3 kph above vEgo across three routes against openpilot's +32.8.

His report is what this is for: *"It tricks my transmission all the time, so I sometimes go into
third gear on the freeway."* A transmission told the car wants 20 mph more than it has will downshift
to deliver it.

NOT PROVEN as the downshift cause -- it is one measured discrepancy in a field the transmission
receives. What these tests protect is that the field carries EGO and can never silently go back to a
constant, which is what makes the next drive readable either way.
"""
import ast
import inspect
import re

from opendbc.car.ford import carcontroller


def _update_src():
  return inspect.getsource(carcontroller.CarController.update)


def test_target_speed_is_derived_from_vego():
  """Parsed, not grepped: every comment explaining this contains both names."""
  tree = ast.parse(_update_src().lstrip())
  for node in ast.walk(tree):
    if isinstance(node, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "target_speed" for t in node.targets):
      src = ast.unparse(node.value)
      assert "vEgo" in src, f"target_speed = {src} -- it must come from the car's own speed"
      assert "V_CRUISE" not in src, f"target_speed = {src} -- back to a constant"
      return
  raise AssertionError("no assignment to target_speed found in CarController.update")


def test_the_conversion_is_ms_to_kph():
  """The signal is km/h and vEgo is m/s. A missing conversion would put 20 kph on a 45 mph road --
  the same class of error as the constant, quieter."""
  m = re.search(r"target_speed = ([^\n]+)", _update_src())
  expr = m.group(1)
  assert "3.6" in expr or "MS_TO_KPH" in expr, f"target_speed = {expr} -- no m/s to km/h conversion"


def test_v_cruise_max_is_not_imported_for_this_any_more():
  """It was imported solely to feed this field. Leaving the import invites it back."""
  src = inspect.getsource(carcontroller)
  head = src[:src.index("class CarController")]
  assert "V_CRUISE_MAX" not in head, "V_CRUISE_MAX is imported again -- check what for"
