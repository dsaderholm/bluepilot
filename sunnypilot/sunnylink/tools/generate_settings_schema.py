#!/usr/bin/env python3
"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from __future__ import annotations

import base64
import datetime
import gzip
import copy
import json
import os
from collections.abc import Callable

from openpilot.sunnypilot.sunnylink.capabilities import CAPABILITY_FIELDS, CAPABILITY_LABELS

SCHEMA_VERSION = "1.0"
_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFINITION_PATH = os.path.join(_DIR, "settings_ui.json")
TORQUE_VERSIONS_PATH = os.path.normpath(
  os.path.join(_DIR, "..", "selfdrive", "controls", "lib", "latcontrol_torque_versions.json")
)


def _load_torque_versions() -> dict:
  """Load latcontrol_torque_versions.json so TorqueControlTune options stay in sync."""
  try:
    with open(TORQUE_VERSIONS_PATH) as f:
      return json.load(f)
  except (FileNotFoundError, json.JSONDecodeError):
    return {}


def _build_torque_options(versions: dict) -> list[dict]:
  options: list[dict] = [{"value": "", "label": "Default"}]
  parsed: list[tuple[float, str]] = []
  for label, info in versions.items():
    try:
      parsed.append((float(info["version"]), label))
    except (KeyError, TypeError, ValueError):
      continue
  for version, label in sorted(parsed, key=lambda kv: kv[0], reverse=True):
    options.append({"value": version, "label": label})
  return options


def _inject_dynamic_options(schema: dict) -> None:
  versions = _load_torque_versions()
  if not versions:
    return
  options = _build_torque_options(versions)

  def visitor(item: dict) -> None:
    if item.get("key") == "TorqueControlTune":
      item["options"] = options

  _walk_all_items(schema, visitor)


_definition_cache: tuple[tuple, dict] | None = None


def _load_definition() -> dict:
  """Load settings_ui.json and inject dynamic options sourced from runtime data files.

  CACHED on the file's identity, because every caller re-read, re-parsed and re-injected the whole
  definition. That was tolerable when it was 96 KB and stopped being tolerable at 133 KB: the
  capabilities the frontend asks for include `has_icbm` and `has_longitudinal_control`, and BOTH
  change when cruise engages -- so the heaviest work on this path lands exactly when the car is
  being driven. A comma 4 owner reported processes falling behind within about ten seconds of
  enabling cruise, which is that window.

  Keyed on (mtime, size) rather than a plain lru_cache so an update that rewrites the file is
  picked up without a restart, which is how this device takes new code.

  A deep copy is returned on every call. Callers MUTATE what they get -- generate_schema() stamps
  generated_at into it, sunnylinkd adds capabilities -- and handing out the cached dict would let
  one caller's additions leak into the next one's payload.
  """
  global _definition_cache
  try:
    st = os.stat(DEFINITION_PATH)
    key = (st.st_mtime_ns, st.st_size)
  except OSError:
    key = None

  if key is not None and _definition_cache is not None and _definition_cache[0] == key:
    return copy.deepcopy(_definition_cache[1])

  with open(DEFINITION_PATH) as f:
    schema = json.load(f)
  _inject_dynamic_options(schema)
  if key is not None:
    _definition_cache = (key, schema)
    return copy.deepcopy(schema)
  return schema


# Public API
def generate_schema() -> dict:
  """Return the settings_ui.json content augmented with runtime metadata.

  Adds three top-level fields the frontend consumes:
    - generated_at: ISO timestamp (drives schema-cache freshness checks)
    - capability_fields: declared CAPABILITY_FIELDS, used for rule validation
    - capability_labels: human-readable labels for capability_fields
  """
  schema = _load_definition()
  schema["generated_at"] = datetime.datetime.now(datetime.UTC).isoformat()
  schema["capability_fields"] = list(CAPABILITY_FIELDS)
  schema["capability_labels"] = dict(CAPABILITY_LABELS)
  return schema


