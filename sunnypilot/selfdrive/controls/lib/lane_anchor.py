"""Which lane are we in? Anchor on the RIGHT road edge, count leftward with the map's lane count.

HIS IDEA, 2026-08-19: *"Can't we just know which lane is near the road edge and then establish that
as the farthest right lane and then keep track from there using lane count from OSM?"*

WHY THIS IS THE ONLY REMAINING ROUTE. Three candidates were measured and two are dead:

    distanceFromWayCenter   24.2% of motorway frames PHYSICALLY IMPOSSIBLE (|d| > roadWidth/2)
    LEFT road edge          trusted 0.0% of motorway frames -- never once, in 3,060 frames
    RIGHT road edge         trusted 15.3% (4 lanes) / 5.3% (5 lanes), p50 4.6-4.8 m,
                            and 99% of those readings sit within 1.5 lane widths

The right edge is a SHOULDER, not a median, which is why it survives where the left does not. It is
intermittent, so this is a LATCH, not a per-frame gate: take a reading only when the model is
confident, convert it to a lane index, and hold that index until something invalidates it.

WHAT IT IS FOR. Passing assist needs "is there a same-direction lane to my left" and "am I in the
far left lane". `lanes` from the map gives the total; this gives our position within it; the
subtraction gives both answers. It is also the missing half of the keep-right and slow-pass logic.

THE DIRECTION OF ITS ERROR IS THE WHOLE DESIGN. This may only ever REFUSE a pass, never open one --
the map-is-evidence rule, applied to a derived quantity that inherits every weakness of its inputs.
So every ambiguous case resolves to `None` (unknown), and a `None` must leave the caller exactly as
it was before this existed. An anchor that says "you are in lane 3 of 5" wrongly, in the permissive
direction, is worse than no anchor at all.
"""
from openpilot.common.constants import CV

# US Interstate standard is 12 ft. Ford's own lane-width estimates and mapd's estimatedRoadWidth
# both sit near this, and the whole scheme is a division by it, so it is a CAR/ROAD FACT with no
# param: changing it would not express a preference, it would express a different country.
LANE_WIDTH_M = 3.7

# How far the right edge may sit from us before "which lane" stops being answerable. Five lane
# widths is 18.5 m, past which the reading is more likely a barrier across a gore or a frontage
# road than our own shoulder. Deliberately generous: the trust gate below does most of the work.
MAX_EDGE_M = 5.0 * LANE_WIDTH_M

# The model's own std on the road edge, matching adjacent_lane.MAX_ROAD_EDGE_STD. Reused rather
# than redefined so one number governs "do we believe this edge" everywhere.
MAX_EDGE_STD = 0.5

# THE SECOND WITNESS, added 2026-08-19 after replaying the edge-only anchor against a real drive.
#
# The right edge is trusted only when it is CLOSE, so an anchor built on it alone can see which
# lane we are in exactly while we are on the right and goes blind as we move left. Measured on
# route 0000038e: over 22,547 moving freeway frames it claimed lane 0 or lane 1 and NEVER lane 2 or
# beyond. So `in_leftmost_lane()` was structurally unreachable on a wide road -- which is the one
# road where the question is asked.
#
# `laneLineProbs[0]` is the outer LEFT lane line: the model asserting a line BEYOND our own left
# boundary, which means a lane beyond it. Its ABSENCE is therefore direct evidence of being in the
# leftmost lane, and it is published every frame rather than 5-15% of them.
#
# Measured before trusting it (bp_lane_line_count.py, route 00000383): on a single-lane ramp --
# the road with genuinely no lane to the left -- it read p50 0.03 with 0% of frames above 0.5. Zero
# false "there is a lane there" in 463 frames. On multi-lane motorway it ran 0.54-0.77. It
# discriminates, and its errors are not in the direction that would invent a left lane.
#
# Used only to say LEFTMOST, never to place us in a numbered lane: it is a boolean about the
# immediate neighbour, not a position.
NO_LEFT_LINE_PROB = 0.25

# A latched anchor is dead reckoning, and dead reckoning drifts. Hold it only this long without a
# fresh confident reading. 20 s at 70 mph is 625 m, which is a realistic distance to hold a lane;
# past that an unobserved lane change is likelier than not.
MAX_LATCH_S = 20.0

# Any of these means the latched index can no longer be trusted and must be dropped rather than
# carried: we changed lanes, the road changed under us, or the map stopped agreeing about width.
# Listing them here rather than inline keeps "what invalidates the anchor" answerable in one place.


