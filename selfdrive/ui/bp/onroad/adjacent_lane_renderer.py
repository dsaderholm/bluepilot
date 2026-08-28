"""
FusionPilot: on-road readout for the vehicle in the next lane over.

The lead already gets distance, speed and time on screen. This puts the same kind of answer over
the car you would be pulling out in front of -- the one number that decides whether moving over is
worth it, at the moment the decision is being made rather than afterwards in a log.

WHY IT READS THE PLANNER AND NOT THE RADAR
`liveTracks` is right there and it would be one more subscription. It is still the wrong source.
The planner has already picked the nearest vehicle per lane, rejected roadside furniture, and
required three consecutive radar messages before believing any of it -- and it publishes what it
decided. Re-deriving that here would put the lane band and the debounce in two places, where they
would drift apart quietly and the panel would start disagreeing with the suggestion it sits next to.

The UI already subscribes to longitudinalPlanSP for the passing-assist panel, so this costs nothing.

WHY THE COLOUR IS NOT COMPUTED HERE
Amber means "this is the lane currently stopping a pass", and it is read straight off blockedBy
rather than by re-testing the speed against the deficit margin. Same reason: one threshold, one
place. A display that applies its own version of the rule will eventually contradict the decision.

TWO THINGS ABOUT DRAWING IT
Position comes from the radar's own lateral estimate rather than an assumed lane center, so the
marker sits on the car. `_map_to_screen` wants the CAMERA frame, where y is left-NEGATIVE, and the
radar reports left-POSITIVE -- hence the flip, which is the same one `_update_leads` performs.

Radar messages arrive at about 8.3 Hz and the display runs at 20, so an unsmoothed marker visibly
steps. Each side keeps its own filters and resets them when that lane goes from empty to occupied,
because lerping from the previous car's position would drag the marker across the screen.

THE FLICKER, AND WHY IT IS FIXED HERE AND NOT IN THE DETECTOR
Two boundaries the detector reports honestly at 20 Hz, both of which look wrong on screen: a
vehicle sitting exactly on the pass threshold, and a track blinking in and out at the edge of
radar range. Neither is a detection fault -- see marker_hold.py, which holds and debounces the
picture while the decision and the log keep the raw per-frame truth.
"""

import pyray as rl

from openpilot.common.constants import CV
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.bp.onroad.marker_hold import MarkerHold, ONCOMING_HOLD_S


# How long the overlay keeps drawing while longitudinalPlanSP is flagged invalid but its publisher
# is still alive. Sized against DROPOUT_HOLD_S (0.8 s), which is what an ordinary radar dropout
# already gets: a comms flag should not be treated more harshly than losing the track itself.
# Beyond this the flag is not flickering, it is stuck, and a frozen overlay would misrepresent it.
INVALID_HOLD_S = 1.0
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached

# Deliberately not the lead's colors. A marker that looked like a lead would read as "openpilot is
# following that car", which is the opposite of what it means.
NEUTRAL = rl.Color(150, 205, 235, 255)     # a car is there, and it is not what is blocking a pass
BLOCKING = rl.Color(240, 175, 60, 255)     # this lane is why no pass is being suggested
# Oncoming gets its own color and is never confusable with the two above. It is not a lane you
# might move into, it is a lane that is theirs -- a different KIND of fact, not a worse version of
# the same one.
ONCOMING = rl.Color(235, 90, 80, 255)
BOX_FILL = rl.Color(30, 32, 36, 225)

FONT_SIZE = 44
PADDING = 10
BORDER = 3
# Nothing is drawn past this: at long range the lateral estimate is poor enough that the marker
# would sit on the wrong lane, which is worse than not drawing it.
MAX_DRAW_D_REL = 160.0



