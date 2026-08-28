"""Compare desired curvature against actual AT THE TIME IT WAS MEANT FOR.

MY FIRST METRIC WAS WRONG AND THIS CORRECTS IT. `controlsState.desiredCurvature` is LAG-ADJUSTED --
its own comment says "lag adjusted curvatures used by lateral controllers" -- so it is the curvature
wanted roughly `lat_delay` seconds from now. Comparing it against `controlsState.curvature` on the
SAME frame compares two different instants, and the sign changes that produces are the lookahead
offset, not oscillation. That is the same "print both on the same frame" trap this fork records,
arriving as a TIME offset rather than a units one.

His car compensates with the learned value: LagdToggle=1, LagdValueCache=0.3806, and lagd reports
status=estimated with validBlocks=50 and std 0.0067. So 0.381 s is real and is already applied.

This shifts by that delay and reports BOTH, so the correction is visible rather than asserted:

    naive      err = des[i] - act[i]          what I measured first
    shifted    err = des[i] - act[i + 38]     comparing like with like at 100 Hz

If ringing survives the shift, it is real oscillation. If it collapses, the ping-pong is not
visible in this pair at all and the search has to move.
"""
import collections
import glob
import os
import sys

import capnp
import zstandard

REPO = r"C:\Users\D.J. Saderholm\Documents\GitHub\Sandbox\bluepilot-icbm"
capnp.remove_import_hook()
log_capnp = capnp.load(os.path.join(REPO, "cereal", "log.capnp"),
                       imports=[os.path.join(REPO, "cereal")])

LAG_S = 0.3806
DT = 0.01                      # controlsState is 100 Hz
SHIFT = int(round(LAG_S / DT))
MIN_ERR = 2e-5
R_LO, R_HI = 300.0, 3000.0


def seg_index(p):
  try:
    return int(os.path.basename(p).split("--")[2].split(".")[0])
  except (IndexError, ValueError):
    return -1


def reversals(seq):
  n = 0
  prev = None
  for e in seq:
    if abs(e) < MIN_ERR:
      continue
    if prev is not None and (e > 0) != (prev > 0):
      n += 1
    prev = e
  return n


def main():
  d = sys.argv[1]
  routes = set(sys.argv[2:])
  files = sorted(glob.glob(os.path.join(d, "*.rlog.zst")), key=seg_index)
  if routes:
    files = [f for f in files if os.path.basename(f).split("--")[0] in routes]

  des, act, ok = [], [], []
  lat = False
  hands = False
  v = 0.0
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
      w = m.which()
      try:
        if w == "carControl":
          lat = bool(m.carControl.latActive)
        elif w == "carState":
          hands = bool(m.carState.steeringPressed)
          v = m.carState.vEgo
        elif w == "controlsState":
          cs = m.controlsState
          dc = float(cs.desiredCurvature)
          r = (1.0 / abs(dc)) if abs(dc) > 1e-9 else 1e9
          des.append(dc)
          act.append(float(cs.curvature))
          ok.append(lat and not hands and v >= 8.0 and R_LO <= r <= R_HI)
      except Exception:
        continue

  n = len(des)
  naive, shifted = [], []
  for i in range(n - SHIFT):
    if not (ok[i] and ok[i + SHIFT]):
      continue
    naive.append(des[i] - act[i])
    shifted.append(des[i] - act[i + SHIFT])

  print("=== DOES THE RINGING SURVIVE THE LAG SHIFT? ===")
  print()
  print("  frames compared         : %d" % len(naive))
  print("  shift applied           : %d frames = %.3f s (the delay his car actually uses)" % (SHIFT, LAG_S))
  print()
  if not naive:
    print("  no qualifying frames")
    return
  secs = len(naive) / 100.0
  for name, seq in (("NAIVE  (same frame) ", naive), ("SHIFTED (like-for-like)", shifted)):
    rv = reversals(seq)
    mean = sum(abs(e) for e in seq) / len(seq)
    worst = max(abs(e) for e in seq)
    print("  %s  %6.2f revs/s   mean |err| %.6f   worst %.6f" % (name, rv / secs, mean, worst))
  print()
  nr, sr = reversals(naive) / secs, reversals(shifted) / secs
  nm = sum(abs(e) for e in naive) / len(naive)
  sm = sum(abs(e) for e in shifted) / len(shifted)
  print("  reversal rate  %+.0f%%   mean error  %+.0f%%   (shifted vs naive)" % (
    100.0 * (sr - nr) / max(nr, 1e-9), 100.0 * (sm - nm) / max(nm, 1e-9)))
  print()
  if sm < nm * 0.7:
    print("  >>> the error LARGELY DISAPPEARS once compared like-for-like. The naive metric was")
    print("  >>> measuring the lookahead, not a tracking failure. My earlier conclusion was wrong.")
  elif sr < nr * 0.7:
    print("  >>> the RINGING largely disappears; the residual is offset, not oscillation.")
  else:
    print("  >>> the ringing SURVIVES the shift. It is real oscillation, not a lookahead artifact.")


if __name__ == "__main__":
  main()