def generate_schema_json() -> str:
  """Generate SettingsSchema as a compact JSON string."""
  return json.dumps(generate_schema(), separators=(",", ":"))


def generate_schema_compressed() -> str:
  """Generate SettingsSchema as gzip-compressed, base64-encoded string.

  Compression pipeline:
    1. JSON serialize (compact, no whitespace)
    2. UTF-8 encode
    3. gzip compress
    4. base64 encode
  """
  raw = json.dumps(generate_schema(), separators=(",", ":")).encode("utf-8")
  return base64.b64encode(gzip.compress(raw)).decode("utf-8")


# Schema introspection utilities
def _walk_rules(rules: list[dict] | None, visitor: Callable[[dict], None]) -> None:
  """Recursively walk all rules, calling visitor on each leaf rule."""
  if not rules:
    return
  for rule in rules:
    visitor(rule)
    if rule.get("type") == "not" and "condition" in rule:
      _walk_rules([rule["condition"]], visitor)
    elif rule.get("type") in ("any", "all") and "conditions" in rule:
      _walk_rules(rule["conditions"], visitor)


def _walk_all_items(schema: dict, visitor: Callable[[dict], None]) -> None:
  """Walk every item in the schema (panels, sections, sub_panels, sub_items, vehicle_settings)."""
  def _visit_item(item: dict) -> None:
    visitor(item)
    for sub in item.get("sub_items", []):
      _visit_item(sub)

  for panel in schema.get("panels", []):
    # Walk section items (V2)
    for section in panel.get("sections", []):
      for item in section.get("items", []):
        _visit_item(item)
      for sp in section.get("sub_panels", []):
        for item in sp.get("items", []):
          _visit_item(item)

    # Walk flat items (V1)
    for item in panel.get("items", []):
      _visit_item(item)
    for sp in panel.get("sub_panels", []):
      for item in sp.get("items", []):
        _visit_item(item)

  for brand_data in schema.get("vehicle_settings", {}).values():
    items = brand_data.get("items", []) if isinstance(brand_data, dict) else brand_data
    for item in items:
      _visit_item(item)


def collect_all_keys(schema: dict) -> set[str]:
  """Collect all param keys referenced in the schema (items + rules)."""
  keys: set[str] = set()

  def _visit_rule(rule: dict) -> None:
    if rule.get("type") in ("param", "param_compare") and "key" in rule:
      keys.add(rule["key"])

  def _visit_item(item: dict) -> None:
    if "key" in item:
      keys.add(item["key"])
    _walk_rules(item.get("visibility"), _visit_rule)
    _walk_rules(item.get("enablement"), _visit_rule)

  _walk_all_items(schema, _visit_item)
  return keys


def collect_capability_refs(schema: dict) -> set[str]:
  """Collect all capability field names referenced in rules."""
  refs: set[str] = set()

  def _visit_rule(rule: dict) -> None:
    if rule.get("type") == "capability" and "field" in rule:
      refs.add(rule["field"])

  def _visit_item(item: dict) -> None:
    _walk_rules(item.get("visibility"), _visit_rule)
    _walk_rules(item.get("enablement"), _visit_rule)

  _walk_all_items(schema, _visit_item)
  return refs


if __name__ == "__main__":
  # CLI: print schema for inspection
  schema = generate_schema()
  print(json.dumps(schema, indent=2))
  print(f"\nTotal panels: {len(schema.get('panels', []))}")
  print(f"Total vehicle brands: {len(schema.get('vehicle_settings', {}))}")
  keys = collect_all_keys(schema)
  print(f"Total unique param keys: {len(keys)}")

  # Show compression stats
  raw_json = json.dumps(schema, separators=(",", ":")).encode("utf-8")
  compressed = gzip.compress(raw_json)
  print(f"\nRaw JSON size: {len(raw_json):,} bytes")
  print(f"Compressed size: {len(compressed):,} bytes")
  print(f"Compression ratio: {len(compressed)/len(raw_json):.1%}")
