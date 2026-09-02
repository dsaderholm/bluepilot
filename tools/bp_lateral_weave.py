"""Straight-road weave: how far the car wanders across the lane, and how often it crosses centre.

This is the metric for `lane_centering_strength_ang` and `lane_centering_damping_ang`, because
those are the only closed POSITION loop in the stack. It is deliberately NOT a steering-angle
metric: angle scales with the gain schedule, so an angle-based test marks any high-gain setting
worse by construction and would confound the two knobs.

WHY THIS TOOL EXISTS RATHER THAN THE NUMBERS ALREADY IN THE LEDGER. Those were produced by an
ad-hoc script that no longer exists, at an unrecorded sampling rate. A second ad-hoc script written
on 2026-09-02 returned crossing rates 3-4x higher on comparable road -- not because the car
changed, but because the two counted crossings differently. **Two numbers from two instruments are
not a comparison.** So this is a tool, it states its own definitions, and every route quoted against
another must be re-run through it.

SAMPLES AT THE MODEL RATE, WHICH IS THE POINT. `modelV2` publishes at 20 Hz. Reading lane position
off a 100 Hz stream resamples each model frame five times, and any crossing count then depends on
the reader's rate rather than on the road. One sample per model frame, always.

    python tools/bp_lateral_weave.py <dir> [--speed 70] [--route 00000407] [--segs 0-14]

Definitions, so a future reader can tell whether a quoted number came from here:

    straight        |desiredCurvature| < 2.5e-4 (a 4000 m radius) for the WHOLE window
    window          6.0 s, non-overlapping, discarded if any frame fails a gate
    offset          (laneLines[1].y[0] + laneLines[2].y[0]) / 2, metres, + is left of centre
    gates           hands off, latActive, speed >= floor, both lane-line probs >= 0.30
    crossings/min   sign changes of (offset - window mean), per minute of qualifying road
    p2p             max(offset) - min(offset) within the window, median across windows
"""
import argparse
import collections
import glob
import os
import statistics

import capnp
import zstandard

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
capnp.remove_import_hook()
log_capnp = capnp.load(os.path.join(REPO, "cereal", "log.capnp"),
                       imports=[os.path.join(REPO, "cereal")])

MPH = 2.23694
WIN_S = 6.0
STRAIGHT_KAPPA = 2.5e-4
MIN_LANE_PROB = 0.30

# Every param that can move this metric, so a route's own configuration is printed beside its
# numbers rather than looked up in a file that may have drifted.
CONFIG_KEYS = ("lane_centering_strength_ang", "lane_centering_damping_ang",
               "enable_lane_positioning_ang", "custom_path_offset_ang")


def segno(path):
  try:
    return int(os.path.basename(path).split("--")[2].split(".")[0])
  except (IndexError, ValueError):
    return -1


def route_of(path):
  return os.path.basename(path).split("--")[0]


def read_config(files):
  """Boot-snapshot config. initData is replayed unchanged into every segment, so this is the value
  at BOOT and cannot show a mid-route change -- see bp_settings_timeline.py."""
  for p in files:
    try:
      with open(p, "rb") as fh:
        raw = zstandard.ZstdDecompressor().stream_reader(fh).read()
      for m in log_capnp.Event.read_multiple_bytes(raw, traversal_limit_in_words=2 ** 32):
        if m.which() == "initData":
          e = {x.key: x.value for x in m.initData.params.entries}
          out = {}
          for k in CONFIG_KEYS:
            v = e.get(k, b"")
            v = v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
            out[k] = v
          return out
    except Exception:
      continue
  return {k: "?" for k in CONFIG_KEYS}


