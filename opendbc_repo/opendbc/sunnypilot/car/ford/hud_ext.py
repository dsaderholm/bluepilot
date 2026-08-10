"""
BluePilot Ford HUD/DM/UI control extension.

Handles driver monitoring message computation, LKAS UI, and ACC UI message
generation with BluePilot-specific features:
  - TJA warning/message from selfdrive state (DM-aware)
  - Hands-free cluster messaging for BlueCruise-equipped vehicles
  - ACC distance bar persistence (4-second send window)
  - Independent left/right lane line display
"""

from opendbc.car import structs
from opendbc.car.ford.values import CarControllerParams, FordFlags
from opendbc.sunnypilot.car.ford import fordcan_ext

VisualAlert = structs.CarControl.HUDControl.VisualAlert


def get_dm_state(d_state, main_on):
  """Parse driver monitoring alert type into driver state and disable state.

  Args:
    d_state: Alert type string from selfdriveState (e.g., "preDriverDistracted/softDisable")
    main_on: Whether cruise main is on

  Returns:
    Tuple of (driverState, disableState) strings
  """
  e = d_state.split("/")
  if main_on:
    en = e[0]
    et = e[-1]
  else:
    en = "none"
    et = "none"
  return en, et


def compute_dm_msg_values(ss, hud_control, send_hands_free_cluster_msg, main, standstill=False):
  """Compute TJA message, TJA warning, and hands-off level from driver monitoring state.

  Maps selfdrive state alert types to Ford cluster TJA signals:
    - tja_msg: 0=None, 1=LCA not available
    - tja_warn: 0=None, 1=Cancelled, 3=Resume Control, 4=Right Lane Departure,
                5=Left Lane Departure
    - hands: 0=HandsOn, 1=Level1 (no chime), 2=Level2 (chime)

  Args:
    ss: selfdriveState message (or None)
    hud_control: HUD control with lane departure info
    send_hands_free_cluster_msg: Whether BlueCruise cluster messages are enabled
    main: Whether cruise main is on
    standstill: Whether vehicle is at standstill

  Returns:
    Tuple of (tja_msg, tja_warn, hands)
  """
  tja_msg = 0
  tja_warn = 0
  hands = 0

  if ss:
    driverState, disableState = get_dm_state(ss.alertType, main)
  else:
    driverState, disableState = "none", "none"

  if send_hands_free_cluster_msg:
    if disableState == "noEntry":
      tja_msg = 1  # Lane Centering Assist not available
    elif (driverState in ("driverDistracted", "driverUnresponsive") or
          disableState in ("softDisable", "immediateDisable")):
      tja_warn = 3  # Resume Control
    elif disableState == "userDisable":
      tja_warn = 1  # Cancelled
    elif driverState == "preDriverDistracted":
      hands = 1  # Keep Hands on Steering Wheel (no chime)
    elif driverState == "promptDriverDistracted":
      hands = 2 if not standstill else 1  # chime unless standstill
    elif driverState == "preDriverUnresponsive":
      hands = 1
    elif driverState == "promptDriverUnresponsive":
      hands = 2 if not standstill else 1
    elif hud_control.leftLaneDepart:
      tja_warn = 5  # Left Lane Departure (chime)
    elif hud_control.rightLaneDepart:
      tja_warn = 4  # Right Lane Departure (chime)
    else:
      tja_warn = 0
  else:
    if disableState == "noEntry":
      tja_msg = 1
    elif (driverState in ("driverDistracted", "driverUnresponsive") or
          disableState in ("softDisable", "immediateDisable")):
      tja_warn = 3
    elif disableState == "userDisable":
      tja_warn = 1
    elif driverState in ("preDriverDistracted", "preDriverUnresponsive"):
      hands = 1
    elif driverState in ("promptDriverDistracted", "promptDriverUnresponsive"):
      hands = 2 if not standstill else 1
    else:
      tja_warn = 0

  return tja_msg, tja_warn, hands


