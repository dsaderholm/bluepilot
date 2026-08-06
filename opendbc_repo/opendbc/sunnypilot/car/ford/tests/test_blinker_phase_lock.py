"""BluePilot: the phase lock, which is the thing that made the blinker work.

Confirmed on the road 2026-08-06 after four earlier attempts failed. Until it was extracted from
blinker_test_ext it had no coverage at all -- the mechanism the whole feature rests on was one of
this fork's untestable surfaces, and every earlier fix was judged by driveway testing he was sick
of doing. These pin the behavior so an upstream change to CANParser, Ford's carstate, or the send
path fails here instead of on his car.
"""

from opendbc.sunnypilot.car.ford.blinker_phase_lock import (
  DT_CTRL, GATEWAY_LOST_S, GATEWAY_PERIOD_S, BlinkerPhaseLock,
)

GW_FRAMES = int(round(GATEWAY_PERIOD_S / DT_CTRL))   # control frames between gateway messages


def drive(lock, seconds, gateway=True, start_frame=0, ts_start=1):
  """Run the lock for a while and return the frames it chose to send on.

  gateway=False models the gateway having gone silent: the timestamp never changes.
  """
  sent, ts = [], ts_start
  for i in range(int(seconds / DT_CTRL)):
    frame = start_frame + i
    if gateway and i % GW_FRAMES == 0:
      ts += 1
    if lock.should_send(frame, ts):
      sent.append(frame)
  return sent


def test_it_sends_only_on_frames_a_gateway_message_just_arrived():
  """The whole idea. The gateway owns this message ID and overwrites ours, so the only moment the
  switch is really ours is straight after one of its frames lands."""
  lock = BlinkerPhaseLock(period_s=0.76)
  lock.arm(0)
  sent = drive(lock, 1.0)
  assert sent, "never sent at all"
  assert all(f % GW_FRAMES == 0 for f in sent), f"sent between gateway frames: {sent}"


def test_it_never_sends_twice_for_one_gateway_frame():
  """Called every control frame, it must answer True at most once per gateway message -- otherwise
  the extra frames are exactly the too-fast sending that reached the car."""
  lock = BlinkerPhaseLock(period_s=0.76)
  lock.arm(0)
  sent = drive(lock, 2.0)
  assert len(sent) == len(set(sent))
  for a, b in zip(sent, sent[1:], strict=False):
    assert b - a >= GW_FRAMES, f"two sends {b - a} frames apart"


def test_on_for_half_the_period_and_off_for_the_other_half():
  lock = BlinkerPhaseLock(period_s=0.76)
  lock.arm(0)
  sent = drive(lock, 3.04)     # four full periods
  # 0.76 s / 2 = 0.38 s, which is 4 gateway frames per half at 10 Hz.
  assert lock._slot_frames() == 4
  # Four ON frames, then a gap of four OFF frames, repeating.
  gaps = [b - a for a, b in zip(sent, sent[1:], strict=False)]
  assert set(gaps) <= {GW_FRAMES, GW_FRAMES * 5}, gaps


def test_one_blink_counted_per_ON_phase_however_many_frames_hold_it():
  """He counts flashes, not CAN frames. A four-gateway-frame ON phase is ONE flash, and getting
  this wrong is what produced "it did five, took a break and did one more, totaling six"."""
  lock = BlinkerPhaseLock(period_s=0.76)
  lock.arm(0)
  drive(lock, 3.04)
  assert lock.blinks_sent == 4, f"four periods produced {lock.blinks_sent} blinks"


def test_arming_starts_a_clean_ON_phase():
  """A run that began wherever the last one left off is how the count came out different every
  time, and why it depended on exactly when the button was pressed."""
  lock = BlinkerPhaseLock(period_s=0.76)
  lock.arm(0)
  drive(lock, 1.0)
  lock.arm(1000)
  first = drive(lock, 0.5, start_frame=1000, ts_start=999)
  assert first and first[0] == 1000, "the first frame after arming was not an ON frame"
  assert lock.blinks_sent >= 1


def test_a_silent_gateway_does_not_stall_it_forever():
  """With nobody contending there is nothing to synchronize against, and waiting for a frame that
  is not coming would hold the command off indefinitely."""
  lock = BlinkerPhaseLock(period_s=0.76)
  lock.arm(0)
  # One real gateway frame first, THEN silence -- otherwise the lock has never seen a timestamp and
  # the first frame reads as fresh, which is correct behavior but is not the case under test.
  assert lock.should_send(0, 5), "the first frame after arming should go out immediately"
  sent = drive(lock, 2.0, gateway=False, start_frame=1, ts_start=5)
  assert sent, "a silent gateway stalled the command completely"
  assert sent[0] * DT_CTRL >= GATEWAY_LOST_S, "gave up waiting for the gateway too early"


def test_a_gateway_that_comes_back_is_locked_onto_again():
  lock = BlinkerPhaseLock(period_s=0.76)
  lock.arm(0)
  drive(lock, 1.0, gateway=False)
  before = lock.blinks_sent
  sent = drive(lock, 1.0, gateway=True, start_frame=100, ts_start=50)
  assert all(f % GW_FRAMES == 0 for f in sent), f"did not re-lock: {sent}"
  assert lock.blinks_sent > before


def test_a_period_shorter_than_the_gateway_still_turns_off():
  """Degrades to alternating gateway frames rather than dividing to a zero-length OFF phase, which
  would hold the signal on permanently."""
  lock = BlinkerPhaseLock(period_s=0.05)
  lock.arm(0)
  assert lock._slot_frames() == 1
  sent = drive(lock, 1.0)
  gaps = {b - a for a, b in zip(sent, sent[1:], strict=False)}
  assert gaps == {GW_FRAMES * 2}, f"never turned off: {gaps}"


def test_his_measured_period_gives_his_flash_rate():
  """760 ms is what "Measure My Blinker" read off his own stalk, and seven flashes is the one-touch
  he set in FORScan. Five seconds of lock should produce about seven."""
  lock = BlinkerPhaseLock(period_s=0.76)
  lock.arm(0)
  drive(lock, 7 * 0.76)
  assert lock.blinks_sent == 7, f"his flash rate produced {lock.blinks_sent} in seven periods"
