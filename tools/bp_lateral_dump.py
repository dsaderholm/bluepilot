"""Frame-level dump of one lateral ringing window. Read-only, runs off-device.

`bp_lateral_ringing.py` says WHICH mechanism the population is consistent with. This shows a single
episode frame by frame, because a rate and a mechanism are different things and this fork has been
wrong before by reading one as the other.

Prints, on the same frame:

    desired      controlsState.desiredCurvature -- lag-adjusted, what the controller is chasing
    actual       controlsState.curvature -- from the vehicle model
    err          desired - actual. SIGN CHANGES are ringing; a steady sign is mere lag.
    R            radius of the desired curve, so "gradual" is visible rather than assumed
    band         is |desired| inside the [0.0007, 0.001] gain-schedule band
    RL           carStateBP.angleRateLimited -- the path_angle soft-ROC clip bit this frame
    delay        liveDelay.lateralDelay, against the 0.15 s clip the lookahead applies
    hands        steeringPressed -- his input is not the controller's, and conflating them is the
                 same error that produced the 3.21 m/s^2 figure

    python tools/bp_lateral_dump.py <dir> <route> <t_start> <t_end>
"""
import glob
import math
import os
import sys

import capnp
import zstandard

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
capnp.remove_import_hook()
log_capnp = capnp.load(os.path.join(REPO, "cereal", "log.capnp"),
                       imports=[os.path.join(REPO, "cereal")])

MS_TO_MPH = 2.23694
KAPPA_LO, KAPPA_HI = 0.0007, 0.001
DELAY_CLIP_HI = 0.15


def main():
  d, route, lo, hi = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
  files = sorted(glob.glob(os.path.join(d, route + "--*.rlog.zst")),
                 key=lambda p: int(os.path.basename(p).split("--")[2].split(".")[0]))

  t0 = None
  cur = dict(v=0.0, hands=False, lat=False, delay=float("nan"), rl=False)
  rows = []
  for p in files:
    try:
      with open(p, "rb") as f:
        raw = zstandard.ZstdDecompressor().stream_reader(f).read()
      evs = log_capnp.Event.read_multiple_bytes(raw, traversal_limit_in_words=2 ** 32)
    except Exception:
      continue
    while True:
      try:
        m = next(evs)
      except (StopIteration, Exception):
        break
      mono = m.logMonoTime / 1e9
      if t0 is None or mono < t0:
        t0 = mono
      ts = mono - t0
      if ts < lo or ts > hi:
        continue
      w = m.which()
      try:
        if w == "carControl":
          cur["lat"] = bool(m.carControl.latActive)
        elif w == "liveDelay":
          cur["delay"] = float(m.liveDelay.lateralDelay)
        elif w == "carStateBP":
          cur["rl"] = bool(getattr(m.carStateBP, "angleRateLimited", False))
        elif w == "carState":
          cur["v"] = m.carState.vEgo
          cur["hands"] = bool(m.carState.steeringPressed)
        elif w == "controlsState":
          cs = m.controlsState
          rows.append((ts, float(cs.desiredCurvature), float(cs.curvature),
                       cur["v"], cur["hands"], cur["lat"], cur["delay"], cur["rl"]))
      except Exception:
        continue

  print("  %8s %11s %11s %11s %8s %5s %4s %7s %5s %5s" % (
    "t+", "desired", "actual", "err", "R(m)", "band", "RL", "delay", "mph", "hands"))
  prev = None
  for ts, des, act, v, hands, lat, delay, rl in rows:
    if not lat:
      continue
    err = des - act
    r = (1.0 / abs(des)) if abs(des) > 1e-9 else float("inf")
    flip = "<<" if (prev is not None and err and prev and (err > 0) != (prev > 0)) else ""
    prev = err if abs(err) > 2e-5 else prev
    print("  %8.2f %11.6f %11.6f %11.6f %8.0f %5s %4s %7.3f %5.0f %5s %s" % (
      ts, des, act, err, min(r, 99999), "IN" if KAPPA_LO <= abs(des) <= KAPPA_HI else "",
      "RL" if rl else "", delay, v * MS_TO_MPH, hands, flip))
  print()
  print("  '<<' marks a sign change of the error -- that is the ringing.")
  print("  delay is clipped to %.2f s by the VLT lookahead regardless of what is shown here." % DELAY_CLIP_HI)


if __name__ == "__main__":
  main()