class AdjacentLaneRenderer:
  """Draws a compact speed readout over the nearest vehicle in each adjacent lane."""

  def __init__(self):
    self._font = gui_app.font(FontWeight.SEMI_BOLD)
    dt = 1 / gui_app.target_fps
    # Two slots, left and right. Filter time constants match the lead's: the same radar, the same
    # jitter, and a marker that settled at a different rate would look like a different sensor.
    self._d_filters = [FirstOrderFilter(0, 0.4, dt, initialized=False) for _ in range(2)]
    self._y_filters = [FirstOrderFilter(0, 0.5, dt, initialized=False) for _ in range(2)]
    # Dropout hold and color debounce, one per side. See marker_hold.py.
    self._holds = [MarkerHold(), MarkerHold()]
    # See ONCOMING_HOLD_S. Separate holds with a much shorter dropout, and no blocking debounce --
    # oncoming markers have one color and never change it.
    self._onc_holds = [MarkerHold(dropout_hold_s=ONCOMING_HOLD_S),
                       MarkerHold(dropout_hold_s=ONCOMING_HOLD_S)]
    self._onc_last: list = [None, None]
    # Last drawn values, so a held marker keeps showing the vehicle it belonged to rather than
    # freezing on whatever the filters happened to contain.
    self._last = [None, None]
    # See the valid/alive split in draw(). Seconds the message has been flagged invalid while its
    # publisher is still alive.
    self._invalid_s = 0.0

  def draw(self, sm, model_renderer, rect: rl.Rectangle) -> None:
    """Called from the model renderer, which owns the projection and the path geometry."""
    # NOT ALIVE IS THE CASE MarkerHold'S OWN RULE IS ABOUT: the publisher is gone, so whatever is
    # in the message belongs to the past and holding a car on screen from a dead source is the one
    # failure this must not have. Clear at once, exactly as before.
    if not sm.alive.get('longitudinalPlanSP', False):
      self._clear()
      self._invalid_s = 0.0
      return

    # INVALID IS A DIFFERENT THING AND WAS BEING TREATED AS THE SAME ONE. `valid` is
    # sm.all_checks() inside plannerd -- it goes False when some OTHER service plannerd subscribes
    # to fails a liveness or frequency check, and says nothing about this data. plannerd carries on
    # publishing passing assist at 20 Hz throughout, so the contents are current.
    #
    # Blanking on it made this overlay strobe at the flag's rate. He drove 1,000 miles with it, and
    # it read as passing assist malfunctioning when passing assist was the only thing on screen
    # honest enough to notice: nothing else is gated on that flag, so nothing else flinched.
    #
    # So a brief invalid run draws on, and a sustained one still clears -- if the flag is stuck,
    # something really is wrong and a frozen overlay would be a lie.
    if not sm.valid.get('longitudinalPlanSP', False):
      self._invalid_s += 1 / gui_app.target_fps
      if self._invalid_s >= INVALID_HOLD_S:
        self._clear()
        return
    else:
      self._invalid_s = 0.0

    try:
      pa = sm['longitudinalPlanSP'].passingAssist
      sides = (pa.adjacentLeft, pa.adjacentRight)
    except (KeyError, AttributeError):
      self._clear()
      return

    blocking = str(pa.blockedBy) == 'adjacentSlow'
    dt = 1 / gui_app.target_fps

    self._draw_oncoming(pa, model_renderer, rect, dt)

    for i, side in enumerate(sides):
      # Out of drawing range counts as not occupied, not as unavailable: the vehicle is real, we
      # simply will not place a marker that far out. That way it fades like any other dropout.
      occupied = bool(side.occupied) and side.dRel <= MAX_DRAW_D_REL
      draw, alpha, fresh = self._holds[i].update(dt, bool(side.available), occupied, blocking)

      if not draw:
        self._last[i] = None
        continue

      if occupied:
        # Reset rather than lerp when the marker is starting from nothing: the previous value
        # belongs to a different vehicle, and animating between them draws a car that never existed.
        if fresh:
          self._d_filters[i] = FirstOrderFilter(side.dRel, 0.4, dt)
          self._y_filters[i] = FirstOrderFilter(side.yRel, 0.5, dt)
        d_rel = self._d_filters[i].update(side.dRel)
        y_rel = self._y_filters[i].update(side.yRel)
        # Speed and distance are frozen at the last good frame while held. Nothing is extrapolated:
        # a held marker shows what was last actually measured, going dim, not a guess about where
        # the car has got to since.
        self._last[i] = (d_rel, y_rel, float(side.vAbs), float(side.dRel))
      elif self._last[i] is None:
        continue

      d_rel, y_rel, v_abs, d_text = self._last[i]
      point = model_renderer.project_ground_point(d_rel, y_rel)
      if point is None:
        continue

      self._draw_marker(point, v_abs, d_text, self._holds[i].blocking, alpha, rect)

  def _clear(self) -> None:
    self._invalid_s = 0.0
    for h in self._holds:
      h.reset()
    self._last = [None, None]
    # The oncoming holds go with them. A stale message or a dead service must not leave a marker
    # riding out its dropout hold over traffic nobody is still measuring.
    for h in self._onc_holds:
      h.reset()
    self._onc_last = [None, None]

  def _draw_oncoming(self, pa, model_renderer, rect: rl.Rectangle, dt: float) -> None:
    """Mark vehicles coming the other way, if the driver asked for them.

    Drawn from the LIVE sighting, never from the veto's memory. The veto deliberately outlives the
    car that caused it -- meeting someone tells you about the road, not just that moment -- but a
    marker floating over empty tarmac for the next ninety seconds would be a lie about where a
    vehicle is, and this exists precisely to be believed about position.

    The one exception is a quarter-second dropout hold, which is about the RADAR missing a message
    rather than about the veto's memory. See ONCOMING_HOLD_S for why it is a third of the
    same-direction one.
    """
    if not ui_state.params.get_bool("ShowOncomingSpeeds"):
      self._onc_last = [None, None]
      return
    for i, side in enumerate((pa.adjacentLeft, pa.adjacentRight)):
      # CORROBORATED, not the first return. See oncomingCorroborated in custom.capnp -- drawing
      # every sighting put 372 markers on a divided-highway drive where none could be real.
      live = bool(side.oncomingCorroborated) and 0 < side.oncomingDRel <= MAX_DRAW_D_REL
      # See ONCOMING_HOLD_S. `available` is passed through untouched, so a radar that has actually
      # gone away still clears the marker at once rather than holding a ghost -- a dropout and an
      # unavailable sensor must never look the same.
      draw, alpha, _ = self._onc_holds[i].update(dt, bool(side.available), live, False)
      if not draw:
        self._onc_last[i] = None
        continue
      if live:
        self._onc_last[i] = (side.oncomingDRel, side.oncomingYRel, abs(side.oncomingVAbs))
      if self._onc_last[i] is None:
        continue
      onc_d, onc_y, onc_v = self._onc_last[i]
      point = model_renderer.project_ground_point(onc_d, onc_y)
      if point is None:
        continue
      # Unfiltered, unlike the same-direction markers. A closing speed near 130 mph crosses the
      # whole range in a couple of seconds, so a smoothing filter tuned for a car being overtaken
      # would lag it badly enough to draw it somewhere it is not.
      self._draw_marker(point, onc_v, onc_d, False, alpha, rect,
                        color=ONCOMING, prefix="<< ")

  def _draw_marker(self, point, v_abs: float, d_rel: float, blocking: bool, alpha: float,
                   rect: rl.Rectangle, color=None, prefix: str = "") -> None:
    """One line: the vehicle's speed, then its distance. Speed first because it is the decision.

    Absolute speed, not closing rate. "62" is a number the driver can compare against their own
    without arithmetic; "-8" needs the set speed held in mind to mean anything.
    """
    conv = CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH
    dist = d_rel if ui_state.is_metric else d_rel * 3.28084
    unit = "m" if ui_state.is_metric else "ft"
    text = f"{prefix}{v_abs * conv:.0f}  |  {dist:.0f}{unit}"

    size = measure_text_cached(self._font, text, FONT_SIZE, 0)
    w, h = size.x + PADDING * 2, size.y + PADDING * 2
    x, y = point[0] - w / 2, point[1] - h / 2

    # Keep it on screen. A marker half off the edge is unreadable exactly when the vehicle is
    # closest and most worth reading.
    x = max(rect.x + 10, min(x, rect.x + rect.width - w - 10))
    y = max(rect.y + 10, min(y, rect.y + rect.height - h - 10))

    def fade(c: rl.Color) -> rl.Color:
      return rl.Color(c.r, c.g, c.b, int(c.a * alpha))

    color = color if color is not None else (BLOCKING if blocking else NEUTRAL)
    box = rl.Rectangle(int(x), int(y), w, h)
    rl.draw_rectangle_rounded(box, 0.3, 10, fade(BOX_FILL))
    rl.draw_rectangle_rounded_lines_ex(box, 0.3, 10, BORDER, fade(color))
    rl.draw_text_ex(self._font, text, rl.Vector2(int(x + PADDING + 2), int(y + PADDING + 2)),
                    FONT_SIZE, 0, fade(rl.Color(0, 0, 0, 180)))
    rl.draw_text_ex(self._font, text, rl.Vector2(int(x + PADDING), int(y + PADDING)),
                    FONT_SIZE, 0, fade(rl.Color(255, 255, 255, 255)))
