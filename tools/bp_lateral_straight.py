"""Does the COMMAND wobble on a STRAIGHT road? 300 miles already contains the answer.

I asked him to drive a test pattern to separate "openpilot sends a shaky target" from "the PSCM
amplifies a clean one". He pushed back -- he had just driven 300+ miles -- and he is right: an
interstate trip is full of straights and of gentle curves at every speed. The conditions already
exist in the logs; they just have to be FILTERED FOR rather than driven again.

THE DISCRIMINATOR, and it needs no new driving:

  On a STRAIGHT the desired curvature is ~0, so there is no bend to track and no lag to expose.
  Anything the command does there is not "following a curve badly" -- it is noise in the input or
  noise we add. If the command is flat on straights and only wobbles in curves, the input is fine
  and the problem is curve-specific. If the command wobbles on straights too, openpilot is handing
  the car a shaky target all the time and that is ours.

Reports, for straights and for gentle curves separately:
    command reversals/s     LatCtlPath_An_Actl decoded off sendcan (979, start bit 31)
    response reversals/s    steeringAngleDeg
    desired reversals/s     controlsState.desiredCurvature
and splits the curve case by speed, because a PSCM that amplifies should get worse with speed
while a noisy input need not.
"""
import collections
import glob
import math
import os
import sys

import capnp
import zstandard

REPO = r"C:\Users\D.J. Saderholm\Documents\GitHub\Sandbox\bluepilot-icbm"
capnp.remove_import_hook()
log_capnp = capnp.load(os.path.join(REPO, "cereal", "log.capnp"),
                       imports=[os.path.join(REPO, "cereal")])

ADDR = 979
SCALE, OFFSET = 0.0005, -0.5
MS_TO_MPH = 2.23694

STRAIGHT_R = 5000.0        # radius above this is a straight for our purposes
GENTLE = (300.0, 3000.0)
CMD_STEP = SCALE * 1.5     # ignore single-LSB dither
ANG_STEP = 0.15            # deg, steering angle noise floor
DES_STEP = 2e-5            # 1/m


def seg_index(p):
  try:
    return int(os.path.basename(p).split("--")[2].split(".")[0])
  except (IndexError, ValueError):
    return -1


def be_bits(data, start, length):
  val, bit = 0, start
  for _ in range(length):
    byte = bit // 8
    if byte >= len(data):
      return None
    val = (val << 1) | ((data[byte] >> (bit % 8)) & 1)
    bit = bit + 15 if bit % 8 == 0 else bit - 1
  return val


def reversals(seq, step):
  n, prev_dir, last = 0, 0, None
  for v in seq:
    if last is None:
      last = v
      continue
    if abs(v - last) < step:
      continue
    d = 1 if v > last else -1
    if prev_dir and d != prev_dir:
      n += 1
    prev_dir, last = d, v
  return n


def main():
  d = sys.argv[1]
  routes = set(sys.argv[2:])
  files = sorted(glob.glob(os.path.join(d, "*.rlog.zst")), key=seg_index)
  if routes:
    files = [f for f in files if os.path.basename(f).split("--")[0] in routes]

  # bucket -> lists
  B = collections.defaultdict(lambda: dict(cmd=[], ang=[], des=[], t=[]))
  lat = hands = False
  v = 0.0
  ang = 0.0
  des = 0.0

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
        if w == "carState":
          ang = float(m.carState.steeringAngleDeg)
          hands = bool(m.carState.steeringPressed)
          v = m.carState.vEgo
        elif w == "carControl":
          lat = bool(m.carControl.latActive)
        elif w == "controlsState":
          des = float(m.controlsState.desiredCurvature)
        elif w == "sendcan":
          for c in m.sendcan:
            if c.address != ADDR:
              continue
            rawv = be_bits(bytes(c.dat), 31, 11)
            if rawv is None or not lat or hands or v < 8.0:
              continue
            r = (1.0 / abs(des)) if abs(des) > 1e-9 else 1e9
            if r >= STRAIGHT_R:
              key = "STRAIGHT"
            elif GENTLE[0] <= r <= GENTLE[1]:
              mph = v * MS_TO_MPH
              key = "gentle %d-%d" % ((int(mph) // 15) * 15, (int(mph) // 15) * 15 + 15)
            else:
              continue
            b = B[key]
            b["cmd"].append(rawv * SCALE + OFFSET)
            b["ang"].append(ang)
            b["des"].append(des)
            b["t"].append(m.logMonoTime / 1e9)
      except Exception:
        continue

  print("=== DOES THE COMMAND WOBBLE ON A STRAIGHT? (from the miles already driven) ===")
  print()
  print("  REVERSAL RATE alone is misleading: near a straight the curvature sits at zero, so tiny")
  print("  noise crosses back and forth constantly. AMPLITUDE is what a driver actually feels.")
  print()
  print("  %-14s %7s %9s %9s %9s %11s %11s" % (
    "condition", "frames", "des/s", "cmd/s", "resp/s", "cmd p2p", "steer p2p"))
  order = sorted(B, key=lambda k: (k != "STRAIGHT", k))
  for k in order:
    b = B[k]
    if len(b["cmd"]) < 400:
      continue
    secs = (b["t"][-1] - b["t"][0]) or 1.0
    # amplitude of the WOBBLE, not of the manoeuvre: median peak-to-peak inside a 1 s window,
    # which is the timescale of the ~1.2 Hz ringing. A steady turn has a large range overall and
    # a small p2p per second; a wobble has a large p2p per second.
    def p2p(seq, hz):
      w = int(hz)
      if len(seq) < w * 2:
        return 0.0
      vals = [max(seq[i:i + w]) - min(seq[i:i + w]) for i in range(0, len(seq) - w, w)]
      vals.sort()
      return vals[len(vals) // 2] if vals else 0.0

    hz_cmd = len(b["cmd"]) / secs
    print("  %-14s %7d %9.2f %9.2f %9.2f %8.4f rad %7.2f deg" % (
      k, len(b["cmd"]),
      reversals(b["des"], DES_STEP) / secs,
      reversals(b["cmd"], CMD_STEP) / secs,
      reversals(b["ang"], ANG_STEP) / secs,
      p2p(b["cmd"], hz_cmd), p2p(b["ang"], hz_cmd)))
  print()
  print("  STRAIGHT = desired radius > %.0f m, so there is no bend to track and no lag to expose." % STRAIGHT_R)
  print("  A command that reverses there is noise we are SENDING, not a curve being followed badly.")


if __name__ == "__main__":
  main()
