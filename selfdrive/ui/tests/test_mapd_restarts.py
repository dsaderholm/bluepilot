"""The two judgments in bp_mapd_restarts.py, which decide whether mapd issue 88 fired on a drive.

Both are false-negative hazards: get either wrong and the tool reports a clean drive, the
restart hypothesis for the SCC-Map fallback runs gets closed, and nothing says otherwise.
"""
import importlib.util
import pathlib

import pytest

_SPEC = importlib.util.spec_from_file_location(
  "bp_mapd_restarts",
  pathlib.Path(__file__).resolve().parents[3] / "tools" / "bp_mapd_restarts.py",
)
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


def row(t, pid, running=True, exit_code=0):
  return (t, pid, running, exit_code)


class TestCountRestarts:
  def test_no_timeline_is_no_restarts(self):
    assert mod.count_restarts([]) == 0

  def test_one_pid_all_drive_is_no_restarts(self):
    assert mod.count_restarts([row(0.0, 4321)]) == 0

  def test_a_new_pid_is_one_restart(self):
    assert mod.count_restarts([row(0.0, 4321), row(90.0, 5555)]) == 1

  def test_pid_zero_alone_is_not_a_restart(self):
    """A death with no respawn is not a restart. manager reports pid 0 while the process is down,
    and if the drive ends there, nothing came back."""
    assert mod.count_restarts([row(0.0, 4321), row(90.0, 0, running=False, exit_code=2)]) == 0

  def test_repeated_death_and_respawn_counts_every_cycle(self):
    timeline = [
      row(0.0, 4321),
      row(30.0, 0, running=False, exit_code=2), row(31.0, 5001),
      row(60.0, 0, running=False, exit_code=2), row(61.0, 5002),
      row(90.0, 0, running=False, exit_code=2), row(91.0, 5003),
    ]
    assert mod.count_restarts(timeline) == 3

  def test_a_reused_pid_after_a_death_is_still_a_restart(self):
    """THE case that makes this a function rather than len(set(pids)) - 1.

    The kernel is free to hand the respawned process the same pid it just released, and on a
    device with a small pid space and a process restarting in a tight loop that is not exotic.
    Both 'distinct PIDs minus one' and 'transitions to an unseen PID' score this zero, which is
    a clean drive reported for a process that died. Same direction as every other failure here:
    it closes the hypothesis rather than raising a false alarm.
    """
    timeline = [row(0.0, 4321), row(30.0, 0, running=False, exit_code=2), row(31.0, 4321)]
    assert mod.count_restarts(timeline) == 1
    assert len({p for _, p, _, _ in timeline if p > 0}) - 1 == 0, \
      "the formula this replaced must still be wrong here, or the test proves nothing"

  def test_running_false_with_a_live_pid_still_counts_as_down(self):
    """manager can report the old pid alongside running=False before it reaps. The pid has not
    changed, so only the running flag distinguishes a restart from a steady process."""
    timeline = [row(0.0, 4321), row(10.0, 4321, running=False, exit_code=2), row(11.0, 4321)]
    assert mod.count_restarts(timeline) == 1

  def test_a_steady_process_is_never_a_restart(self):
    timeline = [row(0.0, 4321), row(10.0, 4321), row(20.0, 4321)]
    assert mod.count_restarts(timeline) == 0


class TestAttributeGaps:
  def test_a_gap_with_nothing_in_it_is_unattributed(self):
    out = mod.attribute_gaps([(10.0, 5.0)], [row(100.0, 4321)])
    assert out == [(10.0, 5.0, [])]

  def test_a_state_change_inside_the_gap_attributes_it(self):
    out = mod.attribute_gaps([(10.0, 5.0)], [row(12.0, 5001)])
    assert out[0][2] == [12.0]

  @pytest.mark.parametrize("t", [10.0, 15.0])
  def test_the_window_includes_both_endpoints(self, t):
    """managerState publishes far slower than mapdOut, so the change that explains a gap
    routinely lands exactly on its first or last silent frame. A half-open window drops
    precisely the changes worth finding."""
    out = mod.attribute_gaps([(10.0, 5.0)], [row(t, 5001)])
    assert out[0][2] == [t], "an endpoint state change must still attribute the gap"

  def test_changes_outside_the_gap_are_not_claimed(self):
    out = mod.attribute_gaps([(10.0, 5.0)], [row(9.99, 5001), row(15.01, 5002)])
    assert out[0][2] == []

  def test_every_gap_is_returned_even_when_none_attribute(self):
    out = mod.attribute_gaps([(10.0, 1.0), (20.0, 2.0), (30.0, 3.0)], [])
    assert [(a, d) for a, d, _ in out] == [(10.0, 1.0), (20.0, 2.0), (30.0, 3.0)]
