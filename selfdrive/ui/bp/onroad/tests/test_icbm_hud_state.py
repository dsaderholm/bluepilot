"""The hold state both screens draw must come from one reader.

Written 2026-08-13 for the comma 4 on-road port. The big screen and mici have separate renderer
trees with no shared base class, so the tempting move is to copy the reader. Two copies drift: one
gets a new field, one keeps a stale enum position, and the screens then disagree about whether the
driver has a hold at all.

The enum RAW VALUES are what this really guards. They are positions in a capnp enum, not names, so
an upstream reorder changes their meaning with nothing failing to compile.
"""
from openpilot.selfdrive.ui.bp.onroad.icbm_hud_state import IcbmHudState, max_box_state, read_icbm_hud_state


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


def test_a_hold_with_no_posted_limit_is_still_drawn():
  """REVERSED 2026-08-20. This asserted the opposite until the rule it tested expired.

  The old rule hid a hold whenever SLA had no number, on the reasoning that the hold IS the MAX
  speed so a second readout invents a concept. That was true while a hold could not exist without a
  limit. `enforce_hold_policy` was rekeyed on 2026-08-19 to "is SLA in assist mode", specifically so
  a hold would survive a coverage gap -- and from that moment this rule was hiding real holds on the
  roads the policy change existed to serve.

  He reported it from the seat: *"when I do plus and minus, when SLA doesn't have a number, it
  should change my max speed and set up a hold at the same time."* The hold was being set up. It
  drew nothing, which is indistinguishable from it not existing.

  The two-numbers confusion is answered by the MODE now: with SLA off, informational or warning the
  controller clears the baseline outright, so `has_hold` is False and no badge is drawn at all.
  """
  s = read_icbm_hud_state(_SM2(_Icbm(baseline=72.0), _SL(False, 0.0)))
  assert s.has_hold, "the hold exists and governs the car"
  assert s.worth_showing, "a hold governing the car must say so on screen"


def test_the_badge_appears_once_a_real_limit_exists():
  s = read_icbm_hud_state(_SM2(_Icbm(baseline=72.0), _SL(True, 24.6)))
  assert s.worth_showing


def test_a_valid_flag_with_no_limit_still_draws_the_hold():
  """SLA stays active on a road with no data -- a documented trap in this fork.

  It no longer changes the badge either way (see above), but the input is kept as a test because it
  is the shape that used to decide visibility, and a future rule keying on it again should fail here.
  """
  assert read_icbm_hud_state(_SM2(_Icbm(baseline=72.0), _SL(True, 0.0))).worth_showing


def test_no_hold_is_never_worth_showing_however_valid_the_limit():
  assert not read_icbm_hud_state(_SM2(_Icbm(baseline=0.0), _SL(True, 24.6))).worth_showing


def test_missing_speed_limit_data_does_not_raise():
  """The point of this one was always that a HUD must not raise; the visibility half moved."""
  s = read_icbm_hud_state(_SM2(_Icbm(baseline=72.0), None))
  assert s.has_hold and s.worth_showing
  assert not s.sla_has_limit, "absent SLA data must still read as no limit"


class TestAPinCanStillBeCreatedWhereThereIsNoLimit:
  """THE BADGE IS THE ONLY TAP TARGET FOR PINNING, so hiding it removes the gesture entirely.

  `enforce_hold_policy` (2026-08-15) drops the baseline on a road with no posted limit. That
  is what he asked for. But `_hold_rect` -- the rectangle a tap is tested against -- is set where
  the badge is DRAWN and cleared to None everywhere else, so no hold meant no badge meant no way to
  create a pin at all. On exactly the roads he says pins are for: *"we do still want pinned holds
  since those are frequently done when SLA doesn't have a number."*

  Found on 2026-08-17 from his device, not from the code: `IcbmHoldObservations` was 6 KB and
  growing while `IcbmPinnedHolds` was `[]` and five days stale.
  """

  def test_a_bare_suggestion_shows_the_badge_with_no_hold_at_all(self):
    s = read_icbm_hud_state(_SM2(_Icbm(baseline=0.0, suggestion=45.0), _SL(False, 0.0)))
    assert not s.has_hold
    assert s.worth_showing, "no badge means no tap target means the pin can never be created"
    assert s.display_value == 45, "the badge would read 0 -- baseline is not the offered speed"

  def test_a_real_hold_still_shows_its_own_number_not_the_suggestion(self):
    s = read_icbm_hud_state(_SM2(_Icbm(baseline=72.0, suggestion=45.0), _SL(True, 24.6)))
    assert s.display_value == 72

  def test_the_suggestion_is_read_even_though_there_is_no_hold(self):
    """It used to be read INSIDE the hold branch, so it was unreachable in the case it exists for."""
    s = read_icbm_hud_state(_SM2(_Icbm(baseline=0.0, suggestion=45.0), _SL(False, 0.0)))
    assert s.pin_suggested and s.pin_suggestion == 45

  def test_no_hold_and_no_suggestion_still_shows_nothing(self):
    s = read_icbm_hud_state(_SM2(_Icbm(baseline=0.0, suggestion=0.0), _SL(True, 24.6)))
    assert not s.worth_showing


