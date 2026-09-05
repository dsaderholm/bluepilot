"""FusionPilot: "Take Control -- Turn Exceeds Steering Limit" only when the car is actually wide.

*"Those steering exhausted warnings drive me crazy."* -- 2026-09-05. Reconstructing selfdrived's own
alert condition at 100 Hz across every route on disk (`tools/bp_steer_saturated.py`, 701 segments,
24 routes, 2026-08-31 to 2026-09-05) gives 61 episodes over 3446 alerting frames:

                          n         p50      p90     p99     max
    while alerting       2998     0.24 m    0.67    1.43    1.67
    normal driving    2403770     0.06 m    0.22    0.70    1.83

    61 episodes, worst lane offset in each:  never over 0.30 m -- 26 (43%)
                                             ever over 0.50 m  -- 24 (39%)

**When the alert fires the car is typically 24 cm off center in a 3.7 m lane, against 6 cm the rest
of the time.** It repeats its chime for two seconds every time, 61 times across these drives, and
more than half of those are for a corner it is taking correctly. The cost of leaving it is not
annoyance: episodes in the same population reached 1.43 m and 1.67 m -- nearly half a lane -- and
they arrive in the same costume as the twenty-six that never left it. He is already trained to
ignore the whole class.

**At 0.50 m that is 61 alerts down to 24**, and every episode that ever went half a meter wide is
kept. The full sweep across thresholds is in the tool's `--sweep` output; do not move the number
without re-running it.

AN EARLIER, SMALLER MEASUREMENT SAID 88% NOISE AND IT IS SUPERSEDED. That figure came from 33
episodes on one pull, counted as "never exceeded 0.30 m", and it ignored what the gate does with an
episode it cannot measure. On the full sample the honest number is 61% silenced, not 88%.

HIS OWN RULE, recorded in `mapd_v2_path.py` long before this: *"I just ignore most steering saturated
errors until it starts to stray enough from my lane"*, i.e. **SATURATION IS NOT THE FAILURE. RUNNING
WIDE IS.** That principle was used to tune SCC's corner speed and never applied to the alert, which
is where he actually experiences it.

WHAT THIS DOES NOT DO. It changes nothing the car does -- not a gain, not a limit, not a command.
`lac.saturated` still reaches every consumer it reached before; the only thing gated is whether the
event reaches `selfdrived.events`, i.e. what he is TOLD.

FAIL OPEN, ALWAYS. Every path that cannot MEASURE the lane returns True and shows the alert: modelV2
invalid, fewer than four lane lines, either inner line below `MIN_LANE_PROB`, an empty or non-finite
`y`. That is not caution for its own sake -- it is this fork's standing rule that evidence which
OPENS something must never be cheaper than evidence that refuses it. Here the permissive direction
is staying QUIET, so silence is the one that has to be expensive.

**IT IS NOT A RARE PATH: 14 of the 61 episodes are unmeasurable and every one of them still
alerts.** Most are 15-40 mph, which is intersections and unmarked surface streets -- exactly where
the model has no lane lines to give and where he is least likely to want a chime. That is the
remaining noise in this feature, it is known, and it stays: the only way to shrink it is to judge
position from something other than lane lines, and the obvious candidate (`roadEdges`) measures past
the shoulder and has already caused one bug here.

THE LATCH IS WHY IT DOES NOT FLICKER. Once an episode has earned the alert it keeps it until the
saturation ENDS, so a car sitting on the threshold cannot chatter the chime, and an episode that
starts centered and then runs wide alerts from the moment it goes wide onward. The episode is over
when `update()` has not been called for `RESET_FRAMES` -- the caller only calls while the alert would
otherwise fire, so a gap in the calls IS the end of the episode.

NO NEW WIRE FIELD, DELIBERATELY, AND THIS IS NOT THE "COMPUTED AND NEVER RENDERED" BUG. Everything
the gate reads is already in the route -- `modelV2.laneLines` and `laneLineProbs` for the deviation,
`controlsState` and `carState` for the trigger -- and `tools/bp_steer_saturated.py` IMPORTS
`lane_deviation` from this module rather than re-implementing it. So a drive explains every
suppression exactly, on the shipped arithmetic, with nothing added to the wire.
"""
import math

# Lane half-width is ~1.85 m on the roads he drives, so 0.50 m is about a quarter of the lane, and
# it is the p90 of NORMAL driving more than doubled (0.22 m). Across 701 segments it shows 24 of 61
# episodes; the surrounding thresholds show 29 (0.40) and 22 (0.60), so the curve is flat here and
# the exact value is not delicate. Scored by `tools/bp_steer_saturated.py --sweep`, which prints the
# surviving count at a range of thresholds -- re-run it before moving this number. A threshold
# argued from memory is the guessed-bound failure this repo already paid for once
# (`MAPD_V2_STALL_S`, where an invented 60 s would have bounced a healthy mapd on two of three
# drives, and the test asserted the invented window rather than a measurement).
LANE_DEVIATION_M = 0.50

