"""
BluePilot: Publishes BP-specific messages from card.py.

Centralizes controllerStateBP and carStateBP message publishing so that
card.py only needs a single function call instead of inline BP blocks.
"""

import time

import cereal.messaging as messaging
from opendbc.car import structs
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.car.helpers import convert_to_capnp

_SETTINGS_INTERVAL = 5.0  # re-read params at most every 5 s
_settings_last_read: float = 0.0
_settings_cache: dict = {}
# Latched off for the drive if either publisher ever raises -- see publish_controller_state_bp.
_publish_failed: bool = False
_car_state_publish_failed: bool = False


def _get_bool(p: Params, key: str, default: bool = False) -> bool:
  try:
    return p.get_bool(key)
  except Exception:
    return default


def _get_float(p: Params, key: str, default: float = 0.0) -> float:
  try:
    v = p.get(key, return_default=True)
    if v in (None, b"", ""):
      return default
    if isinstance(v, bytes):
      v = v.decode("utf-8", errors="replace").strip("\x00")
    return float(v)
  except Exception:
    return default


def _get_int(p: Params, key: str, default: int = 0) -> int:
  try:
    v = p.get(key, return_default=True)
    if v in (None, b"", ""):
      return default
    if isinstance(v, bytes):
      v = v.decode("utf-8", errors="replace").strip("\x00")
    return max(0, min(255, int(float(v))))
  except Exception:
    return default


def _get_str(p: Params, key: str, default: str = "") -> str:
  try:
    v = p.get(key, return_default=True)
    if v is None:
      return default
    return v.decode("utf-8", errors="replace").strip("\x00") if isinstance(v, bytes) else str(v).strip()
  except Exception:
    return default


