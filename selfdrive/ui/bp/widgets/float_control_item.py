import pyray as rl
from collections.abc import Callable

from openpilot.common.params import Params
from openpilot.selfdrive.ui.bp.widgets.param_value_cache import ParamValueCache
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.list_view import ListItem, ItemAction
from openpilot.system.ui.widgets.button import Button, ButtonStyle
from openpilot.system.ui.widgets.label import gui_label
from openpilot.system.ui.lib.application import FontWeight, gui_app, MousePos
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached

ITEM_TEXT_FONT_SIZE = 50
ITEM_TEXT_COLOR = rl.WHITE
ITEM_TEXT_VALUE_COLOR = rl.Color(170, 170, 170, 255)
BUTTON_SIZE = 80
BUTTON_SPACING = 7  # Reduced from 20 to ~1/3 (33/3 ≈ 11, but we need some space, so 7)


class FloatControlAction(ItemAction):
  """Action item for float controls with +/- buttons."""
  
  def __init__(self, param: str, min_value: float, max_value: float, step: float,
               callback: Callable[[float], None] | None = None, enabled: bool | Callable[[], bool] = True,
               suffix: str = "", integer: bool = False):
    super().__init__(width=0, enabled=enabled)  # Width 0 means use full width
    self.param = param
    self.min_value = min_value
    self.max_value = max_value
    self.step = step
    self.callback = callback
    self.suffix = suffix
    self.integer = integer
    self.params = Params()
    # See param_value_cache.py. Every read and write of this setting goes through it: the store is
    # read rarely rather than every frame, our own write is believed immediately rather than being
    # raced by the next read, and a value that could not be read stays None rather than becoming
    # min_value. Those three together were "all my angle tuning got wiped ... and I also couldn't
    # tweak it while driving" -- one bug wearing two faces.
    self._cache = ParamValueCache(self.params, param, integer=integer)
    
    # Create +/- buttons
    self._minus_button = Button(
      "-",
      click_callback=self._decrement,
      font_size=50,
      button_style=ButtonStyle.NORMAL,
      border_radius=10
    )
    self._plus_button = Button(
      "+",
      click_callback=self._increment,
      font_size=50,
      button_style=ButtonStyle.PRIMARY,
      border_radius=10
    )
    
    self._font = gui_app.font(FontWeight.NORMAL)
  
  def _get_value(self) -> float | None:
    """Current value, or None if the store has never given us one.

    None rather than min_value, and every caller below has to cope with it. The old fallback
    returned the single most destructive value in range on a transient read failure.
    """
    return self._cache.get()

  def _set_value(self, value: float):
    """Set parameter value."""
    # Clamp to min/max
    value = max(self.min_value, min(self.max_value, value))
    self._cache.set(value)
    if self.callback:
      self.callback(value)

  def _step_by(self, delta: float):
    """Nudge by one step -- and do NOTHING if we could not read what we are nudging.

    This is the line that stops a bad read from being committed as a real setting.
    """
    current = self._get_value()
    if current is None:
      return
    self._set_value(current + delta)

  def _increment(self):
    self._step_by(self.step)

  def _decrement(self):
    self._step_by(-self.step)

  def _decimals(self) -> int:
    """Infer display precision from step size so small steps are shown correctly."""
    if self.integer:
      return 0
    if self.step < 0.001:
      return 4
    if self.step < 0.01:
      return 3
    if self.step < 0.1:
      return 2
    return 1

  def _value_text(self, current_value: float | None) -> str:
    # An unreadable setting SAYS SO. Printing a number we do not have is what made a display fault
    # look to him like a settings change.
    if current_value is None:
      return "--"
    if self.suffix == "V":
      return f"{current_value:.1f}{self.suffix}"
    return f"{current_value:.{self._decimals()}f}{self.suffix}"

  def _render(self, rect: rl.Rectangle) -> bool:
    current_value = self._get_value()
    value_text = self._value_text(current_value)
    
    # Calculate layout - reduce spacing to 1/3 of original
    # Original was BUTTON_SIZE (80) + BUTTON_SPACING (20) = 100 per side
    # Target is ~33 per side, so BUTTON_SPACING reduced to ~7
    button_y = rect.y + (rect.height - BUTTON_SIZE) / 2
    
    # Check if enabled (handle callable) - use the enabled property from ItemAction
    is_enabled = self.enabled
    
    # Calculate total space needed: buttons + minimal spacing
    total_button_space = BUTTON_SIZE * 2 + BUTTON_SPACING * 2
    # Value width should be just enough for the text, not the full remaining space
    value_text_width = measure_text_cached(self._font, value_text, ITEM_TEXT_FONT_SIZE).x
    # Add small padding around text
    value_width = value_text_width + 20
    
    # Right-justify the controls (like toggle switches)
    total_width = total_button_space + value_width
    RIGHT_PADDING = 20
    start_x = rect.x + rect.width - total_width - RIGHT_PADDING
    
    # Minus button on left
    minus_rect = rl.Rectangle(start_x, button_y, BUTTON_SIZE, BUTTON_SIZE)
    self._minus_button.set_enabled(current_value is not None and is_enabled
                                   and current_value > self.min_value)
    self._minus_button.render(minus_rect)
    
    # Value in center
    value_rect = rl.Rectangle(
      start_x + BUTTON_SIZE + BUTTON_SPACING,
      rect.y,
      value_width,
      rect.height
    )
    gui_label(
      value_rect,
      value_text,
      font_size=ITEM_TEXT_FONT_SIZE,
      color=ITEM_TEXT_VALUE_COLOR,
      font_weight=FontWeight.NORMAL,
      alignment=rl.GuiTextAlignment.TEXT_ALIGN_CENTER,
      alignment_vertical=rl.GuiTextAlignmentVertical.TEXT_ALIGN_MIDDLE
    )
    
    # Plus button on right
    plus_rect = rl.Rectangle(start_x + BUTTON_SIZE + BUTTON_SPACING + value_width + BUTTON_SPACING, button_y, BUTTON_SIZE, BUTTON_SIZE)
    self._plus_button.set_enabled(current_value is not None and is_enabled
                                  and current_value < self.max_value)
    self._plus_button.render(plus_rect)
    
    return False
  
  def _handle_mouse_release(self, mouse_pos: MousePos):
    """Handle mouse clicks on buttons."""
    value_text = self._value_text(self._get_value())
    
    button_y = self._rect.y + (self._rect.height - BUTTON_SIZE) / 2
    
    # Calculate button positions (same as in _render)
    value_text_width = measure_text_cached(self._font, value_text, ITEM_TEXT_FONT_SIZE).x
    value_width = value_text_width + 20
    total_button_space = BUTTON_SIZE * 2 + BUTTON_SPACING * 2
    total_width = total_button_space + value_width
    RIGHT_PADDING = 20
    start_x = self._rect.x + self._rect.width - total_width - RIGHT_PADDING
    
    minus_rect = rl.Rectangle(start_x, button_y, BUTTON_SIZE, BUTTON_SIZE)
    if rl.check_collision_point_rec(mouse_pos, minus_rect):
      self._minus_button._handle_mouse_release(mouse_pos)
      return
    
    plus_rect = rl.Rectangle(start_x + BUTTON_SIZE + BUTTON_SPACING + value_width + BUTTON_SPACING, button_y, BUTTON_SIZE, BUTTON_SIZE)
    if rl.check_collision_point_rec(mouse_pos, plus_rect):
      self._plus_button._handle_mouse_release(mouse_pos)
      return
    
    super()._handle_mouse_release(mouse_pos)


def float_control_item(title: str | Callable[[], str], description: str | Callable[[], str] | None = None,
                       param: str = "", min_value: float = 0.0, max_value: float = 1.0, step: float = 0.05,
                       callback: Callable[[float], None] | None = None, enabled: bool | Callable[[], bool] = True,
                       icon: str = "", suffix: str = "") -> ListItem:
  """Create a list item with a float +/- stepper backed by a FLOAT param."""
  action = FloatControlAction(param, min_value, max_value, step, callback, enabled, suffix, integer=False)
  return ListItem(title=title, description=description, action_item=action, icon=icon)


def int_control_item(title: str | Callable[[], str], description: str | Callable[[], str] | None = None,
                     param: str = "", min_value: int = 0, max_value: int = 100, step: int = 1,
                     callback: Callable[[float], None] | None = None, enabled: bool | Callable[[], bool] = True,
                     icon: str = "") -> ListItem:
  """Create a list item with an integer +/- stepper backed by an INT param."""
  action = FloatControlAction(param, min_value, max_value, step, callback, enabled, suffix="", integer=True)
  return ListItem(title=title, description=description, action_item=action, icon=icon)
