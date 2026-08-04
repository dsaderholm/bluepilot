"""
BluePilot: settings descriptions that state a fact about the code.

A description saying "off by default" or "defaults to 1 s" is an assertion, and nothing keeps it
true. The panel already shipped a label reading "Below 40 mph" months after that floor moved to 30
and became adjustable -- confidently false, invisible to every behavioural test, because it was
never behaviour.

Descriptions rot the same way and are read at exactly the moment a driver is deciding whether to
trust the feature. So any claim about a default is checked against params_keys.h.

Deliberately narrow. It does not try to read English; it looks for the handful of phrasings that
assert a default and verifies those. A description making a claim in some form not listed here goes
unchecked, which is a gap rather than a false assurance.
"""

import ast
import re
from pathlib import Path

import pytest

ROOT = next(d for d in Path(__file__).resolve().parents if (d / "common" / "params_keys.h").exists())
LAYOUTS = sorted(
  p for p in (ROOT / "selfdrive" / "ui").rglob("*settings*.py")
  if "passing_assist" in p.name or "lane_change" in p.name
)


def _defaults() -> dict[str, str]:
  src = (ROOT / "common" / "params_keys.h").read_text(encoding="utf-8", errors="replace")
  return {m.group(1): m.group(2) for m in
          re.finditer(r'\{"(\w+)",\s*\{[^}]*?,\s*\w+,\s*"([^"]*)"\}', src)}


def _items(path: Path):
  """(param, description) for every settings control in a layout."""
  for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
    if not (isinstance(n, ast.Call) and getattr(n.func, "id", "") in ("toggle_item_sp", "option_item_sp")):
      continue
    kw = {k.arg: k.value for k in n.keywords}
    param = getattr(kw.get("param"), "value", None)
    if not isinstance(param, str) or "description" not in kw:
      continue
    text = " ".join(re.findall(r"'([^']*)'", ast.unparse(kw["description"])))
    yield param, text


@pytest.mark.parametrize("path", LAYOUTS, ids=lambda p: p.name)
def test_default_claims_match_the_code(path):
  defaults = _defaults()
  bad = []
  for param, text in _items(path):
    actual = defaults.get(param)
    if actual is None:
      continue
    low = text.lower()
    if re.search(r"\boff by default\b", low) and actual not in ("0", ""):
      bad.append(f"{param}: says off by default, is {actual!r}")
    if re.search(r"\bon by default\b", low) and actual != "1":
      bad.append(f"{param}: says on by default, is {actual!r}")
    for m in re.finditer(r"\bdefaults? (?:to|is) (-?\d+)", low):
      if m.group(1) != actual:
        bad.append(f"{param}: says default {m.group(1)}, is {actual!r}")
  assert not bad, "settings descriptions contradicting params_keys.h:\n  " + "\n  ".join(bad)


@pytest.mark.parametrize("path", LAYOUTS, ids=lambda p: p.name)
def test_every_described_param_exists(path):
  """A description attached to a param that no longer exists is worse than a stale claim -- it
  reads as documentation of a control the driver cannot find."""
  defaults = _defaults()
  missing = [p for p, _ in _items(path) if p not in defaults]
  assert not missing, f"described but not declared: {missing}"