class HudExt:
  """
  BluePilot HUD/UI control extension for Ford vehicles.

  Mixed into CarController via multiple inheritance. Manages DM state computation,
  LKAS UI, and ACC UI message generation with BluePilot-specific behavior.
  """

  def __init__(self, CP, CP_SP):
    # DM/HUD state
    self.tja_msg = 0
    self.tja_warn = 0
    self.hands = 0
    self.steer_alert_last = False

    # Feature flags (currently always False — intended for future Params activation)
    self.send_driver_monitor_can_msg = False
    self.send_lane_depart_can_msg = False
    self.send_hands_free_cluster_msg = False

    # UI state tracking
    self.fcw_alert_last = False
    self.send_ui_last = False
    self.send_bars_ts_last = 0
    self.send_bars_last = False
    self.lead_distance_bars_last = None
    self.distance_bar_frame = 0
    self.main_on_last = False
    self.lkas_enabled_last = False
    # BluePilot: the cluster's lane display IS the passing-assist indicator while openpilot
    # steers. See create_lkas_ui_msg -- each line is a four-level meter for its own side.
    self.passing_last = fordcan_ext.CLUSTER_PASSING_IDLE
    self.show_passing_in_cluster = False
    # BluePilot: the standstill lane-display walk's current override, or None.
    self.lane_test_last = None

  def update_hud_params(self, params, CP):
    """Read HUD-related Params from the UI. Called each frame."""
    self.send_hands_free_cluster_msg = params.get_bool("send_hands_free_cluster_msg")
    self.show_passing_in_cluster = params.get_bool("ShowPassingInCluster")
    # Block hands-free UI on CAN vehicles — only CAN FD supports the cluster message
    if not (CP.flags & FordFlags.CANFD):
      self.send_hands_free_cluster_msg = False

  def update_dm(self, hud_control, main_on, standstill, frame):
    """Compute DM state (TJA message, TJA warning, hands-off level).

    Called each frame. Updates self.tja_msg, self.tja_warn, self.hands.

    Args:
      hud_control: HUD control with lane departure info
      main_on: Whether cruise main is on
      standstill: Whether vehicle is at standstill
      frame: Current frame number
    """
    if self.send_driver_monitor_can_msg:
      # Compute from selfdrive state at ACC_UI rate (5Hz)
      if (frame % CarControllerParams.ACC_UI_STEP) == 0:
        self.tja_msg, self.tja_warn, self.hands = compute_dm_msg_values(
          self.ss, hud_control, self.send_hands_free_cluster_msg, main_on, standstill)
    else:
      # Simple steer_alert → hands mapping
      steer_alert = hud_control.visualAlert in (VisualAlert.steerRequired, VisualAlert.ldw)
      self.hands = 1 if steer_alert else 0

  def update_hud(self, CC, CS, hud_control, main_on, fcw_alert, frame, packer, CAN, CP,
                 lane_test=None):
    """Generate LKAS UI and ACC UI CAN messages.

    Called each frame from CarController.update() after DM state is computed.

    Args:
      CC: CarControl
      CS: CarState
      hud_control: HUD control data
      main_on: Whether cruise main is on
      fcw_alert: Forward collision warning active
      frame: Current frame number
      packer: CAN packer
      CAN: Ford CAN bus config
      CP: CarParams
      lane_test: LaneTestOverride from the standstill display walk, or None. See
        lane_display_test_ext -- while a walk runs it owns the lane display outright.

    Returns:
      List of CAN sends for UI messages.
    """
    can_sends = []
    steer_alert = hud_control.visualAlert in (VisualAlert.steerRequired, VisualAlert.ldw)
    standstill = CS.out.cruiseState.standstill

    # BluePilot: everything the cluster's lane display is being asked to say, built here rather
    # than in the message builder so that a planner that is missing, stale or mid-restart costs the
    # cluster nothing. 0 none, 1 left, 2 right throughout -- the Side enum's own ordering, read
    # straight through, so no mapping table can drift.
    #
    # Behind a toggle because it repurposes a safety indicator: the driver decides whether his
    # lane display is also allowed to mean this.
    passing = fordcan_ext.CLUSTER_PASSING_IDLE
    if self.show_passing_in_cluster:
      try:
        pa = self.sm['longitudinalPlanSP'].passingAssist
        # Past deciding and actually going. `waiting` and `confirming` are still deliberating, and
        # a line that went to Intervene while the machine was only thinking about it would promise
        # a lane change that may never come.
        phase = str(pa.maneuver)
        moving = phase in ("signaling", "changing", "finishing", "aborting")
        # A pass is on the table: the machine is past idle, or a suggestion is standing. `waiting`
        # counts and matters most -- that is the state where a gate is what is stopping us, and
        # oncoming is one of the gates.
        # .raw, NOT int(). A capnp enum off a live message is a _DynamicEnum and int() raises
        # TypeError on the device -- which the broad except below turned into a cluster
        # display that silently never worked. Found 2026-08-10 when the same mistake was
        # caught in new code by test_no_int_on_capnp_enums, whose COVERED list did not
        # reach opendbc. It does now.
        in_play = phase != "idle" or getattr(pa.suggestion, 'raw', pa.suggestion) != 0
        passing = fordcan_ext.ClusterPassing(
          suggestion=getattr(pa.suggestion, 'raw', pa.suggestion),
          maneuver_side=getattr(pa.maneuverSide, 'raw', pa.maneuverSide),
          maneuver_moving=moving,
          pass_in_play=in_play,
          oncoming_left=bool(pa.adjacentLeft.oncoming),
          oncoming_right=bool(pa.adjacentRight.oncoming),
        )
      except Exception:  # noqa: BLE001 - a missing planner must never stop the cluster updating
        passing = fordcan_ext.CLUSTER_PASSING_IDLE

    # Determine if UI state changed
    # A walk step must land on the cluster the moment it changes, and the step BEFORE it has to be
    # replaced when the walk ends -- at 1 Hz the last state would linger for up to a second and get
    # written down as the next one.
    lane_test_changed = lane_test != self.lane_test_last

    send_ui = (lane_test_changed or (self.main_on_last != main_on) or
               # ...INCLUDING the passing side, which is what makes this usable: the message is
               # otherwise sent at 1 Hz, and a suggestion that took a second to appear would be a
               # second stale by the time it did.
               (self.passing_last != passing) or
               (self.lkas_enabled_last != CC.latActive) or
               (self.steer_alert_last != steer_alert))

    # LKAS UI msg at 1Hz or if UI state changes
    if (frame % CarControllerParams.LKAS_UI_STEP) == 0 or send_ui:
      can_sends.append(fordcan_ext.create_lkas_ui_msg(
        packer, CAN, main_on, CC.latActive, self.hands, hud_control, CS.lkas_status_stock_values,
        passing, lane_test))
      self.passing_last = passing
      self.lane_test_last = lane_test

    # ACC UI msg at 5Hz or if UI state changes
    send_bars = False
    if hud_control.leadDistanceBars != self.lead_distance_bars_last:
      send_ui = True
      send_bars = True

    # Keep sending bars for 4 seconds (400 frames at 100Hz)
    if not self.send_bars_last and send_bars:
      self.send_bars_ts_last = frame
      self.distance_bar_frame = frame

    if self.send_bars_ts_last > 0 and (frame - self.send_bars_ts_last) <= 400:
      send_ui = True
      send_bars = True

    if (frame % CarControllerParams.ACC_UI_STEP) == 0 or send_ui:
      can_sends.append(fordcan_ext.create_acc_ui_msg(
        packer, CAN, CP, main_on, CC.latActive, fcw_alert, standstill,
        hud_control, CS.acc_tja_status_stock_values,
        self.send_hands_free_cluster_msg, send_ui, send_bars,
        self.tja_warn, self.tja_msg))

    # Update state for next frame
    self.main_on_last = main_on
    self.send_ui_last = send_ui
    self.send_bars_last = send_bars
    self.lkas_enabled_last = CC.latActive
    self.steer_alert_last = steer_alert
    self.fcw_alert_last = fcw_alert
    self.lead_distance_bars_last = hud_control.leadDistanceBars

    return can_sends
