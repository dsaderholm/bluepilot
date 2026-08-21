"""
FusionPilot: panel-state tests against REAL capnp messages.

Companion to test_acc_status_fields.py and written for the same reason. The passing-assist suite
builds its messages from SimpleNamespace with plain Python values, so every enum it feeds the code
is a str and every count is an int. A live capnp message hands back _DynamicEnum instead, which
behaves differently enough to crash-loop the UI -- int() on one raises TypeError, which is exactly
what took the display down in the ACC readout.

These build actual messages so that class of bug cannot hide. The str()-returns-the-bare-name
behavior the panel depends on is pinned explicitly rather than assumed.
"""

import ast
import pathlib

import pytest

from cereal import custom

HUD_SRC = pathlib.Path(__file__).parents[1] / "hud_renderer_bp.py"


def blocked_text_keys() -> set[str]:
  """Read _BLOCKED_TEXT from source rather than importing the renderer.

  Importing it drags in the whole UI stack -- pyray, ui_state, compiled Params -- which is not
  available off-device and is far more machinery than a completeness check needs. Parsing keeps
  this runnable everywhere, including in CI, and the thing being checked is whether a key exists,
  which the source answers directly.
  """
  tree = ast.parse(HUD_SRC.read_text(encoding="utf-8"))
  for node in tree.body:
    if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "_BLOCKED_TEXT":
      return {k.value for k in node.value.keys}
  raise AssertionError("_BLOCKED_TEXT not found in hud_renderer_bp.py")


def make_plan(**kw):
  """A real longitudinalPlanSP, read back through a reader like the UI does."""
  msg = custom.LongitudinalPlanSP.new_message()
  pa = msg.passingAssist
  for k, v in kw.items():
    setattr(pa, k, v)
  return msg.as_reader().passingAssist


class TestLiveEnumBehaviour:
  def test_str_returns_the_bare_name(self):
    """The panel maps blockedBy through a dict keyed on bare names. If str() ever returned a repr
    instead, every state would fall through to the raw-name branch and read as gibberish."""
    pa = make_plan(suggestion='left', blockedBy='nothingSlower', reason='passing')
    assert str(pa.suggestion) == 'left'
    assert str(pa.blockedBy) == 'nothingSlower'
    assert str(pa.reason) == 'passing'

  def test_int_on_a_live_enum_raises(self):
    """Pins the trap itself, not just our avoidance of it -- this is what crash-looped the ACC
    readout, and the panel must never grow an int() on an enum field."""
    pa = make_plan(suggestion='left')
    with pytest.raises(TypeError):
      int(pa.suggestion)

  def test_numeric_fields_are_still_plain_numbers(self):
    # commanded/overtakeMsg etc. are UInt8, not enums: int() on these is safe and used.
    pa = make_plan(confirmSeconds=12.5, overtakeMsg=2, keepRightSeconds=3.0)
    assert int(pa.overtakeMsg) == 2
    assert pa.confirmSeconds == pytest.approx(12.5)


class TestEveryStateRenders:
  @pytest.mark.parametrize("blocked", [e for e in
                                       custom.LongitudinalPlanSP.PassingAssist.Blocked.schema.enumerants
                                       if e != 'none'])
  def test_every_blocked_state_has_plain_text(self, blocked):
    """A state with no mapping would render its enum name to the driver. Checked against the
    schema so adding a state to the capnp without adding text fails here."""
    pa = make_plan(blockedBy=blocked)
    assert str(pa.blockedBy) in blocked_text_keys(), f"{blocked} would render as a raw enum name"

  @pytest.mark.parametrize("side,reason", [('left', 'passing'), ('right', 'passing'),
                                           ('right', 'keepRight')])
  def test_suggestion_combinations_read_back(self, side, reason):
    pa = make_plan(suggestion=side, reason=reason)
    assert str(pa.suggestion) == side
    assert str(pa.reason) == reason


