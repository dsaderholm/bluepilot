"""The camera sign reader as the resolver's "car" source: when its limit may be used, and the wiring.

Behaviour is tested twice on purpose: the gate function on its own, and the real
SpeedLimitResolver._get_from_car_state with a fake SubMaster, because a gate that is correct and not
called is the failure mode that matters here.
"""
import types
from pathlib import Path

import pytest

from bluepilot.sign_reader.limit import MAX_LIMIT_MS, MIN_LIMIT_MS, sign_reader_limit

REPO = Path(__file__).resolve().parents[3]
MPH = 0.44704


class TestGate:
  def test_a_healthy_reader_passes_its_limit_through(self):
    assert sign_reader_limit(True, True, True, True, 31.29) == pytest.approx(31.29)

  @pytest.mark.parametrize("enabled,alive,valid,loaded", [
    (False, True, True, True),   # switched off
    (True, False, True, True),   # no recent message: process dead or camera gone
    (True, True, False, True),   # message flagged invalid
    (True, True, True, False),   # weights never loaded
  ])
  def test_any_doubt_means_no_limit(self, enabled, alive, valid, loaded):
    assert sign_reader_limit(enabled, alive, valid, loaded, 31.29) == 0.0

  @pytest.mark.parametrize("bad", [0.0, -5.0, float("nan"), float("inf"), MIN_LIMIT_MS - 0.1, MAX_LIMIT_MS + 0.1, None])
  def test_values_a_sign_cannot_carry_are_dropped(self, bad):
    assert sign_reader_limit(True, True, True, True, bad) == 0.0

  def test_real_posted_limits_pass(self):
    for mph in (15, 20, 25, 30, 35, 45, 55, 65, 70, 75, 80):
      assert sign_reader_limit(True, True, True, True, mph * MPH) > 0, mph


class FakeSM:
  def __init__(self, reader=None, car_limit=0.0, alive=True, valid=True):
    ns = types.SimpleNamespace
    self.data = {"carStateSP": ns(speedLimit=car_limit)}
    self.alive = {"signReadingBP": alive}
    self.valid = {"signReadingBP": valid}
    if reader is not None:
      self.data["signReadingBP"] = ns(**reader)
    else:
      self.data["signReadingBP"] = ns(modelLoaded=False, speedLimit=0.0)

  def __getitem__(self, k):
    return self.data[k]


def _resolver(enabled):
  pytest.importorskip("openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.speed_limit_resolver")
  from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.speed_limit_resolver import SpeedLimitResolver
  r = SpeedLimitResolver.__new__(SpeedLimitResolver)  # no __init__: it reads a dozen Params that do not exist offline
  r.limit_solutions = {}
  r.distance_solutions = {}
  r.sign_reader_enabled = enabled
  return r


class TestInTheResolver:
  def _car_slot(self, r, sm):
    from cereal import custom
    r._get_from_car_state(sm)
    return r.limit_solutions[custom.LongitudinalPlanSP.SpeedLimit.Source.car]

  def test_reader_off_is_exactly_the_old_line(self):
    r = _resolver(False)
    sm = FakeSM(reader=dict(modelLoaded=True, speedLimit=31.29), car_limit=13.4)
    assert self._car_slot(r, sm) == pytest.approx(13.4)  # the car's own value; the reader is ignored

  def test_reader_on_uses_the_readers_limit(self):
    r = _resolver(True)
    sm = FakeSM(reader=dict(modelLoaded=True, speedLimit=31.29), car_limit=13.4)
    assert self._car_slot(r, sm) == pytest.approx(31.29)

  def test_reader_on_but_silent_leaves_the_slot_empty_and_never_falls_back_to_tsr(self):
    """TSR is excluded on this fork on purpose (it reads I-80 route shields as 80)."""
    for sm in (FakeSM(reader=dict(modelLoaded=True, speedLimit=0.0), car_limit=35.8),
               FakeSM(reader=dict(modelLoaded=False, speedLimit=31.29), car_limit=35.8),
               FakeSM(reader=dict(modelLoaded=True, speedLimit=31.29), car_limit=35.8, alive=False),
               FakeSM(reader=dict(modelLoaded=True, speedLimit=31.29), car_limit=35.8, valid=False)):
      assert self._car_slot(_resolver(True), sm) == 0.0


class TestWiring:
  def test_the_resolver_marks_its_change_and_reads_the_param_both_places(self):
    src = (REPO / "sunnypilot/selfdrive/controls/lib/speed_limit/speed_limit_resolver.py").read_text()
    assert src.count("# BluePilot:") >= 4 and src.count("# End BluePilot") >= 4
    assert src.count('self.params.get_bool("BPSignReaderEnabled")') == 2  # __init__ and update_params

  def test_plannerd_subscribes_to_the_reading(self):
    assert "'signReadingBP'" in (REPO / "selfdrive/controls/plannerd.py").read_text()

  def test_the_reader_publishes_every_frame_not_only_when_the_model_ran(self):
    """The resolver decides alive-ness from how recently it heard; the model only runs about every 2 s."""
    src = (REPO / "bluepilot/sign_reader/sign_reader.py").read_text()
    loop = src.split("reading = {}", 1)[1]
    assert loop.index("wants_frame") < loop.index("_publish(pm, reading, True")
    assert "continue" not in loop.split("wants_frame", 1)[1].split("_publish", 1)[0]
