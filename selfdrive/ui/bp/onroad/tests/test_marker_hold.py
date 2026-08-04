"""
BluePilot: the adjacent-lane marker's dropout hold and colour debounce.

These are display timings, so the thing worth guarding is not that they smooth -- it is the two
places smoothing would be a lie: a marker outliving an unavailable radar, and a hold that never
expires. Both are asserted directly.
"""

from openpilot.selfdrive.ui.bp.onroad.marker_hold import MarkerHold, DROPOUT_HOLD_S, BLOCKING_DEBOUNCE_S

DT = 1 / 20.0     # the UI's frame rate


def run(h, seconds, available=True, occupied=True, blocking=False):
  out = []
  for _ in range(int(round(seconds / DT))):
    out.append(h.update(DT, available, occupied, blocking))
  return out


class TestDropoutHold:
  def test_a_live_track_draws_at_full_alpha(self):
    h = MarkerHold()
    draw, alpha, _ = run(h, 1.0)[-1]
    assert draw and alpha == 1.0

  def test_the_first_frame_of_a_vehicle_is_fresh(self):
    """The renderer resets its position filters on this, because lerping from the previous car
    drags the marker across the screen."""
    h = MarkerHold()
    assert h.update(DT, True, True, False)[2] is True
    assert h.update(DT, True, True, False)[2] is False

  def test_a_brief_dropout_keeps_the_marker(self):
    """The reported case: a car at the edge of radar range blinking in and out. ~0.36 s is what one
    re-confirmation costs, so a hold shorter than that would not have fixed anything."""
    h = MarkerHold()
    run(h, 1.0)
    held = run(h, 0.4, occupied=False)
    assert all(d for d, _, _ in held)
    assert held[-1][1] < 1.0, "should be fading, not held at full brightness"
    # ...and the same vehicle returns without a filter reset.
    assert run(h, 0.1)[0][2] is False

  def test_the_hold_actually_expires(self):
    """The failure that would matter: a car that genuinely left staying on screen."""
    h = MarkerHold()
    run(h, 1.0)
    out = run(h, DROPOUT_HOLD_S + 0.5, occupied=False)
    assert not out[-1][0]
    assert h.gap_s == 0.0
    # and coming back after a real absence IS a new vehicle
    assert run(h, 0.1)[0][2] is True

  def test_alpha_decays_monotonically(self):
    h = MarkerHold()
    run(h, 1.0)
    alphas = [a for _, a, _ in run(h, DROPOUT_HOLD_S - DT, occupied=False)]
    assert alphas == sorted(alphas, reverse=True)
    assert alphas[-1] < 0.2


class TestUnavailableIsNeverHeld:
  def test_unavailable_drops_the_marker_immediately(self):
    """Not a dropout. A dead radar holding a stale car on screen is the one failure this must not
    have -- unavailable never gets to look like data."""
    h = MarkerHold()
    run(h, 1.0)
    draw, alpha, _ = h.update(DT, False, True, False)
    assert not draw and alpha == 0.0
    assert not h.drawing

  def test_returning_from_unavailable_is_a_fresh_vehicle(self):
    h = MarkerHold()
    run(h, 1.0)
    h.update(DT, False, True, False)
    assert h.update(DT, True, True, False)[2] is True


class TestBlockingDebounce:
  def test_a_vehicle_on_the_threshold_does_not_chatter(self):
    """The other half of the report: a car going 'in between the speed I want to pass at and the
    speed I don't'. Alternating every frame must move the colour zero times, not ten a second."""
    h = MarkerHold()
    for i in range(200):
      h.update(DT, True, True, i % 2 == 0)
    assert h.blocking is False

  def test_a_settled_change_is_followed(self):
    h = MarkerHold()
    run(h, BLOCKING_DEBOUNCE_S + 0.2, blocking=True)
    assert h.blocking is True

  def test_it_is_not_followed_early(self):
    """Bounds the staleness: the colour is never more than the debounce behind the truth, and never
    less than it either, which is what makes the guarantee statable."""
    h = MarkerHold()
    run(h, BLOCKING_DEBOUNCE_S - 2 * DT, blocking=True)
    assert h.blocking is False

  def test_a_dropout_does_not_restart_the_colour_timer(self):
    """The two timings are independent. A track blinking out mid-change must not reset the colour
    debounce, or a car at the range edge could never change colour at all."""
    h = MarkerHold()
    run(h, BLOCKING_DEBOUNCE_S / 2, blocking=True)
    run(h, DROPOUT_HOLD_S / 2, occupied=False, blocking=True)
    run(h, BLOCKING_DEBOUNCE_S / 2 + DT, blocking=True)
    assert h.blocking is True
