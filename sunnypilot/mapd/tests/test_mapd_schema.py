"""FusionPilot: the mapd v2 message definitions are THEIRS, and drift is silent.

The mapd binary is compiled against its own copy of this schema and writes these messages onto the
wire. capnp reads by POSITION -- so a field inserted, renumbered or reordered here does not produce
an error, it produces a message where `speedLimit` is read out of the bytes that hold something
else. Nothing at runtime would say so; the numbers would simply be wrong.

That is the same failure mode recorded in CLAUDE.md for the two branches that collided on ordinals,
except worse: there, both sides were ours. Here the other side is a binary we do not build.

So the ordinals are pinned as literals below. If mapd adds a field, take theirs verbatim and update
this list in the same commit. If we want a field of our own, it goes in a struct of ours.
"""
import os
import re

import capnp

from cereal import custom, log

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# From pfeiferj/openpilot-mapd cereal/custom/custom.capnp, read 2026-08-16 at v2.3.0.
MAPD_OUT_ORDINALS = {
  "wayName": 0, "wayRef": 1, "roadName": 2, "speedLimit": 3, "nextSpeedLimit": 4,
  "nextSpeedLimitDistance": 5, "hazard": 6, "nextHazard": 7, "nextHazardDistance": 8,
  "advisorySpeed": 9, "nextAdvisorySpeed": 10, "nextAdvisorySpeedDistance": 11, "oneWay": 12,
  "lanes": 13, "tileLoaded": 14, "speedLimitSuggestedSpeed": 15, "suggestedSpeed": 16,
  "estimatedRoadWidth": 17, "roadContext": 18, "distanceFromWayCenter": 19, "visionCurveSpeed": 20,
  "mapCurveSpeed": 21, "waySelectionType": 22, "speedLimitAccepted": 23, "highwayClass": 24,
  "wayId": 25, "conditionalSpeedLimit": 26,
}

MAPD_PATH_POINT_ORDINALS = {"latitude": 0, "longitude": 1, "curvature": 2, "targetVelocity": 3}

# The Event slots. These were customReserved17/18/19 and mapd hardcodes exactly these three, so a
# fork that puts them anywhere else gets a binary talking past it.
EVENT_SLOTS = {"mapdExtendedOut": 143, "mapdIn": 144, "mapdOut": 145}


def _declared_ordinals(path: str, struct_or_enum: str) -> dict[str, int]:
  """Read `name @N` pairs straight out of the .capnp text.

  Deliberately textual. Reading them back through pycapnp would ask the schema whether it agrees
  with itself, which it always does -- the question is whether the FILE says what mapd's file says.
  """
  text = open(os.path.join(REPO, path), encoding="utf-8").read()
  body = re.search(rf"(?:struct|enum)\s+{struct_or_enum}\b[^{{]*\{{(.*?)\n\}}", text, re.S)
  assert body, f"{struct_or_enum} not found in {path}"
  return {m.group(1): int(m.group(2)) for m in re.finditer(r"^\s*(\w+)\s*@(\d+)", body.group(1), re.M)}


def test_event_slots_are_where_mapd_expects_them():
  fields = log.Event.schema.fields
  for name, ordinal in EVENT_SLOTS.items():
    assert name in fields, f"Event has no {name}; mapd publishes it"
  declared = _declared_ordinals("cereal/log.capnp", "Event")
  for name, ordinal in EVENT_SLOTS.items():
    assert declared.get(name) == ordinal, (
      f"Event.{name} is @{declared.get(name)}, mapd hardcodes @{ordinal}. "
      f"capnp reads by position: this decodes as a different message.")


def test_mapd_out_ordinals_are_unchanged():
  declared = _declared_ordinals("cereal/custom.capnp", "MapdOut")
  assert declared == MAPD_OUT_ORDINALS, (
    "MapdOut no longer matches mapd's own schema.\n"
    f"  ours:  {sorted(declared.items(), key=lambda kv: kv[1])}\n"
    f"  mapd:  {sorted(MAPD_OUT_ORDINALS.items(), key=lambda kv: kv[1])}")


def test_mapd_path_point_ordinals_are_unchanged():
  assert _declared_ordinals("cereal/custom.capnp", "MapdPathPoint") == MAPD_PATH_POINT_ORDINALS


def test_the_fields_actually_round_trip():
  """The pinned list is only worth something if the schema it describes really loads."""
  m = custom.MapdOut.new_message()
  m.highwayClass = "motorwayLink"
  m.waySelectionType = "fail"
  m.wayId = 36870752
  m.speedLimit = 29.058
  assert str(m.highwayClass) == "motorwayLink"
  assert str(m.waySelectionType) == "fail"
  assert m.wayId == 36870752

  e = log.Event.new_message()
  e.init("mapdExtendedOut")
  pts = e.mapdExtendedOut.init("path", 1)
  pts[0].curvature = 0.0068
  pts[0].targetVelocity = 17.0
  assert e.which() == "mapdExtendedOut"
  assert abs(pts[0].targetVelocity - 17.0) < 1e-6


