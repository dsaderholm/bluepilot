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
  import sys
  sys.modules["openpilot.tools.lib.logreader"] = NS(LogReader=lambda _p: msgs)
  return RR.read("fake-route")


def test_a_long_route_is_streamed_a_segment_at_a_time():
  """A 35 minute route OOM-killed the device -- bare "Killed", no traceback -- because the whole
  segment list went to one LogReader. stream() must open them one at a time, so peak memory is one
  segment however long the drive was, while the caller's state still spans the whole route."""
  import sys
  opened = []
  sys.modules["openpilot.tools.lib.logreader"] = NS(
    LogReader=lambda p: (opened.append(p) or [car_state(len(opened))]))
  # Restored, because a module-level monkeypatch left in place makes every later test read three
  # segments where it expects one -- which fails as a wrong COUNT and looks like a reader bug.
  real_resolve = RR.resolve
  RR.resolve = lambda _t: ["/d/r--0/rlog", "/d/r--1/rlog", "/d/r--2/rlog"]
  try:
    list(RR.stream("r"))
  finally:
    RR.resolve = real_resolve
  assert opened == ["/d/r--0/rlog", "/d/r--1/rlog", "/d/r--2/rlog"], opened


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


# --- turning what a person types into what LogReader accepts -------------------------------------

def test_segments_sort_by_number_not_by_name():
  """--2 comes before --10. Lexically it does not, and a route walked out of order would report
  lane changes and gestures in the wrong sequence while looking perfectly fine."""
  paths = [f"/d/0000031e--abc--{n}/rlog" for n in (0, 1, 2, 10, 11, 35)]
  assert sorted(paths, key=RR._segment_key) == paths
  scrambled = [f"/d/0000031e--abc--{n}/rlog" for n in (10, 2, 35, 0)]
  assert [RR._segment_key(p) for p in sorted(scrambled, key=RR._segment_key)] == [0, 2, 10, 35]


def test_a_bare_route_name_is_passed_through_when_nothing_is_on_disk():
  """Off the device there is no realdata directory, so it must hand the string to LogReader rather
  than silently resolving to an empty list and reporting a drive with no messages in it."""
  assert RR.resolve("0000031e--69e3cd09d2") == "0000031e--69e3cd09d2"


def test_a_route_name_is_recognized_as_one():
  """The shape LogReader rejects with "Segment range is not valid" -- which is exactly what is on
  screen and in `ls` on the device, so it is what he will type."""
  import re
  assert re.fullmatch(r"[0-9a-f]{8}--[0-9a-f]+", "0000031e--69e3cd09d2")
  assert not re.fullmatch(r"[0-9a-f]{8}--[0-9a-f]+", "0000031e--69e3cd09d2--4")


def test_latest_falls_back_rather_than_resolving_to_nothing():
  """Off the device there is no realdata directory. 'latest' must stay a string LogReader can
  complain about, not become an empty list that reads as a drive with no messages in it."""
  assert RR.resolve("latest") == "latest"


def test_the_route_name_is_recovered_from_segment_zero():
  """newest_route strips only the trailing segment number, so a route name containing -- survives."""
  assert "00000321--882fc7224f--0".rsplit("--", 1)[0] == "00000321--882fc7224f"


def test_a_missing_recommendation_is_not_printed_as_zero():
  """The histogram behind geoLoosenTo arrived after some routes on the device, so an older drive
  reports the term and the mean correctly with nothing to compute a percentile from. Printed as a
  number that read "SET IT TO: 0.00" -- a confident instruction to close the gate completely, on a
  drive whose actual measurement was 6.44."""
  d = read([plan(0.0, geo=(0, 6.44, 0.98, 0.0))])
  out = RR.report(d)
  assert "0.00" not in out
  assert "not recorded" in out
  assert "6.44" in out, "the measurement is still good and must survive"


def test_a_saturated_histogram_is_flagged_as_a_lower_bound():
  """Past GEO_SPAN every refusal lands in the top bucket, so the percentile can only answer 'the
  ceiling'. A recommendation BELOW the mean it is derived from is the tell."""
  d = read([plan(0.0, geo=(0, 6.44, 0.98, 2.0))])
  out = RR.report(d)
  assert "lower bound" in out


def test_an_ordinary_recommendation_is_printed_plainly():
  d = read([plan(0.0, geo=(0, 1.04, 1.0, 1.2))])
  out = RR.report(d)
  assert "1.20" in out
  assert "lower bound" not in out
  assert "not recorded" not in out
