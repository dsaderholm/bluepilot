"""How far away was the sign when the camera read it?

The range hypothesis -- that TSR only resolves a sign it is nearly beside -- was built from three
reads that were all on slow roads. He looked at one of them on Street View and said the sign is far
away, which would refute it. This measures it instead of eyeballing.

The sign sits at the START of the speed limit zone it posts, and mapd knows where that boundary is
from the OSM way's maxspeed. So:

    distance from the TSR read to where the map's limit BECOMES that same value  =  read range

A read BEFORE the boundary means the camera saw the sign from that far ahead. A read AFTER it means
the camera was already inside the zone and read the sign late, or read a repeater.

Prints the track around the read so the geometry is visible rather than asserted.

    python tools/bp_tsr_read_range.py 000003ac
"""
import math
import os
import sys

REALDATA = os.environ.get("REALDATA", "/data/media/0/realdata")
sys.path.insert(0, "/data/openpilot")
from openpilot.tools.lib.logreader import LogReader  # noqa: E402


def seg_index(name):
  try:
    return int(name.rsplit("--", 1)[1])
  except Exception:
    return -1


def meters(a, b):
  """Equirectangular distance, plenty accurate over a few km."""
  if not a or not b:
    return None
  lat1, lon1 = a
  lat2, lon2 = b
  x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2.0))
  y = math.radians(lat2 - lat1)
  return math.hypot(x, y) * 6371000.0


def main():
  route = sys.argv[1]
  segs = sorted([d for d in os.listdir(REALDATA) if d.startswith(route)], key=seg_index)

  fix = None
  speed = None
  maplimit = None
  reading = False
  t0 = None
  track = []          # (t, fix, mph, tsr, map_limit)
  read_at = None
  read_t = None
  read_value = None
  # WHICH SEGMENT, and how far into it. This is what lets the actual camera frame be pulled --
  # `ffmpeg -ss <offset> -i <seg>/qcamera.ts` -- which beats arguing about Street View. Anchored on
  # each segment's OWN first timestamp rather than t_rel/60, because header replay puts the boot
  # messages at the front of every segment and would shift the offset.
  seg_name = None
  seg_t0 = None
  read_seg = None
  read_offset = None

  for s in segs:
    seg_name = s
    seg_t0 = None
    p = os.path.join(REALDATA, s, "rlog")
    if not os.path.exists(p):
      p += ".zst"
    if not os.path.exists(p):
      continue
    try:
      lr = LogReader(p)
    except Exception:
      continue
    for m in lr:
      try:
        w = m.which()
      except Exception:
        continue
      t = m.logMonoTime / 1e9
      if t0 is None or t < t0:
        t0 = t
      if w in ("gpsLocation", "gpsLocationExternal"):
        try:
          g = getattr(m, w)
          if g.latitude or g.longitude:
            fix = (float(g.latitude), float(g.longitude))
        except Exception:
          pass
        continue
      if w == "carState":
        if seg_t0 is None or t < seg_t0:
          seg_t0 = t
        try:
          speed = float(m.carState.vEgo) * 2.23694
        except Exception:
          pass
        continue
      if w == "mapdOut":
        try:
          maplimit = float(m.mapdOut.speedLimit) * 2.23694
        except Exception:
          pass
        continue
      if w != "carStateBP":
        continue
      try:
        v = int(m.carStateBP.trafficSignData.vLimit1)
      except Exception:
        continue
      now = v not in (0, 255)
      if now and not reading and read_at is None:
        read_at, read_t, read_value = fix, t, v
        read_seg = seg_name
        read_offset = (t - seg_t0) if seg_t0 is not None else None
      reading = now
      # One row a second is enough to see the geometry.
      if not track or t - track[-1][0] >= 1.0:
        track.append((t, fix, speed, v, maplimit))

  if read_at is None:
    print("no TSR read on this route")
    return

  rel = lambda tt: tt - t0  # noqa: E731
  print("TSR read {} at t+{:.1f}  {:.6f}, {:.6f}  doing {:.0f} mph".format(
    read_value, rel(read_t), read_at[0], read_at[1], speed or 0))
  if read_seg:
    off = "{:.1f}".format(read_offset) if read_offset is not None else "?"
    print("segment {}  at {} s into it".format(read_seg, off))
    print("  ffmpeg -ss {} -i {}/{}/qcamera.ts -frames:v 1 -q:v 2 /tmp/sign.jpg".format(
      off, REALDATA, read_seg))
  print()

  # Where does the MAP first say this same limit, and how far is that from the read?
  boundary = None
  prev = None
  for t, f, mph, tsr, ml in track:
    if ml is not None and prev is not None and abs(ml - read_value) < 1.5 and abs(prev - read_value) >= 1.5:
      boundary = (t, f)
      break
    if ml is not None:
      prev = ml
  if boundary and boundary[1] and read_at:
    d = meters(read_at, boundary[1])
    when = "AFTER the read" if boundary[0] > read_t else "BEFORE the read"
    print("map's limit becomes {} at t+{:.1f}, {} -- {:.0f} m from where the camera read it".format(
      read_value, rel(boundary[0]), when, d or 0))
  else:
    print("map never posts {} on this route, so the zone boundary cannot anchor the range".format(read_value))
  print()

  print("     t+      lat        lon        mph   TSR   map")
  for t, f, mph, tsr, ml in track:
    if abs(t - read_t) > 45:
      continue
    ll = "{:.6f} {:.6f}".format(*f) if f else "     --          --   "
    mark = "  <== READ" if abs(t - read_t) < 0.6 else ""
    print("  {:7.1f}  {}  {:5.1f}  {:>4}  {:>4}{}".format(
      rel(t), ll, mph or 0, tsr if tsr not in (0, 255) else "--",
      "{:.0f}".format(ml) if ml else "--", mark))


if __name__ == "__main__":
  main()
