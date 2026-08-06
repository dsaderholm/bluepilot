"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import json

from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.selfdrive.car.sync_sunnylink_params import CAR_LIST_JSON_OUT

ONROAD_BRIGHTNESS_MIGRATION_VERSION: str = "1.0"
ONROAD_BRIGHTNESS_TIMER_MIGRATION_VERSION: str = "1.0"

# index → seconds mapping for OnroadScreenOffTimer (SSoT)
ONROAD_BRIGHTNESS_TIMER_VALUES = {0: 3, 1: 5, 2: 7, 3: 10, 4: 15, 5: 30, **{i: (i - 5) * 60 for i in range(6, 16)}}
VALID_TIMER_VALUES = set(ONROAD_BRIGHTNESS_TIMER_VALUES.values())


def _migrate_car_platform_bundle(_params):
  bundle = _params.get("CarPlatformBundle")
  if bundle is None:
    return

  old_platform = bundle.get("platform")
  if not old_platform:
    return

  from opendbc.car.fingerprints import MIGRATION  # lazy: avoids heavy import at module level
  if old_platform not in MIGRATION:
    return

  new_platform = str(MIGRATION[old_platform])

  with open(CAR_LIST_JSON_OUT) as f:
    car_list = json.load(f)

  candidates = [(k, v) for k, v in car_list.items() if v.get("platform") == new_platform]
  if candidates:
    old_model = bundle.get("model")
    key, data = next(((k, v) for k, v in candidates if v.get("model") == old_model), candidates[0])
    bundle = {**data, "name": key}
  else:
    bundle["platform"] = new_platform

  _params.put("CarPlatformBundle", bundle, block=True)
  cloudlog.info(f"params_migration: CarPlatformBundle migrated {old_platform!r} -> {new_platform!r}")


# BluePilot: params whose SHIPPED DEFAULT changed on this branch, cleared so the new one applies.
#
# "I never want you to change settings anymore, just defaults." This is the defaults half, and the
# whole point is that it CLEARS rather than writes -- `remove` makes the next read fall through to
# params_keys.h, so a default is stated in exactly one place. Writing the value here would make
# this file a second source of truth and guarantee the two drift.
#
# CLOSED LIST. 2026-08-05: *"I don't like running commands or having you change defaults anymore.
# I will do it."* icbm-1 already ran on the car and stays because removing it changes nothing there,
# but NOTHING NEW GOES IN HERE. He manages his own settings from the settings screen.
#
# That does not make the underlying problem go away, it moves who acts on it. A shipped default only
# ever reaches a device that has never stored the key, and manager.py's "set unset params to their
# default value" loop stores every declared key on the first boot that knows about it. So on his car
# a changed default reaches NOTHING -- including for keys added on this branch, where "it has never
# been written" is true exactly once and false from his next flash onward.
#
# The obligation is therefore to TELL HIM, in the message that ships the change: this default moved,
# your car still has the old value, here is the toggle. Never to reach in and clear it.
# test_every_default_that_ever_moved_is_accounted_for keeps the list of which keys those are.
#
# ONCE PER GENERATION, not every boot, or he could never turn one of these off and keep it -- the
# opposite of what a settings screen is for.
#
# The generation id is branch-distinct, and the marker holds a SET of the ids that have been
# applied rather than the last one written.
#
# That second half is the whole point and the first draft got it backwards. Both branches write the
# same BPDefaultsGeneration key, so with a single value each branch's boot INVALIDATES the other's
# marker: flash passing-assist, come back to this branch, and its migration runs again -- clearing
# settings he had deliberately changed since. Branch-distinct ids did not prevent that, they
# guaranteed it. Demonstrated before this was written: SpeedLimitMode set back to "information"
# survived a normal boot and did not survive a round trip through the other branch.
#
# As a set, neither branch can invalidate the other, applying twice is a no-op, and merging the two
# branches needs nothing more than both ids being present.
# ONE GROUP PER BRANCH, each with its own id, and a device takes each group exactly once.
#
# Not one merged list with one id, which was the obvious shape and is wrong for the same reason the
# single-valued marker was: passing-assist would carry this branch's four keys in its own list, so
# a device that took them here would take them AGAIN on its first boot over there -- clearing
# whatever he had changed in between. Separate groups make each set of defaults a thing that
# happens once on this device, whatever order the branches are flashed in.
#
# A branch adds a tuple here and nothing else. Merging branches is concatenation.
_BP_REDEFAULT_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
 ("icbm-1", (
  # off -> assist. ICBM's whole job is driving the set speed toward the posted limit, and
  # "information" means show a sign and do nothing.
  "SpeedLimitMode",
  # off -> by-limit. Without this the banded offsets never apply and the car drives every posted
  # limit exactly, which is not how he drives and not what he asked for.
  "SpeedLimitOffsetType",
  # off -> on. The map curve controller, which is the only thing that can see an off-ramp bend
  # before the camera does.
  "SmartCruiseControlMap",
  # off -> on. Camera curve control; on for him already in all likelihood, but it is the pair to
  # SCC-Map and shipping one without the other is not a state anyone chose.
  "SmartCruiseControlVision",
 )),
)


