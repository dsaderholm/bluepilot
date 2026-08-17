"""FusionPilot: mapd v2's own settings -- what they are, and why none of them is a control here.

`MapdSettings` has been declared in `params_keys.h` since v2 landed and **nothing ever read or wrote
it**, and no process ever published `mapdIn`. This fills the first half: mapd's own account of its
configuration is cached into that param every tick, so "what was mapd set to on that drive" is
answerable from the device rather than only from a route that happened to log it.

The write path is built and deliberately carries NOTHING. That is a conclusion, not an omission.

WHAT WAS MEASURED, 2026-08-18, route 00000383
---------------------------------------------
Across **6,725 path points, every single one** satisfies

    targetVelocity^2 * |curvature| = 2.200

which is `/personalities/standard/map_curve_target_lat_a` exactly. So mapd computes the path's
corner speeds as `v = sqrt(a_lat / curvature)` and nothing else, using the STANDARD personality
(`subscriber/shadow_selfdrive_state` is False, so it never sees openpilot's).

Three things follow, and they are why no dial ships:

1. **`targetVelocity` carries no information `curvature` does not.** It is a pure geometric
   transform. SCC-Map at `MapdV2` 2 is therefore fed the same KIND of number v1 gave it, at 27
   points instead of one. **The value of v2's path is the CURVATURE PROFILE, not the velocities** --
   which is the opposite of how the migration was framed, and it is what the exit-ramp work should
   be built on.
2. **`map_curve_target_lat_a` and `SmartCruiseControlMapFactor` are one knob applied twice.**
   `v = sqrt(a_lat / k) * factor`. Shipping mapd's as a second control would be two settings for one
   behaviour, which "keep only additions that earn their place" exists to prevent. If the two are
   ever consolidated, mapd's is the better one -- it is dimensionally a lateral acceleration rather
   than a dimensionless multiplier on somebody else's constant -- but that is a swap, not an add.
3. **`curve_target_speed_time_offset` does not reach SCC-Map at all.** SCC-Map walks the path and
   does its own trigger arithmetic, so mapd's earliness offset only shifts mapd's OWN controller
   output, which this fork does not consume. It was written up here as the answer to "the exit that
   never slows enough" and **it is not** -- that lever is still ours, in the walk.

`_FLOAT_SETTINGS` is therefore empty. The mechanism is proven and one entry turns it back on; what
is missing is a REASON, and inventing one to justify the code would be the mistake.

WHAT IS PERMANENTLY REFUSED
---------------------------
`*_speed_control_enabled` are never written. mapd's own longitudinal controllers would be a second
opinion beside SCC and SLA, and `suggestedSpeed` is already banned by `test_mapd_schema.py` for
exactly that reason. All three personalities would be written together if anything ever were, since
which one mapd reads is not observable from here.
"""
from __future__ import annotations

import json

import cereal.messaging as messaging
from cereal import custom
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

MapdInputType = custom.MapdInputType

# The personalities mapd ships. Written together -- see the docstring.
_PERSONALITIES = ("relaxed", "standard", "aggressive")

# Empty on purpose -- see the docstring. Format is param -> (mapd leaf name, scale), and the
# machinery below is exercised by tests so that adding an entry is a one-line change rather than a
# rediscovery.
_FLOAT_SETTINGS: dict[str, tuple[str, float]] = {}

# How many times to re-send a value mapd has not adopted before giving up for this boot. A setting
# that will not stick is a real condition worth seeing in the log rather than retrying forever.
MAX_ATTEMPTS = 5

# Floats compared with a tolerance below the smallest step the controls can express.
_EPS = 0.01


class MapdSettingsSync:
  """Bring mapd's settings in line with ours, then leave it alone.

  Reads `mapdExtendedOut.settings` (mapd's own account of its state) and writes through `mapdIn`.
  Pure of side effects until something actually differs.
  """

  def __init__(self, params: Params | None = None):
    self.params = params or Params()
    self.sm = messaging.SubMaster(['mapdExtendedOut'])
    self.pm = messaging.PubMaster(['mapdIn'])
    self.attempts: dict[str, int] = {}
    self.settled: set[str] = set()
    self._logged_unreadable = False

  def _desired(self) -> dict[str, float]:
    """The value we want at each mapd JSON path, expanded across the personalities."""
    out: dict[str, float] = {}
    for key, (leaf, scale) in _FLOAT_SETTINGS.items():
      try:
        raw = self.params.get(key, return_default=True)
      except Exception:  # noqa: BLE001 -- an unknown key must not take mapd_manager down
        continue
      if raw is None:
        continue
      value = float(raw) * scale
      for personality in _PERSONALITIES:
        out[f"/personalities/{personality}/{leaf}"] = value
    return out

  @staticmethod
  def _lookup(blob: dict, path: str):
    node = blob
    for part in path.strip("/").split("/"):
      if not isinstance(node, dict) or part not in node:
        return None
      node = node[part]
    return node

  def _send(self, path: str, value: float) -> None:
    msg = messaging.new_message('mapdIn')
    msg.valid = True
    mapd_in = msg.mapdIn
    mapd_in.type = MapdInputType.setJsonPathFloat
    mapd_in.jsonPath = path
    mapd_in.float = float(value)
    self.pm.send('mapdIn', msg)

  def _save(self) -> None:
    msg = messaging.new_message('mapdIn')
    msg.valid = True
    msg.mapdIn.type = MapdInputType.saveSettings
    self.pm.send('mapdIn', msg)

  def tick(self) -> None:
    """One pass. Safe to call at any rate; it does nothing unless something differs."""
    try:
      self.sm.update(0)
      if not (self.sm.alive['mapdExtendedOut'] and self.sm.valid['mapdExtendedOut']):
        return

      raw = str(self.sm['mapdExtendedOut'].settings or "")
      if not raw.strip():
        return
      try:
        blob = json.loads(raw)
      except (ValueError, TypeError):
        if not self._logged_unreadable:
          self._logged_unreadable = True
          cloudlog.warning("mapd settings: mapd published a settings blob that is not JSON")
        return

      # Cache mapd's own account of itself. This is what MapdSettings was declared for, and it makes
      # "what was mapd configured as on that drive" answerable from the params rather than only from
      # a route that happened to log it.
      try:
        self.params.put("MapdSettings", blob)
      except Exception:  # noqa: BLE001
        pass

      changed = False
      for path, want in self._desired().items():
        if path in self.settled:
          continue
        have = self._lookup(blob, path)
        if isinstance(have, (int, float)) and abs(float(have) - want) < _EPS:
          self.settled.add(path)
          self.attempts.pop(path, None)
          continue
        n = self.attempts.get(path, 0)
        if n >= MAX_ATTEMPTS:
          continue
        if n == 0:
          cloudlog.warning("mapd settings: %s is %s, asking for %.2f", path, have, want)
        self.attempts[path] = n + 1
        if n + 1 == MAX_ATTEMPTS:
          cloudlog.error("mapd settings: %s would not take %.2f after %d tries; giving up",
                         path, want, MAX_ATTEMPTS)
        self._send(path, want)
        changed = True

      if changed:
        self._save()
    except Exception:  # noqa: BLE001
      # mapd_manager also runs the live-map source and the OSM cleanup. A settings bridge must never
      # be the reason either of those stops.
      cloudlog.exception("mapd settings: sync failed")
