"""BluePilot: taking a moved default only for settings he never touched.

The rule, in his words: *"If I have never changed a value, great, I will get the new default. If I
have, then it shouldn't change."* (2026-08-05)

The reason this needs a mechanism at all is that manager.py writes every declared key to disk on the
first boot that knows about it, so on any car that has been driven "untouched" and "deliberately set
to exactly that" are identical bytes. The missing information is what default he was last handed;
these tests are about that record being kept honestly.

The asymmetry that matters: failing to hand over a new default costs one boot of staleness, and
wrongly clearing a value costs a tune he drove to find. Every ambiguous case must fall the second
way.
"""
import json

from openpilot.sunnypilot.system.params_migration import (
  _migrate_bp_new_defaults, BP_DEFAULTS_SNAPSHOT_KEY, BP_DEFAULTS_OWNED_KEY,
)

KEY = "IcbmMaxTargetDrop"
OTHER = "IcbmLeadMaxTtc"
HIS_TUNE = "FordLowSpeedFactor_ang"


class FakeParams:
  """Separates the shipped default from the stored value, which is the whole distinction here."""

  def __init__(self, stored: dict, defaults: dict):
    self.store = dict(stored)
    self.defaults = dict(defaults)
    self.removed: list[str] = []

  def all_keys(self):
    return list(self.defaults)

  def get_default_value(self, key):
    return self.defaults.get(key)

  def get(self, key, *a, **k):
    return self.store.get(key)

  def remove(self, key):
    self.removed.append(key)
    self.store.pop(key, None)

  # block=, matching params_pyx. A stub without it turns block=True into a TypeError the caller's
  # own except-clause swallows, so the record silently never persists and the migration reruns.
  def put(self, key, value, block=False):
    self.store[key] = value


def boot(p):
  """One boot: run the migration, then let manager.py materialize whatever it cleared."""
  _migrate_bp_new_defaults(p)
  for k, v in p.defaults.items():
    if p.store.get(k) is None:
      p.store[k] = v


class TestTheFirstBootIsPessimistic:
  def test_nothing_is_cleared_before_a_record_exists(self):
    """No record means no way to tell his value from a stale one, and the safe reading is 'his'."""
    p = FakeParams({KEY: "8"}, {KEY: "12"})
    _migrate_bp_new_defaults(p)
    assert p.removed == []
    assert p.store[KEY] == "8"

  def test_it_records_what_it_saw(self):
    p = FakeParams({KEY: "8"}, {KEY: "12"})
    _migrate_bp_new_defaults(p)
    assert json.loads(p.store[BP_DEFAULTS_SNAPSHOT_KEY])[KEY] == "8"


class TestAnUntouchedSettingTakesTheNewDefault:
  def test_a_moved_default_is_handed_over(self):
    p = FakeParams({KEY: "8"}, {KEY: "8"})
    boot(p)                       # first boot records 8, changes nothing
    p.defaults[KEY] = "12"        # a new build ships 12
    boot(p)
    assert KEY in p.removed, "an untouched setting did not take the new default"
    assert p.store[KEY] == "12"

  def test_an_unmoved_default_is_left_alone(self):
    """Clearing a key whose default has not moved is pure churn, and it would show up as a write
    every boot."""
    p = FakeParams({KEY: "12"}, {KEY: "12"})
    boot(p)
    boot(p)
    assert p.removed == []

  def test_it_keeps_working_across_several_moves(self):
    p = FakeParams({KEY: "8"}, {KEY: "8"})
    boot(p)
    for new in ("12", "15", "10"):
      p.defaults[KEY] = new
      boot(p)
      assert p.store[KEY] == new, f"stopped tracking at {new}"


