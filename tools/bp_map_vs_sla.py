#!/usr/bin/env python3
"""FusionPilot: when SLA had no speed limit, did the DEVICE'S OWN TILES have one?

The 50x discrepancy in bluepilot/MAPD-V2-PLAN.md: OSM carries a maxspeed on 86-97% of the roads he
drives, and route 00000379 measured Speed Limit Assist holding one on 1.7% of plan frames. Both
numbers are solid, so something between the tile and SLA is dropping it. Three candidates were
listed -- tiles missing, v1's way-matching losing the road, or SLA's own validation rejecting it --
and nothing could separate them, because v1 mapd talks through /dev/shm/params and puts none of what
it saw into the route.

This separates them by reading BOTH ends of the same drive: the route says where the car was and
whether SLA had a number, the tile store on the same device says what OSM holds at that point.

    tile missing at that spot          -> the maps are the problem, download more
    tile present, way has a maxspeed   -> THE LIMIT WAS ON THE DEVICE AND DID NOT ARRIVE.
                                          way-matching or SLA validation, not coverage.
    tile present, way has no maxspeed  -> genuinely unmapped road, nothing to fix

It matches the way the way NEAREST the car, which is not what mapd does -- mapd tracks a current way
with hysteresis and a heading check, and getting that wrong in the direction of "found something" is
precisely the failure being hunted. So a road-name mismatch here is a hint and not a verdict; what is
trustworthy is the aggregate, and the ONE case that matters: if the nearest ways at a position all
carry a limit, no matching strategy could have come back with nothing.

USAGE, on the device:

    cd /data/openpilot && python tools/bp_map_vs_sla.py
    python tools/bp_map_vs_sla.py --route 00000379--aa11bb22cc --max-segments 20
"""
from __future__ import annotations

import argparse
import math
import os
import sys

REALDATA = "/data/media/0/realdata"
TILE_ROOT = "/data/media/0/osm/offline"
SCHEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bp_offline_tile.capnp")
MS_TO_MPH = 2.23694
EARTH_R = 6373000.0

# ~0.0005 deg is about 55 m of latitude. One sample per bucket, so a drive spends its samples on
# ROAD COVERED rather than on time: sitting at a light for two minutes must not outvote a mile of
# freeway, and the question is about places, not frames.
GRID = 0.0005


def seg_index(name: str) -> int:
  """Segment order is NUMERIC -- sorted() puts --10 before --2. See bp_why_slow.py."""
  tail = name.rsplit("--", 1)[-1]
  return int(tail) if tail.isdigit() else -1


def find_segments(route: str | None) -> list[str]:
  if not os.path.isdir(REALDATA):
    sys.exit(f"no {REALDATA} -- run this on the device")
  entries = sorted((d for d in os.listdir(REALDATA) if "--" in d), key=seg_index)
  if not entries:
    sys.exit("no route segments")
  if route is None:
    route = entries[-1].rsplit("--", 1)[0]
    print(f"# newest route: {route}")
  segs = [os.path.join(REALDATA, d) for d in entries if d.startswith(route + "--")]
  if not segs:
    sys.exit(f"no segments for {route}")
  return segs


def rlog(seg: str) -> str | None:
  for name in ("rlog", "rlog.zst", "rlog.bz2"):
    p = os.path.join(seg, name)
    if os.path.exists(p):
      return p
  return None


class Tiles:
  """The tile store, loaded lazily and kept -- a drive touches a handful of tiles, each ~2 MB."""

  def __init__(self, root: str):
    try:
      import capnp
    except ImportError:
      sys.exit("pycapnp missing; on the device use /usr/local/venv/bin/python")
    capnp.remove_import_hook()
    self.schema = capnp.load(SCHEMA)
    self.index: list[tuple[tuple[float, float, float, float], str]] = []
    self.cache: dict[str, list] = {}
    for dirpath, _, names in os.walk(root):
      for name in names:
        parts = name.split("_")
        if len(parts) != 4:
          continue
        try:
          bb = tuple(float(p) for p in parts)
        except ValueError:
          continue
        self.index.append((bb, os.path.join(dirpath, name)))  # type: ignore[arg-type]

  def ways_at(self, lat: float, lon: float) -> list | None:
    """Every way in the tile covering this point, or None if no tile covers it."""
    paths = [p for bb, p in self.index if bb[0] <= lat <= bb[2] and bb[1] <= lon <= bb[3]]
    if not paths:
      return None
    out: list = []
    for path in paths:
      if path not in self.cache:
        with open(path, "rb") as f:
          off = self.schema.Offline.from_bytes_packed(f.read(), traversal_limit_in_words=2 ** 32)
        self.cache[path] = list(off.ways)
      out.extend(self.cache[path])
    return out


