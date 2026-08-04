"""
BluePilot: how long a pass is actually taking, measured.

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
# Includes gaining at zero or negative, which is the worst version -- matching their speed exactly,
# or losing ground, while sitting in the passing lane.
SLOW_GAIN_MPH = 5

# Below this a "pass" is not the manoeuvre in question. Same floor the detector uses.
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
    return -side.v_rel < min_gain_ms

  def update(self, v_ego: float, left, right, settle_s: float) -> None:
    if v_ego < MIN_V_EGO_MS:
      self._reset()
      return

    min_gain = SLOW_GAIN_MPH * CV.MPH_TO_MS
    left_grinding = self._grinding(left, min_gain)
    right_grinding = self._grinding(right, min_gain)

    if not (left_grinding or right_grinding):
      self._reset()
      return

    if self.crawl_seconds == 0.0:
      # Latched at the START of the crawl. Deciding this per-frame would let a long crawl change
      # its own provenance as the settle timer ran out underneath it.
      self.crawl_after_suggestion = settle_s < AFTER_SUGGESTION_S
      self.crawl_side = Side.left if left_grinding else Side.right

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