class TestASettingHeChoseIsHisForever:
  def test_a_value_he_tuned_BEFORE_this_shipped_is_still_his(self):
    """The case every other test in this class misses, and the one that describes his car today.

    All of these start from stored == default and have him edit while the mechanism is watching.
    But everything he has actually tuned -- his ICBM numbers, the 100 mph ceiling, the curve feel he
    drove to find -- was set long before this existed, so the edit is never observed. Seeding that
    only RECORDED bought exactly one boot: on the second, stored == remembered read as "untouched"
    and the value was cleared. Not only when a default had moved, either -- any stored value that
    differed from the shipped one, which is the definition of a setting he chose.
    """
    p = FakeParams({KEY: "6"}, {KEY: "8"})   # shipped 8, he runs 6, default never moves
    for _ in range(3):
      boot(p)
      assert p.store[KEY] == "6", "a value he set before this shipped was reset to the default"
    assert KEY in set(json.loads(p.store[BP_DEFAULTS_OWNED_KEY])["keys"])

  def test_a_value_he_tuned_before_survives_the_default_moving_afterwards(self):
    p = FakeParams({KEY: "6"}, {KEY: "8"})
    boot(p)
    for new in ("12", "15", "3"):
      p.defaults[KEY] = new
      boot(p)
      assert p.store[KEY] == "6", f"his value was lost when the default moved to {new}"
    assert KEY not in p.removed

  def test_his_value_survives_a_moved_default(self):
    p = FakeParams({KEY: "8"}, {KEY: "8"})
    boot(p)
    p.store[KEY] = "6"            # he sets it himself on the settings screen
    p.defaults[KEY] = "12"        # and a new build ships a different default
    boot(p)
    assert KEY not in p.removed
    assert p.store[KEY] == "6", "his own value was overwritten by a new default"

  def test_it_stays_his_on_every_later_boot(self):
    """The dangerous shape: once he edits, stored == remembered again on the NEXT boot, and a
    mechanism that only compared those two would decide it was untouched and clear it."""
    p = FakeParams({KEY: "8"}, {KEY: "8"})
    boot(p)
    p.store[KEY] = "6"
    boot(p)
    assert KEY in set(json.loads(p.store[BP_DEFAULTS_OWNED_KEY])["keys"])
    for new in ("12", "15", "3"):
      p.defaults[KEY] = new
      boot(p)
      assert p.store[KEY] == "6", f"his value was lost when the default moved to {new}"

  def test_one_setting_he_owns_does_not_freeze_the_others(self):
    p = FakeParams({KEY: "8", OTHER: "40"}, {KEY: "8", OTHER: "40"})
    boot(p)
    p.store[KEY] = "6"
    p.defaults[KEY] = "12"
    p.defaults[OTHER] = "70"
    boot(p)
    assert p.store[KEY] == "6"
    assert p.store[OTHER] == "70", "an untouched setting was held back by an unrelated owned one"


class TestScope:
  def test_his_lateral_tune_is_never_tracked(self):
    """Named exclusion, not a prefix accident. Being wrong here costs a tune he found on the road."""
    p = FakeParams({HIS_TUNE: "0.92"}, {HIS_TUNE: "1.0"})
    boot(p)
    boot(p)
    assert p.removed == []
    assert p.store[HIS_TUNE] == "0.92"

  def test_unrelated_upstream_keys_are_left_alone(self):
    p = FakeParams({"IsMetric": "0"}, {"IsMetric": "1"})
    boot(p)
    boot(p)
    assert p.removed == []
    assert p.store["IsMetric"] == "0"

  def test_our_prefixes_are_tracked(self):
    p = FakeParams({"SpeedLimitMaxSetSpeed": "85"}, {"SpeedLimitMaxSetSpeed": "85"})
    boot(p)
    p.defaults["SpeedLimitMaxSetSpeed"] = "100"
    boot(p)
    assert p.store["SpeedLimitMaxSetSpeed"] == "100"


