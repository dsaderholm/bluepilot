using Cxx = import "./include/c++.capnp";
$Cxx.namespace("cereal");

@0xb526ba661d550a59;

# custom.capnp: a home for empty structs reserved for custom forks
# These structs are guaranteed to remain reserved and empty in mainline
# cereal, so use these if you want custom events in your fork.

# DO rename the structs
# DON'T change the identifier (e.g. @0x81c2f05a394cf4af)

struct ModularAssistiveDrivingSystem {
  state @0 :ModularAssistiveDrivingSystemState;
  enabled @1 :Bool;
  active @2 :Bool;
  available @3 :Bool;

  enum ModularAssistiveDrivingSystemState {
    disabled @0;
    paused @1;
    enabled @2;
    softDisabling @3;
    overriding @4;
  }
}

struct IntelligentCruiseButtonManagement {
  state @0 :IntelligentCruiseButtonManagementState;
  sendButton @1 :SendButtonState;
  vTarget @2 :Float32;
  overrideState @3 :OverrideState;
  vBaseline @4 :Float32;  # BluePilot: the driver's own set speed while overrideState is manual
  # BluePilot: a curve, map point or radar-blind hazard currently owns the target, so a set-speed
  # press cannot change the hold -- it gives a momentary bump the suppressor then reclaims. The
  # HOLD badge greys out while this is set, because otherwise the press silently does nothing
  # lasting and the driver has no way to tell why.
  holdSuppressed @5 :Bool;
  # BluePilot: WHICH mechanism last captured the hold. Added to settle whether the press path was
  # dead code -- every drive appeared to show only the fallback firing, and deleting the press path
  # and its hand-picked settle timers would have removed the source of most of this feature's
  # defects. The answer was no: a badge tag on a real 5 mph hold showed the press path firing
  # first, with the fallback relabeling it a frame later. The press path stays.
  #
  # The on-screen tag is gone now that its question is answered. The field stays: it costs a byte,
  # it is the only way to tell the two capture paths apart in a route, and a capnp field number
  # cannot be reused once retired anyway.
  baselineSource @6 :BaselineSource;
  # BluePilot: a speed worth offering to pin at this place, because the driver has set the same
  # hold here before. 0 = nothing to offer. Only ever a suggestion; a tap on the badge accepts it.
  pinSuggestion @7 :Float32;

  # BluePilot: the ACC follow-gap this fork is asking the car for (Time_Gap_1..5), or 0 for "no
  # request, restore the driver's own". Asserted CONTINUOUSLY for as long as it is wanted -- there
  # is no duration, because silence is what restores. A requester that dies stops asserting and the
  # gap comes back by itself, which no stored deadline could guarantee.
  #
  # Honoured in opendbc's ford/gap_control.py, closed loop against AccTGap_D_Dsply in ACCDATA_3.
  gapTarget @8 :UInt8;

  # FusionPilot: THE TWO VALUES THE HOLD-CLEARING RULE ACTUALLY COMPARES. Neither was published,
  # and on 2026-08-21 that made a real on-road report ("set the speed back to SLA and the hold did
  # not clear") undiagnosable from a route.
  #
  # `vTarget` above is the value AFTER apply_baseline, so while a hold is active it EQUALS
  # vBaseline by construction -- a log reader comparing those two sees equality on every frame and
  # learns nothing. The rule compares `v_target_raw`, the planner's own target before the baseline
  # is applied, against the baseline; and it only acts when `baseline_diverged` says the baseline
  # has actually been somewhere else first.
  #
  # Same shape as the three other "computed correctly and never rendered" bugs in this fork.
  vTargetRaw @9 :Float32;
  baselineDiverged @10 :Bool;

  # FusionPilot: AND THE RULE CHANGED, SO THESE TWO ARE THE ONES IT COMPARES NOW. 2026-08-23.
  #
  # The comment above is right about why `vTargetRaw` and `baselineDiverged` were added, and it
  # went stale the same week: on 2026-08-22 the rule stopped comparing against the winning plan
  # and started comparing against SLA's OWN number, gated on the limit being live. Neither of
  # those reached the wire, so when he reported the hold sticking AGAIN on 2026-08-23 the route
  # could not say which term declined -- the exact failure the paragraph above describes, one
  # struct-field away from where it is written.
  #
  # THE LESSON IS THAT PUBLISHING A DIAGNOSTIC IS NOT A ONE-TIME ACT. It is a property of the
  # rule, and it has to move when the rule moves. Anything added here for a comparison must be
  # re-checked whenever that comparison is rewritten.
  vSlaTarget @11 :Float32;      # SLA's own number with his offset, in cluster units. 0 = none
  speedLimitLive @12 :Bool;     # speedLimitValid ALONE -- what the clearing rule gates on

  enum IntelligentCruiseButtonManagementState {
    inactive @0;      # No button press or default state
    preActive @1;     # Pre-active state before transitioning to increasing or decreasing
    increasing @2;    # Increasing speed
    decreasing @3;    # Decreasing speed
    holding @4;       # Holding steady speed
  }

  # BluePilot: driver manual override latch. In manual, ICBM stops chasing its target
  # entirely and lets the driver's set speed stand until it re-arms.
  enum OverrideState {
    auto @0;          # ICBM is free to drive the set speed toward its target
    manual @1;        # Driver overrode with a real button press; target chasing suspended
  }

  enum SendButtonState {
    none @0;
    increase @1;
    decrease @2;
  }

  enum BaselineSource {
    none @0;            # no hold captured this drive
    press @1;           # a real ButtonEvent reached MANUAL_OVERRIDE_BUTTONS -- the primary path
    fallbackIdle @2;    # set speed moved while ICBM had been silent long enough to rule itself out
    fallbackCounter @3; # set speed moved AGAINST the button ICBM was holding
    pinned @4;          # a hold pinned to this place on an earlier drive re-applied itself
  }
}

