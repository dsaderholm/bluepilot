"""
FusionPilot: how long a pass is actually taking, measured.

The one circumstance in which passing assist is ever allowed to touch the set speed is this one:
crawling alongside a car you are barely faster than. The owner, sharpening it: "the only time I
want passing assist to touch the speed using something like ICBM is when it is taking too long to
pass."

Nothing here actuates. It measures the condition so the boost can be built on a number from real
roads rather than a number I picked -- how often a pass actually grinds, and for how long. Those
two figures are what should set the trigger and the size of the nudge, and neither is guessable.

WHAT CAN AND CANNOT BE SEEN FROM A FORWARD RADAR
"Still alongside after N seconds" is the natural way to say it and the wrong way to measure it. The
MRR looks forward; a vehicle exactly abeam is outside its field of view, so the moment a pass gets
genuinely stuck the sensor stops reporting the car that is stuck beside you.

What it CAN see is the run-up to that: a vehicle in the adjacent lane, close, that we are gaining
on far too slowly. That is the same situation a few seconds earlier, while it is still fixable --
which is also when a speed nudge would have to arrive to be any use. Measuring the visible half is
not a compromise here, it is the better half.

WHY IT DOES NOT TRY TO KNOW WHETHER WE MEANT TO PASS
It could infer an overtake from the driver's blinker, the lead vanishing and a vehicle appearing in
the adjacent band. Every one of those steps is a guess, and a chain of three guesses reported as
one fact is how a log stops being evidence. So it measures the CONDITION, and separately records
whether it began shortly after this system suggested a pass. If crawls turn out to cluster after
suggestions, that is a finding. If they do not, that is a more interesting one.
"""

from cereal import custom
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_MDL

Side = custom.LongitudinalPlanSP.PassingAssist.Side

# Inside this range we are in the ACT of passing rather than approaching. Beyond it a slow closing
# rate is just a car in the next lane somewhere ahead, which is not a stuck overtake.
CLOSE_M = 45.0
# Below this a radar return that close is more likely clutter than a vehicle.
MIN_D_REL_M = 3.0
# Gaining on them slower than this is a grind. ~5 mph: at that rate, moving from 45 m behind to 45 m
# ahead of a lorry takes the better part of half a minute, spent in the other lane.
#
# Includes gaining at zero, which is the worst version -- matching their speed exactly while sitting
# in the passing lane.
SLOW_GAIN_MPH = 5

# ...but NOT while they pull away from you, which is not a slow pass at all.
#
# From the road: "I kept getting slow pass warnings saying barely gaining on the car on the left,
# but obviously I wouldn't be gaining on the car on the left because the cars on the left are going
# faster."
#
# The test was `-v_rel < min_gain`, and -v_rel is how much faster we are. A car doing 10 mph more
# than us satisfies that easily -- it is very much less than five. So every vehicle overtaking US
# counted as a vehicle we were failing to overtake, which on a highway is most of them.
#
# A small negative band survives because a genuinely stuck pass does drift: alongside a lorry that
# creeps ahead by a mile an hour is exactly the case worth naming. Losing more than that means they
# are passing you, and there is nothing to be slow at.
SLOW_LOSS_MPH = 2

# Below this a "pass" is not the maneuver in question. Same floor the detector uses.
MIN_V_EGO_MS = 40 * CV.MPH_TO_MS

# A crawl starting within this long of our own suggestion is recorded as following from it. Loose
# on purpose: it is a label on the data, not a gate, and a tight window would quietly drop the
# slow-developing cases that matter most.
AFTER_SUGGESTION_S = 30.0

# How long before it counts as an event worth naming. The number the boost trigger should
# eventually be fitted to, which is exactly why it is a setting rather than a constant.
DEFAULT_CRAWL_TIME_S = 8


