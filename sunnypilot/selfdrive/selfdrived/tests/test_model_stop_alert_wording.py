"""FusionPilot: the stop alert must not contradict the feature that is switched on.

He caught this on 2026-08-19: *"I still got a few alerts that cruise would not stop, which doesn't
make sense because it should stop for everything now, right?"* The alert had read "Cruise will not
stop for it." since before the stop override existed, and nobody revisited it when the override
shipped -- the same staleness that had the settings screen claiming "IT SLOWS, IT DOES NOT STOP"
a release after it could.

The fix is NOT to promise a stop. Measured the same day, the override has never once fired, and an
alert that says the car will stop when it will not is far worse than one that undersells. What the
alert now carries is the PRECONDITION he can act on -- it only runs while cruise is still engaged,
and braking disengages -- which no screen was telling him.

So there are two wordings and this pins both, because the failure mode is one of them silently
becoming the other:

  override available    "Stay off the brake to let it stop."   <- actionable
  override unavailable  "Slowing to 20 mph -- the stop is yours."

Neither may contain "will not stop", which is the phrase he reacted to.
"""
import sunnypilot.selfdrive.selfdrived.events as ev


def _alert(monkeypatch, available: bool):
  monkeypatch.setattr(ev, "_STOP_OVERRIDE_AVAILABLE", available)
  return ev.model_stop_alert(None, None, None, False, 0, None)


def test_it_never_asks_him_to_leave_the_brake_alone(monkeypatch):
  """WITHDRAWN 2026-08-20, and this test is now its opposite.

  The alert used to say "Stay off the brake to let it stop" whenever the override was available. It
  fires on `hasSlowDown`; the override arms on `shouldStop`; and `shouldStop` is measured to be a
  STOPPED-CAR state -- never true above 3 mph across 21,936 frames on three drives. He followed the
  instruction at a red light and the car did not stop, because the trigger cannot become true until
  the car already has.

  An alert that asks the driver to WITHHOLD a control input has to be keyed on the same signal as
  the thing that will act. Until the override triggers on approach intent, no wording can honestly
  ask that -- so none may.
  """
  for available in (True, False):
    a = _alert(monkeypatch, available)
    t = a.alert_text_2.lower()
    assert "stay off" not in t and "foot off" not in t,       f"available={available}: the alert is asking him not to brake for a stop that cannot happen"
    assert "will not stop" not in t


def test_it_says_the_stop_is_his_when_the_override_cannot_run(monkeypatch):
  a = _alert(monkeypatch, False)
  assert "20" in a.alert_text_2
  assert "will not stop" not in a.alert_text_2.lower()


def test_neither_wording_claims_the_car_will_not_stop(monkeypatch):
  """The exact phrase he reacted to, gone from both branches."""
  for available in (True, False):
    a = _alert(monkeypatch, available)
    assert "will not stop" not in a.alert_text_2.lower(), \
      f"available={available} still tells him cruise will not stop"


def test_both_wordings_still_name_the_cause(monkeypatch):
  """The first line is what distinguishes this from the unconfirmed-lead alert, which names a
  VEHICLE. Two different causes need two different signatures or the driver learns from neither --
  he said so directly: "I'm not sure if the unconfirmed lead has the same warning"."""
  for available in (True, False):
    a = _alert(monkeypatch, available)
    assert "Stop sign or signal" in a.alert_text_1


def test_the_availability_probe_never_raises_into_the_alert_path(monkeypatch):
  """An alert that throws takes selfdrived's alert path down, so an unreadable param must degrade
  to the conservative wording rather than propagate. Same shape as the guard-inside-the-guard
  failures recorded in CLAUDE.md."""
  import sys
  import types

  monkeypatch.setattr(ev, "_STOP_OVERRIDE_AVAILABLE", None)

  class Boom:
    def get_bool(self, *a, **k):
      raise RuntimeError("params store is gone")

  # Replace the MODULE the function imports from, so the failure happens where it really would --
  # inside the lazy import -- rather than at a name the runner has already stubbed.
  mod = types.ModuleType("openpilot.common.params")
  mod.Params = lambda *a, **k: Boom()
  monkeypatch.setitem(sys.modules, "openpilot.common.params", mod)

  assert ev._stop_override_available() is False
