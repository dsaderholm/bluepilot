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
  # FusionPilot: openpilot has taken the command away from Ford. Violet, because it must not read as
  # a more-or-less version of the Ford states beside it -- it is a different AUTHOR, not a different
  # amount of braking, and on a car whose whole design is "Ford drives" that distinction is the one
  # worth seeing at a glance.
  # FusionPilot: openpilot is driving and NOBODY ASKED IT TO. Amber, deliberately not violet -- OP
  # STOP is the system doing its job and this is the system falling back to the controller the
  # whole feature exists to avoid. Different meaning, so a different colour family, and warmer than
  # PRE-BRAKE's orange so the two do not read as neighbours on the braking scale.
  "OP LONG": rl.Color(238, 170, 30, 240),
  # and the same red as BRAKE on purpose -- it is the loudest thing in this palette and this is the
  # state that most needs to interrupt what he is doing. Drive A sat in it for 262 s with nothing on
  # screen; there is no recovering it mid-drive, so the only useful response is to know.
  "ACC LOST": rl.Color(232, 58, 48, 240),
}
# THE HOLD BADGE AND ITS PALETTE WERE DELETED ON 2026-08-22, along with the +/- arrow that hung off
# its label and the pin dot in its corner. The box above already shows the hold as the big number
# and tints while the hold owns it, so the badge was drawing a number that was already on screen.
#
# Where each piece went, so none of it has to be rediscovered:
#   the number        the big number in the set-speed box (`max_box_state`, aim)
#   "not yours to     the box stops tinting while `hold_locked` -- what the gray badge said
#    change" state
#   the +/- arrow     DROPPED, and NOT replaced -- the first version of this note claimed rank 1
#                     covered "every moment the arrow would have been drawn", which is not true and
#                     was corrected on review the same day. The arrow came from `sendButton.raw`
#                     (ICBM is holding a button THIS FRAME); rank 1 fires on
#                     `round(dash) != round(aim)` (the car is not at the target). They diverge both
#                     ways: the dash sits a mile off the aim for seconds with no press in flight,
#                     which is Ford's own increment lag, and a press can be in flight on a frame
#                     where the two happen to agree.
#
#                     So what is genuinely lost is "openpilot is working on it right now" as
#                     distinct from "openpilot and the car disagree and nothing is happening" --
#                     the distinction behind the set-speed hunting report on route 00000361. It is
#                     left lost rather than given a corner of the box, because the box has one
#                     number worth reading and the arrow's home was the badge. If the hunting ever
#                     comes back, this is the readout to rebuild first.
#   the pin dot/ring  the same corner of the box, drawn by `HudRendererSP._draw_set_speed`
#   the offered pin   the label slot, rank 3
# Dark ink on the filled ACCEL/BRAKE pills; they are bright enough that white text greys out.
ACC_INK = rl.Color(10, 14, 20, 255)
# States with no magnitude to report: no number, no intensity bar. They are still filled -- the
# color IS the reading for these two.
QUIET_ACC_STATES = ("COAST", "PRE-BRAKE")

# FusionPilot: how many consecutive frames openpilot must be authoring UNASKED before the pill says
# seconds old when it is published, so both show immediately.
#
# The accel-difference threshold that used to live here is gone with the inference it served: the
# authority is published now, so there is nothing left to compare.
OP_AUTHORING_FRAMES = 5     # ~0.25 s at the UI's rate; a real fallback run lasts longer
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