# Same struct as Log.RadarState.LeadData
struct LeadData {
  dRel @0 :Float32;
  yRel @1 :Float32;
  vRel @2 :Float32;
  aRel @3 :Float32;
  vLead @4 :Float32;
  dPath @6 :Float32;
  vLat @7 :Float32;
  vLeadK @8 :Float32;
  aLeadK @9 :Float32;
  fcw @10 :Bool;
  status @11 :Bool;
  aLeadTau @12 :Float32;
  modelProb @13 :Float32;
  radar @14 :Bool;
  radarTrackId @15 :Int32 = -1;

  aLeadDEPRECATED @5 :Float32;
}

struct SelfdriveStateSP @0x81c2f05a394cf4af {
  mads @0 :ModularAssistiveDrivingSystem;
  intelligentCruiseButtonManagement @1 :IntelligentCruiseButtonManagement;

  enum AudibleAlert {
    none @0;

    engage @1;
    disengage @2;
    refuse @3;

    warningSoft @4;
    warningImmediate @5;

    prompt @6;
    promptRepeat @7;
    promptDistracted @8;

    # unused, these are reserved for upstream events so we don't collide
    reserved9 @9;
    reserved10 @10;
    reserved11 @11;
    reserved12 @12;
    reserved13 @13;
    reserved14 @14;
    reserved15 @15;
    reserved16 @16;
    reserved17 @17;
    reserved18 @18;
    reserved19 @19;
    reserved20 @20;
    reserved21 @21;
    reserved22 @22;
    reserved23 @23;
    reserved24 @24;
    reserved25 @25;
    reserved26 @26;
    reserved27 @27;
    reserved28 @28;
    reserved29 @29;
    reserved30 @30;

    promptSingleLow @31;
    promptSingleHigh @32;
  }
}

struct ModelManagerSP @0xaedffd8f31e7b55d {
  activeBundle @0 :ModelBundle;
  selectedBundle @1 :ModelBundle;
  availableBundles @2 :List(ModelBundle);

  struct DownloadUri {
    uri @0 :Text;
    sha256 @1 :Text;
  }

  enum DownloadStatus {
    notDownloading @0;
    downloading @1;
    downloaded @2;
    cached @3;
    failed @4;
  }

  struct DownloadProgress {
    status @0 :DownloadStatus;
    progress @1 :Float32;
    eta @2 :UInt32;
  }

  struct Artifact {
    fileName @0 :Text;
    downloadUri @1 :DownloadUri;
    downloadProgress @2 :DownloadProgress;
  }

  struct Model {
    type @0 :Type;
    artifact @1 :Artifact;  # Main artifact
    metadata @2 :Artifact;  # Metadata artifact

    enum Type {
      supercombo @0;
      navigation @1;
      vision @2;
      policy @3;
      offPolicy @4;
      onPolicy @5;
    }
  }

  enum Runner {
    snpe @0;
    tinygrad @1;
    stock @2;
  }

  struct Override {
    key @0 :Text;
    value @1 :Text;
  }

  struct ModelBundle {
    index @0 :UInt32;
    internalName @1 :Text;
    displayName @2 :Text;
    models @3 :List(Model);
    status @4 :DownloadStatus;
    generation @5 :UInt32;
    environment @6 :Text;
    runner @7 :Runner;
    is20hz @8 :Bool;
    ref @9 :Text;
    minimumSelectorVersion @10 :UInt32;
    overrides @11 :List(Override);
  }
}

struct LongitudinalPlanSP @0xf35cc4560bbf6ec2 {
  dec @0 :DynamicExperimentalControl;
  longitudinalPlanSource @1 :LongitudinalPlanSource;
  smartCruiseControl @2 :SmartCruiseControl;
  speedLimit @3 :SpeedLimit;
  vTarget @4 :Float32;
  aTarget @5 :Float32;
  events @6 :List(OnroadEventSP.Event);
  e2eAlerts @7 :E2eAlerts;
  unconfirmedLead @8 :UnconfirmedLead;

  # BluePilot: ACC follow-gap requested by a longitudinal feature (Time_Gap_1..5), 0 for none.
  #
  # Defined on the ICBM branch rather than the passing-assist branch on purpose: ICBM is the base
  # the others rebase onto, and a capnp field number can only have one meaning. Passing assist is
  # the first requester; if it has already numbered its own fields from @9 they renumber above this
  # one on rebase.
  accGapRequest @9 :UInt8;

  # BluePilot: vision-detected lead with no radar corroboration. Carried on its own channel rather
  # than folded into vTarget so it bypasses ICBM's target-drop rate limiter -- that limiter exists
  # to keep Ford's ACC coasting for routine speed-limit and curve changes, which is the opposite of
  # what is wanted here.
  struct UnconfirmedLead {
    state @0 :State;
    vTarget @1 :Float32;          # MPC-sourced target, clipped to the ACC floor
    restoreSetSpeed @2 :Float32;  # set speed to return to once the event resolves
    dRel @3 :Float32;
    ttc @4 :Float32;

    # BluePilot: the driving model's own stop intent. LOGGED ONLY -- nothing gates on
    # modelShouldStop, and nothing may. It does not mean "there is a stop line ahead": modeld
    # computes it as (v_ego < 0.3 m/s and desired_accel < 0.1), so it means "already stopped, stay
    # stopped" and is false at every speed the model-stop path can run at. Gating on it is exactly
    # why that path never fired once on the road.
    #
    # What the trigger actually reads is DEC's slow-down decision -- model_slow_down, from
    # dec.has_slow_down() -- which carries its own hysteresis. modelDesiredAccel is not the trigger
    # either; it paces the request inside _model_stop_target once the trigger has fired.
    modelShouldStop @5 :Bool;
    modelDesiredAccel @6 :Float32;
    hasLead @7 :Bool;
    trigger @8 :Trigger;

    enum Trigger {
      none @0;
      visionLead @1;  # vision lead the radar never confirmed
      modelStop @2;   # model wants to stop with nothing for the radar to see: sign or signal
    }

    enum State {
      inactive @0;
      tracking @1;    # candidate seen, still accumulating persistence/range-sweep evidence
      active @2;      # triggered: requesting the MPC target down to the floor
      restoring @3;   # resolved: returning the set speed to restoreSetSpeed
    }
  }