def _refresh_settings_cache() -> dict:
  """BluePilot-menu settings snapshot -- see custom.capnp ControllerStateBP for the field-by-field
  param-key mapping and the field-retirement convention. Keep this dict's keys in sync with the
  capnp/structs.py bms* field names (1:1, same order, same grouping comments)."""
  p = Params()
  return {
    # --- System ---
    "bmsUiDebugLogging":               _get_bool(p, "BPUIDebugLog"),
    "bmsConnectBackend":               _get_int(p, "BPConnectBackend", 0),
    "bmsWebRoutesServerEnabled":       _get_bool(p, "EnableWebRoutesServer"),
    "bmsPreferredWifiNetwork":         _get_str(p, "WifiFavoriteSSID"),
    # --- Vehicle ---
    "bmsShowBlueCruiseUiOnCluster":    _get_bool(p, "send_hands_free_cluster_msg"),
    "bmsTwelveVBatteryLimit":          _get_float(p, "vbatt_pause_charging", 11.8),
    # --- Visuals ---
    "bmsHideOnroadBorder":             _get_bool(p, "BPHideOnroadBorder"),
    "bmsDisableLaneLineStatusColor":   _get_bool(p, "BPDisableLaneLineStatusColor"),
    "bmsMinimalDrivingView":           _get_bool(p, "BPHideCameraView"),
    "bmsEightBitRacerTheme":           _get_str(p, "BPThemePack").strip().lower() == "rad_racer",
    "bmsRainbowLaneLines":             _get_bool(p, "BPRainbowLines"),
    "bmsShowBlindspotOverlay":         _get_bool(p, "ShowBlindspotOverlay", True),
    "bmsShowBrakeStatus":              _get_bool(p, "ShowBrakeStatus"),
    "bmsShowConfidenceBall":           _get_bool(p, "BPShowConfidenceBall", True),
    "bmsAnimateSteeringWheel":         _get_bool(p, "BPAnimateSteeringWheel", True),
    "bmsWheelIconStyle":               _get_int(p, "BPSteeringWheelIconStyle", 0),
    "bmsShowRadarLeadOverlay":         _get_bool(p, "FordPrefShowRadarLeadOverlay", True),
    "bmsRadarOverlaySize":             _get_int(p, "FordPrefRadarOverlaySize", 1),
    "bmsShowHybridBatteryStatus":      _get_bool(p, "FordPrefHybridBatteryStatus"),
    "bmsShowHybridPowerFlow":          _get_bool(p, "FordPrefHybridPowerFlow"),
    "bmsHybridDriveGaugeSize":         _get_int(p, "FordPrefHybridDriveGaugeSize", 1),
    "bmsHybridGaugeStyle":             _get_int(p, "FordPrefGaugeStyle", 0),
    "bmsHybridPowerFlowStyleRound":    _get_bool(p, "FordPrefHybridPowerFlowAlternate"),
    "bmsLowerRightDisplay":            _get_int(p, "mici_complication", 0),
    "bmsRainbowMode":                  _get_bool(p, "RainbowMode"),
    "bmsHideOnroadFade":               _get_bool(p, "mici_hide_onroad_fade"),
    # --- Longitudinal Tuning ---
    "bmsBypassBpLongitudinalControl":  _get_bool(p, "disable_BP_long_UI"),
    "bmsDisableDownhillCompensation":  _get_bool(p, "disable_downhill_comp_UI"),
    "bmsDisableFordRadarVisionOnly":   _get_bool(p, "disable_ford_radar_UI"),
    # --- Lateral Tuning ---
    "bmsDisableBpLateralControl":      _get_bool(p, "disable_BP_lat_UI"),
    "bmsPrimaryControlVariable":       _get_int(p, "FordPrefLateralControl", 0),
    "bmsDisableLaneChangeUnderSpeed":  _get_bool(p, "BlinkerPauseLaneChange"),
    "bmsMinimumSpeedToPauseLaneChange": _get_int(p, "BlinkerMinLateralControlSpeed", 20),
    "bmsShowLateralControlMode":       _get_bool(p, "BpShowLateralControl"),
    # --- Angle Tuning ---
    "bmsLowSpeedAdjustmentFactor":     _get_float(p, "FordLowSpeedFactor_ang", 1.0),
    "bmsHighSpeedAdjustmentFactor":    _get_float(p, "FordHighSpeedFactor_ang", 1.0),
    "bmsLaneChangeFactorHighAngle":    _get_float(p, "lane_change_factor_high_ang", 1.0),
    # --- Curvature Tuning ---
    "bmsEnableHumanTurnDetection":     _get_bool(p, "enable_human_turn_detection_curv", True),
    "bmsLaneChangeFactorHighCurvature": _get_float(p, "lane_change_factor_high_curv", 0.85),
    "bmsEnableLanePositioning":        _get_bool(p, "enable_lane_positioning_curv"),
    "bmsInLaneOffset":                 _get_float(p, "custom_path_offset_curv", 0.0),
    "bmsEnableLanefullMode":           _get_bool(p, "enable_lane_full_mode_curv"),
    "bmsUseCustomTuningProfile":       bool(_get_int(p, "custom_profile_curv", 0)),
    "bmsPredictedCurvatureBlendRatioHigh": _get_float(p, "pc_blend_ratio_high_C_UI_curv", 0.4),
    "bmsPredictedCurvatureBlendRatioLow":  _get_float(p, "pc_blend_ratio_low_C_UI_curv", 0.4),
    "bmsCenteringPidGain":             _get_float(p, "LC_PID_gain_UI_curv", 3.0),
    # --- Angle-mode lane positioning ---
    # The four *_curv entries above are the CURVATURE-mode keys. This car runs angle mode, so
    # without these the settings that actually govern it never reached a route -- which is why the
    # 2026-09-04 lane_centering_strength_ang 0.35 -> 0.45 change could not be scored afterwards.
    "bmsHighSpeedDampening":           _get_float(p, "FordHighSpeedDampening_ang", 1.0),
    "bmsEnableLanePositioningAng":     _get_bool(p, "enable_lane_positioning_ang"),
    "bmsInLaneOffsetAng":              _get_float(p, "custom_path_offset_ang", 0.0),
    "bmsLaneCenteringStrength":        _get_float(p, "lane_centering_strength_ang", 0.0),
    "bmsLaneCenteringDamping":         _get_float(p, "lane_centering_damping_ang", 0.0),
  }


def publish_controller_state_bp(CI, pm):
  """Publish controllerStateBP, LATCHED OFF for the drive on any failure.

  **THIS IS CALLED UNGUARDED FROM card.py's control loop** (card.py:298), so before this wrapper
  existed, ANY exception in here took the car off the road -- the car would sit on "waiting to
  start". That is the failure mode this repo already paid for once, when a single AttributeError in
  `icbm.py::_update_gap` made the car undrivable, and the rule written down then applies exactly:
  a convenience must degrade to doing nothing, never to stopping the car.

  Everything published here is DIAGNOSTIC. Nothing in the driving path reads controllerStateBP, so
  losing it costs a route's telemetry and nothing else -- while a raise costs the drive. The
  settings snapshot is the specific hazard: it is a dict of ~60 field names applied with `setattr`
  in a loop, so one field renamed in `custom.capnp` without its entry here, or one type that capnp
  refuses (a numpy scalar -- the failure that killed plannerd on the first frame of a drive), takes
  out `card`.

  LATCHED rather than retried, and the reason is the same as `icbm_gap_failed`: a per-frame failure
  that logged every 10 ms would flood swaglog and bury the very line that explains it. One
  exception, one log, then silence for the drive.
  """
  global _publish_failed
  if _publish_failed:
    return
  try:
    _publish_controller_state_bp(CI, pm)
  except Exception:
    _publish_failed = True
    cloudlog.exception("bp_card_publisher: controllerStateBP DISABLED for this drive after an "
                       "exception -- diagnostics are lost, driving is unaffected")


