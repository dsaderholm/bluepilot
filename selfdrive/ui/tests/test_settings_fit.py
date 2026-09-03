"""FusionPilot: does every settings control actually fit on the screen?

Reported from the car on 2026-08-04: the "By Limit" speed-limit offset option was off the right
edge and unreachable. Adding a fourth button to a row sized for three took it from 1350 px to 1800,
and MultipleButtonActionSP lays a row out as len(buttons) * button_width -- no wrapping, no fit
check, no clipping. It simply drew past the edge of the display.

Nothing we had could catch it. The offline suite does not render, and the HUD preview tool only
covers the onroad corner. This is the cheap substitute for rendering a settings screen: parse the
layout files, find every fixed-width button row, and multiply. It cannot tell you whether a screen
looks good, but it can tell you when a control is unreachable, which is the failure that actually
stranded a setting.

Static on purpose. Instantiating a settings layout drags in ui_state, Params, gui_app, fonts and
textures; the arithmetic that broke is visible in the source without any of that.
"""
import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[3]

# selfdrive/ui/layouts/settings/settings.py: the panel is the screen minus the sidebar, and
# list_view.py insets each item by ITEM_PADDING on both sides.
SCREEN_WIDTH = 2160
SIDEBAR_WIDTH = 500
ITEM_PADDING = 20
MAX_ROW_WIDTH = SCREEN_WIDTH - SIDEBAR_WIDTH - ITEM_PADDING * 2  # 1620

SETTINGS_DIRS = [
  REPO / "selfdrive" / "ui" / "sunnypilot" / "layouts" / "settings",
  REPO / "selfdrive" / "ui" / "bp" / "layouts" / "settings",
]


def _module_lists(tree: ast.Module) -> dict[str, int]:
  """Module-level list literals, by name, with their length."""
  out = {}
  for node in tree.body:
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.List):
      for target in node.targets:
        if isinstance(target, ast.Name):
          out[target.id] = len(node.value.elts)
  return out


def _button_rows(path: pathlib.Path):
  """(line, n_buttons, button_width) for every fixed-width multi-button row in the file."""
  tree = ast.parse(path.read_text(encoding="utf-8"))
  lists = _module_lists(tree)
  rows = []
  for node in ast.walk(tree):
    if not isinstance(node, ast.Call):
      continue
    name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
    if name not in ("multiple_button_item_sp", "multiple_button_item"):
      continue
    kw = {k.arg: k.value for k in node.keywords}
    width = kw.get("button_width")
    buttons = kw.get("buttons")
    if not isinstance(width, ast.Constant) or not isinstance(width.value, int):
      continue  # width comes from a variable; not statically checkable, and none do today
    if isinstance(buttons, ast.List):
      count = len(buttons.elts)
    elif isinstance(buttons, ast.Name) and buttons.id in lists:
      count = lists[buttons.id]
    else:
      continue
    rows.append((node.lineno, count, width.value))
  return rows


def _all_rows():
  found = []
  for d in SETTINGS_DIRS:
    if not d.is_dir():
      continue
    for path in sorted(d.rglob("*.py")):
      for lineno, count, width in _button_rows(path):
        found.append((path.relative_to(REPO), lineno, count, width))
  return found


def test_some_rows_were_found():
  """A parser that silently matches nothing would pass this file forever."""
  assert _all_rows(), "found no button rows to check -- the AST matching has drifted"


@pytest.mark.parametrize("row", _all_rows(), ids=lambda r: f"{r[0].name}:{r[1]}")
def test_button_row_fits_on_screen(row):
  path, lineno, count, width = row
  total = count * width
  assert total <= MAX_ROW_WIDTH, (
    f"{path}:{lineno} lays out {count} buttons x {width} px = {total} px, which is "
    f"{total - MAX_ROW_WIDTH} px wider than the {MAX_ROW_WIDTH} px available. The row does not "
    f"wrap or clip -- the rightmost buttons render off the edge and cannot be tapped. "
    f"Use button_width <= {MAX_ROW_WIDTH // count}."
  )


