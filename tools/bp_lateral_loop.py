"""Where does the 0.4 s come from, and does the plan react to the car being late? Read-only.

Two questions, both of which have to be answered before any fix is written, because they point at
completely different code.

**Q1 -- IS THE LAG THE PSCM, OR OUR PIPELINE?** The car is ~0.4 s behind the plan. If the PSCM
itself takes 0.4 s to move the wheel to a commanded path_angle, no amount of restructuring upstream
helps and the only lever is compensation. If the PSCM is fast and the 0.4 s accumulates upstream of
the wire, then cadence and pipeline are the target -- `STEER_STEP = 5` means we only update the
command at 20 Hz, and curvature mode has run at 100 Hz on some builds.

    measured as: best-fit shift between path_angle ON THE WIRE (0x3D3, decoded from sendcan)
                 and steeringAngleDeg. That interval contains ONLY the PSCM and the mechanics --
                 everything upstream is already baked into path_angle by the time we read it.

**Q2 -- DOES THE PLAN SWING BECAUSE THE CAR IS LATE?** If the model is reacting to a car that keeps
arriving late, the oscillation is a closed loop through the road and reducing the lag quiets the
plan too. If the plan swings regardless of tracking error, it is the model's own and lag work will
not touch it.

    measured as: correlation between the tracking error e = actual - desired at time t, and the
                 CHANGE in desired at t + lag, swept over lag. A peak at POSITIVE lag means error
                 leads plan change -- the plan is responding to the car. Flat means it is not.

Both restricted to hands off, latActive, and genuinely turning, because a straight road correlates
everything with everything.

    python tools/bp_lateral_loop.py <dir-of-rlog.zst> <route> [max_segments]
"""
import glob
import os
import sys

import capnp
import numpy as np
import zstandard

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
capnp.remove_import_hook()
log_capnp = capnp.load(os.path.join(REPO, "cereal", "log.capnp"),
                       imports=[os.path.join(REPO, "cereal")])

ADDR, SCALE, OFFSET = 979, 0.0005, -0.5      # LateralMotionControl / LatCtlPath_An_Actl
HZ = 100
MAX_SHIFT = 80              # 0.80 s
MIN_KAPPA = 8e-4
MS_TO_MPH = 2.23694


def be_bits(data, start, length):
  val = 0
  bit = start
  for _ in range(length):
    byte = bit // 8
    if byte >= len(data):
      return None
    val = (val << 1) | ((data[byte] >> (bit % 8)) & 1)
    bit = bit + 15 if bit % 8 == 0 else bit - 1
  return val


def seg_index(p):
  try:
    return int(os.path.basename(p).split("--")[2].split(".")[0])
  except (IndexError, ValueError):
    return -1


def resample(t, v, grid):
  if len(t) < 2:
    return None
  return np.interp(grid, t, v)


def best_shift(a, b, mask, label):
  """Shift b forward against a; return (best_frames, corr_by_shift). Both mean-removed."""
  out = []
  n = len(a)
  for s in range(0, MAX_SHIFT + 1):
    m = mask[:n - s]
    if m.sum() < 500:
      out.append(np.nan)
      continue
    x = a[:n - s][m]
    y = b[s:][m]
    x = x - x.mean()
    y = y - y.mean()
    d = np.sqrt((x * x).sum() * (y * y).sum())
    out.append(float((x * y).sum() / d) if d > 0 else np.nan)
  out = np.array(out)
  if np.all(np.isnan(out)):
    return None, out
  return int(np.nanargmax(out)), out


