import pyray as rl
from openpilot.common.params import Params
from cereal import custom
from opendbc.car.structs import ControllerStateBP
from openpilot.bluepilot.ui.lib.bp_shaders import draw_shader_circle_gradient
from openpilot.selfdrive.ui.onroad.hud_renderer import UI_CONFIG, FONT_SIZES, COLORS
from openpilot.selfdrive.ui.sunnypilot.onroad.hud_renderer import HudRendererSP
from openpilot.selfdrive.ui.bp.onroad.icbm_hud_state import read_icbm_hud_state
from openpilot.selfdrive.ui.bp.onroad.exp_button_bp import ExpButtonBP
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.selfdrive.ui.bp.lib.ui_debug_logger import bp_ui_log

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
# to slow "without wearing out the brakes", so this is the good outcome and should not be colored
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
# BluePilot: blockedBy -> what a driver should read. The enum names are for the log; putting
# "nothingSlower" or "noLaneAvailable" in front of someone at 70 mph is a failure of the display, not
# a shorthand. Anything unmapped falls through to the raw name so a new state is visible rather
# than silently blank.
# Ordinal -> name, straight off the schema rather than hand-listed. The last-drive summary stores
# the Blocked value as an int in a param, and a hand-written order here would silently mislabel
# every stored reason the moment a new one is added to the enum.
_BLOCKED_ORDER = tuple(
  name for name, _ in sorted(
    custom.LongitudinalPlanSP.PassingAssist.Blocked.schema.enumerants.items(), key=lambda kv: kv[1]))

_BLOCKED_TEXT = {
  'disabled': "Passing assist off",
  'notEngaged': "Cruise not engaged",
  # Not a number: the floor is a setting and moved from 40 to 30 without this following it, which
  # is exactly how a label starts lying. The panel cannot know the driver's value here.
  'tooSlow': "Too slow to pass",
  'driverActive': "You are driving",
  'noLead': "Road ahead clear",
  'nothingSlower': "Nothing slower ahead",
  'noLaneAvailable': "No lane to move into",
  'blindspotOccupied': "Blind spot not clear",
  'overtakeRestricted': "No-passing zone",
  'rearApproaching': "Traffic coming up behind",
  'suspended': "Paused",
  'adjacentSlow': "Next lane is no faster",
  'oncomingLane': "Oncoming traffic that side",
  # "Closing in" reads as something closing in on YOU, which is the opposite of what it means --
  # this is the system deliberately hanging back until the gap is smaller.
  'closingIn': "Waiting to get closer",
  'leadBraking': "Car ahead is braking",
  'driverChangedLanes': "You just changed lanes",
}

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


# BluePilot: the three thresholds that decide whether a lane exists beside us, mirrored from
# passing_assist.py. Duplicated rather than imported for the same reason ACC_PROPULSION_INACTIVE
# above is -- that file is controls and this is UI, and pulling a planner module into the UI
# process to read three floats is a dependency that buys nothing.
#
# test_geometry_thresholds_mirrored keeps them honest: it fails if either copy moves without the
# other, which is the only real objection to duplicating them.
MIN_ADJACENT_LINE_PROB = 0.5
MIN_LANE_WIDTH_M = 3.0
MAX_LANE_WIDTH_M = 5.0
MIN_EDGE_BEYOND_LINE_M = 0.8
MAX_ROAD_EDGE_STD = 0.5


# Why a blinker run that was already going got stopped. Every one of these is the test working --
# none is a fault of the car or of the signal -- which is exactly why they must not be reported as
# a flash count and left to look like one.
# Mirrors lane_display_test_ext.LANE_TEST_STEPS, in order. See _render_lane_display_test.
_LDT_LABELS = (
  "BOTH GREEN",
  "LEFT: NONE",
  "LEFT: SUPPRESS",
  "LEFT: WARNING",
  "LEFT: INTERVENE",
  "LANE ASSIST OFF",
)

_BT_STOPPED = {
  'notStationary': "STOPPED - CAR MOVED",
  'cruiseEngaged': "STOPPED - CRUISE ON",
  'driverSignalling': "STOPPED - YOUR STALK",
}

# Held, not stopped: the car is still running its own flash pattern from the last press and this one
# will go as soon as it is quiet. Shown for completeness -- he presses these from inside the
# settings menu, where none of this is visible, which is exactly why the request WAITS rather than
# being refused.
_BT_WAITING = "WAITING FOR THE LAMP"


# The left gate's four terms, in the order it evaluates them. Named for a driver rather than for
# the code: "paint" is the model's confidence in a line beyond ours, "room past it" is how much
# road is left outside the lane we would move into.
_GEO_TERMS = ("edge unsure", "paint", "lane width", "room past it")


def _feet(m: float, metric: bool) -> str:
  return f"{m:.1f}m" if metric else f"{m * 3.28084:.1f}ft"


