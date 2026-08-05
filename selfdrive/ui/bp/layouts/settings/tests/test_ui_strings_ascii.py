"""
BluePilot: user-facing UI strings must be plain ASCII.

An em dash in a settings description made that item overlap the one below it. Nothing crashed and
nothing logged -- the glyph is not in the UI font, so measure_text returned a width that did not
match what was drawn, the wrap calculation came out short, and the row was sized too small.

Typographic characters are the failure mode here specifically because they look correct in an
editor and in a diff. em dash, en dash, middot, curly quotes and ellipsis all render fine
everywhere except the one place that matters.

Scoped to strings that reach a screen: everything passed to tr(), and every string literal in the
onroad HUD. Comments are unrestricted -- they are never measured or drawn.
"""

import ast
import pathlib

import pytest

def _repo_root() -> pathlib.Path:
  """Walk up to the checkout root rather than counting parents -- the count is easy to get
  wrong and fails as a confusing FileNotFoundError rather than as the check itself."""
  for d in pathlib.Path(__file__).resolve().parents:
    if (d / "common" / "params_keys.h").exists():
      return d
  raise RuntimeError("repo root not found")


ROOT = _repo_root()

# (path, mode) -- "tr" checks only strings inside tr(...), "all" checks every string literal
TARGETS = [
  ("selfdrive/ui/bp/layouts/settings/bluepilot.py", "tr"),
  ("selfdrive/ui/sunnypilot/layouts/settings/cruise.py", "tr"),
  ("selfdrive/ui/sunnypilot/layouts/settings/steering_sub_layouts/blinker_settings.py", "tr"),
  ("selfdrive/ui/bp/onroad/hud_renderer_bp.py", "all"),
]

# Characters that are easy to type by habit and wrong in every case here.
COMMON = {
  0x2014: "em dash (use --)",
  0x2013: "en dash (use -)",
  0x00b7: "middot (use -)",
  0x2018: "curly quote", 0x2019: "curly quote",
  0x201c: "curly quote", 0x201d: "curly quote",
  0x2026: "ellipsis (use ...)",
  0x00a0: "non-breaking space",
}


def offenders(path: str, mode: str):
  tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
  nodes = []
  if mode == "tr":
    for n in ast.walk(tree):
      if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "tr":
        nodes += [c for c in ast.walk(n) if isinstance(c, ast.Constant) and isinstance(c.value, str)]
  else:
    nodes = [n for n in ast.walk(tree)
             if isinstance(n, ast.Constant) and isinstance(n.value, str)]

  out = []
  for n in nodes:
    for ch in n.value:
      if ord(ch) > 127:
        out.append((n.lineno, hex(ord(ch)), COMMON.get(ord(ch), "non-ASCII"), n.value[:60]))
  return out


@pytest.mark.parametrize("path,mode", TARGETS)
def test_no_non_ascii_in_rendered_strings(path, mode):
  bad = offenders(path, mode)
  assert not bad, "\n".join(
    f"  {path}:{ln}  {code} {why}  in {txt!r}" for ln, code, why, txt in bad)