def point_to_segment_m(lat: float, lon: float, a, b) -> float:
  coslat = math.cos(math.radians(lat))
  ax = math.radians(a.longitude - lon) * EARTH_R * coslat
  ay = math.radians(a.latitude - lat) * EARTH_R
  bx = math.radians(b.longitude - lon) * EARTH_R * coslat
  by = math.radians(b.latitude - lat) * EARTH_R
  dx, dy = bx - ax, by - ay
  den = dx * dx + dy * dy
  if den == 0.0:
    return math.hypot(ax, ay)
  t = max(0.0, min(1.0, (-ax * dx - ay * dy) / den))
  return math.hypot(ax + t * dx, ay + t * dy)


def nearest_ways(lat: float, lon: float, ways: list, radius: float) -> list[tuple[float, object]]:
  # Bounding box first: point-to-segment over every way in a tile is thousands of ways per sample.
  pad = radius / 111000.0 + 0.001
  out = []
  for w in ways:
    if not (w.minLat - pad <= lat <= w.maxLat + pad and w.minLon - pad <= lon <= w.maxLon + pad):
      continue
    nodes = list(w.nodes)
    if len(nodes) < 2:
      continue
    d = min(point_to_segment_m(lat, lon, nodes[i], nodes[i + 1]) for i in range(len(nodes) - 1))
    if d <= radius:
      out.append((d, w))
  out.sort(key=lambda t: t[0])
  return out