# Both inner lane lines have to be believable before their midpoint means anything. Same floor
# `tools/bp_lateral_weave.py` uses, so "the lane is measurable" is one number in two places rather
# than two numbers that can drift apart.
MIN_LANE_PROB = 0.30

# selfdrived runs at 100 Hz, so this is 1 s of silence. Far longer than the call cadence within an
# episode and far shorter than the gap between episodes (the closest pair on the 2026-09-04/05 pull
# were seconds apart). Frames rather than seconds so the gate needs no clock and no import.
RESET_FRAMES = 100


def lane_deviation(lane_lines, lane_line_probs, min_prob: float = MIN_LANE_PROB):
  """Meters from the center of the measured lane, or None when the lane is not measurable.

  `laneLines[1]` and `[2]` are the lines either side of the car and `y[0]` is each one's lateral
  offset at the car's own position, so their midpoint is where the lane center sits relative to us.
  The sign is dropped: how far off center is the question, not which way.

  ROAD EDGES ARE DELIBERATELY NOT A FALLBACK. `roadEdges` measures past the shoulder -- this fork
  has already shipped one bug from reading it as a lane boundary -- so a missing lane line means
  "unmeasurable", which fails open, rather than a worse estimate that would fail quiet.
  """
  try:
    if len(lane_lines) < 4 or len(lane_line_probs) < 4:
      return None
    if lane_line_probs[1] < min_prob or lane_line_probs[2] < min_prob:
      return None
    left, right = lane_lines[1].y, lane_lines[2].y
    if len(left) == 0 or len(right) == 0:
      return None
    center = (float(left[0]) + float(right[0])) / 2.0
  except (AttributeError, IndexError, TypeError, ValueError):
    return None
  if not math.isfinite(center):
    return None
  return abs(center)


class SteerSaturatedGate:
  """Decides whether a saturation event is worth telling him about. Never decides anything else."""

  def __init__(self, deviation_m: float = LANE_DEVIATION_M):
    self.deviation_m = deviation_m
    self.enabled = True
    self._latched = False
    self._last_call_frame = -10 ** 9
    # What the last decision was made on. Not published -- see the module docstring -- but read by
    # the tests, and the obvious thing to reach for if this ever needs a log line.
    self.last_deviation = -1.0        # -1 means "could not measure", never 0.0
    self.last_suppressed = False

  def read_params(self, params):
    """Called from selfdrived's params thread, so the toggle takes effect without a restart."""
    try:
      self.enabled = params.get_bool("SteerAlertLaneGate")
    except Exception:
      # A params store mid-write must not silence the alert. Same shape as the publisher guard:
      # degrade to upstream behavior, never to a quieter car.
      self.enabled = False

  def reset(self):
    self._latched = False
    self._last_call_frame = -10 ** 9
    self.last_deviation = -1.0
    self.last_suppressed = False

  def update(self, lane_lines, lane_line_probs, model_valid: bool, frame: int) -> bool:
    """True when the alert should be shown. Call ONLY on frames the alert would otherwise fire."""
    if frame - self._last_call_frame > RESET_FRAMES:
      self._latched = False
    self._last_call_frame = frame

    if not self.enabled:
      self.last_deviation = -1.0
      self.last_suppressed = False
      self._latched = True
      return True

    deviation = lane_deviation(lane_lines, lane_line_probs) if model_valid else None
    self.last_deviation = -1.0 if deviation is None else deviation

    if deviation is None or deviation >= self.deviation_m:
      self._latched = True

    self.last_suppressed = not self._latched
    return self._latched

  def should_alert(self, sm) -> bool:
    """SubMaster adapter for selfdrived. Keeps the arithmetic above free of cereal entirely, which
    is what lets `tools/bp_steer_saturated.py` import it -- an rlog tool has already called
    `capnp.load()`, and a second schema load aborts the interpreter."""
    try:
      model = sm['modelV2']
      valid = bool(sm.valid['modelV2']) and bool(sm.alive['modelV2'])
      return self.update(model.laneLines, model.laneLineProbs, valid, sm.frame)
    except Exception:
      # Anything unexpected about the message shows the alert. This runs inside selfdrived, so it
      # must also never raise: an exception here would take out the process that publishes every
      # alert, to suppress one of them.
      return True
