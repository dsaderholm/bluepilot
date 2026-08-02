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
  passingAssist @9 :PassingAssist;

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

  # BluePilot: passing-assist observation channel. LOG ONLY -- nothing reads this to make a
  # decision, and no alert or actuation is wired to it. It answers three questions that can only
  # be settled from real drive data:
  #
  #   1. How often does the geometry test claim a lane to the left on an UNDIVIDED road, where
  #      that lane is oncoming traffic? The model publishes geometry, not direction of travel, so
  #      this is the failure mode that decides whether the idea is viable at all. Both the
  #      painted-line evidence (lineProb) and the drivable-width evidence (edgeGap) are recorded
  #      separately so they can be compared as discriminators rather than assumed equivalent.
  #   2. Does this market's camera populate Traffic_RecognitnData's overtaking fields at all?
  #      Ford documents TSR around Vienna Convention signage; US no-passing zones are a solid
  #      yellow line plus a rectangular MUTCD sign. overtakeRestricted may simply never go true.
  #   3. Is BLIS informative here? It reports blind-spot OCCUPANCY, not approach -- a car closing
  #      from well behind does not light it until already alongside. Recorded at decision time so
  #      its correlation with a genuinely safe gap can be measured.
  #
  # The suggestion field is what the system WOULD have said. blockedBy is why it stayed silent,
  # which is the more useful field: it shows which gate is actually doing the work.
  struct PassingAssist {
    suggestion @0 :Side;
    blockedBy @1 :Blocked;
    stuckSeconds @2 :Float32;     # continuous time held below set speed by this lead

    # lead evidence
    hasLead @3 :Bool;
    leadDRel @4 :Float32;
    leadVLead @5 :Float32;
    speedDeficit @6 :Float32;     # set speed - lead speed, m/s. The reason to want to pass.

    # geometry evidence, per side. lineProb is the model's confidence in a painted line BEYOND
    # ego's own lane line; edgeGap is metres of drivable width between ego's lane line and the
    # road edge. On a divided highway in the left lane, edgeGap collapses to the shoulder. On an
    # undivided road it does NOT -- the oncoming lane is drivable width -- which is exactly the
    # discrimination this is here to measure.
    leftLineProb @7 :Float32;
    rightLineProb @8 :Float32;
    leftEdgeGap @9 :Float32;
    rightEdgeGap @10 :Float32;
    leftGeometryOk @11 :Bool;
    rightGeometryOk @12 :Bool;

    # BLIS at decision time. Mirrors carState, which folds sensor-fault and blocked into "occupied".
    leftBlindspot @13 :Bool;
    rightBlindspot @14 :Bool;
    blindspotAvailable @15 :Bool; # false until the BLIS messages reach the bus openpilot reads

    # TSR overtaking veto. See the Traffic_RecognitnData value tables: TsrOvtkMsgTxt_D_Rq is a
    # LATCHED zone state (Lim* in force, LimAllCancelled = zone ended), not a momentary sign
    # event, and TsrOvtkStatMsgTxt_D_Rq is its confidence channel.
    overtakeRestricted @16 :Bool;
    overtakeMsg @17 :UInt8;
    overtakeStatus @18 :UInt8;
    tsrAvailable @19 :Bool;

    # BluePilot: which question produced the suggestion. Both can land on Side.right and they mean
    # opposite things -- passing on the right is an overtake, keepRight is returning to the travel
    # lane after one. Without this the log cannot tell them apart.
    reason @20 :Reason;
    keepRightSeconds @21 :Float32;
    rearLeft @23 :RearApproach;
    rearRight @24 :RearApproach;

    # BluePilot: how much the road opens up to the right between roughly 3 m and 75 m ahead,
    # measured as the growth in the gap between ego's right lane line and the right road edge.
    # A through lane holds that gap constant; an exit, on-ramp or pullout grows it. Because both
    # the lane line and the road edge bend together through a curve, the difference between them
    # is curvature-invariant, which a raw road-edge heading would not be.
    #
    # Logged whether or not it is acting, because it is unproven against real roads and the
    # threshold wants fitting from drive data rather than from argument.
    rightWideningM @25 :Float32;
    rightWidening @26 :Bool;    # exceeded the threshold: treated as a possible exit or merge

    # BluePilot: which situation produced the suggestion.
    trigger @27 :Trigger;
    leadTtc @28 :Float32;       # seconds to reach the lead at the current closing rate
    approachSeconds @29 :Float32;

    # BluePilot: was Ford's ACC already asking for brakes when we decided?
    #
    # This is the quality metric for the whole preemptive path, not diagnostics. On stock ACC the
    # costly sequence is brake-then-accelerate: ACC sheds speed for a lead we were always going to
    # pass, then has to win it back in the other lane. A suggestion that lands BEFORE
    # accDecelRequest goes true is one that could have avoided the deceleration entirely; one that
    # lands after is merely tidying up. Logged so "did we beat ACC" is answerable from a drive
    # rather than assumed.
    accBrakingAtDecision @30 :Bool;
    accBrakingAvailable @31 :Bool;

    # BluePilot: driver-requested pause. Construction zones, weather, unfamiliar roads -- times
    # when the geometry is unusual and no amount of gating substitutes for a driver saying "not
    # here". Times out on its own rather than latching off: a suspend you have to remember to undo
    # is one that silently disables the feature for the rest of the month.
    suspendedSeconds @32 :Float32;

    enum Trigger {
      none @0;
      heldUp @1;      # already behind it and below the set speed -- the reactive case
      approaching @2; # closing on something slower, not yet slowed -- pass without losing speed
    }

    # BluePilot: traffic closing from behind in the adjacent lane. NO SOURCE EXISTS YET -- every
    # field reports unavailable until one is fitted. It is defined now because the shape of this
    # answer determines the shape of the gate, and retrofitting a gate into a state machine after
    # the fact is how the ordering bugs get in.
    #
    # Ford BLIS cannot fill this. carState.leftBlindspot is SodDetct*_D_Stat != 0 -- blind-spot
    # OCCUPANCY -- so a vehicle closing at 25 mph from 150 m back does not register until it is
    # already alongside, which is far too late to plan a lane change against. Whether sodStat or
    # sodAlert encode anything approach-like is the open question the raw BLIS logging exists to
    # answer; if they do, a BLIS adapter fills this in categories with ttc unset.
    #
    # Modelled on the RADAR shape (range, closing rate, derived TTC) rather than the BLIS shape,
    # deliberately: radar carries strictly more information, so a BLIS source can be adapted UP
    # into these fields losing nothing, while the reverse would throw away exactly the numbers the
    # decision needs. ESR.dbc is the reference -- range, range-rate and angle per target.
    struct RearApproach {
      available @0 :Bool;      # false = nothing is watching this side. NOT the same as "clear".
      detected @1 :Bool;       # something is there at all
      closing @2 :Bool;        # ...and gaining on us
      dRel @3 :Float32;        # metres behind, 0 when unknown
      vRel @4 :Float32;        # m/s, positive = closing
      ttc @5 :Float32;         # seconds until it reaches us, large when not closing
      source @6 :Source;
    }

    enum Source {
      none @0;      # no rear sensing fitted
      blis @1;      # derived from Side_Detect_L/R_Stat, categories only, ttc unset
      radar @2;     # a rear-facing radar object list
    }

    # BluePilot: LiveMapDataSP.roadName at decision time. Recorded because it is the cheapest
    # candidate for the divided-highway gate that geometry cannot provide: mapd already publishes
    # it and the UI already renders it, so if it carries a usable interstate identifier the
    # oncoming-lane problem is solvable today without forking mapd or waiting for navigation.
    # Logged before being trusted -- what pfeiferj's mapd puts here for a US interstate is
    # unverified, and a filter written against a guessed format would silently never match.
    roadName @22 :Text;

    enum Reason {
      none @0;
      passing @1;    # a slower lead is holding us below the set speed
      keepRight @2;  # nothing is holding us back and a lane exists to the right
    }

    enum Side {
      none @0;
      left @1;
      right @2;
    }

    # Why no suggestion was made. Ordered by evaluation, first failing gate wins.
    enum Blocked {
      none @0;              # a suggestion was made
      disabled @1;
      notEngaged @2;
      tooSlow @3;
      driverActive @4;      # blinker, brake or steering input -- driver already acting
      noLead @5;
      notStuck @6;          # lead present but not holding us back, or not for long enough
      noLaneAvailable @7;   # geometry says there is nowhere to go on either side
      blindspotOccupied @8; # geometry was fine, BLIS was not
      overtakeRestricted @9; # TSR reports a no-overtaking zone in force
      rearApproaching @10;   # something is closing on that lane from behind
      suspended @11;         # driver paused it -- construction zone, weather, unfamiliar road
    }
  }

  struct DynamicExperimentalControl {
    state @0 :DynamicExperimentalControlState;
    enabled @1 :Bool;
    active @2 :Bool;

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
  blisLeft @4 :SideDetect;
  blisRight @5 :SideDetect;
  blinkerTest @6 :BlinkerTest;

  # BluePilot: bench test for whether openpilot can operate the turn signal on this car.
  #
  # Standing question, because desire_helper's whole lane-change state machine keys off
  # carState.leftBlinker/rightBlinker, and those come from the SCCM's own Steering_Data_FD1 on
  # bus 0. openpilot already transmits that message and passes TurnLghtSwtch_D_Stat through
  # untouched; panda explicitly permits it (ford.h: "blinkers, wiper switches, high beam ...
  # which we passthru in OP"). ICBM proves the car acts on openpilot's injected copy of this exact
  # frame for cruise buttons. Whether the BCM honours it for the turn signal is untested.
  #
  # lampLeft/lampRight are the ANSWER, read from BodyInfo_3_FD1 (0x3B3) -- the body module's own
  # report of what the lamps are doing, on a bus openpilot already parses. That makes this a
  # closed-loop measurement rather than a guess, and needs no extra hardware.
  #
  # Note the lamps FLASH, so lampLeft/lampRight toggle at the flash rate while commanded; a
  # consumer wants to latch over a flash period rather than read a single frame.
  struct BlinkerTest {
    state @0 :State;
    commanded @1 :UInt8;      # what we put in TurnLghtSwtch_D_Stat: 0 none, 1 left, 2 right
    secondsRemaining @2 :Float32;
    lampLeft @3 :Bool;        # TurnLghtLeftOn_B_Stat, BodyInfo_3_FD1
    lampRight @4 :Bool;       # TurnLghtRightOn_B_Stat
    lampSeen @5 :Bool;        # a commanded lamp was observed lit at least once this pulse
    blockedReason @6 :Blocked;

    enum State {
      idle @0;
      pulsing @1;
      done @2;
    }

    # Why a requested pulse did not run. Standstill is not negotiable: this operates a lamp other
    # drivers read, and a stationary car signals nothing about a manoeuvre.
    enum Blocked {
      none @0;
      notStationary @1;
      cruiseEngaged @2;      # never fight a live lane-change decision
      driverSignalling @3;   # the driver's own stalk wins, always
    }
  }

  # BluePilot: every signal in Side_Detect_L/R_Stat (0x3A6 / 0x3A7), raw.
  #
  # openpilot reduces all of this to one bool per side (SodDetct != 0). These are logged in full
  # because that bool discards sensor health, and because the raw values are the only way to check
  # what this particular car does rather than what the DBC claims.
  #
  # CORRECTION (researched 2026-08-02): an earlier version of this comment said sodStat and
  # sodAlert were undocumented and might encode approach. Both are documented, and neither does:
  #
  #   Sod*_D_Stat      Off / Trailer_Tow_Off / On / Disabled / Invalid -- the SYSTEM's enable
  #                    state, not a detection at all
  #   SodAlrt*_D_Stat  Off / On / Flash / Bulb_Proveout -- the mirror LAMP's state. Per Ford's own
  #                    documentation the flash is triggered by the DRIVER's turn signal toward an
  #                    occupied side, so it is a function of our own stalk, not of the other
  #                    vehicle's closing rate
  #   SodSns*_D_Stat   Clear / Blocked / System_Failure / Second_Warning_Audio -- sensor health
  #   SodDetct*_D_Stat AlertOff / Alert_On / Flash_On / Sensor_Fault / Sensor_Blocked
  #
  # So Ford BLIS answers "is that side occupied" and nothing more. It cannot support a lane-change
  # decision that needs to know about traffic still approaching from behind; only a rear-facing
  # radar can. See RearApproach, whose blis source is presence-only by design.
  #
  # The two signals that genuinely have no value table are sodWarnPeriodMs (lamp flash period) and
  # illumPercent (lamp brightness). Both cosmetic. ctaAlert2 does carry AlertZone1-4, which is real
  # zone information, but Cross Traffic Alert runs in reverse at parking speeds -- logged in case
  # a build reuses it, not expected to help.
  #
  # Note SodDetct's own table is: 0=clear 1=Alert_On 2=Flash_On 3=Sensor_Fault 4=Sensor_Blocked,
  # so openpilot's "!= 0" currently treats a faulted or blocked sensor as a permanent detection.
  struct SideDetect {
    dataAvailable @0 :Bool;

    sodDetect @1 :UInt8;        # SodDetct*_D_Stat -- the one openpilot uses
    sodStat @2 :UInt8;          # Sod*_D_Stat -- system enable state, not a detection
    sodAlert @3 :UInt8;         # SodAlrt*_D_Stat -- lamp state; Flash follows OUR turn signal
    sodSensor @4 :UInt8;        # SodSns*_D_Stat -- sensor health
    sodWarnPeriodMs @5 :UInt8;  # SodWarn*_Prd_Rq -- warning flash period, ms

    ctaStat @6 :UInt8;          # Cta*_D_Stat -- cross traffic, active in reverse
    ctaAlert @7 :UInt8;         # CtaAlrt*_D_Stat
    ctaAlert2 @8 :UInt8;        # CtaAlrt*2_D_Stat
    ctaSensor @9 :UInt8;        # CtaSns*_D_Stat
    ctaBrakeDecelReq @10 :Bool; # Cta*BrkDecel_B_Rq -- addressed to ABS_ESC
    ctaBrakeEnableReq @11 :Bool;# Cta*BrkEnbl_B_Rq -- addressed to ABS_ESC
    ctaBrakeMsgReq @12 :Bool;   # CtaBrk*MsgTxt_B_Rq

    bttStat @13 :UInt8;         # Btt*_D_Stat
    bttDriverReq @14 :UInt8;    # Btt*_D_RqDrv
    illumPercent @15 :UInt8;    # Side_Detect_*_Illum -- mirror lamp brightness
  }

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

struct CustomReserved17 @0xa30662f84033036c {
}

struct CustomReserved18 @0xc86a3d38d13eb3ef {
}

struct CustomReserved19 @0xa4f1eb3323f5f582 {
}