  struct DynamicExperimentalControl {
    state @0 :DynamicExperimentalControlState;
    enabled @1 :Bool;
    active @2 :Bool;
    # FusionPilot: the model's "I am planning to stop for something ahead", and where it expects to
    # be stopped. Computed from modelV2 alone, so it is meaningful whatever mode DEC is in and even
    # when DEC is off -- and `unconfirmed_lead.py` already drives the stop-sign path from it.
    #
    # It has NEVER been on the wire. He reported stop-sign slowing as inaccurate while traffic
    # lights are fine, and that complaint was unattributable: nothing in any route says whether the
    # model failed to see the sign or saw it and the response was wrong. Same shape as the three
    # readouts computed correctly and never rendered.
    hasSlowDown @3 :Bool;
    slowDownUrgency @4 :Float32;
    slowDownEndpoint @5 :Float32;  # metres to the model's furthest point; inf when not a full plan

    enum DynamicExperimentalControlState {
      acc @0;
      blended @1;
    }
  }

  struct SmartCruiseControl {
    vision @0 :Vision;
    map @1 :Map;

    struct Vision {
      state @0 :VisionState;
      vTarget @1 :Float32;
      aTarget @2 :Float32;
      currentLateralAccel @3 :Float32;
      maxPredictedLateralAccel @4 :Float32;
      enabled @5 :Bool;
      active @6 :Bool;
    }

    struct Map {
      state @0 :MapState;
      vTarget @1 :Float32;
      aTarget @2 :Float32;
      enabled @3 :Bool;
      active @4 :Bool;

      # FusionPilot: THE VETOES' OWN INPUTS. Added 2026-08-25 because a real road report --
      # *"it decided to drop down to 20 for no reason with no warning"* -- could be attributed to
      # SCC-Map but NOT explained, and the four defenses are the thing that should have stopped it.
      #
      # Measured on routes 000003c0/c2: five of seven floor episodes demanded a corner 8-23x
      # tighter than the road the car then drove (R_map 16-32 m against R_drove 126-753 m). Reading
      # the CODE says defense 2 -- "the camera sees no curve at all" -- is reachable at low speed
      # and should have caught them, gated only by a 4 s horizon; and that defense 4, which exists
      # precisely because a corner can be acted on beyond the model's reach, is highway-only.
      #
      # THAT IS AN INFERENCE AND IT IS THE THIRD ONE IN THIS FILE'S HISTORY OF EXACTLY THIS
      # MISTAKE. `targetDistance` and `modelLatAcc` are the two numbers those gates compare, and
      # neither has ever been on the wire, so no drive can say which gate declined. The rule this
      # repo already wrote down after the cancel-recovery episode is: when a rule cannot be
      # explained from a drive, add the log line rather than a third inference.
      #
      # `modelVetoed` is the existing internal flag; the other three are its arguments. Publishing
      # a decision without its inputs is what made this undiagnosable in the first place.
      targetDistance @5 :Float32;   # metres to the corner. inf on the wire is meaningless, so 0
                                    # means "no corner", never "the corner is here".
      modelLatAcc @6 :Float32;      # the camera's own predicted lateral accel, the veto's evidence
      modelVetoed @7 :Bool;         # either camera veto fired
      cameraNotSeen @8 :Bool;       # suppressed because the corner is beyond the model's horizon

      # FusionPilot 2026-08-27: WHAT THE VETO COST. `vTarget` above is the OUTPUT, and
      # `get_v_target_from_control()` returns V_CRUISE_UNSET once a veto clears `is_active` -- so on
      # exactly the frames where a suppression happened, the speed the map WANTED is thrown away
      # before it reaches the wire.
      #
      # That made the veto's cost unmeasurable from any recorded route, which is not a small gap:
      # after the SLC -> Yosemite trip the question "did the fix suppress a corner that turned out
      # to be real" could be answered for the corners that got THROUGH and not for the ones that did
      # not, and a detector built on `active` could only ever return zero for the suppressed ones.
      #
      # This is the same lesson as `targetDistance`/`modelLatAcc` one field above, arriving a second
      # time: PUBLISHING A DECISION WITHOUT ITS INPUTS -- or here, without its CONSEQUENCE -- leaves
      # a real road report undiagnosable. 0 means no suppressed target this frame.
      vetoedVTarget @9 :Float32;
    }

    enum VisionState {
      disabled @0; # System disabled or inactive.
      enabled @1; # No predicted substantial turn on vision range.
      entering @2; # A substantial turn is predicted ahead, adapting speed to turn comfort levels.
      turning @3; # Actively turning. Managing acceleration to provide a roll on turn feeling.
      leaving @4; # Road ahead straightens. Start to allow positive acceleration.
      overriding @5; # System overriding with manual control.
    }

    enum MapState {
      disabled @0; # System disabled or inactive.
      enabled @1; # No predicted substantial turn on map range.
      turning @2; # Actively turning. Managing acceleration to provide a roll on turn feeling.
      overriding @3; # System overriding with manual control.
    }
  }

  struct SpeedLimit {
    resolver @0 :Resolver;
    assist @1 :Assist;

    struct Resolver {
      speedLimit @0 :Float32;
      distToSpeedLimit @1 :Float32;
      source @2 :Source;
      speedLimitOffset @3 :Float32;
      speedLimitLast @4 :Float32;
      speedLimitFinal @5 :Float32;
      speedLimitFinalLast @6 :Float32;
      speedLimitValid @7 :Bool;
      speedLimitLastValid @8 :Bool;
    }

    struct Assist {
      state @0 :AssistState;
      enabled @1 :Bool;
      active @2 :Bool;
      vTarget @3 :Float32;
      aTarget @4 :Float32;
    }

    enum Source {
      none @0;
      car @1;
      map @2;
    }

    enum AssistState {
      disabled @0;
      inactive @1; # No speed limit set or not enabled by parameter.
      preActive @2;
      pending @3; # Awaiting new speed limit.
      adapting @4; # Reducing speed to match new speed limit.
      active @5; # Cruising at speed limit.
    }
  }

