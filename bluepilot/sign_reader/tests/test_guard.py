"""The sign reader must never be able to make driving worse (it did, once: 2026-11-12).

These pin the rules that keep it polite: it starts late, looks slowly, stops itself at the first
sign of trouble, and is pinned to a core nothing else uses.
"""
import re
from pathlib import Path

import pytest

from bluepilot.sign_reader.guard import (MIN_INFERENCE_GAP_S, SLOW_INFERENCE_LIMIT, SLOW_INFERENCE_S, STARTUP_QUIET_S,
                                         TROUBLE_EVENTS, Guard)

REPO = Path(__file__).resolve().parents[3]


class TestGuard:
  def test_quiet_for_the_first_45_seconds(self):
    g = Guard(started_at=100.0)
    assert not g.may_infer(100.0) and not g.may_infer(100.0 + STARTUP_QUIET_S - 0.1)
    assert g.may_infer(100.0 + STARTUP_QUIET_S)

  def test_never_looks_more_often_than_four_times_a_second(self):
    g = Guard(0.0)
    t = STARTUP_QUIET_S + 1
    assert g.may_infer(t)
    g.note_inference(t, 0.3)
    assert not g.may_infer(t + MIN_INFERENCE_GAP_S - 0.01)
    assert g.may_infer(t + MIN_INFERENCE_GAP_S)

  @pytest.mark.parametrize("event", sorted(TROUBLE_EVENTS))
  def test_any_trouble_event_stops_it_for_good(self, event):
    g = Guard(0.0)
    g.note_events(["gasPressedOverride", event])
    assert g.stopped() and event in g.stopped_reason
    assert not g.may_infer(1e6)  # latched: no amount of time brings it back within this drive

  def test_ordinary_driving_events_do_not_stop_it(self):
    g = Guard(0.0)
    g.note_events(["gasPressedOverride", "steerOverride", "parkBrake", "wrongGear"])
    assert not g.stopped()

  def test_the_first_reason_is_kept(self):
    g = Guard(0.0)
    g.note_events(["selfdrivedLagging"])
    g.note_events(["commIssue"])
    assert "selfdrivedLagging" in g.stopped_reason

  def test_three_slow_inferences_in_a_row_stop_it_but_a_good_one_resets_the_count(self):
    g = Guard(0.0)
    for i in range(SLOW_INFERENCE_LIMIT - 1):
      g.note_inference(100 + i, SLOW_INFERENCE_S + 0.5)
    assert not g.stopped()
    g.note_inference(110, 0.3)  # a healthy one: the streak is over
    for i in range(SLOW_INFERENCE_LIMIT - 1):
      g.note_inference(120 + i, SLOW_INFERENCE_S + 0.5)
    assert not g.stopped()
    g.note_inference(130, SLOW_INFERENCE_S + 0.5)
    assert g.stopped()


class TestProcessIsPolite:
  src = (REPO / "bluepilot/sign_reader/sign_reader.py").read_text()

  def test_thread_limits_are_set_before_numpy_or_mnn_can_load(self):
    first_import = min(self.src.index("\nimport time"), self.src.index("\nfrom cereal"), self.src.index("\nfrom bluepilot"))
    for var in ("OMP_NUM_THREADS", "OMP_WAIT_POLICY", "GOMP_SPINCOUNT"):
      assert self.src.index(var) < first_import, var

  def test_it_is_pinned_before_the_model_is_loaded(self):
    main = self.src.split("def main():", 1)[1]
    assert main.index("_be_polite(pm)") < main.index("SignReader.load")

  def test_it_refuses_to_run_unpinned(self):
    polite = self.src.split("def _be_polite", 1)[1].split("def main", 1)[0]
    assert polite.count("_idle(pm") >= 3 and "sched_setaffinity" in polite and "SCHED_IDLE" in polite

  def test_it_waits_for_its_core_to_come_online_instead_of_giving_up_for_the_drive(self):
    """The big cores are off while parked; pinning to one fails with EINVAL until the drive starts."""
    polite = self.src.split("def _be_polite", 1)[1].split("def main", 1)[0]
    assert "PIN_WAIT_S" in polite and "time.sleep" in polite
    assert float(re.search(r"^PIN_WAIT_S = ([\d.]+)", self.src, re.M).group(1)) >= 60  # longer than a car takes to wake its cores

  def test_the_guard_is_consulted_every_frame_and_stopping_stops_the_camera_reads(self):
    loop = self.src.split("reading = {}", 1)[1]
    assert "guard.note_events" in loop and "guard.stopped()" in loop and "guard.may_infer" in loop
    assert loop.index("guard.stopped()") < loop.index("reader.step")  # decided before looking at a frame
    assert "onroadEvents" in self.src

  def test_its_core_is_not_one_the_driving_stack_is_pinned_to(self):
    core = int(re.search(r"^CORE = (\d+)", self.src, re.M).group(1))
    used = set()
    for py in list((REPO / "selfdrive").rglob("*.py")) + list((REPO / "system").rglob("*.py")) + list((REPO / "sunnypilot").rglob("*.py")):
      if "/tests/" in str(py) or py.name.startswith("test_"):
        continue
      text = py.read_text(errors="ignore")
      for m in re.finditer(r"(?:config_realtime_process|set_core_affinity)\(\s*(\[[^\]]*\]|\d+)", text):
        used.update(int(n) for n in re.findall(r"\d+", m.group(1)))
    assert used, "found no core assignments at all: this check has stopped checking anything"
    assert core not in used, f"core {core} is also used by the driving stack ({sorted(used)})"