def _publish_controller_state_bp(CI, pm):
  """Publish controllerStateBP if the car controller reports lateralUncertainty."""
  global _settings_last_read, _settings_cache
  if hasattr(CI.CC, "lateralUncertainty"):
    cs_bp = structs.ControllerStateBP()
    cs_bp.lateralUncertainty = CI.CC.lateralUncertainty
    cs_bp.angleRateLimited = getattr(CI.CC, "angleRateLimited", False)
    cs_bp.curvatureRateLimited = getattr(CI.CC, "curvatureRateLimited", False)
    cs_bp.curvatureDeviationLimited = getattr(CI.CC, "curvatureDeviationLimited", False)
    cs_bp.humanTurnLateralPaused = bool(getattr(CI.CC, "humanTurnLateralPaused", False))
    cs_bp.stallBlipActive = bool(getattr(CI.CC, "stallBlipActive", False))
    # FusionPilot: angle-mode command and applied gain. float() because these cross into capnp,
    # where a numpy scalar raises -- the failure that killed plannerd on the first frame of a drive
    # once. getattr defaults keep curvature-mode and non-Ford cars publishing zeros rather than
    # raising in the publish path.
    cs_bp.pathAngleFinal = float(getattr(CI.CC, "pathAngleFinal", 0.0))
    cs_bp.kappaCmd = float(getattr(CI.CC, "kappaCmd", 0.0))
    cs_bp.curvatureFactor = float(getattr(CI.CC, "curvatureFactor", 0.0))
    cs_bp.laneCenterCorrection = float(getattr(CI.CC, "laneCenterCorrection", 0.0))
    cs_bp.gainLowCurv = float(getattr(CI.CC, "gainLowCurv", 0.0))
    cs_bp.gainHighCurv = float(getattr(CI.CC, "gainHighCurv", 0.0))
    cs_bp.blendWeight = float(getattr(CI.CC, "blendWeight", 0.0))
    # FusionPilot: who authored ACCDATA this frame. Straight off the car controller, which is the
    # BluePilot: mode the controller actually ran, straight off the car controller (not Params).
    if getattr(CI.CC, "disable_BP_lat_UI", True):
      cs_bp.activeLateralMode = structs.ControllerStateBP.LateralMode.openpilot
    elif getattr(CI.CC, "primary_lateral_control", 0) == 1:
      cs_bp.activeLateralMode = structs.ControllerStateBP.LateralMode.angle
    else:
      cs_bp.activeLateralMode = structs.ControllerStateBP.LateralMode.curvature

    # BluePilot: settings snapshot -- refreshed at most every _SETTINGS_INTERVAL s so Params
    # reads don't add latency to every card.py tick.
    now = time.monotonic()
    if not _settings_cache or now - _settings_last_read >= _SETTINGS_INTERVAL:
      try:
        _settings_cache = _refresh_settings_cache()
      except Exception:
        pass
      _settings_last_read = now
    if _settings_cache:
      for field, value in _settings_cache.items():
        setattr(cs_bp, field, value)

    # BluePilot: fingerprint info -- plain attribute reads on CarParams, no Params round-trip
    # needed, so no caching required (fingerprint never changes after startup).
    CP = getattr(CI, "CP", None)
    if CP is not None:
      cs_bp.bmsFingerprintForced = bool(getattr(CP, "fingerprintSource", None) == "fixed")
      cs_bp.bmsFingerprint = str(getattr(CP, "carFingerprint", "") or "")

    cs_bp_capnp = convert_to_capnp(cs_bp)
    cs_bp_send = messaging.new_message('controllerStateBP')
    cs_bp_send.valid = True
    cs_bp_send.controllerStateBP = cs_bp_capnp
    pm.send('controllerStateBP', cs_bp_send)


def publish_car_state_bp(CI, pm, can_valid):
  """Publish carStateBP, LATCHED OFF for the drive on any failure.

  Same argument as publish_controller_state_bp above: called unguarded from card.py, publishes
  nothing the driving path reads, so it must degrade to doing nothing rather than to stopping the
  car. Kept separate from that latch so one failing publisher does not silence the other.
  """
  global _car_state_publish_failed
  if _car_state_publish_failed:
    return
  try:
    _publish_car_state_bp(CI, pm, can_valid)
  except Exception:
    _car_state_publish_failed = True
    cloudlog.exception("bp_card_publisher: carStateBP DISABLED for this drive after an exception")


def _publish_car_state_bp(CI, pm, can_valid):
  """Publish carStateBP (hybrid drive gauge data) if available from car state."""
  if hasattr(CI.CS, 'car_state_bp_msg') and CI.CS.car_state_bp_msg is not None:
    cs_bp_send = CI.CS.car_state_bp_msg
    cs_bp_send.valid = can_valid
    pm.send('carStateBP', cs_bp_send)