  enum LongitudinalPlanSource {
    cruise @0;
    sccVision @1;
    sccMap @2;
    speedLimitAssist @3;
  }

  struct E2eAlerts {
    greenLightAlert @0 :Bool;
    leadDepartAlert @1 :Bool;
  }
}

struct OnroadEventSP @0xda96579883444c35 {
  events @0 :List(Event);

  struct Event {
    name @0 :EventName;

    # event types
    enable @1 :Bool;
    noEntry @2 :Bool;
    warning @3 :Bool;   # alerts presented only when  enabled or soft disabling
    userDisable @4 :Bool;
    softDisable @5 :Bool;
    immediateDisable @6 :Bool;
    preEnable @7 :Bool;
    permanent @8 :Bool; # alerts presented regardless of openpilot state
    overrideLateral @10 :Bool;
    overrideLongitudinal @9 :Bool;
  }

  enum EventName {
    lkasEnable @0;
    lkasDisable @1;
    manualSteeringRequired @2;
    manualLongitudinalRequired @3;
    silentLkasEnable @4;
    silentLkasDisable @5;
    silentBrakeHold @6;
    silentWrongGear @7;
    silentReverseGear @8;
    silentDoorOpen @9;
    silentSeatbeltNotLatched @10;
    silentParkBrake @11;
    controlsMismatchLateral @12;
    hyundaiRadarTracksConfirmed @13;
    experimentalModeSwitched @14;
    wrongCarModeAlertOnly @15;
    pedalPressedAlertOnly @16;
    laneTurnLeft @17;
    laneTurnRight @18;
    speedLimitPreActive @19;
    speedLimitActive @20;
    speedLimitChanged @21;
    speedLimitPending @22;
    e2eChime @23;
    # BluePilot: SLA changed the set speed automatically (raise or lower)
    speedLimitAutoSet @24;
    # BluePilot: vision lead with no radar confirmation -- driver must brake
    unconfirmedLeadBraking @25;
    # BluePilot: model wants to stop (sign/signal) and Ford ACC will not
    modelStopBraking @26;
    # FusionPilot: the stock-ACC passthrough has gone INERT -- the camera has asked to cancel for
    # five straight seconds, so Ford's command can no longer be carried and openpilot longitudinal
    # is driving from here. On route 0000038d it did this from t+30.8 for the whole drive with
    # nothing but a pill saying so, and he had to work it out from the seat: "it's just annoying
    # that it bricks it for the whole drive". Announced ONCE, because it does not recover within a
    # drive and a repeating alert for a permanent condition is noise.
    accPassthroughInert @27;
  }
}

struct CarParamsSP @0x80ae746ee2596b11 {
  flags @0 :UInt32;        # flags for car specific quirks in sunnypilot
  safetyParam @1 : Int16;  # flags for sunnypilot's custom safety flags
  pcmCruiseSpeed @3 :Bool;
  intelligentCruiseButtonManagementAvailable @4 :Bool;
  enableGasInterceptor @5 :Bool;

  neuralNetworkLateralControl @2 :NeuralNetworkLateralControl;

  struct NeuralNetworkLateralControl {
    model @0 :Model;
    fuzzyFingerprint @1 :Bool;

    struct Model {
      path @0 :Text;
      name @1 :Text;
    }
  }
}

struct CarControlSP @0xa5cd762cd951a455 {
  mads @0 :ModularAssistiveDrivingSystem;
  params @1 :List(Param);
  leadOne @2 :LeadData;
  leadTwo @3 :LeadData;
  intelligentCruiseButtonManagement @4 :IntelligentCruiseButtonManagement;

  struct Param {
    key @0 :Text;
    type @2 :ParamType;
    value @3 :Data;

    valueDEPRECATED @1 :Text; # The data type change may cause issues with backwards compatibility.
  }

  enum ParamType {
    string @0;
    bool @1;
    int @2;
    float @3;
    time @4;
    json @5;
    bytes @6;
  }
}

struct BackupManagerSP @0xf98d843bfd7004a3 {
  backupStatus @0 :Status;
  restoreStatus @1 :Status;
  backupProgress @2 :Float32;
  restoreProgress @3 :Float32;
  lastError @4 :Text;
  currentBackup @5 :BackupInfo;
  backupHistory @6 :List(BackupInfo);

  enum Status {
    idle @0;
    inProgress @1;
    completed @2;
    failed @3;
  }

  struct Version {
    major @0 :UInt16;
    minor @1 :UInt16;
    patch @2 :UInt16;
    build @3 :UInt16;
    branch @4 :Text;
  }

  struct MetadataEntry {
    key @0 :Text;
    value @1 :Text;
    tags @2 :List(Text);
  }

  struct BackupInfo {
    deviceId @0 :Text;
    version @1 :UInt32;
    config @2 :Text;
    isEncrypted @3 :Bool;
    createdAt @4 :Text;  # ISO timestamp
    updatedAt @5 :Text;  # ISO timestamp
    sunnypilotVersion @6 :Version;
    backupMetadata @7 :List(MetadataEntry);
  }
}

struct CarStateSP @0xb86e6369214c01c8 {
  speedLimit @0 :Float32;
}

struct LiveMapDataSP @0xf416ec09499d9d19 {
  speedLimitValid @0 :Bool;
  speedLimit @1 :Float32;
  speedLimitAheadValid @2 :Bool;
  speedLimitAhead @3 :Float32;
  speedLimitAheadDistance @4 :Float32;
  roadName @5 :Text;
}

struct ModelDataV2SP @0xa1680744031fdb2d {
  laneTurnDirection @0 :TurnDirection;

  enum TurnDirection {
    none @0;
    turnLeft @1;
    turnRight @2;
  }
}

struct CustomReserved10 @0xcb9fd56c7057593a {
}

struct CustomReserved11 @0xc2243c65e0340384 {
}

struct CustomReserved12 @0x9ccdc8676701b412 {
}

