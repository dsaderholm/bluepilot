"""
FusionPilot: params.put() must pass the type the key is registered as.

Params enforces this at write time. PYTHON_2_CPP maps (python type, key type) pairs and raises
TypeError for anything else -- notably (str, INT) is NOT a valid pair, so writing "1" to an INT key
fails. That is easy to do because every param's DEFAULT in params_keys.h is written as a string.

It cost a drive: the blinker test button wrote str(int(side)) to an INT key, the TypeError was
caught and logged, and the button silently did nothing. The same mistake in _disarm meant the
state machine could never be re-armed either.

Static check over literal writes, so it needs no device and no compiled Params.
"""

import ast
import pathlib
import re

import pytest

def _repo_root() -> pathlib.Path:
  """Walk up to the checkout root rather than counting parents -- the count is easy to get
  wrong and fails as a confusing FileNotFoundError rather than as the check itself."""
  for d in pathlib.Path(__file__).resolve().parents:
    if (d / "common" / "params_keys.h").exists():
      return d
  raise RuntimeError("repo root not found")


ROOT = _repo_root()
KEYS_H = ROOT / "common" / "params_keys.h"

SOURCES = [
  "selfdrive/ui/bp/layouts/settings/bluepilot.py",
  # The FordBlinkerTest write moved out of bluepilot.py and this list had to move with it --
  # that write to an INT key is the exact bug this file exists for.
  "selfdrive/ui/sunnypilot/layouts/settings/steering_sub_layouts/blinker_settings.py",
  "selfdrive/ui/sunnypilot/layouts/settings/cruise.py",
  "opendbc_repo/opendbc/sunnypilot/car/ford/blinker_test_ext.py",
  "sunnypilot/selfdrive/controls/lib/passing_assist.py",
  "selfdrive/ui/bp/onroad/hud_renderer_bp.py",
  # Writes LaneChangeStats from inside modeld, where a TypeError would be swallowed by the guard
  # that keeps param faults out of the model process -- so a mismatch here is silent by design and
  # this list is the only thing that would catch it.
  "sunnypilot/selfdrive/controls/lib/auto_lane_change.py",
  "sunnypilot/system/params_migration.py",
]

# python type -> the key types Params will accept it for
# Mirrors PYTHON_2_CPP in common/params_pyx.pyx: the pairs Params can actually cast. Anything not
# listed there raises TypeError at the write.
ALLOWED = {
  str: {"STRING"},
  bool: {"BOOL"},
  int: {"INT"},
  float: {"FLOAT"},
  bytes: {"BYTES"},
  dict: {"JSON"},
  list: {"JSON"},
}


@pytest.fixture(scope="module")
def key_types() -> dict[str, str]:
  src = KEYS_H.read_text(encoding="utf-8", errors="replace")
  return {m.group(1): m.group(2)
          for m in re.finditer(r'\{"(\w+)",\s*\{[^}]*?,\s*(STRING|BOOL|INT|FLOAT|BYTES|TIME|JSON)\b', src)}


@pytest.mark.parametrize("path", SOURCES)
def test_literal_put_types_match_registration(path, key_types):
  tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
  bad = []

  for node in ast.walk(tree):
    if not isinstance(node, ast.Call):
      continue
    fname = getattr(node.func, "attr", None)
    if fname not in ("put", "put_bool") or len(node.args) < 2:
      continue
    keynode = node.args[0]
    if not (isinstance(keynode, ast.Constant) and isinstance(keynode.value, str)):
      continue
    key = keynode.value
    ktype = key_types.get(key)
    if ktype is None:
      bad.append((node.lineno, key, "key is not registered in params_keys.h"))
      continue

    if fname == "put_bool":
      if ktype != "BOOL":
        bad.append((node.lineno, key, f"put_bool on a {ktype} key"))
      continue

    val = node.args[1]
    # Only literals and obvious calls are decidable statically; skip anything else.
    py = None
    if isinstance(val, ast.Constant):
      py = type(val.value)
    elif isinstance(val, ast.Call) and getattr(val.func, "id", None) in ("str", "int", "float", "bool"):
      py = {"str": str, "int": int, "float": float, "bool": bool}[val.func.id]
    # A dict or list LITERAL was invisible here -- neither is an ast.Constant, so both fell through
    # to "cannot decide statically" and were skipped. The two drive summaries are exactly that
    # shape, and their writes sit inside a catch, so a mismatch would not raise anywhere: the
    # summary would simply never be written and the drive would end with nothing recorded.
    elif isinstance(val, ast.Dict):
      py = dict
    elif isinstance(val, (ast.List, ast.ListComp)):
      py = list
    if py is None:
      continue

    if ktype not in ALLOWED.get(py, set()):
      bad.append((node.lineno, key,
                  f"writes {py.__name__} to a {ktype} key -- Params has no ({py.__name__}, {ktype}) cast"))

  assert not bad, "\n".join(f"  {path}:{ln}  {k}: {why}" for ln, k, why in bad)
