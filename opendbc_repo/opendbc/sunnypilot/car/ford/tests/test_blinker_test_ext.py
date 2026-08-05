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
  BUTTONS_STEP, SIGNAL_TAP_LEFT, TAP_COMMAND_S, DONE_HOLD_S, SIGNAL_BLINK_LEFT, SIGNAL_MEASURE, DEFAULT_BLINK_PERIOD_S, BLINK_COUNT,
)


class FakeParams:
  def __init__(self, value=0):
    self.value = value

  def get(self, key, block=False, return_default=False):
    if key == "FordBlinkerBlinkPeriod":
      return 1000
    return self.value

  def put(self, key, val, block=False):
    # MATCHES THE REAL SIGNATURE: params_pyx is `put(self, key, dat, bool block = False)`.
    # Without `block` here, a caller that passes it raised TypeError, which the module's own
    # except-clause swallowed -- so the param silently never cleared and the whole test harness
    # reported a bug that only existed in the stub.
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
    signaled = [v for v in out if v == SIGNAL_RIGHT]
    assert signaled, "never signaled at all"
    assert len(signaled) < len(out) / 2, "signal returned on most frames -- rate limit is not applied"

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

  def test_pressing_again_during_the_verdict_runs_the_test_again(self):
    """Measured on the car as "the other three buttons don't work", and measured here as a 7.5 s
    dead window: four seconds of pulse plus three of verdict, with every button inert.

    Dropping that press was the worst of the options. It is the one input that says the driver has
    finished reading the result, so it should start the next test, not be thrown away -- which is
    what made the buttons feel broken rather than busy.
    """
    ext = make_ext(SIGNAL_LEFT)
    run(ext, POLL_FRAMES + int(PULSE_DURATION_S / DT_CTRL) + 10)
    assert ext.bt_state == 2

    ext.bt_params.value = SIGNAL_RIGHT          # the second press, mid-verdict
    run(ext, POLL_FRAMES + 2)
    assert ext.bt_state == 1, "the press was dropped instead of starting the next test"
    assert ext.bt_commanded == SIGNAL_RIGHT

  def test_but_a_param_we_cannot_clear_never_starts_a_second_pulse(self):
    """The other disaster, and the reason preemption is gated rather than free.

    If our disarm write silently fails, the request reads non-zero forever -- indistinguishable
    from a driver leaning on the button, unless you track whether the store has ever accepted a
    write. Without that, "a press restarts the test" means a failed write flashes the lamp until
    the ignition goes off.
    """
    ext = make_ext(SIGNAL_LEFT)
    ext.bt_params.put = lambda *a, **k: None    # writes silently fail
    run(ext, POLL_FRAMES + int((PULSE_DURATION_S + DONE_HOLD_S) / DT_CTRL) + 40)
    assert ext.bt_state != 1, "a stuck request started another pulse"
    out = run(ext, int(PULSE_DURATION_S / DT_CTRL))
    assert all(v == SIGNAL_NONE for v in out), "lamp commanded again on a stuck param"

  def test_spamming_cannot_hold_the_machine_hostage(self):
    """The reported symptom, and the reason DONE is a clock rather than a condition: "if I do them
    in rapid succession, it just will stop working for a little bit", and "all four buttons will
    stop working."

    The old exit needed a poll to see the request at zero. Every press wrote one, so pressing
    repeatedly -- exactly what anyone does when a button looks like it did nothing -- held the
    machine in DONE for as long as they kept trying. The state meant to prevent runaway pulses
    instead punished impatience.
    """
    ext = make_ext(SIGNAL_LEFT)
    run(ext, POLL_FRAMES + int(PULSE_DURATION_S / DT_CTRL) + 10)
    assert ext.bt_state == 2
    # Press about twice a second for the whole verdict window and a bit beyond.
    for _ in range(int((DONE_HOLD_S + 1.0) / 0.5)):
      ext.bt_params.value = SIGNAL_RIGHT
      run(ext, int(0.5 / DT_CTRL))
    assert ext.bt_state != 2, "pressing the button kept the verdict window open indefinitely"

  def test_and_a_press_after_that_still_works(self):
    """The point of the above: the feature survives being pressed twice."""
    ext = make_ext(SIGNAL_LEFT)
    run(ext, POLL_FRAMES + int(PULSE_DURATION_S / DT_CTRL) + 10)
    ext.bt_params.value = SIGNAL_RIGHT
    run(ext, POLL_FRAMES + 2)                        # dropped
    run(ext, int(DONE_HOLD_S / DT_CTRL) + 4)         # then home on its own clock

    ext.bt_params.value = SIGNAL_RIGHT          # a fresh, deliberate press
    assert SIGNAL_RIGHT in run(ext, POLL_FRAMES + 20)
    assert ext.bt_state == 1


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


