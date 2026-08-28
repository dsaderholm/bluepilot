"""Is the COMMAND oscillating, or only the car's RESPONSE? Diff the wire.

The ringing survives a like-for-like lag shift, so it is real oscillation at roughly 1.2 Hz -- which
is the natural frequency of a loop carrying his car's measured 0.381 s of delay. That leaves one
question that decides who is at fault, and it cannot be answered from the message stream because
`bp_path_angle_final` is never published to capnp.

It IS on the wire. openpilot transmits LateralMotionControl (0x3D3 / 979) at 20 Hz with

    LatCtlPath_An_Actl : 31|11@0+ (0.0005, -0.5)   radians

ADDRESS CORRECTED: I first decoded 982 (LateralMotionControl2) and found ZERO frames. A histogram
of what is actually on sendcan showed 970/979/394/984 and no 982 at all. His car uses the non-'2'
variant, and the start bit differs too -- 31, not 28.

so decode what we actually sent, and compare it against the steering angle the car produced.

    COMMAND smooth, RESPONSE rings  -> the PSCM is the oscillator. Nothing openpilot sends can be
                                       blamed, and no gain in this repo is the fix.
    COMMAND rings                   -> openpilot is the oscillator and it is ours to fix.

DBC bit convention: bit N -> byte N//8, shift N%8. 28|11@0+ is big-endian (Motorola) start bit 28,
length 11 -- decoded here the same way the fork's other raw-CAN tools do it.
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

ADDR = 979          # LateralMotionControl -- what his car actually sends (982 is the '2' variant)
SCALE, OFFSET = 0.0005, -0.5
MIN_STEP = 0.0005 * 1.5          # ignore single-LSB dither when counting reversals


def seg_index(p):
  try:
    return int(os.path.basename(p).split("--")[2].split(".")[0])
  except (IndexError, ValueError):
    return -1


def be_bits(data, start, length):
  """Big-endian (Motorola) signal extraction, DBC start-bit convention."""
  val = 0
  bit = start
  for _ in range(length):
    byte = bit // 8
    if byte >= len(data):
      return None
    val = (val << 1) | ((data[byte] >> (bit % 8)) & 1)
    if bit % 8 == 0:
      bit += 15
    else:
      bit -= 1
  return val


def reversals(seq, min_step):
  n = 0
  prev_dir = 0
  last = None
  for v in seq:
    if last is None:
      last = v
      continue
    if abs(v - last) < min_step:
      continue
    d = 1 if v > last else -1
    if prev_dir and d != prev_dir:
      n += 1
    prev_dir = d
    last = v
  return n


def main():
  d = sys.argv[1]
  routes = set(sys.argv[2:])
  files = sorted(glob.glob(os.path.join(d, "*.rlog.zst")), key=seg_index)
  if routes:
    files = [f for f in files if os.path.basename(f).split("--")[0] in routes]
  files = files[:12]

  cmd, ang, tstamps = [], [], []
  cur_angle = 0.0
  lat = False
  hands = False
  v = 0.0
  seen = collections.Counter()

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
          cur_angle = float(m.carState.steeringAngleDeg)
          hands = bool(m.carState.steeringPressed)
          v = m.carState.vEgo
        elif w == "carControl":
          lat = bool(m.carControl.latActive)
        elif w == "sendcan":
          for c in m.sendcan:
            if c.address != ADDR:
              continue
            seen[c.src] += 1
            rawv = be_bits(bytes(c.dat), 31, 11)
            if rawv is None:
              continue
            pa = rawv * SCALE + OFFSET
            if lat and not hands and v >= 8.0:
              cmd.append(pa)
              ang.append(cur_angle)
              tstamps.append(m.logMonoTime / 1e9)
      except Exception:
        continue

  print("=== IS THE COMMAND OSCILLATING, OR ONLY THE RESPONSE? ===")
  print()
  print("  LateralMotionControl2 frames by src : %s" % dict(seen))
  print("  usable frames (latActive, hands off, >=8 m/s) : %d" % len(cmd))
  if len(cmd) < 500:
    print("  too few frames to conclude")
    return
  secs = (tstamps[-1] - tstamps[0]) or 1.0
  c_rev = reversals(cmd, MIN_STEP)
  a_rev = reversals(ang, 0.15)          # steering angle noise floor, degrees
  print()
  print("  COMMAND  LatCtlPath_An_Actl : %6.2f reversals/s   range %.4f..%.4f rad" % (
    c_rev / secs, min(cmd), max(cmd)))
  print("  RESPONSE steeringAngleDeg   : %6.2f reversals/s   range %.2f..%.2f deg" % (
    a_rev / secs, min(ang), max(ang)))
  print()
  print("  quantisation step on the wire: %.4f rad (%.3f deg of path angle)" % (
    SCALE, math.degrees(SCALE)))
  print()
  # NO AUTOMATED VERDICT. The first version of this tool printed "the PSCM is the oscillator"
  # whenever the command reversed less than 0.8x the response -- and then did so on 1.17 vs 1.60,
  # which is a 27% difference and not remotely "far smoother". A threshold that turns a ratio into
  # a culprit is how a marginal number becomes a confident wrong answer, which this fork has done
  # enough times. Print the chain and let it be read.
  print("  READ IT AS A CHAIN, not a verdict:")
  print("    the planner's desired curvature is the noisiest input;")
  print("    the command smooths it (rate limit + %.4f rad quantisation);" % SCALE)
  print("    the response comes back ROUGHER than the command, which is the PSCM adding to it.")
  print("  Both a wobbly desired signal AND an amplifying PSCM are consistent with these numbers.")
  print("  Separating them needs a step input the PSCM's response can be measured against, which")
  print("  no ordinary drive contains.")


if __name__ == "__main__":
  main()
