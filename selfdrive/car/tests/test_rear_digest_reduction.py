"""FusionPilot: the reduction the feeder microcontroller performs.

WHY IT IS TESTED HERE AND NOT ON THE TEENSY. This algorithm turns ~2140 raw frames/s into 60,
measured against a real capture on 2026-08-14. It will be reimplemented in C++ on a part bolted
behind a bumper, where a wrong sign costs a trip with a multimeter. So it is proven in Python
first, and the firmware mirrors this -- when the two disagree, this is the reference.

THE TWO MISTAKES THAT WOULD NOT LOOK LIKE MISTAKES:

  * a sign error on closing speed, which aborts passes for cars falling BEHIND and permits them for
    cars catching up -- exactly inverted, and entirely plausible-looking in a log
  * picking the wrong target out of the set, which produces a number that is always reasonable and
    occasionally about the wrong vehicle
"""
from openpilot.tools.bp_rear_digest_sim import (
  Detection, reduce_to_sides, MIN_CLOSING_MS, OWN_LANE_HALF_WIDTH_M)


def det(d_rel, y_rel, v_rel, amplitude=-20.0):
  return Detection(d_rel=d_rel, y_rel=y_rel, v_rel=v_rel, amplitude=amplitude)


class TestWhichTargetIsReported:

  def test_the_nearest_closing_target_wins(self):
    """At EQUAL closing speed the nearest is also the soonest, so this holds under either rule --
    which is exactly why it went on passing while the rule itself was wrong. See
    test_a_faster_target_DOES_outrank_a_nearer_one for the case that separates them."""
    left, _ = reduce_to_sides([det(90.0, 3.7, 8.0), det(40.0, 3.7, 8.0), det(70.0, 3.7, 8.0)])
    assert left.detected and left.d_rel == 40.0

  def test_a_louder_target_does_not_outrank_a_nearer_one(self):
    """Amplitude is decoded and deliberately unused. A big slow lorry is not more urgent than a
    small fast car, and sorting by return strength would quietly make it so."""
    left, _ = reduce_to_sides([det(30.0, 3.7, 6.0, amplitude=-40.0),
                               det(80.0, 3.7, 6.0, amplitude=+10.0)])
    assert left.d_rel == 30.0

  def test_a_faster_target_DOES_outrank_a_nearer_one(self):
    """REVERSED 2026-08-21, on a measurement rather than an argument. This asserted the opposite
    and its premise was wrong: 95 m closing at 30 m/s arrives in 3.2 s and refuses a lane change,
    while 25 m at 2 m/s takes 12.5 s and allows one. Reporting the nearer car handed openpilot a
    clear answer about a side with something arriving in three seconds.

    tools/bp_digest_pick_rule.py measured it on route 000003a1: of 33,287 multi-target side-scans,
    3.9% crossed the 8 s veto window this way, worst case 12.0 s reported against a discarded
    target at 0.4 s.

    AND THE CHANGE IS MONOTONICALLY CONSERVATIVE, which is what makes it safe to make without a
    road test: min(TTC) <= the TTC of any other target by construction, so the new rule blocks
    everywhere the old one blocked and sometimes more. It can never open a lane the old rule
    refused."""
    left, _ = reduce_to_sides([det(25.0, 3.7, 2.0), det(95.0, 3.7, 30.0)])
    assert left.d_rel == 95.0

  def test_the_count_reports_everything_that_was_considered(self):
    """detected with a count of zero is impossible and would mean a feeder bug -- the DBC comment
    says so, so it has to be true here."""
    left, _ = reduce_to_sides([det(40.0, 3.7, 5.0), det(60.0, 4.5, 5.0), det(80.0, 5.0, 5.0)])
    assert left.detected and left.target_count == 3


