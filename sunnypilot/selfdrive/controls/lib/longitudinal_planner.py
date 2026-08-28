"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

import math
from cereal import messaging, custom
from opendbc.car import structs
from openpilot.common.constants import CV
from openpilot.selfdrive.car.cruise import V_CRUISE_MAX, V_CRUISE_UNSET
from openpilot.sunnypilot.selfdrive.controls.lib.dec.dec import DynamicExperimentalController
from openpilot.sunnypilot.selfdrive.controls.lib.e2e_alerts_helper import E2EAlertsHelper
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.smart_cruise_control import SmartCruiseControl
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.speed_limit_assist import SpeedLimitAssist
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.speed_limit_resolver import SpeedLimitResolver
from openpilot.sunnypilot.selfdrive.controls.lib.passing_assist import PassingAssistDetector
from openpilot.sunnypilot.selfdrive.controls.lib.unconfirmed_lead import UnconfirmedLeadDetector
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP, EventNameSP
from openpilot.sunnypilot.models.helpers import get_active_bundle

DecState = custom.LongitudinalPlanSP.DynamicExperimentalControl.DynamicExperimentalControlState
LongitudinalPlanSource = custom.LongitudinalPlanSP.LongitudinalPlanSource


# The services upstream's longitudinal_planner.publish() passes to all_checks for
# longitudinalPlan.valid. Duplicated here ONLY to explain that rule, so the two must not drift --
# test_planner_checks_mirror_the_plan parses both and fails if they do. Bit order is this order.
PLAN_VALID_SERVICES = ('carState', 'controlsState', 'selfdriveState', 'radarState')