class TestRearApproachOverCapnp:
  def test_defaults_are_unavailable_not_clear(self):
    """The distinction the whole interface exists for, checked on the wire rather than in Python."""
    msg = custom.LongitudinalPlanSP.new_message()
    pa = msg.as_reader().passingAssist
    assert not pa.rearLeft.available
    assert not pa.rearRight.available
    assert str(pa.rearLeft.source) == 'none'

  def test_source_round_trips(self):
    msg = custom.LongitudinalPlanSP.new_message()
    msg.passingAssist.rearLeft.source = 'radar'
    msg.passingAssist.rearRight.source = 'blis'
    pa = msg.as_reader().passingAssist
    assert str(pa.rearLeft.source) == 'radar'
    assert str(pa.rearRight.source) == 'blis'


def _load_fn(name):
  """Pull one pure function out of hud_renderer_bp without importing it.

  The renderer needs pyray, which does not load offline on every platform, so every guard in this
  folder reads the source instead. This compiles a single top-level def and nothing else -- the
  function under test has no imports of its own, which is what makes it safe to lift.
  """
  tree = ast.parse(HUD_SRC.read_text(encoding="utf-8"))
  fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name), None)
  assert fn is not None, f"{name} not found -- this test would pass on anything"
  ns: dict = {}
  exec(compile(ast.Module(body=[fn], type_ignores=[]), "<hud>", "exec"), ns)  # noqa: S102
  return ns[name]


_suggested_deficit = _load_fn("_suggested_deficit")


class TestTheSettingSuggestion:
  """The drive already measured exactly how wrong the speed bar was -- these are passes HE made
  that the bar alone refused -- and then stopped one step short of saying what to set it to.

  Every competitor that adapts to its driver does it silently: Hyundai's HDA2 machine-learns ACC
  habits, Tesla folds it into a speed profile, and neither tells you what it changed. This says the
  number and writes nothing, which is the same rule params_migration.py runs on.
  """

  def test_it_names_a_lower_bar_when_real_passes_were_refused(self):
    assert _suggested_deficit(2.4, 4.0) == 2.0

  def test_it_rounds_DOWN_not_to_nearest(self):
    """2.6 rounded to nearest is 3, which still refuses the 2.6 mph passes it was derived from --
    a recommendation that does not fix the thing it was measured from is worse than silence."""
    assert _suggested_deficit(2.6, 4.0) == 2.0

  def test_it_says_nothing_when_the_bar_is_already_low_enough(self):
    assert _suggested_deficit(3.0, 3.0) is None
    assert _suggested_deficit(4.5, 4.0) is None

  def test_it_does_not_recommend_removing_the_threshold(self):
    """Rounding 0.6 down gives 0, and a bar of zero is not a threshold -- it is every car ahead
    counting as slower, which is a different feature and not one anybody asked for."""
    assert _suggested_deficit(0.6, 4.0) is None

  def test_nothing_measured_means_nothing_suggested(self):
    assert _suggested_deficit(0.0, 4.0) is None
    assert _suggested_deficit(2.0, 0.0) is None


class _C:
  """Stands in for rl.Color and keeps its channels, because the ALPHA is now the whole signal."""
  def __init__(self, r, g, b, a):
    self.r, self.g, self.b, self.a = r, g, b, a


ACCENT = _C(191, 148, 228, 255)      # what the stub passes as _pa_color