def lane_index_from_edge(edge_dist_m, lanes_total):
  """Which lane are we in, counting 0 = FAR RIGHT, given metres to the right road edge?

  Returns None when the answer is not determinable, which the caller must treat as "unknown" and
  never as "lane 0".

  The arithmetic: sitting centred in the rightmost lane puts the right edge half a lane away, so
  index = round((edge - half a lane) / a lane). A car 1.85 m from the edge is index 0; 5.55 m is
  index 1; 9.25 m is index 2.
  """
  if edge_dist_m is None or lanes_total is None:
    return None
  try:
    d = abs(float(edge_dist_m))
    n = int(lanes_total)
  except (TypeError, ValueError):
    return None
  if n <= 0:
    return None                      # the map has no lane count -- 0 is "unknown", never one lane
  if d > MAX_EDGE_M:
    return None                      # too far to be our shoulder
  idx = int(round((d - LANE_WIDTH_M / 2.0) / LANE_WIDTH_M))
  if idx < 0:
    idx = 0                          # slightly inside the rightmost lane centre still means lane 0
  if idx > n - 1:
    return None                      # the reading disagrees with the map; refuse rather than clamp
  return idx


def lanes_to_our_left(lane_index, lanes_total):
  """How many same-direction lanes sit to our left. None propagates."""
  if lane_index is None or lanes_total is None:
    return None
  try:
    n = int(lanes_total)
  except (TypeError, ValueError):
    return None
  if n <= 0 or lane_index < 0 or lane_index > n - 1:
    return None
  return n - 1 - lane_index


def lane_bounds_from_lines(far_left_prob, far_right_prob, lanes_total):
  """Bound our lane index from the OUTER two lane lines. Returns (lo, hi) or None.

  THE MIDDLE-LANE FIX, 2026-08-19. He watched the strip go blank whenever he moved to a middle
  lane, and that was honest rather than broken: the right EDGE is out of reach from there, and the
  far-left LINE is present because there really is a lane to the left, so both prior witnesses fall
  silent at once and every box empties.

  modelV2 publishes FOUR lines, and only the outer left was being read. The other three carry the
  rest of the answer:

      far-left ABSENT    nothing beyond our left boundary   -> we are the LEFTMOST lane
      far-right ABSENT   nothing beyond our right boundary  -> we are the RIGHTMOST lane
      both PRESENT       a lane each side                   -> strictly between, 1..n-2

  On a THREE-lane road "strictly between" is a single value, so the middle lane becomes exactly
  determined -- which is the case that was blank. On four or more it narrows rather than pins, and
  a range is still worth having: `lanes_to_our_left` only needs to know we are not at either end.

  BOTH ABSENT IS A CONTRADICTION on any multi-lane road -- we cannot be leftmost and rightmost at
  once -- so it returns None rather than picking one. That is the case a single-lane road produces
  legitimately, and `lanes_total == 1` is handled before we get here.

  Index ordering is verified rather than assumed: measured medians are idx0 -5.1 m, idx1 -1.7,
  idx2 +1.8, idx3 +5.1, and negative is left, matching adjacent_lane's own `lat < 0 is left`.
  """
  if lanes_total is None:
    return None
  try:
    n = int(lanes_total)
    fl = None if far_left_prob is None else float(far_left_prob)
    fr = None if far_right_prob is None else float(far_right_prob)
  except (TypeError, ValueError):
    return None
  if n <= 1 or fl is None or fr is None:
    return None

  left_open = fl < NO_LEFT_LINE_PROB       # no lane to our left
  right_open = fr < NO_LEFT_LINE_PROB      # no lane to our right

  if left_open and right_open:
    return None                            # contradiction on a multi-lane road; claim nothing
  if left_open:
    return (n - 1, n - 1)
  if right_open:
    return (0, 0)
  if n < 3:
    return None                            # two lanes with a line each side is not consistent
  return (1, n - 2)                        # strictly between; exact when n == 3


