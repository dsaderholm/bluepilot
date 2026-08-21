"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import cereal.messaging as messaging
from cereal import log, car, custom
from openpilot.common.constants import CV
from openpilot.sunnypilot.selfdrive.selfdrived.events_base import EventsBase, Priority, ET, Alert, \
  NoEntryAlert, ImmediateDisableAlert, EngagementAlert, NormalPermanentAlert, AlertCallbackType, wrong_car_mode_alert
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit import PCM_LONG_REQUIRED_MAX_SET_SPEED, CONFIRM_SPEED_THRESHOLD
from openpilot.system.hardware import HARDWARE

AlertSize = log.SelfdriveState.AlertSize
AlertStatus = log.SelfdriveState.AlertStatus
VisualAlert = car.CarControl.HUDControl.VisualAlert
AudibleAlert = car.CarControl.HUDControl.AudibleAlert
AudibleAlertSP = custom.SelfdriveStateSP.AudibleAlert
EventNameSP = custom.OnroadEventSP.EventName


# get event name from enum
EVENT_NAME_SP = {v: k for k, v in EventNameSP.schema.enumerants.items()}


# FusionPilot: can the stop override actually take a stop to a standstill this drive? Both toggles
# have to be on -- the override sends openpilot's braking IN PLACE OF Ford's, which only exists
# under the passthrough. Cached because an alert must not do a filesystem read per call, and because
# the carcontroller reads these same two once at init: changing either mid-drive already does
# nothing, by design.
# `_stop_override_available()` lived here and was REMOVED 2026-08-20. It read two params to decide
# whether the model-stop alert could promise the car would stop. That promise was withdrawn on
# 2026-08-19 -- the alert says "the stop is yours" unconditionally now -- and the helper has fed
# nothing since; only a test kept it alive. A function that reads configuration and changes no
# behaviour is a false lead in exactly the file where alert wording gets debugged.
IS_MICI = HARDWARE.get_device_type() == 'mici'

_METER_TO_FOOT = 3.28084  # common.constants.CV has no length conversions


def speed_limit_adjust_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  speedLimit = sm['longitudinalPlanSP'].speedLimit.resolver.speedLimit
  speed = round(speedLimit * (CV.MS_TO_KPH if metric else CV.MS_TO_MPH))
  message = f'Adjusting to {speed} {"km/h" if metric else "mph"} speed limit'
  return Alert(
    message,
    "",
    AlertStatus.normal, AlertSize.small,
    Priority.LOW, VisualAlert.none, AudibleAlert.none, 4.)


def unconfirmed_lead_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  """BluePilot: a vision lead the radar has not confirmed, so stock ACC will not brake for it.

  Raised the moment the detector triggers rather than when the set speed reaches Ford's floor: the
  whole deceleration is meant to be the driver's reaction time.

  DOWNGRADED 2026-08-06, and the reason is that the feature got BETTER, not that the risk got
  smaller. It shipped as the loudest thing this fork can command -- AlertStatus.critical with
  VisualAlert.fcw, which reaches the Ford carcontroller and lights the CLUSTER's own collision
  warning and chime. That was right when the set speed only walked down along the MPC plan and the
  driver really was the thing that had to stop the car.

  It now asks for Ford's floor the instant it confirms, and two drives say the resulting
  deceleration is right. So the alert's job changed from "brake NOW" to "I am slowing for something
  the radar cannot see". His words, after two false positives in one drive: "we might want to dial
  back the warning to not be a oh no, you're about to die warning".

  What comes off: VisualAlert.fcw, so the cluster no longer shows a collision warning with nothing
  ahead, and warningImmediate, which is the panic tone. What stays: AlertSize.mid so the distance is
  still readable, and a prompt loud enough to look up for. At two false positives a drive, an
  emergency tone is how the real one gets ignored.
  """
  ul = sm['longitudinalPlanSP'].unconfirmedLead
  dist = round(ul.dRel * (1.0 if metric else _METER_TO_FOOT))
  unit = "m" if metric else "ft"

  # Deliberately NOT worded or sounded like model_stop_alert. Downgrading both on 2026-08-06 left
  # them identical -- same status, same size, same tone, both opening with "Slowing" -- and he could
  # not tell which had fired: "I'm not sure if the unconfirmed lead has the same warning and I'm just
  # confused here". Two different causes need two different signatures, or the driver learns nothing
  # from either. This one names a VEHICLE and keeps the higher tone, because it is the one with
  # something solid in the road.
  return Alert(
    "Vehicle ahead - radar has not confirmed it",
    f"Vision only at {dist} {unit}. Cruise will not stop for it.",
    AlertStatus.userPrompt, AlertSize.mid,
    Priority.HIGH, VisualAlert.none, AudibleAlertSP.promptSingleHigh, 2.)


