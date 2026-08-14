"""FusionPilot: the rear digest, from the wire to the gate.

EVERY TEST HERE IS THE SAME QUESTION: does a failure read as UNAVAILABLE, or as CLEAR?

That distinction is the whole safety argument for this feature. `may_actuate` refuses any side whose
rear sensor is unavailable, so unavailable is safe by construction -- but a bug that reports "seen,
nothing there" when the truth is "not looking" turns the gate into a rubber stamp, and it does it
silently, because a clear road and a dead sensor produce identical logs downstream.

The failure modes that must all land as unavailable: no message at all, a message that stopped
arriving, a feeder still talking after its radar died, and a radar whose detection rate has
collapsed. Each is a real thing that can happen to a part bolted behind a bumper.
"""
from types import SimpleNamespace

from openpilot.sunnypilot.selfdrive.controls.lib.rear_approach import RearApproach


def digest(*, available=True, alive=True, hz=33, left=None, right=None):
  def side(d):
    d = d or {}
    return SimpleNamespace(detected=d.get("detected", False), dRel=d.get("dRel", 0.0),
                           yRel=d.get("yRel", 0.0), vRel=d.get("vRel", 0.0),
                           targetCount=d.get("targetCount", 0))
  return SimpleNamespace(dataAvailable=available, radarAlive=alive, detectionHz=hz,
                         validDetections=0, left=side(left), right=side(right))


class FakeSM:
  """SubMaster-shaped, carrying only what update() reads."""

  def __init__(self, msg=None, valid=True, updated=True, missing=False):
    self._missing = missing
    self._msg = msg
    self.valid = {} if missing else {"rearRadarBP": valid}
    self.updated = {} if missing else {"rearRadarBP": updated}

  def __getitem__(self, k):
    if self._missing:
      raise KeyError(k)
    return self._msg


class TestNothingReadsAsClearByAccident:

  def test_no_message_at_all_is_unavailable(self):
    """Every car without a feeder. If this reported clear, passing assist would actuate on hardware
    that does not exist -- which is precisely the state the whole fork is in today."""
    ra = RearApproach()
    ra.update(FakeSM(missing=True))
    assert not ra.left.available and not ra.right.available

  def test_a_stale_message_is_unavailable(self):
    """The feeder stopped. sm.updated goes False while the last message sits in the socket, so
    reading it without checking would serve a frozen snapshot of the road indefinitely."""
    ra = RearApproach()
    ra.update(FakeSM(digest(left={"detected": True, "dRel": 40.0, "vRel": 9.0}), updated=False))
    assert not ra.left.available

  def test_an_invalid_message_is_unavailable(self):
    ra = RearApproach()
    ra.update(FakeSM(digest(), valid=False))
    assert not ra.left.available

  def test_a_feeder_that_outlived_its_radar_is_unavailable(self):
    """THE NASTY ONE. The microcontroller is fine and still transmitting; the radar behind it is
    dead. Every field decodes, nothing is stale, and the digest says the road is empty forever."""
    ra = RearApproach()
    ra.update(FakeSM(digest(available=False, alive=False)))
    assert not ra.left.available and not ra.right.available

  def test_a_collapsed_detection_rate_is_unavailable(self):
    """dataAvailable is the feeder's own verdict and already folds in DetectionHz. Asserted here
    from the consumer's side so the rule survives someone 'simplifying' the feeder."""
    ra = RearApproach()
    ra.update(FakeSM(digest(available=False, alive=True, hz=2)))
    assert not ra.left.available


class TestWhatItDoesWhenItCanSee:

  def test_a_closing_target_fills_the_side_it_is_on(self):
    ra = RearApproach()
    ra.update(FakeSM(digest(left={"detected": True, "dRel": 60.0, "vRel": 12.0})))
    assert ra.left.available and ra.left.detected
    assert ra.left.d_rel == 60.0 and ra.left.v_rel == 12.0
    assert abs(ra.left.ttc - 5.0) < 0.01, "TTC is what the abort and the start gate both read"

  def test_seen_and_empty_is_available_but_not_detected(self):
    """The distinction the whole file is about, from the other direction. A side the radar looked
    at and found empty must CLEAR the gate -- if it read unavailable, a working radar would refuse
    every lane change and be indistinguishable from no radar at all."""
    ra = RearApproach()
    ra.update(FakeSM(digest(left={"detected": False}, right={"detected": False})))
    assert ra.left.available and not ra.left.detected
    assert ra.right.available and not ra.right.detected

  def test_the_sides_do_not_bleed_into_each_other(self):
    """A left-side target clearing the right would permit a move into traffic. The two messages
    are byte-identical in layout, which is exactly the shape that invites a copy-paste error."""
    ra = RearApproach()
    ra.update(FakeSM(digest(left={"detected": True, "dRel": 20.0, "vRel": 15.0},
                            right={"detected": False})))
    assert ra.left.detected and not ra.right.detected
    assert ra.left.d_rel == 20.0 and ra.right.d_rel == 0.0

  def test_a_receding_target_is_not_closing(self):
    """vRel is positive-is-closing by the DBC's own comment, because ESR.dbc and the Fusion ADAS
    dbc disagree on sign for the same hardware. A sign error here would abort passes for cars
    falling behind and permit them for cars catching up."""
    ra = RearApproach()
    ra.update(FakeSM(digest(left={"detected": True, "dRel": 30.0, "vRel": -8.0})))
    assert ra.left.available and ra.left.detected
    assert not ra.left.closing
    assert not ra.left.demands_abort
