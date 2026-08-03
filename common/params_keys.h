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
    // NOT kept under Ford's ~10 mph threshold, which is what an earlier version of this comment
    // said while the value beside it was already 12. That guard was retired deliberately: the
    // violent application it feared has a hard ceiling built into the car and cannot happen, and a
    // cap that is too small does not prevent braking, it just spreads the deceleration out past
    // turn-in. Proper cornering sheds the speed BEFORE the bend.
    //
    // SCC-Map is exempt from this limiter entirely -- see apply_target_drop_limit. Its target
    // arrives with a deadline already attached, so metering it spends road that was budgeted.
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
    //
    // ON, OFF, ON -- and the middle step is the useful part of the record. Shipped on 2026-08-01,
    // shipped OFF again on 2026-08-06 because it "fired almost continuously with no vehicle
    // ahead" and he turned it off himself. That cause was found on 2026-08-08: the trigger asked
    // _ford_tracks, which requires a lead moving above 6 mph, so cars QUEUED at the light -- the
    // one thing guaranteed to be present at a red light -- did not count as a lead and the path
    // fired on them. It now requires no lead at all, which is what the design always said it was
    // for: the empty intersection.
    //
    // Back ON, because the road evidence either side of that fix is good ("the red light thing
    // worked... the slowing was actually perfect") and the only reported false positives are the
    // class that has since been fixed.
    // It remains the weakest-evidence path here -- no lead means no dRel, vRel or TTC, so
    // persistence and the 20 mph ACC floor are its entire filter.
    {"IcbmModelStopEnabled", {PERSISTENT | BACKUP, BOOL, "1"}},
    // BluePilot: how hard the stop would have to be braked for before this path acts at all, in
    // tenths of m/s^2. THE EARLINESS CONTROL for stop signs and red lights.
    //
    // Measured 2026-08-08 on route 0000032c, activation #4: it fired at 34 mph with 193 m still to
    // run, which needs 0.60 m/s^2 to stop -- gentler than coasting, and about 2.5x the distance a
    // comfortable stop wants. "It's stopping for red lights a little too early", and the number
    // agrees. DEC's slow-down flag is deliberately early (that is why it was chosen over
    // shouldStop, which can never fire here) so the earliness has to be bounded downstream.
    //
    // 1.0 m/s^2 is where a stop stops being reachable by lifting off: below it, coasting arrives in
    // time on its own and a set-speed request buys nothing. At 34 mph it moves that trigger from
    // 193 m to about 116 m. Ford's own braking ceiling is ~1.3 (measured), so values much above
    // that ask for a stop the car cannot deliver.
    {"IcbmModelStopMinDecel", {PERSISTENT | BACKUP, INT, "10"}},
    // BluePilot: hold off openpilot's standstill resume request until the lead has actually gone.
    // controlsd asserts resume from ITS OWN MPC plan, which on a stock-ACC car is not the
    // controller that then has to drive -- Ford ACC reads resume as "go", accelerates toward the
    // set speed, and brakes hard when its radar finds the lead still a few feet away.
    {"IcbmResumeGateEnabled", {PERSISTENT | BACKUP, BOOL, "1"}},
    {"IcbmResumeMinGap", {PERSISTENT | BACKUP, INT, "6"}},        // meters of lead gap
    {"IcbmResumeMinLeadSpeed", {PERSISTENT | BACKUP, INT, "5"}},  // mph the lead must be doing
    // BluePilot: holds pinned to a place -- JSON [{"lat":,"lon":,"speed":}], speed in display
    // units. For the handful of spots that need the same correction on every drive: a sign the
    // camera reliably misreads, a limit nobody drives, a school zone out of hours. Not for ramps;
    // those are curve geometry and belong to SCC-Map.
    {"IcbmPinnedHolds", {PERSISTENT | BACKUP, JSON}},
    // BluePilot: places the driver has set the same hold more than once. Feeds the suggestion to
    // pin it -- see SUGGEST_AFTER in pinned_holds.py. Suggestions never act on their own.
    {"IcbmHoldObservations", {PERSISTENT | BACKUP, JSON}},
    {"IcbmPinnedHoldsEnabled", {PERSISTENT | BACKUP, BOOL, "1"}},
    // Metres. Big enough that GPS scatter cannot step over it, small enough that a surface-street
    // pin does not fire on the freeway above it. A pin only has to hit ONCE -- it sets a normal
    // hold, which then persists on its own -- so this covers fix error, not the length of the zone.
    {"IcbmPinnedHoldRadius", {PERSISTENT | BACKUP, INT, "60"}},
    // Set by tapping the on-screen HOLD badge; consumed by selfdrived, which is where the GPS fix
    // and the live baseline both are. Keeps the UI from needing either.
    {"IcbmPinHoldRequest", {CLEAR_ON_MANAGER_START, BOOL}},
    // BluePilot: let a longitudinal feature ask the car for a different ACC follow gap, by pressing
    // the gap button the way ICBM presses the set-speed buttons. Closed loop against
    // AccTGap_D_Dsply in ACCDATA_3, which the camera already broadcasts and carstate already
    // parses -- see opendbc/sunnypilot/car/ford/gap_control.py.
    //
    // OFF by default, and this default matters: whether the camera honours an INJECTED gap press
    // at all is unproven. Only the driver's own cycling button is known to work. The controller
    // finds out for itself and declines if nothing moves, but until that has happened on the road
    // once, nothing should be pressing this button unasked.
    {"IcbmGapControl", {PERSISTENT | BACKUP, BOOL, "0"}},
    // BluePilot: passing-assist observation. Defaults ON, unlike the Icbm* actuating features --
    // this one only publishes a message. It cannot alert, steer, or touch the set speed, and it
    // is worthless switched off because its entire purpose is collecting drive data.
    {"PassingAssistLogEnabled", {PERSISTENT | BACKUP, BOOL, "1"}},
    // How far below the set speed the lead must hold us before it counts as being held back (mph).
    {"PassingAssistMinDeficit", {PERSISTENT | BACKUP, INT, "4"}},
    // How long that must persist before a pass would be suggested (seconds).
    // BluePilot: how long a slower lead must persist before suggesting, in seconds. Short by
    // design -- waiting is the behaviour this exists to remove, and the only job of this timer is
    // to reject a single bad frame of lead tracking.
    {"PassingAssistStuckTime", {PERSISTENT | BACKUP, INT, "2"}},
    // How far ahead to look for a slower vehicle, in metres. Distance, not time: a time bound
    // shrinks as the speed difference shrinks, so it would notice a gently slower car LATER than a
    // dramatically slower one, which is backwards.
    {"PassingAssistMaxDistance", {PERSISTENT | BACKUP, INT, "220"}},
    // Hold-off after suggesting a pass before suggesting the return. Stops a slow left lane on a
    // three-lane road turning into a weave.
    {"PassingAssistSettleTime", {PERSISTENT | BACKUP, INT, "20"}},
    // BluePilot: one-shot request to pause passing assist, set by tapping the onroad panel and
    // cleared by the detector once consumed. NOT persistent -- a pause must never survive a boot.
    {"PassingAssistSuspend", {CLEAR_ON_MANAGER_START | CLEAR_ON_IGNITION_ON, BOOL, "0"}},
    // How long a tap pauses it for, in minutes. Times out on its own so it cannot be left off.
    {"PassingAssistSuspendMinutes", {PERSISTENT | BACKUP, INT, "15"}},
    // BluePilot: "keep right except to pass". DEFAULT OFF -- modelV2 cannot tell a through lane
    // from an exit-only or merge lane, so the suggestion can mean "take the exit". Left in place
    // because the observer still records what it WOULD have said, which measures how often that
    // actually bites; it is not a feature to lean on until map data can distinguish the two.
    {"PassingAssistKeepRight", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"PassingAssistKeepRightDelay", {PERSISTENT | BACKUP, INT, "10"}},  // seconds
    // BluePilot: extra-conservative exit avoidance -- require a further lane BEYOND the one being
    // moved into. Superseded as the primary defence by the road-widening check, which spots an exit
    // from the road opening up ahead and works on two-lane roads. Default off: this one costs
    // keep-right entirely on two-lane-each-way roads, which is most of an interstate.
    {"PassingAssistAvoidOutermost", {PERSISTENT | BACKUP, BOOL, "0"}},
    // BluePilot: use the front radar's off-path tracks to check the target lane is not already
    // full of traffic no faster than the car being passed. DEFAULT ON -- unlike the two gates
    // above it can only ever suppress a suggestion that was going to be wasted, it needs no
    // hardware that is not already fitted, and an unavailable radar reports unavailable rather
    // than clear. Off is for isolating it when the band or the debounce is being fitted from logs.
    {"PassingAssistAdjacentLane", {PERSISTENT | BACKUP, BOOL, "1"}},
    {"DeviceBootMode", {PERSISTENT | BACKUP, INT, "0"}},
    {"DevUIInfo", {PERSISTENT | BACKUP, INT, "0"}},
    {"EnableCopyparty", {PERSISTENT | BACKUP, BOOL}},
    {"EnableGithubRunner", {PERSISTENT | BACKUP, BOOL}},
    // BluePilot: both default ON. They arm only while openpilot is not engaged -- e2e_alerts_helper
    // gates on `not CC.enabled` -- which on this car is the normal state at a stop, because braking
    // cancels Ford's ACC and the owner drives with MADS handling lateral regardless. So the case
    // these were written for is exactly the case this car is in at every light.
    {"GreenLightAlert", {PERSISTENT | BACKUP, BOOL, "1"}},
    {"GithubRunnerSufficientVoltage", {CLEAR_ON_MANAGER_START , BOOL}},
    {"HasAcceptedTermsSP", {PERSISTENT, STRING, "0"}},
    {"HideVEgoUI", {PERSISTENT | BACKUP, BOOL, "0"}},
    {"IntelligentCruiseButtonManagement", {PERSISTENT | BACKUP , BOOL}},
    {"InteractivityTimeout", {PERSISTENT | BACKUP, INT, "0"}},
    {"IsDevelopmentBranch", {CLEAR_ON_MANAGER_START, BOOL}},
    {"IsReleaseSpBranch", {CLEAR_ON_MANAGER_START, BOOL}},
    {"LastGPSPositionLLK", {PERSISTENT, STRING}},
    {"LeadDepartAlert", {PERSISTENT | BACKUP, BOOL, "1"}},
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
    // BluePilot: assist (3), not information (1). ICBM exists to drive the set speed toward the
    // posted limit; shipping "show me a sign and do nothing" would leave the whole feature inert.
    {"SpeedLimitMode", {PERSISTENT | BACKUP, INT, "3"}},
    // BluePilot: bySpeed (3). Shipping "off" would mean the banded offsets below never apply and the
    // car drives every posted limit exactly, which is not how anyone drives and not what was asked
    // for. The bands themselves are the owner's stated habit -- see SpeedLimitOffsetLow.
    {"SpeedLimitOffsetType", {PERSISTENT | BACKUP, INT, "3"}},
    {"SpeedLimitPolicy", {PERSISTENT | BACKUP, INT, "3"}},
    {"SpeedLimitValueOffset", {PERSISTENT | BACKUP, INT, "0"}},
    // BluePilot: bidirectional Speed Limit Assist. When set, SLA follows the limit in both
    // directions instead of only lowering, and never requests above SpeedLimitMaxSetSpeed.
    // BluePilot: one offset per speed band (SpeedLimitOffsetType = bySpeed). Defaults are the
    // owner's own habit, stated 2026-08-04: 2 over in a 20-25, 5 over from 30-60, 10 over at 65+.
    // A single fixed offset is wrong at both ends of that range, and the percentage option is the
    // same mistake wearing a disguise -- 10% of 25 is 2.5 and 10% of 70 is 7, which is roughly
    // backwards from how anyone drives.
    {"SpeedLimitOffsetLow", {PERSISTENT | BACKUP, INT, "2"}},
    {"SpeedLimitOffsetMid", {PERSISTENT | BACKUP, INT, "5"}},
    {"SpeedLimitOffsetHigh", {PERSISTENT | BACKUP, INT, "10"}},
    // Display units. Below mid -> low, below high -> mid, at/above high -> high.
    {"SpeedLimitOffsetMidThreshold", {PERSISTENT | BACKUP, INT, "30"}},
    {"SpeedLimitOffsetHighThreshold", {PERSISTENT | BACKUP, INT, "65"}},
    // BluePilot: what to do when no source knows the limit here. 0 = stand down and let the set
    // speed govern, 1 = keep the last known limit (upstream behavior, and the default nowhere).
    // Defaults to standing down: reported from the road that leaving I-215 carried the freeway's
    // 70 down the ramp and along a residential street until OSM had data again.
    {"SpeedLimitFallback", {PERSISTENT | BACKUP, INT, "0"}},
    // BluePilot: seconds of lead time before an upcoming HIGHER limit is adopted. Slowing for a
    // lower limit is geometry -- meet the new number at the sign -- but speeding up has no such
    // constraint, so this is a plain lead time rather than a deceleration curve. Exists because
    // leaving a slow zone otherwise means waiting until past the sign and then watching ICBM walk
    // the set speed up in steps. 0 disables it. Never adopts past the sign, only before it.
    {"SpeedLimitLookaheadHigher", {PERSISTENT | BACKUP, INT, "4"}},
    {"SpeedLimitAutoFollow", {PERSISTENT | BACKUP, BOOL, "1"}},
    // Ceiling for what SLA may request, in display units (mph/kph). Well under Ford ACC's 110 mph cap.
    // BluePilot: 100, not 85. Utah runs 80 mph on rural I-15, and the banded offset asks for +10 at
    // 65 and up -- so an 80 zone wants 90 and an 85 ceiling would silently clip it on a road the
    // owner drives regularly. A ceiling that binds during normal driving is not a safety bound, it
    // is a bug that looks like one. Set high on purpose; the owner intends to lower it once the
    // banded offsets have been driven.
    {"SpeedLimitMaxSetSpeed", {PERSISTENT | BACKUP, INT, "100"}},

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
    // BluePilot: 110 -> 100 on 2026-08-09. "It handled a lower speed curve pretty well though,
    // maybe a bit too slow." 110 targeted 1.82 m/s^2; 100 targets 2.0, which is the same place the
    // high-speed end now sits relative to its own complaint. "Pretty well" is why this moves less
    // than the high-speed one in absolute terms.
    // MEASURED COMFORT, two drives: he holds 2.7-3.0 m/s^2 through bends deliberately and reports no
    // difficulty. Route 00000338 t+815, 2.98 at 50 mph on the accelerator; route 0000033c t+210, 2.70
    // at 69 mph, again on the accelerator, on a bend SCC-Vision had already let him take at 74 mph
    // pulling 2.17 before it asked for 66. The budget is _A_LAT_REG_MAX / (factor/100), so 80 gives
    // 2.5 -- just under what he actually drives, which is why every bend reads as slightly too slow
    // and why he overrides so often. 70 gives 2.86, inside his demonstrated range.
    {"SmartCruiseControlVisionLowSpeedFactor", {PERSISTENT | BACKUP, INT, "70"}},
    // BluePilot: 100 -> 90 -> 80, each step from the road rather than a model.
    //
    // a_lat_reg_max = _A_LAT_REG_MAX / sensitivity, and _A_LAT_REG_MAX is 2.0 m/s^2, so this targets
    // 2.5 m/s^2. He reported "a little too slow" at 100 and, after applying 90, "took a curve on the
    // freeway too slow" again -- so the first 10-point step bought almost nothing and this one is
    // larger on purpose.
    //
    // THIS IS THE LAST STEP TO TAKE BLIND. Past here the binding limit stops being comfort and
    // becomes whether the retrofit PSCM can hold the angle, and there is no valid measurement of
    // that -- the one attempted on 2026-08-09 recovered his angle-gain calibration instead and was
    // retracted, see tools/bp_pscm_limit.py. Under-steering mid-curve is a worse failure than being
    // slow, so if 80 is still not enough the next move is measuring the steering, not guessing again.
    // MEASURED COMFORT, two drives: he holds 2.7-3.0 m/s^2 through bends deliberately and reports no
    // difficulty. Route 00000338 t+815, 2.98 at 50 mph on the accelerator; route 0000033c t+210, 2.70
    // at 69 mph, again on the accelerator, on a bend SCC-Vision had already let him take at 74 mph
    // pulling 2.17 before it asked for 66. The budget is _A_LAT_REG_MAX / (factor/100), so 80 gives
    // 2.5 -- just under what he actually drives, which is why every bend reads as slightly too slow
    // and why he overrides so often. 70 gives 2.86, inside his demonstrated range.
    {"SmartCruiseControlVisionHighSpeedFactor", {PERSISTENT | BACKUP, INT, "80"}},
    // BluePilot: SCC-Map deceleration target, tenths of m/s^2, magnitude. Unlike SCC-Vision this
    // single value sets BOTH how hard it slows and how early it starts, because the trigger is
    // "am I within the distance needed to reach the corner speed at this rate" -- gentler means a
    // longer distance means an earlier start. 12 = the stock -1.2 m/s^2, deliberately just under
    // the 1.3 that lights the stop lamps. Lower it to begin ramps sooner and more gently.
    {"SmartCruiseControlMapDecel", {PERSISTENT | BACKUP, INT, "8"}},
    // BluePilot: scales the corner speed SCC-Map asks for, in percent. The magnitude control map
    // has never had -- SmartCruiseControlMapDecel is a TRIGGER DISTANCE, so it moves when the
    // slowing starts and not how slow it gets, and vision's factors do not apply to mapped corners.
    //
    // Asked for on 2026-08-08 after an off-ramp: "for the end of the exit, it honestly should've
    // dropped down to 20 mph. My PSCM really wants to take curves like that at low speeds." The
    // mapped target matched the ramp's yellow advisory sign, which is correct for a stock car and
    // too fast for this one to steer -- the retrofit PSCM has less authority than the advisory
    // assumes. 100 keeps the map's own number; lower takes every mapped corner proportionally
    // slower.
    // Now the TIGHT-CORNER end of a speed-blended pair; see _MAP_FACTOR_V_BP in map_controller.py.
    // Kept under its original name rather than renamed so the stored value keeps meaning exactly what
    // it was chosen to mean on 2026-08-08 -- ramps slower than the yellow advisory -- with no
    // migration. It governs corners at or below 25 mph and blends out to the key below by 45 mph.
    {"SmartCruiseControlMapFactor", {PERSISTENT | BACKUP, INT, "90"}},
    // FusionPilot: the HIGHWAY-CORNER end of that pair, in percent. 100 keeps the map's own number.
    //
    // Measured, route 00000338 t+796 on 2026-08-10: the map's number for a highway bend was 48 mph,
    // the single global factor of 90 asked for 43, and he overrode with the accelerator and took the
    // bend at 51 pulling 2.9 m/s^2 comfortably. So the map was already close and the factor was the
    // error. Defaults to 100 because there is no evidence a mapped highway corner needs cutting --
    // and a global cut is what produced the report.
    {"SmartCruiseControlMapHighSpeedFactor", {PERSISTENT | BACKUP, INT, "100"}},

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
    // ShowBrakeStatus default is ICBM's, not this branch's: it was flipped to 1 deliberately as
    // part of making the owner's tested tune the shipped one. Keep theirs.
    {"ShowBrakeStatus", {PERSISTENT | BACKUP, BOOL, "1"}},
    // BluePilot: the passing-assist panel. Every gate, the dry run of the manoeuvre and the drive
    // summary all land here -- it is the whole feature's readout. Nothing acts; this only displays.
    {"ShowPassingAssist", {PERSISTENT | BACKUP, BOOL, "0"}},
    // BluePilot: one-shot stationary turn-signal actuation test. 0 off, 1 left, 2 right.
    // NOT persistent and NOT backed up: cleared on manager start and self-cleared when the pulse
    // ends, so it cannot survive a reboot and cannot repeat without a fresh deliberate request.
    {"FordBlinkerTest", {CLEAR_ON_MANAGER_START | CLEAR_ON_IGNITION_ON, INT, "0"}},
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
