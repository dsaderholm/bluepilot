"""
BluePilot: stationary bench test for turn-signal actuation on Ford.

ONE QUESTION: if openpilot writes TurnLghtSwtch_D_Stat into the Steering_Data_FD1 frame it already
transmits, does the BCM actually light the lamp?

READ THE DBC BEFORE GOING FURTHER -- 2026-08-04, and it should have been read first.

  BO_ 131 Steering_Data_FD1: 8 GWM
   SG_ TurnLghtSwtch_D_Stat : 5|2@0+ (1,0) [0|3] "SED"  IPMA_ADAS,PSCM

The frame is sent by the GATEWAY, and this signal is addressed to IPMA_ADAS and PSCM -- the camera
and the steering module. The body module is not a listed receiver. This is not a lamp command; it
is a status broadcast telling the ADAS modules that the driver has signalled.

The only turn-lamp signals that exist are on the body module's own frame -- TurnLghtLeft_D_Rq,
TurnLghtLeftOn_B_Stat -- and they are the BCM reporting OUTWARD, addressed to CMR_DSMC and
IPMA_ADAS. Nothing in this DBC lets anyone ask the BCM for a lamp.

Which reframes every symptom. Writing this signal means contradicting the gateway about where the
driver's physical stalk is, on a frame the gateway is also sending. The lamp responding at all is
most likely the gateway relaying our value onward to the body module over MS-CAN, in alternation
with the real stalk position it relays from the column. That is a fight no send rate wins, and it
matches what the car does: "test left signal does weird fast flashing, same with test right."

The value table closes the other door too:

  VAL_ 131 TurnLghtSwtch_D_Stat 3 "Unused_Treat_As_Off" 2 "Right" 1 "Left" 0 "Off"

There is no momentary or one-touch encoding to send. Value 3 is explicitly to be treated as off, so
the tap can only ever be a very short Left -- which is exactly what it measured: one flash, no
latch.

CONCLUSION: commanding the turn signal through this signal is probably not possible on this car,
and the remaining honest options are a different message entirely (none is documented here) or the
canbox on MS-CAN, where the body module actually lives. Do not spend more effort on send rates.

It matters because desire_helper's entire lane-change state machine keys off
carState.leftBlinker/rightBlinker, which are decoded from the SCCM's copy of that same message on
bus 0 (carstate.py). openpilot cannot self-receive its own transmissions -- panda returns them at
bus | 0x80 and the parser drops them -- so commanding the signal would NOT feed back through
carState. The answer has to come from the car: BodyInfo_3_FD1 (0x3B3) carries
TurnLghtLeftOn_B_Stat / TurnLghtRightOn_B_Stat, the body module's own report of the lamps, on the
powertrain bus openpilot already parses. Closed loop, no extra hardware, no canbox.

Nothing consumes this. It is not wired to desire_helper, to the passing-assist observer, or to any
controller. It runs a single timed pulse when explicitly requested and then disarms itself.

WHY THE GATES ARE WHAT THEY ARE
A turn signal is not an internal state -- it is a message to other drivers about what this car is
about to do. So:
  - Standstill only. A stationary car signals nothing about a maneuver, which makes an erroneous
    pulse meaningless to bystanders rather than misleading.
  - Never while cruise is engaged, so this can never overlap a real lane-change decision.
  - The driver's stalk always wins. If they are signaling, we do not touch it.
  - Hard timeout, enforced by frame count rather than by anything the requester controls, so a
    stuck param or a crashed UI cannot leave a lamp latched on.
  - The request param is CLEAR_ON_MANAGER_START and self-cleared on completion, so it cannot
    survive a reboot and cannot repeat without a fresh, deliberate request.
"""

from opendbc.car import DT_CTRL
from openpilot.common.params import Params

# Values of TurnLghtSwtch_D_Stat, per the DBC and carstate.py's decode (== 1 left, == 2 right).
SIGNAL_NONE, SIGNAL_LEFT, SIGNAL_RIGHT = 0, 1, 2

SIGNAL_TAP_LEFT, SIGNAL_TAP_RIGHT = 3, 4    # request values only; the COMMAND is still 1/2
SIGNAL_EDGE_LEFT, SIGNAL_EDGE_RIGHT = 5, 6  # ONE frame -- see EDGE_FRAMES

PULSE_DURATION_S = 4.0        # long enough for several flash cycles at ~1.5 Hz