class LaneAnchor:
  """Latches a lane index from intermittent right-edge readings and holds it between them.

  The right edge is trusted on 5-15% of motorway frames, so a per-frame gate built on it would be
  unavailable most of the time. Latching converts an intermittent measurement into a continuous
  estimate -- at the cost of being wrong if the car changes lanes unobserved, which is exactly what
  `note_lane_change` and the staleness bound exist to bound.
  """

  def __init__(self):
    self.index = None            # 0 = far right
    self.lanes_total = None
    self.age_s = 0.0
    self.confident = False       # True only on a frame that took a fresh reading
    self.no_lane_left = False    # the lane-line witness; see in_leftmost_lane
    self.line_bounds = None      # (lo, hi) from the four lines, or None
    self.edge_index = None       # this frame's edge-derived index alone, before any fallback
    # THE TWO WITNESSES DISAGREEING. Measured, not acted on -- see update().
    self.contradiction = False

  def invalidate(self, reason=""):
    self.index = None
    self.lanes_total = None
    self.age_s = 0.0
    self.confident = False
    self.reason = reason

  def note_lane_change(self):
    """The driver or the system moved us. The latched index is now meaningless.

    Deliberately does NOT try to increment or decrement the index to follow the move. That would be
    dead reckoning on top of dead reckoning, and a missed or aborted change would leave a confident
    wrong answer -- the one failure mode this whole module is shaped to avoid.
    """
    self.invalidate("lane change")

  def update(self, dt, edge_dist_m, edge_std, lanes_total, one_way, far_left_line_prob=None,
             far_right_line_prob=None):
    """Advance one frame. Returns the current lane index, or None if unknown.

    `one_way` is required and must be True: on a two-way road the map's `lanes` is the total for
    BOTH directions, so counting leftward from the shoulder would walk straight into the oncoming
    lane and report it as ours. That is the single most dangerous way this could be wrong, so it is
    refused at the top rather than handled downstream.
    """
    self.confident = False
    # Second witness, independent of the edge and available every frame. Kept as its own field
    # rather than folded into the index, because it answers "is anything to my left" and NOT
    # "which lane am I in" -- conflating those is how a boolean becomes a fake position.
    self.no_lane_left = False
    if far_left_line_prob is not None and one_way:
      try:
        self.no_lane_left = float(far_left_line_prob) < NO_LEFT_LINE_PROB
      except (TypeError, ValueError):
        self.no_lane_left = False

    # FOUR-LINE BOUND. Independent of the edge and available every frame, and unlike the single
    # outer-left witness it speaks in the middle lanes. Only an EXACT bound (lo == hi) becomes an
    # index; a range is kept for to_our_left, which does not need the precise lane.
    self.line_bounds = lane_bounds_from_lines(far_left_line_prob, far_right_line_prob, lanes_total)

    if not one_way:
      self.invalidate("not one-way")
      return None

    fresh = None
    if edge_std is not None and lanes_total:
      try:
        if float(edge_std) <= MAX_EDGE_STD:
          fresh = lane_index_from_edge(edge_dist_m, lanes_total)
      except (TypeError, ValueError):
        fresh = None

    self.edge_index = fresh

    # DO THE TWO INDEPENDENT WITNESSES AGREE? The edge counts leftward from the shoulder; the lines
    # bound us between what the model can see either side. They measure the same quantity by
    # completely different means, so a disagreement means one of them is wrong -- and a confidently
    # wrong index is the exact failure this module is shaped around.
    #
    # NOT ACTED ON. Refusing here would be the conservative choice and it may well be right, but it
    # would also throw away good edge readings if the outer lines are merely noisy, and nobody has
    # measured which. bp_anchor_replay counts it against a recorded drive; that number decides.
    # Adding the refusal first and measuring afterwards would be a change nobody could attribute.
    self.contradiction = (fresh is not None and self.line_bounds is not None
                          and not self.line_bounds[0] <= fresh <= self.line_bounds[1])

    if fresh is None and self.line_bounds is not None and self.line_bounds[0] == self.line_bounds[1]:
      # The lines pinned it exactly. Counted as a FRESH reading because it is a measurement this
      # frame, not a latch -- but it never overrides an edge reading, which is the better evidence.
      fresh = self.line_bounds[0]

    if fresh is not None:
      # A fresh reading always wins over a latched one, including when it disagrees.
      self.index = fresh
      self.lanes_total = int(lanes_total)
      self.age_s = 0.0
      self.confident = True
      return self.index

    if self.index is None:
      return None

    # No fresh reading: carry the latch, but only for so long, and only while the map still agrees
    # about how many lanes there are. A changed lane count means a different road cross-section,
    # so an index counted against the old one no longer refers to the same thing.
    self.age_s += max(0.0, float(dt))
    if self.age_s > MAX_LATCH_S:
      self.invalidate("stale")
      return None
    if lanes_total and int(lanes_total) != self.lanes_total:
      self.invalidate("lane count changed")
      return None
    return self.index

  def to_our_left(self):
    """Same-direction lanes to our left, or None. This is what a gate actually asks."""
    return lanes_to_our_left(self.index, self.lanes_total)

  def in_leftmost_lane(self):
    """True only when we KNOW we are in the far left lane. None-safe and refuses on unknown.

    Returns False for "unknown", not None, because every caller is asking "may I be told off for
    hogging the left lane" and the answer on no information must be no.

    TWO INDEPENDENT WITNESSES, either of which suffices:

      the anchor      lane index equals the map's last lane. Precise, and structurally unavailable
                      on a wide road -- the very case this is asked about.
      the lane line   no outer-left line, so no lane beyond our own left boundary. Available every
                      frame, and the only one that works once we are actually over there.

    OR rather than AND is deliberate and it is the safe direction HERE, which is the opposite of
    the usual rule and worth stating. This gate does not open a maneuver: its only consumer is the
    slow-pass warning, where True means "say something" and False means "stay quiet". Requiring
    both would return the feature to silence on wide freeways, which is the state the edge-only
    version was measured to produce. If a future caller ever uses this to permit a lane change,
    that caller must require `self.index is not None` as well -- a warning may be wrong, a
    maneuver may not.
    """
    if self.to_our_left() == 0:
      return True
    if self.line_bounds is not None and self.lanes_total:
      lo, hi = self.line_bounds
      if lo == hi == self.lanes_total - 1:
        return True
    return bool(getattr(self, "no_lane_left", False))


def mph(v_ms):
  return v_ms * CV.MS_TO_MPH
