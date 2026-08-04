"""
BluePilot: tests for the stationary turn-signal actuation test.

This one operates a lamp that other drivers read, so the tests are about the gates, not the
feature. Every one of them asserts that the signal does NOT come on, or that it stops.

The timeout test is the important one: it must hold even if the request param is never cleared,
because a stuck param or a crashed UI must not be able to leave a lamp latched on.
"""

from types import SimpleNamespace as NS

from opendbc.car import DT_CTRL
from opendbc.sunnypilot.car.ford.blinker_test_ext import (
  BlinkerTestExt, SIGNAL_NONE, SIGNAL_LEFT, SIGNAL_RIGHT, PULSE_DURATION_S, STANDSTILL_V_EGO,
  BUTTONS_STEP,
)


class FakeParams:
  def __init__(self, value=0):
    self.value = value

  def get(self, key, block=False, return_default=False):
    return self.value

  def put(self, key, val):
    self.value = int(val)


def make_cs(v_ego=0.0, engaged=False, left_blinker=False, right_blinker=False,
            lamp_left=False, lamp_right=False):
  cs = NS(out=NS(vEgo=v_ego, cruiseState=NS(enabled=engaged),
                 leftBlinker=left_blinker, rightBlinker=right_blinker))
  cs.turn_lamp_left = lamp_left
  cs.turn_lamp_right = lamp_right
  return cs


def make_ext(request=0):
  ext = BlinkerTestExt()
  ext.bt_params = FakeParams(request)
  return ext


def run(ext, frames, **kw):
  out = []
  for _ in range(frames):
    out.append(ext.update_blinker_test(make_cs(**kw)))
  return out


POLL_FRAMES = int(0.5 / DT_CTRL) + 1


class TestSendRate:
  """Found on the car: the lamp flashed fast and erratically during a test pulse.

  Not the tapping. The frame went out every 10 ms, roughly 100 Hz, against the SCCM's own 10 Hz
  copy of the same message on the same bus -- so the BCM saw the switch alternating between the
  commanded side and the driver's actual OFF about ten times per genuine frame.

  These pin the rate at the module boundary, because carcontroller.py cannot be tested offline and
  that is exactly why the bug got as far as a drive.
  """

  def test_the_signal_is_not_returned_every_frame(self):
    ext = make_ext(SIGNAL_RIGHT)
    out = run(ext, POLL_FRAMES + int(1.0 / DT_CTRL))
    signalled = [v for v in out if v == SIGNAL_RIGHT]
    assert signalled, "never signalled at all"
    assert len(signalled) < len(out) / 2, "signal returned on most frames -- rate limit is not applied"

  def test_the_gap_between_sends_is_buttons_step(self):
    ext = make_ext(SIGNAL_RIGHT)
    out = run(ext, POLL_FRAMES + int(2.0 / DT_CTRL))
    idx = [i for i, v in enumerate(out) if v == SIGNAL_RIGHT]
    gaps = {b - a for a, b in zip(idx, idx[1:])}
    assert gaps == {BUTTONS_STEP}, f"send gaps {sorted(gaps)}, expected {BUTTONS_STEP}"

  def test_the_pulse_still_ends_on_time_despite_the_rate_limit(self):
    # The state machine must advance every frame even though it only speaks every fifth. If the
    # timeout were rate-limited too the pulse would run five times too long.
    ext = make_ext(SIGNAL_RIGHT)
    out = run(ext, POLL_FRAMES + int((PULSE_DURATION_S + 1.0) / DT_CTRL))
    last = max(i for i, v in enumerate(out) if v == SIGNAL_RIGHT)
    elapsed = (last - out.index(SIGNAL_RIGHT)) * DT_CTRL
    assert elapsed <= PULSE_DURATION_S + 0.1, f"pulse ran {elapsed:.2f}s"

  def test_motion_still_stops_it_within_one_frame(self):
    # Rate-limiting the send must not rate-limit the standstill re-check.
    ext = make_ext(SIGNAL_RIGHT)
    run(ext, POLL_FRAMES + BUTTONS_STEP)
    assert set(run(ext, 50, v_ego=STANDSTILL_V_EGO + 1.0)) == {SIGNAL_NONE}