def model_stop_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  """BluePilot: the driving model wants to stop -- a sign or signal -- and Ford ACC will not.

  DRIVEN 2026-08-06 and it works: it slowed for red lights, and the paced ramp out of
  _model_stop_target felt right. It also produced two false positives in one drive, which is what
  this presentation now has to survive.

  DOWNGRADED to match unconfirmed_lead_alert, which was downgraded the same day for the same reason.
  Off: VisualAlert.fcw, which reaches the Ford carcontroller and lights the CLUSTER's own collision
  warning and chime -- the thing he described as an "oh no, you're about to die warning" -- and
  warningImmediate, the panic tone. On: AlertSize.mid, userPrompt, and the lower `prompt` tone --
  see the note above the return for why this one is not promptSingleHigh like the lead alert.

  Read the history before raising it again, because it has been argued both ways on bad evidence:

    - It shipped quiet, on the theory it would fire at every signal in town.
    - It was made LOUD because "the owner went weeks without ever seeing this alert once". That
      evidence was worthless -- the trigger gated on modelV2.action.shouldStop, which is false at
      every speed this path can run at. The alert was unreachable, not rare.
    - With a working trigger it fires a few times a drive including false positives, which is the
      fatigue case the original quiet choice was worried about. So quiet was right, for a reason
      nobody had measured until now.

  The severity argument that pushed it loud still stands on its own: a radar-blind lead has an
  escape, since Ford's radar may still acquire it, and a stop sign never does. But that argues for
  the driver being told, not for the cluster's collision warning firing at an empty intersection --
  and the car is now taking real action about it rather than only warning.
  """
  # Distinct from unconfirmed_lead_alert on purpose -- see the note there. This one names a SIGN,
  # carries no distance because there is no object to measure, and uses the lower tone: an empty
  # intersection is the less urgent of the two, and the tone is what tells them apart when the
  # driver is looking at the road rather than the screen.
  # FusionPilot: "Cruise will not stop for it" became a HALF-TRUTH the day the stop override
  # shipped, and he caught it: *"I still got a few alerts that cruise would not stop, which doesn't
  # make sense because it should stop for everything now, right?"*
  #
  # It does not, yet, and the alert must not promise otherwise -- measured 2026-08-19, the override
  # has never once fired. But the reason is a PRECONDITION he can act on, and no screen was telling
  # him: it only runs while cruise is still ENGAGED, and braking disengages. So the alert now says
  # what he can DO about it rather than only what the car will not do.
  #
  # Read once and cached: this fires a few times a drive, and the carcontroller reads the same two
  # params once at init, so a mid-drive change is already meaningless by design.
  # WITHDRAWN 2026-08-20: "Stay off the brake to let it stop" WAS A PROMISE THE CAR CANNOT KEEP.
  #
  # It fired on `hasSlowDown`, while the override arms on `shouldStop` -- and `shouldStop` is
  # measured to be a STOPPED-CAR state, not an approach state. Across three drives and 21,936
  # frames it was never once true above 3 mph:
  #
  #     0000039a  5169 frames  max 1.7 mph      00000393  7103  max 2.9 mph
  #     00000397  9664 frames  max 2.8 mph      above 5 mph: 0.0% on all three
  #
  # So he did exactly what the alert asked -- foot off the brake at a red light, engaged, at 20 mph
  # with the set speed walking down 80 -> 57 -- and nothing stopped, because the override's trigger
  # cannot become true until the car has already stopped. He braked. The alert had told him not to.
  #
  # An alert that asks the driver to withhold a control input MUST be keyed on the same signal as
  # the thing that will act. Until the override triggers on approach intent rather than on a stopped
  # car, there is no wording that can honestly ask him to leave the brake alone -- so it does not.
  return Alert(
    "Stop sign or signal ahead",
    "Slowing to 20 mph -- the stop is yours.",
    AlertStatus.userPrompt, AlertSize.mid,
    Priority.MID, VisualAlert.none, AudibleAlertSP.prompt, 2.)


