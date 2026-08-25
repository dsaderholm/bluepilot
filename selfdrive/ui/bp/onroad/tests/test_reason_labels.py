"""FusionPilot: every suggestion REASON must render as its own words.

`Blocked` codes have had `test_blocked_labels.py` since the panel was written -- if a reason has no
wording it falls through to the raw enum name and somebody sees "driverChangedLanes" at 70 mph.
**`Reason` codes never got the equivalent, and that gap cost something the same day route intent
added the third one.**

`Reason.exitLane` landed on `Side.right`, and the renderer's chain was:

    reason == keepRight   -> "MOVE RIGHT  >>>"
    suggestion == left    -> "<<<  PASS LEFT"
    else                  -> "PASS RIGHT  >>>"

so an exit-lane suggestion fell to the else and rendered as **PASS RIGHT, in the green of a real
overtake** -- telling him he was being offered a pass when he was being told to get into his exit
lane. The comment directly above that chain says, in the file, that keepRight and passing are both
Side.right and mean opposite things and that is why the reason is spelled out rather than inferred.
The third reason was added without extending it.

**The failure mode is worse than a missing label.** An unmapped Blocked code renders as an ugly
enum name and is obvious. An unmapped Reason renders as a DIFFERENT, PLAUSIBLE, CONFIDENT
instruction. Nothing about the screen looks wrong.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = next(d for d in Path(__file__).resolve().parents if (d / "common" / "params_keys.h").exists())
HUD = (ROOT / "selfdrive" / "ui" / "bp" / "onroad" / "hud_renderer_bp.py").read_text(encoding="utf-8")
CAPNP = (ROOT / "cereal" / "custom.capnp").read_text(encoding="utf-8")


# The reason the dispatch chain FALLS THROUGH to, named here rather than inferred.
#
# `passing` is the default and legitimately has no explicit comparison -- it is what the chain does
# when no other reason matched. Naming it is the whole point of this file: the bug was a new reason
# quietly inheriting this branch, so the exemption has to be a deliberate line somebody edits,
# not a silence. Adding a second name here should be an argument, not a convenience.
DEFAULT_REASON = "passing"


def reasons() -> list[str]:
  """Every Reason that must be told apart: all of them except `none` (no suggestion is being made)
  and the documented fall-through."""
  body = CAPNP[CAPNP.index("  struct PassingAssist {"):]
  block = body[body.index("enum Reason {"):]
  found = re.findall(r"^\s*(\w+) @\d+;", block[:block.index("}")], re.M)
  return [n for n in found if n not in ("none", DEFAULT_REASON)]


def _string_comparisons_against(tree: ast.AST) -> set[str]:
  """Every string literal the renderer compares something to. That is how it dispatches on a
  reason -- `str(pa.reason) == 'keepRight'` -- so a reason absent from this set is a reason the
  renderer cannot distinguish."""
  out: set[str] = set()
  for n in ast.walk(tree):
    if isinstance(n, ast.Compare):
      for side in [n.left, *n.comparators]:
        if isinstance(side, ast.Constant) and isinstance(side.value, str):
          out.add(side.value)
  return out


def test_every_reason_is_distinguished_by_the_renderer():
  """A reason the renderer never compares against cannot produce its own words -- it inherits
  whichever branch it happens to fall into, and that branch is a confident sentence about a
  different maneuver."""
  compared = _string_comparisons_against(ast.parse(HUD))
  missing = [r for r in reasons() if r not in compared]
  assert not missing, (
    f"reasons the panel cannot tell apart: {missing}. Each will render as whatever branch it falls "
    "through to -- for a Side.right reason that is 'PASS RIGHT', in the green of a real overtake.")


def test_the_suggestion_line_handles_every_reason_before_falling_back_to_a_side():
  """Structural, and aimed at the specific shape that failed.

  The dispatch chain must test every reason BEFORE it starts inferring from the side, or a new
  reason silently becomes a pass. This checks the reason comparisons all appear ahead of the
  side-based fallback in the same chain.
  """
  i = HUD.index("if suggesting:")
  chain = HUD[i:i + 1800]
  side_fallback = chain.index("suggestion == 'left'")
  for r in reasons():
    where = chain.find(f"'{r}'")
    assert where != -1, f"{r} is not handled in the suggestion chain at all"
    assert where < side_fallback, (
      f"{r} is tested AFTER the side fallback, so a {r} suggestion on the right renders as a pass")
