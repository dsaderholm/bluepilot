"""The hold state both screens draw must come from one reader.

Written 2026-08-13 for the comma 4 on-road port. The big screen and mici have separate renderer
trees with no shared base class, so the tempting move is to copy the reader. Two copies drift: one
gets a new field, one keeps a stale enum position, and the screens then disagree about whether the
driver has a hold at all.

The enum RAW VALUES are what this really guards. They are positions in a capnp enum, not names, so
an upstream reorder changes their meaning with nothing failing to compile.
"""
from openpilot.selfdrive.ui.bp.onroad.icbm_hud_state import IcbmHudState, read_icbm_hud_state


class _Raw:
  def __init__(self, raw): self.raw = raw


class _Icbm:
  def __init__(self, send=0, override=1, baseline=72.0, suppressed=False, source=0, suggestion=0):
    self.sendButton = _Raw(send)
    self.overrideState = _Raw(override)
    self.vBaseline = baseline
    self.holdSuppressed = suppressed
    self.baselineSource = _Raw(source)
    self.pinSuggestion = suggestion


class _SM:
  def __init__(self, icbm): self._icbm = icbm
  def __getitem__(self, k):
    if k != 'selfdriveStateSP':
      raise KeyError(k)
    return type("S", (), {"intelligentCruiseButtonManagement": self._icbm})()


def test_a_held_set_speed_is_reported():
  s = read_icbm_hud_state(_SM(_Icbm(baseline=72.0)))
  assert s.has_hold and s.baseline == 72


def test_no_override_means_no_hold_however_large_the_baseline():
  """overrideState 1 is the ONLY value that means the driver is holding a speed."""
  s = read_icbm_hud_state(_SM(_Icbm(override=0, baseline=72.0)))
  assert not s.has_hold and s.baseline == 0


def test_a_zero_baseline_is_not_a_hold():
  assert not read_icbm_hud_state(_SM(_Icbm(baseline=0.0))).has_hold


def test_the_arrow_tracks_the_button_being_sent():
  assert read_icbm_hud_state(_SM(_Icbm(send=1))).arrow == "+"
  assert read_icbm_hud_state(_SM(_Icbm(send=2))).arrow == "-"
  assert read_icbm_hud_state(_SM(_Icbm(send=0))).arrow == ""


def test_the_arrow_is_reported_even_with_no_hold():
  """ICBM moves the set speed against SLA's target too; the arrow is not the badge's alone."""
  assert read_icbm_hud_state(_SM(_Icbm(send=1, override=0))).arrow == "+"


def test_pinned_is_source_4_and_nothing_else():
  assert read_icbm_hud_state(_SM(_Icbm(source=4))).pinned
  for other in (0, 1, 2, 3, 5):
    assert not read_icbm_hud_state(_SM(_Icbm(source=other))).pinned


def test_a_missing_message_is_the_no_hold_default_not_a_crash():
  """A HUD that raises takes the whole on-road screen with it."""
  class Dead:
    def __getitem__(self, k): raise KeyError(k)
  assert read_icbm_hud_state(Dead()) == IcbmHudState()


def test_a_malformed_message_is_also_the_default():
  class Junk:
    def __getitem__(self, k): return type("S", (), {})()
  assert read_icbm_hud_state(Junk()) == IcbmHudState()