def _load_strip():
  """Lift the two lane-strip methods and drive them against a RECORDING raylib.

  The preview renders these to PNG and a human looks at it, which is how the layout is judged --
  but a picture cannot assert which box was filled versus outlined, and that distinction is the
  entire meaning of the strip. This drives the shipped drawing code with a fake rl and reads back
  the calls, so "the middle lanes went blank" is a failing test rather than a road report.
  """
  tree = ast.parse(HUD_SRC.read_text(encoding="utf-8"))
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "HudRendererBP")
  wanted = ("_lane_strip_worth_drawing", "_draw_lane_strip")
  methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
  assert len(methods) == len(wanted), f"{wanted} moved -- this test would pass on anything"

  calls: list = []

  class FakeRl:
    Color = _C
    Rectangle = staticmethod(lambda x, y, w, h: ("rect", x, y, w, h))

    @staticmethod
    def draw_rectangle_rounded(box, _r, _seg, color):
      # EVERY box is a filled rectangle now -- one shape, three brightnesses -- so the call shape
      # says nothing at all and the color carries the entire meaning. Reading only the call would
      # let every test here pass on a strip that claimed every lane at once.
      if (color.r, color.g, color.b) == (ACCENT.r, ACCENT.g, ACCENT.b):
        calls.append(("fill" if color.a == 255 else "maybe", box))
      else:
        calls.append(("empty", box))

    @staticmethod
    def draw_rectangle_rounded_lines_ex(box, *a):
      raise AssertionError("the strip must not outline anything any more -- one shape, three "
                           "brightnesses. See the note in _draw_lane_strip.")

  # The box geometry lives at module level so the preview and the car cannot drift apart. Lift it
  # rather than restating it -- a test carrying its own copy stops testing the shipped size, which
  # is the whole reason the first strip shipped too small to read.
  consts = [n for n in tree.body
            if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "").startswith("LANE_")]
  assert consts, "LANE_* strip constants moved -- this test would pass on anything"

  ns = {"rl": FakeRl}
  exec(compile(ast.Module(body=consts, type_ignores=[]), "<hud>", "exec"), ns)  # noqa: S102
  exec(compile(ast.Module(body=methods, type_ignores=[]), "<hud>", "exec"), ns)  # noqa: S102
  return ns, calls


class TestTheLaneStrip:
  """He reported it, 2026-08-19: *"it had the outlined box on the left lane when I was in the left
  lane, but then when I went in the middle lane it had all boxes empty."*

  Both halves were honest. From a middle lane the right road edge is out of reach, and the outer
  left line is PRESENT because a lane really is there, so both original witnesses went silent
  together. The four-line bound is what speaks in that gap.
  """

  def _draw(self, lanes_total, lane_index, no_lane_left, bound):
    import types
    ns, calls = _load_strip()
    stub = types.SimpleNamespace(_pa_lanes_total=lanes_total, _pa_lane_index=lane_index,
                                 _pa_no_lane_left=no_lane_left, _pa_lane_bound=bound,
                                 _pa_color=ACCENT)
    stub._lane_strip_worth_drawing = lambda: ns["_lane_strip_worth_drawing"](stub)
    panel = types.SimpleNamespace(x=0.0, width=600.0)
    ns["_draw_lane_strip"](stub, panel, 0.0)
    # Boxes are drawn left to right, so the Nth call is lane (n - 1 - N). Return per-lane kinds.
    kinds = [k for k, _ in calls]
    return list(reversed(kinds))          # index 0 = lane 0 = the RIGHTMOST box

  def test_the_middle_of_five_is_no_longer_blank(self):
    """THE REPORT. Bounded to 1..3, so those three dim and the two ends stay gray."""
    kinds = self._draw(5, -1, False, (1, 3))
    assert kinds == ["empty", "maybe", "maybe", "maybe", "empty"], \
      "a range dims its candidates and never brightens one -- a range is not a position"

  def test_a_pinned_lane_fills_exactly_one_box(self):
    kinds = self._draw(3, 1, False, (1, 1))
    assert kinds == ["empty", "fill", "empty"]

  def test_the_leftmost_witness_still_shows_with_no_bound(self):
    """Reachable when the map gave a lane count but the outer RIGHT line was unreadable."""
    kinds = self._draw(5, -1, True, (-1, -1))
    assert kinds == ["empty", "empty", "empty", "empty", "maybe"]

  def test_unknown_draws_the_strip_with_nothing_claimed(self):
    """An absent strip cannot be told from a feature that is switched off, so it still draws."""
    kinds = self._draw(5, -1, False, (-1, -1))
    assert len(kinds) == 5
    assert kinds == ["empty"] * 5

  def test_no_lane_count_draws_nothing_at_all(self):
    assert self._draw(0, -1, False, (-1, -1)) == []

  def test_a_range_never_outranks_a_measured_index(self):
    """The edge placed us; the bound merely agrees. Only the measurement may show at full."""
    kinds = self._draw(5, 0, False, (1, 3))
    assert kinds[0] == "fill"
    assert "maybe" not in kinds, "a bound must not also dim once the lane is placed"

  def test_nothing_is_ever_outlined_any_more(self):
    """One shape, three brightnesses. The recorder raises on an outline call, so this pins the
    decision rather than leaving the old two-shape design reachable by a future edit."""
    for case in ((5, -1, False, (1, 3)), (3, 1, False, (1, 1)), (5, -1, True, (-1, -1))):
      self._draw(*case)


