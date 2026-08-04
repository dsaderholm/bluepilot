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
    # ego's own lane line; edgeGap is metres of drivable width between ego's lane line and the
    # road edge. On a divided highway in the left lane, edgeGap collapses to the shoulder. On an
    # oncoming_any_side road it does NOT -- the oncoming lane is drivable width -- which is exactly the
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
    # geometry, not direction of travel, so on a two-lane oncoming_any_side road the oncoming lane passes
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
    # braking labelled preemptive suggestions as reactive, which inverted the metric.
    accPrechargeAtDecision @40 :Bool;

    struct AdjacentLane {
      available @0 :Bool;   # false = liveTracks is not reporting. NOT the same as "clear".
      occupied @1 :Bool;    # debounced: 3 consecutive radar messages, not one frame
      dRel @2 :Float32;     # metres ahead of the nearest vehicle in that lane, 0 when clear
      vRel @3 :Float32;     # m/s relative to ego, negative = slower than us
      vAbs @4 :Float32;     # its absolute speed -- the number the pass decision compares
      # Lateral offset in the RADAR's frame, LEFT-POSITIVE, carried so the UI can place the readout
      # over the actual vehicle instead of guessing a lane centre. Note the flip at the draw site:
      # _map_to_screen takes the camera frame, where the sign is the other way round.
      yRel @5 :Float32;
      # Travelling the OTHER WAY on this side. Not debounced, unlike occupied: one sighting is
      # already proof of a two-way road, and waiting for a second costs a suggestion to pass into
      # a head-on lane.
      oncoming @6 :Bool;
      oncomingDRel @7 :Float32;
      oncomingVAbs @8 :Float32;   # negative: its ground speed is towards us
      # The two facts that decide whether this side is refused, logged because they are what a
      # disputed decision comes down to and neither is visible from the road.
      #
      # oncomingAdjacent means opposing traffic was seen in the lane RIGHT NEXT to us -- that lane
      # is theirs and no setting overrides it. Without it, an oncoming sighting further out only
      # says the ROAD is two-way; whether the next lane over is a turn lane or a travel lane is
      # then decided by sameDirectionRecent, because a centre turn lane and an ordinary passing
      # lane are geometrically identical (a lane at 3.7 m, opposing traffic at 7.9 m, our own road
      # edge beyond both) and nothing in the sensors separates them.
      oncomingAdjacent @9 :Bool;
      sameDirectionRecent @10 :Bool;
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
    }

    # BluePilot: the manoeuvre this WOULD perform, run as a dry run. Nothing actuates; see
    # passing_manoeuvre.py. The detector above answers "would I suggest a pass this frame", which
    # is a single frame's verdict and NOT what decides whether an automatic system works -- that
    # depends on whether the verdict holds still long enough to act on. These fields make the
    # sequence visible over time, and `manoeuvreAborts` is the number the whole thing is for.
    manoeuvre @41 :Manoeuvre;
    manoeuvreSeconds @42 :Float32;   # time in the current phase
    manoeuvreSide @43 :Side;         # the side the sequence committed to
    blinkerWouldBeOn @44 :Bool;      # on through the crossing, out when it completes
    steeringWouldBeActive @45 :Bool;

    # Sequences that reached `signalling` and then backed out -- a blinker shown to traffic behind
    # for a manoeuvre that did not happen. Near zero on a drive means the gates are stable enough
    # to act on. Anything else names an unstable gate that no amount of reading the code would
    # have found.
    manoeuvreAborts @46 :UInt16;

    # BluePilot: was the lead this decision rests on RADAR-confirmed, or the camera model alone?
    #
    # radard picks leads camera-first: modelV2 proposes, and a matching radar track refines it. With
    # no matching track and a confident model, get_RadarState_from_vision returns a lead with
    # radar=False whose vLead is the MODEL's velocity estimate rather than Doppler. That is a much
    # weaker basis for "this car is 4 mph slower than I asked for", which is the entire judgement
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
    wantedSeconds @58 :Float32;      # total time a pass was warranted
    topBlockedBy @59 :Blocked;       # the reason that consumed the most of it
    topBlockedShare @60 :Float32;    # its share, 0..1
    clearShare @61 :Float32;         # share where nothing was stopping it at all

    # BluePilot: WHICH manoeuvre the fields above describe. Passing and keep-right are mutually
    # exclusive by construction -- keep-right is only ever evaluated on the frames where no pass is
    # warranted -- so they share one set of state fields and are told apart by this.
    #
    # Their ABORT COUNTS stay separate, though, because that number is the readiness metric for
    # each. Lumping them together would say a gate somewhere is unstable without saying which
    # manoeuvre it belongs to, which is most of the value.
    manoeuvreReason @62 :Reason;
    keepRightAborts @63 :UInt16;

    # BluePilot: crossings REVERSED because something was arriving behind, as opposed to sequences
    # that merely backed out before moving. Counted apart from manoeuvreAborts because they are not
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
    manoeuvreStandDown @68 :Float32;

    enum Manoeuvre {
      idle @0;         # nothing warranted
      confirming @1;   # a slower vehicle is being confirmed, timer running
      waiting @2;      # confirmed, and a gate is what is stopping us -- see blockedBy
      signalling @3;   # blinker would be on, holding before any movement
      changing @4;     # crossing. COMMITTED: gates can no longer call it off, only the driver can
      finishing @5;    # across, blinker out
      aborting @6;     # backing out of a crossing already begun -- something arrived behind us
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

  # BluePilot: the LKA button on the end of the turn-signal stalk. It does nothing on this car --
  # the wheel's LCA button is what toggles lateral -- so it is a free physical input, and a stalk
  # press beats reaching for the screen for anything wanted while driving.
  #
  # LaSwtchPos_D_Stat in Steering_Data_FD1 (0x083, bus 0): 0 Open / 1 Pressed / 2 Unused / 3 Fault.
  # openpilot already parses that message and passes the signal through untouched in
  # create_button_msg, so reading it costs nothing -- no new message, parser entry or DBC work.
  lkaButtonPressed @7 :Bool;

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
      resultStillShowing @4; # pressed again while the last verdict was still up; press once more
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