def _applied_generations(_params) -> set[str]:
  """Which generation ids this device has already taken. See BP_DEFAULTS_GENERATION."""
  try:
    raw = _params.get("BPDefaultsGeneration") or ""
  except Exception:  # noqa: BLE001 - unreadable means "none applied", which is the safe reading
    return set()
  return {g for g in str(raw).split(",") if g}


def _migrate_bp_redefaulted(_params):
  applied = _applied_generations(_params)
  taken: set[str] = set()
  cleared = 0
  for generation, keys in _BP_REDEFAULT_GROUPS:
    if generation in applied:
      continue
    # Per key, not one try around the loop. A single unknown or unreadable key used to abandon the
    # rest of the list AND skip the marker, so the whole migration retried on every boot -- and a
    # migration that runs every boot is the one thing this must never be.
    for key in keys:
      try:
        _params.remove(key)
        cleared += 1
      except Exception as e:  # noqa: BLE001
        cloudlog.exception(f"params_migration: could not clear {key}: {e}")
    taken.add(generation)

  if not taken:
    return
  try:
    _params.put("BPDefaultsGeneration", ",".join(sorted(applied | taken)), block=True)
    cloudlog.info(f"params_migration: took the new defaults for {cleared} settings "
                  f"({', '.join(sorted(taken))})")
  except Exception as e:  # noqa: BLE001
    cloudlog.exception(f"Error recording the defaults generation: {e}")


# BluePilot: take a new shipped default ONLY for settings he has never touched.
#
# 2026-08-05, and this is the actual rule rather than the blunt version above: *"If I have never
# changed a value, great, I will get the new default. If I have, then it shouldn't change."*
#
# That is not expressible with the generation lists. manager.py's "set unset params to their default
# value" loop writes every declared key to disk on the first boot that knows about it, so by the
# time anything can look, "never touched" and "deliberately set to exactly that" are the same bytes
# on disk. The generation approach could only clear blindly, which is why the list had to be closed.
#
# The missing information is what the default WAS when he was last handed one. Record it, and the
# comparison becomes exact:
#
#   stored != remembered  ->  he changed it. His now, permanently; never considered again.
#   stored == remembered  ->  untouched. If the shipped default has since moved, clear the key so
#                             the next read falls through to params_keys.h, and remember the new one.
#
# SEEDING IS DELIBERATELY PESSIMISTIC. On the first boot with this mechanism there is no record, so
# every tracked key is assumed to be HIS and nothing is cleared. It costs one boot of staleness and
# it cannot ever overwrite something he chose -- the wrong side of that trade is the one that loses
# a tune he drove to find. From the boot after, this tracks exactly.
#
# The one indistinguishable case is him setting a value to exactly the default he already had, which
# is harmless: the outcome is the same number either way.
BP_DEFAULTS_SNAPSHOT_KEY = "BPDefaultsSnapshot"
BP_DEFAULTS_OWNED_KEY = "BPDefaultsOwned"

# Prefixes this fork ships defaults for. Anything the OWNER's own tune lives in is excluded by name
# below -- being wrong there costs him a steering tune he found on the road.
#
# PassingAssist and Show are here even though no such key exists on this branch, because the branch
# that owns them cannot usefully add them: it rebases onto this one, so a prefix added over there
# would be reverted by the next rebase. Seven PassingAssist* defaults and five Show* defaults have
# already moved with nothing tracking them, which meant the new value could not reach his car and
# the only recourse was a hand-written note in a test. This is where that stops.
#
# Adding a prefix does NOT retroactively fix a key that is already stale -- pessimistic seeding
# claims anything currently differing from its shipped default as his. It fixes every move from
# here on, which is the part that was unbounded.
_BP_TRACKED_PREFIXES = ("Icbm", "SmartCruiseControl", "SpeedLimit", "PassingAssist", "Show")
_BP_NEVER_TRACKED = frozenset({
  # His lateral tune. The defaults happen to match what he runs; that is not a reason to manage it.
  "FordLowSpeedFactor_ang", "FordHighSpeedFactor_ang", "FordHighSpeedDampening_ang",
  "FordPrefLateralControl", "lane_change_factor_high_ang",
})


