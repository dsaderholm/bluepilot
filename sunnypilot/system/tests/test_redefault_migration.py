"""BluePilot: taking a changed shipped default without touching anything the driver set.

The rule this exists to enforce: *"I never want you to change settings anymore, just defaults."*

Every settings key is PERSISTENT, so once a value is stored the default in params_keys.h stops
meaning anything on that device -- a changed default reaches a car that has been driven exactly
never, while the code and the settings screen describe behaviour it does not have. This migration
closes that gap, and the test guards the two ways it could go wrong: writing a value it should not,
or running more than once.
"""
import pathlib
import re

from openpilot.sunnypilot.system.params_migration import (
  _BP_REDEFAULTED, BP_DEFAULTS_GENERATION, _migrate_bp_redefaulted,
)


class FakeParams:
  """Records removes and puts separately, because the difference between them is the whole point."""

  def __init__(self, store=None):
    self.store = dict(store or {})
    self.removed: list[str] = []
    self.written: dict = {}

  def get(self, key, *a, **k):
    return self.store.get(key)

  def remove(self, key):
    self.removed.append(key)
    self.store.pop(key, None)

  # block=, matching params_pyx. A stub without it turns block=True into a TypeError that the
  # caller's own except-clause swallows, so the write silently never happens.
  def put(self, key, value, block=False):
    self.written[key] = value
    self.store[key] = value


class TestItClearsRatherThanWrites:
  def test_every_redefaulted_key_is_removed(self):
    p = FakeParams({k: "stale" for k in _BP_REDEFAULTED})
    _migrate_bp_redefaulted(p)
    assert set(p.removed) == set(_BP_REDEFAULTED)

  def test_it_writes_nothing_but_the_generation_marker(self):
    """A value written here would make this file a second source of truth for the default, and the
    two would drift. The default belongs in params_keys.h and nowhere else."""
    p = FakeParams({k: "stale" for k in _BP_REDEFAULTED})
    _migrate_bp_redefaulted(p)
    assert set(p.written) == {"BPDefaultsGeneration"}
    assert p.written["BPDefaultsGeneration"] == BP_DEFAULTS_GENERATION

  def test_it_touches_nothing_outside_the_list(self):
    """The list is the contract. Anything he tuned himself has to come through untouched."""
    p = FakeParams({"FordLowSpeedFactor_ang": "0.92", "IcbmMaxTargetDrop": "12"})
    _migrate_bp_redefaulted(p)
    assert "FordLowSpeedFactor_ang" not in p.removed
    assert "IcbmMaxTargetDrop" not in p.removed
    assert p.store["FordLowSpeedFactor_ang"] == "0.92"


class TestItRunsOncePerGeneration:
  def test_a_second_run_does_nothing(self):
    """Otherwise he could never turn one of these off and keep it, which is the opposite of what a
    settings screen is for."""
    p = FakeParams({k: "stale" for k in _BP_REDEFAULTED})
    _migrate_bp_redefaulted(p)
    p.removed.clear()
    p.written.clear()
    _migrate_bp_redefaulted(p)
    assert p.removed == [] and p.written == {}

  def test_an_older_generation_is_re_applied(self):
    p = FakeParams({"BPDefaultsGeneration": "something-older"})
    _migrate_bp_redefaulted(p)
    assert set(p.removed) == set(_BP_REDEFAULTED)


class TestTheListItself:
  def test_no_lateral_tuning_is_ever_cleared(self):
    """His steering tune is his. The shipped defaults happen to match what he runs, but this file
    reaching into lateral settings is exactly what he asked it to stop doing."""
    forbidden = {"FordLowSpeedFactor_ang", "FordHighSpeedFactor_ang",
                 "FordHighSpeedDampening_ang", "FordPrefLateralControl",
                 "lane_change_factor_high_ang"}
    assert not (set(_BP_REDEFAULTED) & forbidden)

  def test_no_duplicates(self):
    assert len(_BP_REDEFAULTED) == len(set(_BP_REDEFAULTED))

  def test_every_key_is_declared(self):
    """A typo here removes nothing and reports success, which is the worst kind of failure."""
    root = pathlib.Path(__file__).resolve().parents[3]
    declared = set(re.findall(r'\{"(\w+)",',
                              (root / "common" / "params_keys.h").read_text(encoding="utf-8")))
    for key in _BP_REDEFAULTED:
      assert key in declared, f"{key} is not declared in params_keys.h"
    assert "BPDefaultsGeneration" in declared


class TestTheOtherBranchCannotUndoThis:
  """The first draft used a branch-distinct generation string on the reasoning that it would stop
  the two branches re-running each other's migration. It did the exact opposite.

  Both branches write the SAME BPDefaultsGeneration key. With a single value in it, a boot on
  passing-assist leaves "2" behind, this branch sees a value that is not "icbm-1", and re-runs --
  clearing settings he had deliberately changed since. Every branch switch, forever, in both
  directions. So the marker holds a SET of applied ids instead.
  """

  OTHER_BRANCH = "2"   # what a boot on passing-assist-phase1 writes

  def test_a_setting_he_changed_survives_a_round_trip_through_the_other_branch(self):
    p = FakeParams({"BPDefaultsGeneration": BP_DEFAULTS_GENERATION, "SpeedLimitMode": 1})
    _migrate_bp_redefaulted(p)
    assert p.removed == [], "cleared on an ordinary boot"

    # he flashes the other branch, whose migration adds its own id, and comes back
    p.store["BPDefaultsGeneration"] = f"{self.OTHER_BRANCH},{BP_DEFAULTS_GENERATION}"
    _migrate_bp_redefaulted(p)
    assert p.removed == [], "the other branch's marker made this one run again"
    assert p.store["SpeedLimitMode"] == 1, "his setting did not survive the round trip"

  def test_it_adds_its_id_without_dropping_the_other_branch(self):
    p = FakeParams({"BPDefaultsGeneration": self.OTHER_BRANCH})
    _migrate_bp_redefaulted(p)
    assert set(p.written["BPDefaultsGeneration"].split(",")) == {self.OTHER_BRANCH,
                                                                BP_DEFAULTS_GENERATION}

  def test_a_device_that_has_never_run_either_still_applies(self):
    p = FakeParams({k: "stale" for k in _BP_REDEFAULTED})
    _migrate_bp_redefaulted(p)
    assert set(p.removed) == set(_BP_REDEFAULTED)


class TestOneBadKeyDoesNotCostTheRest:
  def test_the_remaining_keys_are_still_cleared(self):
    """One try around the whole loop meant an unknown key abandoned every key after it AND skipped
    the marker -- so the migration retried on every boot, which is the one thing it must never do.
    """
    p = FakeParams({k: "stale" for k in _BP_REDEFAULTED})
    first = _BP_REDEFAULTED[0]
    real_remove = p.remove

    def remove(key):
      if key == first:
        raise RuntimeError("unknown key")
      real_remove(key)
    p.remove = remove

    _migrate_bp_redefaulted(p)
    assert set(p.removed) == set(_BP_REDEFAULTED[1:]), "one bad key took the rest with it"
    assert "BPDefaultsGeneration" in p.written, "the marker was skipped, so this runs again on every boot"