# --- WHAT THE CAR ACTUALLY SAID, 2026-08-04 ---
#
# "Before, it worked, but if I spammed the button I did it fast."
#
# A single clean press lights the lamp and flashes it normally. The erratic flashing that has
# haunted this module for two sessions is an artifact of REPEATED REQUESTS, not of the signal
# mechanism. Holding the level works.
#
# Worth recording how nearly that went the other way. The reasoning above -- level versus edge, our
# frames interleaving with the SCCM's, no send rate can fix it -- is sound, fits the symptom, and
# was one message away from being written down as settled fact on the strength of "it flashes
# really fast". It was the wrong conclusion drawn from a real observation, and the only thing that
# separated them was the owner distinguishing "I pressed it" from "I spammed it".
#
# WHICH IS WHY THE FLASH COUNT EXISTS NOW. Two runs of this test produced two recollections and
# settled nothing, because "really fast" is not a measurement. A clean 1.5 Hz signal over a four
# second hold is about six flashes; the erratic case is many times that. One number, compared
# between runs, ends the argument -- and it should have been here from the first version.
#
# THE TAP IS KEPT ANYWAY, as an option rather than a rescue. His BCM is set through FORScan to
# flash eight times from a momentary stalk deflection, and that is how he triggers every nudgeless
# lane change he makes -- so the pattern is the body module's to generate. Handing it one clean edge
# and going silent means the rate, the count and the cancel behavior are all the car's own,
# identical to a stalk tap, with nothing contending with the SCCM at all. If both work, that is the
# better one, and it is also the only one that can produce a signal longer than our own timeout.
TAP_COMMAND_S = 0.25          # about the length of a real stalk tap
# ...then STOP COMMANDING AND KEEP WATCHING. Flashes after we have gone quiet are the BCM running
# its own pattern, which is the entire measurement.
OBSERVE_AFTER_S = 3.0

# --- one frame. the last untried thing, and the smallest possible ask ---
#
# openpilot does not transmit Steering_Data_FD1 at all on a normal frame -- carcontroller only
# appends it when this module returns something. So a "tap" is not a tap: at 0.25 s and one send
# every BUTTONS_STEP it is FIVE separate CAN frames injected into the gateway's continuous stream
# of "stalk is off". Five rising edges. A real stalk tap is one mechanical event.
#
# If the body module triggers its one-touch on an edge, five edges in a quarter second is five
# retriggers -- which looks exactly like what the car does. One frame is the minimum perturbation
# anyone can make, and nobody has tried it.
#
# AND THERE IS NOW A REASON TO EXPECT IT TO WORK, from the owner: "how does ICBM reliably control
# stuff on the steering wheel?"
#
# ICBM drives the cruise buttons through THIS EXACT FRAME, on this bus, against the same gateway
# transmissions -- and it is completely reliable. So contention is not the difference. Edge versus
# level is:
#
#   a button press is an EVENT. One frame saying "pressed" is complete in itself and the receiver
#   latches it; later frames saying "not pressed" do not undo it, they are merely the absence of a
#   new press. Contradicting traffic is harmless.
#
#   TurnLghtSwtch_D_Stat is a LEVEL. It states where the stalk IS. Left, then Off, then Left is not
#   a request for a signal -- it is a stalk being flicked, and that is exactly what the lamp does.
#
# Which says the way to command this signal is to treat it the way ICBM treats a button: one frame,
# one edge, then silence. If the body module latches a one-touch from that, everything longer has
# simply been re-triggering it -- and the erratic flashing was never contention at all.
EDGE_FRAMES = 1

