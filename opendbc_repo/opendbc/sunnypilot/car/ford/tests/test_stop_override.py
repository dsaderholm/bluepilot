"""FusionPilot: the stop override -- the last few mph Ford's set speed cannot ask for.

Every test here is a rule from CLAUDE.md turned into an assertion, because this is the feature most
likely to be "improved" into the thing it was designed not to be:

  - a NAMED condition, never a comparison. Ford's command is never read, so `min(ford, op)` cannot
    creep in through a refactor.
  - bounded in TIME as well as by its trigger. Drive A latched the camera after ~40 s of sustained
    contradiction; a stop is 5-8 s; nobody has measured where between those the threshold sits.
  - a lead disqualifies it. Ford's stop-and-go is years of calibration and it owns that case.
  - spent after it fires, so a stop that does not complete cannot re-trigger every frame and turn a
    bounded override into a permanent one.
"""
from __future__ import annotations

import ast
import inspect

from opendbc.sunnypilot.car.ford import stop_override as mod
from opendbc.sunnypilot.car.ford.stop_override import (
  ENTER_SPEED,
  LEAD_DISQUALIFIES_M,
  MAX_ACTIVE_FRAMES,
  MPH_TO_MS,
  FordStopOverride,
)

SLOW = 15 * 0.44704       # 15 mph, inside the regime the set speed cannot reach
FAST = 40 * 0.44704


def _stopping(o, v=SLOW, lead=0.0, has_slow_down=True, op_stopping=True, long_active=True):
  return o.update(long_active=long_active, v_ego=v, has_slow_down=has_slow_down,
                  op_stopping=op_stopping, lead_distance=lead)


def test_it_fires_for_a_stop_the_radar_cannot_see():
  o = FordStopOverride()
  assert _stopping(o) is True
  assert o.active


def test_it_does_not_fire_where_the_set_speed_could_still_have_asked():
  """Above the entry speed ICBM is strictly better -- Ford picks coast vs engine-brake vs friction
  and that blend is the whole reason the division of labour exists."""
  o = FordStopOverride()
  assert _stopping(o, v=FAST) is False
  assert _stopping(o, v=ENTER_SPEED + 1.0) is False


def test_a_lead_disqualifies_it_because_ford_stops_for_those_itself():
  o = FordStopOverride()
  assert _stopping(o, lead=LEAD_DISQUALIFIES_M - 10) is False
  # ...and a lead appearing mid-override hands straight back.
  o2 = FordStopOverride()
  assert _stopping(o2) is True
  assert _stopping(o2, lead=20.0) is False
  assert "lead" in o2.last_result


def test_it_ends_when_the_car_is_stopped():
  o = FordStopOverride()
  assert _stopping(o) is True
  assert _stopping(o, v=0.0) is False
  assert o.last_result == "stopped"


def test_the_time_bound_ends_it_even_if_the_reason_persists():
  """THE bound. Without it "a stop line ahead" says when to start and nothing about when to stop,
  and continuous seconds of disagreement is what latched the camera on drive A."""
  o = FordStopOverride()
  assert _stopping(o) is True
  for _ in range(MAX_ACTIVE_FRAMES):
    _stopping(o)
  assert _stopping(o) is False
  assert o.last_result == "time bound reached"


def test_it_is_spent_and_will_not_retrigger_on_the_same_approach():
  """A stop that does not complete must not fire again every frame."""
  o = FordStopOverride()
  assert _stopping(o) is True
  for _ in range(MAX_ACTIVE_FRAMES + 2):
    _stopping(o)
  assert not o.active
  for _ in range(50):
    assert _stopping(o) is False, "it re-armed while the model was still asking"


def test_the_model_dropping_the_request_is_what_re_arms_it():
  o = FordStopOverride()
  assert _stopping(o) is True
  for _ in range(MAX_ACTIVE_FRAMES + 2):
    _stopping(o)
  assert _stopping(o) is False
  _stopping(o, has_slow_down=False)          # the reason went away
  assert _stopping(o) is True, "a genuinely new stop was refused"


def test_longitudinal_going_inactive_ends_it_immediately():
  """Nothing may be authored there -- panda passes only the inactive frame, and create_acc_msg
  clearing Cmbb_B_Enbl is how openpilot's disengagement reaches the car."""
  o = FordStopOverride()
  assert _stopping(o) is True
  assert _stopping(o, long_active=False) is False
  assert not o.active


def test_it_waits_for_the_plan_to_commit():
  """The model wanting to stop is not enough; openpilot's plan has to have reached stopping."""
  o = FordStopOverride()
  assert _stopping(o, op_stopping=False) is False
  assert _stopping(o) is True


def test_it_never_reads_fords_command():
  """The structural guard against the documented trap. `min(ford_accel, op_accel)` is one line, it
  handles every case, and it turns the passthrough back into op long behind a comparison operator.
  The defense is that Ford's numbers are not an input to this file at all."""
  # Parsed with ast, not grepped. Every explanation of the trap contains the words, so a text scan
  # would fail on its own docstring -- the same lesson test_mapd_schema.py records for
  # `suggestedSpeed`: prose stays free, code is caught by name.
  tree = ast.parse(inspect.getsource(mod))
  used = set()
  for node in ast.walk(tree):
    if isinstance(node, ast.Name):
      used.add(node.id)
    elif isinstance(node, ast.Attribute):
      used.add(node.attr)
    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
      continue      # docstrings and log strings are prose
  for forbidden in ("AccBrkTot_A_Rq", "AccPrpl_A_Rq", "acc_stock_values", "ford_accel",
                    "stock_values", "passthrough_admissible"):
    assert forbidden not in used, (
      f"{forbidden} reached the stop override -- it decides WHETHER to override, never by comparing "
      "against what Ford asked for")
  sig = inspect.signature(FordStopOverride.update)
  assert set(sig.parameters) == {"self", "long_active", "v_ego", "has_slow_down", "op_stopping",
                                 "lead_distance"}, sig


