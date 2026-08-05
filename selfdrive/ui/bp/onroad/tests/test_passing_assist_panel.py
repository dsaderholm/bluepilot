"""
BluePilot: panel-state tests against REAL capnp messages.

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
