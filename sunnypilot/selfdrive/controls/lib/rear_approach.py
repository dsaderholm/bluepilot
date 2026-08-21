"""
FusionPilot: rear-approach input for passing assist. NO SOURCE FITTED YET.

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
# FusionPilot: the threshold that may stop a lane change ALREADY UNDERWAY, as opposed to refusing to
# start one. Deliberately far tighter than UNSAFE_TTC_S: backing out of a crossing is itself a
# maneuver, done half-way between two lanes, and it is only the right answer when continuing is
# genuinely worse. At 8 s the correct response is to keep going and let them settle behind; at 3 s
# they are arriving whatever anyone does.
COLLISION_TTC_S = 3.0
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
  def demands_abort(self) -> bool:
    """Is something arriving fast enough that a crossing already begun should be reversed?

    Same unavailable rule as below, and it matters more here: a property that answered True with no
    sensor would abort every lane change on a car with no rear radar.
    """
    if not self.available:
      return False
    # A BLIS SOURCE MAY NEVER DEMAND ONE. It answers "is something beside me", never "is something
    # arriving", and the distance between those two is the entire question here: at 8 s the right
    # answer is to keep going and let them settle behind, at 3 s they are arriving whatever anyone
    # does, and BLIS cannot tell those apart. Reversing is itself a maneuver, made half-way between
    # two lanes, so it demands a measurement rather than a presumption. Refusing to start on the
    # same evidence still happens -- see blocks_lane_change -- because a refusal costs a pass and
    # this costs a maneuver nobody chose.
    if self.source == Source.blis:
      return False
    return self.detected and self.closing and self.ttc < COLLISION_TTC_S

  @property
  def blocks_lane_change(self) -> bool:
    """Would moving into this lane put us in front of someone?

    Returns False when unavailable -- the caller must check `available` separately and decide what
    an unknown means. That is deliberate: a property that returned True for "no sensor" would
    silently disable passing on a car with no rear radar, and one that returned False would be a
    lie. Neither belongs here; the policy lives at the call site where it is visible.
    """
    if not self.available or not self.detected:
      return False
    # PRESENCE IS THE WHOLE SIGNAL ON BLIS, and it is tested here rather than smuggled in as a
    # fabricated ttc. `from_blis` used to set ttc to 0.0 so this line would fire, which quietly
    # made `demands_abort` true as well -- so a car merely SITTING in the blind spot commanded an
    # emergency reversal of a crossing already begun. Zero was not a measurement; it was the most
    # alarming number available, invented from a sensor that reports no range at all.
    #
    # `closing` is the answer, not `detected`: with no distinguishing signal from_blis sets it from
    # presence, but if sodStat/sodAlert ever turn out to distinguish, an explicit not-closing stops
    # being a veto without this property changing.
    if self.source == Source.blis:
      return self.closing
    return self.closing and self.ttc < UNSAFE_TTC_S

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
    # LEFT UNSET, deliberately. There is no range and no closing rate in this message, so any
    # number here is invented -- and the one that used to be here (0.0) read as "contact now" to
    # every consumer that compares against a threshold.
    self.ttc = NO_THREAT_TTC_S


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

    RESET FIRST, ALWAYS. Every path below either fills a side or leaves it unavailable, and
    unavailable is not clear -- see the note at the top of this file. A missing message, a dead
    feeder, a radar that stopped: all of them must land here as "we cannot see", never as
    "nothing is there".
    """
    self.left.reset()
    self.right.reset()

    # The digest, when a feeder is fitted. Absent on every car that has not had one built, which is
    # why this is a quiet return rather than anything that logs or alerts.
    try:
      if not sm.valid.get("rearRadarBP", False) or not sm.updated.get("rearRadarBP", False):
        self._update_from_blis(sm)
        return
      rr = sm["rearRadarBP"]
    except (KeyError, AttributeError, TypeError):
      self._update_from_blis(sm)
      return

    # dataAvailable is the feeder's own verdict: it is talking AND its radar is alive AND detection
    # frames are still arriving. A feeder that outlived its sensor would otherwise report an empty
    # road indefinitely, which is the one failure this module was written to refuse.
    if not bool(rr.dataAvailable):
      return

    for side, msg in ((self.left, rr.left), (self.right, rr.right)):
      if not bool(msg.detected):
        # SEEN AND EMPTY is a real answer, and a different one from not looking. The side is
        # available with no target, so blocks_lane_change can clear it rather than veto by silence.
        side.available = True
        continue
      side.from_radar(float(msg.dRel), float(msg.vRel))

  def _update_from_blis(self, sm) -> None:
    """Fall back to blind-spot occupancy when no rear radar digest is arriving.

    THE ONLY REASON THIS IS SAFE is that a BLIS source cannot authorize anything. Every consumer of
    a side except one is a REFUSAL -- the pass gate, the abort on a committed crossing, the
    keep-right timer, the blockedBy label. The single consumer that grants permission is
    `may_actuate`, and it requires `source == Source.radar`, so filling a side from BLIS adds
    refusals and no permissions. That asymmetry is the whole design, not a detail of it: BLIS
    answers "is something beside me" and cannot answer "is something closing", which is the
    question a lane change actually asks.

    RADAR WINS WHEN BOTH EXIST. This runs only where the digest is absent or stale, so fitting the
    feeder later silently upgrades every side rather than needing this removed.

    Nothing populates it until the canbox routes 0x3A6/0x3A7 onto a bus openpilot reads, and
    `dataAvailable` stays False until then -- so on his car today this is inert, exactly as the
    radar branch above is.
    """
    try:
      if not sm.valid.get("carStateBP", False):
        return
      bp = sm["carStateBP"]
    except (KeyError, AttributeError, TypeError):
      return

    for side, det in ((self.left, getattr(bp, "blisLeft", None)),
                      (self.right, getattr(bp, "blisRight", None))):
      if det is None or not bool(det.dataAvailable):
        continue
      # `sodDetect` is the occupancy bit. blis_ext.py states that `carState.leftBlindspot` is
      # `SodDetct*_D_Stat != 0` and nothing else -- `SodAlrt*` is the MIRROR LAMP, whose flash
      # follows the driver's own turn signal rather than the other vehicle, so it is about us.
      side.from_blis(bool(det.sodDetect))