class TestClosingIsTheWholePoint:

  def test_a_receding_target_is_not_reported(self):
    """A car dropping back is not a reason to refuse a lane change, and reporting it would make
    every overtaken vehicle a veto."""
    left, _ = reduce_to_sides([det(40.0, 3.7, -9.0)])
    assert not left.detected and left.target_count == 0

  def test_a_target_holding_station_is_not_closing(self):
    left, _ = reduce_to_sides([det(40.0, 3.7, MIN_CLOSING_MS - 0.01)])
    assert not left.detected

  def test_the_threshold_is_inclusive(self):
    left, _ = reduce_to_sides([det(40.0, 3.7, MIN_CLOSING_MS)])
    assert left.detected


class TestWhichSideATargetIsOn:

  def test_left_and_right_are_separated(self):
    left, right = reduce_to_sides([det(50.0, 3.7, 6.0), det(30.0, -3.7, 9.0)])
    assert left.detected and left.d_rel == 50.0
    assert right.detected and right.d_rel == 30.0

  def test_a_target_dead_astern_belongs_to_neither_side(self):
    """THE DEADBAND, and why it is not a sign test. A car directly behind is in OUR lane, not in
    the lane we would move into. Binning it by the sign of a noisy tenth of a metre would flicker
    it between sides and veto both."""
    left, right = reduce_to_sides([det(40.0, 0.2, 10.0)])
    assert not left.detected and not right.detected
    assert left.target_count == 0 and right.target_count == 0

  def test_a_target_just_outside_the_deadband_is_reported(self):
    left, _ = reduce_to_sides([det(40.0, OWN_LANE_HALF_WIDTH_M + 0.01, 10.0)])
    assert left.detected

  def test_one_side_being_busy_does_not_touch_the_other(self):
    """The two sides are independent by construction, and this is the shape that invites a
    copy-paste error -- the digest messages are byte-identical in layout on purpose."""
    left, right = reduce_to_sides([det(20.0, 3.7, 12.0), det(25.0, 4.0, 14.0)])
    assert left.target_count == 2
    assert not right.detected and right.d_rel == 0.0


class TestItPicksTheSoonestNotTheNearest:
  """The feeder reduces many targets to one, so whichever it drops is invisible downstream. Picking
  min(d_rel) reported a side CLEAR while a car inside the veto window was thrown away -- measured
  at 3.9% of multi-target side-scans on route 000003a1, worst case 12.0 s reported against a
  discarded target at 0.4 s. See reduce_to_sides' docstring for the full measurement."""

  @staticmethod
  def _det(d_rel, v_rel, y_rel):
    return Detection(d_rel=d_rel, y_rel=y_rel, v_rel=v_rel, amplitude=0.0)

  def test_the_arriving_car_wins_over_the_closer_one(self):
    # 80 m at 15 m/s arrives in 5.3 s and refuses the pass; 20 m at 2 m/s takes 10 s and allows it.
    far_fast = self._det(80.0, 15.0, 3.0)
    near_slow = self._det(20.0, 2.0, 3.0)
    left, _ = reduce_to_sides([near_slow, far_fast])
    assert left.detected
    assert left.d_rel == 80.0, "reported the nearer car and discarded the one arriving first"
    assert left.target_count == 2, "the tell that a target was dropped must survive"

  def test_order_does_not_decide_it(self):
    a = reduce_to_sides([self._det(80.0, 15.0, 3.0), self._det(20.0, 2.0, 3.0)])[0]
    b = reduce_to_sides([self._det(20.0, 2.0, 3.0), self._det(80.0, 15.0, 3.0)])[0]
    assert a.d_rel == b.d_rel == 80.0

  def test_nearest_still_wins_when_it_is_also_soonest(self):
    left, _ = reduce_to_sides([self._det(30.0, 10.0, 3.0), self._det(90.0, 10.0, 3.0)])
    assert left.d_rel == 30.0

  def test_the_two_sides_are_reduced_independently(self):
    left, right = reduce_to_sides([self._det(80.0, 15.0, 3.0), self._det(20.0, 2.0, 3.0),
                                   self._det(50.0, 5.0, -3.0)])
    assert left.d_rel == 80.0
    assert right.d_rel == 50.0