class LongitudinalPlannerSP:
  def __init__(self, CP: structs.CarParams, CP_SP: structs.CarParamsSP, mpc):
    self.events_sp = EventsSP()
    self.resolver = SpeedLimitResolver()
    self.dec = DynamicExperimentalController(CP, mpc)
    self.scc = SmartCruiseControl()
    self.resolver = SpeedLimitResolver()
    self.sla = SpeedLimitAssist(CP, CP_SP)
    self.unconfirmed_lead = UnconfirmedLeadDetector()
    self.passing_assist = PassingAssistDetector()
    self.generation = int(model_bundle.generation) if (model_bundle := get_active_bundle()) else None
    self.source = LongitudinalPlanSource.cruise
    self.e2e_alerts_helper = E2EAlertsHelper()

    self.output_v_target = 0.
    self.output_a_target = 0.

  def is_e2e(self, sm: messaging.SubMaster) -> bool:
    experimental_mode = sm['selfdriveState'].experimentalMode
    if not self.dec.active():
      return experimental_mode

    return experimental_mode and self.dec.mode() == "blended"

  def update_targets(self, sm: messaging.SubMaster, v_ego: float, a_ego: float, v_cruise: float) -> tuple[float, float]:
    CS = sm['carState']
    v_cruise_cluster_kph = min(CS.vCruiseCluster, V_CRUISE_MAX)
    v_cruise_cluster = v_cruise_cluster_kph * CV.KPH_TO_MS

    long_enabled = sm['carControl'].enabled
    long_override = sm['carControl'].cruiseControl.override

    # Smart Cruise Control
    self.scc.update(sm, long_enabled, long_override, v_ego, a_ego, v_cruise)

    # Speed Limit Resolver
    self.resolver.update(v_ego, sm)

    # BluePilot: radar-blind lead detection. Reads the stock planner's own MPC plan, which
    # already accounts for vision-only leads, and reports on its own channel so it bypasses ICBM's
    # target-drop rate limiter. v_desired_trajectory is one cycle stale here (mpc.update runs after
    # update_targets), which at 20 Hz is 50 ms and immaterial to a multi-second deceleration.
    # dec.has_slow_down() is this frame's, not last frame's: LongitudinalPlannerSP.update -- which
    # runs dec.update -- is called at the top of LongitudinalPlanner.update, before update_targets.
    self.unconfirmed_lead.update(sm, self.v_desired_trajectory, v_cruise_cluster,
                                 long_enabled and not long_override, self.events_sp,
                                 self.dec.has_slow_down(), self.dec.endpoint_x())

    # BluePilot: passing-assist observation. Log only -- no events_sp, no target, no return value
    # consumed anywhere. Takes v_cruise_cluster rather than v_cruise because the question is
    # whether the lead is holding us below the speed the DRIVER set, not below a limiter's output.
    # speed_limit_final is the limit plus offset -- one of the things that can express what the
    # driver actually asked for, but ONLY when Speed Limit Assist is switched on. A valid limit is
    # not consent to drive it: with SLA off the driver's set speed is the whole intent, and feeding
    # the limit in anyway made passing assist suggest passes to reach a speed they had not asked
    # for. See PassingAssistDetector._reference_speed.
    sl_target = (self.resolver.speed_limit_final
                 if self.sla.enabled and self.resolver.speed_limit_valid else 0.0)
    # ...and the RAW posted limit alongside it, which is a different question and needs a different
    # answer. sl_target is what SLA would drive and is zeroed when SLA is off; this is what the sign
    # says, wanted whether or not he has handed the speed over. See _apply_patience: it measures how
    # far over the limit he has ASKED to go, and gating that on SLA being enabled would switch the
    # measurement off precisely when the driver is the one choosing the speed.
    posted = float(self.resolver.speed_limit) if self.resolver.speed_limit_valid else 0.0
    self.passing_assist.update(sm, v_cruise_cluster, long_enabled, sl_target, posted)
    # ...with one exception to "log only": a chime when it decides. See passingAssistSuggested --
    # the panel is the whole readout for this feature and nobody is reading it at the moment that
    # matters. Still no target and no return value; the only thing that leaves here is a sound.
    if self.passing_assist.suggestion_started and self.passing_assist.chime_enabled:
      self.events_sp.add(EventNameSP.passingAssistSuggested)
    # ...and the lower one when it backs out. Same toggle: a driver who wants to be told what it is
    # doing wants both halves, and a control that announces only the successes would be worse than
    # no control at all.
    if self.passing_assist.abort_started and self.passing_assist.chime_enabled:
      self.events_sp.add(EventNameSP.passingAssistBackedOut)

    # Speed Limit Assist
    has_speed_limit = self.resolver.speed_limit_valid or self.resolver.speed_limit_last_valid
    # BluePilot: the driver's hold, in CLUSTER units, or 0 when there is none. Only the
    # announcement uses it -- SLA's target is unchanged, because a hold governs what ICBM AIMS at
    # and not what the posted limit IS. Read defensively: on a build where selfdriveStateSP is not
    # up yet this reads 0, which restores the previous announce-always behaviour rather than
    # silencing the alert.
    v_baseline_conv = 0.0
    try:
      v_baseline_conv = float(sm['selfdriveStateSP'].intelligentCruiseButtonManagement.vBaseline)
    except Exception:
      pass
    self.sla.update(long_enabled, long_override, v_ego, a_ego, v_cruise_cluster, self.resolver.speed_limit,
                    self.resolver.speed_limit_final_last, has_speed_limit, self.resolver.distance, self.events_sp,
                    v_baseline_conv)

    targets = {
      LongitudinalPlanSource.sccVision: (self.scc.vision.output_v_target, self.scc.vision.output_a_target),
      LongitudinalPlanSource.sccMap: (self.scc.map.output_v_target, self.scc.map.output_a_target),
      LongitudinalPlanSource.speedLimitAssist: (self.sla.output_v_target, self.sla.output_a_target),
    }

    # BluePilot: with bidirectional Speed Limit Assist, SLA becomes authoritative rather than one
    # more limiter -- it replaces the cruise baseline instead of being min()'d against it, which is
    # what lets it raise the set speed as well as lower it. This is a deliberate departure from
    # upstream sunnypilot, where the min()-of-sources architecture is exactly what guarantees SLA
    # can never auto-increase. The ceiling (SpeedLimitMaxSetSpeed) bounds what it may request, and
    # ICBM's manual override latch lets one real button press take it back.
    #
    # The curve controllers stay inside the min() either way: they may only ever lower.
    #
    # ...but ONLY WHILE SLA IS ACTUALLY ASKING FOR SOMETHING. is_active alone is not that: SLA stays
    # active across a stretch with no speed limit data, and its target there is V_CRUISE_UNSET. Drop
    # cruise on is_active alone and every candidate can be unset at once, which publishes a vTarget
    # nobody requested. ICBM then rejects it as unreal and falls back to holding the CURRENT set
    # speed -- so the number freezes wherever it happened to be, and a hold cannot pull it back
    # because the hold is applied to a target that has already been replaced by the cluster.
    #
    # Measured on route 00000348, 2026-08-11, t+838 to t+876: vision and map both inactive at 570 mph
    # (V_CRUISE_UNSET), SLA the same, planner vTarget 570, and the set speed sat at 38 through a full
    # stop and the restart while the driver's hold was 50. It only recovered when he cancelled and
    # re-engaged. His report: "it got stuck at 38, even though my hold was set to 50. The hold never
    # resumed until I canceled and resumed."
    #
    # The plan source said sccVision throughout, which is a red herring worth remembering: min() over
    # equally-unset candidates still has to name one, so the label pointed at a controller that was
    # inactive and asking for nothing.
    sla_owns_baseline = (self.sla.auto_follow and self.sla.is_active
                         and self.sla.output_v_target < V_CRUISE_UNSET)
    if not sla_owns_baseline:
      targets[LongitudinalPlanSource.cruise] = (v_cruise, a_ego)

    self.source = min(targets, key=lambda k: targets[k][0])
    self.output_v_target, self.output_a_target = targets[self.source]
    return self.output_v_target, self.output_a_target

  def update(self, sm: messaging.SubMaster) -> None:
    self.events_sp.clear()
    self.dec.update(sm)
    self.e2e_alerts_helper.update(sm, self.events_sp)

  def publish_longitudinal_plan_sp(self, sm: messaging.SubMaster, pm: messaging.PubMaster) -> None:
    plan_sp_send = messaging.new_message('longitudinalPlanSP')

    plan_sp_send.valid = sm.all_checks(service_list=['carState', 'controlsState'])

    longitudinalPlanSP = plan_sp_send.longitudinalPlanSP

    # See custom.capnp PlannerChecks. A commIssue is ET.SOFT_DISABLE, so an invalid longitudinal
    # plan DISENGAGES -- and nothing recorded WHICH of all_checks' three tests failed, or for which
    # service. Recomputed rather than intercepted because upstream's publish() throws the breakdown
    # away the moment it ANDs it into one bool.
    checks = longitudinalPlanSP.plannerChecks
    not_alive = freq_bad = not_valid = 0
    for bit, svc in enumerate(PLAN_VALID_SERVICES):
      if not sm.alive[svc]:
        not_alive |= 1 << bit
      # Mirrors all_freq_ok's own filter: a service excluded from the average-frequency test can
      # never fail it, and reporting it as failing would send the next drive after a phantom.
      if sm._check_avg_freq(svc) and not sm.freq_ok[svc]:
        freq_bad |= 1 << bit
      if not sm.valid[svc]:
        not_valid |= 1 << bit
    checks.notAlive = not_alive
    checks.freqBad = freq_bad
    checks.notValid = not_valid
    checks.planValid = sm.all_checks(service_list=list(PLAN_VALID_SERVICES))

    longitudinalPlanSP.longitudinalPlanSource = self.source
    longitudinalPlanSP.vTarget = float(self.output_v_target)
    longitudinalPlanSP.aTarget = float(self.output_a_target)
    longitudinalPlanSP.events = self.events_sp.to_msg()

    # FusionPilot: ask ICBM for a closer follow gap while a pass is being pursued.
    #
    # ASSERTED, NOT TIMED -- ICBM releases on silence, which is deliberate on their side and right:
    # a stored deadline cannot survive this planner dying, and continuous assertion can. So this is
    # republished every frame it is still wanted and simply stops otherwise.
    #
    # EARLY, because reaching a gap takes up to ~4.5 s of confirmed toggle steps and set-speed
    # presses win the wire. Requesting at the start of the maneuver would arrive after the moment it
    # was for. See PassingAssistDetector.gap_request for what "pursuing" means.
    longitudinalPlanSP.accGapRequest = int(self.passing_assist.gap_request)

    # Dynamic Experimental Control
    dec = longitudinalPlanSP.dec
    dec.state = DecState.blended if self.dec.mode() == 'blended' else DecState.acc
    dec.enabled = self.dec.enabled()
    dec.active = self.dec.active()
    # FusionPilot: publish the stop-sign signal itself, not only DEC's mode. `has_slow_down()` is
    # what drives the stop-sign path in unconfirmed_lead.py, it is computed from modelV2 alone so it
    # is live even with DEC off, and it had never reached a log -- which is why "stop signs are not
    # accurate, traffic lights are" could not be attributed to detection or to response.
    #
    # endpoint_x() is inf when the model's plan is not full length. capnp Float32 takes inf, but a
    # tool reading it has to handle that, so it is clamped to 0 here and 0 means "no endpoint"
    # rather than "stopping right now" -- the one reading that would invert the meaning.
    # bool(), not the bare value. `has_slow_down()` is `urgency_filtered > SLOW_DOWN_PROB` where
    # urgency_filtered is a numpy scalar, so it returns numpy.bool -- which capnp REFUSES, killing
    # plannerd on the first frame. It cost a drive on 2026-08-18. Any numpy-derived value crossing
    # into capnp needs an explicit Python cast; the float() calls below were already right.
    dec.hasSlowDown = bool(self.dec.has_slow_down())
    dec.slowDownUrgency = float(self.dec.urgency())
    endpoint = float(self.dec.endpoint_x())
    dec.slowDownEndpoint = 0.0 if not math.isfinite(endpoint) else endpoint

    # Smart Cruise Control
    smartCruiseControl = longitudinalPlanSP.smartCruiseControl
    # Vision Control
    sccVision = smartCruiseControl.vision
    sccVision.state = self.scc.vision.state
    sccVision.vTarget = float(self.scc.vision.output_v_target)
    sccVision.aTarget = float(self.scc.vision.output_a_target)
    sccVision.currentLateralAccel = float(self.scc.vision.current_lat_acc)
    sccVision.maxPredictedLateralAccel = float(self.scc.vision.max_pred_lat_acc)
    sccVision.enabled = self.scc.vision.is_enabled
    sccVision.active = self.scc.vision.is_active
    # Map Control
    sccMap = smartCruiseControl.map
    sccMap.state = self.scc.map.state
    sccMap.vTarget = float(self.scc.map.output_v_target)
    sccMap.aTarget = float(self.scc.map.output_a_target)
    sccMap.enabled = self.scc.map.is_enabled
    sccMap.active = self.scc.map.is_active
    # FusionPilot: the vetoes' own inputs. See the comment on the capnp struct -- a decision without
    # its arguments is what made "it dropped to 20 for no reason" undiagnosable from four drives.
    #
    # `target_distance` is inf whenever the map has nothing to say, and capnp clamps inf to a
    # meaningless value, so it goes out as 0.0. **0 means NO CORNER, never "the corner is here"** --
    # the same inversion `slowDownEndpoint` had to guard against, and the reading that would make
    # this field dangerous instead of merely absent.
    td = float(self.scc.map.target_distance)
    sccMap.targetDistance = td if math.isfinite(td) else 0.0
    sccMap.modelLatAcc = float(self.scc.map.model_lat_acc)
    sccMap.modelVetoed = bool(self.scc.map.model_vetoed)
    sccMap.cameraNotSeen = bool(self.scc.map.camera_not_seen)
    sccMap.vetoedVTarget = float(self.scc.map.vetoed_v_target)

    # Speed Limit
    speedLimit = longitudinalPlanSP.speedLimit
    resolver = speedLimit.resolver
    resolver.speedLimit = float(self.resolver.speed_limit)
    resolver.speedLimitLast = float(self.resolver.speed_limit_last)
    resolver.speedLimitFinal = float(self.resolver.speed_limit_final)
    resolver.speedLimitFinalLast = float(self.resolver.speed_limit_final_last)
    resolver.speedLimitValid = self.resolver.speed_limit_valid
    resolver.speedLimitLastValid = self.resolver.speed_limit_last_valid
    resolver.speedLimitOffset = float(self.resolver.speed_limit_offset)
    resolver.distToSpeedLimit = float(self.resolver.distance)
    resolver.source = self.resolver.source
    assist = speedLimit.assist
    assist.state = self.sla.state
    assist.enabled = self.sla.is_enabled
    assist.active = self.sla.is_active
    assist.vTarget = float(self.sla.output_v_target)
    assist.aTarget = float(self.sla.output_a_target)

    # BluePilot: radar-blind lead state
    unconfirmedLead = longitudinalPlanSP.unconfirmedLead
    unconfirmedLead.state = self.unconfirmed_lead.state
    unconfirmedLead.vTarget = float(self.unconfirmed_lead.v_target)
    unconfirmedLead.restoreSetSpeed = float(self.unconfirmed_lead.restore_set_speed)
    unconfirmedLead.dRel = float(self.unconfirmed_lead.d_rel)
    unconfirmedLead.ttc = float(self.unconfirmed_lead.ttc)
    unconfirmedLead.modelShouldStop = self.unconfirmed_lead.model_should_stop
    unconfirmedLead.modelDesiredAccel = float(self.unconfirmed_lead.model_desired_accel)
    unconfirmedLead.hasLead = self.unconfirmed_lead.has_lead
    unconfirmedLead.trigger = self.unconfirmed_lead.trigger

    # BluePilot: passing-assist observation (log only -- see the capnp comment).
    # The field copying lives in the detector so it is not upstream rebase surface.
    self.passing_assist.publish(longitudinalPlanSP.passingAssist)

    # E2E Alerts
    e2eAlerts = longitudinalPlanSP.e2eAlerts
    e2eAlerts.greenLightAlert = self.e2e_alerts_helper.green_light_alert
    e2eAlerts.leadDepartAlert = self.e2e_alerts_helper.lead_depart_alert

    pm.send('longitudinalPlanSP', plan_sp_send)