def speed_limit_auto_set_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  """BluePilot: fired on every automatic set-speed change made by bidirectional Speed Limit Assist.

  Deliberately announces the direction as well as the value: with auto-raise enabled, a wrong map
  tag can push the set speed *up*, and that has to be visible immediately rather than felt. One
  real button press latches ICBM to MANUAL and takes it back.
  """
  speed_conv = CV.MS_TO_KPH if metric else CV.MS_TO_MPH
  unit = "km/h" if metric else "mph"

  target = round(sm['longitudinalPlanSP'].speedLimit.assist.vTarget * speed_conv)
  # carState.vCruiseCluster is kph (card.py sets it from v_cruise_cluster_kph), not m/s
  set_speed = round(CS.vCruiseCluster * CV.KPH_TO_MS * speed_conv)
  direction = "Raising" if target > set_speed else "Lowering"

  return Alert(
    f"{direction} set speed to {target} {unit} speed limit",
    "",
    AlertStatus.normal, AlertSize.small,
    Priority.LOW, VisualAlert.none, AudibleAlertSP.promptSingleHigh, 4.)


def speed_limit_pre_active_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  speed_conv = CV.MS_TO_KPH if metric else CV.MS_TO_MPH
  v_cruise_cluster = CS.vCruiseCluster
  set_speed = sm['controlsState'].deprecated.vCruise if v_cruise_cluster == 0.0 else v_cruise_cluster
  set_speed_conv = round(set_speed * speed_conv)

  speed_limit_final_last = sm['longitudinalPlanSP'].speedLimit.resolver.speedLimitFinalLast
  speed_limit_final_last_conv = round(speed_limit_final_last * speed_conv)
  alert_1_str = ""
  alert_size = AlertSize.small

  if CP.openpilotLongitudinalControl and CP.pcmCruise:
    # PCM long
    cst_low, cst_high = PCM_LONG_REQUIRED_MAX_SET_SPEED[metric]
    pcm_long_required_max = cst_low if speed_limit_final_last_conv < CONFIRM_SPEED_THRESHOLD[metric] else cst_high
    pcm_long_required_max_set_speed_conv = round(pcm_long_required_max * speed_conv)
    speed_unit = "km/h" if metric else "mph"

    alert_1_str = f"Speed Limit Assist: set to {pcm_long_required_max_set_speed_conv} {speed_unit} to engage"
  else:
    if IS_MICI:
      if set_speed_conv < speed_limit_final_last_conv:
        alert_1_str = "Press + to confirm speed limit"
      elif set_speed_conv > speed_limit_final_last_conv:
        alert_1_str = "Press - to confirm speed limit"
    else:
      alert_size = AlertSize.none

  return Alert(
    alert_1_str,
    "",
    AlertStatus.normal, alert_size,
    Priority.LOW, VisualAlert.none, AudibleAlertSP.promptSingleLow, .1)