# How long the verdict stays on screen before the machine re-arms itself.
#
# THIS USED TO DEPEND ON THE REQUEST PARAM READING ZERO, and that is what made all four buttons go
# dead. Reported: "if I do them in rapid succession, it just will stop working for a little bit",
# and "I do tap right, it will work once and then all four buttons will stop working."
#
# The old exit from DONE was: poll every half second, and leave only if the request reads 0. A press
# during the verdict window writes a request, so that poll sees non-zero, drops the press and clears
# it -- and the next press half a second later does the same. Pressing repeatedly, which is exactly
# what anyone does when a button looks like it did nothing, held the machine in DONE for as long as
# they kept trying. The state that was supposed to prevent runaway pulses instead punished
# impatience.
#
# A clock cannot be held open by pressing a button. Three seconds is long enough to read a two-word
# verdict and a flash count, and it expires whatever the param says.
DONE_HOLD_S = 3.0
# Match every other sender of Steering_Data_FD1: CarControllerParams.BUTTONS_STEP, 20 Hz against the
# SCCM's own 10 Hz copy of the same frame.
#
# READ THIS BEFORE TRUSTING THE RATE TO FIX ANYTHING. Observed on the car, one pulse: the lamp
# flashed fast and erratically. The rate was 100 Hz at the time and lowering it is a guess, not a
# diagnosis, and quite possibly the wrong direction --
#
#   Our frames and the SCCM's interleave at EVERY rate; we cannot stop the SCCM transmitting. What
#   the rate changes is only the duty cycle the BCM sees. At 100 Hz it saw the switch commanded
#   ~91% of the time, at 20 Hz ~67%. If the flashing came from the OFF frames getting through, the
#   slower rate makes it worse, not better.
#
#   The likelier mechanism is the signal's TYPE. TurnLghtSwtch_D_Stat is a LEVEL -- where the stalk
#   is right now -- not an edge like the cruise buttons this same frame carries. openpilot's cancel
#   and resume work fine amid contradicting SCCM frames precisely because one frame saying "pressed"
#   is a complete event. A level alternating with OFF ten times a second is a stalk being flicked,
#   and no send rate makes that go away.
#
# Which is what this whole module exists to find out. It is a bench test, not a feature: run it,
# watch the lamp, and let the answer decide whether commanding the signal is possible on this car
# at all. Do not write a fix into this comment before the lamp has been watched again.
#
# The rate lives in this module rather than in carcontroller.py deliberately: that file cannot be
# tested offline, and a send rate is the exact class of mistake that reaches the car unnoticed.
BUTTONS_STEP = 5              # 100 Hz / 5 = 20 Hz, matching CarControllerParams.BUTTONS_STEP
STANDSTILL_V_EGO = 0.3        # m/s
PARAMS_POLL_S = 0.5


