"""Every Parked* param the code uses must be in the compiled params_keys.h.

params.json is only metadata; Params rejects any key that is not in params_keys.h. This is what a fake Params
in the other tests cannot catch, and it would crash the recorder on the device.
"""
import re
from pathlib import Path

import json

ROOT = Path(__file__).resolve().parents[2]
NAME = re.compile(r'"(Parked(?:Recording|BatteryLog)\w*)"')


def registered() -> set[str]:
  text = (ROOT / "common" / "params_keys.h").read_text()
  return set(re.findall(r'\{"(Parked(?:Recording|BatteryLog)\w*)"\s*,', text))


def used_in_code() -> set[str]:
  names = set()
  for path in (ROOT / "bluepilot" / "parked_recorder").glob("*.py"):
    if path.name.startswith("test_"):
      continue
    names |= set(NAME.findall(path.read_text()))
  names |= set(NAME.findall((ROOT / "system" / "manager" / "process_config.py").read_text()))
  names |= set(NAME.findall((ROOT / "system" / "hardware" / "power_monitoring.py").read_text()))
  return names


def test_every_param_used_is_registered():
  missing = used_in_code() - registered()
  assert not missing, f"not in common/params_keys.h: {sorted(missing)}"


def test_every_param_in_json_is_registered():
  data = json.loads((ROOT / "bluepilot" / "params" / "params.json").read_text())
  names = {p["name"] for p in data["params"] if NAME.fullmatch(f'"{p["name"]}"')}
  assert names, "expected Parked* entries in params.json"
  assert not names - registered(), f"in params.json but not params_keys.h: {sorted(names - registered())}"


def test_registered_keys_are_all_used():
  # catches a typo in params_keys.h (a registered name that nothing reads)
  assert registered() - used_in_code() == set()