class EventsSP(EventsBase):
  def __init__(self):
    super().__init__()
    self.event_counters = dict.fromkeys(EVENTS_SP.keys(), 0)

  def get_events_mapping(self) -> dict[int, dict[str, Alert | AlertCallbackType]]:
    return EVENTS_SP

  def get_event_name(self, event: int):
    return EVENT_NAME_SP[event]

  def get_event_msg_type(self):
    return custom.OnroadEventSP.Event


EVENTS_SP: dict[int, dict[str, Alert | AlertCallbackType]] = {
  # sunnypilot
  EventNameSP.lkasEnable: {
    ET.ENABLE: EngagementAlert(AudibleAlert.engage),
  },

  EventNameSP.lkasDisable: {
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.disengage),
  },

  EventNameSP.manualSteeringRequired: {
    ET.USER_DISABLE: Alert(
      "Automatic Lane Centering is OFF",
      "Manual Steering Required",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.disengage, 1.),
  },

  EventNameSP.manualLongitudinalRequired: {
    ET.WARNING: Alert(
      "Smart/Adaptive Cruise Control: OFF",
      "Manual Speed Control Required",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, 1.),
  },

  EventNameSP.silentLkasEnable: {
    ET.ENABLE: EngagementAlert(AudibleAlert.none),
  },

  EventNameSP.silentLkasDisable: {
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.none),
  },

  EventNameSP.silentBrakeHold: {
    ET.WARNING: EngagementAlert(AudibleAlert.none),
    ET.NO_ENTRY: NoEntryAlert("Brake Hold Active"),
  },

  EventNameSP.silentWrongGear: {
    ET.WARNING: Alert(
      "",
      "",
      AlertStatus.normal, AlertSize.none,
      Priority.LOWEST, VisualAlert.none, AudibleAlert.none, 0.),
    ET.NO_ENTRY: Alert(
      "Gear not D",
      "openpilot Unavailable",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, 0.),
  },

  EventNameSP.silentReverseGear: {
    ET.PERMANENT: Alert(
      "Reverse\nGear",
      "",
      AlertStatus.normal, AlertSize.full,
      Priority.LOWEST, VisualAlert.none, AudibleAlert.none, .2, creation_delay=0.5),
    ET.NO_ENTRY: NoEntryAlert("Reverse Gear"),
  },

  EventNameSP.silentDoorOpen: {
    ET.WARNING: Alert(
      "",
      "",
      AlertStatus.normal, AlertSize.none,
      Priority.LOWEST, VisualAlert.none, AudibleAlert.none, 0.),
    ET.NO_ENTRY: NoEntryAlert("Door Open"),
  },

  EventNameSP.silentSeatbeltNotLatched: {
    ET.WARNING: Alert(
      "",
      "",
      AlertStatus.normal, AlertSize.none,
      Priority.LOWEST, VisualAlert.none, AudibleAlert.none, 0.),
    ET.NO_ENTRY: NoEntryAlert("Seatbelt Unlatched"),
  },

  EventNameSP.silentParkBrake: {
    ET.WARNING: Alert(
      "",
      "",
      AlertStatus.normal, AlertSize.none,
      Priority.LOWEST, VisualAlert.none, AudibleAlert.none, 0.),
    ET.NO_ENTRY: NoEntryAlert("Parking Brake Engaged"),
  },

  EventNameSP.controlsMismatchLateral: {
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("Controls Mismatch: Lateral"),
    ET.NO_ENTRY: NoEntryAlert("Controls Mismatch: Lateral"),
  },

  EventNameSP.experimentalModeSwitched: {
    ET.WARNING: NormalPermanentAlert("Experimental Mode Switched", duration=1.5)
  },

  EventNameSP.wrongCarModeAlertOnly: {
    ET.WARNING: wrong_car_mode_alert,
  },

  EventNameSP.pedalPressedAlertOnly: {
    ET.WARNING: NoEntryAlert("Pedal Pressed")
  },

  EventNameSP.laneTurnLeft: {
    ET.WARNING: Alert(
      "Turning Left",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, 1.),
  },

  EventNameSP.laneTurnRight: {
    ET.WARNING: Alert(
      "Turning Right",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, 1.),
  },

  EventNameSP.speedLimitActive: {
    ET.WARNING: Alert(
      "Auto adjusting to speed limit",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlertSP.promptSingleHigh, 5.),
  },

  EventNameSP.modelStopBraking: {
    ET.WARNING: model_stop_alert,
  },

  # FusionPilot: the passthrough is finished for the drive and openpilot longitudinal is driving.
  #
  # Route 0000038d, 2026-08-18: the camera asserted cancel and deny on 8,988 of 8,990 engaged frames
  # from t+30.8 onward, while the PCM reported cruise healthy the whole time. He worked it out from
  # the seat -- "Ford ACC stopped working entirely, so I had to just use MADS the rest of the way" --
  # and afterwards, "it's just annoying that it bricks it for the whole drive".
  #
  # It is not recoverable within a drive, so this fires ONCE. A repeating alert for a permanent
  # condition is noise, and he would learn to ignore it. Priority LOW and a single prompt rather
  # than a warning: nothing is unsafe, the car is being driven by a controller he does not prefer,
  # and the useful response is a decision at the next stop rather than a reaction now.
  EventNameSP.accPassthroughInert: {
    ET.PERMANENT: Alert(
      "Ford ACC unavailable",
      "Camera cancelled; openpilot is driving longitudinal",
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlertSP.promptSingleHigh, 6.),
  },

  EventNameSP.unconfirmedLeadBraking: {
    ET.WARNING: unconfirmed_lead_alert,
  },

  EventNameSP.speedLimitAutoSet: {
    ET.WARNING: speed_limit_auto_set_alert,
  },

  EventNameSP.speedLimitChanged: {
    ET.WARNING: Alert(
      "Set speed changed",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlertSP.promptSingleHigh, 5.),
  },

  EventNameSP.speedLimitPreActive: {
    ET.WARNING: speed_limit_pre_active_alert,
  },

  EventNameSP.speedLimitPending: {
    ET.WARNING: Alert(
      "Auto adjusting to last speed limit",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlertSP.promptSingleHigh, 5.),
  },

  # BluePilot: a bare chime when passing assist decides. No text and AlertSize.none, so it takes
  # nothing over -- the panel is already saying which side and why, and an alert box on top of it
  # would cover the readout at the one moment it is worth reading. Short: this is a notification
  # that a decision happened, not a warning to act on.
  EventNameSP.passingAssistSuggested: {
    ET.PERMANENT: Alert(
      "",
      "",
      AlertStatus.normal, AlertSize.none,
      Priority.LOW, VisualAlert.none, AudibleAlert.prompt, 1.),
  },

  # BluePilot: the LOWER tone, when a sequence lights the blinker and then withdraws it.
  #
  # "I'll keep reporting back to you instances where it messed up. That's why I like that it makes
  # a sound." The sound is his reporting channel, and it covered only the successful case -- so
  # `aborts`, the one number this dry run exists to produce, was the one thing he could not notice
  # without staring at the screen.
  #
  # Same shape as the chime above: no text, AlertSize.none, so it takes nothing over. A different
  # PITCH is the entire message. Two events that sound alike are one event to a driver looking at
  # the road.
  EventNameSP.passingAssistBackedOut: {
    ET.PERMANENT: Alert(
      "",
      "",
      AlertStatus.normal, AlertSize.none,
      Priority.LOW, VisualAlert.none, AudibleAlertSP.promptSingleLow, 1.),
  },

  EventNameSP.e2eChime: {
    ET.PERMANENT: Alert(
      "",
      "",
      AlertStatus.normal, AlertSize.none,
      Priority.MID, VisualAlert.none, AudibleAlert.prompt, 3.),
  },
}