# The pin dot moved to `selfdrive/ui/sunnypilot/onroad/hud_renderer.py` with the rest of the badge
# on 2026-08-22 -- it is drawn on the set-speed box now, in the same corner, at the same size. The
# rule it encodes is unchanged and worth keeping written down: A HOLLOW RING IS A SUGGESTION, A
# FILLED DOT IS A PIN. Same mark, same corner, same tap; the difference is whether the car is
# already doing it or only offering to. Two symbols would have to be learned, one symbol in two
# states reads immediately.

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
    self._acc_state = ""      # "ACCEL" / "COAST" / "BRAKE"
    self._acc_accel = 0.0     # m/s^2, signed
    # FusionPilot: consecutive frames openpilot has been authoring without being asked to. Debounced
    # because scattered non-carriable frames are ordinary -- 8.9% of drive B -- and a pill that
    # through this: one is deliberate and the other is already 5 s old when it is published.
    self._acc_fallback_frames = 0
    self._lamp_data_available = False  # the BCM/brake-system lamp signal is actually being decoded
    self._tsr_fault = ""      # why TSR is not producing a limit; "" when it is working or silent
    self._tsr_limit = ""      # the limit the CAMERA read, when it has one
    # FIVE `_icbm_*` FIELDS LIVED HERE AND ALL FIVE WERE READ ONLY BY THE HOLD BADGE. Deleted with
    # it on 2026-08-22 rather than left assigned: four of them had already become write-only, two
    # were not reset per frame, and the next person to draw a pin state would have reached for
    # last frame's answer. The box resolves all of it now, once, in `max_box_state`.
    self._hold_rect = None      # the set-speed box, while there is a hold or an offer to tap
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
    # FusionPilot: None until controllerStateBP arrives; see _update_state.

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

    # FusionPilot: who is authoring ACCDATA, straight from the controller that decided it. Read
    # every frame -- it changes within a frame and a cached copy would be exactly wrong at the
    # moment it matters. This is a message read, not a param read; the earlier version of this
    sm = ui_state.sm

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
    sm = ui_state.sm

    # THE ICBM READ USED TO LIVE HERE and it is gone, 2026-08-22, with the badge it fed.
    #
    # `HudRendererSP._set_speed_aim` already calls `read_icbm_hud_state` once per frame to resolve
    # the set-speed box, so doing it again here parsed the same two messages a second time for
    # fields nothing draws any more. The one thing still wanted -- whether there is anything to tap
    # -- comes off `self._box` in `_render`, which `_draw_set_speed` has just written.

    # BluePilot: TSR fault reason. Read before the brake-status gate below -- it has nothing to do
    # with brakes and must not disappear when that toggle is off.
    self._tsr_fault = ""
    self._tsr_limit = ""
    if sm.valid['carStateBP']:
      try:
        tsr = sm['carStateBP'].trafficSignData
        # Only when TSR is failing to give a usable limit. 0 and 255 are the DBC's "no limit"
        # values, so a working camera between signs prints nothing either.
        if tsr.dataAvailable and tsr.vLimit1 in (0, 255):
          self._tsr_fault = TSR_MSG_TEXT.get(tsr.tsrMsg) or TSR_STATUS_TEXT.get(tsr.tsrStatus) or ""
        elif tsr.vLimit1 not in (0, 255):
          # BluePilot: and say so when it DOES read one. Until 2026-08-23 nothing on this screen
          # ever confirmed a sign was captured -- the pill drew only faults, so a working read and
          # a dead camera looked identical from the seat. Three reads were sitting in the logs
          # unnoticed for days because of it. The unit is the DBC's: 1 km/h, 2 mph.
          self._tsr_limit = "TSR {}{}".format(int(tsr.vLimit1), "" if tsr.vLimitUnit == 2 else " KPH")
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
      # THE PIN TAP TARGET IS THE SET-SPEED BOX ITSELF, 2026-08-22. The HOLD badge that used to own
      # this rect is gone -- since `max_box_state` landed, the box already shows the hold as the big
      # number and tints while the hold owns it, so the badge was a second drawing of a number
      # already on screen. His call: *"we are just going to use the target speed"*.
      #
      # Set HERE rather than in `_draw_acc_status`, where the badge lived, because that method
      # returns early in several states (lamps-only, nothing to report) and the tap must keep
      # working in all of them. A hold governing the car with no way to pin or unpin it is the
      # defect that killed pinned holds for two days in August.
      # Straight off the box's own resolved state, which `_draw_set_speed` wrote one line ago.
      # `hold_driving` covers a hold worth unpinning; `pin_offer` covers a place worth pinning with
      # no hold yet -- which is exactly the pair the old `display_value` collapsed into one number.
      tappable = bool(self._box.hold_driving or self._box.pin_offer) if self._box else False
      self._hold_rect = self._set_speed_rect if tappable else None
    else:
      self._hold_rect = None   # no box on screen, no tap target
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
    # `_hold_rect` is None unless there is something to pin -- see `_render`. The second condition
    # that used to be here read `_icbm_baseline`, which no longer exists and was saying the same
    # thing twice.
    if (self._hold_rect is not None
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
    if (lamps_only and not self._tsr_fault and not self._tsr_limit
        and not (self._show_brake_status and self._lamp_data_available)):
      return
    # THE HOLD is NOT a reason to draw this stack any more. It was, while the HOLD badge
    # lived here; the hold is now drawn by the set-speed box above and nothing in this column
    # depends on it. Leaving it in the gate would reserve the stack for a readout that no longer
    # exists, and push the ACC pill down for no reason.
    if (not lamps_only and not self._acc_state and not self._tsr_fault and not self._tsr_limit
        and not (self._show_brake_status and self._lamp_data_available)):
      return

    set_speed_width = UI_CONFIG.set_speed_width_metric if ui_state.is_metric else UI_CONFIG.set_speed_width_imperial
    x = rect.x + 60 + (UI_CONFIG.set_speed_width_imperial - set_speed_width) // 2
    y = rect.y + 45 + UI_CONFIG.set_speed_height + 16
    if self._ahead_box_visible():
      y += AHEAD_BOX_HEIGHT + AHEAD_BOX_GAP

    if self._acc_state and not lamps_only:
      y += self._draw_acc_pill(x, y) + STACK_GAP
    # Shown whenever brake status is on, in both states -- an indicator that only appears when lit
    # cannot be told apart from one that is broken, and "are my lamps on right now" is a question
    # about both answers.
    if self._show_brake_status and self._lamp_data_available:
      y += self._draw_brake_lamp_pill(x, y) + STACK_GAP
    self._draw_tsr_pill(x, y)

  def _draw_tsr_pill(self, x: float, y: float) -> int:
    """BluePilot: the camera's own explanation for why there is no speed limit.

    Outlined rather than filled: this is information, not a warning, and it sits in the same column
    as two readouts that go solid red when they mean something urgent.
    """
    label = self._tsr_limit or self._tsr_fault
    if not label:
      return 0
    rect = rl.Rectangle(x, y, LAMP_PILL_WIDTH, LAMP_PILL_HEIGHT)
    rl.draw_rectangle_rounded(rect, 0.5, 10, TSR_PILL_FILL)
    # Filled edge when a sign was actually READ, so a capture is distinguishable at a glance from
    # the fault text that shares this pill.
    rl.draw_rectangle_rounded_lines_ex(rect, 0.5, 10, 6 if self._tsr_limit else 3, TSR_PILL_EDGE)
    width = measure_text_cached(self._font_semi_bold, label, LAMP_LABEL_SIZE).x
    rl.draw_text_ex(self._font_semi_bold, label,
                    rl.Vector2(x + (LAMP_PILL_WIDTH - width) / 2, y + 12), LAMP_LABEL_SIZE, 0,
                    TSR_PILL_INK)
    return LAMP_PILL_HEIGHT


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