def test_a_hold_outranks_a_suggestion_on_the_badge():
  """The `and not has_hold` guard on the suggestion term is gone with the rule that needed it.

  It existed only to stop a standing suggestion re-exposing a hold `sla_has_limit` was hiding.
  Nothing is hidden now, so there is no leak to plug -- but the badge must still read the HOLD when
  both exist, or a tap target would offer to pin a number the car is not holding.
  """
  held = IcbmHudState(baseline=70, sla_has_limit=False, pinned=False, pin_suggested=False)
  assert held.worth_showing
  assert held.display_value == 70

  both = IcbmHudState(baseline=70, sla_has_limit=False, pinned=False,
                      pin_suggested=True, pin_suggestion=65)
  assert both.worth_showing
  assert both.display_value == 70, "the suggestion overwrote the hold the car is actually holding"

  # The case the suggestion exception is actually for: an offer with no hold behind it.
  offered = IcbmHudState(baseline=0, sla_has_limit=False, pin_suggested=True, pin_suggestion=65)
  assert offered.worth_showing
  assert offered.display_value == 65


class TestTheBigNumberIsWhatTheCarIsDrivenTo:
  """The 2026-08-20 rule, from the owner, after walking the five on-screen speeds one at a time.

  *"Should we have the target be the hold when there is a hold at all and then have the SLA speed +
  offset be a different number?"* -- yes, with the fallback in the label slot rather than as a
  fourth number.
  """

  def test_the_offered_pin_takes_the_label_when_there_is_no_hold(self):
    """RANK 3, added 2026-08-22 when the HOLD badge was deleted.

    The badge used to carry this number: `display_value` fell back to `pin_suggestion` when there
    was no hold, and tapping the badge was the only way to accept the offer. Deleting the badge
    left the offer with a mark in the box corner and no way to say WHAT SPEED it was for.

    It cannot become the big number -- that is what the car is being driven to, and an offer is
    not -- so the label slot is the only place left."""
    box = max_box_state(hold=0.0, sla_fallback=None, set_speed=55.0, dash=55.0,
                        pin_suggestion=45.0)
    assert box.aim == 55.0, "an OFFER must never move the number the car is being driven to"
    assert box.label == "45"
    assert box.label_is_number
    assert box.pin_offer, "nothing would draw the ring, so the offer is unacceptable"
    assert not box.hold_driving

  def test_the_dash_still_outranks_an_offer(self):
    """A pin is never urgent; "the car is not at your number" always is."""
    box = max_box_state(hold=0.0, sla_fallback=None, set_speed=55.0, dash=38.0,
                        pin_suggestion=45.0)
    assert box.label == "38"
    assert box.pin_offer, "the ring must survive even when the label is showing something else"

  def test_a_hold_suppresses_the_offer_entirely(self):
    """`pin_offer` and `hold_driving` are mutually exclusive by construction -- rank 2 and rank 3
    can never both be live, which is what keeps the label unambiguous."""
    box = max_box_state(hold=70.0, sla_fallback=55.0, set_speed=55.0, dash=70.0,
                        pin_suggestion=45.0)
    assert not box.pin_offer
    assert box.label == "55", "the offer displaced the fallback, which is the actionable number"

  def test_a_pinned_hold_is_marked_and_a_pressed_one_is_not(self):
    """The badge used to be what told a pinned hold from a pressed one. Now it is this flag, drawn
    as a filled dot in the corner the badge's dot used to occupy."""
    assert max_box_state(hold=45.0, sla_fallback=None, set_speed=45.0, dash=45.0,
                         pinned=True).pinned
    assert not max_box_state(hold=45.0, sla_fallback=None, set_speed=45.0, dash=45.0,
                             pinned=False).pinned
    # A pin flag with no hold behind it is not a pinned hold and must not draw the filled dot --
    # that would claim the car is holding a speed it is not.
    assert not max_box_state(hold=0.0, sla_fallback=None, set_speed=45.0, dash=45.0,
                             pinned=True).pinned

  def test_a_locked_hold_does_not_claim_the_number_is_his(self):
    """`hold_locked` means something else owns the target, so a press cannot move the hold. The
    badge said this by going gray; the box says it by not tinting.

    NOT the same statement as "the car is not at your number", which rank 1 already makes -- a hold
    can be locked while the car sits exactly on it."""
    locked = max_box_state(hold=70.0, sla_fallback=None, set_speed=70.0, dash=70.0,
                           hold_locked=True)
    assert locked.hold_driving, "the hold still governs the car; only the tint is withheld"
    assert locked.hold_locked
    assert not max_box_state(hold=70.0, sla_fallback=None, set_speed=70.0, dash=70.0).hold_locked
    # With no hold there is nothing to lock, so the flag must not survive on its own.
    assert not max_box_state(hold=0.0, sla_fallback=55.0, set_speed=55.0, dash=55.0,
                             hold_locked=True).hold_locked

  def test_sla_drives_the_number_when_there_is_no_hold(self):
    """His words: *"I like having that fall back if I cancel my hold."* Unchanged from today."""
    box = max_box_state(hold=0.0, sla_fallback=45.0, set_speed=45.0, dash=45.0)
    assert box.aim == 45.0
    assert box.label == "MAX"
    assert not box.hold_driving

  def test_a_hold_takes_the_number_over_from_sla(self):
    box = max_box_state(hold=80.0, sla_fallback=45.0, set_speed=45.0, dash=80.0)
    assert box.aim == 80.0
    assert box.hold_driving
    assert box.label == "45", "the fallback he would get back by cancelling is not offered"
    assert box.label_is_number

  def test_a_hold_with_no_limit_falls_through_to_MAX(self):
    """The common case on the roads holds are for: no limit, so no fallback exists to show."""
    box = max_box_state(hold=80.0, sla_fallback=None, set_speed=52.0, dash=80.0)
    assert box.aim == 80.0
    assert box.hold_driving
    assert box.label == "MAX"
    assert not box.label_is_number

  def test_being_slowed_outranks_the_fallback(self):
    """The label slot can say one thing. "Something is pulling you down right now" beats "here is
    what you would get back", because only one of them is happening to the car."""
    box = max_box_state(hold=80.0, sla_fallback=45.0, set_speed=45.0, dash=38.0)
    assert box.aim == 80.0, "the aim must not follow the car down; that is what the little number is for"
    assert box.label == "38"
    assert box.label_is_number

  def test_with_no_hold_and_no_limit_the_set_speed_stands(self):
    """Where SET left him. Arbitrary on an unmapped road -- which is the whole reason a press should
    take over from it -- but it is what the car is driving to until he presses."""
    box = max_box_state(hold=0.0, sla_fallback=None, set_speed=52.0, dash=52.0)
    assert box.aim == 52.0
    assert box.label == "MAX"
    assert not box.hold_driving

  def test_a_pinned_hold_drives_the_number_like_any_other(self):
    """A pin is a hold that was placed earlier; it steers the car identically. The badge below is
    what distinguishes them, not the big number."""
    box = max_box_state(hold=64.0, sla_fallback=None, set_speed=30.0, dash=64.0)
    assert box.aim == 64.0
    assert box.hold_driving

  def test_the_little_number_never_fires_on_a_matching_aim(self):
    """It means "the car is not at your number". Equal values must show the word, or it would appear
    on every press from the ordinary drift between openpilot's count and Ford's."""
    for dash in (80.0, 80.4, 79.6):
      assert max_box_state(80.0, None, 52.0, dash).label == "MAX", f"fired at dash={dash}"
