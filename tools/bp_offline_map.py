#!/usr/bin/env python3
"""FusionPilot: what the OSM tiles on this device actually say, read straight off the disk.

Every diagnostic in tools/ reads the ROUTE, and the route contains nothing about the map -- v1 mapd
talks through /dev/shm/params, so none of what it saw was ever logged. This reads the other end: the
tile store itself, which is the same data mapd is matching against, and which is on the eMMC whether
or not mapd ever managed to publish a limit for the road.

That makes it the tool for the 50x discrepancy in bluepilot/MAPD-V2-PLAN.md -- OSM has a speed limit
on 86-97% of his corridors, SLA held one on 1.7% of route 00000379. If `--at` shows a maxspeed for
the road he was on, the limit was on the device the whole time and the loss is in v1's way-matching
or SLA's validation, not in coverage.

It also reads the fields the SHIPPED v1.12.0 binary cannot publish. The hosted tiles are generated
by mapd's current generator, so `id` (the OSM way id), `highwayClass` and the conditional-maxspeed
tags are already there -- 100% populated in Salt Lake County, measured 2026-08-16. `highwayClass`
separating motorway from motorwayLink is the freeway-vs-ramp fact the exit problem needs.

Speeds in the tiles are m/s; everything printed here is mph.

USAGE, on the device (or anywhere, against --root):

    cd /data/openpilot && python tools/bp_offline_map.py
    python tools/bp_offline_map.py --at 40.7608,-111.8910
    python tools/bp_offline_map.py --at 40.7608,-111.8910 --radius 150
    python tools/bp_offline_map.py --root ./offline --summary
"""
from __future__ import annotations

import argparse
import math
import os
import sys

TILE_ROOT = "/data/media/0/osm/offline"
SCHEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bp_offline_tile.capnp")
MS_TO_MPH = 2.23694
EARTH_R = 6373000.0

# Printed in this order rather than alphabetically: it is the order that matters to us, freeway
# first, and it keeps motorway next to motorwayLink where the exit question lives.
CLASS_ORDER = [
  "motorway", "motorwayLink", "trunk", "trunkLink", "primary", "primaryLink",
  "secondary", "secondaryLink", "tertiary", "tertiaryLink",
  "unclassified", "residential", "livingStreet", "unknown",
]


def load_schema():
  try:
    import capnp
  except ImportError:
    sys.exit("pycapnp is not available. On the device: /usr/local/venv/bin/python tools/bp_offline_map.py")
  capnp.remove_import_hook()
  return capnp.load(SCHEMA)


def tile_bbox(path: str) -> tuple[float, float, float, float] | None:
  """Tile files are named minlat_minlon_maxlat_maxlon, which is the only index there is."""
  parts = os.path.basename(path).split("_")
  if len(parts) != 4:
    return None
  try:
    return tuple(float(p) for p in parts)  # type: ignore[return-value]
  except ValueError:
    return None


def find_tiles(root: str) -> list[str]:
  found = []
  for dirpath, _, filenames in os.walk(root):
    for name in filenames:
      path = os.path.join(dirpath, name)
      if tile_bbox(path) is not None:
        found.append(path)
  return sorted(found)


def read_tile(schema, path: str):
  with open(path, "rb") as f:
    data = f.read()
  # traversal_limit_in_words: a tile is a single message far larger than pycapnp's default budget.
  return schema.Offline.from_bytes_packed(data, traversal_limit_in_words=2 ** 32)


def point_to_segment_m(lat: float, lon: float, a, b) -> float:
  """Metres from a point to a way segment, equirectangular about the point.

  Nearest NODE is not good enough: a freeway way can run a kilometre between nodes, so a car
  halfway along one would read as far off the road it is driving on.
  """
  coslat = math.cos(math.radians(lat))
  px, py = 0.0, 0.0
  ax = math.radians(a.longitude - lon) * EARTH_R * coslat
  ay = math.radians(a.latitude - lat) * EARTH_R
  bx = math.radians(b.longitude - lon) * EARTH_R * coslat
  by = math.radians(b.latitude - lat) * EARTH_R
  dx, dy = bx - ax, by - ay
  den = dx * dx + dy * dy
  if den == 0.0:
    return math.hypot(ax, ay)
  t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / den))
  return math.hypot(ax + t * dx, ay + t * dy)


def way_distance_m(lat: float, lon: float, way) -> float:
  nodes = list(way.nodes)
  if not nodes:
    return float("inf")
  if len(nodes) == 1:
    return point_to_segment_m(lat, lon, nodes[0], nodes[0])
  return min(point_to_segment_m(lat, lon, nodes[i], nodes[i + 1]) for i in range(len(nodes) - 1))


