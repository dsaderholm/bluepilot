#pragma once

#include <string>
#include <unordered_map>

#include "cereal/gen/cpp/log.capnp.h"

inline static std::unordered_map<std::string, ParamKeyAttributes> keys = {
    {"AccessToken", {CLEAR_ON_MANAGER_START | DONT_LOG, STRING}},
    {"AdbEnabled", {PERSISTENT | BACKUP, BOOL}},
    {"AlwaysOnDM", {PERSISTENT | BACKUP, BOOL}},
    {"ApiCache_Device", {PERSISTENT, STRING}},
    {"ApiCache_FirehoseStats", {PERSISTENT, JSON}},
    {"AssistNowToken", {PERSISTENT, STRING}},
    {"AthenadPid", {PERSISTENT, INT}},
    {"AthenadUploadQueue", {PERSISTENT, JSON}},
    {"AthenadRecentlyViewedRoutes", {PERSISTENT, STRING}},
    {"BootCount", {PERSISTENT, INT}},
    {"CalibrationParams", {PERSISTENT, BYTES}},
    {"CameraDebugExpGain", {CLEAR_ON_MANAGER_START, STRING}},
    {"CameraDebugExpTime", {CLEAR_ON_MANAGER_START, STRING}},
    {"CarBatteryCapacity", {PERSISTENT, INT}},
    {"CarParams", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION, BYTES}},
    {"CarParamsCache", {CLEAR_ON_MANAGER_START, BYTES}},
    {"CarParamsPersistent", {PERSISTENT, BYTES}},
    {"CarParamsPrevRoute", {PERSISTENT, BYTES}},
    {"CompletedTrainingVersion", {PERSISTENT, STRING, "0"}},
    {"ControlsReady", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION, BOOL}},
    {"CurrentBootlog", {PERSISTENT, STRING}},
    {"CurrentRoute", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION, STRING}},
    {"DisableLogging", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION, BOOL}},
    {"DisablePowerDown", {PERSISTENT | BACKUP, BOOL}},
    {"DisableUpdates", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"DisengageOnAccelerator", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"DongleId", {PERSISTENT, STRING}},
    {"DoReboot", {CLEAR_ON_MANAGER_START, BOOL}},
    {"DoShutdown", {CLEAR_ON_MANAGER_START, BOOL}},
    {"DoUninstall", {CLEAR_ON_MANAGER_START, BOOL}},
    {"DriverTooDistracted", {CLEAR_ON_MANAGER_START | CLEAR_ON_IGNITION_ON, BOOL}},
    {"AlphaLongitudinalEnabled", {PERSISTENT | DEVELOPMENT_ONLY | BACKUP, BOOL}},
    {"ExperimentalMode", {PERSISTENT | BACKUP, BOOL}},
    {"ExperimentalModeConfirmed", {PERSISTENT | BACKUP, BOOL}},
    {"FirmwareQueryDone", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION, BOOL}},
    {"ForcePowerDown", {PERSISTENT, BOOL}},
    {"GitBranch", {PERSISTENT, STRING}},
    {"GitCommit", {PERSISTENT, STRING}},
    {"GitCommitDate", {PERSISTENT, STRING}},
    {"GitDiff", {PERSISTENT, STRING}},
    {"GithubSshKeys", {PERSISTENT | BACKUP, STRING}},
    {"GithubUsername", {PERSISTENT | BACKUP, STRING}},
    {"GitRemote", {PERSISTENT, STRING}},
    {"GsmApn", {PERSISTENT | BACKUP, STRING}},
    {"GsmMetered", {PERSISTENT | BACKUP, BOOL, "1"}},
    {"GsmRoaming", {PERSISTENT | BACKUP, BOOL}},
    {"HardwareSerial", {PERSISTENT, STRING}},
    {"HasAcceptedTerms", {PERSISTENT, STRING, "0"}},
    {"InstallDate", {PERSISTENT, TIME}},
    {"IsDriverViewEnabled", {CLEAR_ON_MANAGER_START, BOOL}},
    {"IsEngaged", {PERSISTENT, BOOL}},
    {"IsLdwEnabled", {PERSISTENT | BACKUP, BOOL}},
    {"IsMetric", {PERSISTENT | BACKUP, BOOL}},
    {"IsOffroad", {CLEAR_ON_MANAGER_START, BOOL}},
    {"IsOnroad", {PERSISTENT, BOOL}},
    {"IsRhdDetected", {PERSISTENT, BOOL}},
    {"IsReleaseBranch", {CLEAR_ON_MANAGER_START, BOOL}},
    {"IsTakingSnapshot", {CLEAR_ON_MANAGER_START, BOOL}},
    {"IsTestedBranch", {CLEAR_ON_MANAGER_START, BOOL}},
    {"JoystickDebugMode", {CLEAR_ON_MANAGER_START | CLEAR_ON_OFFROAD_TRANSITION, BOOL}},
    {"LanguageSetting", {PERSISTENT | BACKUP, STRING, "en"}},
    {"LastAthenaPingTime", {CLEAR_ON_MANAGER_START, INT}},
    {"LastGPSPosition", {PERSISTENT, STRING}},
    {"LastManagerExitReason", {CLEAR_ON_MANAGER_START, STRING}},
    {"LastOffroadStatusPacket", {CLEAR_ON_MANAGER_START | CLEAR_ON_OFFROAD_TRANSITION, JSON}},
    {"LastAgnosPowerMonitorShutdown", {CLEAR_ON_MANAGER_START, STRING}},
    {"LastPowerDropDetected", {CLEAR_ON_MANAGER_START, STRING}},
    {"LastUpdateException", {CLEAR_ON_MANAGER_START, STRING}},
    {"LastUpdateRouteCount", {PERSISTENT, INT, "0"}},
    {"LastUpdateTime", {PERSISTENT, TIME}},
    {"LastUpdateUptimeOnroad", {PERSISTENT, FLOAT, "0.0"}},
    {"LiveDelay", {PERSISTENT | BACKUP, BYTES}},
    {"LiveParameters", {PERSISTENT, JSON}},
    {"LiveParametersV2", {PERSISTENT, BYTES}},
    {"LivestreamEncoderBitrate", {CLEAR_ON_MANAGER_START | DONT_LOG, INT}},
    {"LiveTorqueParameters", {PERSISTENT | DONT_LOG, BYTES}},
    {"LocationFilterInitialState", {PERSISTENT, BYTES}},
    {"LateralManeuverMode", {CLEAR_ON_MANAGER_START | CLEAR_ON_OFFROAD_TRANSITION, BOOL}},
    {"LongitudinalManeuverMode", {CLEAR_ON_MANAGER_START | CLEAR_ON_OFFROAD_TRANSITION, BOOL}},
    {"LongitudinalPersonality", {PERSISTENT | BACKUP, INT, std::to_string(static_cast<int>(cereal::LongitudinalPersonality::STANDARD))}},
    {"NetworkMetered", {PERSISTENT | BACKUP, BOOL}},
    {"ObdMultiplexingChanged", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION, BOOL}},
    {"ObdMultiplexingEnabled", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION, BOOL}},
    {"Offroad_CarUnrecognized", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION, JSON}},
    {"Offroad_ConnectivityNeeded", {CLEAR_ON_MANAGER_START, JSON}},
    {"Offroad_ConnectivityNeededPrompt", {CLEAR_ON_MANAGER_START, JSON}},
    {"Offroad_ExcessiveActuation", {PERSISTENT, JSON}},
    {"Offroad_IsTakingSnapshot", {CLEAR_ON_MANAGER_START, JSON}},
    {"Offroad_NeosUpdate", {CLEAR_ON_MANAGER_START, JSON}},
    {"Offroad_NoFirmware", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION, JSON}},
    {"Offroad_Recalibration", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION, JSON}},
    {"Offroad_TemperatureTooHigh", {CLEAR_ON_MANAGER_START, JSON}},
    {"Offroad_UnregisteredHardware", {CLEAR_ON_MANAGER_START, JSON}},
    {"Offroad_UpdateFailed", {CLEAR_ON_MANAGER_START, JSON}},
    {"Offroad_DriverMonitoringUncertain", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION, JSON}},
    {"OnroadCycleRequested", {CLEAR_ON_MANAGER_START, BOOL}},
    {"OpenpilotEnabledToggle", {PERSISTENT | BACKUP, BOOL, "1"}},
    {"PandaHeartbeatLost", {CLEAR_ON_MANAGER_START | CLEAR_ON_OFFROAD_TRANSITION, BOOL}},
    {"PrimeType", {PERSISTENT, INT}},
    {"RecordAudio", {PERSISTENT | BACKUP, BOOL}},
    {"RecordAudioFeedback", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"RecordFront", {PERSISTENT | BACKUP, BOOL}},
    {"RecordFrontLock", {PERSISTENT, BOOL}},  // for the internal fleet
    {"SecOCKey", {PERSISTENT | DONT_LOG | BACKUP, STRING}},
    {"ShowDebugInfo", {PERSISTENT, BOOL}},
    {"RouteCount", {PERSISTENT, INT, "0"}},
    {"SnoozeUpdate", {CLEAR_ON_MANAGER_START | CLEAR_ON_OFFROAD_TRANSITION, BOOL}},
    {"SshEnabled", {PERSISTENT | BACKUP, BOOL}},
    {"TermsVersion", {PERSISTENT, STRING}},
    {"TorqueBar", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"TrainingVersion", {PERSISTENT, STRING}},
    {"UbloxAvailable", {PERSISTENT, BOOL}},
    {"UpdateAvailable", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION, BOOL}},
    {"UpdateFailedCount", {CLEAR_ON_MANAGER_START, INT}},
    {"UpdaterAvailableBranches", {PERSISTENT, STRING}},
    {"UpdaterCurrentDescription", {CLEAR_ON_MANAGER_START, STRING}},
    {"UpdaterCurrentReleaseNotes", {CLEAR_ON_MANAGER_START, BYTES}},
    {"UpdaterFetchAvailable", {CLEAR_ON_MANAGER_START, BOOL}},
    {"UpdaterNewDescription", {CLEAR_ON_MANAGER_START, STRING}},
    {"UpdaterNewReleaseNotes", {CLEAR_ON_MANAGER_START, BYTES}},
    {"UpdaterState", {CLEAR_ON_MANAGER_START, STRING}},
    {"UpdaterTargetBranch", {CLEAR_ON_MANAGER_START, STRING}},
    {"UpdaterLastFetchTime", {PERSISTENT, TIME}},
    {"UptimeOffroad", {PERSISTENT, FLOAT, "0.0"}},
    {"UptimeOnroad", {PERSISTENT, FLOAT, "0.0"}},
    {"UsbGpuPresent", {CLEAR_ON_MANAGER_START | CLEAR_ON_OFFROAD_TRANSITION, BOOL}},
    {"UsbGpuCompiled", {CLEAR_ON_MANAGER_START | CLEAR_ON_OFFROAD_TRANSITION, BOOL}},
    {"Version", {PERSISTENT, STRING}},

    // --- sunnypilot params --- //
    {"ApiCache_DriveStats", {PERSISTENT, JSON}},
    {"AutoLaneChangeBsmDelay", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"AutoLaneChangeTimer", {PERSISTENT | BACKUP, INT, "0"}},
    {"BlinkerLateralReengageDelay", {PERSISTENT | BACKUP, INT, "0"}},  // seconds
    {"BlinkerMinLateralControlSpeed", {PERSISTENT | BACKUP, INT, "20"}},  // MPH or km/h
    {"BlinkerPauseLateralControl", {PERSISTENT | BACKUP, INT, "0"}},
    {"Brightness", {PERSISTENT | BACKUP, INT, "0"}},
    {"CarList", {PERSISTENT, JSON}},
    {"CarParamsSP", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION, BYTES}},
    {"CarParamsSPCache", {CLEAR_ON_MANAGER_START, BYTES}},
    {"CarParamsSPPersistent", {PERSISTENT, BYTES}},
    {"CarPlatformBundle", {PERSISTENT | BACKUP, JSON}},
    {"ChevronInfo", {PERSISTENT | BACKUP, INT, "4"}},
    {"CompletedSunnylinkConsentVersion", {PERSISTENT, STRING, "0"}},
    {"CustomAccIncrementsEnabled", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"CustomAccLongPressIncrement", {PERSISTENT | BACKUP, INT, "5"}},
    {"CustomAccShortPressIncrement", {PERSISTENT | BACKUP, INT, "1"}},
    // BluePilot: max per-step ICBM set-speed decrement, in display units (mph/kph).
    // Kept under Ford's ~10 mph aggressive-brake threshold so stock ACC coasts instead of braking.
    {"IcbmMaxTargetDrop", {PERSISTENT | BACKUP, INT, "12"}},
    // BluePilot: max per-step ICBM set-speed increment, in display units (mph/kph). ICBM holds the
    // button rather than tapping it, and Ford reads a held button as a continuous ramp, so without
    // this the set speed slams back up after a curve or a low-limit zone. 0 disables.
    {"IcbmMaxTargetRise", {PERSISTENT | BACKUP, INT, "5"}},
    // BluePilot: how far the posted speed limit must move before a driver set-speed press is
    // discarded and Speed Limit Assist takes the set speed back. Display units (mph/kph).
    // Small enough and every zone-to-zone drift throws away the driver's number; large enough
    // and a 55-zone baseline follows them into a 35 zone.
    {"IcbmBaselineResetDelta", {PERSISTENT | BACKUP, INT, "10"}},
    // BluePilot: furthest a vision-only lead is considered for the radar-blind decel, in meters.
    // Ford ACC handles close leads itself; the case this exists for is a stopped car far ahead.
    {"IcbmLeadMaxDistance", {PERSISTENT | BACKUP, INT, "180"}},
    // BluePilot: time-to-collision bound for the radar-blind lead trigger, in tenths of a second.
    // This is the real earliness control -- against a stopped lead TTC = dRel / v_ego, so at
    // 65 mph 7.0 s reaches ~203 m, so IcbmLeadMaxDistance (180 m) is what binds at highway speed
    // and TTC binds below it. Raised from 4.0 s / 120 m: at those values nothing could trigger
    // beyond ~116 m however obvious the stopped car was, which is far too late to be useful.
    {"IcbmLeadMaxTtc", {PERSISTENT | BACKUP, INT, "70"}},
    // BluePilot: act on the driving model's own stop intent (stop signs, red lights) when no
    // lead explains it. Same floor-and-alert channel as the radar-blind lead case.
    // Defaulted ON as of 2026-08-01, reversing the original "never run on a vehicle" caution:
    // the owner asked twice why stop-sign slowing was not happening, and it could not be reached
    // from the UI at all until now, so leaving it off guaranteed it would never get tested.
    // It remains the weakest-evidence path here -- no lead means no dRel, vRel or TTC, so
    // persistence and the 20 mph ACC floor are its entire filter.
    {"IcbmModelStopEnabled", {PERSISTENT | BACKUP, BOOL, "1"}},
    // BluePilot: hold off openpilot's standstill resume request until the lead has actually gone.
    // controlsd asserts resume from ITS OWN MPC plan, which on a stock-ACC car is not the
    // controller that then has to drive -- Ford ACC reads resume as "go", accelerates toward the
    // set speed, and brakes hard when its radar finds the lead still a few feet away.
    {"IcbmResumeGateEnabled", {PERSISTENT | BACKUP, BOOL, "1"}},
    {"IcbmResumeMinGap", {PERSISTENT | BACKUP, INT, "6"}},        // meters of lead gap
    {"IcbmResumeMinLeadSpeed", {PERSISTENT | BACKUP, INT, "5"}},  // mph the lead must be doing
    {"DeviceBootMode", {PERSISTENT | BACKUP, INT, "0"}},
    {"DevUIInfo", {PERSISTENT | BACKUP, INT, "0"}},
    {"EnableCopyparty", {PERSISTENT | BACKUP, BOOL}},
    {"EnableGithubRunner", {PERSISTENT | BACKUP, BOOL}},
    {"GreenLightAlert", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"GithubRunnerSufficientVoltage", {CLEAR_ON_MANAGER_START , BOOL}},
    {"HasAcceptedTermsSP", {PERSISTENT, STRING, "0"}},
    {"HideVEgoUI", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"IntelligentCruiseButtonManagement", {PERSISTENT | BACKUP , BOOL}},
    {"InteractivityTimeout", {PERSISTENT | BACKUP, INT, "0"}},
    {"IsDevelopmentBranch", {CLEAR_ON_MANAGER_START, BOOL}},
    {"IsReleaseSpBranch", {CLEAR_ON_MANAGER_START, BOOL}},
    {"LastGPSPositionLLK", {PERSISTENT, STRING}},
    {"LeadDepartAlert", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"MaxTimeOffroad", {PERSISTENT | BACKUP, INT, "1800"}},
    {"ModelRunnerTypeCache", {CLEAR_ON_ONROAD_TRANSITION, INT}},
    {"OffroadMode", {CLEAR_ON_MANAGER_START, BOOL}},
    {"Offroad_TiciSupport", {CLEAR_ON_MANAGER_START, JSON}},
    {"OnroadScreenOffBrightness", {PERSISTENT | BACKUP, INT, "0"}},
    {"OnroadScreenOffBrightnessMigrated", {PERSISTENT | BACKUP, STRING, "0.0"}},
    {"OnroadScreenOffTimer", {PERSISTENT | BACKUP, INT, "15"}},
    {"OnroadScreenOffTimerMigrated", {PERSISTENT | BACKUP, STRING, "0.0"}},
    {"OnroadUploads", {PERSISTENT | BACKUP, BOOL, "1"}},
    {"QuickBootToggle", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"QuietMode", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"RainbowMode", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"RocketFuel", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"ShowAdvancedControls", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"ShowTurnSignals", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"StandstillTimer", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"TrueVEgoUI", {PERSISTENT | BACKUP, BOOL, "0"}},

    // MADS params
    {"Mads", {PERSISTENT | BACKUP, BOOL, "1"}},
    {"MadsMainCruiseAllowed", {PERSISTENT | BACKUP, BOOL, "1"}},
    {"MadsSteeringMode", {PERSISTENT | BACKUP, INT, "0"}},
    {"MadsUnifiedEngagementMode", {PERSISTENT | BACKUP, BOOL, "1"}},

    // Model Manager params
    {"ModelManager_ActiveBundle", {PERSISTENT, JSON}},
    {"ModelManager_ClearCache", {CLEAR_ON_MANAGER_START, BOOL}},
    {"ModelManager_DownloadIndex", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION, INT}},
    {"ModelManager_Favs", {PERSISTENT | BACKUP, STRING}},
    {"ModelManager_LastSyncTime", {CLEAR_ON_MANAGER_START | CLEAR_ON_OFFROAD_TRANSITION, INT, "0"}},
    {"ModelManager_ModelsCache", {PERSISTENT | BACKUP, JSON}},

    // Neural Network Lateral Control
    {"NeuralNetworkLateralControl", {PERSISTENT | BACKUP, BOOL, "0"}},

    // sunnylink params
    {"EnableSunnylinkUploader", {PERSISTENT | BACKUP, BOOL}},
    {"LastSunnylinkPingTime", {CLEAR_ON_MANAGER_START, INT}},
    {"ParamsVersion", {PERSISTENT, INT}},
    {"SunnylinkCache_Roles", {PERSISTENT, STRING}},
    {"SunnylinkCache_Users", {PERSISTENT, STRING}},
    {"SunnylinkDongleId", {PERSISTENT, STRING}},
    {"SunnylinkdPid", {PERSISTENT, INT}},
    {"SunnylinkEnabled", {PERSISTENT, BOOL, "1"}},
    {"SunnylinkTempFault", {CLEAR_ON_MANAGER_START | CLEAR_ON_OFFROAD_TRANSITION, BOOL, "0"}},

    // Backup Manager params
    {"BackupManager_CreateBackup", {PERSISTENT, BOOL}},
    {"BackupManager_RestoreVersion", {PERSISTENT, STRING}},

    // sunnypilot car specific params
    {"HyundaiLongitudinalTuning", {PERSISTENT | BACKUP, INT, "0"}},
    {"SubaruStopAndGo", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"SubaruStopAndGoManualParkingBrake", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"TeslaCoopSteering", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"ToyotaEnforceStockLongitudinal", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"ToyotaStopAndGoHack", {PERSISTENT | BACKUP, BOOL, "0"}},

    {"DynamicExperimentalControl", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"BlindSpot", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"ShowBlindspotOverlay", {PERSISTENT | BACKUP, BOOL, "1"}},

    // sunnypilot model params
    {"CameraOffset", {PERSISTENT | BACKUP, FLOAT, "0.0"}},
    {"LagdToggle", {PERSISTENT | BACKUP, BOOL, "1"}},
    {"LagdToggleDelay", {PERSISTENT | BACKUP, FLOAT, "0.2"}},
    {"LagdValueCache", {PERSISTENT, FLOAT, "0.2"}},
    {"LaneTurnDesire", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"LaneTurnValue", {PERSISTENT | BACKUP, FLOAT, "19.0"}},
    {"PlanplusControl", {PERSISTENT | BACKUP, FLOAT, "1.0"}},

    // mapd
    {"MapAdvisorySpeedLimit", {CLEAR_ON_ONROAD_TRANSITION, FLOAT}},
    {"MapdVersion", {PERSISTENT, STRING}},
    {"MapSpeedLimit", {CLEAR_ON_ONROAD_TRANSITION, FLOAT, "0.0"}},
    {"NextMapSpeedLimit", {CLEAR_ON_ONROAD_TRANSITION, JSON}},
    {"Offroad_OSMUpdateRequired", {CLEAR_ON_MANAGER_START, JSON}},
    {"OsmDbUpdatesCheck", {CLEAR_ON_MANAGER_START, BOOL}},  // mapd database update happens with device ON, reset on boot
    {"OSMDownloadBounds", {PERSISTENT, STRING}},
    {"OsmDownloadedDate", {PERSISTENT, STRING, "0.0"}},
    {"OSMDownloadLocations", {PERSISTENT, JSON}},
    {"OSMDownloadProgress", {CLEAR_ON_MANAGER_START, JSON}},
    {"OsmLocal", {PERSISTENT, BOOL}},
    {"OsmLocationName", {PERSISTENT, STRING}},
    {"OsmLocationTitle", {PERSISTENT, STRING}},
    {"OsmLocationUrl", {PERSISTENT, STRING}},
    {"OsmStateName", {PERSISTENT, STRING, "All"}},
    {"OsmStateTitle", {PERSISTENT, STRING}},
    {"OsmWayTest", {PERSISTENT, STRING}},
    {"RoadName", {CLEAR_ON_ONROAD_TRANSITION, STRING}},
    {"RoadNameToggle", {PERSISTENT | BACKUP, BOOL, "0"}},

    // Speed Limit
    {"SpeedLimitMode", {PERSISTENT | BACKUP, INT, "1"}},
    {"SpeedLimitOffsetType", {PERSISTENT | BACKUP, INT, "0"}},
    {"SpeedLimitPolicy", {PERSISTENT | BACKUP, INT, "3"}},
    {"SpeedLimitValueOffset", {PERSISTENT | BACKUP, INT, "0"}},
    // BluePilot: bidirectional Speed Limit Assist. When set, SLA follows the limit in both
    // directions instead of only lowering, and never requests above SpeedLimitMaxSetSpeed.
    {"SpeedLimitAutoFollow", {PERSISTENT | BACKUP, BOOL, "1"}},
    // Ceiling for what SLA may request, in display units (mph/kph). Well under Ford ACC's 110 mph cap.
    {"SpeedLimitMaxSetSpeed", {PERSISTENT | BACKUP, INT, "85"}},

    // Smart Cruise Control
    // BluePilot: both curve controllers default ON, and the vision tuning leans toward slowing
    // more than stock. The owner's stated objective is to minimize how often they have to take
    // over, explicitly accepting a slower car for it -- and lateral demand goes as v^2, so taking
    // 7% off the corner speed removes ~13% of the steering the PSCM has to find. The two are
    // min()'d in the planner, never summed, so running both cannot compound into a double slowdown.
    {"MapTargetVelocities", {CLEAR_ON_ONROAD_TRANSITION, STRING}},
    {"SmartCruiseControlMap", {PERSISTENT | BACKUP, BOOL, "1"}},
    {"SmartCruiseControlVision", {PERSISTENT | BACKUP, BOOL, "1"}},
    // BluePilot: SCC-Vision curve aggressiveness, split by speed regime and blended across
    // 30-60 mph, mirroring FordAngleLow/HighSpeedFactor. 100 = stock behavior for that regime,
    // higher = slows earlier and harder for a given curve, lower = carries more speed through.
    //
    // NOT derived from this car's angle gains, and that is settled rather than assumed. Two
    // earlier attempts got it wrong in opposite directions, so the reasoning is recorded here.
    //
    // The owner runs FordLowSpeedFactor_ang = 0.92 / FordHighSpeedFactor_ang = 0.87. Per
    // BluePilot's own writeup of angle control, that gain is a CALIBRATION: path_angle is derived
    // as curvature * v_ego * gain, which is pure geometry, and the gain exists only because the
    // PSCM continuously compensates for yaw, sway and roll against a factory model of the vehicle
    // it believes it is installed in. A correctly dialed gain means the car tracks the path it
    // was asked to -- there is no steering deficit for a lower corner speed to make up.
    //
    // (The over-aggressive-model problem is real but belongs to CURVATURE mode: desired curvature
    // swinging about predicted on straights, which is what the blend ratio addresses. Angle mode
    // zeroes c2/c3 and does not have it.)
    //
    // So these are set from the objective alone -- minimize takeovers, slower is explicitly
    // acceptable -- and a flat modest bump is the honest expression of that. If the angle gains
    // move after the alignment, these do NOT need to move with them.
    {"SmartCruiseControlVisionLowSpeedFactor", {PERSISTENT | BACKUP, INT, "110"}},
    {"SmartCruiseControlVisionHighSpeedFactor", {PERSISTENT | BACKUP, INT, "110"}},
    // BluePilot: how early the curve cycle starts, independent of how much it slows. 100 = stock.
    // Higher starts sooner, which spreads the same speed change over more distance.
    // Raised from 100 on 2026-08-01: reported as triggering too late on real drives, most
    // noticeably on freeway off-ramps. Went to 140 first, then to the current 170. At 170 the
    // entering threshold drops from 1.3 to 0.76 m/s^2, so at 70 mph it reacts to roughly a
    // 1280 m radius instead of 740 m. (An earlier version of this comment still described the
    // 140 step -- 0.93 m/s^2 and 1030 m -- after the value had already moved to 170.)
    // 200 is the clip ceiling in vision_controller._EARLINESS_MAX; there is no headroom above it.
    {"SmartCruiseControlVisionEarliness", {PERSISTENT | BACKUP, INT, "170"}},
    // BluePilot: SCC-Map deceleration target, tenths of m/s^2, magnitude. Unlike SCC-Vision this
    // single value sets BOTH how hard it slows and how early it starts, because the trigger is
    // "am I within the distance needed to reach the corner speed at this rate" -- gentler means a
    // longer distance means an earlier start. 12 = the stock -1.2 m/s^2, deliberately just under
    // the 1.3 that lights the stop lamps. Lower it to begin ramps sooner and more gently.
    {"SmartCruiseControlMapDecel", {PERSISTENT | BACKUP, INT, "8"}},

    // Torque lateral control custom params
    {"CustomTorqueParams", {PERSISTENT | BACKUP , BOOL}},
    {"EnforceTorqueControl", {PERSISTENT | BACKUP, BOOL}},
    {"LiveTorqueParamsToggle", {PERSISTENT | BACKUP , BOOL}},
    {"LiveTorqueParamsRelaxedToggle", {PERSISTENT | BACKUP , BOOL}},
    {"TorqueControlTune", {PERSISTENT | BACKUP, FLOAT, "0.0"}},
    {"TorqueParamsOverrideEnabled", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"TorqueParamsOverrideFriction", {PERSISTENT | BACKUP, FLOAT, "0.1"}},
    {"TorqueParamsOverrideLatAccelFactor", {PERSISTENT | BACKUP, FLOAT, "2.5"}},

    // Blue Pilot
    {"send_hands_free_cluster_msg", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"enable_human_turn_detection", {PERSISTENT | BACKUP, BOOL, "1"}},
    {"lane_change_factor_high", {PERSISTENT | BACKUP, FLOAT, "0.85"}},
    {"pc_blend_ratio_high_C_UI", {PERSISTENT | BACKUP, FLOAT, "0.4"}},
    {"pc_blend_ratio_low_C_UI", {PERSISTENT | BACKUP, FLOAT, "0.4"}},
    {"enable_lane_positioning", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"custom_path_offset", {PERSISTENT | BACKUP, FLOAT,"0.0"}},
    {"enable_lane_full_mode", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"custom_profile", {PERSISTENT | BACKUP, INT, "0"}},
    {"LC_PID_gain_UI", {PERSISTENT | BACKUP, FLOAT, "3.0"}},
        // BluePilot upstream defaults this to Curvature (0) "until you're ready to switch". This car
    // switched and is not going back -- angle mode zeroes c2/c3 and drives path_angle directly,
    // sidestepping the PSCM's sticky curvature filter entirely.
    {"FordPrefLateralControl", {PERSISTENT | BACKUP, INT, "1"}},
    {"FordAngleLowSpeedFactor", {PERSISTENT | BACKUP, FLOAT, "1.0"}},
    {"FordAngleHighSpeedFactor", {PERSISTENT | BACKUP, FLOAT, "1.0"}},

    // Blue Pilot: lateral-tuning params split by control scheme (curvature vs angle) -- see
    // sunnypilot/system/params_migration.py for the one-time migration from the params above.
    {"enable_human_turn_detection_curv", {PERSISTENT | BACKUP, BOOL, "1"}},
    {"lane_change_factor_high_curv", {PERSISTENT | BACKUP, FLOAT, "0.85"}},
    {"lane_change_factor_high_ang", {PERSISTENT | BACKUP, FLOAT, "1.0"}},
    {"pc_blend_ratio_high_C_UI_curv", {PERSISTENT | BACKUP, FLOAT, "0.4"}},
    {"pc_blend_ratio_low_C_UI_curv", {PERSISTENT | BACKUP, FLOAT, "0.4"}},
    {"enable_lane_positioning_curv", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"custom_path_offset_curv", {PERSISTENT | BACKUP, FLOAT, "0.0"}},
    {"enable_lane_full_mode_curv", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"custom_profile_curv", {PERSISTENT | BACKUP, INT, "0"}},
    {"LC_PID_gain_UI_curv", {PERSISTENT | BACKUP, FLOAT, "3.0"}},
        // BluePilot: the owner's dialed-in values, not the upstream 1.0. These are a CALIBRATION,
    // not a detune. path_angle = curvature * v_ego * gain, which is pure geometry; the gain exists
    // because the PSCM continuously compensates for yaw/sway/roll against a factory model of the
    // vehicle it thinks it is in. This car is a Fusion carrying an Edge PSCM and Edge rack on
    // Fusion suspension, so that model is wrong by construction -- exactly the case BluePilot says
    // needs dialling in. Its PSCM firmware was also never in the reverse-engineered set, which was
    // CAN FD only (F-150, Lightning, Mach-E, Expedition, Ranger, Escape).
    //
    // Below 1.0 means the wheel turns LESS for the same commanded geometry. At a correctly
    // calibrated gain the car tracks the path it was asked to. It does NOT mean the car
    // under-turns and needs to arrive slower -- see the SCC sensitivity comment above.
    {"FordLowSpeedFactor_ang", {PERSISTENT | BACKUP, FLOAT, "0.92"}},
    {"FordHighSpeedFactor_ang", {PERSISTENT | BACKUP, FLOAT, "0.87"}},
    {"FordHighSpeedDampening_ang", {PERSISTENT | BACKUP, FLOAT, "1.0"}},
    {"BPLateralSchemeParamsMigratedV1", {PERSISTENT | BACKUP, STRING, "0"}},

    {"disable_BP_lat_UI", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"disable_BP_long_UI", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"disable_downhill_comp_UI", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"disable_ford_radar_UI", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"vbatt_pause_charging", {PERSISTENT | BACKUP, FLOAT, "11.8"}},
    {"show_lead_speed", {PERSISTENT | BACKUP, BOOL, "1"}},
    {"FordPrefSteerAngleCurvature", {PERSISTENT | BACKUP, BOOL, "0"}},  // pinion-sourced curvature measurement (bad-yaw-sensor workaround); read at car init
    {"FordPrefShowRadarLeadOverlay", {PERSISTENT | BACKUP, BOOL, "1"}},
    {"FordPrefRadarOverlaySize", {PERSISTENT | BACKUP, INT, "1"}},
    {"FordPrefHybridBatteryStatus", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"FordPrefHybridPowerFlow", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"FordPrefHybridDriveGaugeSize", {PERSISTENT | BACKUP, INT, "1"}},
    {"FordPrefGaugeStyle", {PERSISTENT | BACKUP, INT, "0"}},  // hybrid/EV gauge style: 0=flat, 1=arched
    {"FordPrefHevDataAvailable", {CLEAR_ON_MANAGER_START, BOOL, "0"}},
    {"FordPrefHevBattDataAvailable", {CLEAR_ON_MANAGER_START, BOOL, "0"}},
    {"mici_complication", {PERSISTENT | BACKUP, INT, "0"}},
    {"ShowBrakeStatus", {PERSISTENT | BACKUP, BOOL, "1"}},
    {"FordPrefHybridPowerFlowAlternate", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"mici_hide_onroad_fade", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"mici_hide_onroad_border", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"BPHideOnroadBorder", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"BPHideCameraView", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"BPRadRacerTheme", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"BPRainbowLines", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"BPShowConfidenceBall", {PERSISTENT | BACKUP, BOOL, "1"}},
    {"BPAnimateSteeringWheel", {PERSISTENT | BACKUP, BOOL, "1"}},
    // BluePilot: No static defaults; the first active UI persists its matching device styles (C4=0, C3X=1).
    {"BPSteeringWheelIconStyle", {PERSISTENT | BACKUP, INT}},
    {"BPDMStylingChoice", {PERSISTENT | BACKUP, INT}},
    {"BPUseCustomSounds", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"BPCustSoundsSelection", {PERSISTENT | BACKUP, INT, "0"}},  // 0=Comma 4, 1=Comma 3x, 2=Tesla
    {"BpShowLateralControl", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"BPDisableLaneLineStatusColor", {PERSISTENT | BACKUP, BOOL, "0"}},
    // BluePilot: opt-in crash/log reporting to BluePilot's GlitchTip. Default OFF on this
    // fork -- the owner self-hosts and does not want device telemetry leaving the car.
    {"BPSentryEnabled", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"BPUIDebugLog", {PERSISTENT, BOOL, "0"}},
    {"Blindspot", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"BlinkerPauseLaneChange", {PERSISTENT | BACKUP, BOOL, "0"}},

    // BluePilot: Portal (Web Routes Server)
    {"EnableWebRoutesServer", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"BPPortalPort", {PERSISTENT | BACKUP, INT, "8088"}},

    // BluePilot: connect backend — 0=Comma Connect, 1=Konik Stable, 2=Offline Mode.
    // Per-backend dongle ID caches let comma <-> Konik switch without losing either identity;
    // see bluepilot/backend_switch.py. BPUseKonik is legacy (bool toggle); migrated once to
    // BPConnectBackend=1 then cleared.
    {"BPConnectBackend", {PERSISTENT | BACKUP, INT, "0"}},
    {"BPUseKonik", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"BPActiveBackend", {PERSISTENT, STRING}},
    {"BPDongleIdComma", {PERSISTENT, STRING}},
    {"BPDongleIdKonik", {PERSISTENT, STRING}},

    // BluePilot: UI params
    {"BPLastSeenVersion", {PERSISTENT, STRING}},

    // WiFi Management
    {"WifiFavoriteSSID", {PERSISTENT | BACKUP, STRING}},
};
