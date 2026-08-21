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
  CLOSING_CONFIRM_FRAMES,
  SLOWDOWN_CONFIRM_FRAMES,
  CLOSING_FRACTION,
  ENTER_SPEED,
  LEAD_DISQUALIFIES_M,
  MAX_ACTIVE_FRAMES,
  MAX_ACTIVE_S,
  MAX_HOLD_S,
  MPH_TO_MS,
  NO_ASK_RELEASE_FRAMES,
  OVERRIDE_HZ,
  FordStopOverride,
)

# 15 -> 30 mph on 2026-08-20. 15 was "inside the regime the set speed cannot reach", which is still
# true and is no longer sufficient: arming below Ford's 20 mph floor is what latches the camera and
# costs the whole drive's ACC (see ARM_MIN_SPEED). 28 is an ordinary measured arm speed -- route
# 000003a0 armed at 28.3 mph, ran 35 s to a full standstill, and drew no cancel at all.
SLOW = 30 * 0.44704
# 40 mph was "comfortably above the entry speed" while the entry was 20. It is now BELOW the 45 mph
# entry, because measured empty-light approaches happen at 28-44 mph and the gate had to move to
# reach them. Anything meant to be refused for speed has to be above the entry, not merely fast.
FAST = 55 * 0.44704


# The model's stop point, metres. Defaults INSIDE the braking range for SLOW so the existing tests
# still describe an arming approach -- at 15 mph the range is (6.7^2 / 3.0) * 1.3 = 19.4 m.
# It is a default rather than an unconditional value so the endpoint gate itself can be tested.
NEAR = 12.0
FAR = 200.0


def _stopping(o, v=SLOW, lead=0.0, has_slow_down=True, op_stopping=True, long_active=True,
              endpoint=NEAR, confirm=True):
  """One call now means ONE APPROACH, not one frame.

  The trigger requires CLOSING_CONFIRM_FRAMES of the stop point closing like a fixed place before it
  will arm -- added 2026-08-20 after he reported that some stop alerts were false positives that
  self-corrected, which became load-bearing once ENTER_SPEED reached 45 mph and a phantom would cost
  a brake rather than a message.

  So this drives a short closing approach ending at `endpoint` and returns the final frame. Every
  test that said "on this input, does it fire?" keeps exactly that meaning, and the input is now a
  realistic approach rather than a single frame that could never happen alone.

  The pre-roll uses the SAME arguments, so a test about a refusal still refuses at its own gate:
  speed, lead and long_active are all checked BEFORE the closing tracker runs, so those inputs never
  accumulate evidence. Pass `confirm=False` to test the confirmation requirement itself.
  """
  # No pre-roll for an endpoint that is not a real distance. There is no realistic approach to a
  # stop point that does not exist, and synthesising one would arm the override DURING the pre-roll
  # and make "no endpoint fails closed" pass for the wrong reason -- or fail, as it did.
  if confirm and endpoint > 0.0:
    travelled = v / OVERRIDE_HZ
    # Start far enough back that the window closes by a full `travelled` per frame, comfortably over
    # the CLOSING_FRACTION the tracker demands. One extra frame so the window is FULL on the final
    # call rather than one sample short.
    n = CLOSING_CONFIRM_FRAMES + 1
    ep = endpoint + n * travelled
    for _ in range(n):
      o.update(long_active=long_active, v_ego=v, has_slow_down=has_slow_down,
               op_stopping=op_stopping, lead_distance=lead, stop_endpoint_m=ep)
      ep -= travelled
  return o.update(long_active=long_active, v_ego=v, has_slow_down=has_slow_down,
                  op_stopping=op_stopping, lead_distance=lead, stop_endpoint_m=endpoint)


def test_it_fires_for_a_stop_the_radar_cannot_see():
  o = FordStopOverride()
  assert _stopping(o) is True
  assert o.active


def test_it_does_not_fire_where_the_set_speed_could_still_have_asked():
  """Above the entry speed ICBM is strictly better -- Ford picks coast vs engine-brake vs friction
  and that blend is the whole reason the division of labour exists.

  The ENTRY MOVED on 2026-08-20 (20 -> 45 mph) but this test did not change meaning: there is still
  a speed above which this must not arm, and the point of the ceiling is that the blend above it is
  Ford's to choose. What changed is only where the line sits, because empty lights turn out to be
  approached at 28-44 mph and a 20 mph line put every one of them on the wrong side."""
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


