"""
BluePilot: rear-approach input for passing assist. NO SOURCE FITTED YET.

Answers one question per side: is something coming up behind me in that lane, and how fast.

Nothing produces this today. Every side reports `available=False`, which the decision chain treats
as "unknown", NOT as "clear" -- that distinction is the entire reason this module exists rather
than a couple of extra fields on the detector. carState.leftBlindspot already defaults False when
no sensor is fitted, so an absent sensor and an empty lane are indistinguishable downstream unless
availability is carried explicitly and separately.

WHY BUILD THE CONSUMER BEFORE THE PRODUCER
The gate has to sit in a specific place in the decision order (after geometry, before the
suggestion), and its failure mode has to be decided up front: unavailable must never read as safe.
Retrofitting that into a working state machine later is how ordering bugs get in -- the keep-right
path in this same file was written second and needed an explicit reset to stop it firing
mid-overtake. Defining the interface now means fitting a sensor is a swap, not a redesign, and the
phase-1 logs carry the slot from the start so recorded drives can be replayed against it.

WHY THE RADAR SHAPE, NOT THE BLIS SHAPE
Two sources are plausible and they carry different amounts of information:

  radar  per-object range, range-rate, angle. ESR.dbc is the reference: CAN_TX_TRACK_RANGE,
         CAN_TX_TRACK_RANGE_RATE, CAN_TX_TRACK_ANGLE, 64 targets.
  BLIS   Side_Detect_L/R_Stat categories. Presence only -- researched 2026-08-02 and settled:
         Sod*_D_Stat is the system's enable state, SodAlrt*_D_Stat is the mirror lamp whose Flash
         state follows the DRIVER's turn signal, and SodSns*_D_Stat is sensor health. Ford BLIS
         answers "is that side occupied" and cannot answer "is something closing on it".

Modelled on radar because a BLIS source adapts UP into these fields losing nothing (categories set
detected/closing, ttc stays unset), while a BLIS-shaped interface would throw away the range and
rate a radar provides -- which are precisely the numbers a lane-change decision needs. Designing
for the weaker source would have to be undone the moment the stronger one is fitted.

Given what BLIS turned out to be, the radar is not a fallback for it -- it is the only source that
can fill these fields properly. BLIS remains worth wiring as a veto: a lane known to be occupied
right now is still a lane not to move into.
"""

from cereal import custom

Source = custom.LongitudinalPlanSP.PassingAssist.Source

# Closing faster than this counts as gaining on us rather than sensor noise or a lane-speed
# difference we would out-accelerate anyway.
MIN_CLOSING_MS = 1.5
# Below this time-to-contact the lane is not usable. Deliberately generous: a lane change plus the
# time to reach the other car's speed is several seconds, and being wrong here puts us in front of
# someone who cannot stop.
UNSAFE_TTC_S = 8.0
# Returned when nothing is closing, so callers can compare numerically without special-casing.
NO_THREAT_TTC_S = 999.0


class RearApproachSide:
  """One side's answer. Defaults to unavailable, which is not the same as clear."""

  def __init__(self):
    self.available = False
    self.detected = False
    self.closing = False
    self.d_rel = 0.0
    self.v_rel = 0.0
    self.ttc = NO_THREAT_TTC_S
    self.source = Source.none

  def reset(self) -> None:
    self.__init__()

  @property
  def blocks_lane_change(self) -> bool:
    """Would moving into this lane put us in front of someone?

    Returns False when unavailable -- the caller must check `available` separately and decide what
    an unknown means. That is deliberate: a property that returned True for "no sensor" would
    silently disable passing on a car with no rear radar, and one that returned False would be a
    lie. Neither belongs here; the policy lives at the call site where it is visible.
    """
    if not self.available:
      return False
    return self.detected and self.closing and self.ttc < UNSAFE_TTC_S

  def from_radar(self, d_rel: float, v_rel: float) -> None:
    """Fill from a rear-facing radar target. v_rel positive = closing on us.

    Sign convention is stated rather than inherited because ESR.dbc and ford_fusion_2018_adas.dbc
    disagree on the sign of ANGLE (+0.1 vs -0.1 scale) for the same hardware, so nothing about a
    Delphi sign convention should be assumed without checking against real frames.
    """
    self.available = True
    self.source = Source.radar
    self.detected = True
    self.d_rel = float(d_rel)
    self.v_rel = float(v_rel)
    self.closing = v_rel >= MIN_CLOSING_MS
    self.ttc = (d_rel / v_rel) if v_rel >= MIN_CLOSING_MS else NO_THREAT_TTC_S

  def from_blis(self, detected: bool, closing: bool | None = None) -> None:
    """Fill from Ford BLIS categories.

    ttc stays unset because BLIS has no range: `blocks_lane_change` therefore cannot fire on the
    TTC test, and a BLIS source can only ever veto on presence. That limitation is the point of
    recording `source` -- a log full of BLIS-sourced decisions must not be read as though a radar
    had cleared them.

    closing=None means the signal does not distinguish, which is the expected case unless the raw
    sodStat/sodAlert logging turns up something. Treated as closing, because with no way to tell,
    the conservative reading of "something is beside me" is that it matters.
    """
    self.available = True
    self.source = Source.blis
    self.detected = bool(detected)
    self.closing = bool(detected) if closing is None else bool(closing)
    self.d_rel = 0.0
    self.v_rel = 0.0
    self.ttc = 0.0 if self.closing and self.detected else NO_THREAT_TTC_S


class RearApproach:
  """Both sides, plus the adapter that will eventually be given a source."""

  def __init__(self):
    self.left = RearApproachSide()
    self.right = RearApproachSide()

  @property
  def available(self) -> bool:
    return self.left.available or self.right.available

  def update(self, sm) -> None:
    """Populate from whatever rear sensing exists.

    Today: nothing does, so both sides reset to unavailable every cycle. When a source is fitted
    this is the only function that changes -- it calls from_radar() or from_blis() per side and
    everything downstream, including the gate ordering and the display, already works.
    """
    self.left.reset()
    self.right.reset()
