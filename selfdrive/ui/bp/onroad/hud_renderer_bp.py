import pyray as rl
from openpilot.common.params import Params
from opendbc.car.structs import ControllerStateBP
from openpilot.bluepilot.ui.lib.bp_shaders import draw_shader_circle_gradient
from openpilot.selfdrive.ui.onroad.hud_renderer import UI_CONFIG, FONT_SIZES, COLORS
from openpilot.selfdrive.ui.sunnypilot.onroad.hud_renderer import HudRendererSP
from openpilot.selfdrive.ui.bp.onroad.exp_button_bp import ExpButtonBP
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.selfdrive.ui.bp.lib.ui_debug_logger import bp_ui_log
from cereal import custom

LateralMode = ControllerStateBP.LateralMode
SpeedLimitSource = custom.LongitudinalPlanSP.SpeedLimit.Source

# BluePilot: Y center for speed display (matching upstream hardcoded values)
SPEED_CENTER_Y = 180
SPEED_UNIT_CENTER_Y = 290

# BluePilot: below this the propulsion request reads as coasting rather than accelerating. ACC
# trims constantly at small values; with no deadband the readout would never sit still.
ACC_DEADBAND = 0.15  # m/s^2
# BluePilot: AccPrpl_A_Rq's floor is the "no propulsion request" sentinel, not a -5 m/s^2 request.
# opendbc sends INACTIVE_GAS = -5.0 whenever longitudinal is off or the request falls below
# MIN_GAS = -0.5, and fordcan pins AccPrpl_A_Pred at -5.0 outright. Anything at or near the floor
# means "nothing asked for" and must not read as engine braking -- without this the pill would sit
# on ENG BRAKE permanently, which is worse than the COAST it replaced.
ACC_PROPULSION_INACTIVE = -4.5  # m/s^2; at or below this the signal carries no request
# BluePilot: one green-to-red scale, read as "how much is the car slowing". Position on the scale
# is the information, so the four states are ordered rather than merely distinct:
#
#   ACCEL     green   -- adding speed
#   COAST     yellow  -- neither; the resting state, so deliberately the dimmest
#   PRE-BRAKE orange  -- brakes pressurised, still not slowing you
#   BRAKE     red     -- friction brakes in use
#
# COAST is muted rather than a full yellow because it is on screen most of the time and a bright
# resting state trains you to stop looking. The others are vivid: they are the exceptions.
#
# ENG BRAKE sits deliberately OFF that scale, in teal. It is the one state that is both slowing the
# car and costing nothing -- no pads, and below 1.3 m/s^2 no stop lamps either -- which does not
# fit on a single "how hard is it slowing" axis. Ford documents ACC as using transmission downshift
# to slow "without wearing out the brakes", so this is the good outcome and should not be coloured
# like an escalation toward red.
ACC_STATUS_COLORS = {
  "ACCEL": rl.Color(70, 200, 115, 235),
  "COAST": rl.Color(196, 176, 70, 205),
  "ENG BRAKE": rl.Color(55, 185, 195, 235),
  "PRE-BRAKE": rl.Color(245, 145, 35, 235),
  "BRAKE": rl.Color(232, 58, 48, 240),
}
# BluePilot: both readouts used to be 34 px unbacked text under the MAX box, which the owner could
# not pick out at a glance while driving. They are now drawn as filled shapes sized against the
# MAX box next to them -- see scratchpad/hud_preview.py, which renders this corner offline at
# device scale so placement can be judged without a drive.
HOLD_FILL = rl.Color(30, 78, 176, 235)
# BluePilot: while a curve, map point or hazard owns the target, a set-speed press cannot change
# the hold -- it gives a momentary bump the suppressor reclaims within about a second. That is
# deliberate, but it means the press does not do what a press normally does, so the badge goes
# gray to say so. Without it the button silently has no lasting effect and looks broken.
HOLD_LOCKED_FILL = rl.Color(84, 90, 98, 225)
HOLD_LOCKED_EDGE = rl.Color(140, 148, 156, 235)
HOLD_LOCKED_LABEL = rl.Color(178, 186, 194, 255)
HOLD_EDGE = rl.Color(130, 185, 255, 255)
HOLD_LABEL_COLOR = rl.Color(175, 210, 255, 255)
HOLD_HEIGHT = 124
HOLD_LABEL_SIZE = 32
HOLD_VALUE_SIZE = 66
# Dark ink on the filled ACCEL/BRAKE pills; they are bright enough that white text greys out.
ACC_INK = rl.Color(10, 14, 20, 255)
# States with no magnitude to report: no number, no intensity bar. They are still filled -- the
# color IS the reading for these two.
QUIET_ACC_STATES = ("COAST", "PRE-BRAKE")
ACC_PILL_WIDTH = 268   # wider than the MAX column: "BRAKE 1.4" does not fit 172 px legibly
ACC_PILL_HEIGHT = 78
ACC_LABEL_SIZE = 38
ACC_LABEL_MIN_SIZE = 26  # floor for the shrink-to-fit above; below this it stops being legible
ACC_VALUE_SIZE = 34
ACC_MAX_MAG = 2.5      # m/s^2 that fills the intensity bar
STACK_GAP = 12
# BluePilot: the stop lamps themselves, as their own readout rather than only as a speed color.
#
# This is a REAL signal, not an inference: BCM_Lamp_Stat_FD1's StopLghtOn_B_Stat is the body
# control module reporting actual lamp state, with BrakeSysFeatures_2's BrkLamp_B_Rq as fallback.
# Whatever lit them -- the driver's foot, stock ACC, anything -- shows here. The one place that
# reading gets an ACC-derived overlay mixed in is gated on openpilotLongitudinalControl, which is
# false on a car running stock Ford ACC, so on this vehicle it is a pure mirror.
#
# Deliberately distinct from the ACC pill above it. That one says what the system is ASKING for;
# this says what traffic behind you is actually being shown, and light applications below the
# lamp threshold are exactly the case where the two disagree.
#
# DO NOT merge these two, however alike the pills look when both go red. Confirmed with the owner
# on 2026-08-04: he relies on this as a standalone check he can trust. It is the only readout in
# that column that is a MEASURED FACT rather than a request -- StopLghtOn_B_Stat is the body module
# reporting actual lamp state, whatever lit them. Everything above it is something the system wants.
# Folding the two together would trade the one number that needs no interpretation for a tidier
# stack, and he verified this one against the ground behind the car at night before trusting it.
LAMP_PILL_WIDTH = 268
LAMP_PILL_HEIGHT = 56
LAMP_LABEL_SIZE = 32
LAMP_ON_FILL = rl.Color(228, 40, 40, 240)
LAMP_OFF_FILL = rl.Color(0, 0, 0, 150)
LAMP_OFF_EDGE = rl.Color(120, 126, 132, 190)
LAMP_OFF_INK = rl.Color(150, 156, 162, 255)

