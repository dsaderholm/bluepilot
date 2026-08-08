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
         geo=(1, 0.31, 0.86, 0.30), overtaken=(0, 0),
         geometry_ok=True, edge_std=0.1, line_prob=0.99, lane_width=3.7, edge_beyond=1.5):
  pa = NS(suggestion=suggestion, blockedBy=blocked, maneuver=maneuver,
          geoRefusedBy=geo[0], geoRefusedValue=geo[1], geoRefusedShare=geo[2], geoLoosenTo=geo[3],
          leftGeometryOk=geometry_ok, leftEdgeStd=edge_std, leftLineProb=line_prob,
          leftLaneWidth=lane_width, leftEdgeBeyond=edge_beyond,
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


def init_data(**params):
  """initData.params is a capnp Map -- .entries, not dict access. Modelling it as a dict is what
  hid the reader's bug: `k in params` raised, the bare except ate it, and the section printed
  nothing while looking like a route that simply had no params recorded."""
  entries = [NS(key=k, value=v.encode()) for k, v in params.items()]
  return NS(logMonoTime=0, which=lambda: "initData", initData=NS(params=NS(entries=entries)))


def test_the_settings_the_drive_actually_booted_with_are_reported():
  """"I had all passing assist options on... there was absolutely nothing on the screen." A setting
  he believes is on is the thing to check against the record rather than against memory -- and
  ShowPassingAssist is one of the keys whose changed default provably never reached his car."""
  d = read([init_data(ShowPassingAssist="0"), plan(0.0)])
  out = RR.report(d)
  assert "ShowPassingAssist" in out
  assert "OFF" in out
  assert "draws nothing" in out


def test_a_setting_that_is_on_is_not_flagged():
  d = read([init_data(ShowPassingAssist="1"), plan(0.0)])
  out = RR.report(d)
  assert "draws nothing" not in out


def test_a_route_with_no_params_recorded_still_reports():
  d = read([plan(0.0)])
  assert RR.report(d)


# --- every term on its own, so one drive names every blocker ------------------------------------

def test_only_the_edge_refusing_is_called_out_as_such():
  """Three drives showed edge-std at 100%, 98%, 98% -- but the device records only the FIRST
  failing term and edge-std is checked first, so that number cannot distinguish "the only problem"
  from "the first of four". Evaluated independently it can."""
  d = read([plan(0.0, geometry_ok=False, edge_std=8.4)])
  out = RR.report(d)
  assert "edge-std ALONE" in out
  assert "never" in out


def test_a_second_blocker_hiding_behind_the_first_is_surfaced():
  """The case worth spending a drive to learn, and now costing none: loosening edge-std would just
  hand the refusal to paint."""
  d = read([plan(0.0, geometry_ok=False, edge_std=8.4, line_prob=0.2)])
  out = RR.report(d)
  assert "edge-std ALONE" not in out
  assert "would still refuse" in out


def test_lane_width_is_judged_at_BOTH_ends():
  """3.0 to 5.0. A shoulder is too narrow and a merge taper too wide, and only checking the low end
  would call a widening shoulder a lane."""
  assert "lane width" in RR.report(read([plan(0.0, geometry_ok=False, lane_width=2.0)]))
  assert "lane width" in RR.report(read([plan(0.0, geometry_ok=False, lane_width=9.0)]))


def test_frames_where_geometry_was_not_the_blocker_are_not_counted():
  """Ordinary driving with no lane to the left would otherwise swamp the sample -- it did, at
  41091 of 41091 frames. The default fixture blames noLaneAvailable, so this states the other
  blocker explicitly."""
  d = read([plan(0.0, blocked="tooSlow", geometry_ok=False, edge_std=8.4)])
  assert d["geo_frames"] == 0


def test_the_geometry_terms_are_counted_only_where_geometry_was_the_blocker():
  """`not leftGeometryOk` gave 41091 of 41091 frames on one route -- most of any drive has no lane
  to the left and the gate is correctly False throughout, so percentages off that base describe the
  ROAD rather than the gate. noLaneAvailable is the frames where everything else was ready."""
  msgs = [plan(0.0, blocked="tooSlow", geometry_ok=False, line_prob=0.1),
          plan(0.1, blocked="notEngaged", geometry_ok=False, line_prob=0.1),
          plan(0.2, blocked="noLaneAvailable", geometry_ok=False, line_prob=0.1)]
  d = read(msgs)
  assert d["geo_frames"] == 1, "counted frames where something else was the blocker"
  assert d["geo_each"][1] == 1


def log_message(text, which="logMessage"):
  return NS(logMonoTime=0, which=lambda: which,
            **{which: text, "logMessage": text, "errorLogMessage": text})


def test_the_panels_own_crash_is_surfaced():
  """It latches off for the whole drive, so the cloudlog line is the only record of why. "Then I
  just got passing assist error for the rest of my drive after stopping at a red light." """
  d = read([log_message("passing assist panel failed, latched off: KeyError('carStateBP')"),
            plan(0.0)])
  out = RR.report(d)
  assert "THE PANEL CRASHED" in out
  assert "KeyError" in out


def test_unrelated_log_lines_are_not_dumped():
  """A drive is full of logging; pulling all of it in would bury the one line that matters."""
  d = read([log_message("some unrelated thing happened"), plan(0.0)])
  assert d["panel_error"] is None
  assert "THE PANEL CRASHED" not in RR.report(d)


def test_the_header_names_the_route_that_was_actually_read():
  """`route: latest` is the word he typed, not the drive it read. When latest picked a stale route,
  identical numbers were the only clue -- against a report that otherwise looked perfectly fine."""
  real_resolve = RR.resolve
  RR.resolve = lambda _t: ["/d/00000322--abc--0/rlog", "/d/00000322--abc--1/rlog"]
  try:
    assert RR.resolved_name("latest") == "00000322--abc  (2 segments)"
  finally:
    RR.resolve = real_resolve


def test_an_unresolvable_target_still_names_itself():
  assert RR.resolved_name("0000031e--69e3cd09d2") == "0000031e--69e3cd09d2"