class OvertakeProgress:
  """Fed once per frame from the detector. Measures, actuates nothing."""

  def __init__(self):
    self.crawl_seconds = 0.0        # current continuous crawl
    self.crawl_longest = 0.0        # worst this drive
    self.crawl_events = 0           # crawls that passed the threshold
    self.crawl_side = Side.none
    self.crawl_after_suggestion = False
    self.crawl_time_s = float(DEFAULT_CRAWL_TIME_S)
    self._counted = False

  @property
  def crawling(self) -> bool:
    """Past the threshold: this is the state a speed nudge would be for."""
    return self.crawl_seconds >= self.crawl_time_s

  @staticmethod
  def _grinding(side, min_gain_ms: float) -> bool:
    """A vehicle in this lane, close, that we are barely getting past.

    v_rel is theirs relative to ours, so gaining on them is a NEGATIVE v_rel. Gaining at zero or
    less counts too -- matching their speed in the passing lane is the worst case, not an exempt
    one.
    """
    if not (side.available and side.occupied):
      return False
    if not (MIN_D_REL_M < side.d_rel < CLOSE_M):
      return False
    # BOTH ENDS. Below the loss floor they are pulling away, which is them passing us.
    gain = -side.v_rel
    return -SLOW_LOSS_MPH * CV.MPH_TO_MS < gain < min_gain_ms

  def update(self, v_ego: float, left, right, settle_s: float,
             since_lane_change_s: float = 1e3, in_leftmost: bool = True) -> None:
    # A CAR BESIDE YOU IS NOT A PASS. Reported from the road: "it's been saying slow pass even
    # though I'm in the far right lane."
    #
    # Exactly right, and the fault is in the name rather than the arithmetic. _grinding asks "is
    # there a vehicle in an adjacent lane, close, that I am not gaining on" -- which in the right
    # lane of a highway, with traffic in the left lane running about your speed, is true almost
    # continuously. It measured being ALONGSIDE someone and called it overtaking them.
    #
    # A crawl only means something in the context of a pass that was wanted. `settle_s` is the time
    # since the last suggestion and was already computed here to LABEL a crawl; it should have been
    # gating one. Outside that window there is no pass underway, so there is nothing to be slow at.
    #
    # EITHER signal will do, and it needs both. Gating on the suggestion alone would stop measuring
    # the passes he makes himself -- which are most of them, and the ones the crawl number was
    # wanted for. A lane change he just made is the other evidence that a pass is underway, and it
    # is the only one available while this system suggests nothing at all.
    #
    # What is still excluded, correctly, is sitting in a lane doing neither.
    if v_ego < MIN_V_EGO_MS:
      self._reset()
      return

    # THE RIGHT SIDE ONLY, and this is his correction rather than a refinement of mine:
    #
    #   "A slow pass would only matter if I'm passing on the left. If I'm passing on the right, I
    #   should just stay in the right lane. There's no eagerness to get out."
    #
    # Passing on the left puts the car you are passing on your RIGHT. That is the whole of it. A
    # vehicle on the LEFT is either overtaking you or you are in the wrong lane, and neither is a
    # pass you are being slow at -- so watching that side produced a warning about traffic doing
    # nothing but going past, continuously, on every highway.
    #
    # Undertaking on the right is deliberately silent. There is no lane to hurry back to.
    # AND ONLY FROM THE LEFTMOST LANE, which is his second correction on the same idea:
    #
    #   "The slow pass thing should only apply if I'm in the far left lane."
    #
    # The harm in a slow pass is not the slowness, it is WHERE it happens. Grinding past someone
    # from the middle lane of a four-lane road blocks nobody -- the passing lane is still free and
    # anyone in a hurry goes around. Doing it from the far left is the thing he does not want to be:
    # "no one should ever have to be stuck behind me". Same principle as the lane hog counter, which
    # is why it uses the same term rather than a second definition of leftmost.
    #
    # THE WEAKNESS, stated because it is real: "no lane to our left" is read from the camera, and
    # this drive it could not see the left lane line at all -- the paint term refused 73 % of the
    # time at a probability of 0.011. So a middle lane whose left neighbour is invisible reads as
    # leftmost, and a crawl there would still be counted. It errs toward counting, which for a
    # measurement is the right direction, but it is not a clean test and no clean one exists today.
    min_gain = SLOW_GAIN_MPH * CV.MPH_TO_MS
    right_grinding = self._grinding(right, min_gain)

    if not (right_grinding and in_leftmost):
      self._reset()
      return

    if self.crawl_seconds == 0.0:
      # AT THE START ONLY, for both the gate and the label, and for the same reason: deciding
      # either per frame would let a long crawl change its own provenance as the timer ran out
      # underneath it. A 40 s grind that began right after a suggestion is one event, not thirty
      # seconds of event followed by ten seconds of something else.
      if settle_s >= AFTER_SUGGESTION_S and since_lane_change_s >= AFTER_SUGGESTION_S:
        return
      self.crawl_after_suggestion = settle_s < AFTER_SUGGESTION_S
      # Always the car on the right -- see above. Kept as a field because the panel and the drive
      # summary both read it, and because a rear radar will eventually make the left side mean
      # something different.
      self.crawl_side = Side.right

    self.crawl_seconds += DT_MDL
    self.crawl_longest = max(self.crawl_longest, self.crawl_seconds)

    # Once per crawl, not once per frame past the threshold.
    if not self._counted and self.crawl_seconds >= self.crawl_time_s:
      self._counted = True
      self.crawl_events += 1

  def _reset(self) -> None:
    self.crawl_seconds = 0.0
    self.crawl_side = Side.none
    self.crawl_after_suggestion = False
    self._counted = False