def _bp_tracked_keys(_params) -> list[str]:
  try:
    keys = [k.decode() if isinstance(k, bytes) else str(k) for k in _params.all_keys()]
  except Exception:  # noqa: BLE001
    return []
  return sorted(k for k in keys
                if k.startswith(_BP_TRACKED_PREFIXES) and k not in _BP_NEVER_TRACKED)


def _load_json_param(_params, key) -> dict:
  try:
    raw = _params.get(key)
    if isinstance(raw, bytes):
      raw = raw.decode()
    if isinstance(raw, dict):
      return raw
    return json.loads(raw) if raw else {}
  except Exception:  # noqa: BLE001 - unreadable means "no record", which seeds pessimistically
    return {}


def _migrate_bp_new_defaults(_params):
  """Hand over shipped defaults that moved, for settings he has not personally set."""
  remembered = _load_json_param(_params, BP_DEFAULTS_SNAPSHOT_KEY)
  owned = set(_load_json_param(_params, BP_DEFAULTS_OWNED_KEY).get("keys", []))

  took: list[str] = []
  for key in _bp_tracked_keys(_params):
    if key in owned:
      continue
    # Per key. One unreadable key must not abandon the rest, and must not skip the write below --
    # a migration that fails to record its state runs again every boot, which is the one thing this
    # must never do.
    try:
      shipped = _params.get_default_value(key)
      if shipped is None:
        continue
      shipped = str(shipped)
      stored = _params.get(key)
      stored = str(stored.decode() if isinstance(stored, bytes) else stored)

      if key not in remembered:
        # FIRST SIGHT, and the seeding has to claim as well as record.
        #
        # manager.py has already written the shipped default to every declared key on a car that has
        # been driven, so a stored value that DIFFERS from it can only be one he set himself. There
        # is no later boot on which that becomes visible again -- from the next boot on, stored ==
        # remembered looks exactly like "untouched", and the elif below would hand his value away.
        #
        # Recording alone was not enough. It bought exactly one boot: everything he had tuned before
        # this shipped -- his whole ICBM tune, the 100 mph ceiling, the curve feel he drove to find
        # -- was reset to the shipped default on the second boot, and not only when a default had
        # moved. That is the wipe that already happened to him once.
        #
        # A key untouched since BEFORE a default moved in some earlier build can hold a stale
        # default and get claimed here too. That costs staleness on a setting he can still change
        # himself, which is the side this file already says every ambiguous case must fall on.
        remembered[key] = stored
        if stored != shipped:
          owned.add(key)
      elif stored != remembered[key]:
        owned.add(key)                      # he moved it: his from here on
      elif stored != shipped:
        _params.remove(key)                 # untouched and the default moved: hand it over
        remembered[key] = shipped
        took.append(key)
    except Exception as e:  # noqa: BLE001
      cloudlog.exception(f"params_migration: could not evaluate default for {key}: {e}")

  try:
    _params.put(BP_DEFAULTS_SNAPSHOT_KEY, json.dumps(remembered), block=True)
    _params.put(BP_DEFAULTS_OWNED_KEY, json.dumps({"keys": sorted(owned)}), block=True)
    if took:
      cloudlog.info(f"params_migration: took new defaults for {len(took)} untouched settings: "
                    f"{', '.join(took)}")
  except Exception as e:  # noqa: BLE001
    cloudlog.exception(f"Error recording the defaults snapshot: {e}")


BP_LATERAL_SCHEME_PARAMS_MIGRATION_VERSION: str = "1"

# (old key, new key) -- old keys stay declared in common/params_keys.h (harmless orphans) so their
# stored values are still readable here. lane_change_factor_high intentionally has no _ang entry:
# the old curvature-tuned default (0.85) is the wrong direction for angle mode, so _ang just takes
# its own fresh params_keys.h default instead of inheriting a stale value.
_BP_LATERAL_SCHEME_PARAM_RENAMES = (
  ("enable_human_turn_detection", "enable_human_turn_detection_curv"),
  ("lane_change_factor_high", "lane_change_factor_high_curv"),
  ("pc_blend_ratio_high_C_UI", "pc_blend_ratio_high_C_UI_curv"),
  ("pc_blend_ratio_low_C_UI", "pc_blend_ratio_low_C_UI_curv"),
  ("enable_lane_positioning", "enable_lane_positioning_curv"),
  ("custom_path_offset", "custom_path_offset_curv"),
  ("enable_lane_full_mode", "enable_lane_full_mode_curv"),
  ("custom_profile", "custom_profile_curv"),
  ("LC_PID_gain_UI", "LC_PID_gain_UI_curv"),
  ("FordAngleLowSpeedFactor", "FordLowSpeedFactor_ang"),
  ("FordAngleHighSpeedFactor", "FordHighSpeedFactor_ang"),
)


