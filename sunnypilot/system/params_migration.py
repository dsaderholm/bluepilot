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
#   - GreenLightAlert, LeadDepartAlert, ShowBrakeStatus -- already on for him; clearing changes
#     nothing and only creates a chance to get it wrong.
#   - Everything added this session (the offset bands, lookahead, SCC-Map decel, pinned holds).
#     Those keys have never been written, so they already take their default with no help.
#
# ONCE PER GENERATION, not every boot, or he could never turn one of these off and keep it -- the
# opposite of what a settings screen is for.
#
# The generation string is branch-distinct on purpose. passing-assist-phase1 carries its own list
# and counts "1", "2", ...; sharing the counter would make the two branches re-run each other's
# migration on every switch. When they merge, union the lists and pick one counter.
BP_DEFAULTS_GENERATION: str = "icbm-1"

_BP_REDEFAULTED = (
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
)


def _migrate_bp_redefaulted(_params):
  if _params.get("BPDefaultsGeneration") == BP_DEFAULTS_GENERATION:
    return
  try:
    for key in _BP_REDEFAULTED:
      _params.remove(key)
    _params.put("BPDefaultsGeneration", BP_DEFAULTS_GENERATION, block=True)
    cloudlog.info(f"params_migration: took the new defaults for {len(_BP_REDEFAULTED)} settings")
  except Exception as e:
    cloudlog.exception(f"Error applying new defaults: {e}")


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
