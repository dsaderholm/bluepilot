"""What gain did the car actually apply, and where on the ramp was it? Read-only, off-device.

This is the reader for the `controllerStateBP` fields added 2026-09-01. Publishing them without a
reader would be the same bug they were added to fix -- a value that reaches the wire and answers
nothing -- so this ships in the same breath.

It answers, per drive, the questions that previously needed `LateralMotionControl` (0x3D3) decoded
off `sendcan` plus the gain schedule re-implemented in a tool:

    what gain was applied, by curve radius   was the ramp flat, tilted, or inverted in practice
    where on the ramp the driving happened   is the high factor even participating on these roads
    what the lane-centering trim contributed  the only closed position loop in the stack

WHY THE RAMP SHAPE IS THE POINT. `curvature_factor` interpolates between a low-curvature and a
high-curvature gain, so the SAME settings deliver different authority depending on how bent the road
is. A negative slope means the car steers LESS the harder the road bends, which reads on the road as
"calm on curves" and "cannot take tight ones" -- the same fact, and the reason a setting that was
perfect for 600 miles could not take curves the next morning.

A DRIVE OLDER THAN THE FIELDS SAYS NOTHING. Routes recorded before this build carry no
`curvatureFactor`, and a zero there means "not published", not "no gain". The tool says so rather
than printing a confident table of zeros -- the trap this file's own history keeps recording.

    python tools/bp_lateral_gain.py <dir-of-rlog.zst> [route ...]
"""
import collections
import glob
import os
import statistics
import sys

import capnp
import zstandard

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
capnp.remove_import_hook()
log_capnp = capnp.load(os.path.join(REPO, "cereal", "log.capnp"),
                       imports=[os.path.join(REPO, "cereal")])

MPH = 2.23694
MIN_MPH = 40.0

# Named by what they are on his roads, matching bp_lateral_by_radius.py so the two can be read
# side by side.
BANDS = [
  (2000, 10 ** 9, "over 2000 m"),
  (1400, 2000, "1400-2000 m"),
  (1000, 1400, "1000-1400 m"),
  (700, 1000, "700-1000 m"),
  (450, 700, "450-700 m"),
  (0, 450, "under 450 m"),
]


def seg_index(p):
  try:
    return int(os.path.basename(p).split("--")[2].split(".")[0])
  except (IndexError, ValueError):
    return -1


def scan(files):
  lat = False
  v = 0.0
  hands = False
  seen_field = False
  by_band = collections.defaultdict(list)
  trim = []
  anchors = []
  blend = []

  for p in files:
    try:
      with open(p, "rb") as fh:
        raw = zstandard.ZstdDecompressor().stream_reader(fh).read()
      evs = log_capnp.Event.read_multiple_bytes(raw, traversal_limit_in_words=2 ** 32)
    except Exception:
      continue
    while True:
      try:
        m = next(evs)
      except StopIteration:
        break
      except Exception:
        break
      w = m.which()
      try:
        if w == "carControl":
          lat = bool(m.carControl.latActive)
        elif w == "carState":
          v = float(m.carState.vEgo) * MPH
          hands = bool(m.carState.steeringPressed)
        elif w == "controllerStateBP":
          cs = m.controllerStateBP
          # A field absent from an older schema raises; a field present but never written is 0.0.
          try:
            factor = float(cs.curvatureFactor)
            kappa = float(cs.kappaCmd)
            lo = float(cs.gainLowCurv)
            hi = float(cs.gainHighCurv)
            lane = float(cs.laneCenterCorrection)
            b = float(cs.blendWeight)
          except Exception:
            continue
          if factor != 0.0 or lo != 0.0:
            seen_field = True
          if not (lat and not hands and v >= MIN_MPH and factor != 0.0):
            continue
          if abs(kappa) > 1e-9:
            r = 1.0 / abs(kappa)
            for a, bnd, name in BANDS:
              if a <= r < bnd:
                by_band[name].append(factor)
                break
          if lo != 0.0 or hi != 0.0:
            anchors.append((lo, hi))
          trim.append(abs(lane))
          blend.append(b)
      except Exception:
        continue
  return seen_field, by_band, anchors, trim, blend


def main():
  d = sys.argv[1]
  wanted = set(sys.argv[2:])
  files = sorted(glob.glob(os.path.join(d, "*.rlog.zst")), key=seg_index)
  routes = collections.OrderedDict()
  for f in files:
    r = os.path.basename(f).split("--")[0]
    if wanted and r not in wanted:
      continue
    routes.setdefault(r, []).append(f)

  print("=== APPLIED GAIN, BY CURVE RADIUS ===")
  print("   hands off, latActive, >= %.0f mph. This is the number that multiplied the command.\n" % MIN_MPH)
  header = "  %-10s" % "route" + "".join("%13s" % n for _, _, n in BANDS) + "%12s%14s" % ("ramp", "trim |1/m|")
  print(header)

  for route, fs in routes.items():
    seen, by_band, anchors, trim, blend = scan(fs)
    if not seen:
      print("  %-10s  no gain telemetry on this route -- recorded before the fields existed "
            "(2026-09-01). Zero here means NOT PUBLISHED, not 'no gain'." % route)
      continue
    cells = []
    for _, _, name in BANDS:
      vals = by_band.get(name, [])
      cells.append("%13s" % ("%.3f" % statistics.median(vals) if len(vals) >= 40 else "-"))
    if anchors:
      lo = statistics.median(a for a, _ in anchors)
      hi = statistics.median(b for _, b in anchors)
      gap = hi - lo
      shape = "flat" if abs(gap) < 0.01 else ("rising %+.2f" % gap if gap > 0 else "INVERTED %+.2f" % gap)
    else:
      shape = "-"
    tr = "%.5f" % statistics.median(trim) if len(trim) >= 40 else "-"
    print("  %-10s" % route + "".join(cells) + "%12s%14s" % (shape, tr))

  print()
  print("  ramp        median gainHighCurv - gainLowCurv. NEGATIVE means the car steers LESS the")
  print("              harder the road bends: calm on curves AND weak on tight ones, one fact.")
  print("  trim        median |laneCenterCorrection|, the lane-centering loop's own contribution")
  print("              to commanded curvature. It is the only closed position loop in the stack.")
  print()
  print("  A column reading '-' had under 40 qualifying frames in that band -- not a zero.")


if __name__ == "__main__":
  main()