struct ControllerStateBP @0xcd96dafb67a082d0 {
  lateralUncertainty @0 :Float32;  # BluePilot: lateral uncertainty for angleState (e.g. torque bar)
  # BluePilot: lateral rate-limit diagnostics (troubleshooting)
  angleRateLimited     @1 :Bool;  # angle mode: the path_angle soft-ROC clip actually bit this frame
  curvatureRateLimited @2 :Bool;  # would the equivalent curvature have been rate-limited by lateral_curv_ext (sim)
  # BluePilot: did the current_curvature +- CURVATURE_ERROR clip constrain the commanded curvature
  # this frame (deviation from measured, not rate-of-change)? Set by whichever strategy is active
  # (lateral_curv_ext.py or lateral_angle_ext.py) -- tells you what limited a maneuver: ROC
  # (angleRateLimited) vs deviation from current curvature (this).
  curvatureDeviationLimited @3 :Bool;
  humanTurnLateralPaused @4 :Bool;  # angle mode: lateral forced inactive (mode 0) during a manual turn
  stallBlipActive @5 :Bool;  # angle mode: brief mode-0 pulse resetting PSCM authority after a post-override stall

  # BluePilot: full BluePilot-menu settings snapshot, for PlotJuggler/route analysis without
  # reading logs. "bms" = BluePilot Menu Setting. One field per on-device menu item (TICI + MICI
  # union); refreshed on a cache interval, not every frame -- see bp_card_publisher.py.
  # CONVENTION: when adding a new menu item, add a field here and publish it in
  # bp_card_publisher.py. When removing a menu item, rename the field to bmsRemovedSpareNN (keep
  # the @N slot occupied -- never renumber or reuse a retired field's ordinal).

  # --- System ---
  bmsUiDebugLogging @6 :Bool;  # BPUIDebugLog
  bmsConnectBackend @7 :UInt8;  # BPConnectBackend: 0=Comma Connect, 1=Konik Stable, 2=Offline Mode
  bmsWebRoutesServerEnabled @8 :Bool;  # EnableWebRoutesServer
  bmsPreferredWifiNetwork @9 :Text;  # WifiFavoriteSSID

  # --- Vehicle ---
  bmsShowBlueCruiseUiOnCluster @10 :Bool;  # send_hands_free_cluster_msg
  bmsTwelveVBatteryLimit @11 :Float32;  # vbatt_pause_charging

  # --- Visuals ---
  bmsHideOnroadBorder @12 :Bool;  # BPHideOnroadBorder
  bmsDisableLaneLineStatusColor @13 :Bool;  # BPDisableLaneLineStatusColor
  bmsMinimalDrivingView @14 :Bool;  # BPHideCameraView
  bmsEightBitRacerTheme @15 :Bool;  # BPRadRacerTheme (TICI only)
  bmsRainbowLaneLines @16 :Bool;  # BPRainbowLines
  bmsShowBlindspotOverlay @17 :Bool;  # ShowBlindspotOverlay
  bmsShowBrakeStatus @18 :Bool;  # ShowBrakeStatus
  bmsShowConfidenceBall @19 :Bool;  # BPShowConfidenceBall (TICI only)
  bmsAnimateSteeringWheel @20 :Bool;  # BPAnimateSteeringWheel
  bmsWheelIconStyle @21 :UInt8;  # BPSteeringWheelIconStyle: 0=Comma 4, 1=Comma 3x
  bmsShowRadarLeadOverlay @22 :Bool;  # FordPrefShowRadarLeadOverlay
  bmsRadarOverlaySize @23 :UInt8;  # FordPrefRadarOverlaySize: 0=small, 1=medium, 2=large
  bmsShowHybridBatteryStatus @24 :Bool;  # FordPrefHybridBatteryStatus
  bmsShowHybridPowerFlow @25 :Bool;  # FordPrefHybridPowerFlow
  bmsHybridDriveGaugeSize @26 :UInt8;  # FordPrefHybridDriveGaugeSize: 1=small, 2=large
  bmsHybridGaugeStyle @27 :UInt8;  # FordPrefGaugeStyle: 0=flat, 1=arched (TICI only)
  bmsHybridPowerFlowStyleRound @28 :Bool;  # FordPrefHybridPowerFlowAlternate (MICI only)
  bmsLowerRightDisplay @29 :UInt8;  # mici_complication: 0=off..4=time to lead (MICI only)
  bmsRainbowMode @30 :Bool;  # RainbowMode -- upstream sunnypilot param, shown in BP MICI visuals
  bmsHideOnroadFade @31 :Bool;  # mici_hide_onroad_fade (MICI only)

  # --- Longitudinal Tuning ---
  bmsBypassBpLongitudinalControl @32 :Bool;  # disable_BP_long_UI
  bmsDisableDownhillCompensation @33 :Bool;  # disable_downhill_comp_UI
  bmsDisableFordRadarVisionOnly @34 :Bool;  # disable_ford_radar_UI

  # --- Lateral Tuning ---
  bmsDisableBpLateralControl @35 :Bool;  # disable_BP_lat_UI
  bmsPrimaryControlVariable @36 :UInt8;  # FordPrefLateralControl: 0=curvature, 1=angle
  bmsDisableLaneChangeUnderSpeed @37 :Bool;  # BlinkerPauseLaneChange
  bmsMinimumSpeedToPauseLaneChange @38 :UInt8;  # BlinkerMinLateralControlSpeed
  bmsShowLateralControlMode @39 :Bool;  # BpShowLateralControl

  # --- Angle Tuning (angle-mode-only menu items) ---
  bmsLowSpeedAdjustmentFactor @40 :Float32;  # FordLowSpeedFactor_ang
  bmsHighSpeedAdjustmentFactor @41 :Float32;  # FordHighSpeedFactor_ang
  bmsLaneChangeFactorHighAngle @42 :Float32;  # lane_change_factor_high_ang

