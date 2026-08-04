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

LateralMode = ControllerStateBP.LateralMode

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
# grey to say so. Without it the button silently has no lasting effect and looks broken.
HOLD_LOCKED_FILL = rl.Color(84, 90, 98, 225)
HOLD_LOCKED_EDGE = rl.Color(140, 148, 156, 235)
HOLD_LOCKED_LABEL = rl.Color(178, 186, 194, 255)
# BluePilot: TEMPORARY diagnostic. ICBM has two independent ways to notice the driver moved the set
# speed -- a real ButtonEvent, and a fallback that infers it from the set speed moving on its own.
# Every drive so far suggests only the fallback ever fires on this car. Rather than read a route to
# find out, the badge carries a one-letter tag so the owner can just say which appeared:
#   P = press path (the primary one)     I = fallback, ICBM was idle
#   C = fallback, set speed moved against ICBM's own command
# If P never shows up, the press path and its hand-picked settle timers -- the source of most of
# this feature's defects -- get deleted. DELETE THIS AND baselineSource once that is answered.
# Keyed on the RAW integer, like overrideState and sendButton above. A capnp enum does not
# stringify to its name here -- str() gives "1" -- so a name-keyed map silently never matches and
# the tag never appears, which reads as "the press path never fired". That is a false answer to the
# exact question this field exists to settle, and a test caught it rather than a drive.
BASELINE_SOURCE_TAG = {1: "P", 2: "I", 3: "C"}
HOLD_TAG_SIZE = 26
HOLD_TAG_COLOR = rl.Color(255, 214, 120, 255)
HOLD_EDGE = rl.Color(130, 185, 255, 255)
HOLD_LABEL_COLOR = rl.Color(175, 210, 255, 255)
HOLD_HEIGHT = 124
HOLD_LABEL_SIZE = 32
HOLD_VALUE_SIZE = 66
# Dark ink on the filled ACCEL/BRAKE pills; they are bright enough that white text greys out.
ACC_INK = rl.Color(10, 14, 20, 255)
# States with no magnitude to report: no number, no intensity bar. They are still filled -- the
# colour IS the reading for these two.
QUIET_ACC_STATES = ("COAST", "PRE-BRAKE")
ACC_PILL_WIDTH = 268   # wider than the MAX column: "BRAKE 1.4" does not fit 172 px legibly
ACC_PILL_HEIGHT = 78
ACC_LABEL_SIZE = 38
ACC_LABEL_MIN_SIZE = 26  # floor for the shrink-to-fit above; below this it stops being legible
ACC_VALUE_SIZE = 34
ACC_MAX_MAG = 2.5      # m/s^2 that fills the intensity bar
STACK_GAP = 12
# BluePilot: the stop lamps themselves, as their own readout rather than only as a speed colour.
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
LAMP_PILL_WIDTH = 268
LAMP_PILL_HEIGHT = 56
LAMP_LABEL_SIZE = 32
LAMP_ON_FILL = rl.Color(228, 40, 40, 240)
LAMP_OFF_FILL = rl.Color(0, 0, 0, 150)
LAMP_OFF_EDGE = rl.Color(120, 126, 132, 190)
LAMP_OFF_INK = rl.Color(150, 156, 162, 255)


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
    # BluePilot: what Ford ACC is asking for, and what ICBM is doing about it. The speed colours
    # above say what traffic behind you sees; these say what the systems are requesting. Those are
    # different facts, which is why this is a separate readout rather than more colours.
    self._acc_state = ""      # "ACCEL" / "COAST" / "BRAKE", "" when unknown
    self._acc_accel = 0.0     # m/s^2, signed
    self._icbm_baseline = 0   # the driver's held set speed; 0 = no hold
    self._icbm_arrow = ""     # "+" / "-" while ICBM is actively moving the set speed, else ""
    self._icbm_hold_locked = False  # something else owns the target; a press cannot change the hold
    self._icbm_source_tag = ""      # TEMPORARY: which mechanism captured the hold, see the tag map
    self._lamp_data_available = False  # the BCM/brake-system lamp signal is actually being decoded
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
        self._icbm_source_tag = BASELINE_SOURCE_TAG.get(icbm.baselineSource.raw, "")
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

  def _draw_acc_status(self, rect: rl.Rectangle) -> None:
    """BluePilot: a compact line under the MAX box -- what ACC is asking for, what ICBM is doing.

    Placed here rather than as another icon because the two renderers that already exist say WHY
    the target moved (SmartCruiseControl shows a curve, SpeedLimit shows the sign). Neither says
    what the car is doing about it, and nothing at all showed ICBM's state.
    """
    if self._acc_status_failed or (not self._acc_state and not self._icbm_baseline):
      return

    set_speed_width = UI_CONFIG.set_speed_width_metric if ui_state.is_metric else UI_CONFIG.set_speed_width_imperial
    x = rect.x + 60 + (UI_CONFIG.set_speed_width_imperial - set_speed_width) // 2
    y = rect.y + 45 + UI_CONFIG.set_speed_height + 16

    if self._icbm_baseline:
      y += self._draw_hold_badge(x, y, set_speed_width) + STACK_GAP
    if self._acc_state:
      y += self._draw_acc_pill(x, y) + STACK_GAP
    # Shown whenever brake status is on, in both states -- an indicator that only appears when lit
    # cannot be told apart from one that is broken, and "are my lamps on right now" is a question
    # about both answers.
    if self._show_brake_status and self._lamp_data_available:
      self._draw_brake_lamp_pill(x, y)

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
    # TEMPORARY diagnostic tag. Top-right corner: the only free space on the badge, and well clear
    # of the number, which a bottom placement was not -- the 66 px value overlaps that row.
    if self._icbm_source_tag:
      tag_width = measure_text_cached(self._font_semi_bold, self._icbm_source_tag, HOLD_TAG_SIZE).x
      rl.draw_text_ex(self._font_semi_bold, self._icbm_source_tag,
                      rl.Vector2(x + width - tag_width - 14, y + 12), HOLD_TAG_SIZE, 0,
                      HOLD_TAG_COLOR)
    return HOLD_HEIGHT

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

    # All four states are filled: the colour is the reading, and its position on the green-to-red
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
    while label_size > ACC_LABEL_MIN_SIZE and         measure_text_cached(self._font_bold, self._acc_state, label_size).x > available:
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
      # leaves a hard vertical seam that reads as a rendering fault, and it forced the ink colour
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

    # BluePilot: colour the speed by what the brakes are doing, if brake status is enabled.
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
