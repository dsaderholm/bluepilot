#!/usr/bin/env python3
"""FusionPilot: the rear radar reduction, in Python, so it can be proven before it is C++.

WHAT THIS IS FOR. The feeder microcontroller turns ~2150 raw detection frames a second into three
digest messages at 20 Hz. That reduction is the whole architecture -- bus 1 is already 60-73%
loaded and cannot carry the raw stream -- and it is also the one piece that would be miserable to
debug once it is firmware on a part bolted behind a bumper.

So the algorithm lives here first, runs against a real capture, and the firmware mirrors it. When
the two disagree, this is the reference.

  python tools/bp_rear_digest_sim.py <capture.json>

The capture is [[t, addr, hexbytes], ...] as bp_radar_bench writes it.

WHAT IT DELIBERATELY DOES NOT DO: cluster, track, or smooth. RearApproach wants the nearest closing
target and a TTC, and every layer of cleverness between the sensor and that number is a layer that
can invent a target that is not there. The front radar's own interface clusters because it must
produce leads; this must produce a veto, and a veto that fires late is better than one that fires
on a phantom.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass

MRR_START, MRR_END = 0x120, 0x15F
MRR_HEADER = 0x174

# Empty slots read 8080xx0000800400 -- the two high bytes are the tell, and rejecting on them
# before decoding is what lets the feeder keep up. Measured 2026-08-14: ~97% of frames.
EMPTY_PREFIX = b"\x80\x80"

# Long-range scan modes are more sensitive and pick up the road surface. The front interface
# discards long-range returns closer than this for the same reason.
MIN_LONG_RANGE_DIST_M = 30.0
LONG_RANGE_SCANS = (1, 3)

# Below this a target is not closing on us in any sense worth reporting. Matches MIN_CLOSING_MS in
# rear_approach.py deliberately -- two thresholds for one idea is how they drift apart.
MIN_CLOSING_MS = 0.5

# A target beyond this is not about to arrive. The sensor's long-range mode reaches ~175 m and
# measures ~130 m in practice on this part; nothing useful to a lane change lives past that.
MAX_RANGE_M = 175.0

# Lane binning. Deliberately a DEADBAND rather than a sign test: a target dead astern is in our own
# lane, not in the lane we would move into, and calling it left or right by the sign of a noisy
# 0.1 m offset would make the two sides flicker against each other.
OWN_LANE_HALF_WIDTH_M = 1.4

DIGEST_HZ = 20.0


@dataclass
class Detection:
  d_rel: float   # m behind us
  y_rel: float   # m lateral, left positive
  v_rel: float   # m/s, POSITIVE = closing on us
  amplitude: float


@dataclass
class SideDigest:
  detected: bool = False
  d_rel: float = 0.0
  y_rel: float = 0.0
  v_rel: float = 0.0
  target_count: int = 0


def decode_detections(db, addr_to_index, frames, azimuth_offset_rad: float = 0.0):
  """Raw frames for one radar cycle -> detections, in the car's rear frame.

  SIGNS, stated rather than inherited. The DBC gives range, azimuth and RANGE RATE, where a
  negative range rate means the gap is closing. RearApproachSide.from_radar wants v_rel POSITIVE
  for closing, so it is negated exactly once, here. ESR.dbc and ford_fusion_2018_adas.dbc disagree
  on azimuth sign for the same hardware, which is why nothing about a Delphi convention is assumed.
  """
  out = []
  for addr, raw in frames:
    i = addr_to_index.get(addr)
    if i is None or raw[:2] == EMPTY_PREFIX:
      continue
    d = db.decode_message(addr, raw)
    if not d[f"CAN_DET_VALID_LEVEL_{i:02d}"]:
      continue
    scan = int(d[f"CAN_SCAN_INDEX_2LSB_{i:02d}"])
    rng = float(d[f"CAN_DET_RANGE_{i:02d}"])
    if rng <= 0.0 or rng > MAX_RANGE_M:
      continue
    if scan in LONG_RANGE_SCANS and rng < MIN_LONG_RANGE_DIST_M:
      continue
    az = float(d[f"CAN_DET_AZIMUTH_{i:02d}"]) - azimuth_offset_rad
    out.append(Detection(
      d_rel=math.cos(az) * rng,
      y_rel=-math.sin(az) * rng,
      v_rel=-float(d[f"CAN_DET_RANGE_RATE_{i:02d}"]),
      amplitude=float(d[f"CAN_DET_AMPLITUDE_{i:02d}"]),
    ))
  return out


def reduce_to_sides(dets) -> tuple[SideDigest, SideDigest]:
  """Detections -> nearest CLOSING target per side. This is the entire feeder algorithm.

  NEAREST, not strongest and not fastest. The consumer computes TTC from range and closing speed,
  and the target that matters is the one arriving first. Amplitude is decoded but deliberately
  unused: a big slow lorry is not more urgent than a small fast car.
  """
  left, right = SideDigest(), SideDigest()
  for side, keep in ((left, lambda d: d.y_rel > OWN_LANE_HALF_WIDTH_M),
                     (right, lambda d: d.y_rel < -OWN_LANE_HALF_WIDTH_M)):
    closing = [d for d in dets if keep(d) and d.v_rel >= MIN_CLOSING_MS]
    side.target_count = min(31, len(closing))
    if closing:
      n = min(closing, key=lambda d: d.d_rel)
      side.detected = True
      side.d_rel, side.y_rel, side.v_rel = n.d_rel, n.y_rel, n.v_rel
  return left, right


def main() -> int:
  if len(sys.argv) < 2:
    return print(__doc__) or 2
  import cantools
  from pathlib import Path

  repo = Path(__file__).resolve().parent.parent
  db = cantools.database.load_file(repo / "opendbc_repo/opendbc/dbc/FORD_CADS.dbc")
  addr_to_index = {db.get_message_by_name(f"MRR_Detection_{i:03d}").frame_id: i
                   for i in range(1, 65)}

  rows = json.load(open(sys.argv[1]))
  span = rows[-1][0] - rows[0][0] if len(rows) > 1 else 1.0

  # Group into radar cycles on the header, which is what the feeder does: the header carries the
  # scan index every detection in the cycle must agree with.
  cycles, cur = [], []
  for t, addr, hexs in rows:
    if addr == MRR_HEADER and cur:
      cycles.append(cur)
      cur = []
    if MRR_START <= addr <= MRR_END:
      cur.append((addr, bytes.fromhex(hexs)))
  if cur:
    cycles.append(cur)

  raw_frames = sum(1 for r in rows if MRR_START <= r[1] <= MRR_END or r[1] == MRR_HEADER)
  total_dets = 0
  reported = 0
  for frames in cycles:
    dets = decode_detections(db, addr_to_index, frames)
    total_dets += len(dets)
    left, right = reduce_to_sides(dets)
    reported += int(left.detected) + int(right.detected)

  digest_frames = 3 * DIGEST_HZ * span
  print(f"capture: {span:.1f} s, {len(cycles)} radar cycles")
  print(f"  raw in    {raw_frames:7d} frames  {raw_frames / span:8.0f} /s")
  print(f"  digest out{int(digest_frames):7d} frames  {digest_frames / span:8.0f} /s")
  print(f"  reduction {raw_frames / max(1, digest_frames):7.0f}x")
  print(f"  valid detections {total_dets}, of which closing and off-centre: {reported}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