def test_highway_class_matches_the_tile_schema():
  """mapd's own comment: these two enums must stay in perfect sync, because its state.go CASTS
  between the generated types rather than mapping them. We now carry both copies -- the message one
  in cereal/custom.capnp and the tile one in tools/bp_offline_tile.capnp -- so the invariant is
  checkable here instead of being a warning nobody can act on.
  """
  msg_enum = _declared_ordinals("cereal/custom.capnp", "HighwayClass")
  tile_enum = _declared_ordinals("tools/bp_offline_tile.capnp", "HighwayClass")
  assert msg_enum == tile_enum, (
    "HighwayClass differs between the message schema and the tile schema. mapd casts directly "
    "between them, so a mismatch silently relabels every road.\n"
    f"  message: {msg_enum}\n  tile:    {tile_enum}")

  # And the tile reader has to agree with the compiled message enum, or bp_offline_map.py's output
  # cannot be compared against a route.
  capnp.remove_import_hook()
  tile = capnp.load(os.path.join(REPO, "tools", "bp_offline_tile.capnp"))
  assert [e for e in tile.HighwayClass.schema.enumerants] == list(msg_enum)


# Where a decision about the car gets made. Scanned whole, because the ban below is not about one
# file -- it is about one IDEA, and the idea is equally wrong wherever it lands. Passing assist and
# the radar detector rebase onto this branch, so their code is under these roots too and is covered
# here without either branch having to remember to add a guard of its own.
_DECISION_ROOTS = (
  "selfdrive/controls",
  "selfdrive/car",
  "selfdrive/selfdrived",
  "sunnypilot/selfdrive",
  "sunnypilot/mapd",
  "opendbc_repo/opendbc/sunnypilot",
)
# This file names it constantly, on purpose.
_BAN_EXEMPT = ("sunnypilot/mapd/tests/test_mapd_schema.py",)


def _reads_suggested_speed(path: str) -> list[int]:
  """Lines where `suggestedSpeed` is USED as a name -- not merely written about.

  Parsed rather than grepped, and that distinction is the whole reason this can be a hard ban:
  every explanation of why we refuse this field contains the word, so a text search forces whoever
  writes the next comment to either omit the name or add an exemption. `ast` sees code only.

  Catches attribute access (`sm['mapdOut'].suggestedSpeed`) and the getattr spelling
  (`getattr(x, "suggestedSpeed")`). A docstring MENTIONING it is a longer string and is not equal
  to the name, so prose stays free.
  """
  import ast
  try:
    tree = ast.parse(open(path, encoding="utf-8").read())
  except (SyntaxError, UnicodeDecodeError):
    return []
  hits = []
  for node in ast.walk(tree):
    if isinstance(node, ast.Attribute) and node.attr == "suggestedSpeed":
      hits.append(node.lineno)
    elif isinstance(node, ast.Constant) and node.value == "suggestedSpeed":
      hits.append(node.lineno)
  return hits


def test_nothing_clamps_v_cruise_from_the_map():
  """mapd's integration guide, step 7, says to put this in longitudinal_planner.py:

      if sm['mapdOut'].suggestedSpeed > 0 and v_cruise > sm['mapdOut'].suggestedSpeed:
        v_cruise = sm['mapdOut'].suggestedSpeed

  We do not take that step, and this is the guard, because it will look like a tidy-up to whoever
  meets it next. `suggestedSpeed` is mapd's own arbitration -- the minimum of its speed-limit and
  curve numbers -- and it cannot know that this car is driven by BUTTON PRESSES at about 3.3 mph/s,
  that a HOLD exists, or that SCC-Map carries four defenses each built from a measured event on
  these roads. Applied as a clamp it also moves the MAX number, which is his and which he has asked
  twice to be left alone.

  The INGREDIENTS are welcome and are the point of the migration: speedLimitSuggestedSpeed,
  mapCurveSpeed and visionCurveSpeed go in as inputs beside the camera, and our controllers decide.

  THIS SCANS EVERY DECISION-MAKING FILE, not just the planner. The first version checked
  longitudinal_planner.py alone, and a read added to speed_limit_assist.py passed it -- verified by
  doing exactly that on 2026-08-16. The guide names the planner because that is where the guide
  puts it; the bypass is just as complete from SLA, from SCC-Map, from the ICBM controller, or from
  a passing-assist gate. A guard against one spelling of a mistake is not a guard against the
  mistake.
  """
  offenders = []
  for root in _DECISION_ROOTS:
    base = os.path.join(REPO, *root.split("/"))
    for dirpath, dirnames, filenames in os.walk(base):
      dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git")]
      for name in filenames:
        if not name.endswith(".py"):
          continue
        full = os.path.join(dirpath, name)
        rel = os.path.relpath(full, REPO).replace(os.sep, "/")
        if rel in _BAN_EXEMPT:
          continue
        for line in _reads_suggested_speed(full):
          offenders.append(f"{rel}:{line}")

  assert not offenders, (
    "these read mapd's suggestedSpeed: " + ", ".join(offenders) + " -- "
    "that is the one non-additive step in mapd's integration guide and it bypasses ICBM, holds and "
    "the SCC-Map defenses; as a clamp it also moves the MAX number, which is his. Consume "
    "speedLimitSuggestedSpeed / mapCurveSpeed / visionCurveSpeed through the controllers instead.")