# -----------------------------------------------------------------------------------------------
# DESCRIPTION LENGTH IS NOT A FAILURE MODE ON THIS SCREEN. A test asserting it was written on
# 2026-09-02 and DELETED THE SAME HOUR, because the repo refuted its own premise.
#
# The road report was "the UI is now messed up near where I change the lane centering", and the day
# before, three descriptions there had been added or rewritten at 466, 364 and 308 characters when
# nothing on THAT screen exceeded 308. That looked like a clean story. The bound was set at 308 --
# the longest string known to have shipped without complaint -- and the test then failed on nine
# OTHER descriptions, including 815, 548 and 412 characters on the ICBM cruise screen. Those have
# shipped on his car for weeks and he has never once mentioned them.
#
# So 308 was never a ceiling; it was just the largest value on one screen. Raising the bound to 815
# to make the test pass would have been fitting the threshold to the data it was supposed to judge,
# which is exactly the "a guessed bound freezes the guess" failure recorded in CLAUDE.md for
# MAPD_V2_STALL_S. The honest conclusion is that the hypothesis is dead: 815 characters renders
# acceptably, therefore 466 was not what he saw.
#
# The descriptions were left shortened -- the 466-character one was rambling and the information it
# carries fits in 205 -- but that is EDITING, not a fix, and it must not be reported as one.
#
# HIS REPORT REMAINS UNDIAGNOSED, and the shape of it is why: "it looks fine now but didn't look
# right when driving." A static layout problem cannot be intermittent. Two theories about ListItem
# were also built and withdrawn by reading it properly -- `_parse_description` DOES update
# `_prev_description`, and the row height IS recomputed after `_update_state`. Settings screens
# cannot be rendered offline, so the next step is a screenshot or the UI's own swaglog, not another
# static check.


def test_the_dynamic_descriptions_still_carry_the_flat_point():
  """Shortening must not throw the number away. The flat point is the one thing on that screen a
  CAN-FD owner cannot work out for themselves, and it is why those descriptions are dynamic."""
  src = (REPO / "selfdrive" / "ui" / "bp" / "layouts" / "settings" / "bluepilot.py").read_text(
    encoding="utf-8")
  for fn in ("_high_speed_factor_description", "_dampening_description"):
    body = src.split("def " + fn, 1)[1].split("  def ", 1)[0]
    assert "{flat:.2f}" in body, f"{fn} no longer renders the flat point"
    assert "format(flat=flat)" in body, f"{fn} no longer formats it in"


def test_bp_tests_are_registered():
  """FusionPilot: a test file the runner never collects is worse than no test at all.

  test_settings_recommend_defaults.py was written, passed when run by name, and was not in
  DEFAULT_TARGETS -- so the suite total did not move and nothing anywhere said why. The only reason
  it was caught is that the count was expected to go up and did not.

  selfdrive/ui/tests/ cannot be globbed (raylib and device deps live beside these), so new files
  there must be added by name, and "add it by name" is exactly the step that gets forgotten. This
  scans for test files carrying the FusionPilot marker in their module docstring and checks each one
  is covered by a DEFAULT_TARGETS entry -- a name or a parent directory.
  """
  runner = REPO / "tools" / "bp_offline_test.py"
  tree = ast.parse(runner.read_text(encoding="utf-8"), filename=str(runner))
  targets: list[str] = []
  for node in ast.walk(tree):
    if isinstance(node, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "DEFAULT_TARGETS" for t in node.targets):
      targets = [e.value for e in node.value.elts
                 if isinstance(e, ast.Constant) and isinstance(e.value, str)]
  assert targets, "could not read DEFAULT_TARGETS; this guard has gone stale"

  def covered(rel: str) -> bool:
    return any(rel == t or rel.startswith(t.rstrip("/") + "/") for t in targets)

  missing = []
  for path in REPO.rglob("test_*.py"):
    if any(p in {".git", "__pycache__", "node_modules"} for p in path.parts):
      continue
    try:
      mod = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
      continue
    doc = ast.get_docstring(mod)
    # Both markers: files new to this fork say FusionPilot, and files that MODIFY an upstream file
    # keep FusionPilot, because that marker sits alongside FusionPilot's own 316 identical ones and
    # separating them would be churn in exactly the files merges land in.
    if not doc or ("FusionPilot" not in doc and "FusionPilot" not in doc):
      continue
    # Must actually contain tests. bluepilot/test_web_routes.py is named like a test file and
    # carries the marker, but it is a hand-run script with no test functions -- pytest collects
    # nothing from it, so demanding it be registered would be demanding a no-op.
    has_tests = any(isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name.startswith("test_")
                    for n in ast.walk(mod))
    if not has_tests:
      continue
    rel = path.relative_to(REPO).as_posix()
    if not covered(rel):
      missing.append(rel)

  assert not missing, (
    f"FusionPilot test files the runner never collects: {sorted(missing)}. Add each to "
    "DEFAULT_TARGETS in tools/bp_offline_test.py -- by name if its directory holds tests that "
    "need the device.")
