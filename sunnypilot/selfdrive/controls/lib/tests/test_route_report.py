"""BluePilot: the route reader, tested against a fake log rather than a real route.

2026-08-06: *"I haven't been paying attention to anything on the screen. I hope logs will tell a
lot."* This is what makes that true. The reader itself cannot be run against a device route offline,
but every conclusion it draws is arithmetic on messages, and that IS testable -- so the reasoning is
pinned here even though the file reading is not.

The reconstruction that matters is the cancel gesture: carState is 100 Hz, so the switch position is
sampled far finer than the counters in auto_lane_change manage, and a nudge visible for one gateway
frame shows up here even if the state machine never looked on that frame.
"""

import importlib.util
import pathlib
from types import SimpleNamespace as NS


def _load():
  root = pathlib.Path(__file__).resolve()
  while not (root / "common" / "params_keys.h").exists():
    root = root.parent
  path = root / "tools" / "bp_route_report.py"
  spec = importlib.util.spec_from_file_location("bp_route_report", path)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


RR = _load()


def car_state(t, left=False, right=False, pressed=False):
  return NS(logMonoTime=int(t * 1e9), which=lambda: "carState",
            carState=NS(leftBlinker=left, rightBlinker=right, steeringPressed=pressed))


def plan(t, suggestion="none", blocked="noLaneAvailable", maneuver="idle",
         geo=(1, 0.31, 0.86, 0.30), overtaken=(0, 0)):
  pa = NS(suggestion=suggestion, blockedBy=blocked, maneuver=maneuver,
          geoRefusedBy=geo[0], geoRefusedValue=geo[1], geoRefusedShare=geo[2], geoLoosenTo=geo[3],
          adjacentLeft=NS(overtakenCount=overtaken[0]),
          adjacentRight=NS(overtakenCount=overtaken[1]))
  return NS(logMonoTime=int(t * 1e9), which=lambda: "longitudinalPlanSP",
            longitudinalPlanSP=NS(passingAssist=pa))


def read(msgs):
  """Drive the reader with a canned message list instead of a file."""
  RR.read.__globals__["LogReader"] = lambda _p: msgs
  import sys
  fake = NS(LogReader=lambda _p: msgs)
  sys.modules["openpilot.tools.lib.logreader"] = fake
  return RR.read("fake-route")


# --- the gesture, which is the whole reason carState is walked at all ----------------------------

def test_a_nudge_that_reaches_the_other_side_is_seen():
  msgs = [car_state(0.0, left=True), car_state(1.0, left=True),
          car_state(1.5, right=True),        # the nudge, one sample
          car_state(2.0)]                    # and the signal is out
  d = read(msgs)
  assert d["lane_changes"] == 1
  assert d["opposite_switch"] == 1
  assert d["signal_out"] == 0


def test_a_nudge_that_never_reaches_it_reads_as_the_signal_going_out():
  """His car, if the stalk returns toward centre without hitting position 2. The distinction the
  whole cancel design turns on, and it cannot be inferred -- only counted."""
  msgs = [car_state(0.0, left=True), car_state(1.0, left=True), car_state(2.0)]
  d = read(msgs)
  assert d["opposite_switch"] == 0
  assert d["signal_out"] == 1
  assert d["signal_out_steering"] == 0


def test_taking_over_by_hand_is_counted_separately():
  """Hold-and-release with torque on the wheel is NOT a cancel, and the report has to be able to
  say how many of the signal-outs were that rather than a nudge."""
  msgs = [car_state(0.0, left=True), car_state(1.0, left=True, pressed=True), car_state(2.0)]
  d = read(msgs)
  assert d["signal_out"] == 1
  assert d["signal_out_steering"] == 1


def test_several_changes_are_counted_separately():
  msgs = [car_state(0.0, left=True), car_state(1.0),
          car_state(2.0, right=True), car_state(3.0),
          car_state(4.0, left=True), car_state(5.0)]
  d = read(msgs)
  assert d["lane_changes"] == 3


def test_a_signal_left_on_at_the_end_is_not_counted():
  """An unfinished change is not evidence about a gesture that never happened."""
  msgs = [car_state(0.0, left=True), car_state(1.0, left=True)]
  d = read(msgs)
  assert d["lane_changes"] == 0


# --- the geometry gate, which is the number that has been blocking everything --------------------

def test_it_reports_the_term_to_change_and_what_to_set_it_to():
  d = read([plan(0.0, geo=(1, 0.31, 0.86, 0.30))])
  out = RR.report(d)
  assert "paint" in out
  assert "0.30" in out
  assert "86%" in out


def test_the_term_index_maps_to_the_gate_s_own_order():
  assert RR.GEO_TERMS[0] == "edge unsure"
  assert RR.GEO_TERMS[1] == "paint"
  assert RR.GEO_TERMS[2] == "lane width"
  assert RR.GEO_TERMS[3] == "room past it"


def test_a_drive_with_no_suggestions_says_so_rather_than_reading_as_quiet():
  d = read([plan(t / 10) for t in range(20)])
  out = RR.report(d)
  assert "none at all" in out


def test_a_suggestion_is_counted():
  d = read([plan(0.0, suggestion="left", blocked="none")])
  assert sum(v for k, v in d["suggested"].items() if k != "none") == 1


# --- the scenery, which is what he saw on the speed readout --------------------------------------

def test_an_implausible_overtake_rate_is_called_out():
  """Fifty in a few minutes is what he reported. A rate, not a raw count, because the raw number
  means nothing without knowing how long he drove."""
  d = read([car_state(0.0), plan(0.0, overtaken=(25, 25)), car_state(180.0)])
  out = RR.report(d)
  assert "50 vehicles" in out
  assert "implausible" in out


def test_a_sane_overtake_rate_is_not_flagged():
  d = read([car_state(0.0), plan(0.0, overtaken=(1, 1)), car_state(600.0)])
  out = RR.report(d)
  assert "implausible" not in out