  # --- Curvature Tuning (curvature-mode-only menu items) ---
  bmsEnableHumanTurnDetection @43 :Bool;  # enable_human_turn_detection_curv
  bmsLaneChangeFactorHighCurvature @44 :Float32;  # lane_change_factor_high_curv
  bmsEnableLanePositioning @45 :Bool;  # enable_lane_positioning_curv
  bmsInLaneOffset @46 :Float32;  # custom_path_offset_curv
  bmsEnableLanefullMode @47 :Bool;  # enable_lane_full_mode_curv
  bmsUseCustomTuningProfile @48 :Bool;  # custom_profile_curv
  bmsPredictedCurvatureBlendRatioHigh @49 :Float32;  # pc_blend_ratio_high_C_UI_curv
  bmsPredictedCurvatureBlendRatioLow @50 :Float32;  # pc_blend_ratio_low_C_UI_curv
  bmsCenteringPidGain @51 :Float32;  # LC_PID_gain_UI_curv

  # --- Fingerprint (not a menu item, but requested alongside the settings snapshot) ---
  bmsFingerprintForced @52 :Bool;  # true when CarParams.fingerprintSource == fixed (CarPlatformBundle / FINGERPRINT env)
  bmsFingerprint @53 :Text;  # CarParams.carFingerprint

  # BluePilot: lateral mode the car controller actually ran this frame (not the param).
  # Only published by Ford BP, so other cars show nothing.
  activeLateralMode @54 :LateralMode;

  # FusionPilot: the angle-mode command and the gain that produced it. NONE of this reached a route
  # before 2026-09-01, and answering "why did it under-deliver on that curve" therefore needed the
  # CAN wire decoded (LateralMotionControl 0x3D3 off sendcan) plus a re-implementation of the gain
  # schedule in a tool -- a number only one tool can produce has never been checked.
  #
  # `curvature_factor` is the one that matters. It is an interpolation over |kappa_cmd| between a
  # low-curvature and a high-curvature gain, so the SAME settings deliver different authority
  # depending on how bent the road is -- and on 2026-09-01 that cost real curve authority with
  # nothing in any log able to show it. Publishing kappaCmd beside the two anchors makes the whole
  # ramp reconstructible from a drive instead of modelled.
  #
  # laneCenterCorrection is the lane-centering trim's own contribution, added INTO kappa_cmd
  # upstream of every limiter. It is the only closed position loop in the lateral stack and the
  # measured cause of a 29-44 cm straight-road weave, which was likewise invisible in the logs.
  pathAngleFinal       @55 :Float32;  # rad, the commanded path angle after all limits
  kappaCmd             @56 :Float32;  # 1/m, commanded curvature -- where on the gain ramp we sat
  curvatureFactor      @57 :Float32;  # the gain actually applied to kappa_cmd * v_ego
  laneCenterCorrection @58 :Float32;  # 1/m, lane-centering trim's contribution to kappaCmd
  gainLowCurv          @59 :Float32;  # ramp anchor at |kappa| = 0.0005, after user factors
  gainHighCurv         @60 :Float32;  # ramp anchor at the speed-dependent boundary
  blendWeight          @61 :Float32;  # predicted-vs-desired blend weight actually used this frame

  # FusionPilot 2026-09-04: the ANGLE-mode lane-positioning settings. The bms block above carries
  # `enable_lane_positioning_curv`, `custom_path_offset_curv` and `LC_PID_gain_UI_curv` -- all
  # three are the CURVATURE-mode keys, and this car runs angle mode, so the five settings that
  # actually govern it were absent from a struct whose stated purpose is one field per menu item.
  #
  # THAT COST A MEASUREMENT. On 2026-09-04 `lane_centering_strength_ang` was raised 0.35 -> 0.45
  # mid-drive; `initData.params` is a boot snapshot and cannot see a mid-route change, and nothing
  # on the wire carried the value either, so the drive could not be split on the change it was
  # recorded to test. Every setting whose effect anyone might A/B has to reach a route.
  bmsHighSpeedDampening        @62 :Float32;  # FordHighSpeedDampening_ang -- the ramp LEVEL
  bmsEnableLanePositioningAng  @63 :Bool;     # enable_lane_positioning_ang
  bmsInLaneOffsetAng           @64 :Float32;  # custom_path_offset_ang
  bmsLaneCenteringStrength     @65 :Float32;  # lane_centering_strength_ang
  bmsLaneCenteringDamping      @66 :Float32;  # lane_centering_damping_ang

  enum LateralMode {
    openpilot @0;  # BP lateral bypassed (disable_BP_lat_UI)
    curvature @1;
    angle @2;
  }
}

struct CarStateBP @0xb057204d7deadf3f {
  hybridDrive @0 :HybridDrive;
  hybridBattery @1 :HybridBattery;
  brakeLightStatus @2 :BrakeLightStatus;
  trafficSignData @3 :TrafficSignData;

  # BluePilot: the ACC follow gap the camera is reporting, AccTGap_D_Dsply from ACCDATA_3
  # (Time_Gap_1..5; 0 means the camera is not reporting a usable value).
  #
  # Logged for its own sake, not for a feature. What Time_Gap_1..5 actually ARE in seconds is
  # unknown -- the owner set it by feel and believes 3 of 5 gives him about two seconds -- and the
  # only way to find out is to drive with different settings and measure the gap the radar reports
  # against speed. That measurement needs the setting recorded beside the lead distance in the same
  # route, which is all this field is for.
  accGap @4 :UInt8;

  # BluePilot: every signal in Traffic_RecognitnData (0x3CD), raw, for investigation.
  # Only vLimit1 and vLimitUnit feed the speed limit resolver; the rest are logged so what the
  # camera actually reports can be read off a route instead of guessed at.
  #
  # Note there is NO curve / advisory / warning-sign field anywhere in this message. The only two
  # "warn" signals are wrongWayAlert and overSpeedWarn (you are exceeding the detected limit).
  # Ford TSR reports speed limits and overtaking restrictions only.
  struct TrafficSignData {
    dataAvailable @0 :Bool;

