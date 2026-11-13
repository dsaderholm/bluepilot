"""The passing-assist overlay must not blank on a single invalid frame.

He drove 1,000 miles watching this overlay strobe while the rest of the UI was untouched, and
reasonably concluded passing assist was broken. It was not: both renderers bailed out on
`sm.valid['longitudinalPlanSP']`, and nothing else on the screen reads that flag, so this overlay
was the only thing that flinched when it toggled.

`valid` is plannerd's own sm.all_checks() -- it goes False when some OTHER service plannerd
subscribes to fails a liveness or frequency check, while plannerd carries on publishing passing
assist at 20 Hz. The data is current. `alive` is the different, real case: the publisher is gone,
and MarkerHold's own rule says a marker from a dead source must go at once.

Parsed rather than grepped because every explanation of this bug contains the words `valid` and
`alive`, so a text search matches the comments that describe the fix as readily as the code.
"""
import ast

ADJ = "selfdrive/ui/bp/onroad/adjacent_lane_renderer.py"
HUD = "selfdrive/ui/bp/onroad/hud_renderer_bp.py"


def _fn(path, name):
  tree = ast.parse(open(path, encoding="utf-8").read())
  for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == name:
      return node
  raise AssertionError(f"{name} not found in {path}")


def _guard_on(node, attr):
  """Top-level `if not sm.<attr>.get('longitudinalPlanSP', ...)` statements in a function body."""
  out = []
  for st in node.body:
    if not isinstance(st, ast.If):
      continue
    for sub in ast.walk(st.test):
      if isinstance(sub, ast.Attribute) and sub.attr == attr:
        for c in ast.walk(st.test):
          if isinstance(c, ast.Constant) and c.value == "longitudinalPlanSP":
            out.append(st)
            break
        break
  return out


def _calls_clear(stmts):
  return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "_clear"
             for st in stmts for n in ast.walk(st))


def test_a_dead_publisher_still_clears_at_once():
  """MarkerHold's rule: a marker from a source that is gone must never be held."""
  draw = _fn(ADJ, "draw")
  alive = _guard_on(draw, "alive")
  assert alive, "adjacent_lane_renderer.draw no longer checks sm.alive"
  assert _calls_clear(alive[0].body), "the not-alive path must clear immediately"


def test_an_invalid_flag_does_not_clear_on_its_own():
  """The bug: `valid` False cleared instantly, so the overlay strobed at the flag's rate."""
  draw = _fn(ADJ, "draw")
  valid = _guard_on(draw, "valid")
  assert valid, "adjacent_lane_renderer.draw no longer checks sm.valid"
  body = valid[0].body
  # A clear is allowed, but only behind a further condition (the hold expiring) -- never as a
  # direct statement of this branch.
  direct = [st for st in body if not isinstance(st, ast.If)]
  assert not _calls_clear(direct), \
    "the invalid path clears without a hold -- this is the flashing bug"
  assert any(isinstance(st, ast.If) for st in body), \
    "the invalid path must guard its clear behind a sustained-invalid check"


def test_the_alive_check_comes_first():
  """Ordering matters: an invalid-but-dead publisher must take the clear path, not the hold."""
  draw = _fn(ADJ, "draw")
  idx = {id(s): i for i, s in enumerate(draw.body)}
  a = _guard_on(draw, "alive")[0]
  v = _guard_on(draw, "valid")[0]
  assert idx[id(a)] < idx[id(v)], "sm.alive must be checked before sm.valid"


def test_the_hud_panel_got_the_same_split():
  """The panel is a second consumer of the same flag and had the identical bug."""
  src = open(HUD, encoding="utf-8").read()
  tree = ast.parse(src)
  found = False
  for node in ast.walk(tree):
    if isinstance(node, ast.Attribute) and node.attr == "alive":
      found = True
  assert found, "hud_renderer_bp no longer distinguishes alive from valid"
  assert "PA_INVALID_HOLD_FRAMES" in src, "the panel's hold constant is gone"
