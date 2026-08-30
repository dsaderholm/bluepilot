"""Did the bp-dev lane centering trim actually center the car better? Read-only, off-device.

*"Yes, I have noticed it not being in the center of the lane sometimes."* -- which is why the
angle-mode lane centering was cherry-picked from bp-dev. Whether it delivered is measurable, and
on his 2026-08-28 drives there is a genuine A/B sitting in the logs rather than a before/after
across a rebuild:

    routes 000003e2 .. 000003e6   enable_lane_positioning_ang = 0    gains 1.185 / 0.875
    routes 000003e7 .. 000003ea   enable_lane_positioning_ang = 1    gains 1.177 / 0.873

**The gains move less than 1% across that boundary**, so for once the confound this file keeps
recording -- a setting changing between drives and being read as a code change -- is absent by
luck rather than by design. Routes 3eb/3ec are excluded deliberately: he jumped the high-speed
gain from 0.88 to 1.146 there, so they are a different car.

THE METRIC IS LATERAL POSITION, NOT RINGING. `lane_center_trim` computes a curvature correction
toward the lane-line centre (blended toward the model's own path as laneline confidence falls), so
the thing it is supposed to move is how far off centre the car sits:

    lane_centre_y = (laneLines[1].y[0] + laneLines[2].y[0]) / 2      # ego lane, at x = 0
    offset        = -lane_centre_y                                    # car sits at y = 0

Reversal rate is reported beside it only to check the trim did not buy centring by adding wobble.

HANDS OFF, latActive, and only where BOTH ego lane lines are confident -- with no lane lines the
trim reduces to the user's bias term and is not being asked to centre anything, so those frames
say nothing about it either way.

A second comparison became available on 2026-08-29 when he raised the strength from 0.25 to 0.55.
Pass explicit groups to score any pair, since the built-in ones only describe the 08-28 flip:

    python tools/bp_lane_centering_ab.py <dir> --group "0.25:000003e7,000003e8" --group "0.55:000003ed"

Groups may name routes in DIFFERENT directories; pass --dir before each --group to switch. Reading
across directories is the point here, because the 0.25 and 0.55 samples are on different days.

    python tools/bp_lane_centering_ab.py <dir-of-rlog.zst>
"""
import bisect
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

MS_TO_MPH = 2.23694
MIN_PROB = 0.5           # both ego lane lines must be at least this confident
MIN_V = 5.0

# The A/B, from tools/bp_settings_timeline.py on these same routes.
GROUPS = collections.OrderedDict([
  ("lane centering OFF", ["000003e2", "000003e3", "000003e4", "000003e5", "000003e6"]),
  ("lane centering ON", ["000003e7", "000003e8", "000003e9", "000003ea"]),
])


def seg_key(p):
  b = os.path.basename(p).split("--")
  try:
    return (b[0], int(b[2].split(".")[0]))
  except (IndexError, ValueError):
    return (os.path.basename(p), 0)


def main():
  d = sys.argv[1]
  files = sorted(glob.glob(os.path.join(d, "*.rlog.zst")), key=seg_key)

  wanted = {r: g for g, rs in GROUPS.items() for r in rs}
  stats = collections.defaultdict(lambda: {"off": [], "rev": 0, "n": 0})

  for group in GROUPS:
    paths = [f for f in files if wanted.get(os.path.basename(f).split("--")[0]) == group]
    car, model = [], []
    t0 = None
    for p in paths:
      try:
        with open(p, "rb") as f:
          raw = zstandard.ZstdDecompressor().stream_reader(f).read()
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
        mono = m.logMonoTime / 1e9
        if t0 is None or mono < t0:
          t0 = mono
        ts = mono - t0
        w = m.which()
        try:
          if w == "carState":
            cs = m.carState
            car.append((ts, float(cs.vEgo), bool(cs.steeringPressed)))
          elif w == "modelV2":
            mv = m.modelV2
            if len(mv.laneLines) < 4 or len(mv.laneLineProbs) < 4:
              continue
            if mv.laneLineProbs[1] < MIN_PROB or mv.laneLineProbs[2] < MIN_PROB:
              continue
            yl = list(mv.laneLines[1].y)
            yr = list(mv.laneLines[2].y)
            if not yl or not yr:
              continue
            model.append((ts, -(yl[0] + yr[0]) / 2.0))
        except Exception:
          continue

    car.sort()
    model.sort()
    vt = [r[0] for r in car]
    vv = [(r[1], r[2]) for r in car]

    prev_sign = 0
    for ts, off in model:
      i = bisect.bisect_left(vt, ts)
      cs = None
      for j in (i - 1, i):
        if 0 <= j < len(vt) and abs(vt[j] - ts) <= 0.06:
          cs = vv[j]
          break
      if cs is None:
        continue
      v, hands = cs
      if hands or v < MIN_V:
        continue
      s = stats[group]
      s["off"].append(off)
      s["n"] += 1
      sign = 1 if off > 0.05 else (-1 if off < -0.05 else 0)
      if sign and prev_sign and sign != prev_sign:
        s["rev"] += 1
      if sign:
        prev_sign = sign

  print("=== DID THE bp-dev LANE CENTERING TRIM CENTER THE CAR? ===")
  print()
  print("  Same day, gains within 1%, hands off, both ego lane lines confident.")
  print("  offset is the car's lateral distance from lane-line center, in metres.")
  print()
  print("  %-22s %9s %9s %9s %9s %9s" % ("", "|off| med", "|off| p90", "bias", "frames", "cross/min"))
  for group in GROUPS:
    s = stats[group]
    if s["n"] < 500:
      print("  %-22s %9s (only %d frames)" % (group, "--", s["n"]))
      continue
    a = sorted(abs(x) for x in s["off"])
    minutes = s["n"] / 20.0 / 60.0        # modelV2 is 20 Hz
    print("  %-22s %9.3f %9.3f %+9.3f %9d %9.1f"
          % (group, statistics.median(a), a[int(0.9 * (len(a) - 1))],
             statistics.median(s["off"]), s["n"], s["rev"] / max(minutes, 1e-6)))
  print()

  keys = list(GROUPS)
  if all(stats[k]["n"] >= 500 for k in keys):
    off_a = sorted(abs(x) for x in stats[keys[0]]["off"])
    off_b = sorted(abs(x) for x in stats[keys[1]]["off"])
    ma, mb = statistics.median(off_a), statistics.median(off_b)
    delta = mb - ma
    print("  median |offset|  %.3f m -> %.3f m   (%+.3f m, %+.1f%%)"
          % (ma, mb, delta, 100.0 * delta / ma))
    if delta < -0.01:
      print("  The trim is centering the car better.")
    elif delta > 0.01:
      print("  The car sits FURTHER off center with the trim on.")
    else:
      print("  No meaningful difference. On this evidence the trim is not doing what it was taken for.")
    print()
    print("  A caution before reading much into it: these are different ROADS as well as different")
    print("  settings -- one group is not a re-drive of the other. A difference this measurement")
    print("  cannot separate from route mix is not a result, and the honest test is a stretch of")
    print("  road driven twice.")


if __name__ == "__main__":
  main()