def _suggested_deficit(missed_mph: float, active_mph: float) -> float | None:
  """What the speed bar would have had to be to accept the passes he made and it refused.

  Returns None when there is nothing to say -- a bar already at or below what was missed, or a
  suggestion that would land at zero, which is not a threshold but the absence of one.

  ROUNDED DOWN, and to a whole mph because that is what the setting takes. Rounding to nearest
  would recommend a bar that still refuses the very passes it was derived from, which is the one
  outcome that would make this line worse than silence.
  """
  if missed_mph <= 0.0 or active_mph <= 0.0 or missed_mph >= active_mph:
    return None
  suggested = float(int(missed_mph))
  return suggested if suggested >= 1.0 else None


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

    # BluePilot: passing-assist observer readout. Debug only -- this displays what the phase-1
    # observer WOULD have suggested. It is not an instruction and nothing acts on it.
    self._show_passing_assist = self._bp_params.get_bool("ShowPassingAssist")
    self._pa_main = ""
    self._pa_sub = ""
    self._pa_sub_detail = ""
    self._pa_progress = 0.0
    self._pa_alert = False
    self._pa_color = COLORS.WHITE
    self._pa_confirm_target = float(self._bp_params.get("PassingAssistConfirmTime", return_default=True) or 2)
    self._pa_blinker_lead = float(self._bp_params.get("PassingAssistBlinkerLead", return_default=True) or 1)
    self._pa_crawl_target = float(self._bp_params.get("PassingAssistCrawlTime", return_default=True) or 8)
    # BluePilot: suggestions are transient -- a glance at the wrong moment misses one entirely, and
    # "I saw nothing" is indistinguishable from "it never ran". A per-drive count makes the answer
    # readable at any time without watching continuously or opening a log.
    self._pa_count = 0
    self._pa_suggesting_prev = False
    self._pa_started_frame = 0
    self._pa_panel_rect = None   # last drawn rect, used as the tap target
    self._pa_failed = False      # own latch: must not share fate with the ACC readout

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
      self._show_passing_assist = self._bp_params.get_bool("ShowPassingAssist")
      if self._show_passing_assist:
        # Needed as the progress-bar denominator, so the bar means "how close to the threshold
        # you actually configured" rather than to a hardcoded guess.
        self._pa_confirm_target = float(self._bp_params.get("PassingAssistConfirmTime", return_default=True) or 2)
        # Refreshed here as well as in __init__, or the setting would only take effect on a reboot
        # -- which reads as the control doing nothing.
        self._pa_blinker_lead = float(self._bp_params.get("PassingAssistBlinkerLead", return_default=True) or 1)
        self._pa_crawl_target = float(self._bp_params.get("PassingAssistCrawlTime", return_default=True) or 8)

    # 7.0 reads the lateral mode from controllerStateBP rather than re-deriving it from params.
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
          # Decel request only -- precharge produces no deceleration, so coloring the speed for
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

    # BluePilot: same protection, SEPARATE latch. These are unrelated readouts and must fail
    # independently -- sharing one would mean an ACC error silently freezing the passing-assist
    # panel on its last frame forever, and a panel error blanking the ACC line. Calling this from
    # inside _update_acc_status, where it briefly lived after a merge, produced exactly that.
    if not self._pa_failed:
      try:
        self._update_passing_assist()
      except Exception as e:
        self._pa_failed = True
        self._pa_main, self._pa_sub, self._pa_progress, self._pa_alert = "", "", 0.0, False
        bp_ui_log.state("HudRendererBP", "passing_assist_error", repr(e))

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
    # Read through the shared reader, not inline: the comma 4 screen draws the same hold from its own
    # renderer tree, and two copies of this would drift apart on the next enum change.
    icbm_state = read_icbm_hud_state(sm)
    self._icbm_arrow = icbm_state.arrow
    # worth_showing, not has_hold -- see IcbmHudState. The hold still governs the car either way;
    # this only decides whether a badge showing the same number as MAX is drawn.
    if icbm_state.worth_showing:
      self._icbm_baseline = icbm_state.baseline
      self._icbm_hold_locked = icbm_state.hold_locked
      self._icbm_pinned = icbm_state.pinned
      self._icbm_pin_suggested = icbm_state.pin_suggested

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


  # Widest the sub-line may be. The panel clamps its own box to the screen but the text is drawn
  # from its own measured width, so anything longer does not wrap or shrink -- it runs off both
  # ends and is unreadable. The drive summary grew to six possible items without anyone checking,
  # and a preview render of the worst case is what caught it.
  MAX_SUB_WIDTH = 960
  SUB_SIZE = 30

  def _fit_sub(self, lines: list[str]) -> str:
    """Join as many lines as actually fit, in the order given.

    Measured rather than counted: the items vary from "7 backed out" to "mostly: oncoming traffic
    that side 62%", and any fixed count is either wasteful or wrong. Callers pass them in priority
    order, so what gets dropped is the least useful thing rather than the last thing.
    """
    out: list[str] = []
    for line in lines:
      candidate = "  -  ".join(out + [line])
      if out and measure_text_cached(self._font_bold, candidate, self.SUB_SIZE).x > self.MAX_SUB_WIDTH:
        break
      out.append(line)
    return "  -  ".join(out)

  def _draw_drive_summary(self, pa, sm) -> bool:
    """What this drive actually measured, shown while stopped. Returns True if it owns the line.

    Every number here exists to settle a question that cannot be settled by argument:

      slow passes   -> how often a pass grinds, and for how long. Sets the speed-nudge trigger.
      backed out    -> how often a sequence signaled and had to abandon it. Near zero means the
                       gates are steady enough to act on; climbing names an unstable one.
      ACC braked by -> the furthest back Ford's ACC ever lost patience. The close-in hold has to
                       stay clear of this, and until it is measured that hold has no safe value.
    """
    try:
      if sm['carState'].vEgo > 0.3:
        return False

      # Nothing measured yet this drive: show the last one instead. Parking used to throw these
      # away entirely, and they are the whole output of this phase -- read off this panel at a
      # traffic light or not at all.
      if pa.wantedSeconds <= 0.0 and not pa.crawlEvents and not pa.maneuverAborts:
        return self._draw_last_drive()

      # Priority order, most useful first -- see _fit_sub, which drops from the end.
      # PRIORITY ORDER, and it matters far more than it looks. _fit_sub stops adding once the line
      # would overflow, and rendering the whole panel on one sheet showed the truth: the agreement
      # line alone very nearly fills the width, so in practice only two items ever appear. Anything
      # below second is decoration.
      #
      # Which made a comment further down flatly false -- it claimed reversed-mid-change "must
      # never be dropped for space" while sitting fourth, where it was always dropped. It goes
      # first now. It is short and it is usually zero, so it costs nothing when there is nothing
      # to say, and it is the only safety figure here.
      lines = []
      if pa.emergencyAborts:
        lines.append(f"{pa.emergencyAborts} reversed mid-change")

      # Then the one line that answers "is this ready" rather than describing a symptom. Shortened
      # from "you passed 7, agreed 6 (11s early), missed on ..." because that single sentence ate
      # the entire budget and left no room for the evidence behind it. The miss reason is its own
      # item now, so it competes on merit instead of riding along.
      if pa.driverPasses:
        agreed = pa.driverPassesAgreed
        line = f"{pa.driverPasses} passes, {agreed} agreed"
        if agreed:
          line += f" ({pa.driverPassLeadSeconds:.0f}s early)"
        lines.append(line)
        if agreed < pa.driverPasses:
          miss = int(pa.driverPassMissReason)
          name = _BLOCKED_ORDER[miss] if miss < len(_BLOCKED_ORDER) else ""
          if name == "nothingSlower" and pa.missedDeficitMph > 0:
            # Names the number to change rather than the symptom. "Missed on nothing slower" is a
            # complaint; "they were 2.4 mph slower" is an instruction.
            #
            # ...and then TAKE THE LAST STEP, which this stopped one short of. Every competitor
            # that adapts to a driver does it by quietly retuning itself -- Hyundai's HDA2
            # machine-learns ACC habits, Tesla folds it into a speed profile. Neither tells you
            # what it changed. Here the measurement is already exact: these are passes HE made that
            # the speed bar alone refused, so the bar is provably too high by the difference.
            #
            # Says the number, does not write it. A default I picked is a suggestion; a setting he
            # picked is data -- see params_migration.py. The one thing it must never become is a
            # system that retunes itself and mentions it nowhere.
            #
            # ONE ITEM, not two. _fit_sub drops whole items to make the line fit, and a
            # recommendation that can be dropped independently of the measurement it came from
            # would appear as a bare instruction with nothing behind it -- or, more often, not at
            # all, since it sits behind two other items in priority order.
            miss = f"missed cars {pa.missedDeficitMph:.1f} mph slower"
            suggest = _suggested_deficit(pa.missedDeficitMph, pa.minDeficitActive)
            if suggest is not None:
              miss += f", try {suggest:.0f}"
            lines.append(miss)
          elif name:
            lines.append(f"missed on {_BLOCKED_TEXT.get(name, name).lower()}")

      # The total is what actually decides anything -- one drive's seven passes swing by a third on
      # a single odd stretch of road. Shown only once there is more of it than the drive in front
      # of it, or it is the same number printed twice.
      if pa.lifetimeDrives > 1 and pa.lifetimePasses > pa.driverPasses:
        lines.append(f"{pa.lifetimeDrives} drives: {pa.lifetimePasses} passed, "
                     f"{pa.lifetimeAgreed} agreed")
      # The other error direction: suggestions nobody acted on.
      #
      # SHOWN AT ZERO TOO, once a pass has actually been wanted. Hiding it made "it suggested
      # nothing" look exactly like "that line does not exist", which is the difference between a
      # feature working correctly on a quiet road and a feature that is broken. Reported from the
      # car as "I heard zero chimes, but maybe I never turned it on?" -- a question the panel
      # should have answered without anyone having to ask it.
      # WHY IT NEVER FIRED, as one sentence, at a stop. Five drives, twenty-one passes and zero
      # suggestions is a number with no next step attached; "left refused: paint 0.31 (86%)" names
      # the one of four constants to change. Placed above the counts because it is the finding and
      # they are the symptom.
      #
      # Only once there is enough of it to mean anything, and only when it is actually the thing
      # standing in the way -- on a road where a pass was never wanted, the left side being refused
      # is not news.
      # NOT gated on having suggested nothing. It was, and one suggestion anywhere in a drive
      # would have taken the diagnosis away -- on a drive that refused four hundred times and
      # suggested once, the line is still the most useful thing on the screen. He has very little
      # testing time; losing the answer to a technicality costs a day.
      if pa.wantedSeconds > 30.0 and pa.geoRefusedShare > 0.5:
        term = _GEO_TERMS[pa.geoRefusedBy] if pa.geoRefusedBy < len(_GEO_TERMS) else "?"
        v = pa.geoRefusedValue
        shown = f"{v:.2f}" if pa.geoRefusedBy in (0, 1) else _feet(v, ui_state.is_metric)
        lines.append(f"left refused: {term} {shown} ({pa.geoRefusedShare * 100:.0f}%)")
      if pa.suggestionsMade or pa.wantedSeconds > 0.0:
        line = f"suggested {pa.suggestionsMade}, taken {pa.suggestionsTaken}"
        if pa.longestIgnoredSeconds > 5.0:
          line += f", longest ignored {pa.longestIgnoredSeconds:.0f}s"
        lines.append(line)
      # Split by maneuver only when they disagree. The counters were kept separate so an unstable
      # gate could be attributed to one or the other -- and then never shown, which made the whole
      # justification for splitting them worthless. One number when they agree, two when they
      # differ, because the difference IS the finding.
      if pa.keepRightAborts:
        lines.append(f"{pa.keepRightAborts} backed out moving right")
      # The most useful line here, so it goes first: what actually stopped the passes.
      # A minute of evidence, not five seconds. A percentage over six seconds of data is noise
      # dressed as a finding, and this line is meant to decide what gets worked on next.
      if pa.wantedSeconds > 60.0 and pa.topBlockedShare > 0.05:
        reason = _BLOCKED_TEXT.get(str(pa.topBlockedBy), str(pa.topBlockedBy))
        lines.append(f"mostly: {reason.lower()} {pa.topBlockedShare * 100:.0f}%")
      if pa.accBrakingOnsetMax > 0:
        d = pa.accBrakingOnsetMax if ui_state.is_metric else pa.accBrakingOnsetMax * 3.28084
        lines.append(f"ACC braked by {d:.0f}{'m' if ui_state.is_metric else 'ft'}")
      if pa.maneuverAborts:
        lines.append(f"{pa.maneuverAborts} backed out")
      if pa.crawlEvents:
        lines.append(f"{pa.crawlEvents} slow "
                     f"{'pass' if pa.crawlEvents == 1 else 'passes'}, worst {pa.crawlLongestSeconds:.0f}s")
    except (AttributeError, KeyError):
      return False

    if not lines:
      return False

    self._pa_main = "THIS DRIVE"
    # Assembled here, NOT via _pa_sub_detail. That field is folded into _pa_sub at the end of
    # _update_passing_assist, and every method here returns before reaching it -- so anything left
    # in it is silently dropped. Cost me three readouts before a preview render showed it.
    self._pa_sub = self._fit_sub(lines)
    self._pa_color = rl.Color(150, 205, 235, 255)
    return True

  def _draw_last_drive(self) -> bool:
    """The previous drive's numbers, from the param the detector writes. Returns True if shown."""
    try:
      d = self._bp_params.get("PassingAssistLastDrive")
      if not d or float(d.get("wantedSeconds", 0)) <= 5.0:
        return False
      lines = []
      if int(d.get("driverPasses", 0)):
        line = f"you passed {int(d['driverPasses'])}, agreed {int(d['driverPassesAgreed'])}"
        if int(d.get("driverPassesAgreed", 0)):
          line += f" ({float(d['driverPassLead']):.0f}s early)"
        lines.append(line)
      if int(d.get("suggestionsMade", 0)) or float(d.get("wantedSeconds", 0)) > 0:
        lines.append(f"suggested {int(d['suggestionsMade'])}, taken {int(d['suggestionsTaken'])}")
      if int(d.get("lifetimeDrives", 0)) > 1:
        lines.append(f"all {int(d['lifetimeDrives'])} drives: {int(d['lifetimePasses'])} passed, "
                     f"{int(d['lifetimeAgreed'])} agreed")
      share = float(d.get("topBlockedShare", 0))
      if share > 0.05:
        # The int is the Blocked ordinal. Mapped through the same table the live panel uses, so
        # the wording cannot drift between "what stopped it" and "what stopped it last time".
        name = _BLOCKED_ORDER[int(d["topBlockedBy"])] if int(d["topBlockedBy"]) < len(_BLOCKED_ORDER) else ""
        if name:
          lines.append(f"mostly: {_BLOCKED_TEXT.get(name, name).lower()} {share * 100:.0f}%")
      if d.get("crawlEvents"):
        n = int(d["crawlEvents"])
        lines.append(f"{n} slow {'pass' if n == 1 else 'passes'}, worst {float(d['crawlLongest']):.0f}s")
      if d.get("aborts"):
        lines.append(f"{int(d['aborts'])} backed out")
      # Which half of the oncoming veto did the work. Mostly-remembered on a divided highway is
      # the I-15 fault stated as a number rather than a complaint.
      seen, remembered = float(d.get("oncomingSeen", 0)), float(d.get("oncomingRemembered", 0))
      if seen + remembered > 5.0:
        lines.append(f"oncoming: {seen:.0f}s seen, {remembered:.0f}s remembered")
      # How many cars went past us, and how long the lane has been quiet since. The whole question
      # this is meant to answer is whether a long quiet stretch is real, so the gap is the number
      # that matters -- not the count.
      passed_by = int(d.get("overtakenLeft", 0)) + int(d.get("overtakenRight", 0))
      if passed_by:
        quiet = float(d.get("overtakenQuietest", 0))
        lines.append(f"passed by {passed_by}, quiet {quiet:.0f}s")
      if float(d.get("oncomingDRel", 0)) > 0:
        od = float(d["oncomingDRel"])
        ov = abs(float(d["oncomingVAbs"])) * (3.6 if ui_state.is_metric else 2.23694)
        du = od if ui_state.is_metric else od * 3.28084
        lines.append(f"oncoming: {ov:.0f} at {du:.0f}{'m' if ui_state.is_metric else 'ft'}")
      # The driver's own lane changes, from a separate param. Two of passing assist's constants
      # are guesses this measures away: how long a crossing takes, and what a HUMAN abandon rate
      # looks like -- without which its own backed-out count has no scale.
      lc = self._bp_params.get("LaneChangeStats") or {}
      if int(lc.get("changes", 0)):
        line = f"your changes: {int(lc['changes'])}, {float(lc.get('seconds', 0)):.1f}s each"
        if int(lc.get("abandoned", 0)):
          line += f", {int(lc['abandoned'])} abandoned"
        # Cancelled was being counted and saved and never shown, which left the one question a
        # drive can answer about the cancel unanswerable: "turning off my blinker mid lane change
        # doesn't really seem to cancel." A zero here means the cancel never fired and the window
        # is the thing to change; a number means it fired and the car went anyway, which is a
        # different fault with a different fix.
        if int(lc.get("cancelled", 0)):
          line += f", {int(lc['cancelled'])} cancelled"
        lines.append(line)
      if float(d.get("accOnsetMax", 0)) > 0:
        m = float(d["accOnsetMax"])
        v = m if ui_state.is_metric else m * 3.28084
        lines.append(f"ACC braked by {v:.0f}{'m' if ui_state.is_metric else 'ft'}")
    except Exception:  # noqa: BLE001 - a malformed param must never blank the panel
      return False

    if not lines:
      return False
    self._pa_main = "LAST DRIVE"
    self._pa_sub = self._fit_sub(lines)
    self._pa_color = rl.Color(150, 205, 235, 255)
    return True

  def _draw_crawl(self, pa) -> bool:
    """A pass that is taking too long. Returns True if it owns the line this frame."""
    try:
      if pa.crawlSeconds < self._pa_crawl_target:
        return False
    except (AttributeError, KeyError):
      return False

    # NO SIDE IN THE TEXT. Only one side can produce a slow pass now -- the car you are passing is
    # on your right, by definition -- so naming it is noise at best. At worst it is the wording that
    # made the old bug so confusing to report: "barely gaining on the car on the left" described
    # traffic that was overtaking HIM, and reads as nonsense precisely because it was.
    self._pa_main = f"SLOW PASS  {pa.crawlSeconds:.0f}s"
    self._pa_sub = "barely gaining on the car you are passing"
    self._pa_color = rl.Color(240, 175, 60, 255)
    self._pa_alert = True
    return True

  def _draw_maneuver(self, pa) -> bool:
    """The dry run: what a fully automatic pass WOULD be doing right now. Returns True if it owns
    the line this frame.

    Nothing here is actuating anything and the wording has to keep saying so, every phase, or a
    driver glancing at "SIGNALING LEFT" will reasonably conclude the car did it. Hence the
    "would" line under every phase, and no chevrons -- those mean "act on this" everywhere else on
    this screen.
    """
    try:
      phase = str(pa.maneuver)
    except (AttributeError, KeyError):
      return False
    if phase in ('idle', 'confirming', 'waiting'):
      # ...unless a crossing was just reversed. The detector still says a pass is warranted and
      # the lane is clear -- both true -- so with nothing said here the screen falls through to a
      # green PASS LEFT seconds after the car backed out of exactly that pass. Contradicting
      # yourself on the one readout a driver is meant to trust is worse than saying nothing.
      if pa.maneuverStandDown > 0.0:
        # ...or a run that went all the way through, which shares the clock and nothing else.
        # Reported as "BACKED OUT" it would be a flat lie about a sequence that worked, and it is
        # the more common of the two now that a completed run stands down at all -- see
        # COMPLETE_STANDDOWN_S, and the loop that made it necessary.
        if pa.maneuverStandDownComplete:
          self._pa_main = "WOULD BE DONE"
          self._pa_sub = f"holding {pa.maneuverStandDown:.0f}s before looking again"
          self._pa_color = rl.Color(190, 150, 235, 255)
        else:
          self._pa_main = "BACKED OUT"
          self._pa_sub = f"waiting {pa.maneuverStandDown:.0f}s before trying again"
          self._pa_color = rl.Color(235, 90, 80, 255)
        self._pa_alert = True
        return True
      return False   # nothing committed yet; the verdict display below is the better readout

    side = str(pa.maneuverSide).upper()
    keep_right = str(pa.maneuverReason) == 'keepRight'
    self._pa_alert = True
    self._pa_color = rl.Color(190, 150, 235, 255)   # not the green of a real suggestion

    if phase == 'signaling':
      self._pa_main = f"WOULD SIGNAL {side}"
      # Keep-right and passing look identical on this line otherwise, and they mean opposite
      # things: one is going round something, the other is getting out of the way.
      self._pa_sub = "moving back over" if keep_right else "waiting before moving"
      self._pa_progress = min(1.0, pa.maneuverSeconds / max(self._pa_blinker_lead, 0.1))
    elif phase == 'changing':
      self._pa_main = f"WOULD BE CHANGING {side}"
      self._pa_sub = "blinker on, steering across"
      self._pa_progress = min(1.0, pa.maneuverSeconds / 4.0)
    elif phase == 'aborting':
      # Red, alone among these. Every other phase is the system going about its business; this one
      # is it getting out of the way of something, and it should not look like the others.
      self._pa_main = "WOULD BACK OUT"
      self._pa_sub = "something arriving behind"
      self._pa_color = rl.Color(235, 90, 80, 255)
    else:
      self._pa_main = "WOULD BE DONE"
      self._pa_sub = "blinker off"

    # A sequence resting on a camera-only lead is worth seeing AS IT HAPPENS, not just in a log:
    # that lead's speed is the model's guess rather than the radar's measurement, and "4 mph
    # slower" is the entire judgment. Takes priority over the abort count -- it says something
    # about THIS maneuver, where the count is about the drive.
    # Appended to _pa_sub directly -- see the note in _draw_drive_summary about _pa_sub_detail.
    if not pa.leadRadarConfirmed:
      self._pa_sub += "  -  camera only, speed not radar-measured"
    elif pa.maneuverAborts:
      # The number this whole dry run exists to produce, on screen rather than only in the log --
      # a drive where this climbs is a drive that answered the question.
      self._pa_sub += f"  -  {pa.maneuverAborts} backed out this drive"
    return True

  def _update_passing_assist(self) -> None:
    """BluePilot: build the passing-assist panel state.

    Written to be read at a glance at speed, not decoded. Enum names like "nothingSlower" are what the
    log records and are the wrong thing to put in front of a driver, so every state maps to plain
    words; the confirmation timer becomes a progress bar because a bar answers "is it nearly there" in
    peripheral vision and a number does not.

    Three things have to be legible without study: is it running, is it building toward something,
    and did it decide -- which way.
    """
    self._pa_main = ""
    self._pa_sub = ""
    self._pa_sub_detail = ""
    self._pa_progress = 0.0
    self._pa_alert = False
    self._pa_color = COLORS.WHITE
    if not self._show_passing_assist:
      return

    sm = ui_state.sm

    # The stationary blinker test owns the panel while it runs. The two barely overlap -- the
    # blinker test only runs stopped, and passing assist needs PassingAssistMinSpeed, 30 mph by
    # default -- but the drive summary IS a stopped state, so this has to come first outright. It
    # is a deliberate action the driver is standing there waiting for a result from.
    if self._render_blinker_test(sm):
      return

    # Same claim on the panel, same reason: a stationary test the driver is standing there watching
    # for. It goes first because its whole output is a word the driver has to read off this screen
    # while looking at the cluster -- if anything else owned the line, the walk would be five
    # unlabeled pictures.
    if self._render_lane_display_test(sm):
      return

    if not sm.valid.get('longitudinalPlanSP', False):
      return

    try:
      pa = sm['longitudinalPlanSP'].passingAssist
    except (KeyError, AttributeError):
      return

    # Reset the count on each new drive rather than each UI start, so it always means
    # "this trip" no matter when the display was switched on.
    if ui_state.started_frame != self._pa_started_frame:
      self._pa_started_frame = ui_state.started_frame
      self._pa_count = 0
      self._pa_suggesting_prev = False

    # TURNED OFF, and it says so rather than going blank. He turned it off with the LKA button and
    # it stays off across ignition cycles -- "leave it off until I turn it on" -- so an empty panel
    # would be indistinguishable from a quiet road, and from a broken feature.
    #
    # NO PANEL TAP any more. It used to toggle the pause, and he is right that it does not belong:
    # "MADS and stuff don't have panel controls." A full-panel target that disables a feature is an
    # accidental press waiting to happen, and the physical button is the precedent that fits.
    if str(pa.blockedBy) == 'disabled':
      self._pa_main = "PASSING ASSIST OFF"
      self._pa_sub = "LKA button turns it back on"
      self._pa_color = rl.Color(170, 175, 180, 255)
      return

    suggestion = str(pa.suggestion)

    # Stopped, so nothing is happening and the panel is free. This is the only place the drive's
    # own numbers can be read: the owner does not SSH into the car and does not read logs, so a
    # measurement with nowhere to appear is a measurement that was never taken.
    if self._draw_drive_summary(pa, sm):
      return

    # ORDER MATTERS, and the obvious order is wrong. A grinding pass is happening now and is the
    # one state a driver might act on, so it outranks the dry run -- but NOT while the car is
    # committed. Once the maneuver is crossing, backing out, or standing down after a reversal, a
    # slow-pass warning would suppress the only red state this panel has, which is the one that
    # says something arrived behind us. Crawling and crossing can both be true at once: a slow pass
    # IS a car close alongside being barely gained on.
    committed = str(pa.maneuver) in ('changing', 'aborting') or pa.maneuverStandDown > 0.0
    if not committed and self._draw_crawl(pa):
      return

    # The dry run takes the line whenever a sequence is actually running. It is strictly more
    # informative than the single-frame verdict below -- it says the same thing plus where in the
    # maneuver it is -- and it is the readout the whole phase-2 question turns on.
    if self._draw_maneuver(pa):
      return

    # Rising edge only: a suggestion that holds for 30 s is one event, not 600.
    suggesting = suggestion != 'none'
    if suggesting and not self._pa_suggesting_prev:
      self._pa_count += 1
    self._pa_suggesting_prev = suggesting

    if suggesting:
      self._pa_alert = True
      # Direction is carried by chevrons on the correct side, so the side registers before the
      # words are read. KEEP RIGHT and PASS RIGHT are both Side.right and mean opposite things,
      # which is why the reason is spelled out rather than inferred from the side.
      if str(pa.reason) == 'keepRight':
        self._pa_main = "MOVE RIGHT  >>>"
        self._pa_color = rl.Color(140, 190, 230, 255)
      elif suggestion == 'left':
        self._pa_main = "<<<  PASS LEFT"
        self._pa_color = rl.Color(120, 220, 140, 255)
      else:
        self._pa_main = "PASS RIGHT  >>>"
        self._pa_color = rl.Color(120, 220, 140, 255)
    else:
      blocked = str(pa.blockedBy)
      self._pa_color = rl.Color(170, 175, 180, 255)
      if blocked == 'nothingSlower' and pa.confirmSeconds > 0:
        # The one state that is genuinely "working on it". Show the bar, not the arithmetic.
        self._pa_main = "Slower car ahead"
        self._pa_progress = min(1.0, pa.confirmSeconds / max(self._pa_confirm_target, 1.0))
      else:
        self._pa_main = _BLOCKED_TEXT.get(blocked, blocked)
        # Show the two numbers actually being compared. "Nothing slower ahead" with a visibly
        # slower car in front is the exact report that took a drive to diagnose twice; the operands
        # make it a glance instead. Reference is the speed the driver asked for, which with ICBM
        # running is NOT the number on the dash.
        conv = 2.23694 if not ui_state.is_metric else 3.6
        if blocked == 'nothingSlower' and pa.hasLead and pa.referenceSpeed > 0:
          self._pa_sub_detail = (f"want {pa.referenceSpeed * conv:.0f}"
                                 f"  lead {pa.leadVLead * conv:.0f}"
                                 f"  [{pa.referenceSource}]")
        # ONE WORD, for the LEFT side, and nothing else. It used to print "L paint 0.31     R
        # shoulder 0.3ft" -- four numbers to parse at 70 mph -- which earned "and you expect me to
        # read all of that while driving?" and was removed outright.
        #
        # A single word is the other thing he said, though: "I love having good visual information
        # on the screen, but only for at-a-glance information." This is glanceable, it is the only
        # question standing between the feature and working at all, and it means one look answers
        # what otherwise costs an SSH session. The numbers stay in the drive summary.
        elif blocked == 'noLaneAvailable' and pa.geoRefusedShare > 0.0:
          # THE WORD AND THE NUMBER. A word alone names the constant; it does not say what to set
          # it to, and a drive that produces "paint" and nothing else still costs a second drive to
          # act on. geoLoosenTo is where that term would have to sit to admit four fifths of the
          # refusals -- so "paint 0.31" read off the screen at a light is the entire answer.
          term = (_GEO_TERMS[pa.geoRefusedBy] if pa.geoRefusedBy < len(_GEO_TERMS) else "?")
          self._pa_sub_detail = f"left: {term} {pa.geoLoosenTo:.2f}"
        elif blocked == 'closingIn' and pa.minApproachActive > 0:
          # Auto derives this from what the car's own ACC has been measured doing, so the number is
          # different per car and changes as it learns. Without showing it, "Waiting to get closer"
          # is a state with no observable meaning -- closer than WHAT.
          d = pa.minApproachActive if ui_state.is_metric else pa.minApproachActive * 3.28084
          unit = 'm' if ui_state.is_metric else 'ft'
          now = pa.leadDRel * (1 if ui_state.is_metric else 3.28084)
          self._pa_sub_detail = f"until {d:.0f}{unit}  -  now {now:.0f}{unit}"
        elif blocked == 'oncomingLane':
          # Say how long the veto has left, so a driver who has just turned off a two-lane road
          # onto a divided one can see it counting down rather than wonder if it has hung.
          # WHAT fired it, not just that it did. Reported on I-15, which is divided, so this
          # veto is either seeing the opposite carriageway or inventing traffic -- and the two
          # need completely different fixes. The numbers tell them apart at a glance, which is the
          # only way to tell without reading a log.
          # Named per SIDE, because that is all this ever knew. The veto has always been
          # per-side -- on a four-lane two-way road in the left lane, the oncoming lane is one
          # to the left and an ordinary through lane is one to the right, and the right is still
          # offered. Calling the state "two-way road" claimed something about the whole road that
          # was never measured, and on I-15 it read as a flat error rather than as "something came
          # at me on this side", which is the honest and much narrower claim.
          left = pa.adjacentLeft.oncoming or pa.adjacentLeft.oncomingSeconds > 0
          onc = pa.adjacentLeft if left else pa.adjacentRight
          self._pa_main = f"ONCOMING {'LEFT' if left else 'RIGHT'}"
          conv = 2.23694 if not ui_state.is_metric else 3.6
          if onc.oncomingDRel > 0:
            d = onc.oncomingDRel if ui_state.is_metric else onc.oncomingDRel * 3.28084
            self._pa_sub_detail = (f"saw {abs(onc.oncomingVAbs) * conv:.0f} at "
                                   f"{d:.0f}{'m' if ui_state.is_metric else 'ft'}  -  "
                                   f"{pa.oncomingSecondsLeft:.0f}s left")
          else:
            self._pa_sub_detail = f"seen {pa.oncomingSecondsLeft:.0f}s of memory left"
        elif blocked == 'adjacentSlow':
          # Same reasoning as above: show the comparison, not just its verdict. Which side is
          # reported matters, because "the next lane is no faster" is a claim about a specific
          # lane and the driver can look at it.
          parts = [f"lead {pa.leadVLead * conv:.0f}"]
          for name, adj in (("L", pa.adjacentLeft), ("R", pa.adjacentRight)):
            if adj.available and adj.occupied:
              parts.append(f"{name} {adj.vAbs * conv:.0f} at {adj.dRel:.0f}m")
          self._pa_sub_detail = "  ".join(parts)

    # Sub-line: what was NOT checked, plus the per-drive count. Both belong here rather than in the
    # headline -- a suggestion with no blind-spot data must never read as one that passed a check,
    # but the caveat should not crowd out the thing being suggested.
    caveats = []
    if not pa.blindspotAvailable:
      caveats.append("no blind spot data")
    if not pa.tsrAvailable:
      caveats.append("no sign data")
    if not (pa.rearLeft.available or pa.rearRight.available):
      caveats.append("no rear data")
    if not (pa.adjacentLeft.available or pa.adjacentRight.available):
      caveats.append("no next-lane data")
    # Say so even when it is not what is blocking. On a four-lane two-way road a pass can still be
    # suggested to the right while the left is refused, and the driver should be able to see WHY
    # only one side is ever offered rather than infer it.
    if pa.oncomingAnySide:
      caveats.append("oncoming seen")
    if self._pa_count:
      caveats.append(f"{self._pa_count} this drive")
    if self._pa_sub_detail:
      caveats.insert(0, self._pa_sub_detail)
    # FITTED, like the drive summary. This joined everything unconditionally, and the worst case
    # measures 1355px against a 1008px panel -- so it ran off BOTH edges, and the two things at the
    # front of the list, the geometry numbers, went off the left one first.
    #
    # Which is the whole of "it still just says no lane to move into all the time" plus "at some
    # point the entire UI went off the screen". The reason was computed correctly, published
    # correctly, and drawn 170px past the edge of the display on either side.
    #
    # Priority order is already right: the detail is inserted at the front, so what gets dropped
    # is "no sign data" and friends rather than the number that explains the refusal.
    self._pa_sub = self._fit_sub(caveats)

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
    self._draw_passing_assist(rect)

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
    # The label stays centerd on its own and the arrow hangs off its right, so the word does not
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

  def _render_lane_display_test(self, sm) -> bool:
    """Name the lane-display state the cluster is being sent right now. True while a walk runs.

    The labels are duplicated from lane_display_test_ext.LANE_TEST_STEPS rather than imported --
    the UI process does not import opendbc car code -- and test_lane_display_test_labels_match
    fails if they drift apart. Written down because a walk that names the steps wrongly is worse
    than no walk: it produces confident, wrong answers.
    """
    try:
      if float(sm['carState'].vEgo) > 0.5:
        return False
    except (KeyError, AttributeError, TypeError):
      pass
    try:
      ldt = sm['carStateBP'].laneDisplayTest
    except (KeyError, AttributeError):
      return False

    step = int(ldt.step)
    if step == 0:
      if int(ldt.blockedReason) == 1:
        self._pa_main = "STOP THE CAR FIRST"
        self._pa_sub = "the lane walk only runs stopped"
        self._pa_color = rl.Color(255, 200, 60, 255)
        self._pa_alert = True
        return True
      return False

    if step > len(_LDT_LABELS):
      return False

    label = _LDT_LABELS[step - 1]
    # Where to look. "Watch the left line" is wrong for LA_Off, which is a whole-display value --
    # and being told to watch the wrong thing is how a walk produces a confident wrong answer.
    where = "watch the LEFT line" if label.startswith("LEFT:") else "watch the whole display"
    self._pa_main = label
    self._pa_sub = f"{where}  --  {step} of {len(_LDT_LABELS)}"
    self._pa_progress = 0.0
    self._pa_color = rl.Color(150, 205, 235, 255)
    self._pa_alert = True
    return True

  def _render_blinker_test(self, sm) -> bool:
    """Show blinker-test state while a pulse runs, or its verdict just after. Returns True if it
    owns the line this frame.

    STANDSTILL ONLY, and that guard is not belt-and-braces -- its absence blanked the whole panel
    on a drive. Reported: "all the feedback went away on the screen and it just kept beeping."

    This function returns True for any non-idle blinker-test state and the caller returns
    immediately, so it silently owns the line. That was harmless for as long as nothing published
    the state -- it read 0 forever and this never fired. Wiring the verdict through to the message
    made the path live, and a state left non-idle then hid every passing-assist readout for the
    rest of the drive while the planner carried on chiming into a blank screen.

    The test itself refuses to run above STANDSTILL_V_EGO, so there is no case where this should
    own the panel in motion. Checking the car rather than the state means a stuck, stale or
    unpublished value cannot cost the driver their instruments.
    """
    try:
      if float(sm['carState'].vEgo) > 0.5:
        return False
    except (KeyError, AttributeError, TypeError):
      pass
    try:
      bt = sm['carStateBP'].blinkerTest
    except (KeyError, AttributeError):
      return False

    state = str(bt.state)
    if state == 'idle' and str(bt.blockedReason) == 'lampStillFlashing':
      self._pa_main = _BT_WAITING
      self._pa_sub = "the car is still flashing from the last one"
      self._pa_color = rl.Color(255, 200, 60, 255)
      self._pa_alert = True
      return True
    # MEASURING has no commanded side and no progress -- it is waiting for the driver. Reported as
    # "measure my blinker didn't seem to do anything at all", because this fell through to the
    # pulsing branch below, which reads bt.commanded (zero here) and cheerfully announced
    # "SIGNAL RIGHT" while a progress bar filled against a pulse that was not running.
    if bt.commanded == 0 and state == 'pulsing':
      self._pa_main = "FLICK YOUR STALK"
      self._pa_sub = (f"{bt.flashes} flashes so far" if bt.flashes else "watching your blinker...")
      self._pa_progress = 0.0
      self._pa_color = rl.Color(150, 205, 235, 255)
      self._pa_alert = True
      return True
    if state == 'pulsing':
      side = "LEFT" if bt.commanded == 1 else "RIGHT"
      self._pa_main = f"SIGNAL {side}"
      self._pa_sub = "lamp confirmed" if bt.lampSeen else "waiting for lamp..."
      self._pa_progress = min(1.0, max(0.0, 1.0 - bt.secondsRemaining / 4.0))
      self._pa_color = rl.Color(255, 200, 60, 255)
      self._pa_alert = True
      return True
    if state == 'done':
      if bt.measuredPeriodMs:
        # Measure mode's whole output: his own flasher, timed. The number the blink follows.
        self._pa_main = "YOUR BLINKER"
        self._pa_sub = f"{bt.flashes} flashes, {bt.measuredPeriodMs} ms apart"
        self._pa_color = rl.Color(150, 205, 235, 255)
        self._pa_alert = True
        return True
      # A RUN THAT WAS CUT SHORT SAYS SO, before anything else. Reported six times in one drive as
      # "it only did two flashes", "only six", "only three" -- short, never gapped, varying with
      # how long he waited. That is a run being stopped, not a car dropping blinks, and the most
      # likely stopper is the first one here: the gate is 0.3 m/s, which is below a creep, so
      # testing at a light ends the run the moment the car rolls. Correct behaviour, reported as
      # "SIGNAL WORKS, 2 flashes", which reads as the car half-ignoring us.
      reason = str(bt.blockedReason)
      # A short count with NO gate to blame is a different fault and has to read differently.
      # Reported as "the readout does say what blink count it was, it's just not always 7" -- no
      # stop reason, so nothing cut it off; it ran out of time waiting for a lamp report that never
      # came. See BLINK_STALL_S. With the watchdog this should stop happening, and if it does not,
      # this line is the difference between a run that was stopped and a run that lost the lamp.
      if reason == 'none' and bt.blinksWanted and bt.blinksSent < bt.blinksWanted:
        self._pa_main = "LOST THE LAMP"
        self._pa_sub = f"{bt.blinksSent} of {bt.blinksWanted} blinks - the car stopped reporting it"
        self._pa_color = rl.Color(255, 200, 60, 255)
        self._pa_alert = True
        return True
      if reason != 'none' and bt.blinksWanted:
        self._pa_main = _BT_STOPPED.get(reason, "STOPPED")
        self._pa_sub = f"{bt.blinksSent} of {bt.blinksWanted} blinks - press again when stopped"
        self._pa_color = rl.Color(255, 200, 60, 255)
        self._pa_alert = True
        return True
      # Held after the pulse so the answer is readable without watching all four seconds.
      ok = bool(bt.lampSeen)
      self._pa_main = "SIGNAL WORKS" if ok else "SIGNAL DID NOT WORK"
      # THE COUNT, not just "it lit". A clean signal is about six flashes in four seconds; the
      # erratic case is many times that, and telling them apart by eye is what left this question
      # open across two drives. `after` is the tap measurement: flashes once we stopped commanding
      # are the car running its own one-touch pattern.
      if ok:
        self._pa_sub = f"{bt.flashes} flashes"
        if bt.flashesAfter:
          self._pa_sub += f", {bt.flashesAfter} on its own"
      else:
        self._pa_sub = "car ignored the request"
      self._pa_color = rl.Color(120, 220, 140, 255) if ok else rl.Color(255, 90, 90, 255)
      self._pa_alert = True
      return True
    return False

  def _draw_passing_assist(self, rect: rl.Rectangle) -> None:
    """BluePilot: glanceable panel under the speed. Off by default.

    Sized and colored so the three questions answer themselves in peripheral vision: a dim panel
    means running-but-idle, a filling bar means building toward a suggestion, and a bright panel
    with chevrons means it decided and which way. Nothing here instructs -- it reports what an
    observer that checks no approaching traffic would have said.
    """
    if not self._pa_main:
      return

    main_size = 52 if self._pa_alert else 40
    sub_size = 30
    main_dims = measure_text_cached(self._font_bold, self._pa_main, main_size)
    sub_dims = measure_text_cached(self._font_bold, self._pa_sub, sub_size) if self._pa_sub else rl.Vector2(0, 0)

    pad_x, pad_y = 36, 18
    content_w = max(main_dims.x, sub_dims.x)
    content_h = main_dims.y + (sub_dims.y + 6 if self._pa_sub else 0) + (14 if self._pa_progress > 0 else 0)
    panel_w = min(content_w + pad_x * 2, rect.width - 40)
    panel_h = content_h + pad_y * 2

    panel = rl.Rectangle(
      rect.x + rect.width / 2 - panel_w / 2,
      rect.y + SPEED_UNIT_CENTER_Y + FONT_SIZES.speed_unit,
      panel_w, panel_h,
    )

    bg_alpha = 210 if self._pa_alert else 130
    rl.draw_rectangle_rounded(panel, 0.25, 10, rl.Color(0, 0, 0, bg_alpha))
    if self._pa_alert:
      rl.draw_rectangle_rounded_lines_ex(panel, 0.25, 10, 3, self._pa_color)

    y = panel.y + pad_y
    rl.draw_text_ex(self._font_bold, self._pa_main,
                    rl.Vector2(panel.x + panel.width / 2 - main_dims.x / 2, y),
                    main_size, 0, self._pa_color)
    y += main_dims.y + 6

    if self._pa_sub:
      rl.draw_text_ex(self._font_bold, self._pa_sub,
                      rl.Vector2(panel.x + panel.width / 2 - sub_dims.x / 2, y),
                      sub_size, 0, rl.Color(200, 205, 210, 200))
      y += sub_dims.y + 6

    self._pa_panel_rect = panel

    if self._pa_progress > 0:
      # A bar answers "nearly there?" without reading digits, which a number never does at speed.
      bar_w = panel.width - pad_x * 2
      track = rl.Rectangle(panel.x + pad_x, y, bar_w, 8)
      rl.draw_rectangle_rounded(track, 1.0, 6, rl.Color(255, 255, 255, 50))
      fill = rl.Rectangle(track.x, track.y, max(8.0, bar_w * self._pa_progress), 8)
      rl.draw_rectangle_rounded(fill, 1.0, 6, self._pa_color)

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
