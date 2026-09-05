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
  passingAssist @9 :PassingAssist;

  # BluePilot: ACC follow-gap requested by a longitudinal feature (Time_Gap_1..5), 0 for none.
  #
  # Defined on the ICBM branch rather than the passing-assist branch on purpose: ICBM is the base
  # the others rebase onto, and a capnp field number can only have one meaning.
  #
  # MOVED TO @10 ON REBASE, and deliberately the other way round from how it was proposed. The
  # tiebreaker is not which branch is the base -- it is which field has WIRE HISTORY.
  # `passingAssist @9` has been published on every drive since it was written and is in every route
  # log on the device; `accGapRequest` had never run. Renumbering passingAssist would make @9 in
  # those logs decode as a UInt8, so every recorded drive would read as garbage for the one feature
  # whose whole output is recorded drives. Renumbering this one cost nothing, because there was
  # nothing to be compatible with yet.
  accGapRequest @10 :UInt8;

  # FusionPilot: WHY the longitudinal plan was marked invalid, which is the question a commIssue
  # cannot answer today. Added 2026-08-28 after a Salt Lake City -> Yosemite drive raised
  # `commIssue` repeatedly on the curvy sections. That event is ET.SOFT_DISABLE, so it DISENGAGES:
  # it is not a banner. plannerd published at a clean 20.0 Hz throughout and marked its own outputs
  # INVALID -- 162 of 162 frames in one segment -- and nothing recorded which check tripped.
  #
  # `longitudinalPlan.valid` is `sm.all_checks(['carState','controlsState','selfdriveState',
  # 'radarState'])`, and all_checks is three separate tests -- alive, freq_ok, valid. Those fail for
  # completely different reasons: alive means a service stopped, freq_ok means it is arriving at the
  # wrong RATE (radarState's tolerance is 16-24 Hz, far tighter than the 100 Hz services' 40-120),
  # and valid means the publisher itself declared the data bad. One segment was fully explained by
  # the third -- radarState.valid was False on 738 of 1176 frames with 27 radar events beside it --
  # and two others had every service alive, valid and on-rate at publish time, so only plannerd's
  # OWN receive timing can say what happened. That cannot be reconstructed off-device.
  #
  # Bit order for all three masks: 0=carState 1=controlsState 2=selfdriveState 3=radarState.
  plannerChecks @11 :PlannerChecks;

  struct PlannerChecks {
    # Mirrors the service list in selfdrive/controls/lib/longitudinal_planner.py's publish().
    # test_planner_checks_mirror_the_plan asserts the two lists are identical, because a
    # diagnostic naming a different set of services than the rule it explains is worse than none.
    notAlive @0 :UInt8;
    freqBad @1 :UInt8;
    notValid @2 :UInt8;
    # The same boolean upstream assigns to longitudinalPlan.valid, carried here so a drive can be
    # scored without joining two messages.
    planValid @3 :Bool;
  }

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
  #      this was the failure mode that decided whether the idea was viable at all. Both the
  #      painted-line evidence (lineProb) and the drivable-width evidence (edgeGap) are recorded
  #      separately so they can be compared as discriminators rather than assumed equivalent.
  #      ANSWERED by the radar rather than by either of them -- see oncomingAnySide below. These two
  #      are still logged because the question of which geometry channel discriminates better is
  #      independently useful, and because the radar veto needs something to be audited against.
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
    # How long the slower vehicle has been CONTINUOUSLY confirmed -- not how long we have been
    # stuck behind it. The distinction is the whole design: waiting to be held up means waiting for
    # Ford's ACC to brake first, and the point is to have moved over before it does.
    confirmSeconds @2 :Float32;

    # lead evidence
    hasLead @3 :Bool;
    leadDRel @4 :Float32;
    leadVLead @5 :Float32;
    speedDeficit @6 :Float32;     # set speed - lead speed, m/s. The reason to want to pass.

    # geometry evidence, per side. lineProb is the model's confidence in a painted line BEYOND
    # ego's own lane line; edgeGap is meters of drivable width between ego's lane line and the
    # road edge. On a divided highway in the left lane, edgeGap collapses to the shoulder. On an
    # two-way road it does NOT -- the oncoming lane is drivable width -- which is exactly the
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
    # Seconds to reach the lead at the current closing rate. Nothing gates on it -- distance
    # replaced it -- and it is kept only so the drive data can answer whether TTC would have been
    # the better gate after all.
    leadTtc @28 :Float32;

    # DEPRECATED: an exact duplicate of confirmSeconds @2, from before that field was renamed.
    # Ordinals cannot be reclaimed without renumbering every field after it, so it stays and keeps
    # being written -- old logs would otherwise silently start reading zero. Read confirmSeconds.
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

    # BluePilot: the speed the DRIVER asked for, which the deficit is measured against, and where
    # it came from. Logged because getting this operand wrong is silent -- the subtraction looks
    # perfectly correct and simply never fires -- and it has now been wrong twice.
    #
    # With ICBM running, the number on the dash is NOT the intent: ICBM actively lowers
    # Veh_V_DsplyCcSet for curves, speed limits and the radar-blind lead, then restores it. The
    # intent is whichever of these is highest -- the driver's held baseline, the speed limit plus
    # offset that SLA is following, or the current dash value as a floor.
    referenceSpeed @33 :Float32;
    referenceSource @34 :ReferenceSource;

    # BluePilot: what is already sitting in the lane we would move into, from the FRONT radar's
    # off-path tracks. Unlike rearApproach this needs no new hardware: the Delphi MRR derives a
    # lateral position for every detection and card publishes all of them on liveTracks, where
    # radard discards everything that is not an in-path lead. So the measurement exists today and
    # is simply thrown away.
    #
    # This is the missing half of the passing decision. Geometry answers "is there a lane", and a
    # lane can be there and full. Without this the system suggests a pass into traffic no faster
    # than the car being passed, then wants to undo it -- which is what the settle timer papers
    # over rather than fixes.
    #
    # Note the two different frames in play: radar yRel is LEFT-POSITIVE while modelV2 lane
    # geometry is left-negative. The conversion happens in adjacent_lane.py; these fields are
    # already resolved to a named side.
    adjacentLeft @35 :AdjacentLane;
    adjacentRight @36 :AdjacentLane;

    # BluePilot: does this road carry traffic the other way?
    #
    # The question the whole design was built around and could not answer. modelV2 publishes lane
    # geometry, not direction of travel, so on a two-lane two-way road the oncoming lane passes
    # every geometry test as a passing lane. Map data cannot settle it either: mapd v1.12.0 ships
    # here and writes no oneway tag and no lane count.
    #
    # The radar answers it directly. An oncoming vehicle's absolute ground speed is roughly minus
    # its own -- about -27 m/s at 60 mph -- which nothing travelling our way and no roadside object
    # can produce. Held for a while after the last sighting rather than read per frame, because one
    # oncoming car is evidence about the ROAD, and the road does not become one-way again once it
    # has passed.
    # NAMED PER SIDE, because that is all this ever knew. It was "undividedRoad", which claimed
    # something about the whole road; the veto has always been per side, and on a four-lane
    # undivided road in the left lane the oncoming lane is one to the left while an ordinary
    # through lane is one to the right, and the right stays available.
    oncomingAnySide @37 :Bool;         # a sighting on EITHER side is still in memory
    oncomingSecondsLeft @38 :Float32;  # how much of that memory is left
    oncomingSeen @39 :Bool;         # ever this drive, whether or not the memory has expired

    # BluePilot: ACC had pressurised the brakes but was not yet slowing the car. A weaker claim
    # than accBrakingAtDecision and an EARLIER one -- beating the precharge means the suggestion
    # landed before Ford had even decided it would need to brake. Split out because counting it as
    # braking labeled preemptive suggestions as reactive, which inverted the metric.
    accPrechargeAtDecision @40 :Bool;

    struct AdjacentLane {
      available @0 :Bool;   # false = liveTracks is not reporting. NOT the same as "clear".
      occupied @1 :Bool;    # debounced: 3 consecutive radar messages, not one frame
      dRel @2 :Float32;     # meters ahead of the nearest vehicle in that lane, 0 when clear
      vRel @3 :Float32;     # m/s relative to ego, negative = slower than us
      vAbs @4 :Float32;     # its absolute speed -- the number the pass decision compares
      # Lateral offset in the RADAR's frame, LEFT-POSITIVE, carried so the UI can place the readout
      # over the actual vehicle instead of guessing a lane center. Note the flip at the draw site:
      # _map_to_screen takes the camera frame, where the sign is the other way round.
      yRel @5 :Float32;
      # Travelling the OTHER WAY on this side. Not debounced, unlike occupied: one sighting is
      # already proof of a two-way road, and waiting for a second costs a suggestion to pass into
      # a head-on lane.
      oncoming @6 :Bool;
      oncomingDRel @7 :Float32;
      oncomingVAbs @8 :Float32;   # negative: its ground speed is towards us
      # Where it is, so it can be DRAWN rather than only counted. The whole reason the oncoming
      # veto is hard to trust is that nothing shows what it saw -- a marker over a real car on the
      # far carriageway and a marker over empty tarmac are the same log line and completely
      # different bugs.
      oncomingYRel @11 :Float32;
      # WAS THE ROAD EDGE TRUSTED when this fired. The discriminator for "I was on I-15 for a
      # while, and kept saying two-way road", and without it that report cannot be closed from a
      # drive that has already happened. Two completely different bugs produce the same record:
      #
      #   trusted   -- the model placed our carriageway's edge BEYOND the opposing lanes, so real
      #                traffic across a median read as being on our road. A geometry problem.
      #   untrusted -- _on_our_carriageway fell back to the adjacent band, so whatever fired was
      #                within 5.5 m of us. On a divided highway that is not opposing traffic at
      #                all, it is close-range scenery reading as a closing vehicle.
      #
      # dRel and vAbs cannot separate those. The 2026-08-09 drive recorded 5.0 m and -9.2 m/s --
      # far too slow for highway opposing traffic, which reads -25 to -31 -- and that was as far
      # as the record could take it.
      oncomingEdgeTrusted @16 :Bool;
    # The same sighting once ONCOMING_FRAMES messages have agreed. `oncoming` is the first return;
    # this is the one the veto would act on. Drawn from this rather than from `oncoming`, because a
    # marker that appears on a single unbelieved return shows the driver noise the decision layer is
    # correctly ignoring -- 372 of them on one divided-highway drive against 0.1 s of actual veto.
    oncomingCorroborated @17 :Bool;
      # The two facts that decide whether this side is refused, logged because they are what a
      # disputed decision comes down to and neither is visible from the road.
      #
      # oncomingAdjacent means opposing traffic was seen in the lane RIGHT NEXT to us -- that lane
      # is theirs and no setting overrides it. Without it, an oncoming sighting further out only
      # says the ROAD is two-way; whether the next lane over is a turn lane or a travel lane is
      # then decided by sameDirectionRecent, because a center turn lane and an ordinary passing
      # lane are geometrically identical (a lane at 3.7 m, opposing traffic at 7.9 m, our own road
      # edge beyond both) and nothing in the sensors separates them.
      oncomingAdjacent @9 :Bool;
      sameDirectionRecent @10 :Bool;
      # How much of the oncoming memory is left ON THIS SIDE. The veto is per-side and
      # always was, so deciding which side to name needs a per-side number -- the UI read
      # one here before it existed, and crashed the panel for a whole drive on 2026-08-08.
      # oncomingSecondsLeft on the parent is NOT this: it is the max across both sides.
      oncomingSeconds @15 :Float32;

      # BluePilot: WHO HAS OVERTAKEN US IN THIS LANE, AND HOW LONG AGO.
      #
      # This is a forward-looking radar answering a question about what is BEHIND the car, and it
      # works because of the one thing an overtake does: a vehicle that passes us was behind us a
      # moment ago and is in front of us now. It enters our sensor's view at short range in the
      # adjacent lane, pulling away. That transition is visible, and it is the only evidence about
      # rear traffic this car can produce without a rear sensor.
      #
      # It exists because the obvious rule is backwards for how this car is meant to drive. Ford's
      # own BlueCruise "avoids merging with traffic moving much faster than your vehicle", which is
      # sound for a system aiming at a smooth average drive and useless for the case a passing aid
      # is FOR: stuck behind a truck at 45 in a 70, the left lane running 25 mph faster is exactly
      # the lane you want and exactly the one that rule refuses.
      #
      # The speed of that lane was never the question. The question is whether something is closing
      # on us in it, and a rate answers that where a speed cannot:
      #
      #   overtakes coming thick and fast   the lane is busy behind us; assume the next one is
      #                                     already there, because it usually is
      #   nothing for a long time           the lane is genuinely empty behind, and this is the
      #                                     evidence for GOING rather than a reason to hold off
      #
      # That second line is the point. Every gate in this file can only ever say no; this is the
      # first thing here that can say yes on its own evidence, which is what "more aggressive, using
      # the sensors" has to mean if it is to mean anything safe.
      #
      # PHASE 1: MEASURED AND PUBLISHED, GATING NOTHING. Whether the detection is any good, and what
      # a quiet lane actually looks like in seconds, are road questions. Wiring a gate to a number
      # nobody has seen is how the invented constants got here in the first place.
      overtakenSeconds @12 :Float32;   # since a vehicle last passed us in this lane; 0 = never seen
      overtakenCount @13 :UInt16;      # this drive, this side
      overtakenVAbs @14 :Float32;      # ground speed of the last one, for judging the threshold
    }

    enum ReferenceSource {
      cluster @0;      # the dash value; also the fallback
      icbmHold @1;     # driver took the set speed back, ICBM is holding their number
      speedLimit @2;   # SLA is following the limit plus offset
    }

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
      dRel @3 :Float32;        # meters behind, 0 when unknown
      vRel @4 :Float32;        # m/s, positive = closing
      ttc @5 :Float32;         # seconds until it reaches us, large when not closing
      source @6 :Source;
    }

    enum Source {
      none @0;      # no rear sensing fitted
      blis @1;      # derived from Side_Detect_L/R_Stat, categories only, ttc unset
      radar @2;     # a rear-facing radar object list
    }

    # BluePilot: LiveMapDataSP.roadName at decision time.
    #
    # ORIGINAL PURPOSE SOLVED, kept for a smaller one. This was the cheapest candidate for the
    # divided-highway gate that geometry cannot provide, back when nothing else could tell an
    # oncoming lane from a passing lane. The front radar answers that directly now -- see
    # adjacent_lane.py -- so this is no longer load-bearing and no filter is written against it.
    #
    # It stays because it is the only human-readable label on a decision: when a drive log shows
    # the oncoming veto firing where it should not have, this says which road that was. Cheap and
    # unambiguous in a way coordinates are not.
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
      nothingSlower @6;     # no vehicle ahead slow enough to be worth passing, or not yet confirmed
      noLaneAvailable @7;   # geometry says there is nowhere to go on either side
      blindspotOccupied @8; # geometry was fine, BLIS was not
      overtakeRestricted @9; # TSR reports a no-overtaking zone in force
      rearApproaching @10;   # something is closing on that lane from behind
      suspended @11;         # driver paused it -- construction zone, weather, unfamiliar road
      adjacentSlow @12;      # the lane is there and clear behind, but full of traffic no faster
      oncomingLane @13;      # two-way road: the lane to the left is theirs, not a passing lane
      closingIn @14;         # deliberately holding off: still closing, ACC has not had to brake
      leadBraking @15;       # they are braking hard: wait and see before committing to go round
      driverChangedLanes @16; # standing down after the driver's own lane change -- see below
      inCurve @17;
      # ON A RAMP. mapd v2's highwayClass says motorwayLink, which is OSM's own word for an on- or
      # off-ramp rather than anything inferred. Nothing on the car can tell a ramp from a road: the
      # camera sees lane lines and a drivable surface either way, which is why every exit test in
      # passing_assist is REACTIVE and fires only after he has already moved.
      onRamp @18;
      # The map says the road has no same-direction lane to the left -- a single-lane carriageway, or
      # a two-way road with one lane each way, where the lane to the left IS the oncoming lane. A
      # claim about the ROAD, needing no idea which lane we are in, which is the only map claim of
      # that shape that survived measurement.
      noRoomInMap @19;
      # A speed limit DROP is close enough that a pass started now would be finished while braking
      # for it. The map is the only thing that can see a limit change before the sign does.
      limitDropAhead @20;           # the road is bending hard enough that a pass is the wrong place for it
    }

    # BluePilot: the maneuver this WOULD perform, run as a dry run. Nothing actuates; see
    # passing_maneuver.py. The detector above answers "would I suggest a pass this frame", which
    # is a single frame's verdict and NOT what decides whether an automatic system works -- that
    # depends on whether the verdict holds still long enough to act on. These fields make the
    # sequence visible over time, and `maneuverAborts` is the number the whole thing is for.
    maneuver @41 :Maneuver;
    maneuverSeconds @42 :Float32;   # time in the current phase
    maneuverSide @43 :Side;         # the side the sequence committed to
    blinkerWouldBeOn @44 :Bool;      # on through the crossing, out when it completes
    # NARROWER THAN blinkerWouldBeOn, and the difference is a safety property rather than a detail.
    # The lamp comes on at `signaling`, which since the signal-first change begins BEFORE the gates
    # pass. A DESIRE must not, because desire_helper does not consult our gates -- it advances on its
    # own nudgeless timer and a blind-spot check alone. Raising the desire at `signaling` would start
    # the crossing on a 1 s timer with oncoming, adjacent-slow, rear-approach and geometry all still
    # refusing. See PassingManeuver.desire_ok.
    desireOk @98 :Bool;
    # IS THIS DRIVING THE CAR, or narrating what it would do? The one bit the car side cannot work
    # out for itself, and the only thing that was missing to make the signal real.
    #
    # blinkerWouldBeOn and maneuverSide are published in BOTH modes -- they are the dry run's whole
    # output -- so a consumer that acted on them alone would command the signal on every drive since
    # the feature was written. This says which it is. See PassingAssistDetector.may_actuate: false
    # whenever the rear sensor on the side being moved into is unavailable, which today is always.
    actuating @96 :Bool;
    steeringWouldBeActive @45 :Bool;

    # Sequences that reached `signaling` and then backed out -- a blinker shown to traffic behind
    # for a maneuver that did not happen. Near zero on a drive means the gates are stable enough
    # to act on. Anything else names an unstable gate that no amount of reading the code would
    # have found.
    maneuverAborts @46 :UInt16;

    # BluePilot: was the lead this decision rests on RADAR-confirmed, or the camera model alone?
    #
    # radard picks leads camera-first: modelV2 proposes, and a matching radar track refines it. With
    # no matching track and a confident model, get_RadarState_from_vision returns a lead with
    # radar=False whose vLead is the MODEL's velocity estimate rather than Doppler. That is a much
    # weaker basis for "this car is 4 mph slower than I asked for", which is the entire judgment
    # here.
    #
    # NOT gated on -- deliberately. Requiring radar would throw away the earlier detection that is
    # the whole point of deciding before Ford's ACC brakes, and whether vision-only leads actually
    # produce bad passes is a question for drive data, not argument. Logged so it can be answered.
    leadRadarConfirmed @47 :Bool;
    leadModelProb @48 :Float32;

    # BluePilot: HOW FAR BACK does Ford's ACC actually start shedding speed for this lead?
    #
    # The whole design is built on deciding before it does, and the margin was estimated rather
    # than known -- Ford's gap setting is a time headway and ACC eases off well before reaching it,
    # so "about 70 m" was a guess with a wide error bar. This measures it: the distance to the lead
    # the first time ACC requested deceleration during this approach, held until the approach ends.
    #
    # It settles the look-ahead question directly. If this comes back at 150 m on real roads, a
    # 220 m look-ahead has 70 m of margin and is right. If it comes back at 250 m, the look-ahead
    # is too short and every pass starts behind an ACC that has already braked -- which is the one
    # outcome this feature exists to avoid. Zero means ACC never asked during the approach, which
    # is the good case and the one to hope for.
    accBrakingOnsetDRel @49 :Float32;

    # BluePilot: is this pass GRINDING? See overtake_progress.py.
    #
    # The one circumstance in which passing assist may ever touch the set speed -- crawling
    # alongside a car you are barely faster than. Measured before it is acted on, because how often
    # it happens and how long it lasts are what should set the trigger and the size of the nudge,
    # and neither is guessable.
    #
    # crawlAfterSuggestion labels provenance rather than gating anything: a forward radar cannot
    # honestly tell whether we MEANT to overtake, so the condition is measured and the association
    # recorded separately. Crawls clustering after suggestions would be a finding; crawls that do
    # not would be a more interesting one.
    crawlSeconds @50 :Float32;
    crawlLongestSeconds @51 :Float32;   # worst this drive
    crawlEvents @52 :UInt16;            # crawls that passed the threshold
    crawlSide @53 :Side;
    crawlAfterSuggestion @54 :Bool;

    # BluePilot: the lead's own deceleration, and whether it is enough to hold a pass off.
    leadAccel @55 :Float32;
    leadBrakingHold @56 :Bool;

    # The FURTHEST BACK Ford's ACC has started braking this drive -- the earliest it ever lost
    # patience. accBrakingOnsetDRel above is per-approach and resets; this is the drive's answer.
    #
    # The max rather than the average on purpose. It is the number the close-in hold has to stay
    # clear of, and a hold set to the average would sail past ACC on every worse-than-average
    # approach. Sizing a safety margin off a typical case is how it stops being a margin.
    accBrakingOnsetMax @57 :Float32;

    # BluePilot: WHEN A PASS WAS WANTED, WHAT STOPPED IT -- over the whole drive.
    #
    # blockedBy says what is stopping it right now, which is the wrong question for deciding what
    # to build next. This says where the time actually went: seconds counted per reason, but only
    # while a slower car was spotted, so an empty road contributes nothing. "62% blind spot" and
    # "62% two-way road" point at completely different work, and neither is visible from watching
    # the panel, where every reason looks equally common because each one is only ever on screen
    # for a moment.
    wantedSeconds @58 :Float32;
    # LEFT LANE HOGS. Seconds spent behind someone camped in the leftmost lane below
    # the set speed with a lane free to their right, and how many distinct ones. See
    # _track_lane_hog for why the lane-to-the-right term is what makes it a hog.
    hogSeconds @94 :Float32;
    hogCount @95 :UInt16;      # total time a pass was warranted
    topBlockedBy @59 :Blocked;       # the reason that consumed the most of it
    topBlockedShare @60 :Float32;    # its share, 0..1
    clearShare @61 :Float32;         # share where nothing was stopping it at all

    # BluePilot: WHICH maneuver the fields above describe. Passing and keep-right are mutually
    # exclusive by construction -- keep-right is only ever evaluated on the frames where no pass is
    # warranted -- so they share one set of state fields and are told apart by this.
    #
    # Their ABORT COUNTS stay separate, though, because that number is the readiness metric for
    # each. Lumping them together would say a gate somewhere is unstable without saying which
    # maneuver it belongs to, which is most of the value.
    maneuverReason @62 :Reason;
    keepRightAborts @63 :UInt16;

    # BluePilot: crossings REVERSED because something was arriving behind, as opposed to sequences
    # that merely backed out before moving. Counted apart from maneuverAborts because they are not
    # the same event at all: one is the system changing its mind, the other is the system avoiding
    # a collision. Averaging them would hide the second inside the first.
    emergencyAborts @64 :UInt16;

    # BluePilot: seconds left of the stand-down after the driver changed lanes themselves.
    driverChangeStandDown @65 :Float32;
    driverChangeWasExit @66 :Bool;   # ...and whether the lane they moved into looked like an exit

    # BluePilot: the close-in distance actually in force, after Auto has resolved. 0 = no hold.
    minApproachActive @67 :Float32;

    # Seconds left of the stand-down after a crossing was reversed. Published because without it
    # the panel contradicts itself: the detector still says a pass is warranted and clear, the dry
    # run is refusing to start one, and with nothing to say otherwise the screen shows a green
    # PASS LEFT seconds after the car backed out of exactly that.
    maneuverStandDown @68 :Float32;

    # BluePilot: DID IT AGREE WITH THE DRIVER? The most useful thing measurable before any sensor
    # is fitted, and the closest thing to a readiness score this phase can produce.
    #
    # Every gate here is checkable in isolation and none of that answers the only question that
    # matters: when a real driver decided to pass a real car on a real road, had this system
    # decided the same thing, and how long before? A system that agrees on nine passes out of ten
    # and names a specific gate for the tenth is ready to be trusted with a blinker. One that
    # agrees on half is not, whatever its unit tests say.
    #
    # Counted on the driver signaling LEFT with a confirmed slow lead ahead -- unambiguous, unlike
    # a right-hand signal which could be an exit, a keep-right or a pass.
    driverPasses @69 :UInt16;
    driverPassesAgreed @70 :UInt16;      # ...where it had already suggested that same side
    # Of those, the ones the feature was ELIGIBLE to have an opinion about. See OFF_BY_DESIGN in
    # passing_assist.py: a pass made below the minimum speed, or with the feature off, counted
    # against it in the agreement score -- which is the number deciding whether this is ever worth
    # letting steer, and it read 2 of 106 while a large share of the denominator was drives it was
    # switched off for.
    driverPassesEligible @97 :UInt16;
    driverPassLeadSeconds @71 :Float32;  # how long it had been suggesting before the driver acted
    driverPassMissReason @72 :Blocked;   # the gate that most often stopped it when it disagreed

    # ...and the SAME QUESTION FROM THE OTHER SIDE. driverPasses counts the passes the driver made
    # and asks whether this system found them -- recall. It says nothing about passes this system
    # offered that no sane driver would take, which is the error that matters once it is allowed
    # to act on its own, and which is invisible in the numbers above.
    #
    # An unacted suggestion is not automatically wrong: the lead speeds up, a lane closes, the
    # driver simply is not in a hurry. So what is recorded is how LONG one stood unacted, because
    # a suggestion held for half a minute while the driver sits there is a different claim from one
    # that lapsed after three seconds.
    suggestionsMade @73 :UInt16;
    suggestionsTaken @74 :UInt16;
    longestIgnoredSeconds @75 :Float32;

    # ...ACCUMULATED ACROSS DRIVES, which is the only scale at which any of it decides anything.
    # Seven passes says nothing -- one unusual road, one odd bit of traffic, and the ratio swings
    # by a third. Eighty says whether this is ready to be trusted with a blinker, and that is the
    # question the whole phase exists to answer. Carried in the same param as the drive summary.
    lifetimeDrives @76 :UInt16;
    lifetimePasses @77 :UInt16;
    lifetimeAgreed @78 :UInt16;

    # BluePilot: HOW WRONG the deficit threshold was, when it was wrong.
    #
    # "missed on nothing slower ahead" says the threshold rejected a car the driver went round. It
    # does not say by how much, and that is the whole calibration question -- 4 mph is an invented
    # number and the only thing that can settle it is what he actually passes. A car he passed that
    # this called 3.6 mph slower argues for lowering it; one at 0.5 argues that he simply wanted
    # past, and no threshold would have agreed.
    #
    # The MEAN of those, so a single unusual pass cannot move it, and in mph because that is the
    # unit the setting is in -- a figure the driver could act on directly rather than convert.
    missedDeficitMph @79 :Float32;

    # BluePilot: when oncoming refuses a pass, is it SEEING traffic or REMEMBERING it?
    #
    # The veto holds for 90 s after a sighting, which is the right shape for a two-lane road and
    # the reason one bad detection can silence the feature for a mile and a half. Splitting the
    # time tells the two apart, and they need opposite fixes: mostly-seen means the detection is
    # doing its job and the road really is two-way, mostly-remembered means one sighting is
    # carrying the whole refusal and the memory is too long -- or, on I-15, that a phantom is.
    #
    # This is the measurement that turns the reported I-15 fault into a diagnosis rather than three
    # mitigations and a hope.
    # BluePilot: the model's own uncertainty about where the road edge is, which is the third and
    # least visible of the three terms that decide whether a lane exists beside us. Published
    # because "No lane to move into" was being reported on roads with an obvious empty lane, and
    # nothing on screen could say which term refused it. See MAX_ROAD_EDGE_STD -- an invented 0.5.
    leftEdgeStd @82 :Float32;
    rightEdgeStd @83 :Float32;

    # ...and the two that actually separate a lane from a shoulder. edgeGap above measures ego's
    # lane line out to the road edge, which on an interior lane is the next lane PLUS its shoulder
    # and on the outermost lane is the shoulder alone -- one number for two different things, which
    # is why no threshold on it could ever work. laneWidth is the candidate lane by itself, and
    # edgeBeyond is how much road is left past its far line. When the model has no lane out there it
    # puts that far line on the road edge itself, so edgeBeyond collapses to zero and says so.
    leftLaneWidth @84 :Float32;
    rightLaneWidth @85 :Float32;
    leftEdgeBeyond @86 :Float32;
    rightEdgeBeyond @87 :Float32;

    # ...and whether that stand-down follows a run that COMPLETED rather than one that backed
    # out. Same clock, opposite news.
    maneuverStandDownComplete @88 :Bool;

    # The deficit threshold the drive actually ran with, mph. Published so the summary can compare
    # it against missedDeficitMph rather than the panel keeping its own copy of a setting -- that
    # copy is how a readout ends up explaining a gate using a number the gate stopped using.
    minDeficitActive @89 :Float32;

    # WHICH geometry term refuses the left side, over the whole drive, and by how much. The live
    # per-side reason is on the panel already; this is the version that survives to a stop, because
    # "and you expect me to read all of that while driving?" is a fair question and the answer is no.
    # 0 edge-std, 1 paint, 2 width, 3 room-beyond -- the gate's own order.
    geoRefusedBy @90 :UInt8;
    geoRefusedValue @91 :Float32;
    geoRefusedShare @92 :Float32;

    # ...and where that term would have to sit to admit four fifths of the refusals. The number to
    # SET, which a mean cannot give: a mean of 0.31 could be tightly clustered, or half at 0.45 and
    # half at 0.17, and only one of those is fixed by 0.30.
    geoLoosenTo @93 :Float32;
    oncomingSeenSeconds @80 :Float32;        # refused while actually watching a vehicle
    oncomingRememberedSeconds @81 :Float32;  # refused on memory alone, nothing in view

    # How much the minimum speed gain is being multiplied by right now, from how far over the
    # posted limit the driver has asked to go. 1.0 is "his setting, unmodified".
    #
    # SEPARATE FROM minDeficitActive ON PURPOSE. That field feeds the summary's "try N mph"
    # recommendation, which names the SETTING to change -- scaling it would make the panel
    # recommend a number derived from a momentary road condition, which is the exact fault its own
    # comment warns about one field up.
    patienceScale @99 :Float32;
    # ...and what it cost: passes HE went and made himself, out of leads that were slow enough by
    # his own setting and were refused only because of this. The number to look at before deciding
    # whether to keep the feature -- a pass he made is a verdict, where seconds are only exposure.
    #
    # The seconds are kept too, in the drive record rather than here, because nothing on screen
    # would draw them and a published field nobody renders reads as a real measurement forever.
    patienceMissed @100 :UInt16;

    enum Maneuver {
      idle @0;         # nothing warranted
      confirming @1;   # a slower vehicle is being confirmed, timer running
      waiting @2;      # confirmed, and a gate is what is stopping us -- see blockedBy
      signaling @3;   # blinker would be on, holding before any movement
      changing @4;     # crossing. COMMITTED: gates can no longer call it off, only the driver can
      finishing @5;    # across, blinker out
      aborting @6;     # backing out of a crossing already begun -- something arrived behind us
    }
    # WHERE THE SYSTEM THINKS WE ARE ACROSS THE ROAD -- the lane strip on the panel.
    #
    # Added 2026-08-19 because this fork has now computed a value correctly and never rendered it
    # FOUR times, and the anchor was the fourth: lane index, lanes-to-our-left and the lane-line
    # witness all gate the slow-pass warning and none of them reached a screen. A gate you cannot
    # see is a gate he can only debug by reporting that it behaved oddly.
    #
    # NOT a redraw of the road. openpilot already draws lane lines and the path, and anything that
    # merely repeats the camera is redundant with what he can see out of the window. These three
    # carry what the camera CANNOT show: the map's lane count, our derived position within it, and
    # the fact that the two disagree or are unknown.
    #
    # laneIndex is -1 for UNKNOWN, never 0. 0 is a real answer meaning the rightmost lane, and the
    # panel must be able to draw "no idea" differently from "far right" -- conflating them is how
    # an unavailable estimator reads as a confident one.
    laneIndex @101 :Int8;
    lanesTotal @102 :Int8;
    # The lane-line witness ALONE: no line beyond our left boundary. Weaker evidence than a numeric
    # index, and drawn differently for that reason -- an outline rather than a fill. Kept separate
    # from laneIndex rather than folded in, because it is a claim about the immediate neighbour and
    # not a position, and a boolean promoted to a position is a fake measurement.
    noLaneLeft @103 :Bool;

    # THE FOUR-LINE BOUND, 2026-08-19. The outer left AND right lane lines together, which narrow
    # the lane even where the right road edge is out of reach -- the middle lanes, where he watched
    # every box on the strip go empty at once.
    #
    # An inclusive range of candidate lane indices, or -1/-1 for none. On a three-lane road lo ==
    # hi and the lane is pinned exactly, so it arrives as a real laneIndex instead and this merely
    # agrees with it. On four or more it is a genuine range and this is the ONLY thing that has
    # anything to say -- which is why it is on the wire rather than derived at the panel.
    laneBoundLo @104 :Int8;
    laneBoundHi @105 :Int8;

    # AGREEMENT AFTER WAITING FOR THE LANE TO CLEAR. His report: "A lot of lane changes are
    # correct, I just have to wait for no one to be in that lane." Strict agreement is sampled at
    # the instant of his stalk, so that sequence scored as a MISS -- 82 passes read as 3
    # agreements, which measured simultaneity rather than whether the decision was right.
    #
    # ON THE WIRE because the readout is the whole point. It was computed and written only to the
    # history JSON for a day, which is this fork's oldest recurring fault -- a value derived
    # correctly and never rendered, now on its fifth instance. The panel shows it beside the
    # strict count; the delay says how long he waited, and that distribution is what a future
    # AGREE_WINDOW_S should be read off rather than guessed at twice.
    driverPassesAgreedLate @106 :UInt16;
    driverPassLateDelay @107 :Float32;

    # THE LEFT ROAD EDGE CLOSING IN AHEAD, metres, over the same near/far span the RIGHT side uses
    # to spot an off-ramp. Positive means narrowing. The right-side test throws this sign away --
    # `max(0.0, far - near)` -- because there a narrowing road is a lane ending the availability
    # test already handles; on the left it is the coned-work-zone signature, which is the one
    # hazard recorded here that no signal in the system carries.
    #
    # MEASURED BEFORE IT GATES ANYTHING, 8 drives, 86k moving frames. On motorway the frames the
    # gate already opens and the frames a looser edge term would admit have the SAME distribution
    # (p99 0.51 vs 0.71, max 1.44 vs 1.38); on primary/secondary/tertiary the would-admit set has
    # a long tail the open set does not (secondary p90 5.99 against a max of 0.83). So it is
    # published and watched first, and gates nothing yet.
    leftNarrowingM @108 :Float32;

    # FusionPilot: the side the blinker is lit for, which is a DIFFERENT question from `suggestion`
    # and is the one passing_maneuver actually reads. `wanted` means "a slow car is spotted and a
    # lane exists that side"; `suggestion` additionally means every safety gate passed and the
    # confirmation completed. The maneuver leaves `signaling` on `wanted == none or wanted !=
    # self.side`, so this field is what a back-out comes down to.
    #
    # PUBLISHED 2026-08-26 BECAUSE FOUR BACK-OUTS COULD NOT BE EXPLAINED WITHOUT IT. blockedBy reads
    # `none` while signaling -- that is what "a suggestion is being made" looks like -- so on two of
    # the four the only recorded reason was the one value that cannot name a cause. Reading
    # `suggestion` instead ruled out a side flip and left nothing. The deciding term had never been
    # on the wire, which is the third time in this fork a decision was logged without its input.
    #
    # It is DEBOUNCED (WANTED_RISE_S / WANTED_FALL_S), and that debounce was itself bought by 126
    # aborts in 37 minutes -- so a back-out attributable here means the hold is still not long
    # enough, and it is measurable now instead of inferred.
    wantedSide @109 :Side;

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
    # BluePilot: passing assist has decided a pass is on. A SOUND, not a message.
    #
    # The whole readout for this feature is three lines of text on the panel, which is fine for
    # reading numbers at a light and useless for the moment that actually matters -- the system
    # deciding to go. Ford gives a visual AND audible prompt before a BlueCruise lane change for
    # the same reason: nobody is watching the screen at the moment a car moves.
    #
    # Earns its place in phase 1 too, before anything actuates. It turns every suggestion into
    # something noticed at the time rather than a counter read afterwards, which is exactly what
    # the agreement measurement needs -- a chime, then whether you would have gone anyway.
    passingAssistSuggested @27;
    passingAssistBackedOut @28;
    # FusionPilot: the stock-ACC passthrough has gone INERT -- the camera has asked to cancel for
    # five straight seconds, so Ford's command can no longer be carried and openpilot longitudinal
    # is driving from here. On route 0000038d it did this from t+30.8 for the whole drive with
    # nothing but a pill saying so, and he had to work it out from the seat: "it's just annoying
    # that it bricks it for the whole drive". Announced ONCE, because it does not recover within a
    # drive and a repeating alert for a permanent condition is noise.
    #
    # @29, NOT @27 where it was authored. Both branches added an event at @27 and the tiebreaker is
    # WIRE HISTORY, not base branch: passingAssistSuggested has already been written to route logs
    # -- 43 suggestions on 0000038e alone -- and capnp reads enums by VALUE, so renumbering it makes
    # every recorded drive decode 27 as the wrong event. This field has never run anywhere, so it
    # is the one that moves. See CLAUDE.md, "Capnp field numbers across branches".
    accPassthroughInert @29;
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
  blisLeft @4 :SideDetect;
  blisRight @5 :SideDetect;
  blinkerTest @6 :BlinkerTest;

  # BluePilot: the LKA button on the end of the turn-signal stalk. It does nothing on this car --
  # the wheel's LCA button is what toggles lateral -- so it is a free physical input, and a stalk
  # press beats reaching for the screen for anything wanted while driving.
  #
  # LaSwtchPos_D_Stat in Steering_Data_FD1 (0x083, bus 0): 0 Open / 1 Pressed / 2 Unused / 3 Fault.
  # openpilot already parses that message and passes the signal through untouched in
  # create_button_msg, so reading it costs nothing -- no new message, parser entry or DBC work.
  lkaButtonPressed @7 :Bool;

  # BluePilot: the standstill walk that reads the cluster's lane-display vocabulary off the car.
  #
  # LaActvStats_D_Dsply has five states per side and passing assist's hint is built from them, but
  # only Available and Intervene have ever been transmitted by code that has run on this car. None,
  # Suppress and Warning are unmeasured. This walks all five on the left line against a known-green
  # right line so each can be named. See lane_display_test_ext.
  laneDisplayTest @8 :LaneDisplayTest;

  # BluePilot: bench test for whether openpilot can operate the turn signal on this car.
  #
  # Standing question, because desire_helper's whole lane-change state machine keys off
  # carState.leftBlinker/rightBlinker, and those come from the SCCM's own Steering_Data_FD1 on
  # bus 0. openpilot already transmits that message and passes TurnLghtSwtch_D_Stat through
  # untouched; panda explicitly permits it (ford.h: "blinkers, wiper switches, high beam ...
  # which we passthru in OP"). ICBM proves the car acts on openpilot's injected copy of this exact
  # frame for cruise buttons. Whether the BCM honors it for the turn signal is untested.
  #
  # lampLeft/lampRight are the ANSWER, read from BodyInfo_3_FD1 (0x3B3) -- the body module's own
  # report of what the lamps are doing, on a bus openpilot already parses. That makes this a
  # closed-loop measurement rather than a guess, and needs no extra hardware.
  #
  # Note the lamps FLASH, so lampLeft/lampRight toggle at the flash rate while commanded; a
  # consumer wants to latch over a flash period rather than read a single frame.
  struct LaneDisplayTest {
    # 0 idle; 1..N the step being shown, indexing lane_display_test_ext.LANE_TEST_STEPS.
    step @0 :UInt8;
    secondsRemaining @1 :Float32;
    # 0 none, 1 refused because the car was moving.
    blockedReason @2 :UInt8;
  }

  struct BlinkerTest {
    state @0 :State;
    commanded @1 :UInt8;      # what we put in TurnLghtSwtch_D_Stat: 0 none, 1 left, 2 right
    secondsRemaining @2 :Float32;
    lampLeft @3 :Bool;        # TurnLghtLeftOn_B_Stat, BodyInfo_3_FD1
    lampRight @4 :Bool;       # TurnLghtRightOn_B_Stat
    lampSeen @5 :Bool;        # a commanded lamp was observed lit at least once this pulse
    blockedReason @6 :Blocked;
    # BluePilot: HOW MANY TIMES the lamp lit, not merely whether it did.
    #
    # Two runs of this test produced two recollections -- "it worked" and "it flashes really fast"
    # -- and settled nothing, because "really fast" is not a measurement. A clean 1.5 Hz signal over
    # a four second hold is about six; the erratic case is many times that. One number, comparable
    # between runs, is what turns this from an argument into a result.
    flashes @7 :UInt8;
    # ...of which, after we STOPPED commanding. This is the tap measurement: flashes that happen
    # once we have gone quiet are the body module running its own one-touch pattern, which is the
    # thing worth knowing -- it means the car owns the rate and the count, not us.
    flashesAfter @8 :UInt8;
    # BluePilot: the mean interval between the DRIVER's own flashes, measured off the body module's
    # lamp report while commanding nothing. This is the number to set the blink period to -- his
    # car's actual rate rather than the FMVSS band, which is a factor of two wide.
    measuredPeriodMs @9 :UInt16;

    # BluePilot: blinks COMMANDED, and how many were asked for. A run cut short reports a low
    # flash count and nothing else, which is indistinguishable from a car that ignored us -- and
    # the most common reason it gets cut short is the car creeping at a light, which is the gate
    # working correctly. "It only did two flashes" was that, six times in one drive.
    blinksSent @10 :UInt8;
    blinksWanted @11 :UInt8;

    enum State {
      idle @0;
      pulsing @1;
      done @2;
    }

    # Why a requested pulse did not run. Standstill is not negotiable: this operates a lamp other
    # drivers read, and a stationary car signals nothing about a maneuver.
    enum Blocked {
      none @0;
      notStationary @1;
      cruiseEngaged @2;      # never fight a live lane-change decision
      driverSignalling @3;   # the driver's own stalk wins, always
      resultStillShowing @4; # pressed again while the last verdict was still up; press once more
      lampStillFlashing @5;  # the car is still running its own flash pattern from the last test
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
  #
  # MOVED TO @9 ON REBASE. It landed on @4, which `blisLeft` has held since carStateBP existed --
  # git merged the two additions cleanly because they are in different parts of the file, and capnp
  # then ABORTS THE PROCESS rather than raising, so the whole suite died at import with a stack that
  # named pytest rather than the schema. Same tiebreaker as accGapRequest: the field with wire
  # history keeps its number, and blisLeft is in every route log on the device.
  accGap @9 :UInt8;

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

# FusionPilot: the rear radar digest, as the feeder microcontroller sends it.
#
# NOT raw detections. The MRR emits 64 detection messages at 33 Hz -- ~2100 frames/s, measured on
# the bench 2026-08-14 -- and bus 1 is already 60-73% loaded, so the feeder reduces them to the
# nearest CLOSING target per side before anything reaches this message. See bp_rear_radar.dbc.
#
# Deliberately its own message rather than a field on carStateBP: that one is declared at 100 Hz
# and already over-published, and hanging 20 Hz sensor data off it would republish it five times
# over on every BluePilot Ford, most of which have no rear radar at all.
struct RearRadarBP @0xbd443b539493bc68 {
  # The feeder is talking AND the radar behind it is alive. Both, because a feeder that keeps
  # sending after its radar dies would otherwise report an empty road forever.
  dataAvailable @0 :Bool;
  radarAlive @1 :Bool;
  # Rate of MRR_Detection frames the feeder sees. A silent digest cannot otherwise be told from an
  # empty road, and those must never read the same to a feature whose job is refusing when blind.
  detectionHz @2 :UInt8;
  validDetections @3 :UInt8;
  left @4 :Target;
  right @5 :Target;

  struct Target {
    detected @0 :Bool;
    dRel @1 :Float32;      # m behind us, positive rearward
    yRel @2 :Float32;      # m lateral, left positive
    vRel @3 :Float32;      # m/s, POSITIVE = closing on us -- matches RearApproachSide.from_radar
    targetCount @4 :UInt8; # closing targets this side had before reduction
  }
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