def flash(ext, cycles, side='left', period_s=0.66, frames=None):
  """Drive the machine while the lamp blinks at a realistic rate, and return the states seen."""
  half = int((period_s / 2) / DT_CTRL)
  out = []
  total = frames if frames is not None else cycles * half * 2
  for i in range(total):
    on = (i // half) % 2 == 0
    kw = {f"lamp_{side}": on}
    out.append(ext.update_blinker_test(make_cs(**kw)))
  return out


class TestFlashCounting:
  """"Really fast" is not a measurement, and two runs of this test settled nothing because of it.

  A clean 1.5 Hz signal over a four second hold is about six flashes; the erratic case is many
  times that. The count is what turns this question from an argument into a result -- and it is
  also what would have told the difference between "it worked" and "I spammed the button" without
  anyone having to remember which they did.
  """

  def test_a_steady_lamp_counts_its_flashes(self):
    ext = make_ext(SIGNAL_LEFT)
    run(ext, POLL_FRAMES)                       # arm it
    flash(ext, cycles=6, side='left')
    assert ext.bt_lamp_seen
    assert 4 <= ext.bt_flashes <= 8, f"counted {ext.bt_flashes} for six flashes"

  def test_a_lamp_stuck_on_is_one_flash_not_thousands(self):
    """Rising edges only. Counting the level would count frames and every run would read 400."""
    ext = make_ext(SIGNAL_LEFT)
    run(ext, POLL_FRAMES)
    run(ext, int(2.0 / DT_CTRL), lamp_left=True)
    assert ext.bt_flashes == 1

  def test_an_erratic_lamp_counts_far_higher(self):
    """The case that was reported. It has to be numerically distinguishable from a clean signal,
    which is the entire point -- otherwise the panel just says "it lit" for both."""
    ext = make_ext(SIGNAL_LEFT)
    run(ext, POLL_FRAMES)
    flash(ext, cycles=0, side='left', period_s=0.08, frames=int(3.0 / DT_CTRL))
    assert ext.bt_flashes > 12, f"an erratic signal counted only {ext.bt_flashes}"

  def test_the_wrong_side_is_not_counted(self):
    ext = make_ext(SIGNAL_LEFT)
    run(ext, POLL_FRAMES)
    flash(ext, cycles=6, side='right')
    assert ext.bt_flashes == 0
    assert not ext.bt_lamp_seen

  def test_a_fresh_run_starts_from_zero(self):
    ext = make_ext(SIGNAL_LEFT)
    run(ext, POLL_FRAMES)
    flash(ext, cycles=4, side='left')
    first = ext.bt_flashes
    assert first > 0
    run(ext, int((PULSE_DURATION_S + DONE_HOLD_S) / DT_CTRL) + 20)   # finish, then re-arm
    ext.bt_params.value = SIGNAL_LEFT                # ask again
    run(ext, POLL_FRAMES * 3)
    assert ext.bt_flashes < first or ext.bt_flashes == 0, "the second run inherited the first"


class TestTheTap:
  """Asking the way the stalk does: a brief command, then silence.

  His BCM is set through FORScan to flash eight times from a momentary deflection, and that is how
  he triggers every nudgeless lane change he makes. If openpilot can trigger the same thing, the
  rate and the count belong to the car and nothing contends with the steering column module.
  """

  def test_it_stops_commanding_almost_immediately(self):
    ext = make_ext(SIGNAL_TAP_LEFT)
    out = run(ext, POLL_FRAMES + int(2.0 / DT_CTRL))
    commanded = [i for i, v in enumerate(out) if v == SIGNAL_LEFT]
    assert commanded, "never commanded at all"
    span = (commanded[-1] - commanded[0]) * DT_CTRL
    assert span < 0.5, f"held the signal for {span:.2f}s -- that is a hold, not a tap"

  def test_but_it_keeps_watching_long_after(self):
    """The whole measurement: flashes AFTER we go quiet are the car's own pattern."""
    ext = make_ext(SIGNAL_TAP_LEFT)
    run(ext, POLL_FRAMES + int(TAP_COMMAND_S / DT_CTRL) + 2)
    assert ext.bt_state == 1, "stopped watching when it stopped commanding"
    flash(ext, cycles=4, side='left')
    assert ext.bt_flashes_after > 0, "flashes after the command were not attributed"
    assert ext.bt_flashes_after == ext.bt_flashes, "counted command-phase flashes as self-generated"

  def test_a_hold_attributes_nothing_to_the_car(self):
    """The control. A held pulse commands throughout, so nothing should land in flashesAfter --
    if it does, the two modes are not actually being told apart."""
    ext = make_ext(SIGNAL_LEFT)
    run(ext, POLL_FRAMES)
    flash(ext, cycles=4, side='left')
    assert ext.bt_flashes > 0
    assert ext.bt_flashes_after == 0

  def test_a_tap_still_obeys_every_gate(self):
    for kw in ({"v_ego": 5.0}, {"engaged": True}, {"left_blinker": True}):
      ext = make_ext(SIGNAL_TAP_LEFT)
      out = run(ext, POLL_FRAMES + 20, **kw)
      assert all(v == SIGNAL_NONE for v in out), f"a tap ignored {kw}"


class TestBlinkMode:
  """The lamp mirrors our frames one for one, so the SEND RATE is the FLASH RATE -- and the send
  DURATION is the on-time.

  The first version sent one frame per cycle. From the car: "it does them in groups of four, with
  the fog light only coming on for a fraction of a second for each blink." Right rhythm, no duty
  cycle -- a single frame buys only the milliseconds before the gateway's next frame clears it.
  """

  def _sends(self, seconds):
    ext = make_ext(SIGNAL_BLINK_LEFT)
    out = run(ext, POLL_FRAMES + int(seconds / DT_CTRL))
    return [i for i, v in enumerate(out) if v == SIGNAL_LEFT]

  def test_one_frame_per_blink(self):
    """The body module holds the lamp itself: "the blinker didn't briefly turn on, it stayed on for
    the normal amount." So one frame per cycle is a whole blink, and bursting frames to hold it on
    was solving a problem the main lamp does not have."""
    sends = self._sends(BLINK_COUNT * DEFAULT_BLINK_PERIOD_S)
    assert all(b - a > 1 for a, b in zip(sends, sends[1:])),       "consecutive frames -- that is a burst, and the lamp does not need one"

  def _drive_with_a_fake_bcm(self, ext, seconds, on_s=0.45, refractory_s=0.0):
    """A body module that lights the lamp for on_s when commanded -- and IGNORES commands while it
    is still lit, which is the absorption that caused the missed blinks.

    refractory_s extends the deaf period past the lamp going out, so a naive sender can be shown
    drifting into it.
    """
    lamp_until = -1
    deaf_until = -1
    commands, absorbed = [], 0
    for i in range(int(seconds / DT_CTRL)):
      lit = i < lamp_until
      out = ext.update_blinker_test(make_cs(lamp_left=lit))
      if out == SIGNAL_LEFT:
        if i < deaf_until:
          absorbed += 1
        else:
          commands.append(i)
          lamp_until = i + int(on_s / DT_CTRL)
          deaf_until = lamp_until + int(refractory_s / DT_CTRL)
    return commands, absorbed

  def test_no_command_is_ever_absorbed(self):
    """The reported fault: "some blinks getting missed at random times, sometimes missing 1, and
    sometimes missing 2." A fixed send period beats against the body module's own cycle and drifts
    through its ON phase. Waiting for the lamp to go out has no phase to drift."""
    ext = make_ext(SIGNAL_BLINK_LEFT)
    run(ext, POLL_FRAMES + 1)
    _, absorbed = self._drive_with_a_fake_bcm(ext, 20.0, on_s=0.45, refractory_s=0.15)
    assert absorbed == 0, f"{absorbed} commands landed while the lamp was still lit"

  def test_it_still_stops_after_the_right_number(self):
    ext = make_ext(SIGNAL_BLINK_LEFT)
    run(ext, POLL_FRAMES + 1)
    commands, _ = self._drive_with_a_fake_bcm(ext, 25.0)
    assert len(commands) == BLINK_COUNT, f"{len(commands)} blinks, expected {BLINK_COUNT}"

  def test_it_follows_a_flasher_SLOWER_than_any_fixed_period(self):
    """The test that actually proves closing the loop was necessary.

    A body module deaf for 1.2 s -- lit 0.9, then a 0.3 s tail -- outlasts the 1.0 s fixed period
    entirely, so an open-loop sender is absorbed on every single blink no matter what number is
    configured. Waiting for the lamp cannot be, because it has no number.

    Mutation-checked: reverting to the fixed period fails this and nothing else, which is why it is
    here. The earlier version used a flasher fast enough that both approaches worked.
    """
    ext = make_ext(SIGNAL_BLINK_LEFT)
    run(ext, POLL_FRAMES + 1)
    commands, absorbed = self._drive_with_a_fake_bcm(ext, 40.0, on_s=0.9, refractory_s=0.3)
    assert absorbed == 0, f"{absorbed} commands swallowed by a flasher slower than the period"
    assert len(commands) == BLINK_COUNT, f"{len(commands)} blinks got through, expected {BLINK_COUNT}"
    gaps = [(b - a) * DT_CTRL for a, b in zip(commands, commands[1:])]
    assert gaps and all(g > 1.2 for g in gaps), f"outran a 1.2s-deaf flasher: {gaps}"

  def test_a_car_that_never_reports_a_lamp_still_blinks(self):
    """Open loop is worse than closed. It is much better than sending nothing at all."""
    ext = make_ext(SIGNAL_BLINK_LEFT)
    out = run(ext, POLL_FRAMES + int(8.0 / DT_CTRL))
    assert out.count(SIGNAL_LEFT) >= 3, "no lamp feedback meant no blinks at all"

  def test_blink_obeys_every_gate(self):
    for kw in ({"v_ego": 5.0}, {"engaged": True}, {"left_blinker": True}):
      ext = make_ext(SIGNAL_BLINK_LEFT)
      out = run(ext, POLL_FRAMES + 20, **kw)
      assert all(v == SIGNAL_NONE for v in out), f"blink mode ignored {kw}"


class TestMeasureMode:
  """"I want it to match Ford's rate as well as you can."

  The best anyone can do from a desk is the FMVSS band, 1-2 Hz, which is a factor of two wide. His
  car knows the exact number and has been reporting it all along on BodyInfo_3_FD1. So this mode
  commands nothing and times his own stalk.
  """

  @staticmethod
  def _flash(ext, cycles, period_s, on_frac=0.5):
    """Drive the machine while the DRIVER's lamp blinks at a known rate."""
    half = int(period_s * on_frac / DT_CTRL)
    off = int(period_s / DT_CTRL) - half
    for _ in range(cycles):
      for _ in range(half):
        ext.update_blinker_test(make_cs(lamp_left=True, left_blinker=True))
      for _ in range(off):
        ext.update_blinker_test(make_cs(lamp_left=False, left_blinker=True))

  def test_it_measures_the_real_interval(self):
    ext = make_ext(SIGNAL_MEASURE)
    run(ext, POLL_FRAMES + 2)
    assert ext.bt_measuring, "never armed -- the driver-stalk gate probably refused it"
    self._flash(ext, cycles=6, period_s=0.9)
    assert abs(ext.bt_measured_ms - 900) < 60, f"measured {ext.bt_measured_ms} ms, expected ~900"

  def test_it_commands_nothing_at_all(self):
    """The whole point: it must not touch the lamp it is measuring."""
    ext = make_ext(SIGNAL_MEASURE)
    out = run(ext, POLL_FRAMES + int(3.0 / DT_CTRL), lamp_left=True, left_blinker=True)
    assert all(v == SIGNAL_NONE for v in out), "measure mode transmitted something"

  def test_the_drivers_stalk_does_not_block_it(self):
    """Every other mode refuses while the driver is signalling. Here that is the input."""
    ext = make_ext(SIGNAL_MEASURE)
    run(ext, POLL_FRAMES + 2, left_blinker=True)
    assert ext.bt_state == 1
    assert ext.bt_blocked == 0

  def test_it_still_refuses_to_run_while_moving(self):
    ext = make_ext(SIGNAL_MEASURE)
    run(ext, POLL_FRAMES + 2, v_ego=5.0, left_blinker=True)
    assert ext.bt_state != 1

  def test_a_long_pause_is_not_counted_as_an_interval(self):
    """Two separate stalk taps are not one slow blinker. A gap far longer than any flash period is
    the driver stopping, and averaging it in would report a rate no car has."""
    ext = make_ext(SIGNAL_MEASURE)
    run(ext, POLL_FRAMES + 2)
    self._flash(ext, cycles=4, period_s=0.9)
    first = ext.bt_measured_ms
    run(ext, int(4.0 / DT_CTRL), left_blinker=True)      # a long quiet gap
    self._flash(ext, cycles=3, period_s=0.9)
    assert abs(ext.bt_measured_ms - first) < 60, "a pause between taps polluted the mean"

  def test_the_spacing_setting_governs_the_open_loop_fallback(self):
    """It was read from the param and never used -- the fallback ran on a hardcoded constant. A
    control that does nothing is the same fault as a readout nobody renders.

    Only reachable with no lamp feedback, which is the only time an open loop happens at all.
    """
    ext = make_ext(SIGNAL_BLINK_LEFT)
    ext.bt_params.get = lambda k, **kw: 600 if k == "FordBlinkerBlinkPeriod" else 7
    out = run(ext, POLL_FRAMES + int(6.0 / DT_CTRL))       # lamp never lights
    sends = [i for i, v in enumerate(out) if v == SIGNAL_LEFT]
    gaps = [(b - a) * DT_CTRL for a, b in zip(sends, sends[1:])]
    assert gaps, "no fallback blinks at all"
    assert all(abs(g - 0.6) < 0.05 for g in gaps), f"setting ignored: gaps {gaps}"
