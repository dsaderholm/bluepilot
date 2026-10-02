#!/usr/bin/env python3
"""FusionPilot: did the stop hold HOLD the wheel, or wind it? Read-only, run off-device.

Route 000004bb (2026-09-28) held a flat command through a stop and the wheel went -35 -> -130 deg,
2.5x the turn the model asked for. hold_wheel_cap.py now trims the command when the wheel winds past
that turn. This scores every stretch where the stop hold drove the command, so one drive says
whether it worked:

    geo       the wheel angle that turns the car on the curvature commanded (bicycle model)
    peak      the most wheel on the stretch
    stop      the wheel once stopped, and how far it wandered while stopped
    trim      the most command the limiter took away (angleHoldWheelTrim), 0 = never acted

`latActive` and `steeringPressed` are separate columns: a stretch with his hands on it is his
steering, not the hold's, and must not be read as either a success or a failure.

    python tools/bp_stop_hold_wheel.py <dir of segment folders> [route-prefix ...]

Segment folders are `<route>--<n>/rlog.zst` (or `qlog.zst`), the layout the device and the pull
scripts use.
"""
import glob
import math
import os
import sys
from collections import defaultdict

import capnp
import zstandard

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
capnp.remove_import_hook()
log = capnp.load(os.path.join(REPO, "cereal", "log.capnp"), imports=[os.path.join(REPO, "cereal")])
MPH = 2.23694


def _msgs(seg):
  # rlog when it was pulled, else the qlog: 10 Hz carState and controllerStateBP is enough to score a
  # stop, and the qlogs for a whole day come off the car in seconds.
  path = os.path.join(seg, "rlog.zst")
  if not os.path.exists(path):
    path = os.path.join(seg, "qlog.zst")
  raw = zstandard.ZstdDecompressor().stream_reader(open(path, "rb")).read()
  it = log.Event.read_multiple_bytes(raw, traversal_limit_in_words=2**32)
  out = []
  while True:
    try:
      out.append(next(it))
    except StopIteration:
      break
    except Exception:
      break      # a truncated last segment is normal; keep what decoded
  out.sort(key=lambda m: m.logMonoTime)
  return out


def main():
  d = sys.argv[1]
  want = sys.argv[2:]
  routes = defaultdict(list)
  for seg in glob.glob(os.path.join(d, "*--*")):
    route = os.path.basename(seg).rsplit("--", 1)[0]
    if not want or any(route.startswith(w) for w in want):
      routes[route].append(seg)

  print("route     t_start   s     mph_in  lat   hands  kappa    geo     peak   stop  wander  trim")
  for route in sorted(routes):
    sr = wb = None
    st = dict(lat=False, hold=0.0, kappa=0.0, trim=0.0)
    rows = []
    t0 = None
    for seg in sorted(routes[route], key=lambda p: int(p.rsplit("--", 1)[1])):
      for m in _msgs(seg):
        t0 = m.logMonoTime if t0 is None else t0
        w = m.which()
        if w == "carParams" and sr is None:
          sr, wb = m.carParams.steerRatio, m.carParams.wheelbase
        elif w == "carControl":
          st["lat"] = m.carControl.latActive
        elif w == "controllerStateBP":
          c = m.controllerStateBP
          st["hold"], st["kappa"], st["trim"] = c.angleHoldKappa, c.kappaCmd, c.angleHoldWheelTrim
        elif w == "carState":
          cs = m.carState
          rows.append(((m.logMonoTime - t0) / 1e9, cs.vEgo, cs.steeringAngleDeg, cs.steeringPressed,
                       st["lat"], st["hold"], st["kappa"], st["trim"]))
    if not rows or not sr:
      continue
    i = 0
    while i < len(rows):
      if abs(rows[i][5]) < 1e-6:
        i += 1
        continue
      j = i
      while j < len(rows) and abs(rows[j][5]) > 1e-6:
        j += 1
      e = rows[i:j]
      i = j
      if e[-1][0] - e[0][0] < 1.0:
        continue
      kappa = max((r[5] for r in e), key=abs)
      geo = -math.degrees(math.atan(kappa * wb)) * sr
      peak = max((r[2] for r in e), key=abs)
      stopped = [r[2] for r in e if r[1] < 0.1]
      stop = f"{stopped[-1]:+6.1f}" if stopped else "   -  "
      wander = f"{max(stopped) - min(stopped):5.1f}" if stopped else "   - "
      lat = 100.0 * sum(r[4] for r in e) / len(e)
      hands = 100.0 * sum(r[3] for r in e) / len(e)
      trim = max(r[7] for r in e)
      print(f"{route[4:12]}  {e[0][0]:8.1f} {e[-1][0] - e[0][0]:5.1f}  {e[0][1] * MPH:5.1f}  {lat:4.0f}%  {hands:4.0f}%  "
            f"{kappa:+.4f}  {geo:+6.1f}  {peak:+6.1f}  {stop}  {wander}  {trim:.2f}")


if __name__ == "__main__":
  main()
