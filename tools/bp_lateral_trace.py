"""Emit a desired-vs-actual curvature trace as JSON, for plotting. Read-only, off-device.

*"Can you graph out what the ping ponging you're seeing looks like so I can confirm it's what I see
on the debug screen?"* -- and that is the right question to ask of every number in this file, because
a trace he can recognise is worth more than an aggregate he has to trust.

Emits one JSON object per window: time, desired curvature, actual curvature, steering angle and
speed, hands-off frames only flagged so a plot can grey out anything he was steering through.

    python tools/bp_lateral_trace.py <dir> <route> <t_start> <t_end> [--out FILE]
"""
import glob
import json
import os
import sys

import capnp
import zstandard

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
capnp.remove_import_hook()
log_capnp = capnp.load(os.path.join(REPO, "cereal", "log.capnp"),
                       imports=[os.path.join(REPO, "cereal")])

MS_TO_MPH = 2.23694


def seg_index(p):
  try:
    return int(os.path.basename(p).split("--")[2].split(".")[0])
  except (IndexError, ValueError):
    return -1


def main():
  d, route = sys.argv[1], sys.argv[2]
  t_start, t_end = float(sys.argv[3]), float(sys.argv[4])
  out = None
  if "--out" in sys.argv:
    out = sys.argv[sys.argv.index("--out") + 1]

  files = sorted([f for f in glob.glob(os.path.join(d, "*.rlog.zst"))
                  if os.path.basename(f).split("--")[0] == route], key=seg_index)

  ctrl, car = [], []
  t0 = None
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
      mono = m.logMonoTime / 1e9
      if t0 is None or mono < t0:
        t0 = mono
      ts = mono - t0
      if ts > t_end + 2:
        continue
      w = m.which()
      try:
        if w == "controlsState":
          cs = m.controlsState
          ctrl.append((ts, float(cs.desiredCurvature), float(cs.curvature)))
        elif w == "carState":
          cs = m.carState
          car.append((ts, float(cs.steeringAngleDeg), float(cs.vEgo) * MS_TO_MPH,
                      bool(cs.steeringPressed)))
      except Exception:
        continue

  ctrl.sort()
  car.sort()
  ctrl = [r for r in ctrl if t_start <= r[0] <= t_end]
  car = [r for r in car if t_start <= r[0] <= t_end]
  if not ctrl:
    print(json.dumps({"error": "no frames in window"}))
    return

  ct = [r[0] for r in car]

  def near(t):
    if not ct:
      return (0.0, 0.0, False)
    lo, hi = 0, len(ct) - 1
    while lo < hi:
      mid = (lo + hi) // 2
      if ct[mid] < t:
        lo = mid + 1
      else:
        hi = mid
    return car[lo][1], car[lo][2], car[lo][3]

  base = ctrl[0][0]
  pts = []
  for t, des, act in ctrl:
    ang, mph, hands = near(t)
    pts.append({"t": round(t - base, 3), "des": round(des, 6), "act": round(act, 6),
                "ang": round(ang, 2), "mph": round(mph, 1), "hands": hands})

  doc = {"route": route, "t_start": t_start, "t_end": t_end, "n": len(pts), "points": pts}
  txt = json.dumps(doc)
  if out:
    with open(out, "w", encoding="utf-8") as f:
      f.write(txt)
    print("wrote %s (%d points, %.0f%% hands-off)"
          % (out, len(pts), 100.0 * sum(1 for p in pts if not p["hands"]) / len(pts)))
  else:
    print(txt)


if __name__ == "__main__":
  main()
