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
  BLINK_STALL_S,
  LAMP_SETTLE_S, LAMP_WAIT_MAX_S, GATEWAY_PERIOD_S,
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
            lamp_left=False, lamp_right=False, gw_ts=0):
  cs = NS(out=NS(vEgo=v_ego, cruiseState=NS(enabled=engaged),
                 leftBlinker=left_blinker, rightBlinker=right_blinker))
  cs.turn_lamp_left = lamp_left
  cs.turn_lamp_right = lamp_right
  # WHEN the gateway's own Steering_Data_FD1 last arrived. The blink loop rides this rather than a
  # clock of its own -- see GATEWAY_PERIOD_S. A fixture that never advances it is a car whose
  # gateway has stopped talking, which is a case worth testing but not the normal one.
  cs.steering_data_ts = gw_ts
  return cs


def make_ext(request=0):
  ext = BlinkerTestExt()
  ext.bt_params = FakeParams(request)
  return ext


GATEWAY_FRAMES = int(GATEWAY_PERIOD_S / DT_CTRL)   # control frames between gateway messages


def run(ext, frames, *, gateway=True, **kw):
  """Advance the machine, simulating the gateway's 10 Hz Steering_Data_FD1 unless told not to."""
  out = []
  for i in range(frames):
    ts = ((i // GATEWAY_FRAMES) + 1) * int(GATEWAY_PERIOD_S * 1e9) if gateway else 0
    out.append(ext.update_blinker_test(make_cs(gw_ts=ts, **kw)))
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
    """THE OTHER SIDE, deliberately. This used to use the same side the pulse was commanding, which
    passed for the wrong reason: carState.leftBlinker is decoded from TurnLghtSwtch_D_Stat, the
    exact signal this module writes, so a commanded LEFT reported back was being read as the driver
    reaching for the stalk -- and a run could abort itself.

    The driver going the OTHER way is the case the check exists for, and it still stops it at once.
    """
    ext = make_ext(SIGNAL_LEFT)
    run(ext, POLL_FRAMES + 5)
    assert ext.update_blinker_test(make_cs(right_blinker=True)) == SIGNAL_NONE
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
  """PHASE-LOCKED to the gateway, which is what the four earlier attempts all missed.

  Steering_Data_FD1 is sent by the GATEWAY at 10 Hz carrying the driver's real stalk position, and
  openpilot writes its own copy of the same frame to command a signal. Both claim the switch and the
  body module obeys whichever landed last -- so a command with no phase relationship to the gateway
  owns the switch for somewhere between 0 and 100 ms at random. That is exactly the reported fault:
  "6 blinks, then 1 and 4 more, then 4 and 2 more, then zero... it just seems random."

  Sending immediately after each gateway frame makes that deterministic: our value holds the switch
  for very nearly the whole 100 ms until the next one. A blink is therefore no longer one frame; it
  is every gateway frame for the on-time.
  """

  @staticmethod
  def _sends(out):
    return [i for i, v in enumerate(out) if v == SIGNAL_LEFT]

  def test_a_command_goes_out_on_every_gateway_frame_of_an_on_phase(self):
    """The point. One frame per blink leaves the gateway to overwrite it after a random slice of
    100 ms; holding across the whole phase is what makes the lamp solid."""
    ext = make_ext(SIGNAL_BLINK_LEFT)
    run(ext, POLL_FRAMES + 2)
    sends = self._sends(run(ext, int(1.0 / DT_CTRL)))
    assert len(sends) >= 3, f"only {len(sends)} commands in a second; the lamp will flicker"
    gaps = [b - a for a, b in zip(sends, sends[1:])]
    assert all(g == GATEWAY_FRAMES for g in gaps[:2]), (
      f"not locked to the gateway's own frames: {gaps[:4]}")

  def test_nothing_is_sent_between_gateway_frames(self):
    """Sending off our own clock is what produced the erratic counts. Every command has to ride an
    arrival, or it lands at a random point in the gateway's cycle and gets overwritten."""
    ext = make_ext(SIGNAL_BLINK_LEFT)
    run(ext, POLL_FRAMES + 2)
    out = run(ext, int(1.0 / DT_CTRL))
    for i in self._sends(out):
      assert i % GATEWAY_FRAMES == 0, "a command was sent between two gateway frames"

  def test_on_and_off_split_the_period(self):
    ext = make_ext(SIGNAL_BLINK_LEFT)
    ext.bt_params.get = lambda k, **kw: 800 if k == "FordBlinkerBlinkPeriod" else 7
    run(ext, POLL_FRAMES + 2)
    sends = self._sends(run(ext, int(2.0 / DT_CTRL)))
    # 800 ms period -> 400 ms on, 400 ms off -> four gateway frames each way
    runs, cur = [], 1
    for a, b in zip(sends, sends[1:]):
      if b - a == GATEWAY_FRAMES:
        cur += 1
      else:
        runs.append(cur); cur = 1
    runs.append(cur)
    # The SECOND run: arming consumes part of the first phase, so it is short by construction.
    assert runs[1] == 4, f"on-phase was {runs[1]} gateway frames, wanted 4 for an 800 ms period"

  def test_it_stops_after_the_right_number_of_blinks(self):
    ext = make_ext(SIGNAL_BLINK_LEFT)
    run(ext, POLL_FRAMES + 2)
    out = run(ext, int(20.0 / DT_CTRL))
    sends = self._sends(out)
    blinks = 1 + sum(1 for a, b in zip(sends, sends[1:]) if b - a > GATEWAY_FRAMES)
    assert blinks == BLINK_COUNT, f"{blinks} blinks, wanted {BLINK_COUNT}"

  def test_a_gateway_that_stops_talking_does_not_hang_the_run(self):
    """If the frame we lock to disappears, a blink still has to happen -- a sequence that waits
    forever for a contender that has gone quiet is worse than one that free-runs."""
    ext = make_ext(SIGNAL_BLINK_LEFT)
    run(ext, POLL_FRAMES + 2)
    out = run(ext, int(3.0 / DT_CTRL), gateway=False)
    assert self._sends(out), "nothing was sent at all once the gateway went quiet"

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

  def test_the_spacing_setting_governs_the_slot_length(self):
    """The setting still decides the rhythm, but it now sizes the ON and OFF phases in GATEWAY
    frames rather than spacing single commands -- see GATEWAY_PERIOD_S. A shorter period has to
    produce shorter phases, or the control does nothing.
    """
    def phases(period_ms):
      ext = make_ext(SIGNAL_BLINK_LEFT)
      ext.bt_params.get = lambda k, **kw: period_ms if k == "FordBlinkerBlinkPeriod" else 7
      run(ext, POLL_FRAMES + 2)
      sends = [i for i, v in enumerate(run(ext, int(4.0 / DT_CTRL))) if v == SIGNAL_LEFT]
      runs, cur = [], 1
      for a, b in zip(sends, sends[1:]):
        if b - a == GATEWAY_FRAMES:
          cur += 1
        else:
          runs.append(cur); cur = 1
      runs.append(cur)
      return runs

    assert phases(1000)[1] > phases(600)[1], "the spacing setting no longer changes anything"


class TestTheRunawayGuardCanLetGo:
  """From the road, three times now: "occasionally pressing blink left or blink right is absolutely
  nothing", "I still need to wait a little bit in between tests", "working sometimes, but I think a
  delay was preventing them from working".

  One race, and its consequence was permanent. _disarm writes 0 and reads it straight back to prove
  the store took the write. If a button is pressed in between, the read-back returns HIS value
  instead of our zero, the guard concludes the store is broken and latches. Nothing else ever
  writes that key, so it never reads zero again, the guard never lifts, and every button is dead
  until the ignition cycles.

  These are about the latch, not the race -- the race is a millisecond wide and cannot be provoked
  reliably. The poisoned state is set up directly, which is also exactly what it looks like after
  the race.
  """

  def test_the_guard_clears_the_request_instead_of_sitting_on_it(self):
    ext = make_ext(SIGNAL_BLINK_LEFT)
    ext._bt_saw_clear = False           # what the race leaves behind
    run(ext, POLL_FRAMES + 2)
    assert ext.bt_params.value == SIGNAL_NONE, "a request it will not run must not be left standing"
    assert ext._bt_saw_clear, "the store answered, so the guard had no business staying up"

  def test_the_next_press_works(self):
    """The one that matters. Before this, every press after the race did nothing, forever."""
    ext = make_ext(SIGNAL_BLINK_LEFT)
    ext._bt_saw_clear = False
    run(ext, POLL_FRAMES + 2)           # the poisoned press is discarded here
    ext.bt_params.value = SIGNAL_LEFT   # he presses again
    out = run(ext, POLL_FRAMES + int(1.0 / DT_CTRL), lamp_left=False)
    assert ext.bt_state == 1, "still refusing after the store proved it works"
    assert any(v == SIGNAL_LEFT for v in out), "armed but never commanded the lamp"

  def test_a_store_that_really_is_broken_still_holds_it_down(self):
    """The guard's actual job. If the write genuinely cannot land, retrying must not become a way
    in -- a request we can never clear must never start a pulse."""
    ext = make_ext(SIGNAL_LEFT)
    ext.bt_params.put = lambda *a, **kw: None     # writes vanish
    ext._bt_saw_clear = False
    out = run(ext, POLL_FRAMES + int(3.0 / DT_CTRL))
    assert ext.bt_state == 0, "armed against a store that cannot be cleared"
    assert all(v == SIGNAL_NONE for v in out), "commanded the lamp with no way to stop it"


class TestARunDoesNotAbortItself:
  """From the driveway, stopped with the parking brake on: "blink right did nothing... then two
  flashes... waited, six... waited more, three. Only ever short a few blinks, never a gap."

  Short, never gapped, varying with the wait is a run being CUT OFF. The standstill gate was the
  first suspect and it is ruled out -- the car was not moving at all. That leaves the driver-stalk
  check, which reads the very signal this module writes.

  Whether the value can come back around is not settled here. These pin the part that is certain:
  the side we are commanding cannot be read as the driver reaching for the stalk.
  """

  def test_the_commanded_side_reported_back_does_not_stop_the_run(self):
    ext = make_ext(SIGNAL_BLINK_LEFT)
    run(ext, POLL_FRAMES + 2)
    assert ext.bt_state == 1, "never armed"
    # the car now reports a LEFT switch -- which is what we are commanding
    run(ext, int(2.0 / DT_CTRL), left_blinker=True, lamp_left=True)
    assert ext.bt_state == 1, "our own commanded side aborted the run"

  def test_the_other_side_still_stops_it_at_once(self):
    """The case the check exists for: the driver reaching for the stalk to go the other way."""
    ext = make_ext(SIGNAL_BLINK_LEFT)
    run(ext, POLL_FRAMES + 2)
    assert ext.bt_state == 1
    run(ext, 4, right_blinker=True)
    assert ext.bt_state == 2, "the driver signalling the other way did not stop it"
    assert ext.bt_blocked == 3, "stopped without saying it was the stalk"

  def test_a_stop_records_which_gate_did_it(self):
    """Every one of these ends a run already under way, and all four used to do it silently -- the
    panel then showed a flash count under SIGNAL WORKS, which reads as the car half-ignoring us."""
    ext = make_ext(SIGNAL_BLINK_LEFT)
    run(ext, POLL_FRAMES + 2)
    run(ext, 4, v_ego=5.0)
    assert ext.bt_state == 2
    assert ext.bt_blocked == 1, "car moved, and the panel would have blamed the signal"


class TestOneMissedLampEdgeDoesNotEndTheRun:
  """The shape that fits every observation: "7, then nothing, then 2, then 6, then 3, then 1... it
  still seems really random what number of flashes it will do", with the readout agreeing with the
  count and naming no stop reason.

  No stop reason means no gate cut it off -- it ran out of time waiting. The loop sends the next
  blink only on a falling edge later than its own last command, and once the lamp has been seen
  there was no timeout at all, so a single missed falling edge was terminal: nothing more is sent
  and the run ends wherever it had got to. Short but never gapped, correct spacing, random count,
  unaffected by waiting or by cycling the ignition.
  """

  @staticmethod
  def _blink(ext, cycles, period_s=0.76, on_s=0.4, miss_after=None):
    """Drive the lamp like a real flasher, optionally dropping ONE falling edge."""
    sent = []
    lit = False
    t_on = 0.0
    for i in range(int(cycles * period_s * 2 / DT_CTRL)):
      t = i * DT_CTRL
      # the lamp follows our commands: light it when one goes out, hold for on_s
      if sent and not lit and t - sent[-1] < DT_CTRL * 2:
        lit, t_on = True, t
      if lit and t - t_on >= on_s:
        drop = miss_after is not None and len(sent) == miss_after
        if not drop:
          lit = False
      out = ext.update_blinker_test(make_cs(lamp_left=lit))
      if out == SIGNAL_LEFT:
        sent.append(t)
    return sent

  def test_a_healthy_loop_is_paced_by_the_lamp(self):
    ext = make_ext(SIGNAL_BLINK_LEFT)
    run(ext, POLL_FRAMES + 2)
    sent = self._blink(ext, cycles=10)
    assert len(sent) >= BLINK_COUNT - 1, f"only {len(sent)} of {BLINK_COUNT}"
    gaps = [b - a for a, b in zip(sent, sent[1:])]
    assert gaps and all(g < BLINK_STALL_S for g in gaps), (
      f"the watchdog paced a healthy loop instead of the lamp: {gaps}")

  def test_a_missed_falling_edge_no_longer_ends_it(self):
    """The lamp sticks on after the third command. Before the watchdog, that was the last blink."""
    ext = make_ext(SIGNAL_BLINK_LEFT)
    run(ext, POLL_FRAMES + 2)
    sent = self._blink(ext, cycles=14, miss_after=3)
    assert len(sent) > 3, f"stalled at {len(sent)} blinks on one missed edge"

  def test_the_watchdog_cannot_fire_before_a_whole_cycle_has_passed(self):
    """A timeout at the blink period raced the loop when the flasher was slower than configured --
    which is why the fallback below it is gated on never having seen the lamp at all. This one has
    to sit far enough out that it cannot do the same."""
    assert BLINK_STALL_S > 2 * DEFAULT_BLINK_PERIOD_S


class TestItWillNotStartIntoTheCarsOwnFlashing:
  """The invariant he found, which is the one that explains everything: "if I wait long enough in
  between tests, the blinker works flawlessly. If I don't wait enough, I'll get less blinks or
  sometimes a gap."

  The fault depends on the gap between TESTS, so state survives from one run into the next -- and
  the only thing that does is the lamp. His BCM flashes seven times from one stalk deflection, so
  the module is still working through its own pattern when a run ends. Starting into that means the
  closed loop is watching edges the CAR is producing.

  The arming gate watched TurnLghtSwtch_D_Stat, which is the STALK, and the stalk is idle the whole
  time the BCM is finishing a pattern nobody is asking for any more.
  """

  def test_it_holds_off_while_the_lamp_is_still_going(self):
    ext = make_ext(SIGNAL_BLINK_LEFT)
    run(ext, POLL_FRAMES + 2, lamp_left=True)
    assert ext.bt_state == 0, "armed into the car's own flashing"
    assert ext.bt_blocked == 5, "held off without saying the lamp was busy"

  def test_the_request_is_HELD_not_dropped(self):
    """He cannot see the panel from inside the settings menu, so a refusal is invisible and reads
    as a dead button. The press has to survive the wait and then run."""
    ext = make_ext(SIGNAL_BLINK_LEFT)
    run(ext, POLL_FRAMES + 2, lamp_left=True)
    assert ext.bt_params.value == SIGNAL_BLINK_LEFT, "the press was thrown away"
    run(ext, int((LAMP_SETTLE_S + 0.2) / DT_CTRL))
    run(ext, POLL_FRAMES + 2)
    assert ext.bt_state == 1, "the held request never ran"

  def test_but_a_lamp_that_never_settles_does_not_hold_it_forever(self):
    """Holding against a stuck lamp is how one ends up pulsing long after anybody asked."""
    ext = make_ext(SIGNAL_BLINK_LEFT)
    run(ext, int((LAMP_WAIT_MAX_S + 2.0) / DT_CTRL), lamp_left=True)
    assert ext.bt_state == 0
    assert ext.bt_params.value == SIGNAL_NONE, "a request nobody can run was left armed"

  def test_a_gap_between_flashes_is_not_the_pattern_ending(self):
    """The lamp is dark for half of every cycle. Arming in one of those gaps is the same fault."""
    ext = make_ext(SIGNAL_BLINK_LEFT)
    run(ext, POLL_FRAMES + 2, lamp_left=True)
    run(ext, int(0.4 / DT_CTRL))                       # a plausible off-phase
    assert ext.bt_state == 0, "armed in the gap between two of the car's own flashes"

  def test_and_starts_once_the_lamp_has_actually_settled(self):
    ext = make_ext(SIGNAL_BLINK_LEFT)
    run(ext, POLL_FRAMES + 2, lamp_left=True)
    run(ext, int((LAMP_SETTLE_S + 0.2) / DT_CTRL))     # the pattern has ended
    run(ext, POLL_FRAMES + 2)
    assert ext.bt_state == 1, "still refusing after the lamp went quiet"

  def test_a_fresh_start_does_not_have_to_wait(self):
    """Nothing has flashed since boot, so there is nothing to settle from."""
    ext = make_ext(SIGNAL_BLINK_LEFT)
    run(ext, POLL_FRAMES + 2)
    assert ext.bt_state == 1

  def test_measuring_is_exempt(self):
    """It commands nothing, and the driver's own lamp is exactly what it is there to watch."""
    ext = make_ext(SIGNAL_MEASURE)
    run(ext, POLL_FRAMES + 2, lamp_left=True)
    assert ext.bt_state == 1, "refused to watch the lamp because the lamp was lit"