class TestTheLateAgreementReachesTheScreen:
  """This fork's oldest recurring fault is a value computed correctly and never rendered, and the
  late-agreement count was the fifth instance -- written to the history JSON and nowhere else,
  while the panel kept showing the strict count that motivated the change.

  These assert the WIRE and the LINE, because the bug was never in the arithmetic.
  """

  def test_the_fields_exist_on_the_wire(self):
    pa = make_plan(driverPassesAgreedLate=4, driverPassLateDelay=11.5)
    assert pa.driverPassesAgreedLate == 4
    assert pa.driverPassLateDelay == pytest.approx(11.5)

  def test_the_summary_line_names_them(self):
    """Reads the shipped source rather than the docstring: the string has to be built from the
    field, or the number is on the wire and still invisible."""
    src = HUD_SRC.read_text(encoding="utf-8")
    assert "driverPassesAgreedLate" in src, "the panel must read the field, not just the schema"
    assert "driverPassLateDelay" in src, "the delay is what a future AGREE_WINDOW_S is read off"

  def test_it_is_drawn_beside_the_strict_count_not_instead_of_it(self):
    """They answer different questions -- strict supports the lead-time claim, late says whether
    the decision was right -- so replacing one with the other would lose a real measurement."""
    src = HUD_SRC.read_text(encoding="utf-8")
    i_strict = src.index("driverPassesAgreed")
    i_late = src.index("driverPassesAgreedLate")
    assert i_strict < i_late, "the strict count must still be rendered"


_rear_caveat = _load_fn("_rear_caveat")


class TestTheRearCaveat:
  """What the panel says about rear coverage, which is the only place the driver learns that the
  thing behind him was checked by a sensor that cannot see anything approaching.

  This is a lifted function rather than a rendered scene on purpose: the preview supplies the
  sub-line as a literal string, so it proves the line FITS and proves nothing about which line is
  chosen. Choosing is where this was wrong.
  """

  def test_nothing_fitted_says_so(self):
    assert _rear_caveat(False, 'none', False, 'none') == "no rear data"

  def test_radar_on_both_sides_says_nothing(self):
    assert _rear_caveat(True, 'radar', True, 'radar') == ""

  def test_a_CLEAR_radar_lane_still_counts_as_radar(self):
    """The panel half of the source-on-empty bug. A live radar watching an empty road must not be
    reported as a downgrade -- it is the configuration the whole feature is for."""
    assert _rear_caveat(True, 'radar', True, 'radar') == ""

  def test_blis_only_is_named_as_a_downgrade(self):
    """It must NOT fall through to silence. Wiring BLIS makes both sides available, so a bare
    availability test drops the caveat the day the canbox lands and the screen reads as properly
    rear-checked by a sensor that cannot see a car closing from two hundred feet back."""
    assert _rear_caveat(True, 'blis', True, 'blis') == "rear: blind spot only"

  def test_one_radar_side_does_not_launder_the_other(self):
    # The permissive combiner, one level down: radar on the left must not silence a BLIS right.
    assert _rear_caveat(True, 'radar', True, 'blis') == "rear: blind spot only"
    assert _rear_caveat(True, 'blis', True, 'radar') == "rear: blind spot only"

  def test_an_uncovered_side_is_named_rather_than_averaged_away(self):
    """RearApproach.available is left OR right, and may_actuate already had to learn that one
    working sensor answers yes for both sides. The panel must not repeat it."""
    assert _rear_caveat(False, 'none', True, 'radar') == "no rear data left"
    assert _rear_caveat(True, 'radar', False, 'none') == "no rear data right"

  def test_an_uncovered_side_outranks_the_blis_downgrade(self):
    # No coverage at all is the more serious of the two, so it is the one that gets the line.
    assert _rear_caveat(False, 'none', True, 'blis') == "no rear data left"
