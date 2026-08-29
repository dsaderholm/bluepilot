"""Can the exit-biased blend fire at all? Read-only, off-device.

`lateral_angle_ext` carries a documented, load-bearing mechanism:

    # Exit-biased blend: near the PSCM authority limit or while the planner is actively
    # reducing curvature (exit detected), drop model prediction weight from 60% -> ~15%.

with three ways in, and in angle mode two of them are already known not to fire:

    _pscm_lim >= 1     the file's own comment: "In angle mode, LatCtlLim_D_Stat does not fire."
    _dbc_sat           path_angle within 10% of the +-0.5 rad CAN limit -- about 26 degrees
    _desired_falling   abs(desired) < abs(desired_last) - 0.010

The third is the one that is supposed to catch ordinary curve exits, and its threshold is **0.010
1/m** -- a 100 m radius of change, between two consecutive frames. Commanded curvature on the roads
he drives runs around 0.0015 1/m, so `abs(last) - 0.010` is NEGATIVE and the comparison can never be
true. If that reads right, `b_blend` is a constant 0.50 on every frame of every drive, and a
mechanism this file treats as active has never once run.

That is the "a guard that cannot fire, carrying a comment that calls it load-bearing" shape already
recorded here, so it is measured rather than asserted: this counts how often the trigger WOULD fire
on real frames, and prints the largest fall actually observed beside the threshold asked of it.

MEASURE OVER THE INTERVAL THE COMPARISON ACTUALLY SPANS. `_desired_curvature_last` is written by
`update_angle_strategy`, which runs inside the `STEER_STEP = 5` gate -- so consecutive calls are
**0.05 s** apart, not the 0.01 s of the `controlsState` stream. A first version of this tool
compared 100 Hz frame-to-frame falls against a threshold designed for a 20 Hz cadence and made the
trigger look five times deader than it is. Same shape as comparing a lag-adjusted signal against a
same-frame one: the numbers are real and the interval is wrong.

    python tools/bp_lateral_exitbias.py <dir-of-rlog.zst> [route ...]
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

DESIRED_FALLING_TH = 0.010     # the threshold in the shipped code
STEER_STEP = 5                 # CarControllerParams.STEER_STEP -- the angle path runs at 20 Hz
CTRL_HZ = 100                  # controlsState publish rate
STRIDE = STEER_STEP            # controlsState frames spanned by one angle-path call


def seg_key(p):
  b = os.path.basename(p).split("--")
  try:
    return (b[0], int(b[2].split(".")[0]))
  except (IndexError, ValueError):
    return (os.path.basename(p), 0)


def main():
  d = sys.argv[1]
  routes = set(sys.argv[2:])
  files = sorted(glob.glob(os.path.join(d, "*.rlog.zst")), key=seg_key)
  if routes:
    files = [f for f in files if os.path.basename(f).split("--")[0] in routes]

  falls = []
  n = 0
  would_fire = 0
  recent = collections.deque(maxlen=STRIDE + 1)
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
      except StopIteration:
        break
      except Exception:
        break
      if m.which() != "controlsState":
        continue
      des = abs(float(m.controlsState.desiredCurvature))
      recent.append(des)
      if len(recent) > STRIDE:
        prev = recent[0]          # one angle-path call earlier, 0.05 s back
        n += 1
        fall = prev - des
        if fall > 0:
          falls.append(fall)
        if des < prev - DESIRED_FALLING_TH:
          would_fire += 1

  print("=== CAN THE EXIT-BIASED BLEND FIRE? ===")
  print()
  if not n:
    print("  no controlsState frames found")
    return
  print("  falls measured over %.2f s -- one angle-path call at STEER_STEP=%d" % (STRIDE / CTRL_HZ, STEER_STEP))
  print()
  print("  intervals examined                           %d" % n)
  print("  intervals where _desired_falling would fire  %d   (%.4f%%)" % (would_fire, 100.0 * would_fire / n))
  print()
  if falls:
    falls.sort()
    print("  the threshold it must clear                  %.4f 1/m" % DESIRED_FALLING_TH)
    print("  largest fall observed                        %.6f 1/m" % falls[-1])
    print("  p99 fall                                     %.6f 1/m" % falls[int(0.99 * (len(falls) - 1))])
    print("  p999 fall                                    %.6f 1/m" % falls[int(0.999 * (len(falls) - 1))])
    print("  median fall                                  %.6f 1/m" % statistics.median(falls))
    print()
    print("  the threshold is %.1fx the p99 fall this drive contains."
          % (DESIRED_FALLING_TH / falls[int(0.99 * (len(falls) - 1))]))
  print()
  if 100.0 * would_fire / n < 0.5:
    print("  _desired_falling is effectively DEAD. With _pscm_lim silent in angle mode and")
    print("  _dbc_sat needing ~26 degrees of path angle, b_blend is 0.50 on essentially every")
    print("  frame, and the exit-biased blend -- described in the code as dropping model weight")
    print("  to ~15%% on exits -- does not run on the curve exits it was written for.")


if __name__ == "__main__":
  main()