def _migrate_bp_lateral_scheme_params(_params):
  # Marker is a STRING param (like the OnroadScreenOff*Migrated flags above): the original BOOL
  # declaration made put("1") raise a type error inside the except below, so the marker never
  # stuck and this re-ran (and re-seeded, clobbering user-tuned values) on every boot.
  if _params.get("BPLateralSchemeParamsMigratedV1") == BP_LATERAL_SCHEME_PARAMS_MIGRATION_VERSION:
    return

  try:
    for old_key, new_key in _BP_LATERAL_SCHEME_PARAM_RENAMES:
      # Never overwrite a value that has already been written (by a previous migration run or by
      # the user tuning the new key) -- makes re-runs harmless, and lets in-field devices that hit
      # the every-boot re-seed keep whatever they have now instead of taking one final clobber.
      if _params.get(new_key) is not None:
        cloudlog.info(f"params_migration: {new_key} already set, not re-seeding")
        continue
      old_val = _params.get(old_key, return_default=True)
      _params.put(new_key, old_val, block=True)
      cloudlog.info(f"params_migration: seeded {new_key} from {old_key} ({old_val})")

    _params.put("BPLateralSchemeParamsMigratedV1", BP_LATERAL_SCHEME_PARAMS_MIGRATION_VERSION, block=True)
    cloudlog.info("params_migration: BP lateral scheme param split complete")
  except Exception as e:
    cloudlog.exception(f"Error migrating BP lateral scheme params: {e}")


def run_migration(_params):
  # migrate OnroadScreenOffBrightness
  if _params.get("OnroadScreenOffBrightnessMigrated") != ONROAD_BRIGHTNESS_MIGRATION_VERSION:
    try:
      val = _params.get("OnroadScreenOffBrightness", return_default=True)
      if val >= 2:  # old: 5%, new: Screen Off
        new_val = val + 1
        _params.put("OnroadScreenOffBrightness", new_val, block=True)
        log_str = f"Successfully migrated OnroadScreenOffBrightness from {val} to {new_val}."
      else:
        log_str = "Migration not required for OnroadScreenOffBrightness."

      _params.put("OnroadScreenOffBrightnessMigrated", ONROAD_BRIGHTNESS_MIGRATION_VERSION, block=True)
      cloudlog.info(log_str + f" Setting OnroadScreenOffBrightnessMigrated to {ONROAD_BRIGHTNESS_MIGRATION_VERSION}")
    except Exception as e:
      cloudlog.exception(f"Error migrating OnroadScreenOffBrightness: {e}")

  # migrate OnroadScreenOffTimer
  if _params.get("OnroadScreenOffTimerMigrated") != ONROAD_BRIGHTNESS_TIMER_MIGRATION_VERSION:
    try:
      val = _params.get("OnroadScreenOffTimer", return_default=True)
      if val not in VALID_TIMER_VALUES:
        _params.put("OnroadScreenOffTimer", 15, block=True)
        log_str = f"Successfully migrated OnroadScreenOffTimer from {val} to 15 (default)."
      else:
        log_str = "Migration not required for OnroadScreenOffTimer."

      _params.put("OnroadScreenOffTimerMigrated", ONROAD_BRIGHTNESS_TIMER_MIGRATION_VERSION, block=True)
      cloudlog.info(log_str + f" Setting OnroadScreenOffTimerMigrated to {ONROAD_BRIGHTNESS_TIMER_MIGRATION_VERSION}")
    except Exception as e:
      cloudlog.exception(f"Error migrating OnroadScreenOffTimer: {e}")

  _migrate_car_platform_bundle(_params)

  # BluePilot: split lateral-tuning params by control scheme (curvature vs angle)
  _migrate_bp_lateral_scheme_params(_params)

  # BluePilot: the legacy generation lists. Closed -- icbm-1 has already run on the car, and the
  # snapshot pass below supersedes this entirely for anything new.
  _migrate_bp_redefaulted(_params)

  # BluePilot: hand over shipped defaults that moved, for settings he never set himself.
  # MUST run before manager.py's "set unset params to their default value" loop, which it does --
  # manager calls run_migration first and then materializes whatever this cleared.
  _migrate_bp_new_defaults(_params)