class BlinkerTestExt:
  """Mixed into CarController. Owns one pulse at a time and nothing else."""

  def __init__(self):
    self.bt_params = Params()
    self.bt_state = 0          # 0 idle, 1 pulsing, 2 done -- mirrors the capnp enum
    self.bt_commanded = SIGNAL_NONE
    self.bt_frames_left = 0
    self.bt_blocked = 0        # mirrors capnp Blocked
    self.bt_lamp_seen = False
    # HOW MANY TIMES the lamp lit, not merely whether it did. See the note above TAP_COMMAND_S:
    # "really fast" is not a measurement, and two runs of this test settled nothing because of it.
    self.bt_flashes = 0
    self.bt_flashes_after = 0
    self.bt_watching = SIGNAL_NONE
    self._bt_lamp_prev = False
    self._bt_command_frames_left = 0
    self._bt_done_frames = 0
    # Has the request param been SEEN at zero since the last pulse was armed?
    #
    # This is what separates "the driver pressed again" from "our disarm write failed", and those
    # two need opposite handling. Without it the module had to pick one disaster: trust the param
    # and a failed write restarts the lamp forever, or distrust it and a second press deadlocks the
    # buttons until reboot. The car produced the second one.
    self._bt_saw_clear = True
    self._bt_frame = 0

  @property
  def bt_seconds_remaining(self) -> float:
    return self.bt_frames_left * DT_CTRL

  def _read_request(self) -> int:
    try:
      return int(self.bt_params.get("FordBlinkerTest", return_default=True))
    except (ValueError, TypeError):
      return SIGNAL_NONE

  def _verdict_press(self) -> bool:
    """Is there a genuinely NEW press waiting while the verdict is on screen?

    Reading zero here is the proof that our disarm write landed; only after that can a non-zero
    read mean a driver rather than a param we cannot clear. See _bt_saw_clear.
    """
    return self._read_request() != SIGNAL_NONE and self._bt_saw_clear

  def _disarm(self) -> None:
    """Clear the request so a pulse can never repeat on its own."""
    # int, NOT str -- see the note in bluepilot.py::_request_blinker_test. Writing "0" here raised
    # TypeError, so DONE could never be cleared and a second pulse could never be armed.
    try:
      # block=True, and it has to be. put() defaults to putNonBlocking, so the read-back below
      # raced the write and usually returned the OLD value -- which set _bt_saw_clear False and
      # left the runaway guard holding every button down until some later poll happened to see a
      # zero. That is "tap left will do one signal and then stop working for a while."
      #
      # A blocking write is acceptable here and nowhere else in this file: this path only runs at
      # standstill, once, in response to a button the driver is standing there waiting on.
      self.bt_params.put("FordBlinkerTest", 0, block=True)
    except Exception:  # noqa: BLE001 - a param write failure must not stop the timeout above
      pass
    # READ IT BACK NOW. This is the only proof the store took the write, and it has to be taken
    # here rather than waiting for a later poll to notice a zero -- that is a race the driver wins
    # by pressing again, which is precisely when the answer matters. See _bt_saw_clear.
    try:
      self._bt_saw_clear = self._read_request() == SIGNAL_NONE
    except Exception:  # noqa: BLE001 - unreadable is not proof of anything
      self._bt_saw_clear = False

  def update_blinker_test(self, CS) -> int:
    """Advance the state machine. Returns the TurnLghtSwtch_D_Stat value to transmit, or
    SIGNAL_NONE on frames where nothing should be sent.

    Returns SIGNAL_NONE in every case except an active, gated pulse -- and the caller passes that
    through to create_button_msg, which otherwise copies the driver's own switch position.

    **The state machine advances every frame; only the RETURN is rate-limited** to BUTTONS_STEP.
    That keeps the timeout, the standstill re-check and the lamp observation running at full rate
    while the message goes out at the rate the bus expects. See BUTTONS_STEP for what happened when
    it did not.
    """
    self._bt_frame += 1
    send_frame = (self._bt_frame % BUTTONS_STEP) == 0
    # THE VERDICT HAS TO GET OUT OF THIS OBJECT. This machine lives in the CarController; the
    # carStateBP message is built in CarState. Nothing bridged them, so `state`, `lampSeen`,
    # `secondsRemaining` and `blockedReason` were read by the panel and written by nobody -- the
    # whole test reported its answer to an empty room, and the only way to read it was to watch a
    # mirror. Stashed onto the CarState the controller is already handed, which carstate_ext then
    # publishes. One cycle stale by construction, which at 100 Hz is 10 ms.
    self._publish_to(CS)

    # ---- active pulse: timeout is checked FIRST, before anything that could throw or block ----
    if self.bt_state == 1:
      self.bt_frames_left -= 1

      self._bt_command_frames_left = max(0, self._bt_command_frames_left - 1)
      commanding = self._bt_command_frames_left > 0

      lamp_left, lamp_right = self._lamp_state(CS)
      # Watch the side we ASKED for. bt_commanded is cleared for transmission during a tap's
      # observe phase, so it is the wrong thing to watch once we go quiet -- which is exactly when
      # the interesting flashes happen.
      lamp = lamp_left if self.bt_watching == SIGNAL_LEFT else lamp_right
      if lamp:
        self.bt_lamp_seen = True
      # Rising edges only. The lamp is a square wave; counting the level would count frames.
      if lamp and not self._bt_lamp_prev:
        self.bt_flashes += 1
        if not commanding:
          self.bt_flashes_after += 1
      self._bt_lamp_prev = lamp

      # Any of these ends the pulse immediately. Standstill is re-checked every frame, not just at
      # the start: if the car begins rolling mid-pulse the signal drops at once.
      if self.bt_frames_left <= 0 or CS.out.vEgo > STANDSTILL_V_EGO or \
         CS.out.leftBlinker or CS.out.rightBlinker or CS.out.cruiseState.enabled:
        self.bt_state = 2
        self.bt_commanded = SIGNAL_NONE
        self.bt_frames_left = 0
        self._bt_done_frames = int(DONE_HOLD_S / DT_CTRL)
        self._disarm()
        return SIGNAL_NONE

      # Silent once the command window closes: the point of a tap's observe phase is to find out
      # what the car does WITHOUT us.
      if not commanding:
        return SIGNAL_NONE
      return self.bt_commanded if send_frame else SIGNAL_NONE

    # ---- showing the verdict: a clock, not a condition. See DONE_HOLD_S ----
    #
    # Runs EVERY frame rather than on the poll, and depends on nothing the driver can do. Presses
    # that land in here are still dropped -- queueing would light a lamp seconds after the button,
    # with nothing on screen connecting the two -- but they can no longer extend the wait.
    if self.bt_state == 2:
      self._bt_done_frames -= 1
      if self._bt_done_frames <= 0:
        self.bt_state = 0
        self.bt_blocked = 0
        self._disarm()
      elif self._bt_frame % int(PARAMS_POLL_S / DT_CTRL) == 0 and self._verdict_press():
        # A FRESH PRESS PREEMPTS THE VERDICT rather than being dropped.
        #
        # Measured before this: a hold locked the buttons for 7.5 s and a tap for 6.75 s -- four
        # seconds of pulse and three of verdict, during which every button was dead. That is the
        # whole of "the other three buttons don't work", and dropping the press made it worse by
        # wasting the one input that says the driver has finished reading.
        #
        # Safe because the request param is a ONE-SHOT: it is cleared the moment a pulse ends, so a
        # non-zero read here is a new click and not a stale one. A stuck param cannot repeat,
        # because nothing but the UI ever writes a value.
        self.bt_state = 0
        self.bt_blocked = 0
      if self.bt_state == 2:
        return SIGNAL_NONE
      # ...otherwise fall through and arm on this same frame, which is already a poll frame.

    # ---- idle: look for a request, at a low rate ----
    if self._bt_frame % int(PARAMS_POLL_S / DT_CTRL) != 0:
      return SIGNAL_NONE

    request = self._read_request()
    if request == SIGNAL_NONE:
      # Proof the store is taking our writes. Until this is seen, a non-zero read cannot be told
      # apart from our own disarm having silently failed. See _bt_saw_clear.
      self._bt_saw_clear = True

    if request not in (SIGNAL_LEFT, SIGNAL_RIGHT, SIGNAL_TAP_LEFT, SIGNAL_TAP_RIGHT,
                       SIGNAL_EDGE_LEFT, SIGNAL_EDGE_RIGHT):
      self.bt_blocked = 0
      return SIGNAL_NONE

    # A request we can never clear must not start a second pulse. This is the runaway guard, and it
    # is the only thing between a failed param write and a lamp that pulses until the ignition
    # goes off.
    if not self._bt_saw_clear:
      return SIGNAL_NONE

    # Gates. Each records why so a refused request is visible rather than silently ignored.
    if CS.out.vEgo > STANDSTILL_V_EGO:
      self.bt_blocked = 1
      return SIGNAL_NONE
    if CS.out.cruiseState.enabled:
      self.bt_blocked = 2
      return SIGNAL_NONE
    if CS.out.leftBlinker or CS.out.rightBlinker:
      self.bt_blocked = 3
      return SIGNAL_NONE

    self.bt_blocked = 0
    self.bt_state = 1
    self._bt_saw_clear = False
    tap = request in (SIGNAL_TAP_LEFT, SIGNAL_TAP_RIGHT, SIGNAL_EDGE_LEFT, SIGNAL_EDGE_RIGHT)
    edge = request in (SIGNAL_EDGE_LEFT, SIGNAL_EDGE_RIGHT)
    self.bt_commanded = (SIGNAL_LEFT if request in (SIGNAL_LEFT, SIGNAL_TAP_LEFT, SIGNAL_EDGE_LEFT)
                         else SIGNAL_RIGHT)
    self.bt_watching = self.bt_commanded
    # A tap commands briefly then watches in silence; a hold commands throughout. Same counter for
    # both, so the two runs are directly comparable with one variable changed.
    # EDGE_FRAMES is counted in SENDS, not control frames -- the send is rate-limited to
    # BUTTONS_STEP, so one send needs the window to stay open that long.
    self._bt_command_frames_left = (EDGE_FRAMES * BUTTONS_STEP if edge else
                                    int((TAP_COMMAND_S if tap else PULSE_DURATION_S) / DT_CTRL))
    self.bt_frames_left = int(((TAP_COMMAND_S + OBSERVE_AFTER_S) if tap
                               else PULSE_DURATION_S) / DT_CTRL)
    self.bt_lamp_seen = False
    self.bt_flashes = 0
    self.bt_flashes_after = 0
    self._bt_lamp_prev = False
    return self.bt_commanded

  def _publish_to(self, CS) -> None:
    """Hand this cycle's state to CarState, which owns the message. See update_blinker_test."""
    CS.bt_state = self.bt_state
    CS.bt_commanded = self.bt_commanded
    CS.bt_seconds_remaining = self.bt_seconds_remaining
    CS.bt_lamp_seen = self.bt_lamp_seen
    CS.bt_blocked = self.bt_blocked
    CS.bt_flashes = self.bt_flashes
    CS.bt_flashes_after = self.bt_flashes_after

  @staticmethod
  def _lamp_state(CS) -> tuple[bool, bool]:
    """The car's own report of the lamps, decoded in carstate.py from BodyInfo_3_FD1.

    This is the measurement the whole test exists to take -- what the BCM actually did, as opposed
    to what we asked for.
    """
    return bool(getattr(CS, 'turn_lamp_left', False)), bool(getattr(CS, 'turn_lamp_right', False))
