"""BluePilot: the LEGACY generation-list migration, plus the guard on which defaults are tracked.

_BP_REDEFAULT_GROUPS is closed. It cleared a fixed list of keys once per generation id, which was
the best available before there was any way to tell "he never touched this" from "he set it to
exactly that" -- and being unable to tell is why it had to stop. icbm-1 has already run on the car,
so it stays and is still tested here; nothing new goes in it.

What replaced it is _migrate_bp_new_defaults, tested in test_new_defaults_snapshot.py, implementing
his actual rule: *"If I have never changed a value, great, I will get the new default. If I have,
then it shouldn't change."*

The guard at the bottom of this file survives the handover with a different question. It used to
ask "was a migration written for this moved default"; it now asks "is this key tracked at all",
because a default that moves outside the tracked prefixes still reaches his car exactly never.
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
    # Any key not in a group works as the example. IcbmMaxTargetDrop was briefly the wrong choice
    # here -- it joined a group, which silently turned this test into a tautology.
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

    # A moved default is now handled automatically by _migrate_bp_new_defaults: untouched settings
    # take it, ones he set stay his. So the question this guard asks changed. It is no longer "was a
    # migration written" -- it is "is the key inside the tracked set at all". A default that moves
    # for a key no prefix covers reaches his car exactly never, silently, which is the original bug
    # wearing different clothes.
    from openpilot.sunnypilot.system.params_migration import (
      _BP_TRACKED_PREFIXES, _BP_NEVER_TRACKED,
    )

    # Moved defaults deliberately outside the tracked set. Each needs a reason.
    untracked_on_purpose = {
      # His lateral tune, set on the settings screen. Excluded by name in _BP_NEVER_TRACKED so a
      # prefix change can never quietly pull it in.
      "FordLowSpeedFactor_ang", "FordHighSpeedFactor_ang", "FordPrefLateralControl",
      # He confirmed on 2026-08-05 that both work on the car -- he turned them on himself, so
      # whatever is stored is already what he wants. They carry no tracked prefix and are not worth
      # inventing one for. (ShowBrakeStatus was here too until "Show" became a tracked prefix; the
      # overlap assertion below is what caught that it had to move.)
      "GreenLightAlert", "LeadDepartAlert",
    }

    moved = {k for k, vals in ever.items() if len(vals) > 1}
    tracked = {k for k in moved if k.startswith(_BP_TRACKED_PREFIXES) and k not in _BP_NEVER_TRACKED}
    unaccounted = moved - tracked - untracked_on_purpose
    assert not unaccounted, (
      f"shipped default moved for a key nothing tracks: {sorted(unaccounted)}. It will not reach "
      "his car and nothing will say so. Bring it under _BP_TRACKED_PREFIXES, or add it to "
      "untracked_on_purpose with a reason.")

    overlap = tracked & untracked_on_purpose
    assert not overlap, f"listed as untracked on purpose but the prefixes do track it: {sorted(overlap)}"

    stale = untracked_on_purpose - moved
    assert not stale, f"listed as a changed default, but it has only ever had one: {sorted(stale)}"

  def test_a_group_already_taken_is_skipped_while_a_new_one_runs(self):
    """The point of separate groups. A branch adding its own must not re-clear this branch's."""
    first_gen, first_keys = _BP_REDEFAULT_GROUPS[0]
    p = FakeParams({"BPDefaultsGeneration": first_gen, **{k: "his value" for k in _BP_REDEFAULTED}})
    _migrate_bp_redefaulted(p)
    assert not (set(p.removed) & set(first_keys)), "re-cleared a group this device already took"
    for _, keys in _BP_REDEFAULT_GROUPS[1:]:
      assert set(keys) <= set(p.removed), "a group it had never taken was skipped"
