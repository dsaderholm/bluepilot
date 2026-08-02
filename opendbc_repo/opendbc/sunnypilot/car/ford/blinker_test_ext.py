"""
BluePilot: stationary bench test for turn-signal actuation on Ford.

ONE QUESTION: if openpilot writes TurnLghtSwtch_D_Stat into the Steering_Data_FD1 frame it already
transmits, does the BCM actually light the lamp?

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
  - Standstill only. A stationary car signals nothing about a manoeuvre, which makes an erroneous
    pulse meaningless to bystanders rather than misleading.
  - Never while cruise is engaged, so this can never overlap a real lane-change decision.
  - The driver's stalk always wins. If they are signalling, we do not touch it.
  - Hard timeout, enforced by frame count rather than by anything the requester controls, so a
    stuck param or a crashed UI cannot leave a lamp latched on.
  - The request param is CLEAR_ON_MANAGER_START and self-cleared on completion, so it cannot
    survive a reboot and cannot repeat without a fresh, deliberate request.
"""

from opendbc.car import DT_CTRL
from openpilot.common.params import Params

# Values of TurnLghtSwtch_D_Stat, per the DBC and carstate.py's decode (== 1 left, == 2 right).
SIGNAL_NONE, SIGNAL_LEFT, SIGNAL_RIGHT = 0, 1, 2

PULSE_DURATION_S = 4.0        # long enough for several flash cycles at ~1.5 Hz
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
    self._bt_frame = 0

  @property
  def bt_seconds_remaining(self) -> float:
    return self.bt_frames_left * DT_CTRL

  def _read_request(self) -> int:
    try:
      return int(self.bt_params.get("FordBlinkerTest", return_default=True))
    except (ValueError, TypeError):
      return SIGNAL_NONE

  def _disarm(self) -> None:
    """Clear the request so a pulse can never repeat on its own."""
    # int, NOT str -- see the note in bluepilot.py::_request_blinker_test. Writing "0" here raised
    # TypeError, so DONE could never be cleared and a second pulse could never be armed.
    try:
      self.bt_params.put("FordBlinkerTest", 0)
    except Exception:  # noqa: BLE001 - a param write failure must not stop the timeout above
      pass

  def update_blinker_test(self, CS) -> int:
    """Advance the state machine. Returns the TurnLghtSwtch_D_Stat value to transmit.

    Returns SIGNAL_NONE in every case except an active, gated pulse -- and the caller passes that
    through to create_button_msg, which otherwise copies the driver's own switch position.
    """
    self._bt_frame += 1

    # ---- active pulse: timeout is checked FIRST, before anything that could throw or block ----
    if self.bt_state == 1:
      self.bt_frames_left -= 1

      lamp_left, lamp_right = self._lamp_state(CS)
      if (self.bt_commanded == SIGNAL_LEFT and lamp_left) or \
         (self.bt_commanded == SIGNAL_RIGHT and lamp_right):
        self.bt_lamp_seen = True

      # Any of these ends the pulse immediately. Standstill is re-checked every frame, not just at
      # the start: if the car begins rolling mid-pulse the signal drops at once.
      if self.bt_frames_left <= 0 or CS.out.vEgo > STANDSTILL_V_EGO or \
         CS.out.leftBlinker or CS.out.rightBlinker or CS.out.cruiseState.enabled:
        self.bt_state = 2
        self.bt_commanded = SIGNAL_NONE
        self.bt_frames_left = 0
        self._disarm()
        return SIGNAL_NONE

      return self.bt_commanded

    # ---- idle: look for a request, at a low rate ----
    if self._bt_frame % int(PARAMS_POLL_S / DT_CTRL) != 0:
      return SIGNAL_NONE

    request = self._read_request()

    # DONE is terminal until the request goes back to 0. Without this, a param write that failed
    # (or a UI that keeps rewriting the value) re-arms the machine the moment the pulse ends and
    # the lamp flashes forever -- the timeout stops each pulse but nothing stops the next one.
    # Requiring the request to clear first is what makes "one deliberate request, one pulse" true
    # rather than merely intended.
    if self.bt_state == 2:
      if request == SIGNAL_NONE:
        self.bt_state = 0
      return SIGNAL_NONE
    if request not in (SIGNAL_LEFT, SIGNAL_RIGHT):
      self.bt_blocked = 0
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
    self.bt_commanded = request
    self.bt_frames_left = int(PULSE_DURATION_S / DT_CTRL)
    self.bt_lamp_seen = False
    return self.bt_commanded

  @staticmethod
  def _lamp_state(CS) -> tuple[bool, bool]:
    """The car's own report of the lamps, decoded in carstate.py from BodyInfo_3_FD1.

    This is the measurement the whole test exists to take -- what the BCM actually did, as opposed
    to what we asked for.
    """
    return bool(getattr(CS, 'turn_lamp_left', False)), bool(getattr(CS, 'turn_lamp_right', False))