# BluePilot: why traffic-sign recognition is not producing a limit.
#
# The camera says so itself, and we were already decoding both fields and reading neither. TSR has
# been dead on this car for months with no way to tell whether it is switched off, unsupported in
# this region, faulted, or simply not seeing a sign -- and those want completely different actions.
#
# Values are the DBC's own, verbatim (VAL_ 973 TsrStatMsgTxt_D_Rq / TsrMsgTxt_D_Rq). Entries mapped
# to None mean TSR is working, so the pill does not draw -- this readout deletes itself the moment
# the thing it diagnoses starts working, which is the only honest lifetime for a diagnostic.
TSR_STATUS_TEXT = {
  1: "TSR OFF",             # TSR_Off -- switched off in the vehicle's own menu
  2: None,                  # Available_FusionMode   -- camera + nav, working
  3: None,                  # Available_CameraOnly   -- working
  4: "TSR NAV ONLY",        # Available_NavigationOnly -- camera not contributing
  5: "TSR ERROR",           # TSR_Error
  6: "TSR NO DATA",         # NoDataExists
}
# Checked first when it names a specific cause: TsrStatMsgTxt says whether TSR is up, TsrMsgTxt says
# what is stopping it. "NoInformationAllOK" (1) is not a fault and must not print.
TSR_MSG_TEXT = {
  2: "TSR NAV OFF",         # NoNavAvailableSwitchedOff
  3: "TSR NO NAV DATA",     # NoNavDataAvailable
  4: "TSR NAV DATA BAD",    # WrngNavDatIncompDatCarrier
  5: "TSR COUNTRY N/A",     # CountryNotSupported
  6: "TSR REGION N/A",      # RegionNotSupported
  7: "TSR OFF ROAD",        # OffRoad
  8: "TSR LIMITED",         # LimitedSystemPerformance
  9: "TSR SIGN UNREADABLE",  # RecgnzdSignNotUsblForDsply
}
TSR_PILL_FILL = rl.Color(0, 0, 0, 150)
TSR_PILL_EDGE = rl.Color(196, 176, 70, 205)
TSR_PILL_INK = rl.Color(226, 206, 110, 255)

# BluePilot: a hold pinned to this place re-applies itself on every drive. Marked with a dot in the
# badge's LEFT corner rather than a word: the badge is 172 px wide and already carries a label and a
# two-digit number, and "PIN" competing with "HOLD" reads as two labels for one thing. Left because
# the right corner belongs to the +/- arrow.
# A HOLLOW dot is a suggestion, a filled one is a pin. Same mark, same corner, same tap -- the
# difference is whether the car is already doing it or only offering to. Two symbols would have to
# be learned; one symbol in two states reads immediately.
PIN_DOT_RADIUS = 9
PIN_DOT_COLOR = rl.Color(255, 214, 120, 255)

# BluePilot: radar detector readout. Same width as the ACC and lamp pills so the left column stays
# one column; taller than the lamp pill because it carries an eight-segment bar graph rather than
# a word.
RADAR_PILL_WIDTH = 268
RADAR_PILL_HEIGHT = 56
RADAR_LABEL_SIZE = 32
RADAR_VALUE_SIZE = 40
RADAR_PAD = 16
RADAR_IDLE_FILL = rl.Color(0, 0, 0, 150)
RADAR_IDLE_EDGE = rl.Color(120, 126, 132, 190)
RADAR_NO_LINK_EDGE = rl.Color(196, 176, 70, 205)   # matches the TSR pill: information, not alarm
RADAR_NO_LINK_INK = rl.Color(226, 206, 110, 255)
RADAR_MUTED_INK = rl.Color(150, 156, 162, 255)
# Ka is the only band that moves the car, so it is the only one that gets a loud color. K and X are
# shown because they are what the detector is showing, not because they mean anything here.
RADAR_BAND_FILL = {
  "Ka": rl.Color(228, 132, 24, 240),
  "K": rl.Color(96, 88, 40, 210),
  "X": rl.Color(70, 74, 80, 200),
  "Ku": rl.Color(96, 88, 40, 210),
  # Laser is red and unmissable on purpose. You cannot react to it -- by the time it alerts you
  # have been measured -- so the only thing this readout can usefully do is tell you to mark the
  # spot, which is the one defense that works next time.
  "LASER": rl.Color(228, 40, 40, 245),
}
# The same green as the MAX label: in this corner green already means "openpilot is managing this".
RADAR_ACTING_EDGE = rl.Color(128, 216, 166, 255)

# BluePilot: sunnypilot's "AHEAD" box hangs off the bottom of the speed-limit sign, in the same
# rows our stack occupies. Its geometry, from SpeedLimitRenderer._draw_ahead_info: 170x160 at
# sign_rect.y + sign_rect.height + 10, horizontally centred on the sign.
#
# Our pills are 268 px wide from the left margin and reach x+328; the AHEAD box starts at x+271.
# 57 px of overlap, and it wins because the speed-limit renderer draws after us. Reported from the
# car. Rather than narrow the pills -- they were widened deliberately so "BRAKE 1.4" is legible at
# a glance -- the stack starts below the box whenever the box is there.
AHEAD_BOX_HEIGHT = 160
AHEAD_BOX_GAP = 10


