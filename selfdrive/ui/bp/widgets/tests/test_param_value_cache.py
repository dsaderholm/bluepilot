"""
BluePilot: tests for the value behind a +/- settings control.

Reported after a drive: "all my angle tuning got wiped in the BluePilot settings, and I also
couldn't tweak it while driving."

Those read as two complaints and were one bug in three parts, all of them in this value path. The
widget itself needs pyray and cannot be imported offline, which is exactly why the logic was pulled
out into param_value_cache.py -- there was nothing here to test before, and nothing caught any of
it.
"""

import pytest

from openpilot.selfdrive.ui.bp.widgets.param_value_cache import ParamValueCache


class FakeParams:
  """Counts reads, so "how often does this touch the store" is an assertion rather than a guess."""

  def __init__(self, value=0.92, fail=False):
    self.value = value
    self.fail = fail
    self.reads = 0
    self.writes = []

  def get(self, key, return_default=False):
    self.reads += 1
    if self.fail:
      raise RuntimeError("param store unavailable")
    return self.value

  # MATCHES THE REAL SIGNATURE: params_pyx is `put(self, key, dat, bool block = False)`.
  def put(self, key, dat, block=False):
    self.writes.append((dat, block))
    self.value = dat


def read_n(cache, n):
  for _ in range(n):
    cache.get()


class TestItDoesNotReadTheStoreEveryFrame:
  """Fault 1: _render called _get_value() on every frame. Four float controls in Angle Tuning, ten
  on the page, twenty-odd frames a second -- hundreds of eMMC reads per second while driving. That
  is why the page felt dead onroad and fine parked."""

  def test_a_hundred_frames_are_not_a_hundred_reads(self):
    p = FakeParams()
    cache = ParamValueCache(p, "FordLowSpeedFactor_ang")
    read_n(cache, 100)
    assert p.reads <= 3, f"{p.reads} store reads in 100 frames"

  def test_it_still_notices_a_value_changed_elsewhere(self):
    """Rarely is not never. A defaults migration or the other panel has to show up eventually."""
    p = FakeParams(value=0.92)
    cache = ParamValueCache(p, "FordLowSpeedFactor_ang")
    assert cache.get() == pytest.approx(0.92)
    p.value = 1.10
    read_n(cache, ParamValueCache.REFRESH_FRAMES + 1)
    assert cache.get() == pytest.approx(1.10)


class TestOurOwnWriteIsNotRacedByTheNextRead:
  """Fault 2: set wrote with block=False -- putNonBlocking -- and the very next frame read the
  store back. The read beats the write, so the number snapped back to where it was. "I couldn't
  tweak it while driving."
  """

  def test_the_value_holds_after_a_write_even_if_the_store_is_still_stale(self):
    p = FakeParams(value=0.92)
    cache = ParamValueCache(p, "FordLowSpeedFactor_ang")
    cache.get()
    cache.set(0.93)
    p.value = 0.92                      # the store has not caught up yet
    for _ in range(10):
      assert cache.get() == pytest.approx(0.93), "the display snapped back to the old value"

  def test_the_write_still_goes_out(self):
    p = FakeParams()
    cache = ParamValueCache(p, "FordLowSpeedFactor_ang")
    cache.set(0.93)
    assert p.writes == [(0.93, False)], "believing our own value is not a substitute for writing it"

  def test_an_integer_param_is_written_as_an_int(self):
    """Params.put type-checks through PYTHON_2_CPP: (float, INT) is not in it, so a float written
    to an INT key raises TypeError -- which the caller swallows and the setting silently never
    changes."""
    p = FakeParams(value=760)
    cache = ParamValueCache(p, "FordBlinkerBlinkPeriod", integer=True)
    cache.set(770.0)
    assert p.writes == [(770, False)]
    assert isinstance(p.writes[0][0], int)

  def test_a_failing_write_does_not_escape(self):
    """A settings screen must not crash because a param write did."""
    p = FakeParams()
    p.put = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("store gone"))
    cache = ParamValueCache(p, "FordLowSpeedFactor_ang")
    cache.set(1.0)
    assert cache.get() == pytest.approx(1.0)


class TestAnUnreadableValueIsNoneNotTheMinimum:
  """Fault 3, and the one that actually overwrote his tuning. _get_value returned self.min_value on
  any read failure, and _increment reads, adds a step and WRITES. So one press after a bad read
  committed the minimum: FordLowSpeedFactor_ang 0.92 -> shows 0.50 -> press -> stores 0.51.
  """

  def test_a_store_that_cannot_be_read_gives_none(self):
    cache = ParamValueCache(FakeParams(fail=True), "FordLowSpeedFactor_ang")
    assert cache.get() is None
    assert not cache.known

  def test_a_value_we_already_have_survives_a_failed_read(self):
    """A momentarily unreadable store is not evidence the setting changed."""
    p = FakeParams(value=0.92)
    cache = ParamValueCache(p, "FordLowSpeedFactor_ang")
    assert cache.get() == pytest.approx(0.92)
    p.fail = True
    read_n(cache, ParamValueCache.REFRESH_FRAMES + 1)
    assert cache.get() == pytest.approx(0.92), "a failed read discarded a good value"

  def test_a_value_that_is_not_a_number_is_not_guessed_at(self):
    cache = ParamValueCache(FakeParams(value="banana"), "FordLowSpeedFactor_ang")
    assert cache.get() is None

  def test_known_is_what_the_widget_gates_the_buttons_on(self):
    """The widget refuses to increment while this is False. Asserted here because the widget
    itself cannot be imported without pyray, so this property is the only thing standing between a
    bad read and a written-back minimum."""
    p = FakeParams(fail=True)
    cache = ParamValueCache(p, "FordLowSpeedFactor_ang")
    assert not cache.known
    p.fail = False
    read_n(cache, ParamValueCache.REFRESH_FRAMES + 1)
    assert cache.known