def test_it_HOLDS_when_the_car_is_stopped():
  """THE CREEP FIX, 2026-08-20. This used to end at a standstill on the reasoning that "Ford's own
  AccStopStat handling takes it from here". It does not -- not without a lead. Ford's stop-and-go
  holds a stop behind a CAR; at an empty light its radar has nothing to hold against, so handing
  back at 0.5 mph handed back to a controller with no reason to keep the car still.

  He reported it directly: *"OP long tried to stop, but it crept forward a bit so I gave up."*
  """
  o = FordStopOverride()
  assert _stopping(o) is True
  assert _stopping(o, v=0.0) is True, "handing back at a standstill is what makes the car creep"
  assert o.holding is True


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
  # DEBOUNCED: one frame of has_slow_down False is a dropped `longitudinalPlanSP`, not a change of
  # mind, and acting on it would drop the brake at a light and reset the once-per-approach latch.
  for _ in range(NO_ASK_RELEASE_FRAMES + 1):
    _stopping(o, has_slow_down=False)      # the reason went away, and stayed away
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
  # `op_stopping` NO LONGER GATES -- it was measured to be a stopped-car state (never true above
  # 3 mph in 21,936 frames), which made the trigger circular and it never fired on any drive.
  assert _stopping(o, op_stopping=False) is True,     "op_stopping must no longer gate: it cannot be true while still approaching"
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
  # THE SIGNATURE IS THE DEFENCE, so every addition has to be argued rather than appended.
  # `slowdown_gap` is how far below the current speed OPENPILOT'S OWN plan wants us for a corner, added
  # 2026-08-20 for the curve path. It is not Ford's number and not a comparison against Ford's: it
  # says "the stalk cannot close this in time", a fact about our plan and the car's press-recognition
  # rate, both of which this file may legitimately know. The forbidden set above is what guards the
  # trap; this list guards against absent-minded plumbing.
  sig = inspect.signature(FordStopOverride.update)
  assert set(sig.parameters) == {"self", "long_active", "v_ego", "has_slow_down", "op_stopping",
                                 "stop_endpoint_m", "slowdown_gap",
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
  # 20.0, raised with ENTER_SPEED so the bound can finish the approach it now permits. Still HALF
  # drive A's 40 s, which is the only thing this bound protects. If this ever needs to go past 20,
  # that is a design question about how long we may contradict the camera, not a number to nudge.
  assert MAX_ACTIVE_S <= 20.0, "half of drive A's 40 s is the entire point of this bound"


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
  """Run a full approach and return the (was_active, override, last_result) triple per frame.

  The six speed rows are preceded by the closing evidence the trigger requires (2026-08-20). Without
  it this fixture held `stop_endpoint_m` at a constant NEAR for the whole approach -- a stop point
  that never gets nearer while the car drives at it, which is precisely the signature of the phantom
  stops the confirmation exists to reject. The rows themselves are unchanged: once armed, the
  tracker is not consulted again.
  """
  out = []
  lead = kw.get("lead", 0.0)
  # 24 -> 30 mph: ARM_MIN_SPEED is 25, and an approach that never gets above it never arms, so the
  # fixture would be testing the floor instead of the hold behaviour it is named for.
  v0 = 30.0 * MPH_TO_MS
  travelled = v0 / OVERRIDE_HZ
  ep = NEAR + (CLOSING_CONFIRM_FRAMES + 1) * travelled
  for _ in range(CLOSING_CONFIRM_FRAMES + 1):
    so.update(long_active=True, v_ego=v0, has_slow_down=True, op_stopping=True,
              lead_distance=lead, stop_endpoint_m=ep)
    ep -= travelled
  for v_mph in (30.0, 18.0, 12.0, 6.0, 2.0, 0.3):
    was_active = so.active
    override = so.update(long_active=True, v_ego=v_mph * MPH_TO_MS, has_slow_down=True,
                         op_stopping=True, lead_distance=lead, stop_endpoint_m=NEAR)
    out.append((was_active, override, so.last_result))
  return out


def test_only_a_stop_WE_are_holding_latches_the_resume_gate():
  """The gate tells `resume_allowed` "this stop was ours, do not pull away on the model's say-so".

  It used to key on `last_result == "stopped"` read on the frame the override ENDED, which had to be
  edge-triggered: `last_result` is a string that persists, so testing it on its own re-latched on
  every later stop for the rest of the drive -- including the queue-cleared open-road case where
  automatic resume is exactly what he wants, and he would instead sit at a green light waiting for a
  press. (Found re-checking my own fix, 2026-08-18.)

  The override HOLDS through a standstill now rather than ending at one, so that end never comes and
  that edge never fires. The gate keys on `holding` instead -- state that is true only while this
  override actually has the car, and that `_end` clears. This test is the same requirement against
  the new signal: it must be true through OUR stop and false through a stop we never touched.
  """
  so = FordStopOverride()
  frames = _drive_to_a_stop(so)
  assert any(f[1] for f in frames), "the override never took the stop at all"
  assert so.holding is True, "the override must still have the car while it is stopped"

  # A SECOND stop the override has nothing to do with: a lead close enough that Ford owns it, so it
  # never arms. `last_result` is still the string from the first stop, which is exactly why the gate
  # must not read that.
  for _ in range(NO_ASK_RELEASE_FRAMES + 1):
    so.update(long_active=True, v_ego=30.0 * MPH_TO_MS, has_slow_down=False,
              op_stopping=False, lead_distance=0.0, stop_endpoint_m=NEAR)
  assert so.holding is False, "the reason going away must release the hold"
  for v_mph in (20.0, 10.0, 4.0, 0.3):
    override = so.update(long_active=True, v_ego=v_mph * MPH_TO_MS, has_slow_down=True,
                         op_stopping=True, lead_distance=25.0, stop_endpoint_m=NEAR)
    assert not override, "a lead inside 60 m is Ford's stop, not ours"
    assert so.holding is False, (
      "a stop the override never took latched the resume gate -- he would sit at a green light")


def test_the_hold_is_released_when_longitudinal_goes_inactive():
  """His brake or his gas ends longitudinal, and the hold must not outlive it -- nothing may be
  authored with longitudinal inactive at all."""
  so = FordStopOverride()
  _drive_to_a_stop(so)
  assert so.holding is True
  assert so.update(long_active=False, v_ego=0.0, has_slow_down=True, op_stopping=True,
                   lead_distance=0.0, stop_endpoint_m=NEAR) is False
  assert so.holding is False


def test_the_hold_is_bounded_so_a_wedged_hold_cannot_last_the_drive():
  """45 s covers an ordinary light. It is REASONED rather than measured -- nothing has ever held a
  stop on this car -- so it stays finite, and the car rolling is the failure it trades against."""
  so = FordStopOverride()
  _drive_to_a_stop(so)
  held = 0
  for _ in range(int(MAX_HOLD_S * OVERRIDE_HZ) + 50):
    if not so.update(long_active=True, v_ego=0.0, has_slow_down=True, op_stopping=True,
                     lead_distance=0.0, stop_endpoint_m=NEAR):
      break
    held += 1
  assert held > OVERRIDE_HZ * 30, f"gave up after {held / OVERRIDE_HZ:.0f}s -- too short for a light"
  assert held <= MAX_HOLD_S * OVERRIDE_HZ + 2, "the hold is effectively unbounded"


def test_the_entry_speed_is_reachable_within_the_time_bound():
  """ENTER_SPEED and MAX_ACTIVE_S are one decision, and they were set independently.

  The bound answers drive A: 8 s, five times under the ~40 s of sustained contradiction that latched
  the camera. The entry speed answered a different question -- where the set speed stops being able
  to express the request. Nobody checked they agreed, and at 25 mph they did not:

      from 25 mph   1.5 m/s^2 -> 7.5 s    1.2 -> 9.3 s    1.0 -> 11.2 s
      from 20 mph   1.5 m/s^2 -> 6.0 s    1.2 -> 7.5 s    1.0 ->  8.9 s

  openpilot's e2e stops run about 1.0-1.5 m/s^2, so 25 put the LIKELY deceleration over the bound.
  A stop that runs out of time hands back mid-stop while Ford's set speed is still 20 -- so the car
  stops braking and accelerates toward 20 short of the line.

  The bound is not the thing to relax: it is the only number here sized against a measured failure.
  So the entry speed is what has to fit inside it. Asserted at 1.2 m/s^2, comfortably inside the
  range openpilot actually plans, so this goes red if either constant drifts back out of agreement.
  """
  # ASSERTED AGAINST THE BEHAVIOUR, NOT A HARDCODED RATE. This used to assume 1.2 m/s^2, which
  # tracked the gate only while arming required 1.15; when STOP_DECEL moved to 0.9 in the same
  # commit that raised ENTER_SPEED, the two silently decoupled and this test certified a bound the
  # code could exceed by 9 s. Review caught it, not the suite.
  #
  # So: find the furthest stop the code will ACTUALLY arm on at the entry speed, and require the
  # time bound to cover it. Binary search rather than arithmetic, so it keeps holding if the gate
  # is ever rewritten.
  lo, hi = 0.0, 1000.0
  for _ in range(60):
    mid = (lo + hi) / 2.0
    if _stopping(FordStopOverride(), v=ENTER_SPEED, op_stopping=False, endpoint=mid):
      lo = mid
    else:
      hi = mid
  furthest = lo
  assert furthest > 1.0, "nothing arms at the entry speed at all -- the gate is broken, not the bound"
  # Stopping from v over distance d takes 2d/v.
  seconds_needed = 2.0 * furthest / ENTER_SPEED
  assert seconds_needed <= MAX_ACTIVE_S, (
    f"the gate arms up to {furthest:.0f} m at the entry speed, which needs {seconds_needed:.1f} s "
    f"to stop, and the bound is {MAX_ACTIVE_S:.1f} s -- it would hand back mid-stop, below Ford's "
    f"floor, with the stop still ahead")


def test_the_override_overlaps_fords_floor_rather_than_meeting_it():
  """The override must arm ABOVE Ford's floor, and the two must never leave a gap.

  This asserted EQUALITY until 2026-08-20, on the reasoning that above the floor Ford is still able
  to carry the request so the division of labour says leave it there. True as far as it goes, and it
  produced a feature that could not fire: arming exactly AT the floor makes this a race against the
  instant Ford bails rather than a decision taken before it.

  Measured, route 0000039a: 930 frames refused here for "too fast", and the whole window where the
  car was slow enough, had no lead and the model had an endpoint was 13 frames -- 0.26 s.

  So the invariant is an ORDERING, not an equality. `ENTER_SPEED >= ACC_FLOOR_MS` is what actually
  matters: an overlap means both authorities can act in the band and the handoff has somewhere to
  happen, while ENTER_SPEED *below* the floor is the real defect -- a band where Ford has quit and
  the override is not yet allowed, which is precisely where a light goes unstopped.

  The upper bound keeps it honest. Arming far above the floor spends the time bound on deceleration
  Ford is doing perfectly well by itself."""
  from openpilot.sunnypilot.selfdrive.controls.lib.unconfirmed_lead import ACC_FLOOR_MS

  assert ENTER_SPEED >= ACC_FLOOR_MS, (
    f"the override arms at {ENTER_SPEED / MPH_TO_MS:.1f} mph but Ford quits at "
    f"{ACC_FLOOR_MS / MPH_TO_MS:.1f} mph -- that band belongs to nobody and a stop in it is missed")

  # THE UPPER BOUND IS DERIVED, NOT PICKED. It was `<= 30.0` for about an hour -- a ceiling chosen
  # after the fact to accommodate the 25 mph margin the same change had just introduced, which
  # cannot fail for any ENTER_SPEED under 50 mph and so constrained nothing. Review called that
  # passing by construction, correctly.
  #
  # The real limit on how far above the floor this may arm is whether the time bound can still
  # deliver the stop: `test_the_time_bound_is_expressed_in_SECONDS_at_the_rate_it_actually_ticks`
  # owns that and asserts it against the gate's own behaviour. What belongs HERE is the other half
  # -- that the two authorities overlap rather than leaving a band owned by nobody -- plus a sanity
  # ceiling that is a statement about the road rather than about the current constant.
  #
  # 60 mph: above that a "stop ahead" is a highway event, and taking it is a different feature with
  # a different argument, not a wider margin on this one.
  margin_mph = (ENTER_SPEED - ACC_FLOOR_MS) / MPH_TO_MS
  assert ENTER_SPEED / MPH_TO_MS <= 60.0, (
    f"the override arms at {ENTER_SPEED / MPH_TO_MS:.0f} mph -- a stop ahead at highway speed is a "
    f"different feature, argued separately, not a wider margin on this one (currently "
    f"{margin_mph:.0f} mph above Ford's floor)")


def test_a_radar_blind_lead_is_ours_and_a_radar_lead_is_fords():
  """The unconfirmed-lead case and the stop override compose, and the seam is `radarState`.

  `unconfirmed_lead.py` exists because Ford's ACC follows only RADAR-confirmed leads, and the model
  regularly sees a stopped car at the end of a queue that the radar never returns. Its own docstring
  said reaching the 20 mph floor was "the end of what this can do, not the start of a stop" -- true
  when it was written, and no longer true, because the override brakes below the floor.

  The two need no wiring between them. `LEAD_DISQUALIFIES_M` reads `radarState.leadOne`, which is
  exactly the thing that is ABSENT in the radar-blind case, so the override arms for it on its own.

  And the handback is the same seam read the other way. `unconfirmed_lead`'s expected resolution is
  that slowing lets the radar finally acquire the lead, after which Ford follows it down itself.
  The frame the radar acquires is the frame `lead_distance` becomes real -- so the override ends and
  hands Ford back its own stop-and-go, which is better than ours. That is one condition serving two
  features, not a coincidence to leave untested."""
  # Radar-blind: the model is planning a stop, the radar has nothing. lead_distance is 0.0, which
  # is what `radarState.leadOne.status == False` produces in the carcontroller.
  blind = FordStopOverride()
  assert _stopping(blind, lead=0.0) is True, (
    "the override refused a stop the radar cannot see -- the exact case unconfirmed_lead is for")

  # ...and the moment the radar acquires it, Ford owns the stop again.
  acquired = blind.update(long_active=True, v_ego=SLOW, has_slow_down=True, op_stopping=True, stop_endpoint_m=NEAR,
                          lead_distance=LEAD_DISQUALIFIES_M - 20.0)
  assert acquired is False, "kept braking after the radar acquired the lead"
  assert "lead appeared" in blind.last_result


# --- the trigger swap: a distance, not the plan's post-hoc commitment ---------------------------

class TestTheEndpointArmsItNotShouldStop:
  """WHY `op_stopping` WAS REPLACED, 2026-08-20. Measured over 21,936 frames on three drives where
  `longitudinalPlan.shouldStop` was true:

      0000039a  5169 frames  max 1.7 mph      00000393  7103  max 2.9 mph
      00000397  9664 frames  max 2.8 mph      above 5 mph: 0.0% on ALL THREE

  It is a STOPPED-CAR state. With `v_ego > STOPPED_SPEED` also required, the arming window was
  0.5-2.9 mph -- nothing left to stop. The trigger was circular and never fired on any drive.

  His light on 0000039a: engaged, foot off the brake, set speed walking 80 -> 57, holding 20 mph,
  `shouldStop` false throughout. He braked.
  """

  def test_it_arms_while_still_approaching_with_the_plan_uncommitted(self):
    """THE WHOLE POINT. This is the state his light was actually in."""
    o = FordStopOverride()
    assert _stopping(o, op_stopping=False, endpoint=NEAR) is True

  def test_a_far_stop_does_not_arm_it(self):
    """`has_slow_down` alone is far too loose -- 8,207 frames on one drive. The model's own stop
    point has to be close enough that braking is actually due, or this becomes 'brake whenever the
    model feels uneasy'."""
    o = FordStopOverride()
    assert _stopping(o, endpoint=FAR) is False

  def test_no_endpoint_fails_CLOSED(self):
    """`endpoint_x()` is inf when the plan is not full length and inf is clamped to 0 on the wire,
    so 0 means "no endpoint" -- NEVER "stopping right here". Arming on 0 would fire at every light
    the model merely felt uneasy about, which is the opposite of a named bounded condition."""
    o = FordStopOverride()
    assert _stopping(o, endpoint=0.0) is False
    assert _stopping(o, endpoint=-1.0) is False

  def test_the_arming_range_scales_with_speed(self):
    """A fixed metre count is wrong at both ends -- the same reason SCC-Map uses a trigger distance
    rather than a constant. Faster means arm sooner."""
    # Through `_stopping` so both sides get the closing evidence the trigger now requires; a bare
    # single `update` can no longer arm anything and would make this test read as "19 mph refuses".
    # Both speeds moved above Ford's floor on 2026-08-20: below it the arm is refused outright by
    # ARM_MIN_SPEED, so 19 vs 6 mph no longer measures the distance gate at all -- it measures the
    # floor twice and would pass for the wrong reason.
    # 120 m sits between the two gates: 178.8 m at 40 mph, 69.9 m at 22 mph. A distance inside BOTH
    # would pass for the wrong reason -- the first version of this used 60 m and did exactly that.
    # BOTH speeds above ARM_MIN_SPEED, or the slow side is refused by the floor and this measures
    # the floor twice instead of the distance gate. 140 m sits between the two gates: 178.8 m at
    # 40 mph, 105 m at 27 mph.
    fast_ok = _stopping(FordStopOverride(), v=40 * 0.44704, op_stopping=False, endpoint=140.0)
    slow_no = _stopping(FordStopOverride(), v=27 * 0.44704, op_stopping=False, endpoint=140.0)
    assert fast_ok is True, "40 mph with a 140 m stop should be arming"
    assert slow_no is False, "27 mph with a 140 m stop is far too early"

  def test_a_crawl_with_nothing_ahead_does_not_arm(self):
    """THE FLOOR IS GONE, and this is why. `STOP_MIN_RANGE_M = 10.0` was added so the last few mph
    stayed reachable. It did the opposite: the model's trajectory horizon is roughly 10*v, so below
    about 2.2 mph the horizon is ITSELF under 10 m and the gate armed with NOTHING AHEAD -- a
    spurious brake-to-a-stop at walking pace, in traffic. The arithmetic needs no floor: at low
    speed the remaining stopping distance is short too."""
    o = FordStopOverride()
    # 1.5 mph, free-flow: the model's horizon is ~6.7 m and there is no stop at all.
    assert _stopping(o, v=1.5 * 0.44704, endpoint=6.7) is False,       "armed at a crawl with an empty road ahead"

  def test_the_last_few_mph_of_a_REAL_stop_no_longer_ARM(self):
    """REVERSED 2026-08-20, and the road is why. This asserted `is True`.

    Arming at a crawl was thought harmless -- the endpoint is close, so the arithmetic agrees. What
    the arithmetic could not see is the CAMERA. Taking ACC authority below Ford's 20 mph floor makes
    it assert `AccCancl_B_Rq`, and on route 000003a0 it then never released: one cancel transition
    in the last 550 s of the drive, and it was the ON. Ford's command was never forwarded again and
    he was on openpilot longitudinal until he restarted the car.

    Both arms below the floor across four drives provoked a cancel; every arm above it was tolerated,
    including a 35 s one that ran to a full standstill. So the last few mph of a stop are reached by
    ARMING EARLIER AND CARRYING THE CAR DOWN, never by grabbing authority at the bottom.
    """
    o = FordStopOverride()
    assert _stopping(o, v=4 * 0.44704, endpoint=1.0) is False,       "armed below Ford's floor -- this is what latched the camera and cost a whole drive of ACC"

  def test_an_override_armed_above_the_floor_carries_the_car_below_it(self):
    """The other half, and the reason the bound is on ARMING only.

    Once it has authority, going under 20 mph is fine and measured to be fine -- route 000003a0
    armed at 28.3 mph and ran 35 s to a standstill with no cancel at all. A bound that ended the
    override at 20 would abandon every stop at exactly the point Ford also gives up.
    """
    o = FordStopOverride()
    assert _stopping(o, v=30 * 0.44704, endpoint=40.0) is True, "did not arm above the floor"
    # Now below the floor, still asking: it must keep the car.
    for mph in (18.0, 12.0, 6.0, 1.0):
      out = o.update(long_active=True, v_ego=mph * 0.44704, has_slow_down=True, op_stopping=False,
                     lead_distance=0.0, stop_endpoint_m=max(0.5, 40.0 * mph / 30.0))
      assert out is True, f"handed back at {mph} mph -- the stop is abandoned where Ford also quits"

  def test_every_other_gate_still_refuses(self):
    """The trigger got cheaper, so the gates that make it SAFE must be untouched -- speed ceiling,
    the lead carve-out, and longitudinal being active."""
    assert _stopping(FordStopOverride(), v=FAST) is False, "above the entry speed the set speed owns it"
    assert _stopping(FordStopOverride(), lead=25.0) is False, "a lead is Ford's stop-and-go"
    assert _stopping(FordStopOverride(), long_active=False) is False
    assert _stopping(FordStopOverride(), has_slow_down=False) is False


def test_it_survives_a_WHOLE_approach_with_the_plan_never_committing():
  """THE REGRESSION TEST FOR THE BUG THE REVIEW FOUND, and the reason no test caught it.

  `op_stopping` was removed from ARMING and left as the per-frame SUSTAIN gate, so the override
  armed on frame 1 and `_end`ed on frame 2 with `spent = True` -- dead for the rest of the approach,
  exactly as dead as before the fix, with the circularity moved sixty lines down instead of removed.

  Every existing fixture hid it: `_stopping` defaults `op_stopping=True`, `_drive_to_a_stop`
  hardcodes it True, and the one test that passed False called `update` ONCE and asserted the return
  value. A single-frame assertion cannot see a state machine that dies on frame two.

  So this drives a whole approach with `op_stopping` False throughout -- which is what a real
  approach looks like, since `shouldStop` is a stopped-car state.
  """
  o = FordStopOverride()
  v = 30.0 * MPH_TO_MS          # above ARM_MIN_SPEED, or it never arms and tests nothing
  # A STOP POINT FIXED IN THE WORLD, closed at the speed the car is travelling. This used to be
  # recomputed from `v` every frame, so before the override took over -- when `v` is constant -- the
  # endpoint was constant too: a stop that never gets closer while you drive at it. That is the
  # phantom signature, so with the closing confirmation it could never arm, and the fixture would
  # have reported the feature broken when it was the fixture that was unphysical.
  dist = (v * v) / (2 * 1.2)                          # a 1.2 m/s^2 model stop from here
  took = 0
  for _ in range(1200):
    if o.update(long_active=True, v_ego=v, has_slow_down=True, op_stopping=False,
                lead_distance=0.0, stop_endpoint_m=max(0.0, dist)):
      took += 1
      v = max(0.0, v - 1.2 / OVERRIDE_HZ)             # it is braking, so the car slows
    dist = max(0.0, dist - v / OVERRIDE_HZ)
    if v <= 0.05:
      break
  assert took > OVERRIDE_HZ, f"the override held the car for only {took / OVERRIDE_HZ:.2f}s"
  assert o.holding is True, "it never reached the standstill it was braking toward"


class TestAPhantomStopDoesNotArmABrake:
  """HIS THREE MEASURED SHAPES, from route 0000039c. *"Maybe there were some false positives but
  they self-corrected."*

  That remark was harmless while ENTER_SPEED was 20 mph -- a phantom cost an alert. At 45 mph it
  costs a brake on an open road, so the trigger has to tell them apart. Every engaged asking-episode
  over 3 s on that drive, with the endpoint at the start and end:

      REAL      t=68    13.9 s   105 ->  32 m   (he confirmed this one as his traffic light)
                t=281   29.4 s     6 ->   0 m
                t=1009  57.5 s   139 ->   0 m
      PHANTOM   t=329    4.1 s   132 -> 148 m
                t=965    5.0 s   141 -> 142 m
                t=370    4.2 s    32 ->  73 m

  A stop point fixed in the world must get closer as the car drives at it. None of the phantoms do.
  """

  @staticmethod
  def _episode(first_ep, last_ep, seconds, v_mph=32.0):
    """Drive an endpoint linearly from first_ep to last_ep and report whether it ever armed."""
    o = FordStopOverride()
    v = v_mph * MPH_TO_MS
    n = max(2, int(seconds * OVERRIDE_HZ))
    armed = False
    for i in range(n):
      ep = first_ep + (last_ep - first_ep) * (i / (n - 1))
      if o.update(long_active=True, v_ego=v, has_slow_down=True, op_stopping=False,
                  lead_distance=0.0, stop_endpoint_m=ep):
        armed = True
    return armed

  def test_an_endpoint_that_grows_never_arms(self):
    assert self._episode(132.0, 148.0, 4.1, v_mph=35.0) is False, "armed on a stop moving AWAY"
    assert self._episode(32.0, 73.0, 4.2, v_mph=18.0) is False, "armed on a stop moving AWAY"

  def test_an_endpoint_that_does_not_close_never_arms(self):
    """141 -> 142 m over 5 s while covering ~94 m of road. Whatever that is, it is not a place."""
    assert self._episode(141.0, 142.0, 5.0, v_mph=42.0) is False, "armed on a stop that never neared"

  def test_his_real_traffic_light_still_arms(self):
    """The one that matters: t=68, 105 -> 32 m over 13.9 s at ~30 mph, no car at the light.

    The confirmation must not cost this. If this ever goes red the feature is off again and he
    drives through another light."""
    assert self._episode(105.0, 32.0, 13.9, v_mph=30.0) is True, "his actual traffic light no longer arms"

  def test_a_real_stop_that_reaches_zero_still_arms(self):
    """t=1009 ran 43 mph to a standstill. Modelled as a real fixed point rather than replayed
    linearly: that episode's 57.5 s window spans the approach, the standstill and a second stop, so
    its 139 -> 42 m net figure describes the WINDOW, not the approach. Driving a constant 43 mph
    across it -- as this test first did -- covers 1.1 km while the endpoint gives up 139 m, which is
    not an approach at all and correctly fails. The fixture was wrong, not the gate."""
    o = FordStopOverride()
    v = 43.0 * MPH_TO_MS
    dist = 139.0
    armed = False
    for _ in range(int(30.0 * OVERRIDE_HZ)):
      if o.update(long_active=True, v_ego=v, has_slow_down=True, op_stopping=False,
                  lead_distance=0.0, stop_endpoint_m=max(0.0, dist)):
        armed = True
        v = max(0.0, v - 1.2 / OVERRIDE_HZ)
      dist = max(0.0, dist - v / OVERRIDE_HZ)
      if v <= 0.05:
        break
    assert armed is True, "a genuine approach to a fixed stop point never armed"


def test_a_lead_does_not_freeze_the_closing_window():
  """THE REVIEW BUG, 2026-08-20. A refusal must not leave stale evidence behind.

  The closing tracker sat below the speed and lead gates, which `return False` without appending or
  clearing. So while a lead refused the override, the window FROZE -- and its samples aged while the
  car kept driving. When the lead cleared, `closing_window[0]` was a reading from tens of seconds
  and hundreds of metres earlier, and comparing it against the current endpoint passed the
  confirmation instantly.

  That defeats the phantom filter on the most ordinary event on the road: `a lead appeared` ended
  the override on 13,012 frames of the 0000039c replay.

  So: earn honest evidence, sit behind a lead while the world moves on, then present a stop point
  that DOES NOT CLOSE AT ALL. It must be refused on its own merits.
  """
  o = FordStopOverride()
  v = 30.0 * MPH_TO_MS
  step = v / OVERRIDE_HZ

  ep = 200.0
  for _ in range(CLOSING_CONFIRM_FRAMES - 1):
    o.update(long_active=True, v_ego=v, has_slow_down=True, op_stopping=False,
             lead_distance=0.0, stop_endpoint_m=ep)
    ep -= step

  for _ in range(int(20 * OVERRIDE_HZ)):
    o.update(long_active=True, v_ego=v, has_slow_down=True, op_stopping=False,
             lead_distance=25.0, stop_endpoint_m=ep)

  armed = False
  for _ in range(20):
    if o.update(long_active=True, v_ego=v, has_slow_down=True, op_stopping=False,
                lead_distance=0.0, stop_endpoint_m=20.0):
      armed = True
  assert not armed, "armed on stale evidence banked before a lead, with a static endpoint since"


def test_a_lead_clearing_on_a_REAL_approach_still_arms():
  """The other half, so the fix above cannot be 'clear the window and re-earn it'.

  A lead ahead does not make the stop behind it imaginary. The endpoint keeps closing while the car
  follows, that evidence is true, and throwing it away would make the override re-earn 1.5 s of
  confirmation after every car that merges in -- at exactly the moment it is needed.
  """
  o = FordStopOverride()
  v = 30.0 * MPH_TO_MS
  step = v / OVERRIDE_HZ
  # Starts inside the arming range for 30 mph so the DISTANCE gate is not what this test measures.
  ep = 100.0

  # Behind a lead the whole time, genuinely closing on a real stop.
  for _ in range(int(3 * OVERRIDE_HZ)):
    o.update(long_active=True, v_ego=v, has_slow_down=True, op_stopping=False,
             lead_distance=25.0, stop_endpoint_m=ep)
    ep -= step

  # The lead changes lanes. The stop is real and close; it should be taken at once.
  armed = o.update(long_active=True, v_ego=v, has_slow_down=True, op_stopping=False,
                   lead_distance=0.0, stop_endpoint_m=ep)
  assert armed is True, "a real closing approach had to re-earn confirmation after the lead left"


def test_the_arming_range_never_exceeds_what_the_time_bound_can_finish():
  """Checked across the speed range, not just at ENTER_SPEED.

  `brake_range` grows with v^2 while the reachable range grows with v, so the two cross and the
  quadratic one wins at the top. Sampling several speeds keeps this honest if either constant moves.
  """
  for mph in (25.0, 30.0, 35.0, 40.0, 45.0):
    v = mph * MPH_TO_MS
    lo, hi = 0.0, 1000.0
    for _ in range(60):
      mid = (lo + hi) / 2.0
      if _stopping(FordStopOverride(), v=v, op_stopping=False, endpoint=mid):
        lo = mid
      else:
        hi = mid
    seconds = 2.0 * lo / v
    assert seconds <= MAX_ACTIVE_S + 0.01, (
      f"at {mph:.0f} mph the gate arms up to {lo:.0f} m, a {seconds:.1f} s stop, against a "
      f"{MAX_ACTIVE_S:.1f} s bound")


class TestTheCurvePath:
  """Braking a corner harder than the stalk can. Added 2026-08-20 from a measured drive.

  A 77 mph approach to a 28 mph corner: SCC-Map asked at t+1.2, the car was already pulling
  4.68 m/s^2 lateral by t+3.7 and peaked at 5.20 against a 2.4 target, and the set speed did not
  reach 25 mph until t+15.0. Nothing malfunctioned -- the buttons move 1 mph per 0.30 s and that is
  the ceiling. Authoring the command directly has no such limit.

  ARMED ON THE SPEED GAP, not on a requested deceleration. `actuators.accel` was the first attempt
  and is unusable here: under ICBM openpilot's longitudinal controller is not driving, so it winds
  up to its -3.5 floor for more than 10% of engaged frames. The gap is what the stalk has to close.

  MOST CURVES DO NOT END IN A STOP, which is his point and the reason the release path matters more
  than the standstill one: the ordinary exit is the corner ending and the gap closing.
  """

  @staticmethod
  def _drive(o, mph, gap_mph, frames):
    out = False
    for _ in range(frames):
      out = o.update(long_active=True, v_ego=mph * MPH_TO_MS, has_slow_down=False,
                     op_stopping=False, lead_distance=0.0, stop_endpoint_m=0.0,
                     slowdown_gap=gap_mph * MPH_TO_MS)
    return out

  def test_it_arms_on_a_gap_the_buttons_cannot_close_in_time(self):
    """His corner was a 49 mph gap. The stalk closes 3.3 mph/s, so that is 15 s of tapping."""
    o = FordStopOverride()
    assert self._drive(o, 60.0, 49.0, SLOWDOWN_CONFIRM_FRAMES + 2) is True
    assert o.slowdown_active

  def test_it_stays_out_of_gaps_the_buttons_can_manage(self):
    """A 12 mph gap is under four seconds of tapping. Taking the command there would contradict the
    camera for no gain -- ICBM had it. Measured: gaps this small are the ordinary case."""
    o = FordStopOverride()
    self._drive(o, 60.0, 12.0, SLOWDOWN_CONFIRM_FRAMES + 40)
    assert not o.slowdown_active

  def test_the_ordinary_exit_is_the_gap_closing(self):
    """THE COMMON CASE. Through the corner, the gap shrinks, Ford gets the car back. No standstill
    involved -- his words: *"it won't always reach a standstill"*."""
    o = FordStopOverride()
    assert self._drive(o, 60.0, 49.0, SLOWDOWN_CONFIRM_FRAMES + 2) is True
    assert self._drive(o, 35.0, 3.0, 2) is False, "did not hand back once the gap closed"
    assert not o.slowdown_active

  def test_hysteresis_so_it_cannot_flap(self):
    """Every arm and release is a handoff, and handoffs are what the camera reacts to. A gap
    hovering near the arm threshold must not toggle the command on and off."""
    o = FordStopOverride()
    assert self._drive(o, 60.0, 49.0, SLOWDOWN_CONFIRM_FRAMES + 2) is True
    # Below the arm threshold but above the release threshold: keep it.
    assert self._drive(o, 50.0, 14.0, 20) is True, "released inside the hysteresis band"
    assert o.slowdown_active

  def test_a_lead_hands_it_straight_back(self):
    o = FordStopOverride()
    assert self._drive(o, 60.0, 49.0, SLOWDOWN_CONFIRM_FRAMES + 2) is True
    out = o.update(long_active=True, v_ego=60.0 * MPH_TO_MS, has_slow_down=False, op_stopping=False,
                   lead_distance=25.0, stop_endpoint_m=0.0, slowdown_gap=49.0 * MPH_TO_MS)
    assert out is False and not o.slowdown_active

  def test_it_will_not_arm_below_fords_floor(self):
    """The same rule as the stop path, and for the same measured reason -- arming under Ford's floor
    is what latches the camera for the whole drive."""
    o = FordStopOverride()
    self._drive(o, 18.0, 49.0, SLOWDOWN_CONFIRM_FRAMES + 20)
    assert not o.slowdown_active

  def test_it_will_not_arm_above_the_curve_ceiling(self):
    o = FordStopOverride()
    self._drive(o, 95.0, 49.0, SLOWDOWN_CONFIRM_FRAMES + 20)
    assert not o.slowdown_active

  def test_a_curve_that_does_reach_a_stop_hands_into_the_hold(self):
    """When the corner IS the stop -- his sharp exit into a red light -- releasing at a standstill
    would be the creep this feature was rewritten to remove."""
    o = FordStopOverride()
    assert self._drive(o, 40.0, 39.0, SLOWDOWN_CONFIRM_FRAMES + 2) is True
    out = o.update(long_active=True, v_ego=0.1 * MPH_TO_MS, has_slow_down=False, op_stopping=False,
                   lead_distance=0.0, stop_endpoint_m=0.0, slowdown_gap=39.0 * MPH_TO_MS)
    assert out is True, "let go at a standstill"
    assert o.holding and not o.slowdown_active, "should be the stop path's hold now"

  def test_longitudinal_going_inactive_drops_it(self):
    o = FordStopOverride()
    assert self._drive(o, 60.0, 49.0, SLOWDOWN_CONFIRM_FRAMES + 2) is True
    out = o.update(long_active=False, v_ego=60.0 * MPH_TO_MS, has_slow_down=False,
                   op_stopping=False, lead_distance=0.0, stop_endpoint_m=0.0,
                   slowdown_gap=49.0 * MPH_TO_MS)
    assert out is False and not o.slowdown_active

  def test_the_radar_acquiring_the_lead_hands_straight_back_to_ford(self):
    """THE UNCONFIRMED-LEAD SEQUENCE, and the seam that makes it safe.

    A stopped car the radar cannot see opens a gap the stalk cannot close -- measured on his drives
    at 48.6 mph with a 28 mph gap and 52.4 mph with 32. openpilot covers that blind window. THE
    MOMENT THE RADAR ACQUIRES IT, Ford's stop-and-go is better than ours and gets the car back on
    that frame, which is exactly what `unconfirmed_lead.py` describes as the expected resolution
    rather than a failure.
    """
    o = FordStopOverride()
    assert self._drive(o, 52.0, 32.0, SLOWDOWN_CONFIRM_FRAMES + 2) is True
    assert o.slowdown_active, "did not cover the radar-blind window"

    # The radar finally returns it. Ford owns everything from here.
    out = o.update(long_active=True, v_ego=45.0 * MPH_TO_MS, has_slow_down=False, op_stopping=False,
                   lead_distance=40.0, stop_endpoint_m=0.0, slowdown_gap=32.0 * MPH_TO_MS)
    assert out is False, "kept the command after the radar acquired the lead"
    assert not o.slowdown_active

  def test_the_stop_path_still_outranks_it(self):
    """Only one may author. A stop already underway must not be interrupted by the curve path."""
    o = FordStopOverride()
    assert _stopping(o) is True and o.active
    out = o.update(long_active=True, v_ego=SLOW, has_slow_down=True, op_stopping=False,
                   lead_distance=0.0, stop_endpoint_m=NEAR, slowdown_gap=49.0 * MPH_TO_MS)
    assert out is True
    assert o.active and not o.slowdown_active


def test_a_NaN_endpoint_fails_closed():
  """`stop_endpoint_m <= 0.0` does NOT catch NaN -- every comparison against NaN is False, so it
  fell through both the zero test and the range test straight into arming, which is the exact
  opposite of the "FAILS CLOSED" comment above it. The publisher's isfinite guard is in a different
  repo, so this file cannot lean on it."""
  assert _stopping(FordStopOverride(), endpoint=float("nan")) is False


def test_a_lead_arriving_mid_hold_hands_back_to_ford():
  """The standstill branch used to return before `lead_close` was evaluated, so this was
  unreachable -- and the module docstring's "a lead appeared. Hand back." was false at a standstill.
  Ford's stop-and-go holds behind a car better than we do; that is why a lead disqualifies the
  override at all."""
  o = FordStopOverride()
  _drive_to_a_stop(o)
  assert o.holding is True
  assert _stopping(o, v=0.0, lead=20.0) is False
  assert o.holding is False


def test_the_corner_target_stays_under_the_measured_collapse():
  """A GUARD, not a mirror. The mapd tests import `_CORNER_LAT_ACC` and assert against it, which
  makes them track any value rather than bound it -- setting it to 9.9 leaves them all green. The
  collapse point is measured: tracking shortfall is flat to 2.5 m/s^2 and jumps to 0.909 above it,
  so the constant may never reach that without new measurement saying otherwise."""
  from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.mapd_v2_path import (
    _CORNER_LAT_ACC,
  )
  assert 1.0 < _CORNER_LAT_ACC <= 2.5, \
    f"{_CORNER_LAT_ACC} m/s2 is at or past the measured tracking collapse"