    vLimit1 @1 :UInt8;          # TsrVLim1MsgTxt_D_Rq
    vLimit2 @2 :UInt8;          # TsrVLim2MsgTxt_D_Rq -- conditional/supplementary limit
    vLimitUnit @3 :UInt8;       # TsrVlUnitMsgTxt_D_Rq: 1=km/h, 2=mph
    vLimit1Status @4 :UInt8;    # TsrVl1StatMsgTxt_D_Rq
    vLimit2Status @5 :UInt8;    # TsrVl2StatMsgTxt_D_Rq
    vLimit1Restrict @6 :UInt8;  # TsrVl1RstrcMsgTxt_D_Rq -- e.g. wet/time/vehicle-class qualifier
    vLimit2Restrict @7 :UInt8;  # TsrVl2RstrcMsgTxt_D_Rq
    vLimit1Restrict2 @8 :UInt8; # TsrVl1RstrcMsgTxt2_D_Rq
    vLimit2Restrict2 @9 :UInt8; # TsrVl2RstrcMsgTxt2_D_Rq
    vLimit1Permanent @10 :UInt8;  # TsrVl1PrmntMsgTxt_D_Rq -- permanent vs temporary (roadworks)
    vLimit2Permanent @11 :UInt8;  # TsrVl2PrmntMsgTxt_D_Rq

    overtakeMsg @12 :UInt8;       # TsrOvtkMsgTxt_D_Rq
    overtakeMsg2 @13 :UInt8;      # TsrOvtkMsgTxt2_D_Rq
    overtakeStatus @14 :UInt8;    # TsrOvtkStatMsgTxt_D_Rq

    tsrMsg @15 :UInt8;            # TsrMsgTxt_D_Rq
    tsrStatus @16 :UInt8;         # TsrStatMsgTxt_D_Rq
    overSpeedWarn @17 :UInt8;     # TsrOswWarnMsgTxt_D_Rq -- exceeding the detected limit
    wrongWayAlert @18 :Bool;      # WwaWarn_B_Rq
  }

  struct HybridDrive {
    dataAvailable @0 :Bool;
    throttleDemandPercent @1 :Float32;
    throttleThresholdPercent @2 :Float32;
    powerFlowMode @3 :Text;
    powerFlowModeValue @5 :UInt8;  # Raw numeric value for PlotJuggler compatibility
    engineOnReason @4 :Text;
    engineOnReasonValue @6 :UInt8;  # Raw numeric value for PlotJuggler compatibility
  }

  struct HybridBattery {
    dataAvailable @0 :Bool;
    voltHighLimit @1 :Float32;
    voltLowLimit @2 :Float32;
    voltActual @3 :Float32;
    ampsActual @4 :Float32;  # from MtrTracData_1_FD1; Battery_Traction_1 carries no current on Mach-E
    socMinPerc @5 :Float32;
    socMaxPerc @6 :Float32;
    socActual @7 :Float32;
  }

  struct BrakeLightStatus {
    dataAvailable @0 :Bool;
    brakeLightsOn @1 :Bool;

    # BluePilot: what Ford's own ACC is asking the brakes to do, read from the stock ACCDATA the
    # camera sends. Kept separate from brakeLightsOn on purpose -- the lamp is what traffic behind
    # you sees, these are what the system is actually doing, and they are not the same event.
    # Precharge pressurises the system without meaningful deceleration and normally lights nothing.
    accDataAvailable @2 :Bool;
    accDecelRequest @3 :Bool;      # AccBrkDecel_B_Rq
    accPrechargeRequest @4 :Bool;  # AccBrkPrchg_B_Rq
    accAccelRequest @5 :Float32;   # AccBrkTot_A_Rq, m/s^2 (negative is deceleration)
    # BluePilot: the propulsion side. AccBrkTot_A_Rq above is the BRAKE total despite its
    # capnp name, so on its own it cannot tell accelerating from coasting.
    accPropulsionRequest @6 :Float32;  # AccPrpl_A_Rq, m/s^2
  }
}

struct CustomReserved15 @0xbd443b539493bc68 {
}

struct CustomReserved16 @0xfc6241ed8877b611 {
}

# FusionPilot: mapd v2 (pfeiferj/openpilot-mapd), copied from its cereal/custom/custom.capnp.
#
# THESE ARE NOT OURS TO EDIT. The mapd binary is compiled against its own copy of this schema and
# writes these messages onto the wire; capnp reads by POSITION, so changing a field's ordinal here
# does not rename a field, it silently reads a different one. If mapd adds a field, take theirs
# verbatim. If we want a field of our own, it goes in a struct of ours, not in one of these.
#
# The three struct IDs below were already the reserved ones -- upstream cereal's rule is "DO rename
# the structs, DON'T change the identifier", and mapd claims the same three, so this is a rename in
# place rather than a new claim. CustomReserved17/18/19 they were, @143/@144/@145 they stay in
# log.capnp's Event.
#
# NOTE `suggestedSpeed` is mapd's own final answer -- the minimum of its speed-limit and curve
# numbers, meant to be clamped straight onto v_cruise by forks with nothing in that layer. WE DO NOT
# CONSUME IT. It cannot know about ICBM's button presses, about holds, or about the four SCC-Map
# defenses that were each built from a measured event on this car. The INGREDIENTS are what we take:
# speedLimitSuggestedSpeed, mapCurveSpeed and visionCurveSpeed, as inputs beside the camera.

struct MapdDownloadLocationDetails @0xff889853e7b0987f {
  location @0 :Text;
  totalFiles @1 :UInt32;
  downloadedFiles @2 :UInt32;
}

struct MapdDownloadProgress @0xfaa35dcac85073a2 {
  active @0 :Bool;
  cancelled @1 :Bool;
  totalFiles @2 :UInt32;
  downloadedFiles @3 :UInt32;
  locations @4 :List(Text);
  locationDetails @5 :List(MapdDownloadLocationDetails);
}

# One point on the road ahead. This is the structural gain over v1: today SCC-Map is handed a single
# corner speed at the instant braking must begin, with no context, which is why four defenses had to
# be built to question it. A list of these is the whole profile ahead.
struct MapdPathPoint @0xd6f78acca1bc3939 {
  latitude @0 :Float64;
  longitude @1 :Float64;
  curvature @2 :Float32;
  targetVelocity @3 :Float32;
}

struct MapdPosition @0xde9705979aca8339 {
  latitude @0 :Float64;
  longitude @1 :Float64;
}