def main():
  d, route = sys.argv[1], sys.argv[2]
  maxseg = int(sys.argv[3]) if len(sys.argv) > 3 else 40

  files = sorted([f for f in glob.glob(os.path.join(d, "*.rlog.zst"))
                  if os.path.basename(f).split("--")[0] == route], key=seg_index)[:maxseg]

  wt, wv = [], []          # path_angle on the wire
  ct, cdes, cact = [], [], []
  st, sang, sv, shand, slat = [], [], [], [], []
  t0 = None
  lat = False
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
      w = m.which()
      try:
        if w == "carControl":
          lat = bool(m.carControl.latActive)
        elif w == "controlsState":
          cs = m.controlsState
          ct.append(ts)
          cdes.append(float(cs.desiredCurvature))
          cact.append(float(cs.curvature))
        elif w == "carState":
          cs = m.carState
          st.append(ts)
          sang.append(float(cs.steeringAngleDeg))
          sv.append(float(cs.vEgo))
          shand.append(bool(cs.steeringPressed))
          slat.append(lat)
        elif w == "sendcan":
          for c in m.sendcan:
            if c.address != ADDR:
              continue
            r = be_bits(bytes(c.dat), 31, 11)
            if r is not None:
              wt.append(ts)
              wv.append(r * SCALE + OFFSET)
      except Exception:
        continue

  print("=== %s : WHERE THE LAG LIVES, AND WHETHER THE PLAN REACTS TO IT ===" % route)
  print()
  print("  segments read: %d   wire frames: %d   controlsState: %d" % (len(files), len(wt), len(ct)))
  if len(wt) < 2000 or len(ct) < 5000:
    print("  not enough data")
    return

  lo = max(min(wt), min(ct), min(st))
  hi = min(max(wt), max(ct), max(st))
  grid = np.arange(lo, hi, 1.0 / HZ)
  pa = resample(np.array(wt), np.array(wv), grid)
  des = resample(np.array(ct), np.array(cdes), grid)
  act = resample(np.array(ct), np.array(cact), grid)
  ang = resample(np.array(st), np.array(sang), grid)
  v = resample(np.array(st), np.array(sv), grid)
  hands = resample(np.array(st), np.array(shand, dtype=float), grid) > 0.5
  latm = resample(np.array(st), np.array(slat, dtype=float), grid) > 0.5

  turning = (np.abs(des) >= MIN_KAPPA) & (~hands) & latm & (v > 17.9)
  print("  qualifying frames (turning, hands off, latActive, >40 mph): %d (%.1f min)"
        % (turning.sum(), turning.sum() / HZ / 60.0))
  print()

  # Q1: wire command -> steering angle. This interval is the PSCM and the mechanics only.
  s1, c1 = best_shift(pa, ang, turning, "pscm")
  print("  Q1  PSCM RESPONSE -- path_angle on the wire vs steering angle")
  if s1 is None:
    print("      insufficient data")
  else:
    print("      best-fit shift: %.3f s   (peak r = %.3f)" % (s1 / HZ, c1[s1]))
    line = "      r by shift:"
    for s in range(0, MAX_SHIFT + 1, 10):
      if not np.isnan(c1[s]):
        line += "  %.2fs:%.3f" % (s / HZ, c1[s])
    print(line)
    print()
    print("      Our command cadence is STEER_STEP=5 -> 20 Hz, i.e. 0.050 s of quantisation plus")
    print("      ~0.025 s of zero-order hold on average = ~0.075 s. Anything beyond that in the")
    print("      figure above is the PSCM and the mechanics, which no restructuring upstream fixes.")
  print()

  # Q2: does the tracking error lead the plan's change?
  err = act - des
  ddes = np.gradient(des) * HZ
  s2, c2 = best_shift(err, ddes, turning, "loop")
  print("  Q2  DOES THE PLAN REACT TO THE CAR BEING LATE?")
  print("      correlation of tracking error e(t) with d(desired)/dt at t+lag")
  if s2 is None:
    print("      insufficient data")
  else:
    print("      strongest at lag %.3f s   (r = %+.3f)" % (s2 / HZ, c2[s2]))
    line = "      r by lag:  "
    for s in range(0, MAX_SHIFT + 1, 10):
      if not np.isnan(c2[s]):
        line += "  %.2fs:%+.3f" % (s / HZ, c2[s])
    print(line)
    print()
    peak = abs(c2[s2])
    if peak < 0.15:
      print("      FLAT / WEAK. The plan is not measurably responding to the tracking error, so the")
      print("      oscillation is the model's own and reducing lag will not quiet it.")
    else:
      print("      The plan DOES follow the error, so this is a closed loop through the road and")
      print("      cutting the lag should quiet the plan as well as the tracking.")


if __name__ == "__main__":
  main()
