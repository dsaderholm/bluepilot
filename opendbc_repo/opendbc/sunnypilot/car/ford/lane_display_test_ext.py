"""
BluePilot: read the cluster's lane-display vocabulary off the actual car.

WHY THIS EXISTS
---------------
`LaActvStats_D_Dsply` has five states per side, and passing assist's cluster hint is built out of
them. Exactly ONE of those has provably rendered on this car:

  * **Available** (1 / 5) -- what `create_lkas_ui_msg` sends for a visible lane. That is the
    always-green line he described, so Available is green, and this is a fact rather than an
    inference.

Everything else is unmeasured, and one of them is not what it looks like. Upstream's departure
branch sends **Intervene**, but it sits inside `if enabled:` and `ldw.py` gates departure on
`not CC.latActive` -- so upstream can never reach it. Its Warning states (3 / 15) and its
**LA_Off** (30) live in the `else` branch, for cruise main off entirely.

BluePilot dropped upstream's `enabled` / `main_on` structure and computes the engaged-style values
unconditionally, which is very likely his "green on both sides of my car all the time, no matter
what": the lines go green whenever the model sees paint, engaged or not, and LA_Off is never sent
at all. It also makes Intervene the departure look in every state -- reachable, but ours, and
almost certainly never seen.

So four of five states plus LA_Off are guesses, and the passing hint rests on Suppress, one of the
guesses. Guessing at an unmeasured car display is exactly the mistake the blinker work made four
times over.

This walks them on the LEFT line while holding the RIGHT line at Available, so every step is a
side-by-side comparison against a line whose appearance is known. The sides are symmetric in the
value table, so there is nothing to learn from repeating it mirrored. LA_Off is the exception and
comes last: it is a whole-display value outside the 5x5 matrix, so it is sent raw.

The first step is deliberately the baseline, both sides Available. If step 1 does not draw two
green lines, the finding is that the cluster will not render this graphic at a standstill and the
test needs re-running some other way -- the test reports that about itself rather than producing
five identical "nothing" answers that look like data.

Runs only at a standstill: several of these states are the car's lane-departure look, and a display
that cries wolf is worse than no display. See [[fusion-ipc-is-lka-only]] for why the LKA display is
the only cluster channel this car has.
"""

try:
  from openpilot.common.params import Params
except ImportError:  # pragma: no cover - opendbc is importable without openpilot
  Params = None

DT_CTRL = 0.01

# Seconds each state is held. Long enough to look up, see it and look back down; short enough that
# the whole walk is under half a minute.
LANE_TEST_STEP_S = 3.0

# (raw LaActvStats_D_Dsply value, what the screen calls it). Raw rather than a (left, right) pair
# because LA_Off is 30, which does not decompose as left + 5*right -- the signal is [0|31] and the
# matrix only covers 0..24. Everything else here pins the right line to Available, the known green,
# so each step reads as "left looks like THIS next to a normal line".
LANE_TEST_STEPS = (
  (1 + 5 * 1, "BOTH GREEN"),
  (0 + 5 * 1, "LEFT: NONE"),
  (2 + 5 * 1, "LEFT: SUPPRESS"),
  (3 + 5 * 1, "LEFT: WARNING"),
  (4 + 5 * 1, "LEFT: INTERVENE"),
  # Upstream's value for lane assist not running. BluePilot never sends it, which is part of why
  # the lines are green regardless of whether anything is steering.
  (30, "LANE ASSIST OFF"),
)

# Published step index: 0 is idle, 1..len(LANE_TEST_STEPS) is the step being shown.
LANE_TEST_IDLE = 0

# Why a run was refused, published so the screen can say which. Mirrors BlinkerTestExt's blocked
# reasons: a test that silently does nothing is indistinguishable from a car that ignored it.
LANE_TEST_BLOCK_NONE = 0
LANE_TEST_BLOCK_MOVING = 1


class LaneDisplayTestExt:
  """Steps the cluster's lane display through every state so its vocabulary can be written down.

  Mixed into CarController alongside BlinkerTestExt. Owns no CAN: it returns an override pair that
  create_lkas_ui_msg substitutes for its computed value, which keeps the normal display logic in
  one place and makes the test incapable of altering it when idle.
  """

  def __init__(self):
    self.ldt_params = Params() if Params is not None else None
    self.ldt_step = LANE_TEST_IDLE      # 1-based index into LANE_TEST_STEPS, 0 when idle
    self.ldt_blocked = LANE_TEST_BLOCK_NONE
    self.ldt_seconds_remaining = 0.0
    self._ldt_frames_left = 0

  def _requested(self) -> bool:
    if self.ldt_params is None:
      return False
    try:
      return int(self.ldt_params.get("FordLaneDisplayTest", return_default=True)) != 0
    except (ValueError, TypeError):
      return False

  def _clear_request(self) -> None:
    if self.ldt_params is not None:
      # block=True: the read above races a putNonBlocking, and a request that survives its own run
      # restarts the walk forever.
      self.ldt_params.put("FordLaneDisplayTest", 0, block=True)

  def update_lane_display_test(self, CS):
    """Advance the walk by one cycle and return the raw LaActvStats override, or None when idle.

    Called every frame from CarController.update() before the HUD messages are built.
    """
    moving = not bool(CS.out.standstill)

    if self.ldt_step == LANE_TEST_IDLE:
      self.ldt_seconds_remaining = 0.0
      if self._requested():
        self._clear_request()
        if moving:
          # Refused, and it says so. The request is already cleared, so the walk does not ambush him
          # the moment he stops.
          self.ldt_blocked = LANE_TEST_BLOCK_MOVING
        else:
          self.ldt_blocked = LANE_TEST_BLOCK_NONE
          self.ldt_step = 1
          self._ldt_frames_left = int(round(LANE_TEST_STEP_S / DT_CTRL))
          # Set BEFORE publishing, not on the next cycle. The screen draws whatever it is handed on
          # the first frame, and a countdown that starts at zero reads as a walk that already ended.
          self.ldt_seconds_remaining = LANE_TEST_STEP_S
      self._publish_to(CS)
      return None if self.ldt_step == LANE_TEST_IDLE else self._current_override()

    # A run in progress. Rolling away abandons it immediately -- the departure-looking states must
    # never be on the cluster while the car is moving.
    if moving:
      self._abort()
      self._publish_to(CS)
      return None

    self._ldt_frames_left -= 1
    if self._ldt_frames_left <= 0:
      self.ldt_step += 1
      if self.ldt_step > len(LANE_TEST_STEPS):
        self._abort()
        self._publish_to(CS)
        return None
      self._ldt_frames_left = int(round(LANE_TEST_STEP_S / DT_CTRL))

    self.ldt_seconds_remaining = max(0.0, min(self._ldt_frames_left * DT_CTRL, LANE_TEST_STEP_S))
    self._publish_to(CS)
    return self._current_override()

  def _current_override(self):
    return LANE_TEST_STEPS[self.ldt_step - 1][0]

  def _abort(self) -> None:
    self.ldt_step = LANE_TEST_IDLE
    self._ldt_frames_left = 0
    self.ldt_seconds_remaining = 0.0

  def _publish_to(self, CS) -> None:
    """Hand this cycle's state to CarState, which owns the message. Mirrors BlinkerTestExt."""
    CS.ldt_step = self.ldt_step
    CS.ldt_blocked = self.ldt_blocked
    CS.ldt_seconds_remaining = self.ldt_seconds_remaining