struct MapdExtendedOut @0xa30662f84033036c {
  downloadProgress @0 :MapdDownloadProgress;
  settings @1 :Text;
  path @2 :List(MapdPathPoint);
  position @3 :MapdPosition;
  # mapd v2.3.1 appended these two. Taken VERBATIM from pfeiferj's own custom.capnp -- the Mapd*
  # structs are THEIRS and capnp reads by POSITION, so our copy must match theirs field for field.
  # Additive at the end, so a v2.3.0 binary (which sends neither) still decodes correctly here.
  #
  # They are also the metric that scores the upgrade: v2.3.1's release note is "message publishing
  # is now on its own thread that ensures a constant 20 hz publish rate", and mapdOut was measured
  # at 13.85-16.16 Hz on 2026-09-04/05 with mapdExtendedOut at 0.96 Hz. Read these back on the next
  # drive instead of re-deriving the rate from message timestamps.
  loopRateAverage @4 :Float32;
  loopRateMin @5 :Float32;
}

enum MapdInputType {
  download @0;
  reloadSettings @9;
  saveSettings @10;
  loadDefaultSettings @21;
  loadRecommendedSettings @22;
  loadPersistentSettings @26;
  cancelDownload @27;
  setJsonPathFloat @43;
  setJsonPathText @44;
  setJsonPathBool @45;
  acceptSpeedLimit @34;

  # DEPRECATED settings inputs
  setLogLevel @6;
  setLogSource @29;
  setLogJson @28;
  setTargetLateralAccel @1;
  setSpeedLimitOffset @2;
  setSpeedLimitControl @3;
  setMapCurveSpeedControl @4;
  setVisionCurveSpeedControl @5;
  setVisionCurveTargetLatA @7;
  setVisionCurveMinTargetV @8;
  setEnableSpeed @11;
  setVisionCurveUseEnableSpeed @12;
  setMapCurveUseEnableSpeed @13;
  setSpeedLimitUseEnableSpeed @14;
  setHoldLastSeenSpeedLimit @15;
  setTargetSpeedJerk @16;
  setTargetSpeedAccel @17;
  setTargetSpeedTimeOffset @18;
  setDefaultLaneWidth @19;
  setMapCurveTargetLatA @20;
  setSlowDownForNextSpeedLimit @23;
  setSpeedUpForNextSpeedLimit @24;
  setHoldSpeedLimitWhileChangingSetSpeed @25;
  setExternalSpeedLimitControl @30;
  setExternalSpeedLimit @31;
  setSpeedLimitPriority @32;
  setSpeedLimitChangeRequiresAccept @33;
  setPressGasToAcceptSpeedLimit @35;
  setAdjustSetSpeedToAcceptSpeedLimit @36;
  setAcceptSpeedLimitTimeout @37;
  setPressGasToOverrideSpeedLimit @38;
  setConditionalSpeedLimitControl @39;
  setShadowCarState @40;
  setShadowModelV2 @41;
  setShadowGpsLocation @42;
  setShadowGpsLocationExternal @46;
}

# `fail` is the one this fork has been missing: it says the map is LOST rather than confident and
# wrong. Six consecutive positions on US 40/189 were measured on 2026-08-16 where the tile on this
# device holds 65 mph and Speed Limit Assist showed nothing, and today that failure is silent.
enum WaySelectionType {
  current @0;
  predicted @1;
  possible @2;
  extended @3;
  fail @4;
}

enum SpeedLimitOffsetType {
  static @0;
  percent @1;
}

struct MapdIn @0xc86a3d38d13eb3ef {
  type @0 :MapdInputType;
  float @1 :Float32;
  str @2 :Text;
  bool @3 :Bool;
  jsonPath @4 :Text;
}

enum RoadContext {
  freeway @0;
  city @1;
  unknown @2;
}

# WARNING: must be kept in perfect sync (names and values) with the
# HighwayClass enum in cereal/offline/offline.capnp -- state.go casts directly
# between the two generated enum types.
# unknown either means the way's highway tag was not one of the listed values
# or the loaded map tiles predate this field.
#
# FusionPilot note: motorway vs motorwayLink is freeway vs on/off-ramp, stated as a fact instead of
# inferred. The tiles on this device already carry it -- 292 motorway against 403 motorwayLink in
# the Salt Lake box, measured 2026-08-16 from a tile downloaded 2026-08-02. Our copy of the tile
# schema is tools/bp_offline_tile.capnp and test_mapd_schema.py checks the two agree.
enum HighwayClass {
  unknown @0;
  motorway @1;
  motorwayLink @2;
  trunk @3;
  trunkLink @4;
  primary @5;
  primaryLink @6;
  secondary @7;
  secondaryLink @8;
  tertiary @9;
  tertiaryLink @10;
  unclassified @11;
  residential @12;
  livingStreet @13;
}

struct MapdOut @0xa4f1eb3323f5f582 {
  wayName @0 :Text;
  wayRef @1 :Text;
  roadName @2 :Text;
  speedLimit @3 :Float32;
  nextSpeedLimit @4 :Float32;
  nextSpeedLimitDistance @5 :Float32;
  hazard @6 :Text;
  nextHazard @7 :Text;
  nextHazardDistance @8 :Float32;
  advisorySpeed @9 :Float32;
  nextAdvisorySpeed @10 :Float32;
  nextAdvisorySpeedDistance @11 :Float32;
  oneWay @12 :Bool;
  lanes @13 :UInt8;
  tileLoaded @14 :Bool;
  speedLimitSuggestedSpeed @15 :Float32;
  suggestedSpeed @16 :Float32;
  estimatedRoadWidth @17 :Float32;
  roadContext @18 :RoadContext;
  distanceFromWayCenter @19 :Float32;
  visionCurveSpeed @20 :Float32;
  mapCurveSpeed @21 :Float32;
  waySelectionType @22 :WaySelectionType;
  speedLimitAccepted @23 :Bool;
  highwayClass @24 :HighwayClass;
  wayId @25 :Int64;
  conditionalSpeedLimit @26 :Text;
}
