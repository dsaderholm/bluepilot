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
# Deliberately the narrowest list that changes anything. Only keys whose params_keys.h value moved
# AND that are likely already stored on the car appear. Excluded on purpose:
#   - FordLow/HighSpeedFactor_ang, FordPrefLateralControl -- his own steering tune. The defaults now
#     match what he runs, but clearing them is this file reaching into his lateral settings, which
#     is exactly what he asked it to stop doing.
#   - ShowBrakeStatus -- he can see the brake pill on the road, so this one demonstrably already
#     holds the value he wants. Clearing it would land on the same 1 and gain nothing.
#     (GreenLightAlert and LeadDepartAlert were excluded here too, on the same "already on for him"
#     reasoning, and that was wrong -- see icbm-2.)
#   - Everything added this session (the offset bands, lookahead, SCC-Map decel, pinned holds).
#     Those keys have never been written, so they already take their default with no help.
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
 ("icbm-2", (
  # Keys ADDED on this branch whose default I then changed again. icbm-1 skipped these on the
  # reasoning that a new key "has never been written, so it already takes its default with no
  # help". That is true exactly once. manager.py writes every unset param to disk at boot, so the
  # value shipping on the day he first flashed a build containing the key is the value frozen on
  # his car -- and he has flashed this branch after nearly every fix. Every later edit to
  # params_keys.h since then has gone to a file nobody reads.
  #
  # THE ONE THAT MATTERS. Shipped 1, then deliberately shipped 0 ("ship the model-stop path off",
  # 2ed220b6a), then back to 1. If he flashed anywhere in that window he has a stored 0, and
  # model_stop_enabled is the outermost gate in unconfirmed_lead -- a false there skips the entire
  # block, so the car does nothing at a red light or a stop sign and there is no alert to say why.
  # That is the standing "I still haven't seen it do anything for traffic lights and stop signs".
  "IcbmModelStopEnabled",
  # 0 -> 1. The standstill resume gate, shipped off for one commit and enabled the next.
  "IcbmResumeGateEnabled",
  # 120 -> 180 m and 40 -> 70 (4.0 -> 7.0 s TTC). A stale pair here does not disable anything, it
  # quietly shortens how far ahead the radar-blind detector is allowed to look -- which reads as
  # "it warned me late" rather than as a setting being wrong.
  "IcbmLeadMaxDistance",
  "IcbmLeadMaxTtc",
  # 85 -> 100 mph. He asked for 100 explicitly; at a stored 85 the ceiling clips.
  "SpeedLimitMaxSetSpeed",
  # 8 -> 12. How fast ICBM is allowed to walk the set speed DOWN. Frozen at 8 the car gives up
  # ground more slowly than every curve controller feeding it expects, which is the shape of the
  # open "still took an exit ramp going 80" report.
  "IcbmMaxTargetDrop",
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

  # BluePilot: take shipped defaults that changed, without touching anything he set himself
  _migrate_bp_redefaulted(_params)