def scan(files, speed_floor):
  lat = False
  hands = False
  v = 0.0
  des = 0.0
  window = []
  offs, p2p, cross = [], [], []
  minutes = 0.0
  rejected = collections.Counter()

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
        elif w == "controlsState":
          des = float(m.controlsState.desiredCurvature)
        elif w == "modelV2":
          # ONE sample per model frame. Resampling this onto a 100 Hz stream makes the crossing
          # count a property of the reader instead of the road.
          t = m.logMonoTime / 1e9
          ll = m.modelV2.laneLines
          probs = m.modelV2.laneLineProbs
          ok = True
          if not (lat and not hands):
            ok = False
            rejected["hands or lat"] += 1
          elif v < speed_floor:
            ok = False
            rejected["too slow"] += 1
          elif abs(des) >= STRAIGHT_KAPPA:
            ok = False
            rejected["not straight"] += 1
          elif len(ll) < 3 or not len(ll[1].y) or not len(ll[2].y):
            ok = False
            rejected["no lane lines"] += 1
          elif len(probs) < 3 or probs[1] < MIN_LANE_PROB or probs[2] < MIN_LANE_PROB:
            ok = False
            rejected["lane prob low"] += 1

          if not ok:
            window = []
            continue
          window.append((t, (float(ll[1].y[0]) + float(ll[2].y[0])) / 2.0))
          if window[-1][0] - window[0][0] >= WIN_S:
            o = [x for _, x in window]
            mean = statistics.fmean(o)
            n = sum(1 for a, b in zip(o, o[1:]) if (a - mean) * (b - mean) < 0)
            offs.append(statistics.median([abs(x) for x in o]))
            p2p.append(max(o) - min(o))
            cross.append(n / (WIN_S / 60.0))
            minutes += WIN_S / 60.0
            window = []
      except Exception:
        continue
  return dict(offs=offs, p2p=p2p, cross=cross, minutes=minutes, rejected=rejected)


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("directory")
  ap.add_argument("--speed", type=float, default=70.0, help="mph floor (default 70)")
  ap.add_argument("--route", action="append", help="limit to route(s)")
  ap.add_argument("--segs", help="segment range within a single route, e.g. 0-14")
  ap.add_argument("--min-windows", type=int, default=5,
                  help="below this many windows a route reports INSUFFICIENT rather than a number")
  args = ap.parse_args()

  files = sorted(glob.glob(os.path.join(args.directory, "*.rlog.zst")), key=segno)
  if args.route:
    files = [f for f in files if route_of(f) in set(args.route)]
  lo = hi = None
  if args.segs:
    lo, hi = (int(x) for x in args.segs.split("-"))
    files = [f for f in files if lo <= segno(f) <= hi]

  groups = collections.OrderedDict()
  for f in files:
    groups.setdefault(route_of(f), []).append(f)
  if not groups:
    print("no matching segments")
    return

  label = "route" if not args.segs else f"route (segs {lo}-{hi})"
  print(f"=== STRAIGHT-ROAD WEAVE, >= {args.speed:.0f} mph ===")
  print(f"   {WIN_S:.0f} s windows, straight throughout, hands off, lane probs >= {MIN_LANE_PROB}")
  print("   Sampled at the modelV2 rate. Comparable ONLY with other output of this tool.\n")
  print(f"  {label:<26}{'LC':>6}{'damp':>6}{'min':>8}{'off-centre':>12}{'p2p':>9}{'cross/min':>11}")

  thin = []
  for route, fs in groups.items():
    cfg = read_config(fs)
    r = scan(fs, args.speed)
    lc = cfg["lane_centering_strength_ang"][:5]
    dp = cfg["lane_centering_damping_ang"][:5] or "-"
    if len(r["offs"]) < args.min_windows:
      print(f"  {route:<26}{lc:>6}{dp:>6}{r['minutes']:>8.1f}"
            f"{'INSUFFICIENT -- ' + str(len(r['offs'])) + ' windows':>32}")
      thin.append((route, r))
      continue
    print(f"  {route:<26}{lc:>6}{dp:>6}{r['minutes']:>8.1f}"
          f"{statistics.median(r['offs']):>11.2f}m{statistics.median(r['p2p']):>8.2f}m"
          f"{statistics.median(r['cross']):>11.1f}")

  if thin:
    print("\n  Why the thin routes had no qualifying road:")
    for route, r in thin:
      top = ", ".join(f"{k} {c}" for k, c in r["rejected"].most_common(3))
      print(f"    {route}: {top}")

  print("\n  off-centre  median |offset| within a window -- how far off centre it SITS")
  print("  p2p         max-min within a window -- how far it WANDERS")
  print("  cross/min   sign changes about the window mean -- how OFTEN it hunts")
  print("\n  A low crossing rate with a high off-centre is the P-gain trade, not an improvement.")
  print("  Read all three columns or the trade is invisible.")


if __name__ == "__main__":
  main()
