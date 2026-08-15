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


class _SL:
  """Minimal speedLimit stand-in: resolver.speedLimitValid + resolver.speedLimit."""
  def __init__(self, valid, limit):
    self.resolver = type("R", (), {"speedLimitValid": valid, "speedLimit": limit})()


class _SM2:
  def __init__(self, icbm, sl=None):
    self._icbm, self._sl = icbm, sl
  def __getitem__(self, k):
    if k == 'selfdriveStateSP':
      return type("S", (), {"intelligentCruiseButtonManagement": self._icbm})()
    if k == 'longitudinalPlanSP':
      if self._sl is None:
        raise KeyError(k)
      return type("L", (), {"speedLimit": self._sl})()
    raise KeyError(k)


def test_the_badge_is_hidden_when_speed_limit_assist_has_no_limit():
  """Without SLA the hold IS the MAX speed -- drawing it twice invents a second concept.

  The controller sets v_baseline = v_cruise_cluster on a press and falls back to v_cruise_cluster
  with no hold, so ICBM aims at the driver's own number either way. An owner running without SLA
  reported exactly this confusion: two numbers on screen and no idea which was his.
  """
  s = read_icbm_hud_state(_SM2(_Icbm(baseline=72.0), _SL(False, 0.0)))
  assert s.has_hold, "the hold still exists and still governs the car"
  assert not s.worth_showing, "but there is nothing to show that MAX does not already say"


def test_the_badge_appears_once_a_real_limit_exists():
  s = read_icbm_hud_state(_SM2(_Icbm(baseline=72.0), _SL(True, 24.6)))
  assert s.worth_showing


def test_a_valid_flag_with_no_limit_is_not_a_limit():
  """SLA stays active on a road with no data -- a documented trap in this fork."""
  assert not read_icbm_hud_state(_SM2(_Icbm(baseline=72.0), _SL(True, 0.0))).worth_showing


def test_no_hold_is_never_worth_showing_however_valid_the_limit():
  assert not read_icbm_hud_state(_SM2(_Icbm(baseline=0.0), _SL(True, 24.6))).worth_showing


def test_missing_speed_limit_data_hides_the_badge_rather_than_raising():
  s = read_icbm_hud_state(_SM2(_Icbm(baseline=72.0), None))
  assert s.has_hold and not s.worth_showing
