"""
FusionPilot: the stored value behind a +/- control, cached and written through.

WHY THIS EXISTS, in the owner's words: "all my angle tuning got wiped in the FusionPilot settings,
and I also couldn't tweak it while driving."

One widget, three faults, and they compound into exactly that:

  1. _render called _get_value() EVERY FRAME. Four float controls in Angle Tuning, ten on the page,
     twenty-odd frames a second -- hundreds of Params reads per second off the eMMC while the
     device is also driving. That alone is why it felt dead onroad and fine parked.

  2. _set_value wrote with block=False, which is putNonBlocking, and the very next frame read the
     store back. The read beats the write. Press +, watch the number snap back to where it was:
     "I couldn't tweak it."

  3. _get_value returned self.min_value on ANY read failure -- and _increment reads, adds a step,
     and WRITES. So one press after a bad read persists the minimum. FordLowSpeedFactor_ang goes
     0.92 -> displays 0.50 -> one press stores 0.51. Nothing wiped his tuning; the widget showed
     him a wrong number and then his own press committed it.

So the rules here, each one aimed at a specific fault above:

  - THE CACHE IS AUTHORITATIVE FOR WHAT WE WROTE. A write updates it immediately and holds it for
    long enough that putNonBlocking has landed. The display follows the button, not the store.
  - The store is re-read only occasionally, so a value changed elsewhere (a defaults migration, the
    other panel) still turns up, without a disk read per frame.
  - A VALUE THAT HAS NEVER BEEN READ IS None, NOT A GUESS. Callers must refuse to increment it.
    You cannot add a step to a number you could not read, and min_value is not a safe stand-in --
    it is the most destructive value in range.
"""


class ParamValueCache:
  """Reads a numeric param rarely, writes it through, and admits when it does not know."""

  # Frames between re-reads of the store. ~3 s at 20 fps: often enough that a value changed
  # elsewhere appears while the page is open, rare enough to be nothing next to a render.
  REFRESH_FRAMES = 60

  # ...and how long our own write suppresses a re-read. put(block=False) is putNonBlocking, so the
  # store can still be serving the old value for a moment afterwards; re-reading inside that window
  # is precisely the race that made the number snap back. Comfortably longer than the write, well
  # under the refresh above.
  WRITE_HOLD_FRAMES = 20

  def __init__(self, params, key: str, integer: bool = False):
    self._params = params
    self._key = key
    self._integer = integer
    self._value: float | None = None
    self._frames = self.REFRESH_FRAMES     # first get() reads

  @property
  def known(self) -> bool:
    """Has the value ever been read or written successfully?

    False means the control must not offer to change it. See the module docstring -- incrementing
    an unknown value is the whole of how his angle tuning got overwritten.
    """
    return self._value is not None

  def get(self) -> float | None:
    """The current value, or None if it has never been read successfully."""
    self._frames += 1
    if self._frames >= self.REFRESH_FRAMES:
      self._frames = 0
      read = self._read()
      # A failed read does NOT discard a value we already have. The store being briefly unreadable
      # is not evidence the setting changed.
      if read is not None:
        self._value = read
    return self._value

  def set(self, value: float) -> None:
    """Write it, and believe it from this moment -- see WRITE_HOLD_FRAMES."""
    stored = int(round(value)) if self._integer else float(value)
    self._value = float(stored)
    self._frames = self.REFRESH_FRAMES - self.WRITE_HOLD_FRAMES
    try:
      self._params.put(self._key, stored, block=False)
    except Exception:  # noqa: BLE001 - a settings screen must not crash on a param write
      pass

  def _read(self) -> float | None:
    try:
      return float(self._params.get(self._key, return_default=True))
    except Exception:  # noqa: BLE001 - UnknownKeyName, TypeError and ValueError all mean the same
      return None      # thing here: we do not know, and must not pretend otherwise