class HudRendererBP(HudRendererSP):
  """BluePilot HudRenderer with brake status display.

  Note: Torque bar is rendered by TorqueBarRendererBP in AugmentedRoadViewBP,
  not here. This keeps the torque bar above gauges in draw order and allows
  repositioning above the battery/power flow gauges.
  """

  def __init__(self):
    super().__init__()
    # BluePilot: Restore the animated C3X wheel without modifying the upstream ExpButton.
    self._exp_button = ExpButtonBP(UI_CONFIG.button_size, UI_CONFIG.wheel_icon_size)
    self._bp_params = Params()
    self._brakes_on = False
    # BluePilot: Ford ACC asking for brakes, which is not the same event as the lamps lighting.
    # Light applications decelerate without ever reaching the stop-lamp threshold.
    self._acc_braking = False
    # BluePilot: what Ford ACC is asking for, and what ICBM is doing about it. The speed colors
    # above say what traffic behind you sees; these say what the systems are requesting. Those are
    # different facts, which is why this is a separate readout rather than more colors.
    self._acc_state = ""      # "ACCEL" / "COAST" / "BRAKE", "" when unknown
    self._acc_accel = 0.0     # m/s^2, signed
    self._icbm_baseline = 0   # the driver's held set speed; 0 = no hold
    self._icbm_arrow = ""     # "+" / "-" while ICBM is actively moving the set speed, else ""
    self._icbm_hold_locked = False  # something else owns the target; a press cannot change the hold
    self._lamp_data_available = False  # the BCM/brake-system lamp signal is actually being decoded
    self._tsr_fault = ""      # why TSR is not producing a limit; "" when it is working or silent
    self._icbm_pinned = False   # this hold came from a pin, so tapping the badge removes it
    self._icbm_pin_suggested = False  # set the same hold here before; tapping accepts
    self._hold_rect = None      # last drawn badge rect; the tap target for pinning
    self._acc_status_failed = False   # latched on any error; keeps a display bug off the screen
    self.speed_right = 0
    self._gradient_rect = None  # BluePilot: Full-width rect for header gradient

    # BluePilot: Cache params to avoid per-frame disk I/O (refresh every ~60 frames)
    self._param_counter = 0
    self._show_brake_status = self._bp_params.get_bool("ShowBrakeStatus")
    self._hide_v_ego_ui = self._bp_params.get_bool("HideVEgoUI")
    self._show_lateral_control = self._bp_params.get_bool("BpShowLateralControl")
    # BluePilot: actual mode from controllerStateBP (None = not published, e.g. non-Ford)
    self._lateral_mode = None

  def set_gradient_rect(self, rect: rl.Rectangle):
    """Set full-width rect for header gradient (when HUD renders offset for confidence ball)."""
    self._gradient_rect = rect

  def get_speed_right(self) -> int:
    return self.speed_right

  def _update_state(self) -> None:
    super()._update_state()

    # BluePilot: Refresh cached params periodically (~1s at 20fps)
    self._param_counter += 1
    if self._param_counter >= 60:
      self._param_counter = 0
      self._show_brake_status = self._bp_params.get_bool("ShowBrakeStatus")
      self._hide_v_ego_ui = self._bp_params.get_bool("HideVEgoUI")
      self._show_lateral_control = self._bp_params.get_bool("BpShowLateralControl")

    if self._show_lateral_control:
      sm = ui_state.sm
      self._lateral_mode = sm['controllerStateBP'].activeLateralMode if sm.alive['controllerStateBP'] else None

    # Check brake status if enabled
    if self._show_brake_status:
      sm = ui_state.sm
      if sm.valid['carStateBP']:
        try:
          car_state_bp = sm['carStateBP']
          brake_light_status = car_state_bp.brakeLightStatus
          self._lamp_data_available = brake_light_status.dataAvailable
          self._brakes_on = brake_light_status.dataAvailable and brake_light_status.brakeLightsOn
          # Decel request only -- precharge produces no deceleration, so colouring the speed for
          # it would claim the car was slowing when it was not.
          self._acc_braking = (brake_light_status.accDataAvailable and
                               brake_light_status.accDecelRequest)
        except (KeyError, AttributeError):
          self._lamp_data_available = False
          self._brakes_on = False
          self._acc_braking = False
      else:
        self._lamp_data_available = False
        self._brakes_on = False
        self._acc_braking = False
    else:
      self._lamp_data_available = False
      self._brakes_on = False
      self._acc_braking = False

    # BluePilot: a cosmetic readout must never be able to take the screen down. This one did --
    # int() on a capnp _DynamicEnum raised TypeError inside _update_state, which crash-looped the
    # UI, and because it only runs when cruise is available it only happened with the car on.
    # Anything unexpected here now disables the readout for the session instead of the display.
    if not self._acc_status_failed:
      try:
        self._update_acc_status()
      except Exception as e:
        self._acc_status_failed = True
        self._acc_state, self._acc_accel = "", 0.0
        self._icbm_baseline, self._icbm_arrow = 0, ""
        bp_ui_log.state("HudRendererBP", "acc_status_error", repr(e))

    bp_ui_log.state("HudRendererBP", "brakes_on", self._brakes_on)
    bp_ui_log.state("HudRendererBP", "acc_braking", self._acc_braking)
    bp_ui_log.state("HudRendererBP", "acc_state", self._acc_state)

  def _update_acc_status(self) -> None:
    """BluePilot: is Ford ACC accelerating, coasting or braking, and is ICBM moving the set speed?

    Read from the stock ACCDATA the camera sends, so this is Ford's own request even though
    openpilot is not the longitudinal controller.
    """
    self._acc_state, self._acc_accel = "", 0.0
    self._icbm_baseline, self._icbm_arrow = 0, ""
    # Reset with the rest. Today it cannot be read stale -- the badge only draws when
    # _icbm_baseline is non-zero, and both are written together below -- but leaving one field of
    # the group holding last frame's value is a trap for whoever next draws the lock state.
    self._icbm_hold_locked = False
    sm = ui_state.sm

    # BluePilot: the ICBM line is NOT gated on the brake-status toggle. Whether ICBM is holding
    # the driver's own set speed or chasing Speed Limit Assist is basic state, not a debug
    # readout -- and hiding it behind an unrelated toggle meant the driver spent days unable to
    # see whether an override had taken at all. The ACC accel/coast/brake line below stays behind
    # the toggle; that one really is diagnostic.
    try:
      icbm = sm['selfdriveStateSP'].intelligentCruiseButtonManagement
      self._icbm_arrow = {1: "+", 2: "-"}.get(icbm.sendButton.raw, "")
      if icbm.overrideState.raw == 1 and icbm.vBaseline > 0:
        self._icbm_baseline = round(icbm.vBaseline)
        self._icbm_hold_locked = bool(icbm.holdSuppressed)
        self._icbm_pinned = icbm.baselineSource.raw == 4  # BaselineSource.pinned
        self._icbm_pin_suggested = icbm.pinSuggestion > 0
    except Exception:
      pass

    # BluePilot: TSR fault reason. Read before the brake-status gate below -- it has nothing to do
    # with brakes and must not disappear when that toggle is off.
    self._tsr_fault = ""
    if sm.valid['carStateBP']:
      try:
        tsr = sm['carStateBP'].trafficSignData
        # Only when TSR is failing to give a usable limit. 0 and 255 are the DBC's "no limit"
        # values, so a working camera between signs prints nothing either.
        if tsr.dataAvailable and tsr.vLimit1 in (0, 255):
          self._tsr_fault = TSR_MSG_TEXT.get(tsr.tsrMsg) or TSR_STATUS_TEXT.get(tsr.tsrStatus) or ""
      except Exception:
        pass

    if not self._show_brake_status:
      return

    if sm.valid['carStateBP']:
      try:
        bls = sm['carStateBP'].brakeLightStatus
        # BluePilot: ACCDATA is broadcast by the camera whether or not ACC is engaged, and its
        # request fields do not zero when it is off -- so the pill was reporting BRAKE with cruise
        # not even running. Reported from the road. The readout describes what ACC is DOING, which
        # is meaningless unless ACC is actually driving.
        cruise_on = ui_state.sm['carState'].cruiseState.enabled
        if bls.accDataAvailable and cruise_on:
          # The friction-brake bits win outright: they mean the pads are being used, whatever the
          # propulsion request says. Otherwise the two m/s^2 requests decide between them.
          # accAccelRequest is AccBrkTot_A_Rq -- the BRAKE total, despite the name -- so it cannot
          # tell accelerating from coasting on its own. That is what accPropulsionRequest is for.
          # BluePilot: precharge is NOT braking and must not read as it. It pressurises the
          # system so a later application arrives without slack -- no meaningful deceleration,
          # no stop lamps, and no pad wear worth the name. Counting it as BRAKE made the readout
          # overstate how often the friction brakes were doing anything, which is the one number
          # worth trusting when the goal is to use the pads as little as possible.
          #
          # It still gets its own state rather than folding into COAST: ACC precharging means it
          # is expecting to brake shortly, which is worth seeing coming.
          if bls.accDecelRequest:
            self._acc_state, self._acc_accel = "BRAKE", bls.accAccelRequest
          elif bls.accPropulsionRequest > ACC_DEADBAND:
            self._acc_state, self._acc_accel = "ACCEL", bls.accPropulsionRequest
          # BluePilot: NEGATIVE propulsion is the powertrain being asked to slow the car -- closed
          # throttle and a downshift, no friction brakes. AccPrpl_A_Rq runs [-5 | 5.23] m/s^2 and
          # goes to the PCM, while AccBrkTot_A_Rq goes to ABS_ESC; two channels, two modules.
          #
          # This case previously fell through to COAST, so engine braking -- the one way the car
          # slows at zero cost in pads or stop lamps -- was invisible.
          #
          # Whether stock ACC actually uses this channel is UNVERIFIED. Ford documents ACC as
          # downshifting to slow "without wearing out the brakes", but fordcan.py notes the stock
          # system appears to put positives here and negatives in AccBrkTot, which would mean the
          # PCM decides to downshift on its own and this signal never goes negative. Reading it is
          # how that gets settled: if ENG BRAKE never appears on a descent or a curve, it does not
          # use this channel. Checked after accDecelRequest, so anything touching the pads is
          # BRAKE regardless.
          elif ACC_PROPULSION_INACTIVE < bls.accPropulsionRequest < -ACC_DEADBAND:
            self._acc_state, self._acc_accel = "ENG BRAKE", bls.accPropulsionRequest
          elif bls.accAccelRequest < -ACC_DEADBAND:
            self._acc_state, self._acc_accel = "BRAKE", bls.accAccelRequest
          elif bls.accPrechargeRequest:
            self._acc_state, self._acc_accel = "PRE-BRAKE", 0.0
          else:
            self._acc_state, self._acc_accel = "COAST", 0.0
      except Exception:
        pass


  def _render(self, rect: rl.Rectangle) -> None:
    # BluePilot: Draw header gradient at full content width (not offset by confidence ball)
    gradient_rect = self._gradient_rect if self._gradient_rect else rect
    rl.draw_rectangle_gradient_v(
      int(gradient_rect.x), int(gradient_rect.y), int(gradient_rect.width),
      UI_CONFIG.header_height,
      COLORS.HEADER_GRADIENT_START, COLORS.HEADER_GRADIENT_END,
    )

    # HUD elements use the (possibly offset) rect for positioning
    if self.is_cruise_available:
      self._draw_set_speed(rect)
    # BluePilot: the ACC readouts describe what ACC is doing, so they follow cruise availability.
    # The brake lamps do not -- they are a fact about the car regardless of what is driving it, and
    # the owner asked for them visible whenever the setting is on. Drawn outside that gate, and
    # positioned by the same stack so it lands where the ACC pill would have been when there is no
    # ACC pill to sit under.
    self._draw_acc_status(rect)
    self._draw_current_speed(rect)

    button_x = rect.x + rect.width - UI_CONFIG.border_size - UI_CONFIG.button_size
    button_y = rect.y + UI_CONFIG.border_size
    self._exp_button.render(rl.Rectangle(button_x, button_y, UI_CONFIG.button_size, UI_CONFIG.button_size))
    self._draw_lateral_control_overlay(
      button_x + UI_CONFIG.button_size / 2,
      button_y + UI_CONFIG.button_size / 2,
      UI_CONFIG.button_size,
    )

    # SP additions (dev UI, road name, speed limit, SCC, turn signals, circular alerts, rocket fuel)
    self.developer_ui.render(rect)
    self.road_name_renderer.render(rect)
    self.speed_limit_renderer.render(rect)
    self.smart_cruise_control_renderer.render(rect)
    self.turn_signal_controller.render(rect)
    self.circular_alerts_renderer.render(rect)
    self.rocket_fuel.render(rect, ui_state.sm)

  def _handle_mouse_release(self, mouse_pos) -> None:
    """BluePilot: tapping the HOLD badge pins this hold to this place, or unpins it.

    The badge is the tap target because it is already the thing on screen that means "hold", and
    because the cruise buttons are full -- every one of them carries a settled meaning the owner
    learned once, and adding a gesture would mean relearning one to gain a rare action.

    Only a request is raised here. selfdrived does the work, because that is where the GPS fix and
    the live baseline both are; the UI has neither and should not grow a second copy of either.
    """
    # THE BADGE TAP IS CONSUMED BEFORE THE PARENT SEES IT. This used to call super() first, so every
    # tap reached upstream's handler and opened the sidebar -- including taps on the badge. Reported
    # 2026-08-12: "tapping a hold does nothing, if you tap the screen it just opens the menu on the
    # left." The pin request was still being raised underneath, but the sidebar sliding out is what
    # the driver sees, so the gesture read as dead and the feedback was hidden behind the menu.
    #
    # Checking our own target first and returning is what makes it a real button rather than a
    # side effect of a tap that also does something else.
    if (self._hold_rect is not None and self._icbm_baseline
        and rl.check_collision_point_rec(mouse_pos, self._hold_rect)):
      try:
        self._bp_params.put_bool("IcbmPinHoldRequest", True)
      except Exception:
        pass
      return

    super()._handle_mouse_release(mouse_pos)

  def _ahead_box_visible(self) -> bool:
    """Is sunnypilot's AHEAD box on screen, so our stack has to start below it?

    The condition is read off the speed-limit renderer we already own a reference to, mirroring
    SpeedLimitRenderer._draw_ahead_info. Duplicated rather than shared because that file is
    upstream's and editing it buys a merge conflict on every future update for a layout question
    that is entirely ours. If the box ever stops appearing where we expect, this is the first thing
    to re-check against that method.
    """
    try:
      slr = self.speed_limit_renderer
      return bool(slr.speed_limit_ahead_valid
                  and slr.speed_limit_ahead > 0
                  and slr.speed_limit_ahead != slr.speed_limit
                  and slr.speed_limit_source == SpeedLimitSource.map)
    except Exception:
      return False

  def _draw_acc_status(self, rect: rl.Rectangle) -> None:
    """BluePilot: a compact line under the MAX box -- what ACC is asking for, what ICBM is doing.

    Placed here rather than as another icon because the two renderers that already exist say WHY
    the target moved (SmartCruiseControl shows a curve, SpeedLimit shows the sign). Neither says
    what the car is doing about it, and nothing at all showed ICBM's state.
    """
    lamps_only = not self.is_cruise_available
    if self._acc_status_failed:
      return
    # The TSR fault line is its own reason to draw. It reports a camera that is not working, which
    # is true whether or not cruise is engaged and has nothing to do with the brake-status toggle.
    if lamps_only and not self._tsr_fault and not (self._show_brake_status and self._lamp_data_available):
      return
    if (not lamps_only and not self._acc_state and not self._icbm_baseline and not self._tsr_fault
        and not (self._show_brake_status and self._lamp_data_available)):
      return

    set_speed_width = UI_CONFIG.set_speed_width_metric if ui_state.is_metric else UI_CONFIG.set_speed_width_imperial
    x = rect.x + 60 + (UI_CONFIG.set_speed_width_imperial - set_speed_width) // 2
    y = rect.y + 45 + UI_CONFIG.set_speed_height + 16
    if self._ahead_box_visible():
      y += AHEAD_BOX_HEIGHT + AHEAD_BOX_GAP

    if self._icbm_baseline and not lamps_only:
      y += self._draw_hold_badge(x, y, set_speed_width) + STACK_GAP
    else:
      self._hold_rect = None   # no badge on screen, no tap target
    if self._acc_state and not lamps_only:
      y += self._draw_acc_pill(x, y) + STACK_GAP
    # Shown whenever brake status is on, in both states -- an indicator that only appears when lit
    # cannot be told apart from one that is broken, and "are my lamps on right now" is a question
    # about both answers.
    if self._show_brake_status and self._lamp_data_available:
      y += self._draw_brake_lamp_pill(x, y) + STACK_GAP
    self._draw_tsr_pill(x, y)

  def _draw_hold_badge(self, x: float, y: float, width: float) -> int:
    """BluePilot: the driver's own number, drawn as a sibling of the MAX box.

    Same width, same label-over-number structure, so it reads as "the other set speed" rather than
    as a caption.

    Distinct from BOTH numbers the MAX box can show, which is worth being precise about because
    all three are speeds and two of them often agree:

      big number      carState.vCruiseCluster -- openpilot's OWN v_cruise. With ICBM
                      (pcmCruiseSpeed False) VCruiseHelper maintains this from button presses
                      using openpilot's increments, NOT the car's.
      small number    carState.cruiseState.speedCluster -- the car's real dash set speed, shown
                      in place of the word MAX by HudRendererSP._get_icbm_status whenever the two
                      disagree, latched ~3 s.
      this badge      the ICBM baseline -- the number ICBM returns the set speed to once a curve
                      or hazard has passed. Persistent for as long as the override is held, where
                      the small number is transient.

    Geometry is safe against the small number: that one is drawn inside the box (y + 15) and this
    starts below it (y + set_speed_height + 16).
    """
    locked = self._icbm_hold_locked
    rect = rl.Rectangle(x, y, width, HOLD_HEIGHT)
    rl.draw_rectangle_rounded(rect, 0.32, 10, HOLD_LOCKED_FILL if locked else HOLD_FILL)
    rl.draw_rectangle_rounded_lines_ex(rect, 0.32, 10, 6,
                                       HOLD_LOCKED_EDGE if locked else HOLD_EDGE)

    center_x = x + width / 2
    label_width = measure_text_cached(self._font_semi_bold, "HOLD", HOLD_LABEL_SIZE).x
    # The label stays centred on its own and the arrow hangs off its right, so the word does not
    # shift position every time ICBM starts or stops adjusting.
    rl.draw_text_ex(self._font_semi_bold, "HOLD",
                    rl.Vector2(center_x - label_width / 2, y + 12), HOLD_LABEL_SIZE, 0,
                    HOLD_LOCKED_LABEL if locked else HOLD_LABEL_COLOR)
    if self._icbm_arrow:
      self._draw_arrow(center_x + label_width / 2 + 20, y + 29, 24, self._icbm_arrow == "+")

    value = str(self._icbm_baseline)
    value_width = measure_text_cached(self._font_bold, value, HOLD_VALUE_SIZE).x
    rl.draw_text_ex(self._font_bold, value, rl.Vector2(center_x - value_width / 2, y + 46),
                    HOLD_VALUE_SIZE, 0, COLORS.WHITE)
    # Remember where the badge landed: this is the tap target for pinning, and the geometry above
    # is the only place that knows it.
    self._hold_rect = rl.Rectangle(x, y, width, HOLD_HEIGHT)
    # LEFT of the label, not right. The right corner is where the +/- arrow hangs off the label,
    # and the two landed within a pixel of each other -- arrow centre x+151, dot centre x+152 --
    # so a hold that was both pinned and being adjusted drew them on top of one another. Found by
    # rendering every readout at once rather than one state at a time; the individual scenes each
    # looked fine.
    if self._icbm_pinned:
      rl.draw_circle(int(x + 20), int(y + 20), PIN_DOT_RADIUS, PIN_DOT_COLOR)
    elif self._icbm_pin_suggested:
      # A ring, not draw_circle_lines -- that is a single hairline and it disappeared against the
      # badge fill at a glance, which for a mark whose whole job is to be noticed is no mark at all.
      rl.draw_ring(rl.Vector2(x + 20, y + 20), PIN_DOT_RADIUS - 3, PIN_DOT_RADIUS, 0, 360, 24,
                   PIN_DOT_COLOR)
    return HOLD_HEIGHT

  def _draw_tsr_pill(self, x: float, y: float) -> int:
    """BluePilot: the camera's own explanation for why there is no speed limit.

    Outlined rather than filled: this is information, not a warning, and it sits in the same column
    as two readouts that go solid red when they mean something urgent.
    """
    if not self._tsr_fault:
      return 0
    rect = rl.Rectangle(x, y, LAMP_PILL_WIDTH, LAMP_PILL_HEIGHT)
    rl.draw_rectangle_rounded(rect, 0.5, 10, TSR_PILL_FILL)
    rl.draw_rectangle_rounded_lines_ex(rect, 0.5, 10, 3, TSR_PILL_EDGE)
    width = measure_text_cached(self._font_semi_bold, self._tsr_fault, LAMP_LABEL_SIZE).x
    rl.draw_text_ex(self._font_semi_bold, self._tsr_fault,
                    rl.Vector2(x + (LAMP_PILL_WIDTH - width) / 2, y + 12), LAMP_LABEL_SIZE, 0,
                    TSR_PILL_INK)
    return LAMP_PILL_HEIGHT

  @staticmethod
  def _draw_arrow(center_x: float, center_y: float, size: float, up: bool) -> None:
    """Drawn rather than typed: the device loads bitmap .fnt fonts and an arrow glyph is not
    guaranteed to be baked into them, whereas a triangle always renders."""
    half = size / 2
    if up:
      a = rl.Vector2(center_x, center_y - half)
      b = rl.Vector2(center_x - half, center_y + half)
      c = rl.Vector2(center_x + half, center_y + half)
    else:
      a = rl.Vector2(center_x, center_y + half)
      b = rl.Vector2(center_x + half, center_y - half)
      c = rl.Vector2(center_x - half, center_y - half)
    rl.draw_triangle(a, b, c, COLORS.WHITE)

  def _draw_brake_lamp_pill(self, x: float, y: float) -> int:
    """BluePilot: are the stop lamps lit, right now. See LAMP_* for why this is its own readout."""
    rect = rl.Rectangle(x, y, LAMP_PILL_WIDTH, LAMP_PILL_HEIGHT)
    if self._brakes_on:
      rl.draw_rectangle_rounded(rect, 0.5, 10, LAMP_ON_FILL)
      ink = COLORS.WHITE
    else:
      rl.draw_rectangle_rounded(rect, 0.5, 10, LAMP_OFF_FILL)
      rl.draw_rectangle_rounded_lines_ex(rect, 0.5, 10, 4, LAMP_OFF_EDGE)
      ink = LAMP_OFF_INK

    label = "BRAKE LAMPS"
    width = measure_text_cached(self._font_bold, label, LAMP_LABEL_SIZE).x
    rl.draw_text_ex(self._font_bold, label,
                    rl.Vector2(x + (LAMP_PILL_WIDTH - width) / 2, y + 12), LAMP_LABEL_SIZE, 0, ink)
    return LAMP_PILL_HEIGHT

  def _draw_acc_pill(self, x: float, y: float) -> int:
    """BluePilot: what Ford ACC is asking for, and how hard."""
    rect = rl.Rectangle(x, y, ACC_PILL_WIDTH, ACC_PILL_HEIGHT)

    # All four states are filled: the color is the reading, and its position on the green-to-red
    # scale is what makes the pill glanceable. COAST is muted in ACC_STATUS_COLORS rather than
    # given a different treatment here, so the scale stays continuous.
    rl.draw_rectangle_rounded(rect, 0.42, 10, ACC_STATUS_COLORS.get(self._acc_state, COLORS.WHITE))
    ink = ACC_INK

    show_value = self._acc_state not in QUIET_ACC_STATES
    value = f"{abs(self._acc_accel):.1f}" if show_value else ""
    value_width = measure_text_cached(self._font_semi_bold, value, ACC_VALUE_SIZE).x if value else 0.0

    # Shrink the label to whatever room the value leaves, rather than trusting every state name to
    # fit at one size. "ENG BRAKE 0.9" does not, and hard-coding a shorter word for that one state
    # just moves the problem to the next state added.
    available = ACC_PILL_WIDTH - 44 - (value_width + 14 if value else 0)
    label_size = ACC_LABEL_SIZE
    while (label_size > ACC_LABEL_MIN_SIZE
           and measure_text_cached(self._font_bold, self._acc_state, label_size).x > available):
      label_size -= 2
    # Keep the baseline steady as the size changes, so the row does not jump between states.
    label_y = y + 16 + (ACC_LABEL_SIZE - label_size) * 0.5
    rl.draw_text_ex(self._font_bold, self._acc_state, rl.Vector2(x + 22, label_y),
                    label_size, 0, ink)

    if show_value:
      rl.draw_text_ex(self._font_semi_bold, value,
                      rl.Vector2(x + ACC_PILL_WIDTH - 22 - value_width, y + 20),
                      ACC_VALUE_SIZE, 0, ink)
      # Intensity as its own bar rather than as a fill behind the text: clipping a rounded rect
      # leaves a hard vertical seam that reads as a rendering fault, and it forced the ink color
      # to change halfway across the pill.
      bar_width, bar_height = ACC_PILL_WIDTH - 44, 7
      bar_x, bar_y = x + 22, y + ACC_PILL_HEIGHT - 16
      rl.draw_rectangle_rounded(rl.Rectangle(bar_x, bar_y, bar_width, bar_height), 1.0, 6,
                                rl.Color(0, 0, 0, 70))
      frac = min(1.0, abs(self._acc_accel) / ACC_MAX_MAG)
      rl.draw_rectangle_rounded(
        rl.Rectangle(bar_x, bar_y, max(bar_height, bar_width * frac), bar_height), 1.0, 6, ACC_INK)
    return ACC_PILL_HEIGHT

  def _draw_radar_pill(self, x: float, y: float) -> int:
    """BluePilot: the radar detector, as a sibling of the ACC and lamp pills.

    Deliberately a MIRROR of what is on the windshield rather than an interpretation of it. Band on
    the left, direction in the middle, and the same eight-segment strength bar the detector itself
    shows -- because the strength threshold is a setting the driver has to be able to check against
    the device, and a readout that invented its own scale would make that impossible.

    ALWAYS DRAWN WHILE THE FEATURE IS ON, including with no alert and including with the link down.
    Two reasons, and the second is the one that matters:

      - It is the tap target for marking a place. Marking has to work when NOTHING is alerting --
        the case that actually matters is seeing a cruiser parked with nothing transmitting, or an
        officer with a lidar gun, which the detector will usually miss entirely. A control that
        appears only during an alert cannot mark the thing you most want marked.
      - A dead link has to LOOK dead. If this vanished when the link dropped, a disconnected
        detector would be indistinguishable from a quiet road, which is the single worst failure
        this readout can have.

    The acting edge is the same green as the MAX label, and that is not decoration: green in this
    corner already means "openpilot is managing this number", so an alert that is moving the set
    speed borrows the meaning rather than inventing a new one.
    """
    if not self._radar_enabled:
      return 0

    rect = rl.Rectangle(x, y, RADAR_PILL_WIDTH, RADAR_PILL_HEIGHT)
    band = "" if self._radar_muted else self._radar_band
    fill = RADAR_BAND_FILL.get(band, RADAR_IDLE_FILL)
    if not self._radar_link:
      fill = RADAR_IDLE_FILL

    rl.draw_rectangle_rounded(rect, 0.42, 10, fill)
    # The edge carries the state that the fill cannot: acting outranks alerting, because "this is
    # changing your speed right now" is a different fact from "there is something out there".
    if self._radar_acting:
      edge = RADAR_ACTING_EDGE
    elif not self._radar_link:
      edge = RADAR_NO_LINK_EDGE
    else:
      edge = RADAR_IDLE_EDGE
    rl.draw_rectangle_rounded_lines_ex(rect, 0.42, 10, 6, edge)

    if not self._radar_link:
      # No bars, no band, no direction. Showing an empty eight-segment graph here would read as
      # "eight bars of nothing detected", which is exactly the lie this branch exists to avoid.
      rl.draw_text_ex(self._font_semi_bold, "NO LINK", rl.Vector2(x + 20, y + 16),
                      RADAR_LABEL_SIZE, 0, RADAR_NO_LINK_INK)
      self._radar_rect = rect
      return RADAR_PILL_HEIGHT

    # Laser confirms; it does not ask.
    #
    # This said MARK IT for one iteration, which was wrong twice over. Laser is almost never a false
    # alarm, so there is nothing for the driver to decide -- and it is the one alert you cannot
    # react to, because by the time it fires you have already been measured. Prompting for a tap
    # therefore asks for a distraction at the worst possible moment in exchange for a judgment call
    # nobody needs to make. The mark happens on its own and this says so.
    #
    # "MARKED" rather than "LASER" for the same reason nothing else here mirrors the detector: the
    # V1 is already shouting LASER a few inches away. That it has been RECORDED is the only part of
    # this the detector cannot tell him.
    if band == "LASER":
      # Centred inline rather than through a helper: the preview tool builds its namespace from
      # this class's methods and this file's top-level assignments, so a module-level helper would
      # not survive extraction and the preview would diverge from the car.
      prompt_w = measure_text_cached(self._font_bold, "MARKED", RADAR_VALUE_SIZE).x
      rl.draw_text_ex(self._font_bold, "MARKED",
                      rl.Vector2(x + (RADAR_PILL_WIDTH - prompt_w) / 2, y + 8),
                      RADAR_VALUE_SIZE, 0, COLORS.WHITE)
      self._radar_rect = rect
      return RADAR_PILL_HEIGHT

    ink = RADAR_MUTED_INK if self._radar_muted else COLORS.WHITE
    rl.draw_text_ex(self._font_semi_bold, "RADAR", rl.Vector2(x + 20, y + 14),
                    RADAR_LABEL_SIZE, 0, ink)

    # NO NUMBER HERE, deliberately.
    #
    # It had one -- the set speed the alert was asking for -- and rendering the loaded scene killed
    # it: while this is acting, that speed IS the MAX box two rows up, so the column showed 54 at
    # the top and 54 at the bottom. The same fact twice is not a readout, it is noise in the one
    # place noise costs the most.
    #
    # What is left is exactly what the detector cannot say and the MAX box cannot say: the fill
    # means "your detector is alerting", the green edge means "openpilot is acting on it", and the
    # dot means "this place is already marked". Band, strength, direction and bogey count are all on
    # the windshield a few inches away, brighter and bigger.

    # Right-hand side, vertically centred -- NOT the top-left corner the HOLD badge uses. That badge
    # is 124 px tall so its dot clears the label; this pill is 56 and the dot landed on top of the
    # R in RADAR. Same class of collision as the pin dot and the +/- arrow, found the same way, by
    # rendering every readout at once rather than one state at a time.
    if self._radar_marked:
      rl.draw_circle(int(x + RADAR_PILL_WIDTH - RADAR_PAD - PIN_DOT_RADIUS),
                     int(y + RADAR_PILL_HEIGHT / 2), PIN_DOT_RADIUS, PIN_DOT_COLOR)

    # The tap target for marking. Recorded here because this is the only place that knows where the
    # pill landed -- the same arrangement as _hold_rect.
    self._radar_rect = rect
    return RADAR_PILL_HEIGHT

  def _draw_lateral_control_overlay(self, center_x: float, center_y: float, wheel_size: int) -> None:
    """Draw the current lateral control mode over the steering wheel icon."""
    if not self._show_lateral_control or self._lateral_mode is None:
      return

    text_size = int(wheel_size * 0.4)
    if self._lateral_mode == LateralMode.angle:
      letter, color = "A", rl.Color(50, 100, 255, 220)
    elif self._lateral_mode == LateralMode.curvature:
      letter, color = "C", rl.Color(255, 165, 0, 220)
    else:
      letter, color = "OP", rl.Color(100, 100, 100, 220)

    text_dims = measure_text_cached(self._font_bold, letter, text_size)
    text_pos = rl.Vector2(center_x - text_dims.x / 2, center_y - text_dims.y / 2)

    top = rl.Color(250, 250, 250, 200)
    bottom = rl.Color(200, 200, 200, 200)
    draw_shader_circle_gradient(center_x, center_y, text_size / 2, top, bottom)
    rl.draw_text_ex(self._font_bold, letter, text_pos, text_size, 0, color)

  def _draw_current_speed(self, rect: rl.Rectangle) -> None:
    """Override to add brake status red coloring and track speed_right."""
    # BluePilot: Respect "Speedometer: Hide from Onroad Screen" (HideVEgoUI) from Visuals.
    if self._hide_v_ego_ui:
      self.speed_right = 0
      return
    speed_text = str(round(self.speed))
    speed_text_size = measure_text_cached(self._font_bold, speed_text, FONT_SIZES.current_speed)
    speed_pos = rl.Vector2(
      rect.x + rect.width / 2 - speed_text_size.x / 2,
      SPEED_CENTER_Y - speed_text_size.y / 2
    )
    self.speed_right = speed_pos.x + speed_text_size.x

    # BluePilot: color the speed by what the brakes are doing, if brake status is enabled.
    #   red   -> stop lamps are lit: traffic behind you is being told you are slowing
    #   amber -> ACC is asking for brakes but the lamps have not lit, i.e. a light application
    #            below the stop-lamp threshold. This is the "slowed without anyone noticing"
    #            case, and the one worth tuning IcbmMaxTargetDrop against.
    #   white -> no braking of either kind
    if self._brakes_on:
      speed_color = rl.Color(255, 60, 60, 255)
    elif self._acc_braking:
      speed_color = rl.Color(255, 180, 40, 255)
    else:
      speed_color = COLORS.WHITE
    rl.draw_text_ex(self._font_bold, speed_text, speed_pos, FONT_SIZES.current_speed, 0, speed_color)

    unit_text = "km/h" if ui_state.is_metric else "mph"
    unit_text_size = measure_text_cached(self._font_medium, unit_text, FONT_SIZES.speed_unit)
    unit_pos = rl.Vector2(rect.x + rect.width / 2 - unit_text_size.x / 2, SPEED_UNIT_CENTER_Y - unit_text_size.y / 2)
    # Draw drop shadow for readability over camera feed
    shadow_offset = 2
    shadow_pos = rl.Vector2(unit_pos.x + shadow_offset, unit_pos.y + shadow_offset)
    rl.draw_text_ex(self._font_medium, unit_text, shadow_pos, FONT_SIZES.speed_unit, 0, rl.Color(0, 0, 0, 150))
    rl.draw_text_ex(self._font_medium, unit_text, unit_pos, FONT_SIZES.speed_unit, 0, COLORS.WHITE_TRANSLUCENT)