class TestBlinkerTestGates:
  def test_no_request_never_signals(self):
    ext = make_ext(0)
    assert set(run(ext, 200)) == {SIGNAL_NONE}

  def test_moving_blocks_the_request(self):
    ext = make_ext(SIGNAL_LEFT)
    assert set(run(ext, POLL_FRAMES, v_ego=STANDSTILL_V_EGO + 1.0)) == {SIGNAL_NONE}
    assert ext.bt_blocked == 1  # notStationary

  def test_cruise_engaged_blocks(self):
    ext = make_ext(SIGNAL_LEFT)
    assert set(run(ext, POLL_FRAMES, engaged=True)) == {SIGNAL_NONE}
    assert ext.bt_blocked == 2

  def test_driver_stalk_wins(self):
    ext = make_ext(SIGNAL_LEFT)
    assert set(run(ext, POLL_FRAMES, right_blinker=True)) == {SIGNAL_NONE}
    assert ext.bt_blocked == 3

  def test_stationary_request_pulses(self):
    ext = make_ext(SIGNAL_LEFT)
    out = run(ext, POLL_FRAMES + 10)
    assert SIGNAL_LEFT in out
    assert ext.bt_state == 1

  def test_right_request_pulses_right(self):
    ext = make_ext(SIGNAL_RIGHT)
    out = run(ext, POLL_FRAMES + 10)
    assert SIGNAL_RIGHT in out


class TestBlinkerTestTermination:
  def test_pulse_times_out_even_if_param_never_clears(self):
    """The safety-critical one. FakeParams.put works here, so force the stuck case explicitly."""
    ext = make_ext(SIGNAL_LEFT)
    ext.bt_params.put = lambda *a: None  # simulate a param write that silently fails
    frames = POLL_FRAMES + int(PULSE_DURATION_S / DT_CTRL) + 50
    out = run(ext, frames)
    assert out[-1] == SIGNAL_NONE, "lamp still commanded after the timeout"
    assert ext.bt_state == 2

  def test_motion_mid_pulse_stops_it_immediately(self):
    ext = make_ext(SIGNAL_LEFT)
    run(ext, POLL_FRAMES + 5)
    assert ext.bt_state == 1
    assert ext.update_blinker_test(make_cs(v_ego=STANDSTILL_V_EGO + 0.5)) == SIGNAL_NONE
    assert ext.bt_state == 2

  def test_driver_stalk_mid_pulse_stops_it(self):
    ext = make_ext(SIGNAL_LEFT)
    run(ext, POLL_FRAMES + 5)
    assert ext.update_blinker_test(make_cs(left_blinker=True)) == SIGNAL_NONE
    assert ext.bt_state == 2

  def test_engaging_cruise_mid_pulse_stops_it(self):
    ext = make_ext(SIGNAL_LEFT)
    run(ext, POLL_FRAMES + 5)
    assert ext.update_blinker_test(make_cs(engaged=True)) == SIGNAL_NONE
    assert ext.bt_state == 2

  def test_does_not_repeat_without_a_new_request(self):
    ext = make_ext(SIGNAL_LEFT)
    frames = POLL_FRAMES + int(PULSE_DURATION_S / DT_CTRL) + 10
    run(ext, frames)
    assert ext.bt_params.value == 0, "request param was not self-cleared"
    assert set(run(ext, 400)) == {SIGNAL_NONE}


class TestBlinkerTestMeasurement:
  def test_lamp_seen_records_a_confirmed_actuation(self):
    ext = make_ext(SIGNAL_LEFT)
    run(ext, POLL_FRAMES + 2)
    run(ext, 10, lamp_left=True)
    assert ext.bt_lamp_seen

  def test_wrong_side_lamp_does_not_count(self):
    # If the right lamp lights while we commanded left, something is wrong and it must not read
    # as a success.
    ext = make_ext(SIGNAL_LEFT)
    run(ext, POLL_FRAMES + 2)
    run(ext, 10, lamp_right=True)
    assert not ext.bt_lamp_seen

  def test_no_lamp_means_no_confirmation(self):
    ext = make_ext(SIGNAL_LEFT)
    run(ext, POLL_FRAMES + 20)
    assert not ext.bt_lamp_seen
