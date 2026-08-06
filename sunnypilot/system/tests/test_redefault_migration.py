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
import subprocess

import pytest

from openpilot.sunnypilot.system.params_migration import (
  _BP_REDEFAULT_GROUPS, _migrate_bp_redefaulted,
)

# Flattened for the tests that do not care which branch a key came from.
_BP_REDEFAULTED = tuple(k for _, keys in _BP_REDEFAULT_GROUPS for k in keys)
_GENERATIONS = tuple(g for g, _ in _BP_REDEFAULT_GROUPS)
BP_DEFAULTS_GENERATION = _GENERATIONS[0]


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
    assert set(p.written["BPDefaultsGeneration"].split(",")) == set(_GENERATIONS)

  def test_it_touches_nothing_outside_the_list(self):
    """The list is the contract. Anything he tuned himself has to come through untouched."""
    # IcbmMaxTargetRise, not ...Drop: Drop is now cleared by icbm-2, and an example key that
    # quietly joins a group turns this test into a tautology.
    p = FakeParams({"FordLowSpeedFactor_ang": "0.92", "IcbmMaxTargetRise": "5"})
    _migrate_bp_redefaulted(p)
    assert "FordLowSpeedFactor_ang" not in p.removed
    assert "IcbmMaxTargetRise" not in p.removed
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
    p = FakeParams({"BPDefaultsGeneration": ",".join(_GENERATIONS), "SpeedLimitMode": 1})
    _migrate_bp_redefaulted(p)
    assert p.removed == [], "cleared on an ordinary boot"

    # he flashes the other branch, whose migration adds its own id, and comes back
    p.store["BPDefaultsGeneration"] = ",".join((self.OTHER_BRANCH,) + _GENERATIONS)
    _migrate_bp_redefaulted(p)
    assert p.removed == [], "the other branch's marker made this one run again"
    assert p.store["SpeedLimitMode"] == 1, "his setting did not survive the round trip"

  def test_it_adds_its_id_without_dropping_the_other_branch(self):
    p = FakeParams({"BPDefaultsGeneration": self.OTHER_BRANCH})
    _migrate_bp_redefaulted(p)
    assert set(p.written["BPDefaultsGeneration"].split(",")) == ({self.OTHER_BRANCH}
                                                                | set(_GENERATIONS))

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


