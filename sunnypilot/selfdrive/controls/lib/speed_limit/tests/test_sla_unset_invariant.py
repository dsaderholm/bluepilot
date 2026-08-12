"""FusionPilot: Speed Limit Assist must report V_CRUISE_UNSET when it has no limit.

longitudinal_planner relies on exactly this. Bidirectional SLA REPLACES the cruise baseline rather
than being min()'d against it, so the planner drops `cruise` from its candidates while SLA owns the
baseline -- and "owns the baseline" has to mean "is actually asking for a speed", not merely
`is_active`, because SLA stays active across a stretch with no speed limit data.

Route 00000348 on 2026-08-11 is what that costs when the invariant is not checked: every candidate
was unset at once, the planner published a target nobody requested, ICBM correctly rejected it as
unreal and then held the current set speed, and 38 mph froze for 40 seconds through a full stop with
the driver's hold sitting at 50.

So this pins the contract the gate reads. If SLA's fallback ever becomes 0, or a real speed, or a
different sentinel, the gate silently stops working and nothing else in the suite would notice.
"""
from openpilot.selfdrive.car.cruise import V_CRUISE_UNSET
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.speed_limit_assist import SpeedLimitAssist


def _sla(has_limit: bool, active: bool, limit: float = 25.0) -> SpeedLimitAssist:
  sla = SpeedLimitAssist.__new__(SpeedLimitAssist)
  sla._has_speed_limit = has_limit
  sla.pcm_op_long = False
  sla.is_enabled = True
  sla.is_active = active
  sla._speed_limit_final_last = limit
  return sla


def test_no_limit_reports_unset_even_while_active():
  """The measured case. Active with nothing to follow must not look like a request."""
  assert _sla(has_limit=False, active=True).get_v_target_from_control() == V_CRUISE_UNSET


def test_a_real_limit_reports_a_real_speed():
  assert _sla(has_limit=True, active=True).get_v_target_from_control() == 25.0


def test_the_sentinel_is_larger_than_any_real_speed():
  """The planner's gate is `output_v_target < V_CRUISE_UNSET`, which only distinguishes the two if
  the sentinel is out of range for a genuine target. 255 against a road speed in m/s is."""
  assert V_CRUISE_UNSET > 100.0