class TestItDegradesSafely:
  def test_a_corrupt_record_does_not_clear_anything(self):
    """Unreadable state must read as 'no record', which seeds pessimistically rather than
    treating every setting as untouched."""
    p = FakeParams({KEY: "8", BP_DEFAULTS_SNAPSHOT_KEY: "not json"}, {KEY: "12"})
    _migrate_bp_new_defaults(p)
    assert p.removed == []

  def test_one_bad_key_does_not_cost_the_rest(self):
    p = FakeParams({KEY: "8", OTHER: "40"}, {KEY: "8", OTHER: "40"})
    boot(p)
    p.defaults[KEY] = "12"
    p.defaults[OTHER] = "70"
    real_remove = p.remove

    def remove(k):
      if k == KEY:
        raise RuntimeError("unwritable")
      real_remove(k)
    p.remove = remove

    boot(p)
    assert p.store[OTHER] == "70", "one bad key took the rest with it"
    assert BP_DEFAULTS_SNAPSHOT_KEY in p.store, "the record was skipped, so this reruns every boot"

  def test_a_key_with_no_declared_default_is_skipped(self):
    p = FakeParams({KEY: "8"}, {KEY: None})
    _migrate_bp_new_defaults(p)
    assert p.removed == []


class TestAKeyThatHasNeverBeenWritten:
  """run_migration runs BEFORE manager.py writes shipped defaults, so an unset key is normal.

  Every other test here starts from a store that already holds the key, which is what a driven car
  looks like -- but not what a fresh flash, a Reset All Params, or a newly declared key looks like on
  the boot that first sees it. Stringifying that None gives the literal "None", which matches no real
  default, so pessimistic seeding claims the key as his and nothing can ever hand it a new default
  again. That is this mechanism failing in exactly the way it was built to prevent.
  """

  def test_an_unset_key_is_not_claimed_as_his(self):
    p = FakeParams({}, {KEY: "8"})
    boot(p)
    assert KEY not in set(json.loads(p.store[BP_DEFAULTS_OWNED_KEY])["keys"])
    assert p.store[KEY] == "8"

  def test_a_fresh_device_still_takes_later_defaults(self):
    p = FakeParams({}, {KEY: "8"})
    boot(p)                       # first boot: nothing stored, manager.py materializes 8
    p.defaults[KEY] = "12"
    boot(p)
    assert p.store[KEY] == "12", "a fresh device was frozen out of every future default"

  def test_a_newly_declared_key_on_a_driven_car_still_takes_later_defaults(self):
    """The same shape on a car that has been driven for months: a key this branch has only just
    added is unset on the boot that introduces it, alongside keys with a long history."""
    p = FakeParams({KEY: "6"}, {KEY: "8"})
    boot(p)                       # his tuned value is claimed, correctly
    p.defaults[OTHER] = "40"      # a new build declares a key that has never existed here
    boot(p)
    p.defaults[OTHER] = "70"
    boot(p)
    assert p.store[KEY] == "6", "his tuned value was lost"
    assert p.store[OTHER] == "70", "a newly declared key never received a moved default"


class TestSettingItBackToTheRecommendationRestoresManagement:
  """Announced 2026-08-08: "On my next drive I will go through each setting, check the description
  for recommended value, and set my value to it."

  Without this, that drive is a trap. `owned` only ever grew, and owned keys are skipped forever --
  so every setting he had ever touched would freeze at today's number and never take another
  improvement, which is the opposite of what he is doing it for.
  """

  def test_a_key_he_returns_to_the_default_is_managed_again(self):
    p = FakeParams({KEY: "6"}, {KEY: "8"})
    boot(p)
    assert KEY in set(json.loads(p.store[BP_DEFAULTS_OWNED_KEY])["keys"]), "his 6 should be owned"

    p.store[KEY] = "8"          # he reads "Recommended: 8" and sets it
    boot(p)
    assert KEY not in set(json.loads(p.store[BP_DEFAULTS_OWNED_KEY])["keys"]), (
      "still owned after he matched the recommendation; it can never take a new default again")

    p.defaults[KEY] = "12"      # a later build moves it
    boot(p)
    assert p.store[KEY] == "12", "released key did not take the moved default"

  def test_a_key_he_actually_chose_stays_his(self):
    """The release must key on matching the SHIPPED value, not on him having touched it."""
    p = FakeParams({KEY: "6"}, {KEY: "8"})
    boot(p)
    p.defaults[KEY] = "12"
    boot(p)
    assert p.store[KEY] == "6", "his own value was taken away"
