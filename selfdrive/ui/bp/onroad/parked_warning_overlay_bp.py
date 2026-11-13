import pyray as rl

from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget
from bluepilot.parked_recorder.warning_bp import flash_frame
from bluepilot.parked_recorder.warning_screen_bp import WarningScreenState

TEXT = "RECORDING"
TEXT_SIZE = 150


class ParkedWarningOverlayBP(Widget):
  """Full-screen police-then-white flash with RECORDING, shown while the parked recorder's ParkedRecordingWarning
  is set and the car is parked. Drawn over every screen. The device wakes at full brightness while it is up."""

  def __init__(self, params):
    super().__init__()
    self.state = WarningScreenState(lambda: params.get_bool("ParkedRecordingWarning"))
    self.active = False
    self._font = gui_app.font(FontWeight.BOLD)

  def update_active(self, parked: bool) -> bool:
    self.active = self.state.update(parked)
    return self.active

  def _render(self, rect: rl.Rectangle):
    if not self.active:
      return
    (r, g, b), _ = flash_frame(self.state.elapsed())
    rl.draw_rectangle_rec(rect, rl.Color(r, g, b, 255))
    # Black text on white or amber, white on the dark colours; chosen from the background so it always reads.
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    color = rl.BLACK if luma > 140 else rl.WHITE
    size = measure_text_cached(self._font, TEXT, TEXT_SIZE)
    pos = rl.Vector2(rect.x + (rect.width - size.x) / 2, rect.y + (rect.height - size.y) / 2)
    rl.draw_text_ex(self._font, TEXT, pos, TEXT_SIZE, 0, color)
