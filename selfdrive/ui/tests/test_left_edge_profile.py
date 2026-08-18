"""Percentile math for bp_left_edge_profile.py.

The tool's whole output is percentiles, and its conclusion is a comparison between two drives.
An off-by-one in the index would shift both runs the same way and stay invisible, which is the
kind of error that survives right up until it decides a safety gate.
"""
import importlib.util
import math
import pathlib

_SPEC = importlib.util.spec_from_file_location(
  "bp_left_edge_profile",
  pathlib.Path(__file__).resolve().parents[3] / "tools" / "bp_left_edge_profile.py",
)
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


class TestPct:
  def test_empty_is_nan_not_zero(self):
    """Zero would read as 'the edge is right beside us', the most alarming possible answer,
    from having measured nothing at all."""
    assert math.isnan(mod.pct([], 0.5))

  def test_median_of_odd_length(self):
    assert mod.pct([3.0, 1.0, 2.0], 0.5) == 2.0

  def test_it_sorts_its_input(self):
    assert mod.pct([9.0, 1.0, 5.0], 0.0) == 1.0

  def test_p100_does_not_run_off_the_end(self):
    assert mod.pct([1.0, 2.0, 3.0], 1.0) == 3.0

  def test_single_value(self):
    assert mod.pct([7.5], 0.9) == 7.5

  def test_p90_of_a_long_tail_picks_the_tail(self):
    vals = [1.0] * 90 + [50.0] * 10
    assert mod.pct(vals, 0.90) == 50.0

  def test_input_is_not_mutated(self):
    """The caller reuses the same list for p10, p50 and p90."""
    vals = [3.0, 1.0, 2.0]
    mod.pct(vals, 0.5)
    assert vals == [3.0, 1.0, 2.0]