def way_limit(w) -> float:
  return float(w.maxSpeed or w.maxSpeedForward or w.maxSpeedBackward or 0.0)


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--route", default=None)
  ap.add_argument("--max-segments", type=int, default=12)
  ap.add_argument("--radius", type=float, default=40.0, help="metres a way may be from the car")
  ap.add_argument("--tiles", default=TILE_ROOT)
  ap.add_argument("--min-speed", type=float, default=5.0, help="mph below which samples are dropped")
  args = ap.parse_args()

  try:
    from openpilot.tools.lib.logreader import LogReader
  except ImportError as e:
    sys.exit(f"no LogReader ({e}); run from /data/openpilot")

  segs = find_segments(args.route)
  if len(segs) > args.max_segments:
    # EVENLY SPACED, not the first N. The front of a route is the driveway: the receiver has no fix
    # yet, so the first ten segments of route 00000379 yielded four positions out of 11,923 plan
    # frames and the tool reported almost nothing to explain. Every other tool here caps at the
    # front, which is fine when the question is "did this event happen" and wrong when it is "what
    # did the whole drive look like".
    step = len(segs) / args.max_segments
    segs = [segs[int(i * step)] for i in range(args.max_segments)]
    print(f"# sampling {args.max_segments} segments spread across the route (--max-segments to change)")

  # One entry per grid square: (had a limit at least once, no-limit sample count, a position).
  cells: dict[tuple[int, int], list] = {}
  plan_frames = 0
  sla_frames = 0
  pos = None
  speed = 0.0

  for seg in segs:
    path = rlog(seg)
    if path is None:
      continue
    for msg in LogReader(path):
      w = msg.which()
      try:
        # THIS CAR LOGS `gpsLocation`, NOT `gpsLocationExternal`. qcomgpsd publishes the first and
        # ubloxd the second, and reading only the second returns a silent zero -- the first run of
        # this tool reported "SLA had a limit everywhere" from 9,523 frames and no positions at all.
        # mapd v2 makes the same distinction (it prefers External and falls back), so both are read.
        if w in ("gpsLocation", "gpsLocationExternal"):
          g = getattr(msg, w)
          # A fix at exactly 0,0 is the null island the driver is not on.
          pos = (g.latitude, g.longitude) if (g.latitude or g.longitude) else None
          speed = float(g.speed) * MS_TO_MPH
        elif w == "longitudinalPlanSP":
          r = msg.longitudinalPlanSP.speedLimit.resolver
          plan_frames += 1
          has = bool(r.speedLimitValid) and r.speedLimit > 0
          sla_frames += 1 if has else 0
          if pos is None or speed < args.min_speed:
            continue
          key = (int(pos[0] / GRID), int(pos[1] / GRID))
          cell = cells.setdefault(key, [False, 0, pos, str(r.source)])
          if has:
            cell[0] = True
          else:
            cell[1] += 1
      except Exception:  # noqa: BLE001
        continue

  if not plan_frames:
    sys.exit("no longitudinalPlanSP in those segments")

  print(f"\nplan frames {plan_frames}, SLA had a valid limit in {100.0 * sla_frames / plan_frames:.1f}%")

  # "No positions" and "SLA had a limit everywhere" are opposite findings and must never print the
  # same line -- a diagnostic that collapses two states into one message is how the wrong controller
  # gets blamed. See the note about `--` in bp_why_slow's ancestors.
  if not cells:
    sys.exit(f"no GPS fix above {args.min_speed:.0f} mph in those segments -- nothing to match "
             f"against. This says nothing about SLA.")

  blind = [c for c in cells.values() if not c[0]]
  print(f"{len(cells)} distinct ~55 m positions above {args.min_speed:.0f} mph; "
        f"SLA was blind at {len(blind)} of them\n")
  if not blind:
    print("nothing to explain -- SLA had a limit everywhere the car went.")
    return 0

  tiles = Tiles(args.tiles)
  print(f"# {len(tiles.index)} tiles indexed; matching {len(blind)} positions...", file=sys.stderr)

  no_tile = 0
  no_way = 0
  had_limit = 0
  no_limit = 0
  examples: list[str] = []
  for _, _, (lat, lon), src in blind:
    ways = tiles.ways_at(lat, lon)
    if ways is None:
      no_tile += 1
      continue
    near = nearest_ways(lat, lon, ways, args.radius)
    if not near:
      no_way += 1
      continue
    limited = [(d, w) for d, w in near if way_limit(w)]
    if limited:
      had_limit += 1
      if len(examples) < 12:
        d, w = limited[0]
        examples.append(f"  {lat:.5f},{lon:.5f}  {d:5.0f}m  {str(w.highwayClass):<13} "
                        f"{way_limit(w) * MS_TO_MPH:3.0f} mph  way {w.id:<11} "
                        f"{w.name or w.ref or ''}")
    else:
      no_limit += 1

  n = len(blind)
  pct = lambda v: f"{100.0 * v / n:5.1f}%"  # noqa: E731
  print(f"where SLA had no limit, the device's own tiles said:\n")
  print(f"  a way with a maxspeed was right there   {had_limit:5d}  {pct(had_limit)}   <-- LOST BETWEEN TILE AND SLA")
  print(f"  nearest way carries no maxspeed         {no_limit:5d}  {pct(no_limit)}   genuinely unmapped")
  print(f"  no way within {args.radius:.0f} m                    {no_way:5d}  {pct(no_way)}   off-road, or way-geometry gap")
  print(f"  no tile covering the point              {no_tile:5d}  {pct(no_tile)}   maps not downloaded here")

  if examples:
    print("\nexamples (position, distance to the way, what OSM holds there):")
    print("\n".join(examples))

  print("\nThe first row is the one that matters: those are places the limit was sitting on this")
  print("device's eMMC and never reached the planner. That is v1 way-matching or SLA validation,")
  print("and neither needs a mapd upgrade to fix -- but only mapd v2 would let a route SAY which.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
