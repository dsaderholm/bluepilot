"""FusionPilot: the test stubs' param defaults must match what the device actually ships.

A stub that has drifted is worse than no stub. Every behavioral test in the passing-assist suite
runs against `_STUB_PARAM_DEFAULTS`, so if that says 2 where params_keys.h says 1, the whole suite
is green about a configuration no car will ever be in -- and the drive is the first thing that
disagrees.

This is not hypothetical. Lowering the confirmation from 2 s to 1 s left the stub on 2, and three
tests kept passing for the wrong reason until the stub was corrected by hand. Nothing would have
noticed if the numbers had happened to stay compatible.

Only the keys the stub actually declares are checked. It is a subset by design -- adding a param
should not require touching the stub unless a test needs it.
"""

import ast
import io
import re
from pathlib import Path

BASE = Path(__file__).resolve().parents[3]
KEYS_H = BASE / "common" / "params_keys.h"
STUB = BASE / "sunnypilot" / "selfdrive" / "controls" / "lib" / "tests" / "test_passing_assist.py"


def _shipped_defaults() -> dict[str, str]:
  """{name: default} for every key params_keys.h gives a literal default."""
  src = io.open(KEYS_H, encoding="utf-8").read()
  out = {}
  for name, _flags, kind, default in re.findall(
      r'\{"(\w+)",\s*\{([^,]+),\s*(\w+),\s*"([^"]*)"\}\}', src):
    out[name] = (kind, default)
  return out


def _stub_defaults() -> dict[str, object]:
  """The literal dict out of the test file, without importing it."""
  tree = ast.parse(io.open(STUB, encoding="utf-8").read())
  for node in ast.walk(tree):
    if isinstance(node, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "_STUB_PARAM_DEFAULTS" for t in node.targets):
      return ast.literal_eval(node.value)
  raise AssertionError("_STUB_PARAM_DEFAULTS not found -- this test would pass on anything")


def test_the_stub_mirrors_the_shipped_defaults():
  shipped = _shipped_defaults()
  stub = _stub_defaults()
  assert stub, "empty stub dict"

  wrong = {}
  for name, stub_value in stub.items():
    assert name in shipped, f"{name} is in the stub but not declared in params_keys.h"
    kind, default = shipped[name]
    want = float(default) if kind == "FLOAT" else int(default) if kind == "INT" else default
    if float(want) != float(stub_value):
      wrong[name] = (stub_value, want)

  assert not wrong, ("the stub has drifted from params_keys.h -- tests are green about a "
                     f"configuration no car ships: {wrong}")


def test_every_params_stub_accepts_block():
  """The real API is `put(self, key, dat, bool block = False)`.

  A stub without `block` raises TypeError when a caller passes it -- and every one of these callers
  wraps param writes in `except Exception: pass`, because a param failure must never reach the
  planner. So the write silently does nothing, the test harness reports a bug that exists only in
  the stub, and the fix gets applied to the wrong file. That happened: `block=True` on a disarm
  looked like it broke the blinker test.

  Checked by signature rather than by calling, so it holds for stubs no test happens to exercise.
  """
  # Scoped to the trees this fork's own features are tested in. bluepilot/ is the upstream
  # BluePilot layer -- its web portal has stubs with the same drift, and per CLAUDE.md an unrelated
  # upstream fault is reported there rather than patched here.
  OURS = ("sunnypilot", "opendbc_repo", "selfdrive")
  bad = []
  for path in BASE.rglob("test_*.py"):
    parts = path.relative_to(BASE).parts
    if not parts or parts[0] not in OURS or "openpilot" in parts or ".git" in parts:
      continue
    src = io.open(path, encoding="utf-8").read()
    if "def put(self" not in src:
      continue
    for node in ast.walk(ast.parse(src)):
      if isinstance(node, ast.FunctionDef) and node.name in ("put", "put_bool"):
        names = {a.arg for a in node.args.args} | {a.arg for a in node.args.kwonlyargs}
        if "block" not in names and node.args.kwarg is None:
          bad.append(f"{path.relative_to(BASE)}:{node.lineno} {node.name}")
  assert not bad, "params stubs missing `block`, so a blocking write silently no-ops: " + str(bad)