class TestTheGroupsThemselves:
  def test_no_generation_id_is_reused(self):
    """Two groups sharing an id means the second never runs on a device that took the first."""
    assert len(_GENERATIONS) == len(set(_GENERATIONS))

  def test_no_key_appears_in_two_groups(self):
    """It would be cleared again by the second group on a device that took only the first, which is
    the re-clearing this whole shape exists to prevent."""
    assert len(_BP_REDEFAULTED) == len(set(_BP_REDEFAULTED))

  def test_every_default_that_ever_moved_is_accounted_for(self):
    """The bug this exists to catch, found on the road rather than here.

    A default is only ever read on a device that has NEVER stored the key. manager.py's
    "set unset params to their default value" loop writes every declared key to disk at boot, and
    these are PERSISTENT | BACKUP, so that first boot freezes the value shipping that day. Every
    later edit to params_keys.h goes to a file nobody reads.

    The first version of this test compared against upstream only, which missed the case that
    actually cost a drive: IcbmModelStopEnabled was ADDED on this branch at 1, deliberately shipped
    at 0 for a stretch, and set back to 1. It never differed from upstream -- upstream has never
    heard of it -- so an upstream diff called it clean while the car sat at red lights doing
    nothing. What matters is not "does it differ from upstream" but "has this value ever moved
    under a device that might have booted the older one".

    So: walk every revision of params_keys.h on this branch, and require any key that has ever held
    more than one default to be either cleared by a group or excluded here ON PURPOSE.
    """
    root = pathlib.Path(__file__).resolve().parents[3]
    try:
      base = subprocess.run(["git", "merge-base", "HEAD", "upstream/bp-7.0"], cwd=root,
                            capture_output=True, text=True, timeout=30, check=True).stdout.strip()
      revs = [base] + subprocess.run(
        ["git", "log", "--reverse", "--format=%H", f"{base}..HEAD", "--", "common/params_keys.h"],
        cwd=root, capture_output=True, text=True, timeout=30, check=True).stdout.split()
    except (OSError, subprocess.SubprocessError) as e:
      pytest.skip(f"needs the upstream remote to compare against: {e}")

    entry = re.compile(r'\{"(\w+)",\s*\{[^}]*?,\s*\w+,\s*"([^"]*)"\s*\}\s*\}')
    ever: dict[str, set[str]] = {}
    for rev in revs:
      src = subprocess.run(["git", "show", f"{rev}:common/params_keys.h"], cwd=root,
                           capture_output=True, text=True, timeout=30, check=True).stdout
      seen_here = 0
      for m in entry.finditer(src):
        ever.setdefault(m.group(1), set()).add(m.group(2))
        seen_here += 1
      assert seen_here > 100, f"{rev[:9]} parsed only {seen_here} keys; the regex has gone stale"

    # Keys whose shipped default moved and which are therefore NOT reaching his car. Since
    # 2026-08-05 the migration is closed -- *"I don't like ... having you change defaults anymore.
    # I will do it."* -- so this is not an excuse list, it is the standing answer to "which settings
    # do I have to tell him to toggle himself". Every entry needs a reason.
    his_to_toggle = {
      # Lateral tune he set himself on the settings screen. Whatever is stored IS his tune.
      "FordLowSpeedFactor_ang", "FordHighSpeedFactor_ang", "FordPrefLateralControl",
      # He confirmed on 2026-08-05 that these three work on the car -- he turned them on himself.
      "ShowBrakeStatus", "GreenLightAlert", "LeadDepartAlert",
      # Curve feel. He drove the current behavior on 2026-08-05 and called it good, so what is
      # stored is a tested state and the shipped numbers are not. Do not chase these.
      "SmartCruiseControlVisionEarliness", "SmartCruiseControlVisionLowSpeedFactor",
      "SmartCruiseControlVisionHighSpeedFactor",
      # Ceiling he asked for explicitly (100 mph). Shipped 85 first.
      "SpeedLimitMaxSetSpeed",
      # --- ICBM keys added on this branch whose default then moved. Each is a toggle or a number
      # on the ICBM settings screen, and each is his to set. ---
      # 1 -> 0 -> 1. Red lights and stop signs. Deliberately shipped off for a stretch. He says it
      # was on for his last drive, and the reason nothing happened was the shouldStop bug in
      # unconfirmed_lead, not this key.
      "IcbmModelStopEnabled",
      "IcbmResumeGateEnabled",   # 0 -> 1, standstill resume gate
      "IcbmLeadMaxDistance",     # 120 -> 180 m, how far the radar-blind detector looks
      "IcbmLeadMaxTtc",          # 40 -> 70 (4.0 -> 7.0 s)
      # 8 -> 12. NOT a response-rate limit, though the name reads like one: it is the step size
      # that keeps Ford COASTING instead of braking, because stock ACC treats one large drop in set
      # speed as a reason to brake hard and a series of small ones as a reason to coast. Net
      # deceleration is the same either way. So it cannot make anything happen earlier, and raising
      # it to chase the exit-ramp problem -- which I suggested -- would only trade coasting for
      # braking.
      "IcbmMaxTargetDrop",
    }

    moved = {k for k, vals in ever.items() if len(vals) > 1}
    unaccounted = moved - set(_BP_REDEFAULTED) - his_to_toggle
    assert not unaccounted, (
      f"shipped default moved and is unaccounted for: {sorted(unaccounted)}. His car still holds "
      "the value it booted first, so this change reaches nothing there. Add the key to "
      "his_to_toggle with a reason AND tell him which toggle to flip -- do not add a migration "
      "group, that list is closed.")

    # And the reverse: bookkeeping for a key whose default never actually moved is noise that makes
    # the real entries harder to trust.
    stale = (set(_BP_REDEFAULTED) | his_to_toggle) - moved
    assert not stale, f"listed as a changed default, but it has only ever had one: {sorted(stale)}"

  def test_a_group_already_taken_is_skipped_while_a_new_one_runs(self):
    """The point of separate groups. A branch adding its own must not re-clear this branch's."""
    first_gen, first_keys = _BP_REDEFAULT_GROUPS[0]
    p = FakeParams({"BPDefaultsGeneration": first_gen, **{k: "his value" for k in _BP_REDEFAULTED}})
    _migrate_bp_redefaulted(p)
    assert not (set(p.removed) & set(first_keys)), "re-cleared a group this device already took"
    for _, keys in _BP_REDEFAULT_GROUPS[1:]:
      assert set(keys) <= set(p.removed), "a group it had never taken was skipped"
