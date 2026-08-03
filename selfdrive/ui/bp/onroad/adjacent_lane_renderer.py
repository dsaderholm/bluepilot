"""
BluePilot: on-road readout for the vehicle in the next lane over.

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
Position comes from the radar's own lateral estimate rather than an assumed lane centre, so the
marker sits on the car. `_map_to_screen` wants the CAMERA frame, where y is left-NEGATIVE, and the
radar reports left-POSITIVE -- hence the flip, which is the same one `_update_leads` performs.

Radar messages arrive at about 8.3 Hz and the display runs at 20, so an unsmoothed marker visibly
steps. Each side keeps its own filters and resets them when that lane goes from empty to occupied,
because lerping from the previous car's position would drag the marker across the screen.
"""

import pyray as rl

from openpilot.common.constants import CV
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached

# Deliberately not the lead's colours. A marker that looked like a lead would read as "openpilot is
# following that car", which is the opposite of what it means.
NEUTRAL = rl.Color(150, 205, 235, 255)     # a car is there, and it is not what is blocking a pass
BLOCKING = rl.Color(240, 175, 60, 255)     # this lane is why no pass is being suggested
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
    self._was_occupied = [False, False]

  def draw(self, sm, model_renderer, rect: rl.Rectangle) -> None:
    """Called from the model renderer, which owns the projection and the path geometry."""
    if not sm.valid.get('longitudinalPlanSP', False):
      self._was_occupied = [False, False]
      return

    try:
      pa = sm['longitudinalPlanSP'].passingAssist
      sides = (pa.adjacentLeft, pa.adjacentRight)
    except (KeyError, AttributeError):
      self._was_occupied = [False, False]
      return

    blocking = str(pa.blockedBy) == 'adjacentSlow'

    for i, side in enumerate(sides):
      if not (side.available and side.occupied) or side.dRel > MAX_DRAW_D_REL:
        self._was_occupied[i] = False
        continue

      # Reset rather than lerp when the lane goes from empty to occupied: the previous value
      # belongs to a different vehicle, and animating between them draws a car that never existed.
      if not self._was_occupied[i]:
        self._d_filters[i] = FirstOrderFilter(side.dRel, 0.4, 1 / gui_app.target_fps)
        self._y_filters[i] = FirstOrderFilter(side.yRel, 0.5, 1 / gui_app.target_fps)
      self._was_occupied[i] = True

      d_rel = self._d_filters[i].update(side.dRel)
      y_rel = self._y_filters[i].update(side.yRel)

      point = model_renderer.project_ground_point(d_rel, y_rel)
      if point is None:
        continue

      self._draw_marker(point, side, blocking, rect)

  def _draw_marker(self, point, side, blocking: bool, rect: rl.Rectangle) -> None:
    """One line: the vehicle's speed, then its distance. Speed first because it is the decision.

    Absolute speed, not closing rate. "62" is a number the driver can compare against their own
    without arithmetic; "-8" needs the set speed held in mind to mean anything.
    """
    conv = CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH
    dist = side.dRel if ui_state.is_metric else side.dRel * 3.28084
    unit = "m" if ui_state.is_metric else "ft"
    text = f"{side.vAbs * conv:.0f}  |  {dist:.0f}{unit}"

    size = measure_text_cached(self._font, text, FONT_SIZE, 0)
    w, h = size.x + PADDING * 2, size.y + PADDING * 2
    x, y = point[0] - w / 2, point[1] - h / 2

    # Keep it on screen. A marker half off the edge is unreadable exactly when the vehicle is
    # closest and most worth reading.
    x = max(rect.x + 10, min(x, rect.x + rect.width - w - 10))
    y = max(rect.y + 10, min(y, rect.y + rect.height - h - 10))

    color = BLOCKING if blocking else NEUTRAL
    box = rl.Rectangle(int(x), int(y), w, h)
    rl.draw_rectangle_rounded(box, 0.3, 10, BOX_FILL)
    rl.draw_rectangle_rounded_lines_ex(box, 0.3, 10, BORDER, color)
    rl.draw_text_ex(self._font, text, rl.Vector2(int(x + PADDING + 2), int(y + PADDING + 2)),
                    FONT_SIZE, 0, rl.Color(0, 0, 0, 180))
    rl.draw_text_ex(self._font, text, rl.Vector2(int(x + PADDING), int(y + PADDING)),
                    FONT_SIZE, 0, rl.Color(255, 255, 255, 255))