def test_the_time_bound_is_expressed_in_SECONDS_at_the_rate_it_actually_ticks():
  """A factor of two hid here and was caught before the drive rather than on it.

  `update` runs inside the carcontroller's ACCDATA block, which is gated on `ACC_CONTROL_STEP = 2`
  -- so it ticks at 50 Hz, not the 100 Hz control rate. The first version of the constant was
  written as "8 s at 100 Hz" and would have been SIXTEEN seconds of continuous contradiction, which
  is not comfortably under the 40 s that latched the camera on drive A.
  """
  from opendbc.car.ford.values import CarControllerParams
  from opendbc.sunnypilot.car.ford.stop_override import MAX_ACTIVE_S, OVERRIDE_HZ

  assert OVERRIDE_HZ == 100.0 / CarControllerParams.ACC_CONTROL_STEP, (
    "the override's assumed tick rate no longer matches ACC_CONTROL_STEP, so the time bound is "
    f"off by {CarControllerParams.ACC_CONTROL_STEP * OVERRIDE_HZ / 100.0:.1f}x")
  assert MAX_ACTIVE_FRAMES == int(MAX_ACTIVE_S * OVERRIDE_HZ)
  assert MAX_ACTIVE_S <= 15.0, "well under drive A's 40 s is the entire point of this bound"


# --- resuming from a stop WE authored ------------------------------------------------------------

def test_a_stop_we_authored_is_not_resumed_from_automatically():
  """`controlsd` sets cruiseControl.resume from `standstill and not shouldStop`, so once the model
  judges an intersection clear the car would pull away with no driver input. That is upstream
  behaviour, and the override is what makes it reachable on this car for the first time -- the
  standstill state has never existed here, because stock ACC cannot hold a stop without a lead.

  "Come to a complete stop" did not ask for "and then go when the model feels like it". Deciding an
  intersection is clear is the side of the Level 2 line where the driver is responsible, and the
  model alone is the cheapest evidence there is.
  """
  from opendbc.sunnypilot.car.ford.longitudinal_ext import LongitudinalExt

  class FakeLead:
    status = False
    dRel = 0.0
    vLead = 0.0

  class FakeSM:
    def __getitem__(self, _):
      return type("_R", (), {"leadOne": FakeLead()})()

  class Host:
    resume_gate_enabled = True
    resume_min_gap_m = 6.0
    resume_min_lead_speed_ms = 1.0
    resume_gate_blocking = False
    stop_override_stopped_us = False

  host = Host()
  sm = FakeSM()

  # No lead and no override stop: unchanged -- nothing to wait for.
  assert LongitudinalExt.resume_allowed(host, sm) is True

  # The same frame after WE stopped the car: held for the driver.
  host.stop_override_stopped_us = True
  assert LongitudinalExt.resume_allowed(host, sm) is False
  assert host.resume_gate_blocking

  # And the gate being off still means off -- it is his switch, not ours to ignore.
  host.resume_gate_enabled = False
  assert LongitudinalExt.resume_allowed(host, sm) is True


def _drive_to_a_stop(so, **kw):
  """Run a full approach and return the (was_active, override, last_result) triple per frame."""
  out = []
  for v_mph in (24.0, 18.0, 12.0, 6.0, 2.0, 0.3):
    was_active = so.active
    override = so.update(long_active=True, v_ego=v_mph * MPH_TO_MS, has_slow_down=True,
                         op_stopping=True, lead_distance=kw.get("lead", 0.0))
    out.append((was_active, override, so.last_result))
  return out


def test_the_stopped_latch_must_be_edge_triggered():
  """`last_result` persists, so the resume gate cannot key on its value alone.

  The latch tells `resume_allowed` "this stop was ours, do not pull away on the model's say-so".
  After one real override stop, `last_result` stays the string "stopped" until the next arm or end
  -- so a later stop that the override never touched (a lead in front, the model never asking, the
  feature never arming) still reads "stopped" and re-latches. That is the queue-cleared open-road
  case, where openpilot's automatic resume is exactly what he wants, and he would instead sit at a
  green light waiting for a press. Found re-checking my own fix, 2026-08-18."""
  so = FordStopOverride()
  frames = _drive_to_a_stop(so)
  edges = [f for f in frames if f[0] and not f[1] and f[2] == "stopped"]
  assert len(edges) == 1, f"the stop should latch on exactly one frame, got {len(edges)}"
  assert so.last_result == "stopped"

  # Now a SECOND stop that the override has nothing to do with: a lead close enough that Ford owns
  # it, so the override never arms. `last_result` is still "stopped" from the first one.
  so.update(long_active=True, v_ego=30.0 * MPH_TO_MS, has_slow_down=False,
            op_stopping=False, lead_distance=0.0)
  latched_again = False
  for v_mph in (20.0, 10.0, 4.0, 0.3):
    was_active = so.active
    override = so.update(long_active=True, v_ego=v_mph * MPH_TO_MS, has_slow_down=True,
                         op_stopping=True, lead_distance=25.0)
    assert not override, "a lead inside 60 m is Ford's stop, not ours"
    if was_active and not override and so.last_result == "stopped":
      latched_again = True
  assert not latched_again, (
    "the second stop re-latched -- the carcontroller must read `was_active` from BEFORE update() "
    "rather than testing last_result on its own")
  # And the stale string is still sitting there, which is why the edge is the only safe test.
  assert so.last_result == "stopped"
