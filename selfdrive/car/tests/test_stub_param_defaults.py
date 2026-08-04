"""BluePilot: the test stubs' param defaults must match what the device actually ships.

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