def mph(v: float) -> str:
  return f"{v * MS_TO_MPH:.0f}" if v else "--"


def summarize(schema, tiles: list[str]) -> None:
  stats: dict[str, list[int]] = {}
  total = 0
  for i, path in enumerate(tiles):
    print(f"\r  reading {i + 1}/{len(tiles)} tiles...", end="", file=sys.stderr)
    for way in read_tile(schema, path).ways:
      cls = str(way.highwayClass)
      row = stats.setdefault(cls, [0, 0, 0, 0, 0, 0])
      row[0] += 1
      row[1] += 1 if (way.maxSpeed or way.maxSpeedForward or way.maxSpeedBackward) else 0
      row[2] += 1 if way.lanes else 0
      row[3] += 1 if way.oneWay else 0
      row[4] += 1 if way.advisorySpeed else 0
      row[5] += 1 if way.id else 0
      total += 1
  print("\r" + " " * 40 + "\r", end="", file=sys.stderr)

  print(f"{len(tiles)} tiles, {total} ways\n")
  print("  class            ways  maxspeed  lanes  oneway  advisory  wayId")
  for cls in CLASS_ORDER + sorted(set(stats) - set(CLASS_ORDER)):
    row = stats.get(cls)
    if not row:
      continue
    n = row[0]
    pct = lambda v: f"{100.0 * v / n:.0f}%"  # noqa: E731
    print(f"  {cls:<14} {n:6d}  {pct(row[1]):>8}  {pct(row[2]):>5}  {pct(row[3]):>6}  "
          f"{pct(row[4]):>8}  {pct(row[5]):>5}")

  print("\n  oneway is not a coverage number -- absence means two-way, which is OSM's default.")
  print("  wayId and highwayClass at 100% mean the tiles are current-generator; the shipped")
  print("  v1.12.0 binary reads these files and cannot publish either field.")


def report_at(schema, tiles: list[str], lat: float, lon: float, radius: float) -> None:
  hits = [p for p in tiles if (bb := tile_bbox(p)) and bb[0] <= lat <= bb[2] and bb[1] <= lon <= bb[3]]
  if not hits:
    print(f"no tile covers {lat},{lon} -- that road has no map on this device at all.")
    print("that is the 'no map here' case, which is NOT the same as 'no limit here'.")
    return

  ways = []
  for path in hits:
    for way in read_tile(schema, path).ways:
      d = way_distance_m(lat, lon, way)
      if d <= radius:
        ways.append((d, way))
  ways.sort(key=lambda t: t[0])

  print(f"{lat},{lon} -- {len(hits)} tile(s), {len(ways)} ways within {radius:.0f} m\n")
  if not ways:
    print("  tile present but no way within the radius. Widen it with --radius.")
    return

  print("     dist  class          limit  adv  lanes  1way  wayId       name / ref")
  for d, w in ways[:20]:
    limit = w.maxSpeed or w.maxSpeedForward or w.maxSpeedBackward
    label = w.name or w.ref or ""
    if w.name and w.ref:
      label = f"{w.name} ({w.ref})"
    cond = f"  cond={w.maxSpeedConditional}" if w.maxSpeedConditional else ""
    print(f"  {d:7.0f}m  {str(w.highwayClass):<13} {mph(limit):>5}  {mph(w.advisorySpeed):>3}  "
          f"{w.lanes or '--':>5}  {'yes' if w.oneWay else '  -':>4}  {w.id:<10}  {label}{cond}")
  if len(ways) > 20:
    print(f"  ... {len(ways) - 20} more")


def main() -> int:
  p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  p.add_argument("--root", default=TILE_ROOT, help=f"tile store (default {TILE_ROOT})")
  p.add_argument("--at", metavar="LAT,LON", help="what the map says at a point")
  p.add_argument("--radius", type=float, default=60.0, help="metres, with --at (default 60)")
  p.add_argument("--summary", action="store_true", help="coverage table (the default)")
  args = p.parse_args()

  if not os.path.isdir(args.root):
    print(f"no tile store at {args.root}")
    print("on the device that means no maps are downloaded; off it, pass --root.")
    return 1

  tiles = find_tiles(args.root)
  if not tiles:
    print(f"{args.root} exists but holds no tiles.")
    return 1

  schema = load_schema()
  if args.at and not args.summary:
    try:
      lat, lon = (float(v) for v in args.at.replace(" ", "").split(","))
    except ValueError:
      print("--at wants LAT,LON, e.g. --at 40.7608,-111.8910")
      return 1
    report_at(schema, tiles, lat, lon, args.radius)
  else:
    summarize(schema, tiles)
  return 0


if __name__ == "__main__":
  sys.exit(main())
